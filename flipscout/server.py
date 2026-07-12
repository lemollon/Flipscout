"""The little backend that unlocks live eBay lookups for the web app.

A browser can't call eBay directly — it would leak your API secret into the page,
and eBay blocks cross-origin browser calls (CORS) anyway. So this server holds the
secret (from env), makes the eBay call on the page's behalf, and serves the web app
from the same origin (so the page's fetch is same-origin and Just Works).

Run it:
    pip install -e ".[server]"
    export EBAY_CLIENT_ID=...  EBAY_CLIENT_SECRET=...
    uvicorn flipscout.server:app --port 8000
    # open http://localhost:8000  -> the "eBay" button now looks up sold prices

Endpoints:
    GET /                  -> the web app (web/index.html)
    GET /api/health        -> {"ok": true, "ebay_configured": bool}
    GET /api/comps?q=...   -> {sold_price, sold_count, active_count, source, low, high}

With no eBay keys set, the app still serves and works fully in estimate mode; only
/api/comps returns 503 (nothing else needs credentials).
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Optional

try:
    from fastapi import FastAPI, File, HTTPException, Query, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "The web server needs FastAPI. Install it with:  pip install -e \".[server]\""
    ) from e

from .comps import Comp, CompsProvider

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Test seams: set these to fakes to bypass eBay / the screenshot scanner.
_provider: Optional[CompsProvider] = None
_scanner = None


def get_provider() -> CompsProvider:
    """The live eBay provider, built lazily from env. Raises if creds are unset."""
    global _provider
    if _provider is None:
        from .ebay_api import EbayApiComps  # lazy: needs requests + creds
        _provider = EbayApiComps()  # EbayConfig.from_env() raises if unset
    return _provider


def ebay_configured() -> bool:
    return bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))


def get_scanner():
    """The screenshot extractor, built lazily. Raises RuntimeError if unconfigured."""
    global _scanner
    if _scanner is None:
        from .scan import get_extractor  # lazy: needs anthropic or pytesseract
        _scanner = get_extractor()
    return _scanner


def create_app() -> "FastAPI":
    app = FastAPI(title="Flipscout", docs_url="/api/docs")

    # Allow the app to be opened from another origin too (e.g. a self-hosted static
    # copy) — this is a personal tool with no user data, so permissive CORS is fine.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("FLIPSCOUT_CORS", "*").split(","),
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"ok": True, "ebay_configured": ebay_configured()}

    @app.get("/api/comps")
    def comps(q: str = Query(..., min_length=1, description="item search query")):
        if not ebay_configured() and _provider is None:
            raise HTTPException(
                status_code=503,
                detail="Live eBay lookups aren't set up. Set EBAY_CLIENT_ID and "
                       "EBAY_CLIENT_SECRET, or type the sold price in by hand.",
            )
        try:
            comp: Comp = get_provider().lookup(q)
        except Exception as e:  # network/credential failure -> 502, keep app usable
            raise HTTPException(status_code=502, detail=f"eBay lookup failed: {e}")
        return {
            "query": comp.query,
            "sold_price": comp.sold_price,
            "sold_count": comp.sold_count,
            "active_count": comp.active_count,
            "source": comp.source,
            "low": comp.low,
            "high": comp.high,
        }

    @app.get("/api/deals")
    def deals(q: str = Query(..., min_length=1, description="comma-separated searches"),
              min_profit: float = 15.0, min_roi: float = 0.5,
              local: bool = False, zip: Optional[str] = None,
              minutes: Optional[int] = None):
        if not ebay_configured() and _provider is None:
            raise HTTPException(status_code=503, detail=(
                "Live eBay lookups aren't set up. Set EBAY_CLIENT_ID and "
                "EBAY_CLIENT_SECRET to scan for deals."))
        from .analyzer import Thresholds
        from .scanner import scan
        queries = [s.strip() for s in q.split(",") if s.strip()]
        try:
            hits = scan(queries, get_provider(),
                        thresholds=Thresholds(min_profit=min_profit, min_roi=min_roi),
                        local=local, zip_code=zip, effort_minutes=minutes,
                        limit_per_query=10)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Scan failed: {e}")
        return {"deals": [dataclasses.asdict(h) for h in hits[:50]]}

    @app.post("/api/scan")
    async def scan(image: UploadFile = File(...)):
        data = await image.read()
        if not data:
            raise HTTPException(status_code=400, detail="No image received.")
        try:
            scanner = get_scanner()
        except RuntimeError as e:  # not configured
            raise HTTPException(status_code=503, detail=str(e))
        try:
            return scanner.extract(data, image.content_type or "image/png")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Couldn't read the screenshot: {e}")

    # Serve the web app from the same origin (so its fetch to /api/* is same-origin).
    if _WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")

    return app


app = create_app()

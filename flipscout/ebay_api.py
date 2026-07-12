"""Live eBay data — turn "type in the sold price" into "the tool looks it up".

This wires the analyzer to eBay's official APIs so you stop comping by hand:

  * OAuth (client-credentials grant) -> an application token, cached until it expires.
  * Browse API (item_summary/search) -> ACTIVE listings: how many are up right now
    (supply / active_count) and their asking prices.
  * Marketplace Insights (item_sales/search) -> actual SOLD prices over ~90 days.
    This is the number that matters. NOTE: Insights is a "Limited Release" API —
    eBay must approve your app for it. Until then the call 403s and we degrade
    gracefully: you still get active_count from Browse, but sold_price stays None
    (a NEEDS_COMP) so the tool never passes off asking prices as sold prices.

Set credentials via env (free at https://developer.ebay.com/ -> create app):
    EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, optionally EBAY_ENV=sandbox|production,
    EBAY_MARKETPLACE=EBAY_US.

Nothing here touches Facebook. This is eBay's own data about eBay.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from statistics import median
from typing import Optional

import requests

from .comps import Comp

_HOSTS = {
    "production": "https://api.ebay.com",
    "sandbox": "https://api.sandbox.ebay.com",
}
_SCOPE = "https://api.ebay.com/oauth/api_scope"
_TIMEOUT = 20


@dataclass
class EbayConfig:
    client_id: str
    client_secret: str
    marketplace: str = "EBAY_US"
    env: str = "production"

    @property
    def host(self) -> str:
        return _HOSTS[self.env]

    @classmethod
    def from_env(cls) -> "EbayConfig":
        cid = os.environ.get("EBAY_CLIENT_ID")
        secret = os.environ.get("EBAY_CLIENT_SECRET")
        if not cid or not secret:
            raise RuntimeError(
                "Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (free app keys from "
                "https://developer.ebay.com/). Until then, run in estimate mode."
            )
        return cls(
            client_id=cid,
            client_secret=secret,
            marketplace=os.environ.get("EBAY_MARKETPLACE", "EBAY_US"),
            env=os.environ.get("EBAY_ENV", "production"),
        )


# --- OAuth ------------------------------------------------------------------

@dataclass
class _Token:
    value: str
    expires_at: float

    @property
    def valid(self) -> bool:
        return bool(self.value) and time.time() < self.expires_at - 60  # 60s safety


def fetch_app_token(cfg: EbayConfig, session: Optional[requests.Session] = None) -> _Token:
    """Client-credentials OAuth: a token for public application-scoped calls."""
    session = session or requests
    basic = base64.b64encode(f"{cfg.client_id}:{cfg.client_secret}".encode()).decode()
    resp = session.post(
        f"{cfg.host}/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": _SCOPE},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    return _Token(
        value=body["access_token"],
        expires_at=time.time() + float(body.get("expires_in", 7200)),
    )


# --- pure response parsers (unit-testable without the network) --------------

def parse_browse(body: dict) -> tuple[Optional[int], list[float]]:
    """(active_count, asking_prices) from a Browse item_summary/search response."""
    total = body.get("total")
    asks: list[float] = []
    for it in body.get("itemSummaries", []) or []:
        price = (it.get("price") or {}).get("value")
        if price is not None:
            try:
                asks.append(float(price))
            except (TypeError, ValueError):
                pass
    return (int(total) if total is not None else None), asks


def parse_insights(body: dict) -> tuple[Optional[int], list[float]]:
    """(sold_count, sold_prices) from a Marketplace Insights item_sales/search response."""
    total = body.get("total")
    solds: list[float] = []
    for it in body.get("itemSales", []) or []:
        price = (it.get("lastSoldPrice") or {}).get("value")
        if price is not None:
            try:
                solds.append(float(price))
            except (TypeError, ValueError):
                pass
    return (int(total) if total is not None else None), solds


# --- provider ---------------------------------------------------------------

class EbayApiComps:
    """Live comps provider. Drop-in replacement for EstimateComps.

    lookup() returns a Comp with:
      * sold_price  = median of recent SOLD prices (Insights), or None if Insights
                      isn't approved for your app yet (honest NEEDS_COMP).
      * sold_count  = # sold in the window (Insights).
      * active_count= # active listings now (Browse) — always available.
    An observed_price passed in still wins (manual override), so you can mix modes.
    """

    def __init__(self, cfg: Optional[EbayConfig] = None,
                 session: Optional[requests.Session] = None):
        self.cfg = cfg or EbayConfig.from_env()
        self.session = session or requests.Session()
        self._token: Optional[_Token] = None

    def _auth_header(self) -> dict:
        if self._token is None or not self._token.valid:
            self._token = fetch_app_token(self.cfg, self.session)
        return {
            "Authorization": f"Bearer {self._token.value}",
            "X-EBAY-C-MARKETPLACE-ID": self.cfg.marketplace,
        }

    def _browse(self, query: str, limit: int = 50) -> tuple[Optional[int], list[float]]:
        resp = self.session.get(
            f"{self.cfg.host}/buy/browse/v1/item_summary/search",
            headers=self._auth_header(),
            params={"q": query, "limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return parse_browse(resp.json())

    def _insights(self, query: str, limit: int = 50) -> tuple[Optional[int], list[float]]:
        """Sold data. Returns (None, []) if the API isn't available to this app
        (403/404) rather than raising — the caller degrades to NEEDS_COMP."""
        try:
            resp = self.session.get(
                f"{self.cfg.host}/buy/marketplace_insights/v1_beta/item_sales/search",
                headers=self._auth_header(),
                params={"q": query, "limit": limit},
                timeout=_TIMEOUT,
            )
            if resp.status_code in (401, 403, 404):
                return None, []
            resp.raise_for_status()
            return parse_insights(resp.json())
        except requests.RequestException:
            return None, []

    def lookup(self, query: str, observed_price: Optional[float] = None) -> Comp:
        if observed_price is not None:
            return Comp(query=query, sold_price=observed_price, source="manual")

        active_count, asks = self._browse(query)
        sold_count, solds = self._insights(query)

        sold_price = median(solds) if solds else None
        source = "ebay_insights" if solds else ("ebay_browse" if active_count is not None
                                                else "unknown")
        low = min(solds) if solds else (min(asks) if asks else None)
        high = max(solds) if solds else (max(asks) if asks else None)

        return Comp(
            query=query,
            sold_price=sold_price,
            sold_count=sold_count,
            active_count=active_count,
            source=source,
            low=low,
            high=high,
        )

"""Read a listing off a screenshot — the phone-native capture path.

You screenshot a Facebook Marketplace listing and submit the image; this pulls out
the item title and asking price so you don't retype anything. Like the eBay button,
it runs through the backend (a browser can't OCR reliably, and the offline artifact
can't call out) — see flipscout/server.py.

Two extractors, auto-selected by what you have configured:
  * ClaudeVisionExtractor — best quality. Sends the image to Claude (a vision model)
    and gets back structured {name, price, condition}. Needs ANTHROPIC_API_KEY.
  * TesseractExtractor — free fallback. Runs local OCR (needs the `tesseract` binary
    + `pip install ".[scan]"`) and parses the text with the same heuristics the web
    app uses for pasted listings.

Nothing here touches Facebook — it reads an image you captured and handed over.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Optional, Protocol

# Mirror of the web app's parseListing() (web/index.html), used for the OCR path so
# screenshot and paste capture behave identically.
_COND = r"(used|new|free|like new|open box|refurbished)"


def parse_listing_text(text: str) -> dict:
    """Pull {name, price} out of raw listing text (OCR output or a paste)."""
    text = (text or "").strip()
    if not text:
        return {"name": "", "price": None}
    pm = re.search(r"\$\s?([\d][\d,]*(?:\.\d{1,2})?)", text)
    price = float(pm.group(1).replace(",", "")) if pm else None

    def clean(line: str) -> str:
        line = re.sub(r"^\s*marketplace\s*[-–—:]\s*", "", line, flags=re.I)
        line = re.sub(rf"^\s*{_COND}\s*[·:\-–—]\s*", "", line, flags=re.I)
        line = re.sub(r"\s*\|\s*facebook.*$", "", line, flags=re.I)
        return line.strip()

    def is_noise(line: str) -> bool:
        return (not line
                or bool(re.match(r"^https?://|^www\.", line, re.I))
                or bool(re.match(r"^\$?\s?[\d,]+(\.\d{1,2})?$", line))
                or bool(re.match(r"^\d+\s*(mi|miles|km|k)\b", line, re.I))
                or bool(re.match(rf"^{_COND}$|^(good|fair)$", line, re.I))
                or bool(re.match(r"^(message|save|share|send seller|see (more|less)|"
                                 r"about this|details|listed|condition|location)\b", line, re.I)))

    name = ""
    for raw in re.split(r"\n+", text):
        line = clean(raw)
        if is_noise(line):
            continue
        name = line
        break
    name = re.sub(r"\s*·.*$", "", name)[:80].strip()
    return {"name": name, "price": price}


class VisionExtractor(Protocol):
    def extract(self, image_bytes: bytes, mime: str = "image/png") -> dict: ...


_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The item's title/name."},
        "price": {"type": "number", "description": "Asking price in dollars, 0 if none visible."},
        "condition": {"type": "string", "description": "Condition e.g. 'Used', or '' if unknown."},
    },
    "required": ["name", "price", "condition"],
    "additionalProperties": False,
}


class ClaudeVisionExtractor:
    """Reads the screenshot with Claude vision + structured output. Best quality."""

    def __init__(self, model: str = "claude-opus-4-8"):
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic  # lazy: only needed for this path
            self._client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY / ant profile
        return self._client

    def extract(self, image_bytes: bytes, mime: str = "image/png") -> dict:
        b64 = base64.standard_b64encode(image_bytes).decode()
        resp = self._get_client().messages.create(
            model=self.model,
            max_tokens=1024,
            system=("You extract structured data from a screenshot of an online "
                    "marketplace listing (e.g. Facebook Marketplace). Report the "
                    "item's title, its asking price as a number, and its condition."),
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": mime, "data": b64}},
                {"type": "text", "text": "Extract the listing. Price is dollars as a "
                                         "number (0 if not shown)."},
            ]}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("The image couldn't be read (declined).")
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        data = json.loads(text)
        price = data.get("price")
        return {
            "name": (data.get("name") or "").strip(),
            "price": float(price) if price and price > 0 else None,
            "condition": (data.get("condition") or "").strip() or None,
            "source": "claude_vision",
        }


class TesseractExtractor:
    """Free fallback: local OCR (needs the tesseract binary), then heuristic parse."""

    def extract(self, image_bytes: bytes, mime: str = "image/png") -> dict:
        import io
        import pytesseract  # lazy
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(io.BytesIO(image_bytes)))
        parsed = parse_listing_text(text)
        return {"name": parsed["name"], "price": parsed["price"],
                "condition": None, "source": "tesseract_ocr"}


def _tesseract_available() -> bool:
    try:
        import pytesseract
        from PIL import Image  # noqa: F401
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def get_extractor() -> VisionExtractor:
    """Auto-pick: Claude vision if credentialed, else Tesseract, else a clear error."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeVisionExtractor()
    if _tesseract_available():
        return TesseractExtractor()
    raise RuntimeError(
        "Screenshot scanning isn't set up. Set ANTHROPIC_API_KEY for best results, "
        "or install OCR with: pip install \".[scan]\" plus the tesseract binary. "
        "Until then, use Paste or type the item in."
    )

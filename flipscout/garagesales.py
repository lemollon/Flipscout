"""Garage/yard sales near home - a weekly "go look at these" digest.

Same philosophy as estates.py: these carry NO item prices, so this is
deliberately NOT a hunter and never emits a max bid. It answers "which sales
are worth a Saturday-morning drive", and the price book rides along as the
shopping list (cameras, calculators, Fluke meters, Featherweights).

Source: yardsalesearch.com - probed 2026-07-30 as the only aggregator that
serves real sale data headless. Craigslist's garage-sale category (gms) returns
an EMPTY ld+json ItemList (no-price posts are excluded from it), and gsalr.com
404s its guessable paths. YSS renders schema.org microdata server-side, which
is what's parsed here; if the itemprop attributes vanish, sales() returns []
and the digest just doesn't post (fail-soft, like every other source).

Pickup-only by nature, which is the point (Leron, 2026-07-30: no longer
trusting anybody to ship).
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import re
from typing import Optional

import requests

_TIMEOUT = 30
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_URL = "https://www.yardsalesearch.com/garage-sales.html?zip={}"

# One sale card starts at its itemprop="name" heading; parse each chunk
# independently so one malformed card can't take down the sweep.
_CARD_SPLIT = re.compile(r'<h2 itemprop="name">')
_FIELD = {
    "url": re.compile(r'<a itemprop="url" href="([^"]+)"'),
    "title": re.compile(r'<a itemprop="url" href="[^"]+">([^<]+)</a>'),
    "street": re.compile(r'itemprop="streetAddress">([^<]+)<'),
    "city": re.compile(r'itemprop="addressLocality">([^<]+)<'),
    "start": re.compile(r'itemprop="startDate" content="([^"]+)"'),
    "end": re.compile(r'itemprop="endDate" content="([^"]+)"'),
}


class YardSaleSearch:
    name = "garagesales"

    def __init__(self, zip_code: str, session: Optional[requests.Session] = None):
        self.zip = zip_code
        self.session = session or requests.Session()

    def fetch(self) -> str:
        r = self.session.get(_URL.format(self.zip),
                             headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.text

    def sales(self, html: Optional[str] = None,
              today: Optional[_dt.date] = None) -> list[dict]:
        """Upcoming/active sales, soonest first. [] on any failure."""
        try:
            html = html if html is not None else self.fetch()
        except Exception:
            return []
        today = today or _dt.date.today()
        out = []
        for chunk in _CARD_SPLIT.split(html)[1:]:
            chunk = chunk[:4000]         # a card is ~2KB; don't scan the page tail
            row = {k: (_html.unescape(p.search(chunk).group(1)).strip()
                       if p.search(chunk) else "") for k, p in _FIELD.items()}
            if not row["title"] or not row["url"]:
                continue
            try:
                if _dt.date.fromisoformat(row["end"]) < today:
                    continue             # already over
            except ValueError:
                pass                     # undated sales stay in - go look
            out.append(row)
        out.sort(key=lambda r: r.get("start") or "9999")
        return out


def digest(sales: list[dict], zip_code: str, cap: int = 12) -> str:
    if not sales:
        return ""
    lines = [f"🏡 **Garage sales near {zip_code}** - {len(sales)} upcoming. "
             f"No listed prices, so no max bids: this is the drive-by list. "
             f"Shopping list = the book: cameras, TI-84 CEs, Fluke/Mitutoyo, "
             f"Featherweights, iPods, Gunne Sax. Cash, pickup only.", ""]
    for s in sales[:cap]:
        when = s.get("start") or "?"
        if s.get("end") and s["end"] != s.get("start"):
            when += f" → {s['end']}"
        where = ", ".join(x for x in (s.get("street"), s.get("city")) if x)
        lines.append(f"• **{s['title'][:70]}** ({when})")
        lines.append(f"  {where}  <{s['url']}>")
    if len(sales) > cap:
        lines.append(f"…and {len(sales) - cap} more.")
    return "\n".join(lines)

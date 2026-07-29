"""Online estate sales and estate auctions happening near you.

This is deliberately NOT a hunter. Every source in `hunters.py` returns priced
listings that the price book can quote a max bid on. EstateSales.NET doesn't
expose item prices at all: its pages carry the SALE (who is running it, where,
when it ends, one photo), while the actual bidding happens on whatever platform
the organizer uses. Measured 2026-07-27 - a sale detail page has no item names,
no lot ids and no bid data in the HTML, so there is nothing to price.

Pretending otherwise would mean inventing numbers, so instead this feeds a
separate digest: "here are the estate sales and online estate auctions near you
this week, go look." That is a genuinely different product from a max-bid alert,
and worth having, because an estate sale is the one channel with no national
bidder pool at all - the structural reason the auction sources beat eBay.

Reachability (measured 2026-07-27, from 77441):
    EstateSales.NET   OK headless   SaleEvent ld+json, ~20 sales/page
    estatesales.org   client-rendered - 825KB, no ld+json, no sale links
    EBTH              client-rendered
    MaxSold           403 to scripts
    CTBids            token-walled (Bearer from a mint endpoint; browser tier)
"""

from __future__ import annotations

import json
import re
from typing import Optional

import requests

_TIMEOUT = 30
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# The kinds EstateSales.NET reports in `description`. The online ones are the
# ones you can act on from your desk, so they sort first.
ONLINE_KINDS = ("Online Only Auction", "Online Estate Sale")


class EstateSalesNet:
    """Estate sales + online estate auctions around one zip.

    The area path is `<STATE>/<City>/<zip>` exactly as the site builds it -
    e.g. TX/Fulshear/77441. It returns sales from the surrounding metro, not
    just that one town, which is what you want: the 77441 page carries sales in
    Stafford, Sugar Land and NW Houston.
    """

    name = "estatesales.net"
    BASE = "https://www.estatesales.net"

    def __init__(self, area: str = "TX/Fulshear/77441",
                 session: Optional[requests.Session] = None):
        self.area = (area or "").strip("/ ")
        self.session = session or requests.Session()

    @staticmethod
    def parse(html: str) -> list[dict]:
        """Pull SaleEvent ld+json blocks out of a listing page."""
        out, seen = [], set()
        for blob in re.findall(
                r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html or "", re.S):
            try:
                d = json.loads(blob)
            except Exception:
                continue
            for it in (d if isinstance(d, list) else [d]):
                if not isinstance(it, dict) or it.get("@type") != "SaleEvent":
                    continue
                url = it.get("url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                img = it.get("image")
                if isinstance(img, list):
                    img = img[0] if img else ""
                org = it.get("organizer") or {}
                kind = (it.get("description") or "").strip()
                # City comes out of the sale url: /TX/Stafford/77477/5013303
                bits = [b for b in url.split("/") if b]
                city = bits[-3].replace("-", " ") if len(bits) >= 4 else ""
                out.append({
                    "source": "estatesales.net",
                    "id": bits[-1] if bits else url,
                    "title": (it.get("name") or "").strip(),
                    "url": url,
                    "image": img or "",
                    "kind": kind,
                    "online": kind in ONLINE_KINDS,
                    "company": (org.get("name") or "").strip(),
                    "phone": (org.get("telephone") or "").strip(),
                    "city": city,
                    "starts": (it.get("startDate") or "")[:10],
                    "ends": (it.get("endDate") or "")[:10],
                })
        # Online-biddable first, then whatever ends soonest.
        out.sort(key=lambda s: (not s["online"], s["ends"] or "9999"))
        return out

    def sales(self, limit: int = 12) -> list[dict]:
        try:
            r = self.session.get(f"{self.BASE}/{self.area}",
                                 headers={"User-Agent": _UA,
                                          "Accept-Language": "en-US,en;q=0.9"},
                                 timeout=_TIMEOUT)
            r.raise_for_status()
            return self.parse(r.text)[:limit]
        except Exception:
            return []

    def hibid_catalog_ids(self, sales: list[dict]) -> list[int]:
        """Which of these sales are HiBid catalogs in disguise.

        Measured 2026-07-29 from Fulshear: 3 of the 6 online estate auctions
        in the digest linked straight to hibid.com catalogs - lots that never
        surface in HiBid's own keyword search unless the title happens to hit
        a term. Resolving the ids lets the watcher sweep EVERY lot of every
        nearby online estate auction through the book, which is the difference
        between handing Leron links and doing the work for him."""
        ids: list[int] = []
        for s in sales:
            if not s.get("online"):
                continue
            try:
                r = self.session.get(s["url"], headers={"User-Agent": _UA},
                                     timeout=_TIMEOUT)
                r.raise_for_status()
                for m in re.findall(r"hibid\.com/catalog/(\d+)", r.text, re.I):
                    aid = int(m)
                    if aid not in ids:
                        ids.append(aid)
            except Exception:
                continue                     # one dead page never kills the sweep
        return ids


def digest(sales: list[dict], area_label: str = "you") -> str:
    """Render the sales as one Discord message. Returns "" when there's nothing,
    so the caller can stay quiet rather than posting an empty heading."""
    if not sales:
        return ""
    online = [s for s in sales if s["online"]]
    lines = [f"**Estate sales near {area_label}** - {len(sales)} live "
             f"({len(online)} biddable online)"]
    for s in sales:
        when = f"ends {s['ends']}" if s["ends"] else ""
        where = s["city"] or ""
        tag = ":computer: " if s["online"] else ":house: "
        bits = " - ".join(x for x in (where, s["kind"], when) if x)
        lines.append(f"{tag}[{s['title'][:80]}]({s['url']}) - {bits}")
        if s["company"]:
            lines.append(f"    _{s['company']}_")
    lines.append("_No prices here on purpose - these sites don't publish item "
                 "prices, so go look at the photos yourself._")
    return "\n".join(lines)[:1900]

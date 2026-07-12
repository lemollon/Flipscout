"""Buying sources beyond eBay — where you *source*, priced against eBay for resale.

A source only needs `active_listings(query) -> [{title, price, url, item_id}]` and a
`name`; the scanner comps every source against eBay (`ebay_api.EbayApiComps`) and
ranks by profit-per-hour. That's the whole plug-in contract, so new pools drop in
here without touching the engine.

ShopGoodwill is the standout second source: Goodwill's national online auctions,
thrift prices, ships to you — a classic reseller arbitrage pool. Its buyer API is
public-ish but unofficial, so this adapter is **best-effort and fail-soft**: any
error returns an empty list rather than breaking the scan. Verify it live once
deployed; if Goodwill changes the API, only `parse_goodwill` / the request here
need a tweak.
"""

from __future__ import annotations

import time
from typing import Optional

import requests

_GOODWILL_SEARCH = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"
_TIMEOUT = 20

# ShopGoodwill's WAF 403s non-browser clients; a browser-shaped identity gets through.
_GOODWILL_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Origin": "https://shopgoodwill.com",
    "Referer": "https://shopgoodwill.com/",
}


def parse_goodwill(body: dict) -> list[dict]:
    """[{title, price, url, item_id}] from a ShopGoodwill search response. Uses the
    current bid (or opening bid) as the buy price — what you'd pay right now."""
    items = ((body or {}).get("searchResults") or {}).get("items") or []
    out: list[dict] = []
    for it in items:
        price = it.get("currentPrice", it.get("minimumBid"))
        if price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        item_id = str(it.get("itemId") or it.get("id") or "")
        out.append({
            "title": it.get("title") or "",
            "price": price,
            "url": f"https://shopgoodwill.com/item/{item_id}" if item_id else "",
            "item_id": item_id,
        })
    return out


class ShopGoodwillSource:
    """Goodwill's national online auctions. Ships to you; thrift-priced. Best-effort."""

    name = "goodwill"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()

    def active_listings(self, query: str, limit: int = 40, local: bool = False,
                        zip_code: Optional[str] = None, **kw) -> list[dict]:
        # ShopGoodwill is a ship-to-you auction site — it has no local-pickup mode,
        # so `local` is ignored here (the scanner still comps + ranks the same way).
        # The API 500s unless the full search form is present, so send it all with
        # the site's defaults (US shipping, open auctions, price ascending).
        body = {
            "catIds": "",
            "categoryId": 0,
            "categoryLevel": 1,
            "categoryLevelNo": "1",
            "closedAuctionDaysBack": "7",
            "closedAuctionEndingDate": time.strftime("%m/%d/%Y"),
            "highPrice": "999999",
            "isFromHeaderMenuTab": False,
            "isFromHomePage": False,
            "isMultipleCategoryIds": False,
            "isSize": False,
            "isWeddingCatagory": "false",
            "layout": "grid",
            "lowPrice": "0",
            "page": "1",
            "pageSize": str(min(limit, 40)),
            "partNumber": "",
            "savedSearchId": 0,
            "searchBuyNowOnly": "",
            "searchCanadaShipping": "false",
            "searchClosedAuctions": "false",
            "searchDescriptions": "false",
            "searchInternationalShippingOnly": "false",
            "searchNoPickupOnly": "false",
            "searchOneCentShippingOnly": "false",
            "searchPickupOnly": "false",
            "searchText": query,
            "searchUSOnlyShipping": "true",
            "selectedCategoryIds": "",
            "selectedSellerIds": "",
            "sortColumn": "1",
            "sortDescending": "false",
            "useBuyerPrefs": "true",
        }
        try:
            resp = self.session.post(_GOODWILL_SEARCH, json=body, timeout=_TIMEOUT,
                                     headers=_GOODWILL_HEADERS)
            resp.raise_for_status()
            return parse_goodwill(resp.json())[:limit]
        except Exception:
            return []  # fail-soft: a flaky source never breaks the whole scan


# name -> factory, for building a source list from a CLI/API "sources" param.
def build_sources(names: list[str], ebay) -> list:
    """Map source names to instances. `ebay` is the shared EbayApiComps (used as
    both the eBay buying source and the resale comp for every source)."""
    out = []
    for n in names:
        n = n.strip().lower()
        if n in ("ebay", ""):
            out.append(ebay)
        elif n in ("goodwill", "shopgoodwill"):
            out.append(ShopGoodwillSource())
    return out or [ebay]

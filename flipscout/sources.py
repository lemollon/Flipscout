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

from typing import Optional

import requests

_GOODWILL_SEARCH = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"
_TIMEOUT = 20


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
        body = {
            "searchText": query,
            "page": 1,
            "pageSize": min(limit, 40),
            "isSize": False,
            "isWeddingCatagory": "false",
            "isMultipleCategoryIds": False,
            "isFromHeaderMenuTab": False,
            "layout": "grid",
            "searchListType": "keyword",
        }
        try:
            resp = self.session.post(_GOODWILL_SEARCH, json=body, timeout=_TIMEOUT,
                                     headers={"Accept": "application/json"})
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

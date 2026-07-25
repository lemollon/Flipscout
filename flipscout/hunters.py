"""Where we hunt. One adapter per buying pool, one shape out.

Reachability was measured 2026-07-25, because it decides the whole architecture:

    ShopGoodwill  JSON API      OK headless   high volume, 0-1 bids typical
    HiBid         GraphQL       OK headless   1000s of regional auction houses
    eBay          --            BLOCKED       WAF; browser-only (see ebay_ui)
    Mercari       --            403           browser-only
    OfferUp       --            403           browser-only
    GovDeals      SPA           shell only    results are XHR; endpoint TBD
    PublicSurplus SPA           shell only    same

The blocked ones aren't gone, they're just a different tier: `flipscout comp`
drives them through your own browser. Everything here runs unattended.

Why HiBid matters: eBay auctions for the same item ran 13-33 bidders on 2026-07-25,
while regional auction houses on HiBid had a TI-84 Plus CE sitting at a $1 opening
bid with zero bids. Competition, not price, is what you're shopping for.

Every hunter returns dicts with a common shape:
    source, id, title, url, price, min_bid, increment, bids, handling, image, ends
"""

from __future__ import annotations

import re
import time
from typing import Optional

import requests

_TIMEOUT = 30
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# --- ShopGoodwill -----------------------------------------------------------

_GW_SEARCH = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"
_GW_DETAIL = "https://buyerapi.shopgoodwill.com/api/ItemDetail/GetItemDetailModelByItemId/{}"
_GW_HEADERS = {
    "Accept": "application/json", "Content-Type": "application/json", "User-Agent": _UA,
    "Origin": "https://shopgoodwill.com", "Referer": "https://shopgoodwill.com/",
}


def _gw_body(query: str, page: int = 1, size: int = 40) -> dict:
    # The API 500s unless the whole search form is present.
    return {
        "catIds": "", "categoryId": 0, "categoryLevel": 1, "categoryLevelNo": "1",
        "closedAuctionDaysBack": "7", "closedAuctionEndingDate": time.strftime("%m/%d/%Y"),
        "highPrice": "999999", "isFromHeaderMenuTab": False, "isFromHomePage": False,
        "isMultipleCategoryIds": False, "isSize": False, "isWeddingCatagory": "false",
        "layout": "grid", "lowPrice": "0", "page": str(page), "pageSize": str(size),
        "partNumber": "", "savedSearchId": 0, "searchBuyNowOnly": "",
        "searchCanadaShipping": "false", "searchClosedAuctions": "false",
        "searchDescriptions": "false", "searchInternationalShippingOnly": "false",
        "searchNoPickupOnly": "false", "searchOneCentShippingOnly": "false",
        "searchPickupOnly": "false", "searchText": query, "searchUSOnlyShipping": "true",
        "selectedCategoryIds": "", "selectedSellerIds": "", "sortColumn": "1",
        "sortDescending": "false", "useBuyerPrefs": "true",
    }


class ShopGoodwill:
    """Goodwill's national online auctions. Highest volume of the headless sources."""

    name = "goodwill"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()

    def search(self, query: str, limit: int = 40) -> list[dict]:
        try:
            r = self.session.post(_GW_SEARCH, json=_gw_body(query, size=limit),
                                  headers=_GW_HEADERS, timeout=_TIMEOUT)
            r.raise_for_status()
            items = ((r.json() or {}).get("searchResults") or {}).get("items") or []
        except Exception:
            return []                      # fail-soft: one dead source never kills a run
        out = []
        for it in items:
            try:
                price = float(it.get("currentPrice") or it.get("minimumBid") or 0)
            except (TypeError, ValueError):
                continue
            iid = str(it.get("itemId") or "")
            out.append({
                "source": self.name, "id": iid, "title": (it.get("title") or "").strip(),
                "url": f"https://shopgoodwill.com/item/{iid}",
                "price": price,
                "min_bid": float(it.get("minimumBid") or 0) or None,
                "increment": 1.0,
                "bids": it.get("numBids"),
                # NOTE: search results report shippingPrice 0 / unpopulated. Real
                # handling only comes from the detail endpoint - see enrich().
                "handling": None,
                "image": (it.get("imageURL") or "").replace("\\", "/"),
                "ends": (it.get("endTime") or "")[:16],
            })
        return out

    def enrich(self, row: dict) -> dict:
        """Fetch handling + full-res photos. Costs a request, so only call it on
        listings that already look like candidates."""
        try:
            r = self.session.get(_GW_DETAIL.format(row["id"]), headers=_GW_HEADERS,
                                 timeout=_TIMEOUT)
            r.raise_for_status()
            d = r.json() or {}
        except Exception:
            return row
        base = d.get("imageServer", "")
        imgs = [base + x.replace("\\", "/")
                for x in (d.get("imageUrlString") or "").split(";") if x.strip()]
        row = dict(row)
        row["handling"] = float(d.get("handlingPrice") or 0)
        row["increment"] = float(d.get("bidIncrement") or 1.0)
        if d.get("minimumBid"):
            row["min_bid"] = float(d["minimumBid"])
        if imgs:
            row["image"] = imgs[0]
            row["images"] = imgs[:4]
        row["description"] = re.sub(r"<[^>]+>", " ", d.get("description") or "")[:400]
        return row


# --- HiBid ------------------------------------------------------------------

_HIBID_GQL = "https://hibid.com/graphql"
_HIBID_HEADERS = {
    "Accept": "application/json", "Content-Type": "application/json", "User-Agent": _UA,
    "Origin": "https://hibid.com", "Referer": "https://hibid.com/",
}

# Rebuilt minimal form of the site's own LotSearch operation. Only the fields we
# price on, so a schema change breaks loudly instead of silently.
_HIBID_QUERY = """
query LotSearch($searchText: String, $pageNumber: Int!, $pageLength: Int!,
                $status: AuctionLotStatus, $sortOrder: EventItemSortOrder) {
  lotSearch(
    input: {searchText: $searchText, status: $status, sortOrder: $sortOrder, countAsView: false}
    pageNumber: $pageNumber
    pageLength: $pageLength
    sortDirection: DESC
  ) {
    pagedResults {
      totalCount
      results {
        id
        itemId
        lead
        lotNumber
        description
        featuredPicture { thumbnailLocation fullSizeLocation }
        lotState { bidCount highBid minBid isClosed buyNow }
        auction { id }
      }
    }
  }
}
"""


class HiBid:
    """Aggregates thousands of regional auction houses - far thinner competition
    than eBay for the same goods. Caveat: many lots are LOCAL PICKUP ONLY, and
    shipping (when offered) is set by the individual auctioneer, so treat
    inbound cost as unknown until you read the lot terms."""

    name = "hibid"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()

    def search(self, query: str, limit: int = 50) -> list[dict]:
        payload = {
            "operationName": "LotSearch", "query": _HIBID_QUERY,
            "variables": {"searchText": query, "pageNumber": 1, "pageLength": limit,
                          "status": "OPEN", "sortOrder": "NO_ORDER"},
        }
        try:
            r = self.session.post(_HIBID_GQL, json=payload, headers=_HIBID_HEADERS,
                                  timeout=_TIMEOUT)
            r.raise_for_status()
            d = r.json() or {}
            if d.get("errors"):
                return []
            res = ((((d.get("data") or {}).get("lotSearch") or {})
                    .get("pagedResults") or {}).get("results")) or []
        except Exception:
            return []
        out = []
        for L in res:
            st = L.get("lotState") or {}
            if st.get("isClosed"):
                continue
            pic = L.get("featuredPicture") or {}
            lot_id = L.get("id")
            out.append({
                "source": self.name, "id": str(lot_id),
                "title": (L.get("lead") or "").strip(),
                "url": f"https://hibid.com/lot/{lot_id}",
                "price": float(st.get("highBid") or 0),
                "min_bid": float(st.get("minBid") or 0) or None,
                "increment": 1.0,
                "bids": st.get("bidCount"),
                "handling": None,          # auctioneer-set; unknown from search
                "image": pic.get("fullSizeLocation") or pic.get("thumbnailLocation") or "",
                "ends": "",
                "description": (L.get("description") or "")[:400],
                "pickup_risk": True,       # many HiBid lots are pickup-only
            })
        return out


# --- registry ---------------------------------------------------------------

HUNTERS = {"goodwill": ShopGoodwill, "hibid": HiBid}


def build_hunters(names: Optional[list] = None) -> list:
    names = names or list(HUNTERS)
    return [HUNTERS[n]() for n in names if n in HUNTERS]

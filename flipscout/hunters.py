"""Where we hunt. One adapter per buying pool, one shape out.

Reachability was measured 2026-07-25, because it decides the whole architecture:

    ShopGoodwill  JSON API      OK headless   high volume, 0-1 bids typical
    HiBid         GraphQL       OK headless   1000s of regional auction houses
    eBay          --            BLOCKED       WAF; browser-only (see ebay_ui)
    Mercari       --            403           browser-only
    OfferUp       --            403           browser-only
    GovDeals      SPA           shell only    results are XHR; endpoint TBD
    PublicSurplus SPA           shell only    same

Second sweep 2026-07-27, hunting estate sales and auction houses near 77441:

    Nellis        Remix loader  OK headless   ADDED - Katy + SW Houston pickup
    HiBid geo     GraphQL       OK headless   ADDED - zip+miles, 50k lots <60mi
    EstateSales   ld+json       OK headless   ADDED - see estates.py (no prices)
    PublicSurplus form          DECOY         `keyword=` is IGNORED on browse/
                                              home - "fluke", "calculator" and
                                              "texas instruments" all return the
                                              SAME featured lots. Real search is
                                              XHR. Nearly shipped as a source.
    Proxibid      --            403 WAF       browser tier
    MaxSold       --            403 WAF       browser tier
    CTBids        SPA           token-walled  api.ctbids.com/services wants a
                                              Bearer; browsing is anonymous in
                                              the app, so a mint endpoint exists
    AuctionNinja  PHP           no results    search_mid.php returns 0 bytes
    AuctionZip    JS-built      shell only    results eval'd client-side
    estatesales.org             shell only    825KB, no ld+json, no sale links

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

    def _query(self, query: str, limit: int, buy_now_only: bool) -> list[dict]:
        body = _gw_body(query, size=limit)
        if buy_now_only:
            # The form field was sitting there empty the whole time. Set to
            # "true" it returns Goodwill's Buy-It-Now inventory: FIXED prices,
            # no bidding war, buy outright. Measured 2026-07-28: 40 items per
            # broad term, including a $12.99 Gunne Sax against a $122 comp.
            body["searchBuyNowOnly"] = "true"
        try:
            r = self.session.post(_GW_SEARCH, json=body,
                                  headers=_GW_HEADERS, timeout=_TIMEOUT)
            r.raise_for_status()
            return ((r.json() or {}).get("searchResults") or {}).get("items") or []
        except Exception:
            return []                      # fail-soft: one dead source never kills a run

    def search(self, query: str, limit: int = 40) -> list[dict]:
        # TWO passes, merged: the auction inventory this hunter always had,
        # plus the Buy-It-Now shelf. Buy-now first so that when an item shows
        # up in both, the row that survives is the one you can buy outright.
        out: dict[str, dict] = {}
        for buy_now in (True, False):
            for it in self._query(query, limit, buy_now):
                buy_price = it.get("buyNowPrice")
                try:
                    if buy_now:
                        price = float(buy_price or it.get("currentPrice") or 0)
                    else:
                        price = float(it.get("currentPrice") or it.get("minimumBid") or 0)
                except (TypeError, ValueError):
                    continue
                if buy_now and price <= 0:
                    continue
                iid = str(it.get("itemId") or "")
                if iid in out:
                    continue
                row = {
                    "source": self.name, "id": iid, "title": (it.get("title") or "").strip(),
                    "url": f"https://shopgoodwill.com/item/{iid}",
                    "price": price,
                    "min_bid": (price if buy_now
                                else float(it.get("minimumBid") or 0) or None),
                    "increment": 1.0,
                    "bids": 0 if buy_now else it.get("numBids"),
                    # NOTE: search results report shippingPrice 0 / unpopulated. Real
                    # handling only comes from the detail endpoint - see enrich().
                    "handling": None,
                    "image": (it.get("imageURL") or "").replace("\\", "/"),
                    "ends": (it.get("endTime") or "")[:16],
                    # When it was LISTED. Goodwill is the only source that exposes
                    # this: Craigslist's search JSON has no date field at all, and
                    # HiBid gives auction dates rather than per-lot listing dates.
                    "listed": (it.get("startTime") or "")[:16],
                }
                if buy_now:
                    # fixed-price semantics: the ask IS the number, alerts read
                    # "Asking / Don't pay over" and there is no proxy-bid walk-up
                    row["listing_type"] = "fixed"
                out[iid] = row
        return list(out.values())

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
                $status: AuctionLotStatus, $sortOrder: EventItemSortOrder,
                $zip: String, $miles: Int) {
  lotSearch(
    input: {searchText: $searchText, status: $status, sortOrder: $sortOrder,
            countAsView: false, zip: $zip, miles: $miles}
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
        shippingOffered
        featuredPicture { thumbnailLocation fullSizeLocation }
        lotState { bidCount highBid minBid isClosed buyNow }
        auction { id eventName auctioneer { name city state } }
      }
    }
  }
}
"""


class HiBid:
    """Aggregates thousands of regional auction houses - far thinner competition
    than eBay for the same goods.

    Runs TWO passes per term and merges them:

      * a NATIONAL pass, which is what this hunter always did, and
      * a LOCAL pass filtered to `zip` + `miles`, which is where the estate,
        farm and industrial houses around your own town show up.

    The local pass is the point. Measured 2026-07-27 from 77441 (Fulshear TX):
    "fluke" returned 67 lots nationally but 9 within 150 miles, and a generic
    "tools" search returned **4,711 lots inside that radius** - hundreds of
    regional auction houses that never surface in a national sort. Those lots
    are drivable, which means inbound shipping is $0 rather than ~$9, and that
    $9 is exactly what kills thin margins (see the structural floor in the
    project notes).

    Geo fields were found by probing the input type - `zip` (String) and
    `miles` (Int) are accepted, while zipCode/postalCode/radius/distance/
    latitude are all rejected as unknown fields, so don't "fix" the names.

    `shippingOffered` replaces the blanket pickup guess this used to make: the
    old code flagged EVERY HiBid lot as maybe-pickup-only, which trained you to
    ignore the warning. Now it only fires when the auctioneer really offers no
    shipping.
    """

    name = "hibid"

    def __init__(self, session: Optional[requests.Session] = None,
                 zip_code: Optional[str] = None, miles: Optional[int] = None):
        self.session = session or requests.Session()
        self.zip_code = zip_code or None
        self.miles = int(miles) if miles else None

    def _query(self, query: str, limit: int, zip_code: Optional[str],
               miles: Optional[int]) -> list[dict]:
        payload = {
            "operationName": "LotSearch", "query": _HIBID_QUERY,
            "variables": {"searchText": query, "pageNumber": 1, "pageLength": limit,
                          "status": "OPEN", "sortOrder": "NO_ORDER",
                          "zip": zip_code, "miles": miles},
        }
        try:
            r = self.session.post(_HIBID_GQL, json=payload, headers=_HIBID_HEADERS,
                                  timeout=_TIMEOUT)
            r.raise_for_status()
            d = r.json() or {}
            if d.get("errors"):
                return []
            return ((((d.get("data") or {}).get("lotSearch") or {})
                     .get("pagedResults") or {}).get("results")) or []
        except Exception:
            return []

    @staticmethod
    def _row(L: dict, nearby: bool) -> Optional[dict]:
        st = L.get("lotState") or {}
        if st.get("isClosed"):
            return None
        pic = L.get("featuredPicture") or {}
        auc = L.get("auction") or {}
        house = auc.get("auctioneer") or {}
        lot_id = L.get("id")
        ships = L.get("shippingOffered")
        return {
            "source": "hibid", "id": str(lot_id),
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
            # Who and where, so an alert can tell you whether it's a drive or a
            # gamble. HiBid's `city` comes through lowercased ("houston").
            "house": (house.get("name") or "").strip(),
            "city": (house.get("city") or "").strip().title(),
            "state": (house.get("state") or "").strip().upper(),
            "event": (auc.get("eventName") or "").strip(),
            "ships": bool(ships) if ships is not None else None,
            # Within driving range -> you collect it, so no inbound shipping.
            "local": nearby,
            "nearby": nearby,
            # Only a real warning now, not a blanket one.
            "pickup_risk": ships is False,
        }

    def search(self, query: str, limit: int = 50) -> list[dict]:
        out: dict[str, dict] = {}
        # Local first, so that when the same lot appears in both passes the row
        # that survives is the one flagged `nearby` (dict insert order wins).
        if self.zip_code and self.miles:
            for L in self._query(query, limit, self.zip_code, self.miles):
                row = self._row(L, nearby=True)
                if row:
                    out[row["id"]] = row
        for L in self._query(query, limit, None, None):
            row = self._row(L, nearby=False)
            if row and row["id"] not in out:
                out[row["id"]] = row
        return list(out.values())

    _CATALOG_QUERY = _HIBID_QUERY.replace(
        "$searchText: String,", "$auctionId: Int,").replace(
        "searchText: $searchText,", "auctionId: $auctionId,")

    def catalog_lots(self, auction_id: int, max_lots: int = 1000) -> list[dict]:
        """EVERY open lot of one auction, by catalog id.

        The keyword search only surfaces a lot when its title matches a term;
        an estate catalog resolved from EstateSales.NET should be swept in
        full - the junk-titled lot hiding the mju-II is the entire edge. Lots
        are flagged nearby: catalogs only come from the estate digest, which
        is already area-filtered. Capped because consignment houses run
        2,000-lot catalogs (measured) and 20 pages per catalog per run is
        the polite ceiling."""
        out: list[dict] = []
        page = 1
        while len(out) < max_lots:
            payload = {
                "operationName": "LotSearch", "query": self._CATALOG_QUERY,
                "variables": {"auctionId": int(auction_id), "pageNumber": page,
                              "pageLength": 100, "status": "OPEN",
                              "sortOrder": "NO_ORDER", "zip": None, "miles": None},
            }
            try:
                r = self.session.post(_HIBID_GQL, json=payload,
                                      headers=_HIBID_HEADERS, timeout=_TIMEOUT)
                r.raise_for_status()
                d = r.json() or {}
                if d.get("errors"):
                    break
                pr = (((d.get("data") or {}).get("lotSearch") or {})
                      .get("pagedResults") or {})
                results = pr.get("results") or []
            except Exception:
                break
            for L in results:
                row = self._row(L, nearby=True)
                if row:
                    out.append(row)
            if not results or len(out) >= (pr.get("totalCount") or 0):
                break
            page += 1
        return out[:max_lots]


# --- Craigslist -------------------------------------------------------------

# ONE metro by default, on purpose. The whole advantage of Craigslist is that you
# collect it yourself, so hunting cities you can't drive to just burns requests -
# and Craigslist rate-limits: sweeping 8 search terms x 10 cities returned ONE
# listing, while a single city returns 24. Set FLIPSCOUT_CL_CITIES to your metro.
_CL_CITIES = ["houston"]      # Fulshear TX -> the Houston craigslist region
_CL_DELAY = 0.8      # be a polite guest; this is somebody's free classifieds site


class Craigslist:
    """Fixed-price LOCAL inventory. Structurally different from the auctions:

      * no bidding and no waiting - you just buy it
      * **local pickup, so inbound shipping is $0**, which is worth more than it
        sounds: a flat $9 to ship something to you was quietly killing every thin
        margin in the book
      * prices are negotiable, so the ceiling doubles as your walk-away in person
      * no login needed to search

    Craigslist is per-city, not national, so `cities` decides your coverage.
    Set FLIPSCOUT_CL_CITIES to your own metro(s) - hunting a city you can't drive
    to is pointless, since the whole advantage is that you collect it yourself.

    Results come from the JSON-LD block the search page embeds; the visible HTML
    is rendered client-side and has no prices in it.
    """

    name = "craigslist"

    def __init__(self, cities: Optional[list] = None,
                 session: Optional[requests.Session] = None):
        self.cities = cities or _CL_CITIES
        self.session = session or requests.Session()

    @staticmethod
    def parse(html: str, city: str) -> list[dict]:
        """Pull listings out of the embedded JSON-LD search results.

        Two quirks of Craigslist's markup, both found the hard way:

        * The JSON-LD `url` field is **empty** on live results, and no post id
          appears anywhere in the HTML (the page is client-rendered). The first
          version keyed dedup on that empty string, so 40 distinct listings
          collapsed into one row.
        * So we key on the IMAGE url, which is unique per post, and link to a
          title search in the right city rather than the post itself. Their
          private API (`sapi.craigslist.org/web/v8/postings/search/full`) does
          carry ids, but they're offsets into a decode table and the
          reconstructed permalinks 404 - not worth the fragility for a link that
          a search reproduces reliably.
        """
        import json as _json
        from urllib.parse import quote_plus as _q
        m = re.search(r'<script[^>]*id="ld_searchpage_results"[^>]*>(.*?)</script>',
                      html or "", re.S)
        if not m:
            return []
        try:
            data = _json.loads(m.group(1))
        except Exception:
            return []
        out = []
        for el in data.get("itemListElement") or []:
            item = el.get("item") or {}
            offers = item.get("offers") or {}
            try:
                price = float(offers.get("price"))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            img = item.get("image")
            if isinstance(img, list):
                img = img[0] if img else ""
            title = (item.get("name") or "").strip()
            url = item.get("url") or offers.get("url") or ""
            if not url:
                url = f"https://{city}.craigslist.org/search/sss?query={_q(title[:70])}"
            # unique per post; the empty `url` field is not
            ident = (re.search(r"/(\d+)\.html", url).group(1)
                     if re.search(r"/(\d+)\.html", url)
                     else (re.search(r"/([0-9A-Za-z_]+)_\d+x\d+", img or "").group(1)
                           if re.search(r"/([0-9A-Za-z_]+)_\d+x\d+", img or "")
                           else f"{city}:{title[:40]}:{price}"))
            out.append({
                "source": "craigslist",
                "id": ident,
                "title": title,
                "url": url,
                "price": price,
                # Fixed price: there is no bid to place, so the "opening" number
                # IS the asking price and the ceiling is your negotiating limit.
                "min_bid": price,
                "increment": 0.0,
                "bids": None,
                "handling": 0.0,
                "image": img or "",
                "ends": "",
                "listing_type": "fixed",
                "local": True,          # -> inbound shipping is $0
                "city": city,
            })
        return out

    def search(self, query: str, limit: int = 40) -> list[dict]:
        out = []
        for i, city in enumerate(self.cities):
            if i:
                time.sleep(_CL_DELAY)
            try:
                r = self.session.get(
                    f"https://{city}.craigslist.org/search/sss",
                    params={"query": query}, headers={"User-Agent": _UA,
                                                      "Accept-Language": "en-US,en;q=0.9"},
                    timeout=_TIMEOUT)
                r.raise_for_status()
                out.extend(self.parse(r.text, city))
            except Exception:
                continue          # fail-soft per city
            if len(out) >= limit:
                break
        return out[:limit]


# --- Poshmark ---------------------------------------------------------------

_POSH_SEARCH = "https://poshmark.com/search"


class Poshmark:
    """Online secondhand clothing. Fixed price, ships to you, no login to search.

    Only serves the outerwear models (Arc'teryx, Patagonia) - there are no Fluke
    meters on a fashion resale site - and those are the thinnest margins in the
    book, so treat it as breadth rather than the main event.

    Parsing note: Poshmark renders server-side (no XHR for search results at all),
    so it works headless. Titles come from the ld+json ItemList urls, where the
    slug IS the title; prices come from the rendered tiles. The two lists came
    back the same length and in the same order on every query tested, but that
    alignment is an assumption about their markup, so parse() refuses to guess
    when the counts disagree rather than pairing the wrong price to an item.

    The other thrift sites were checked and rejected 2026-07-26:
      goodwillfinds.com  DNS_PROBE_FINISHED_NXDOMAIN - the domain is gone
      ThredUp / Mercari / eBid            403 to scripts
      Vinted / Curtsy / EBTH / satruck    client-rendered, no structured data
    """

    name = "poshmark"

    # Each search page is ~5MB. Throwing the full 25-term watchlist at it is
    # 125MB a run and gets throttled to zero results - and asking a fashion
    # resale site for "fluke multimeter" was never going to return anything.
    # Women's-apparel terms added 2026-07-28 with that category - this IS the
    # native channel for them.
    TERMS = ("arcteryx", "arc'teryx", "patagonia", "patagonia jacket",
             "gunne sax", "st john knit", "johnny was", "veronica beard",
             "reformation dress")

    def relevant_terms(self, terms: list) -> list:
        want = {t.lower() for t in self.TERMS}
        return [t for t in terms if t.lower() in want] or list(self.TERMS[:2])

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()

    @staticmethod
    def title_from_url(url: str) -> str:
        from urllib.parse import unquote
        slug = (url or "").rstrip("/").split("/listing/")[-1]
        slug = re.sub(r"-[0-9a-f]{24,}$", "", slug)      # drop the trailing id
        return unquote(slug).replace("-", " ").strip()

    @staticmethod
    def parse(html: str) -> list[dict]:
        import json as _json
        urls: list[str] = []
        for blob in re.findall(r'type="application/ld\+json">(\{.*?\})</script>',
                               html or "", re.S):
            try:
                d = _json.loads(blob)
            except Exception:
                continue
            if d.get("@type") == "ItemList":
                for el in d.get("itemListElement") or []:
                    if el.get("url"):
                        urls.append(el["url"])
        prices = re.findall(
            r'tile-grid-redesign__price-current[^>]*>\s*\$?([0-9][0-9,]*)', html or "")

        # Pairing by position is only safe while the counts match. If Poshmark
        # changes its markup this goes to zero rather than mispricing items.
        if not urls or len(urls) != len(prices):
            return []

        out = []
        for url, raw in zip(urls, prices):
            try:
                price = float(raw.replace(",", ""))
            except ValueError:
                continue
            if price <= 0:
                continue
            ident = (re.search(r"-([0-9a-f]{24,})$", url.rstrip("/")) or [None, url])[1]
            out.append({
                "source": "poshmark", "id": str(ident),
                "title": Poshmark.title_from_url(url),
                "url": url, "price": price,
                "min_bid": price, "increment": 0.0, "bids": None,
                "handling": 0.0, "image": "", "ends": "",
                "listing_type": "fixed", "local": False,
            })
        return out

    def search(self, query: str, limit: int = 40) -> list[dict]:
        time.sleep(0.5)          # 5MB a page; don't hammer it
        try:
            r = self.session.get(_POSH_SEARCH,
                                 params={"query": query, "type": "listings"},
                                 headers={"User-Agent": _UA,
                                          "Accept-Language": "en-US,en;q=0.9"},
                                 timeout=_TIMEOUT)
            r.raise_for_status()
            return self.parse(r.text)[:limit]
        except Exception:
            return []


# --- PropertyRoom -----------------------------------------------------------


class PropertyRoom:
    """Police / seized-property auctions: jewellery, watches, coins, electronics.

    Narrow overlap with this book - it carries iPods but no test gear or
    metrology ("fluke" there returns a Wyland painting *called* Fluke). And its
    iPod prices ran ABOVE our ceiling when measured (a 160GB at $250 against a
    $149.99 comp), partly because several are modded 128GB-SSD units.

    Kept anyway because it costs one request per term and fails soft: if nothing
    clears the ceiling nothing is alerted, and a cheap unit will get caught the
    day it appears.

    Search lives at /s/<query>; /search?q= silently returns the homepage, which
    is how the first version "worked" while returning nothing relevant.
    """

    name = "propertyroom"

    TERMS = ("ipod", "ipod classic", "ipod video")

    def relevant_terms(self, terms: list) -> list:
        want = {t.lower() for t in self.TERMS}
        return [t for t in terms if t.lower() in want] or list(self.TERMS)

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()

    @staticmethod
    def parse(html: str) -> list[dict]:
        out, seen = [], set()
        blocks = re.split(r'<div[^>]*class="[^"]*ListingContainer[^"]*"', html or "")[1:]
        for b in blocks:
            lid = (re.search(r'lid="(\d+)"', b) or [None, None])[1]
            if not lid or lid in seen:
                continue
            seen.add(lid)
            href = (re.search(r'href="(/l/[^"]+?/\d+)"', b) or [None, ""])[1]
            title = (re.search(r'class="product-name-category"><a[^>]*>([^<]{4,150})</a>', b)
                     or re.search(r'alt="([^"]{6,150})"', b) or [None, ""])[1]
            img = (re.search(r'<img[^>]+src="(https://content\.propertyroom\.com[^"]+)"', b)
                   or [None, ""])[1]
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", b))
            raw = (re.search(r"\$\s?([0-9][0-9,]*\.?\d{0,2})", text) or [None, None])[1]
            if not raw:
                continue
            try:
                price = float(raw.replace(",", ""))
            except ValueError:
                continue
            if price <= 0:
                continue
            bids = (re.search(r"(\d+)\s*bid", text, re.I) or [None, None])[1]
            out.append({
                "source": "propertyroom", "id": lid, "title": title.strip(),
                "url": f"https://www.propertyroom.com{href}" if href else "",
                "price": price, "min_bid": price, "increment": 1.0,
                "bids": int(bids) if bids else None,
                "handling": 0.0, "image": img, "ends": "",
                "listing_type": "auction", "local": False,
            })
        return out

    def search(self, query: str, limit: int = 40) -> list[dict]:
        from urllib.parse import quote
        time.sleep(0.4)
        try:
            r = self.session.get(f"https://www.propertyroom.com/s/{quote(query)}",
                                 headers={"User-Agent": _UA,
                                          "Accept-Language": "en-US,en;q=0.9"},
                                 timeout=_TIMEOUT)
            r.raise_for_status()
            return self.parse(r.text)[:limit]
        except Exception:
            return []


# --- Nellis Auction (local warehouses) --------------------------------------

_NELLIS = "https://www.nellisauction.com"
# From the site's own location picker (POST /change-shopping-location).
NELLIS_LOCATIONS = {"las vegas": 1, "phoenix": 2, "houston": 5,
                    "philadelphia": 6, "denver": 7, "dallas": 8}


class NellisAuction:
    """Retail-returns and overstock auctions run out of physical warehouses.

    Why it earns a slot: it is genuinely LOCAL. With the location set to
    Houston, lots sit in **Katy and SW Houston** - roughly 15-30 minutes from
    Fulshear - so you collect them yourself and inbound shipping is $0. Bid
    counts on measured samples ran 0-7 against Amazon retail prices, because
    the bidder pool is whoever will drive to that warehouse.

    Two things to be honest about:

      * It is EVERYTHING-goods (returns pallets), not a metrology or retro-game
        pool, so overlap with the price book is thin today and most of what it
        returns will be filtered out by the model guards. Measured 2026-07-27:
        "fluke" returned boat anchors and fluted wall panels, "ti-84" returned a
        calculator CASE. That is the ACCESSORY_EXCLUDE / brand-is-not-a-model
        machinery doing its job, and it is the reason this source is safe to add
        rather than a reason to skip it.
      * Everything here is pickup-only, so a win you don't collect is a loss.

    Transport: a Remix app. Appending `_data=routes/search` to the normal search
    URL returns the loader's JSON directly - no HTML parsing, no browser. The
    warehouse is chosen by a `__shopping-location` cookie, which is set by
    POSTing `shoppingLocationId` to /change-shopping-location; without it every
    result is Las Vegas.
    """

    name = "nellis"

    def __init__(self, location: str = "houston",
                 session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.location = (location or "houston").strip().lower()
        self._located = False

    def _set_location(self) -> None:
        """Pin the session to a warehouse. Failure is not fatal - it just means
        results come back for the default city, which the alert would then
        mislabel as local, so on failure we mark it and stop claiming local."""
        if self._located:
            return
        loc_id = NELLIS_LOCATIONS.get(self.location)
        if not loc_id:
            self._located = True
            return
        try:
            self.session.post(f"{_NELLIS}/change-shopping-location",
                              data={"shoppingLocationId": str(loc_id)},
                              headers={"User-Agent": _UA,
                                       "Content-Type": "application/x-www-form-urlencoded"},
                              timeout=_TIMEOUT)
        except Exception:
            pass
        self._located = True

    @staticmethod
    def parse(payload: dict, want_city: str = "") -> list[dict]:
        products = (payload or {}).get("products") or []
        # The loader echoes which warehouse it actually served. If the cookie
        # didn't take we are looking at another state's inventory, and calling
        # that "local pickup, $0 inbound" would be a lie that costs money.
        served = ((payload or {}).get("currentShoppingLocation") or {}).get("name") or ""
        is_local = (not want_city) or want_city.split(",")[0].strip().lower() in served.lower()
        out = []
        for p in products:
            if p.get("isClosed") or p.get("marketStatus") not in (None, "open"):
                continue
            try:
                price = float(p.get("currentPrice") or 0)
            except (TypeError, ValueError):
                continue
            photos = p.get("photos") or []
            img = ""
            for ph in photos:
                if isinstance(ph, dict) and ph.get("url"):
                    img = ph["url"]
                    break
            close = p.get("closeTime")
            if isinstance(close, dict):
                close = close.get("value") or ""
            loc = p.get("location")
            if isinstance(loc, dict):
                loc = loc.get("name") or ""
            out.append({
                "source": "nellis", "id": str(p.get("id") or ""),
                "title": (p.get("title") or "").strip(),
                "url": f"{_NELLIS}/p/{p.get('id')}",
                "price": price,
                # Nellis has no visible reserve: the next legal bid is $1 over.
                "min_bid": price + 1.0,
                "increment": 1.0,
                "bids": p.get("bidCount"),
                "handling": 0.0,
                "image": img,
                "ends": str(close)[:16],
                "listing_type": "auction",
                "local": is_local,
                "nearby": is_local,
                "pickup_risk": True,      # ALWAYS pickup - there is no shipping
                "city": loc or "",
                "house": f"Nellis Auction ({served})" if served else "Nellis Auction",
                "retail": p.get("retailPrice"),
            })
        return out

    def search(self, query: str, limit: int = 40) -> list[dict]:
        from urllib.parse import quote_plus
        self._set_location()
        time.sleep(0.3)
        try:
            r = self.session.get(
                f"{_NELLIS}/search?query={quote_plus(query)}&_data=routes%2Fsearch",
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=_TIMEOUT)
            r.raise_for_status()
            return self.parse(r.json(), want_city=self.location)[:limit]
        except Exception:
            return []


# --- Shopify-backed used-gear shops -----------------------------------------


class ShopifyStore:
    """Any Shopify storefront, via the built-in /search/suggest.json endpoint.

    Out&Back and GearTrade both sell USED outdoor gear and both run Shopify, so
    one adapter covers them - and any future Shopify resale shop is a one-line
    addition rather than a new parser.

    Two honest limits:
      * suggest.json returns at most 10 products per query no matter what
        resources[limit] says, so this is breadth, not depth.
      * these are shops, not individuals - they price used gear near market
        ($419 for an Arc'teryx parka), so expect most items to sit above the
        ceiling and get filtered out. The win is catching their clearance end.
    """

    def __init__(self, name: str, domain: str, terms: tuple,
                 session: Optional[requests.Session] = None):
        self.name = name
        self.domain = domain
        self.TERMS = terms
        self.session = session or requests.Session()

    def relevant_terms(self, terms: list) -> list:
        want = {t.lower() for t in self.TERMS}
        return [t for t in terms if t.lower() in want] or list(self.TERMS[:2])

    @staticmethod
    def parse(payload: dict, name: str, domain: str) -> list[dict]:
        products = (((payload or {}).get("resources") or {}).get("results") or {}).get("products") or []
        out = []
        for p in products:
            if p.get("available") is False:
                continue
            try:
                price = float(p.get("price"))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            url = p.get("url") or ""
            if url.startswith("/"):
                url = f"https://{domain}{url.split('?')[0]}"
            img = p.get("image") or ""
            if isinstance(p.get("featured_image"), dict):
                img = p["featured_image"].get("url") or img
            out.append({
                "source": name, "id": str(p.get("id") or p.get("handle") or ""),
                "title": (p.get("title") or "").strip(),
                "url": url, "price": price,
                "min_bid": price, "increment": 0.0, "bids": None,
                "handling": 0.0, "image": img, "ends": "",
                "listing_type": "fixed", "local": False,
            })
        return out

    def search(self, query: str, limit: int = 40) -> list[dict]:
        time.sleep(0.3)
        try:
            r = self.session.get(
                f"https://{self.domain}/search/suggest.json",
                params={"q": query, "resources[type]": "product",
                        "resources[limit]": "10"},
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=_TIMEOUT)
            r.raise_for_status()
            return self.parse(r.json(), self.name, self.domain)[:limit]
        except Exception:
            return []


_GEAR_TERMS = ("arcteryx", "arc'teryx", "patagonia", "patagonia jacket")

# Unclaimed Baggage sells the CONTENTS of airlines' lost luggage - cameras,
# electronics, designer apparel - fixed price, actually shipped, and it runs
# Shopify so the existing adapter covers it (probed live 2026-07-28: real
# camera inventory at $30-100). Terms span every book category that plausibly
# rides in a suitcase.
_LUGGAGE_TERMS = ("canon powershot", "canon g7x", "nikon coolpix", "sony cybershot",
                  "fujifilm finepix", "ipod classic", "apple ipod", "sony handycam",
                  "polaroid sx-70", "arcteryx", "patagonia", "johnny was",
                  "st john knit", "gunne sax", "reformation dress")


def _outandback():
    return ShopifyStore("outandback", "outandbackoutdoor.com", _GEAR_TERMS)


def _geartrade():
    return ShopifyStore("geartrade", "www.geartrade.com", _GEAR_TERMS)


def _unclaimedbaggage():
    return ShopifyStore("unclaimedbaggage", "www.unclaimedbaggage.com", _LUGGAGE_TERMS)


# --- eBay Browse: fixed-price BIN, the big pool ------------------------------

class EbayBrowse:
    """eBay Buy-It-Now listings via the official Browse API.

    Sleeps until the developer account is approved: without EBAY_CLIENT_ID /
    EBAY_CLIENT_SECRET in the environment every search returns [] and the
    sweep carries on - the hunter is wired NOW so approval day is a
    two-secret change, not a code change. Fixed-price only on purpose
    (Leron: buy outright, no bidding war); the book + deep-discount gate
    decide what's actually underpriced.
    """

    name = "ebay"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self._api = None
        try:
            from .ebay_api import EbayApiComps, EbayConfig
            self._api = EbayApiComps(EbayConfig.from_env(), session=self.session)
        except Exception:
            self._api = None            # keys absent: stay wired, stay silent

    @staticmethod
    def parse(body: dict) -> list[dict]:
        out = []
        for it in (body or {}).get("itemSummaries") or []:
            price = (it.get("price") or {}).get("value")
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            img = ((it.get("image") or {}).get("imageUrl")
                   or (it.get("thumbnailImages") or [{}])[0].get("imageUrl") or "")
            ship = None
            for so in it.get("shippingOptions") or []:
                cost = (so.get("shippingCost") or {}).get("value")
                if cost is not None:
                    try:
                        ship = float(cost)
                    except (TypeError, ValueError):
                        pass
                    break
            out.append({
                "source": "ebay", "id": str(it.get("itemId") or ""),
                "title": (it.get("title") or "").strip(),
                "url": it.get("itemWebUrl") or it.get("itemHref") or "",
                "price": price,
                "min_bid": price,           # fixed price: the ask IS the number
                "increment": 0.0,
                "bids": 0,
                "handling": ship,           # shipping cost when the API states it
                "image": img,
                "ends": "",
                "listing_type": "fixed",
                "local": False,
            })
        return out

    def search(self, query: str, limit: int = 50) -> list[dict]:
        if self._api is None:
            return []
        try:
            r = self.session.get(
                f"{self._api.cfg.host}/buy/browse/v1/item_summary/search",
                params={"q": query, "limit": min(limit, 50),
                        "filter": "buyingOptions:{FIXED_PRICE},conditions:{USED}"},
                headers=self._api._auth_header(), timeout=_TIMEOUT)
            r.raise_for_status()
            return self.parse(r.json() or {})[:limit]
        except Exception:
            return []


# --- GSA Auctions: federal surplus ------------------------------------------

class GSAAuctions:
    """US federal surplus via api.gsa.gov - a real public API, no scraping.

    One request returns EVERY open lot (~1,200), so this ignores the query and
    filters locally; sweep-level dedup collapses the repeats. Leron's call
    2026-07-28: geography doesn't matter as long as the goods can move - but
    be honest about what "delivery" means here: GSA releases to whoever shows
    up, INCLUDING a shipper you hire; it packs and mails NOTHING itself. Every
    row therefore carries pickup_risk so the alert says so.

    DEMO_KEY is rate-limited per IP (30/hr, shared on CI runners) - one call
    per run stays inside it, and a real key is a free 2-minute form at
    api.data.gov (FLIPSCOUT_GSA_KEY) if throttling ever shows up.
    """

    name = "gsa"
    _URL = "https://api.gsa.gov/assets/gsaauctions/v2/auctions"

    def __init__(self, api_key: Optional[str] = None,
                 session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.api_key = (api_key or "").strip() or "DEMO_KEY"
        self._cache: Optional[list] = None

    @staticmethod
    def parse(payload: dict) -> list[dict]:
        raw = (payload or {}).get("Results") or []
        rows = []
        for it in raw:
            if (it.get("auctionStatus") or "").lower() not in ("", "active", "open"):
                continue
            try:
                price = float(it.get("highBidAmount") or 0)
            except (TypeError, ValueError):
                price = 0.0
            lot = f"{it.get('saleNo') or ''}-{it.get('lotNo') or ''}"
            st = (it.get("locationST") or "").strip().upper()
            city = (it.get("locationCity") or "").strip().title()
            rows.append({
                "source": "gsa", "id": lot,
                "title": (it.get("itemName") or "").strip(),
                "url": (it.get("itemDescURL") or "https://gsaauctions.gov").strip(),
                "price": price,
                "min_bid": price + float(it.get("aucIncrement") or 1.0) if price else None,
                "increment": float(it.get("aucIncrement") or 1.0),
                "bids": int(it.get("biddersCount") or 0),
                "handling": None,
                "image": (it.get("imageURL") or "").strip(),
                "ends": (it.get("aucEndDt") or "")[:16],
                "house": (it.get("agencyName") or "GSA").strip(),
                "city": city, "state": st,
                "local": False, "nearby": st == "TX",
                # GSA never ships - the buyer removes it or sends a shipper.
                "pickup_risk": True,
            })
        return rows

    def _fetch(self) -> list[dict]:
        if self._cache is not None:
            return self._cache
        try:
            r = self.session.get(self._URL,
                                 params={"api_key": self.api_key, "format": "JSON"},
                                 timeout=_TIMEOUT)
            r.raise_for_status()
            self._cache = self.parse(r.json() or {})
        except Exception:
            self._cache = []
        return self._cache

    def search(self, query: str, limit: int = 40) -> list[dict]:
        q = (query or "").lower()
        rows = [r for r in self._fetch() if q in r["title"].lower()]
        return rows[:limit]


# --- registry ---------------------------------------------------------------

HUNTERS = {"goodwill": ShopGoodwill, "hibid": HiBid, "craigslist": Craigslist,
           "poshmark": Poshmark, "propertyroom": PropertyRoom,
           "nellis": NellisAuction,
           "outandback": _outandback, "geartrade": _geartrade,
           "unclaimedbaggage": _unclaimedbaggage, "gsa": GSAAuctions,
           "ebay": EbayBrowse}


def build_hunters(names: Optional[list] = None, env=None) -> list:
    import os
    env = env if env is not None else os.environ
    names = names or list(HUNTERS)
    # Where you actually live. HiBid uses it to add a local pass, Nellis to pick
    # a warehouse. Both fall back to national-only behaviour when it's unset,
    # so an unconfigured install still works.
    zip_code = (env.get("FLIPSCOUT_ZIP") or "").strip() or None
    miles = (env.get("FLIPSCOUT_RADIUS_MILES") or "").strip() or None
    built = []
    for n in names:
        if n not in HUNTERS:
            continue
        if n == "craigslist":
            cities = [c.strip() for c in env.get("FLIPSCOUT_CL_CITIES", "").split(",") if c.strip()]
            built.append(Craigslist(cities=cities or None))
        elif n == "hibid":
            built.append(HiBid(zip_code=zip_code, miles=miles))
        elif n == "nellis":
            built.append(NellisAuction(
                location=env.get("FLIPSCOUT_NELLIS_LOCATION", "houston")))
        elif n == "gsa":
            built.append(GSAAuctions(api_key=env.get("FLIPSCOUT_GSA_KEY")))
        else:
            built.append(HUNTERS[n]())
    return built

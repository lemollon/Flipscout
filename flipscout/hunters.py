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

import datetime as _dt
import os
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

    # Buy-It-Now pages we are willing to pull per term. 5 x 40 = 200, which is
    # far above any BIN count measured (the largest was 97, for "ti-84").
    # It exists so one pathological term cannot walk the whole catalogue.
    _BIN_MAX_PAGES = 5

    def _one_page(self, query: str, page: int, size: int,
                  buy_now_only: bool) -> tuple:
        """One page. Returns (items, itemCount) - itemCount is the server's own
        total for the query, which is what makes paging exact rather than a
        guess about whether another page exists."""
        body = _gw_body(query, page=page, size=size)
        if buy_now_only:
            # The form field was sitting there empty the whole time. Set to
            # "true" it returns Goodwill's Buy-It-Now inventory: FIXED prices,
            # no bidding war, buy outright. Measured 2026-07-28: 40 items per
            # broad term, including a $12.99 Gunne Sax against a $122 comp.
            body["searchBuyNowOnly"] = "true"
        r = self.session.post(_GW_SEARCH, json=body,
                              headers=_GW_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
        sr = ((r.json() or {}).get("searchResults") or {})
        return (sr.get("items") or []), (sr.get("itemCount") or 0)

    def _query(self, query: str, limit: int, buy_now_only: bool) -> list[dict]:
        """All BIN results for the query; first page only for auctions.

        🚨 The page size is capped SERVER-SIDE at 40 no matter what pageSize
        says - asking for 200 returns 40. So a term with more than 40 hits was
        silently truncated. Measured 2026-08-16 over 18 terms:

            BIN      296 exist, 177 fetched -> 119 MISSING (40%)
            AUCTION 1887 exist, 512 fetched -> 1375 MISSING (73%)

        Buy-It-Now is paged to completion because that is the half you can act
        on without winning a bidding war, and because it is SMALL - the largest
        BIN count measured on any term was 97, so this costs a handful of extra
        calls against a source with no quota.

        Auctions are deliberately NOT paged. 1,375 missing over 18 terms
        extrapolates to ~10k over the full 133-term book, which is a different
        decision about runtime and volume, not a bug fix. Auctions already
        supply ~96% of the board.
        """
        try:
            items, total = self._one_page(query, 1, limit, buy_now_only)
        except Exception:
            return []                      # fail-soft: one dead source never kills a run
        if not buy_now_only or not items:
            return items

        seen = {str(i.get("itemId")) for i in items}
        page = 2
        while len(items) < total and page <= self._BIN_MAX_PAGES:
            time.sleep(0.2)                # polite: this is a charity's server
            try:
                more, _ = self._one_page(query, page, limit, True)
            except Exception:
                # 🚨 Fail-soft PER PAGE, not per query. Wrapping the whole loop
                # in one try meant a 500 on page 2 discarded the 40 good rows
                # from page 1 - strictly worse than not paging at all, and a
                # partial outage would have read as "this term has nothing".
                break
            if not more:
                break
            fresh = [i for i in more if str(i.get("itemId")) not in seen]
            if not fresh:                  # server repeated a page - stop
                break
            seen.update(str(i.get("itemId")) for i in fresh)
            items.extend(fresh)
            page += 1
        return items

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


from .auctionfees import (parse_premium, premium_is_stated, parse_tax,
                          tax_is_stated, min_increment, card_accepted)

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
        lotState { bidCount highBid minBid isClosed buyNow timeLeftSeconds }
        auction {
          id eventName buyerPremium biddingNotice bidType paymentInfo
          bidIncrements { minBidIncrement upToAmount }
          auctioneer { name city state }
        }
      }
    }
  }
}
"""


def _hibid_ends(seconds) -> str:
    """A local wall-clock `ends` from HiBid's per-lot COUNTDOWN.

    🚨 HIBID ROWS CARRIED NO END TIME AT ALL - `ends` was hardcoded to "".
    hours_until's own docstring says "HiBid sends nothing", and that was true
    of what we ASKED for, not of what it has: every lot ships
    lotState.timeLeftSeconds. The cost was invisible until a closing-soon lane
    was added and matched zero HiBid lots (2026-08-19) - the one source whose
    bidding path is verified end to end could never be prioritised by urgency.

    🚨 USE THE COUNTDOWN, NOT auction.bidCloseDateTime. That is the AUCTION's
    close, carries no timezone, and under a staggered close individual lots end
    hours after it - three lots of one auction were measured 20 seconds apart
    while all reporting the same bidCloseDateTime.

    Emitted as naive local time because hours_until has no zone mapping for
    hibid and compares such values against the runner's clock.
    """
    import math
    try:
        left = float(seconds)
    except (TypeError, ValueError):
        return ""
    # 🚨 inf and nan reach datetime.timedelta as OverflowError / ValueError,
    # not as a bad number - and this runs inside the row builder, so ONE
    # poisoned lot took down the whole HiBid search. Measured 2026-08-19.
    if left == 0 or not math.isfinite(left):
        return ""
    # A countdown past ~10 years is nonsense; keep timedelta in sane territory.
    if abs(left) > 315_360_000:
        return ""
    # 🚨 THE SIGN IS UNRELIABLE, THE MAGNITUDE IS NOT. About half of search rows
    # come back with a negative countdown on lots that plainly have days to run
    # ("25d 4h 59m" alongside a negative number of seconds), the lot page agrees
    # with the search on the same value, and re-probing minutes later can return
    # it positive. So it is transient vendor noise, not "already ended".
    #
    # abs() is safe HERE because this only decides ALERT ORDER: the worst case
    # is a lot prioritised on the wrong clock, which costs one slot in a run.
    #
    # 🚨 DO NOT COPY THIS INTO THE SNIPER. hibidsnipe reads its own countdown
    # straight from the lot page and refuses to bid on a negative one, because
    # there the same guess would fire a real bid at the wrong moment. It polls
    # every minute, so a transient negative self-corrects.
    end = _dt.datetime.now() + _dt.timedelta(seconds=abs(left))
    return end.isoformat(timespec="minutes")


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
        # 🚨 The hammer is NOT the price. Every house adds a buyer's premium of
        # 10-20% at checkout, and until 2026-08-18 none of it reached the
        # ceiling, so all 330 HiBid cards on the board overstated profit by
        # about a fifth. There is no structured field for it - see auctionfees.
        premium = parse_premium(auc.get("buyerPremium"))
        stated = premium_is_stated(auc.get("buyerPremium"))
        # 🚨 Sales tax rides on hammer + premium and is charged at checkout too.
        # Only 18 of 217 sampled auctions state a rate, so the auctioneer's
        # STATE is the fallback - see auctionfees.
        pay = auc.get("paymentInfo")
        st_code = (house.get("state") or "").strip().upper()
        tax = parse_tax(pay, st_code)
        price = float(st.get("highBid") or 0)
        # ...and the step is NOT $1. Houses in the sample stepped by up to $550,
        # so a hardcoded 1.0 quoted opening bids that the site would reject.
        step = min_increment(price, auc.get("bidIncrements"))
        return {
            "source": "hibid", "id": str(lot_id),
            "title": (L.get("lead") or "").strip(),
            "url": f"https://hibid.com/lot/{lot_id}",
            "price": price,
            "min_bid": float(st.get("minBid") or 0) or None,
            "increment": step,
            "buyer_premium_rate": premium,
            "buyer_premium_guessed": not stated,
            "sales_tax_rate": tax,
            "sales_tax_guessed": not tax_is_stated(pay),
            # Leron pays by card. A cash/wire-only house is unwinnable for him
            # however good the price - see auctionfees.card_accepted.
            "card_ok": card_accepted(pay),
            "payment_info": (pay or "")[:120],
            # Free text, and the ONLY place a house states its soft close
            # ("bids in the last minute extend the close by 2 minutes").
            "bidding_notice": (auc.get("biddingNotice") or "").strip()[:400],
            "bid_type": auc.get("bidType") or "",
            "bids": st.get("bidCount"),
            "handling": None,          # auctioneer-set; unknown from search
            "image": pic.get("fullSizeLocation") or pic.get("thumbnailLocation") or "",
            "ends": _hibid_ends(st.get("timeLeftSeconds")),
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
        # 🚨 PER-LOT FAIL-SOFT. _row parses vendor JSON, and a single
        # malformed lot used to raise straight out of search() - which the
        # source-level handler then swallowed as "HiBid returned nothing".
        # A silent source outage is far worse than one skipped lot.
        def _rows(pairs, nearby):
            for L in pairs:
                try:
                    row = self._row(L, nearby=nearby)
                except Exception:
                    continue
                if row and row["id"] not in out:
                    out[row["id"]] = row

        if self.zip_code and self.miles:
            _rows(self._query(query, limit, self.zip_code, self.miles), True)
        _rows(self._query(query, limit, None, None), False)
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

    # 🚨 WIDENED 2026-08-16. This was ("ipod", "ipod classic", "ipod video"),
    # which is why production reported EXACTLY `propertyroom=10` on every run
    # for weeks - only two book terms ever matched the allowlist, so the source
    # was pinned to iPods regardless of how the book grew around it.
    #
    # The allowlist was right when the book was calculators, iPods and test
    # gear. Since then it gained watches (2026-08-13), cameras, and the console
    # pack (2026-08-16) - and this hunter's own docstring says it carries
    # "jewellery, watches, coins, electronics". It was blocking its own best
    # categories.
    #
    # Measured 2026-08-16 across the terms below: 130 unique listings, 37 of
    # them priced by the book - against 10 rows and 0 priced in production.
    # Best yields were "citizen watch" (16/16 priced), "ipod classic" (10/10),
    # "casio g-shock" (7/4), "xbox one" (15/2).
    #
    # Cost is one request per term on a source with no quota, so the ~20 extra
    # calls are cheap. Every term here must also exist in pricebook
    # search_terms(); a term that does not appear there is silently dead.
    TERMS = (
        # watches + jewellery - the category it is genuinely best at
        "citizen watch", "citizen eco-drive", "casio g-shock", "g shock watch",
        "seiko automatic", "seiko watch lot", "watch lot", "wristwatch lot",
        # ipods - the original entries, minus a bare "ipod" that is not in
        # search_terms() and so could never fire
        "ipod classic", "ipod video", "apple ipod", "ipod nano", "ipod touch",
        # cameras
        "nikon coolpix", "canon powershot", "sony cybershot", "digital camera",
        # consoles - seized electronics is full of them
        "nintendo switch", "xbox one", "playstation 4", "playstation 5",
        "xbox series x",
        # test gear, which it carries occasionally
        "fluke multimeter",
    )

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


def _slug(title) -> str:
    """Nellis-style URL slug from a listing title."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", (title or "").lower())).strip("-")[:80]
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
                # Nellis 404s /p/<id> alone - the route is /p/<slug>/<id>, and
                # ANY slug resolves (verified live 2026-07-31: /p/120603364 is
                # a 404, /p/x/120603364 loads). Leron: "all these nellisauction
                # links are broken" - every alert link was the bare-id form.
                "url": f"{_NELLIS}/p/{_slug(p.get('title')) or 'item'}/{p.get('id')}",
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


# DEAD AS OF 2026-08-15, kept only so the two stores below still construct.
# Every term here is apparel, and apparel is benched in the price book
# (pricebook.BENCHED_CATEGORIES), so `outandback` and `geartrade` can no longer
# price ANY listing they return - they are pure sweep cost. Both were removed
# from the live FLIPSCOUT_SOURCES the same day. If apparel is ever un-benched,
# add them back to that variable and this comes alive again unchanged.
_GEAR_TERMS = ("arcteryx", "arc'teryx", "patagonia", "patagonia jacket")

# Unclaimed Baggage sells the CONTENTS of airlines' lost luggage - cameras,
# electronics, designer apparel - fixed price, actually shipped, and it runs
# Shopify so the existing adapter covers it (probed live 2026-07-28: real
# camera inventory at $30-100). Terms span every book category that plausibly
# rides in a suitcase.
#
# The six apparel terms ("arcteryx", "patagonia", "johnny was", "st john knit",
# "gunne sax", "reformation dress") were REMOVED 2026-08-15. They were not
# merely unused: `relevant_terms()` intersects against `search_terms()`, and
# apparel is gone from there, so they could never match again. Leaving them in
# would have advertised apparel coverage this store no longer has - which is
# exactly the kind of stale claim that cost an audit an hour the same day.
# This store keeps earning its place on cameras and iPods.
_LUGGAGE_TERMS = ("canon powershot", "canon g7x", "nikon coolpix", "sony cybershot",
                  "fujifilm finepix", "ipod classic", "apple ipod", "sony handycam",
                  "polaroid sx-70")


def _outandback():
    return ShopifyStore("outandback", "outandbackoutdoor.com", _GEAR_TERMS)


def _geartrade():
    return ShopifyStore("geartrade", "www.geartrade.com", _GEAR_TERMS)


def _unclaimedbaggage():
    return ShopifyStore("unclaimedbaggage", "www.unclaimedbaggage.com", _LUGGAGE_TERMS)


# --- eBay Browse: fixed-price BIN, the big pool ------------------------------

class EbayBrowse:
    """eBay Buy-It-Now listings via the official Browse API.

    APPROVED AND LIVE since 2026-07-29 (the docstring here used to say it was
    "sleeping until the developer account is approved" and that misled an audit
    on 2026-08-15 into reporting we had no eBay API). Keys live in GitHub
    Actions secrets; the local .env is empty ON PURPOSE, so running the sweep
    from your laptop still returns [] for this source. That is not a bug - if
    every search comes back empty locally, check the environment before you
    check the code.

    Fixed-price only on purpose (Leron: buy outright, no bidding war); the book
    + deep-discount gate decide what's actually underpriced.
    """

    name = "ebay"

    # 🚨 THE TRIPWIRE FIRED, SO THE TERMS GET CUT - HERE, NOT EVERYWHERE.
    # Run 32552763979 (2026-08-22 04:50) printed `ebay: 149 search error(s)
    # this run (last: HTTP 429)` and returned ebay=0 listings, the first time
    # that counter has printed. It follows the card pack taking search_terms()
    # from 133 to 149 (+12%), which is exactly the condition the note above
    # says to act on: "if it starts printing, cut terms before anything else."
    #
    # But cutting them everywhere would undo the fix that made cards work at
    # all, and the quota problem is not shared: goodwill and hibid returned
    # 4,601 and 4,453 listings in that same run with no errors, and THEY are
    # where a card worth buying actually turns up. A card on eBay Browse is
    # already at retail - it is the one source where these terms buy the least
    # and cost the most.
    #
    # So Browse goes back to the 133 terms it was measured clean on, and every
    # other source keeps all 149. Same shape as the Poshmark and Nellis term
    # filters above.
    def relevant_terms(self, terms: list) -> list:
        from .pricebook import CARD_SEARCH_TERMS
        return [t for t in terms if t.lower() not in CARD_SEARCH_TERMS]

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self._api = None
        self._err_count = 0
        self._err_last = ""
        # AUCTION pass (Leron 2026-07-29: "bring this back" - ending-soon
        # auctions with low bids are the biggest underpriced pool on eBay; a
        # one-shot snipe at max bid is not a bidding war). Auctions sweep once
        # per hour: only on runs in the first half of the hour (the :17 cron;
        # the :47 run skips).
        #
        # QUOTA, re-checked against live logs 2026-08-15 (the old note here
        # sized this off "71 terms" and warned the tail would 429 near the end
        # of the UTC day - both stale):
        #   * search_terms() is 113 terms, not 71. That is 24 runs x 113 x 2
        #     calls + 24 x 113 x 1 = ~8.1k/day, well past the 5k/day Browse
        #     DEFAULT - so the Application Growth Check (ticket 260729-000022)
        #     appears to have been granted.
        #   * MEASURED over 16 consecutive runs spanning UTC midnight: zero
        #     429s, zero rate-limit lines, `auth OK` on every run, and NO
        #     drop-off late in the UTC day followed by a reset spike. There is
        #     no quota cliff to design around right now.
        #   * The listing count is BIMODAL - ~8,190 on :17 runs vs ~4,450 on
        #     :47 runs, a 1.83x split. That is THIS GATE working as intended
        #     (two passes vs one), not lost coverage. Do not "fix" it.
        # Re-check with the same method before adding another 40 terms.
        #
        # RE-CHECKED 2026-08-16, after the platform pack took search_terms()
        # from 113 to 133 (+17.7%):
        #   * projected ~9.6k calls/day (24 x 133 x 2 + 24 x 133 x 1), up from
        #     ~8.1k. Still no cliff found.
        #   * MEASURED over 8 consecutive runs: zero counted search errors.
        #     🚨 Check for the string `ebay: N search error(s)` that
        #     error_summary() emits - NOT for "429" in the raw log. Grepping
        #     the log for 429 matches TIMESTAMPS ("17:00:31.8429335Z") and
        #     reports phantom rate limiting. That false positive cost a
        #     re-check on 2026-08-16 before the right signal was used.
        #   * The bimodal split still holds: 7,905 listings on the :17-window
        #     run vs ~4,300 on the :47 runs.
        # The 5k "default" is clearly not what we are on - 8.1k/day ran clean
        # for a day - but the granted ceiling is still UNKNOWN, so this is
        # headroom by observation, not by contract. The error counter is the
        # tripwire; if it starts printing, cut terms before anything else.
        #   FLIPSCOUT_EBAY_AUCTIONS: "always" | "off" | unset (= hourly gate)
        mode = (os.environ.get("FLIPSCOUT_EBAY_AUCTIONS") or "").strip().lower()
        if mode == "always":
            self._auction_pass = True
        elif mode == "off":
            self._auction_pass = False
        else:
            self._auction_pass = _dt.datetime.utcnow().minute < 30
        try:
            from .ebay_api import EbayApiComps, EbayConfig
            self._api = EbayApiComps(EbayConfig.from_env(), session=self.session)
        except Exception:
            self._api = None            # keys absent: stay wired, stay silent

    @staticmethod
    def bid_increment(price: float) -> float:
        """eBay's published bid increment for a current price (USD bands)."""
        for cap, inc in ((1, 0.05), (5, 0.25), (25, 0.50), (100, 1.00),
                         (250, 2.50), (500, 5.00), (1000, 10.00),
                         (2500, 25.00), (5000, 50.00)):
            if price < cap:
                return inc
        return 100.00

    @staticmethod
    def parse(body: dict) -> list[dict]:
        out = []
        for it in (body or {}).get("itemSummaries") or []:
            buying = it.get("buyingOptions") or []
            is_auction = "AUCTION" in buying and "FIXED_PRICE" not in buying
            # Auctions carry the live bid in currentBidPrice; fixed-price in price.
            raw = ((it.get("currentBidPrice") or {}).get("value") if is_auction
                   else (it.get("price") or {}).get("value"))
            try:
                price = float(raw)
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
                # Fixed price: the ask IS the number. Auction: the next bid is
                # current + eBay's published increment for that price band.
                "min_bid": (price + EbayBrowse.bid_increment(price)
                            if is_auction and int(it.get("bidCount") or 0) > 0
                            else price),
                "increment": EbayBrowse.bid_increment(price) if is_auction else 0.0,
                "bids": int(it.get("bidCount") or 0),
                "handling": ship,           # shipping cost when the API states it
                "image": img,
                # itemEndDate feeds the existing ending-soon alert tier.
                "ends": (it.get("itemEndDate") or "") if is_auction else "",
                "listing_type": "auction" if is_auction else "fixed",
                "local": False,
                # Downstream pricing needs both of these: a for-parts item must
                # not be bid at working-item comps, and a Best Offer listing's
                # ask isn't its floor.
                "condition": (it.get("condition") or "").strip(),
                "best_offer": "BEST_OFFER" in buying,
            })
        return out

    def auth_probe(self) -> str:
        """One-line health check for the run log. search() fails soft, so a bad
        or mispasted key is otherwise indistinguishable from an empty market —
        which is exactly how a wrong EBAY_CLIENT_SECRET hid on go-live day.
        The OAuth error body ('invalid_client', 'unsupported_grant_type', ...)
        never contains the credentials, so it is safe to print."""
        if self._api is None:
            return "ebay: keys absent - hunter asleep"
        try:
            self._api._auth_header()
            return "ebay: auth OK"
        except Exception as e:
            body = getattr(getattr(e, "response", None), "text", "") or ""
            return f"ebay: AUTH FAILED - {e} {body[:200]}"

    _NOT_NEW = "conditionIds:{2000|2500|2750|3000|4000|5000|6000|7000}"

    def _one_search(self, query: str, limit: int, buying: str, sort: str) -> list[dict]:
        r = self.session.get(
            f"{self._api.cfg.host}/buy/browse/v1/item_summary/search",
            params={"q": query, "limit": min(limit, 50), "sort": sort,
                    "filter": f"buyingOptions:{{{buying}}},{self._NOT_NEW}"},
            headers=self._api._auth_header(), timeout=_TIMEOUT)
        r.raise_for_status()
        return self.parse(r.json() or {})[:limit]

    def search(self, query: str, limit: int = 50) -> list[dict]:
        if self._api is None:
            return []
        # conditionIds instead of conditions:{USED}: the named USED bucket
        # excludes 7000 ("For parts or not working"), which for cameras is
        # exactly the discounted cohort the book was measured to exploit
        # (untested SX-70s at $40-85 vs $100 working). The id list is every
        # not-new grade: refurbs, Used, media grades, parts.
        # newlyListed sort: underpriced BINs die in minutes, so fresh listings
        # beat best-match popularity for deal-hunting.
        #
        # ⛔ DO NOT switch this to price-ascending. It is the obvious idea when
        # someone asks for more Buy-It-Now, and it is wrong - MEASURED
        # 2026-08-16 over three terms, share of results that are parts /
        # accessories / empty boxes rather than the product:
        #
        #     term                      price+ship ASC    newlyListed
        #     canon ae-1                        63%            18%
        #     nintendo switch console           68%            28%
        #     singer featherweight              56%            53%
        #
        # The cheapest listings are cheap BECAUSE they are parts. Sorting by
        # price aims the feed straight at the cohort the accessory guard exists
        # to reject, and the fixed-price feed is already the worst offender for
        # it (22% of live BIN board items were components).
        #
        # ⛔ DO NOT add an offset page here either. limit is capped at 50/term
        # and there is no paging on purpose: a second page doubles eBay Browse
        # calls, and Browse is the ONE quota-metered source. We run ~9.6k
        # calls/day against a granted ceiling nobody has measured. eBay is 52%
        # of all swept volume, so trading a silent degradation of the largest
        # source for some extra BIN is a bad bet. Goodwill BIN paging (free,
        # unmetered) was the right place to buy that coverage instead.
        passes = [("FIXED_PRICE", "newlyListed")]
        # Ending-soon auctions with low bids - swept hourly (see __init__ quota
        # math), sorted by soonest close so the 50-row window holds the lots
        # whose price is nearly final.
        if self._auction_pass:
            passes.append(("AUCTION", "endingSoonest"))
        rows: list[dict] = []
        for buying, sort in passes:
            try:
                rows += self._one_search(query, limit, buying, sort)
            except Exception as e:
                # Fail-soft PER PASS (an auction-pass 429 must not discard the
                # fixed-price rows), but COUNT it: the two passes together run
                # ~5.1k calls/day against the 5k default Browse quota, and a
                # 429 storm would otherwise be indistinguishable from a quiet
                # market (the same trap the auth probe closed for bad keys).
                self._err_count += 1
                resp = getattr(e, "response", None)
                self._err_last = (f"HTTP {resp.status_code}" if resp is not None
                                  else str(e))[:120]
        return rows

    def error_summary(self) -> Optional[str]:
        """Non-None when searches failed this run - printed by hunt.run()."""
        if not self._err_count:
            return None
        return f"ebay: {self._err_count} search error(s) this run (last: {self._err_last})"


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

    # One call returns the WHOLE catalogue, so there is nothing to search for -
    # ask for it once and let the price book decide, which is what the class
    # docstring always claimed happened.
    _ALL = "__gsa_catalogue__"

    def relevant_terms(self, terms: list) -> list:
        return [self._ALL]

    def search(self, query: str, limit: int = 40) -> list[dict]:
        """Every open lot. The `query` and `limit` are deliberately ignored.

        🚨 This used to be `[r for r in self._fetch() if q in r["title"].lower()]`
        - a substring match of OUR consumer search terms against GOVERNMENT
        surplus titles. Terms like "canon powershot" and "nintendo switch oled"
        essentially never appear in "Excess Medical Supplies" or "Lot of 4 Dell
        CPUs", which is why production reported `gsa=6` every run against a
        catalogue of ~665 active lots. Diagnosed 2026-08-16.

        ⛔ Fixing it does NOT make money today, and that is worth knowing before
        anyone celebrates: feeding all 665 lots through match() prices ZERO of
        them. GSA sells fleet vehicles (median bid $3,025), aircraft, hospital
        beds and generators - a different business from this book. It is also
        pickup-only, and the entire current catalogue sits in four cities
        (Springfield IL, Montgomery AL, Phoenix AZ, Boston MA) with nothing in
        Texas. Kept because it is one free request per run and inventory
        rotates; if a drivable lot ever appears, the book will now actually see
        it instead of filtering it out on a keyword.
        """
        return self._fetch()


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

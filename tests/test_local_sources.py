"""The local / estate-sale sources added 2026-07-27.

Fixtures here are trimmed copies of REAL responses captured that day, because
every source bug this project has hit was invisible to invented fixtures.
"""

import json

import pytest

from flipscout import estates, hunt
from flipscout.hunters import HiBid, NellisAuction, build_hunters


# --- HiBid geo --------------------------------------------------------------

def _lot(lot_id=1, ships=True, house="Empire Furniture LLC", city="houston"):
    return {
        "id": lot_id, "itemId": lot_id, "lead": "Fluke 87V Multimeter",
        "lotNumber": "12", "description": "works", "shippingOffered": ships,
        "featuredPicture": {"fullSizeLocation": "http://img/1.jpg"},
        "lotState": {"bidCount": 0, "highBid": 5.0, "minBid": 6.0,
                     "isClosed": False, "buyNow": None},
        "auction": {"id": 763621, "eventName": "Estate Auction 07-27",
                    "auctioneer": {"name": house, "city": city, "state": "TX"}},
    }


class FakeGQL:
    """Records the GraphQL variables it was called with, replies per-pass."""

    def __init__(self, local_ids=(), national_ids=()):
        self.local_ids, self.national_ids = local_ids, national_ids
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        v = (json or {}).get("variables") or {}
        self.calls.append(v)
        ids = self.local_ids if v.get("zip") else self.national_ids
        payload = {"data": {"lotSearch": {"pagedResults": {
            "totalCount": len(ids),
            "results": [_lot(i) for i in ids]}}}}

        class R:
            status_code = 200

            def raise_for_status(self): pass

            def json(self_inner): return payload
        return R()


def test_hibid_runs_a_local_pass_and_a_national_pass():
    gql = FakeGQL(local_ids=(1, 2), national_ids=(3,))
    rows = HiBid(session=gql, zip_code="77441", miles=150).search("fluke")

    assert len(gql.calls) == 2
    assert gql.calls[0]["zip"] == "77441" and gql.calls[0]["miles"] == 150
    assert gql.calls[1]["zip"] is None          # national pass is unfiltered
    assert {r["id"] for r in rows} == {"1", "2", "3"}


def test_local_lots_are_marked_nearby_and_national_ones_are_not():
    gql = FakeGQL(local_ids=(1,), national_ids=(2,))
    rows = {r["id"]: r for r in
            HiBid(session=gql, zip_code="77441", miles=150).search("fluke")}
    assert rows["1"]["nearby"] is True and rows["1"]["local"] is True
    assert rows["2"]["nearby"] is False and rows["2"]["local"] is False


def test_a_lot_in_both_passes_keeps_its_local_flag():
    """The same lot comes back nationally too. If the national row won, a
    drivable lot would be priced with $9 of inbound shipping it doesn't have."""
    gql = FakeGQL(local_ids=(7,), national_ids=(7,))
    rows = HiBid(session=gql, zip_code="77441", miles=150).search("fluke")
    assert len(rows) == 1
    assert rows[0]["nearby"] is True


def test_no_zip_means_no_local_pass():
    gql = FakeGQL(national_ids=(1,))
    HiBid(session=gql).search("fluke")
    assert len(gql.calls) == 1 and gql.calls[0]["zip"] is None


def test_pickup_warning_only_when_shipping_is_not_offered():
    assert HiBid._row(_lot(ships=True), nearby=False)["pickup_risk"] is False
    assert HiBid._row(_lot(ships=False), nearby=False)["pickup_risk"] is True


def test_hibid_surfaces_the_auction_house_and_city():
    row = HiBid._row(_lot(), nearby=True)
    assert row["house"] == "Empire Furniture LLC"
    assert row["city"] == "Houston"      # source reports it lowercased
    assert row["state"] == "TX"


# --- Nellis -----------------------------------------------------------------

NELLIS_PAYLOAD = {
    "currentShoppingLocation": {"id": 5, "name": "Houston, TX"},
    "products": [{
        "id": 119993402, "title": "Fluke 87V Industrial Multimeter",
        "photos": [{"url": "http://img/n.jpg"}],
        "retailPrice": 18.8, "bidCount": 0, "currentPrice": 3.0,
        "closeTime": {"__type": "Date", "value": "2026-07-28T04:11:00.000Z"},
        "isClosed": False, "marketStatus": "open", "location": "Katy",
    }],
}


def test_nellis_parses_a_houston_lot_as_local_pickup():
    rows = NellisAuction.parse(NELLIS_PAYLOAD, want_city="houston")
    assert len(rows) == 1
    r = rows[0]
    assert r["local"] is True and r["pickup_risk"] is True
    assert r["price"] == 3.0
    assert r["min_bid"] == 4.0            # next legal bid, not the current one
    assert r["city"] == "Katy"
    assert "Houston" in r["house"]


def test_nellis_url_carries_a_slug_because_bare_ids_404():
    # Leron, 7/31: "all these nellisauction links are broken." Nellis routes
    # are /p/<slug>/<id>; /p/<id> alone is a hard 404 (verified live). Any
    # slug resolves, so the title slug keeps the link human-readable too.
    r = NellisAuction.parse(NELLIS_PAYLOAD, want_city="houston")[0]
    assert r["url"] == ("https://www.nellisauction.com/p/"
                        "fluke-87v-industrial-multimeter/119993402")


def test_nellis_refuses_to_call_another_state_local():
    """If the location cookie doesn't take, Nellis serves Las Vegas. Calling
    that "local pickup, $0 inbound" would quietly mis-price every row."""
    payload = dict(NELLIS_PAYLOAD,
                   currentShoppingLocation={"id": 1, "name": "Las Vegas, NV"})
    rows = NellisAuction.parse(payload, want_city="houston")
    assert rows[0]["local"] is False


def test_nellis_skips_closed_lots():
    payload = {"currentShoppingLocation": {"name": "Houston, TX"},
               "products": [dict(NELLIS_PAYLOAD["products"][0], isClosed=True)]}
    assert NellisAuction.parse(payload, want_city="houston") == []


# --- estate sales -----------------------------------------------------------

ESN_HTML = """
<script type="application/ld+json">{"@type":"SaleEvent",
 "url":"https://www.estatesales.net/TX/Stafford/77477/5013303",
 "name":"The Treasure Zone: Multi-Seller Event","image":["http://img/e.jpg"],
 "startDate":"2026-07-25T05:00:00.000Z","endDate":"2026-07-28T00:00:00.000Z",
 "description":"Online Only Auction",
 "organizer":{"name":"Caring Transitions Of Houston Metro","telephone":"(713) 966-6767"}}</script>
<script type="application/ld+json">{"@type":"SaleEvent",
 "url":"https://www.estatesales.net/TX/Katy/77450/5012874","name":"Katy Estate Sale",
 "image":["http://img/k.jpg"],"endDate":"2026-07-29T01:00:00.000Z",
 "description":"Estate Sale","organizer":{"name":"Wall 2 Wall"}}</script>
<script type="application/ld+json">{"@type":"WebSite","name":"ignore me"}</script>
"""


def test_estate_feed_parses_sales_and_flags_the_online_ones():
    sales = estates.EstateSalesNet.parse(ESN_HTML)
    assert len(sales) == 2                      # the WebSite block is skipped
    assert sales[0]["online"] is True           # online sorts first
    assert sales[0]["city"] == "Stafford"
    assert sales[0]["company"] == "Caring Transitions Of Houston Metro"
    assert sales[1]["online"] is False


def test_estate_digest_is_empty_when_there_is_nothing():
    assert estates.digest([]) == ""


def test_estate_digest_names_the_sales_and_admits_it_has_no_prices():
    body = estates.digest(estates.EstateSalesNet.parse(ESN_HTML), area_label="Fulshear")
    assert "Fulshear" in body and "Treasure Zone" in body
    assert "1 biddable online" in body
    assert "don't publish item prices" in body


# --- wiring -----------------------------------------------------------------

def test_build_hunters_passes_the_zip_through_to_hibid():
    env = {"FLIPSCOUT_ZIP": "77441", "FLIPSCOUT_RADIUS_MILES": "150"}
    hb = [h for h in build_hunters(["hibid"], env=env)][0]
    assert hb.zip_code == "77441" and hb.miles == 150


def test_build_hunters_without_a_zip_leaves_hibid_national():
    hb = build_hunters(["hibid"], env={})[0]
    assert hb.zip_code is None and hb.miles is None


def test_nellis_location_comes_from_env():
    n = build_hunters(["nellis"], env={"FLIPSCOUT_NELLIS_LOCATION": "dallas"})[0]
    assert n.location == "dallas"


def test_digest_posts_once_a_day(tmp_path):
    hb = tmp_path / "hb.json"
    posted = []

    class Feed:
        def sales(self, limit=12):
            return estates.EstateSalesNet.parse(ESN_HTML)

    cfg = {"estate_area": "TX/Fulshear/77441", "heartbeat_file": str(hb)}
    assert hunt.post_estate_digest(cfg, lambda a, content="": posted.append(content),
                                   feed=Feed()) is True
    assert hunt.post_estate_digest(cfg, lambda a, content="": posted.append(content),
                                   feed=Feed()) is False
    assert len(posted) == 1


def test_digest_marker_does_not_clobber_the_checkin_marker(tmp_path):
    """Both markers share one file. An earlier version wrote {"last": ...} flat,
    so posting the digest would have reset the daily check-in and vice versa."""
    hb = str(tmp_path / "hb.json")
    hunt._mark_heartbeat(hb, today="2026-07-27", key="last")
    hunt._mark_heartbeat(hb, today="2026-07-27", key="estates")
    with open(hb, encoding="utf-8") as f:
        state = json.load(f)
    assert state == {"last": "2026-07-27", "estates": "2026-07-27"}
    assert hunt._due_for_heartbeat(hb, today="2026-07-27", key="last") is False
    assert hunt._due_for_heartbeat(hb, today="2026-07-27", key="estates") is False


# --- the false positive the first live local sweep produced -----------------

def test_merchandise_borrowing_a_product_name_is_not_priced():
    """Caught live 2026-07-27 on the first sweep with the local sources on:
    a plastic "Pokemon Crystal Ball" matched the Pokemon Crystal CARTRIDGE and
    was quoted a $100.63 max bid. The local liquidators sell consumer goods by
    the pallet, so the book meets a lot more of this than it used to."""
    from flipscout.pricebook import match
    assert match("1pc Pokemon Crystal Ball Pikachu Gengar Eevee Anime Figure") is None
    assert match("Pokemon Crystal Plush Toy Backpack") is None
    # ...while the real cartridge still prices.
    assert match("Pokemon Crystal Version Game Boy Color Authentic") is not None


def test_merchandise_guard_does_not_eat_pinball():
    """`\bball\b` must not swallow words that merely end in 'ball'."""
    import re
    from flipscout.pricebook import ACCESSORY_EXCLUDE
    assert not re.search(ACCESSORY_EXCLUDE, "pokemon pinball game boy")


def test_accessories_for_a_device_are_not_priced_as_the_device():
    """Both of these quoted a $23.50 TI-84 CE max bid on the first live local
    sweep. Retail-returns sources sell the accessory AS the product."""
    from flipscout.pricebook import match
    assert match("Hard Case Compatible with Texas Instruments TI-84 Plus CE") is None
    assert match("SCOVEE Charger Cable Compatible with TI-84 Plus CE Mini USB") is None
    assert match("Screen Protector for TI-84 Plus CE Graphing Calculator") is None
    # The calculator itself still prices.
    assert match("Texas Instruments TI-84 Plus CE Graphing Calculator Teal") is not None


# --- ShopGoodwill Buy-It-Now (2026-07-28): buy outright, no bidding war ------

def test_goodwill_merges_buynow_and_auction_passes(monkeypatch):
    from flipscout.hunters import ShopGoodwill
    g = ShopGoodwill()
    calls = []

    def fake_query(query, limit, buy_now_only):
        calls.append(buy_now_only)
        if buy_now_only:
            return [{"itemId": 1, "title": "Gunne Sax Dress", "buyNowPrice": 12.99,
                     "currentPrice": 12.99, "imageURL": "i", "endTime": "", "startTime": ""},
                    {"itemId": 3, "title": "No-price glitch row", "buyNowPrice": 0,
                     "currentPrice": 0, "imageURL": "", "endTime": "", "startTime": ""}]
        return [{"itemId": 1, "title": "Gunne Sax Dress", "currentPrice": 5.00,
                 "minimumBid": 6.00, "numBids": 2, "imageURL": "i", "endTime": "", "startTime": ""},
                {"itemId": 2, "title": "TI-84 Plus CE", "currentPrice": 9.99,
                 "minimumBid": 10.99, "numBids": 0, "imageURL": "i", "endTime": "", "startTime": ""}]

    monkeypatch.setattr(g, "_query", fake_query)
    rows = {r["id"]: r for r in g.search("x")}
    assert calls == [True, False]
    # item 1 exists in both passes: the BUY-NOW row wins (you can just buy it)
    assert rows["1"]["listing_type"] == "fixed"
    assert rows["1"]["price"] == 12.99 and rows["1"]["min_bid"] == 12.99
    # auction-only item keeps auction semantics
    assert "listing_type" not in rows["2"] and rows["2"]["bids"] == 0 or rows["2"].get("listing_type") != "fixed"
    # zero-priced buy-now rows are dropped, not priced at $0
    assert "3" not in rows


def test_goodwill_buynow_rows_price_as_fixed_through_the_book():
    # was Gunne Sax - womens-apparel is benched (BENCHED_CATEGORIES,
    # active=False 2026-08-15) and no longer matches; swapped for an active
    # ipods model to keep exercising the fixed-listing pricing path.
    from flipscout import hunt
    row = {"source": "goodwill", "id": "9", "title": "Apple iPod Classic 160GB MP3 Player",
           "url": "u", "price": 12.99, "min_bid": 12.99, "increment": 1.0, "bids": 0,
           "handling": 0.0, "image": "i", "ends": "", "listing_type": "fixed"}
    cfg = {"sources": ["goodwill"], "target_profit": 20.0, "inbound_shipping": 9.0,
           "top": 10, "state_file": "nonexistent.json"}
    got = hunt.evaluate([row], cfg, hunters=[])
    assert got and got[0]["model"].key == "ipod_classic_160"
    a = hunt.to_alert(got[0])
    assert a["listing_type"] == "fixed"
    assert "Asking" in a["reason"]


# --- GSA Auctions + Unclaimed Baggage (2026-07-28) ---------------------------

GSA_PAYLOAD = {"Results": [
    {"itemName": "Nikon D50 Digital Camera", "highBidAmount": "120.0",
     "aucIncrement": "10", "biddersCount": "2", "auctionStatus": "active",
     "itemDescURL": "https://gsaauctions.gov/auctions/preview/12345",
     "imageURL": "https://img/1.jpg", "aucEndDt": "2026-08-01 10:00",
     "saleNo": "91QSCI25400701", "lotNo": "3", "agencyName": "GSA",
     "locationCity": "BOSTON", "locationST": "MA"},
    {"itemName": "Forklift", "highBidAmount": None, "auctionStatus": "closed",
     "saleNo": "X", "lotNo": "1"},
]}


def test_gsa_rows_carry_pickup_risk(monkeypatch):
    from flipscout.hunters import GSAAuctions
    g = GSAAuctions()
    monkeypatch.setattr(g, "_fetch", lambda: GSAAuctions.parse(GSA_PAYLOAD))
    rows = g.search("nikon")
    r = next(x for x in rows if "Nikon" in x["title"])
    assert r["source"] == "gsa" and r["price"] == 120.0 and r["bids"] == 2
    # GSA never ships anything itself - every row must say so
    assert r["pickup_risk"] is True
    assert r["url"].startswith("https://gsaauctions.gov")
    # the closed Forklift row must still be dropped by parse()
    assert not any("Forklift" in x["title"] for x in rows)


def test_gsa_returns_the_whole_catalogue_and_ignores_the_query(monkeypatch):
    """🚨 This ASSERTED THE BUG until 2026-08-16.

    It used to require `search("nikon") == 1 row` and `search("zamboni") == []`
    - i.e. it enshrined a substring match of OUR consumer search terms against
    GOVERNMENT surplus titles. Terms like "canon powershot" never appear in
    "Excess Medical Supplies", which is why production reported `gsa=6` every
    run against ~665 active lots. One free request returns everything, so the
    price book should be the filter, not the keyword.
    """
    from flipscout.hunters import GSAAuctions
    from flipscout.pricebook import search_terms
    g = GSAAuctions()
    monkeypatch.setattr(g, "_fetch", lambda: GSAAuctions.parse(GSA_PAYLOAD))
    everything = g.search("nikon")
    assert g.search("zamboni") == everything, "the query must not filter anything"
    # and it must be asked for ONCE, not once per book term
    assert g.relevant_terms(search_terms()) == [GSAAuctions._ALL]


def test_gsa_one_fetch_serves_every_search_term():
    """DEMO_KEY allows ~30 req/hr per (shared) IP: the whole point of the
    local-filter design is ONE api call per run, not one per term."""
    from flipscout.hunters import GSAAuctions
    g = GSAAuctions()
    calls = []

    class Sess:
        def get(self, url, params=None, timeout=None):
            calls.append(url)
            class R:
                def raise_for_status(self): pass
                def json(self): return GSA_PAYLOAD
            return R()

    g.session = Sess()
    g.search("nikon"); g.search("camera"); g.search("fluke")
    assert len(calls) == 1


def test_unclaimedbaggage_is_registered_with_book_terms():
    """_LUGGAGE_TERMS in hunters.py still lists arcteryx/patagonia/johnny was/
    st john knit/gunne sax/reformation dress ("designer apparel" per its own
    docstring), but womens-apparel/outerwear are benched (BENCHED_CATEGORIES,
    2026-08-15) and those 11 terms were DELETED from search_terms() - so
    relevant_terms(), which only keeps terms present in BOTH lists, can no
    longer find them there. Only the electronics half of _LUGGAGE_TERMS still
    intersects the book."""
    from flipscout.hunters import build_hunters
    from flipscout.pricebook import search_terms
    h = build_hunters(["unclaimedbaggage"])[0]
    assert h.name == "unclaimedbaggage"
    terms = h.relevant_terms(search_terms())
    # electronics still come through
    assert any("coolpix" in t for t in terms)
    assert any("ipod" in t for t in terms)
    # apparel is gone from the book entirely - benched, not just excluded
    assert not any("patagonia" in t or "arcteryx" in t or "johnny was" in t
                   or "gunne sax" in t or "reformation" in t for t in terms)


# --- estate catalogs swept in full (2026-07-29) ------------------------------
# "its you job to go to the links and find me deals i dont want to do the
# manual work" - online estate sales that resolve to HiBid catalogs now feed
# every lot through the book instead of arriving as bare links.

def test_estate_pages_resolve_to_hibid_catalog_ids(monkeypatch):
    from flipscout.estates import EstateSalesNet
    es = EstateSalesNet(area="TX/Fulshear/77441")

    class Sess:
        def get(self, url, headers=None, timeout=None):
            class R:
                status_code = 200
                text = ('<a href="https://hibid.com/catalog/762712">bid</a>'
                        '<a href="https://www.hibid.com/catalog/762712">dup</a>'
                        if "online" in url else "<p>house sale, no catalog</p>")
                def raise_for_status(self): pass
            return R()

    es.session = Sess()
    sales = [{"url": "https://x/online-sale", "online": True},
             {"url": "https://x/house-sale", "online": False}]
    assert es.hibid_catalog_ids(sales) == [762712]


def test_estate_catalog_rows_feed_the_pipeline(monkeypatch):
    from flipscout import hunt

    class FakeHiBid:
        name = "hibid"
        def catalog_lots(self, aid, max_lots=1000):
            assert aid == 762712
            return [{"source": "hibid", "id": "L1",
                     "title": "Canon AE-1 Program 35mm Film Camera w/ 50mm Lens",
                     "url": "u", "price": 5.0, "min_bid": 6.0, "increment": 1.0,
                     "bids": 0, "handling": None, "image": "i", "ends": "",
                     "nearby": True, "local": True}]

    class FakeFeed:
        def sales(self): return [{"url": "s", "online": True}]
        def hibid_catalog_ids(self, sales): return [762712]

    cfg = {"estate_area": "TX/Fulshear/77441"}
    rows = hunt.estate_catalog_rows(cfg, hunters=[FakeHiBid()], feed=FakeFeed())
    assert len(rows) == 1 and rows[0]["nearby"] is True


def test_estate_catalog_sweep_is_off_without_area_or_hibid():
    from flipscout import hunt
    assert hunt.estate_catalog_rows({"estate_area": ""}, hunters=[]) == []
    assert hunt.estate_catalog_rows({"estate_area": "TX/X/1"}, hunters=[]) == []


def test_hibid_catalog_lots_paginate_and_stop(monkeypatch):
    from flipscout.hunters import HiBid
    h = HiBid()
    pages = []

    class Sess:
        def post(self, url, json=None, headers=None, timeout=None):
            pages.append(json["variables"]["pageNumber"])
            class R:
                def raise_for_status(self): pass
                def json(self):
                    return {"data": {"lotSearch": {"pagedResults": {
                        "totalCount": 2,
                        "results": [{"id": 10 + len(pages), "lead": "Lot",
                                     "lotState": {"bidCount": 0, "highBid": 0,
                                                  "minBid": 1, "isClosed": False},
                                     "featuredPicture": {}, "auction": {}}]}}}}
            return R()

    h.session = Sess()
    rows = h.catalog_lots(762712)
    assert len(rows) == 2 and pages == [1, 2]


# --- eBay Browse hunter (2026-07-29): wired now, sleeps until approval -------

EBAY_BODY = {"itemSummaries": [
    {"itemId": "v1|123|0", "title": "Canon PowerShot G7X Mark II Digital Camera",
     "price": {"value": "650.00"}, "itemWebUrl": "https://ebay.com/itm/123",
     "image": {"imageUrl": "https://i.ebayimg.com/1.jpg"},
     "shippingOptions": [{"shippingCost": {"value": "8.50"}}]},
    {"itemId": "v1|124|0", "title": "Broken price row", "price": {}},
]}


def test_ebay_parse_builds_fixed_price_rows():
    from flipscout.hunters import EbayBrowse
    rows = EbayBrowse.parse(EBAY_BODY)
    assert len(rows) == 1
    r = rows[0]
    assert r["source"] == "ebay" and r["listing_type"] == "fixed"
    assert r["price"] == 650.0 and r["min_bid"] == 650.0
    assert r["handling"] == 8.5 and r["image"].endswith("1.jpg")


def test_ebay_parse_carries_condition_and_best_offer():
    from flipscout.hunters import EbayBrowse
    body = {"itemSummaries": [
        {"itemId": "v1|125|0", "title": "Polaroid SX-70 Land Camera untested",
         "price": {"value": "45.00"}, "itemWebUrl": "https://ebay.com/itm/125",
         "condition": "For parts or not working",
         "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"]},
    ]}
    r = EbayBrowse.parse(body)[0]
    assert r["condition"] == "For parts or not working"
    assert r["best_offer"] is True
    # Rows without the fields stay safe defaults (no condition, no best offer).
    r2 = EbayBrowse.parse(EBAY_BODY)[0]
    assert r2["condition"] == "" and r2["best_offer"] is False


def test_ebay_search_includes_parts_grade_and_newly_listed():
    # The named USED bucket excludes conditionId 7000 (for-parts) - the exact
    # discounted cohort the camera book was measured on. Pin the filter.
    import inspect
    from flipscout import hunters
    src = inspect.getsource(hunters.EbayBrowse.search)
    assert "7000" in src and "conditionIds" in src
    assert "newlyListed" in src


def test_ebay_parse_builds_auction_rows():
    from flipscout.hunters import EbayBrowse
    body = {"itemSummaries": [
        {"itemId": "v1|126|0", "title": "Canon AE-1 35mm Film Camera w/ 50mm",
         "currentBidPrice": {"value": "26.00"}, "bidCount": 3,
         "itemWebUrl": "https://ebay.com/itm/126",
         "itemEndDate": "2026-07-29T21:15:00.000Z",
         "buyingOptions": ["AUCTION"]},
    ]}
    r = EbayBrowse.parse(body)[0]
    assert r["listing_type"] == "auction"
    assert r["price"] == 26.0
    assert r["min_bid"] == 27.0          # $25-99.99 band -> $1.00 increment
    assert r["increment"] == 1.0
    assert r["bids"] == 3
    assert r["ends"].startswith("2026-07-29T21:15")


def test_ebay_auction_with_no_bids_opens_at_current_price():
    from flipscout.hunters import EbayBrowse
    body = {"itemSummaries": [
        {"itemId": "v1|127|0", "title": "Pentax K1000 body",
         "currentBidPrice": {"value": "9.99"}, "bidCount": 0,
         "itemWebUrl": "https://ebay.com/itm/127",
         "buyingOptions": ["AUCTION"]},
    ]}
    r = EbayBrowse.parse(body)[0]
    assert r["min_bid"] == 9.99          # nobody bid: the open IS the number


def test_ebay_bid_increment_bands():
    from flipscout.hunters import EbayBrowse
    assert EbayBrowse.bid_increment(0.50) == 0.05
    assert EbayBrowse.bid_increment(12.00) == 0.50
    assert EbayBrowse.bid_increment(26.00) == 1.00
    assert EbayBrowse.bid_increment(150.00) == 2.50
    assert EbayBrowse.bid_increment(700.00) == 10.00


def test_ebay_auction_pass_env_override(monkeypatch):
    from flipscout.hunters import EbayBrowse
    monkeypatch.setenv("FLIPSCOUT_EBAY_AUCTIONS", "always")
    assert EbayBrowse()._auction_pass is True
    monkeypatch.setenv("FLIPSCOUT_EBAY_AUCTIONS", "off")
    assert EbayBrowse()._auction_pass is False


def test_ebay_hunter_sleeps_without_keys(monkeypatch):
    from flipscout.hunters import EbayBrowse
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    h = EbayBrowse()
    assert h.search("canon g7x") == []      # silent, not raising - approval-gated


def test_ebay_hunter_is_registered():
    from flipscout.hunters import HUNTERS
    assert "ebay" in HUNTERS


# --- ShopGoodwill Buy-It-Now PAGING (2026-08-16) -----------------------------
# Leron: "are we also finding deals where i dont have to bid and do buy it now?"
# We were - but only the first 40 per term. Goodwill caps pageSize at 40
# server-side regardless of what you ask for, so any term with more than 40
# Buy-It-Now hits was silently truncated. Measured over 18 terms that day:
# 296 BIN listings existed, 177 were fetched, 119 (40%) were never seen.

def _gw_page(n_total, page_size=40):
    """Fake Goodwill paging: n_total items served page_size at a time."""
    def fake(query, page, size, buy_now_only):
        start = (page - 1) * page_size
        items = [{"itemId": i, "title": f"item {i}", "buyNowPrice": 10.0,
                  "currentPrice": 10.0, "imageURL": "", "endTime": "",
                  "startTime": ""}
                 for i in range(start, min(start + page_size, n_total))]
        return items, n_total
    return fake


def test_buynow_pass_pages_until_the_server_count_is_reached(monkeypatch):
    from flipscout.hunters import ShopGoodwill
    g = ShopGoodwill()
    monkeypatch.setattr(g, "_one_page", _gw_page(75))
    assert len(g._query("nintendo switch", 40, True)) == 75


def test_auction_pass_is_deliberately_not_paged(monkeypatch):
    """1,375 auction listings were missing over the same 18 terms, which
    extrapolates to ~10k over the full book. That is a runtime/volume decision,
    not a bug fix, and auctions already supply ~96% of the board."""
    from flipscout.hunters import ShopGoodwill
    g = ShopGoodwill()
    monkeypatch.setattr(g, "_one_page", _gw_page(500))
    assert len(g._query("ti-84", 40, False)) == 40


def test_buynow_paging_is_capped(monkeypatch):
    """One pathological term must not walk the whole catalogue."""
    from flipscout.hunters import ShopGoodwill
    g = ShopGoodwill()
    monkeypatch.setattr(g, "_one_page", _gw_page(10_000))
    got = g._query("everything", 40, True)
    assert len(got) == 40 * ShopGoodwill._BIN_MAX_PAGES


def test_buynow_paging_stops_if_the_server_repeats_a_page(monkeypatch):
    """Defends against an itemCount that never gets satisfied - otherwise the
    loop spins to the page cap collecting duplicates."""
    from flipscout.hunters import ShopGoodwill
    g = ShopGoodwill()

    def stuck(query, page, size, buy_now_only):
        return ([{"itemId": 1, "title": "same", "buyNowPrice": 10.0,
                  "currentPrice": 10.0, "imageURL": "", "endTime": "",
                  "startTime": ""}], 999)

    monkeypatch.setattr(g, "_one_page", stuck)
    assert len(g._query("x", 40, True)) == 1


def test_a_dead_page_two_keeps_page_one(monkeypatch):
    """Fail-soft has to survive PARTWAY through paging, not just at the start."""
    from flipscout.hunters import ShopGoodwill
    g = ShopGoodwill()

    def flaky(query, page, size, buy_now_only):
        if page == 1:
            return ([{"itemId": i, "title": f"i{i}", "buyNowPrice": 10.0,
                      "currentPrice": 10.0, "imageURL": "", "endTime": "",
                      "startTime": ""} for i in range(40)], 200)
        raise RuntimeError("goodwill 500")

    monkeypatch.setattr(g, "_one_page", flaky)
    # 40, not 0: a 500 on page 2 must not discard page 1. Wrapping the whole
    # paging loop in one try did exactly that - strictly worse than not
    # paging, since a partial outage would read as "this term has nothing".
    assert len(g._query("x", 40, True)) == 40


# --- allowlisted hunters must not silently drift from the book ---------------
# 2026-08-16: production reported EXACTLY `propertyroom=10` on every run for
# weeks. PropertyRoom's allowlist was ("ipod", "ipod classic", "ipod video"),
# so only two book terms ever matched it - the source stayed pinned to iPods
# while the book grew watches, cameras and the whole console pack around it.
# Its own docstring says it carries "jewellery, watches, coins, electronics".
# Measured after widening: 130 unique listings and 37 priced, vs 10 and 0.

def _allowlisted_hunters():
    from flipscout.hunters import PropertyRoom, _unclaimedbaggage
    return [("propertyroom", PropertyRoom()), ("unclaimedbaggage", _unclaimedbaggage())]


def test_allowlist_terms_all_exist_in_the_price_book():
    """A term in a hunter's TERMS that is not in search_terms() can NEVER fire.

    That is the silent-death shape: the hunter looks configured, the sweep
    looks healthy, and the source quietly contributes nothing.
    """
    from flipscout.pricebook import search_terms
    book = {t.lower() for t in search_terms()}
    for name, h in _allowlisted_hunters():
        dead = [t for t in h.TERMS if t.lower() not in book]
        assert not dead, f"{name}: allowlist terms missing from search_terms(): {dead}"


def test_propertyroom_covers_the_categories_it_actually_carries():
    """Watches especially - "citizen watch" alone returned 16 listings, all 16
    priceable, while the allowlist was blocking it."""
    from flipscout.hunters import PropertyRoom
    from flipscout.pricebook import search_terms
    live = {t.lower() for t in PropertyRoom().relevant_terms(search_terms())}
    for needed in ("citizen watch", "casio g-shock", "xbox one", "ipod classic",
                   "nikon coolpix", "nintendo switch"):
        assert needed in live, f"propertyroom no longer sweeps {needed!r}"
    assert len(live) >= 15, f"allowlist shrank back to {len(live)} terms"


# --- FB Marketplace sweep (2026-08-17) ---------------------------------------
# FB is login-walled, so it is the one source that cannot run in the hourly
# GitHub Action. It used to run as a Claude-driven CronCreate, which is
# session-only: it died when the session exited and nobody noticed for TWO
# DAYS, because a dead sweep and a quiet sweep both post nothing to Discord.
# flipscout/fbsweep.py replaces it with a Windows scheduled task driving a
# headless Playwright profile. These tests pin the money logic and, more
# importantly, the loud-failure contract.

def test_fb_local_pickup_means_zero_inbound_shipping():
    """The whole reason FB beats the shipped sources on thin margins."""
    from flipscout import fbsweep
    assert fbsweep.INBOUND == 0.0
    hit = fbsweep.evaluate("Singer Featherweight 221 Sewing Machine", 120.0)
    assert hit and hit["max_bid"] > 120.0


@pytest.mark.parametrize("title,price,why", [
    ("Canon G7X Mark II - Ships to you", 300.0, "not local, so inbound is not 0"),
    ("Canon AE-1 Program", 1.0, "$1 asks are message-me placeholder bait"),
    ("Arc'teryx Beta AR Jacket", 70.0, "local search is flooded with UA fakes"),
    ("Nintendo Switch OLED Dock Station HEG-007", 40.0, "dock, not console"),
    ("Singer Featherweight 221 Light Switch", 18.0, "component part"),
    ("PS3 Console CECH-3001A Parts or Repair", 20.0, "seller-declared dead"),
    ("Random bicycle", 50.0, "not in the book at all"),
])
def test_fb_sweep_skips(title, price, why):
    from flipscout import fbsweep
    assert fbsweep.evaluate(title, price) is None, why


def test_fb_sweep_inherits_every_price_book_guard():
    """fbsweep calls match(), so the dock / component / dead-hardware guards
    added on 2026-08-16 apply here for free - it must never grow its own copy
    of them, which would drift."""
    import inspect
    from flipscout import fbsweep
    src = inspect.getsource(fbsweep.evaluate)
    assert "match(" in src


def test_a_logged_out_sweep_is_loud_not_silent(monkeypatch):
    """🚨 THE BUG THIS FILE EXISTS FOR. A broken sweep and a quiet sweep must
    not look the same - that ambiguity hid a two-day outage."""
    from flipscout import fbsweep
    monkeypatch.setattr(fbsweep, "sweep",
                        lambda **kw: {"finds": [], "logged_out": True,
                                      "scanned": 0, "terms": 0})
    sent = []
    monkeypatch.setattr("flipscout.notify.notify",
                        lambda msg, **kw: sent.append(msg))
    rc = fbsweep.run(dry_run=False)
    assert rc == 2, "logged out must be a non-zero exit, not a clean run"
    assert sent and "LOGGED OUT" in sent[0]


def test_a_genuinely_quiet_sweep_posts_nothing(monkeypatch):
    from flipscout import fbsweep
    monkeypatch.setattr(fbsweep, "sweep",
                        lambda **kw: {"finds": [], "logged_out": False,
                                      "scanned": 120, "terms": 30})
    posted = []
    monkeypatch.setattr("flipscout.notify.notify_rich",
                        lambda c, **kw: posted.append(c))
    assert fbsweep.run(dry_run=False) == 0
    assert not posted


def test_already_seen_finds_are_not_reposted(monkeypatch, tmp_path):
    from flipscout import fbsweep
    seen_file = tmp_path / "seen.json"
    seen_file.write_text('{"seen": ["111"]}', encoding="utf-8")
    monkeypatch.setattr(fbsweep, "SEEN_PATH", seen_file)
    hit = dict(id="111", price=10.0, profit_at_open=50.0, title="x",
               model_key="ipod_nano")
    fresh = dict(id="222", price=10.0, profit_at_open=40.0, title="y",
                 model_key="ipod_nano")
    monkeypatch.setattr(fbsweep, "sweep",
                        lambda **kw: {"finds": [hit, fresh], "logged_out": False,
                                      "scanned": 2, "terms": 1})
    posted = []
    monkeypatch.setattr("flipscout.notify.notify_rich",
                        lambda c, **kw: posted.append(c))
    fbsweep.run(dry_run=False)
    assert [f["id"] for f in posted[0]] == ["222"]
    assert "111" in fbsweep.load_seen() and "222" in fbsweep.load_seen()


def test_fbsweep_loads_dotenv_before_doing_anything(monkeypatch):
    """🚨 A Scheduled Task gets NO shell profile, so FLIPSCOUT_ALERT_WEBHOOK
    only exists in .env. Without load_env_file() the webhook is unset and
    notify() falls back to `print(text)` - so the LOGGED OUT warning lands in a
    log file nobody reads instead of Discord.

    That is exactly what happened on the first real task run 2026-08-17: the
    log had the warning twice (once from run(), once from notify()'s fallback),
    which is the tell that nothing was delivered. mybids.py already documented
    this trap; fbsweep had to learn it too.
    """
    from flipscout import fbsweep
    called = []
    monkeypatch.setattr("flipscout.mybids.load_env_file",
                        lambda *a, **k: called.append(True))
    monkeypatch.setattr(fbsweep, "run", lambda **kw: 0)
    fbsweep.main(["sweep"])
    assert called, "main() must load .env before running"


def test_fbsweep_config_is_read_lazily_not_at_import(monkeypatch):
    """Module-level os.environ.get() constants evaluate at IMPORT, which is
    before load_env_file() runs - they would freeze the defaults and silently
    ignore .env."""
    from flipscout import fbsweep
    monkeypatch.setenv("FLIPSCOUT_FB_CITY", "austin")
    monkeypatch.setenv("FLIPSCOUT_TARGET_PROFIT", "35")
    assert fbsweep._city() == "austin"
    assert fbsweep._target_profit() == 35.0


# --- FB card parsing, from REAL cards captured 2026-08-17 --------------------
# The first live run leaked a $80 RENTAL as a "$910 profit" find and would have
# taken a "Ships to you" listing as local. Both came from parsing the card
# wrong, and neither was visible until the profile was actually logged in.

FB_CARDS = [
    (["$350", "Canon EOS M50 Great condition", "Houston, TX"],
     350.0, "Canon EOS M50 Great condition", "Houston, TX"),
    # a DISCOUNTED card carries two prices: current first, strikethrough second
    (["$140", "$220", "Nintendo switch", "Channelview, TX"],
     140.0, "Nintendo switch", "Channelview, TX"),
    (["$150", "$175", "Nintendo Switch OLED", "Ships to you"],
     150.0, "Nintendo Switch OLED", "Ships to you"),
    (["$80", "Rent Only Canon G7X Mark II", "Houston, TX"],
     80.0, "Rent Only Canon G7X Mark II", "Houston, TX"),
]


@pytest.mark.parametrize("lines,price,title,loc", FB_CARDS)
def test_fb_card_parsing(lines, price, title, loc):
    """🚨 LOCATION IS THE LAST LINE. The first version did lines[1:3] and
    produced titles like "Rent Only Canon G7X Mark II Houston, TX"."""
    from flipscout.fbsweep import parse_card
    assert parse_card(lines) == (price, title, loc)


def test_discounted_card_takes_the_current_price_not_the_original():
    from flipscout.fbsweep import parse_card
    price, title, _ = parse_card(["$140", "$220", "Nintendo switch", "Katy, TX"])
    assert price == 140.0, "must take the ask, not the strikethrough original"
    assert "$220" not in title


@pytest.mark.parametrize("title,loc", [
    ("Canon G7X mark II FOR RENT", "Sugar Land, TX"),
    ("Canon G7x Mark III (Rental)", "Houston, TX"),
    ("Rent Only Canon G7X Mark II", "Houston, TX"),
])
def test_rentals_are_rejected(title, loc):
    """Houston's G7X market is mostly RENTALS - 4 of the first 8 results. The
    original `\brental\b` missed "Rent Only" and let an $80 rental through as a
    $910 profit."""
    from flipscout.fbsweep import evaluate
    assert evaluate(title, 80.0, loc) is None


def test_ships_to_you_is_caught_in_the_LOCATION_field():
    """🚨 It is not in the title. Checking only the title missed it, and a
    shipped item breaks the inbound=0 assumption the FB edge rests on."""
    from flipscout.fbsweep import evaluate
    # $100 is UNDER the switch_oled ceiling ($121.41), so location is the only
    # thing that can reject it - which is what makes this test meaningful.
    assert evaluate("Nintendo Switch OLED Console", 100.0, "Ships to you") is None
    assert evaluate("Nintendo Switch OLED Console", 100.0, "Houston, TX") is not None


def test_fb_partner_listings_are_not_local():
    """"Partner listing" is FB's label for dealer/retail inventory, not a
    neighbour selling a thing - retail priced and generally shipped, so the
    inbound=0 maths does not apply. Seen live 2026-08-17 on an Olympus Stylus."""
    from flipscout.fbsweep import evaluate
    assert evaluate("Partner listing Olympus Stylus Epic Zoom 80", 69.0,
                    "Houston, TX") is None


def test_fb_finds_carry_the_shared_scam_flag():
    """fbsweep reuses hunt.scam_shaped rather than inventing a second
    threshold, so the flag cannot diverge from the board's ranking or the
    Discord copy - the exact divergence hunt.py's docstring warns about."""
    from flipscout.fbsweep import evaluate
    hit = evaluate("Nintendo Switch OLED Console", 100.0, "Houston, TX")
    assert hit is not None and "scam_shaped" in hit


# --- one bad lot must not take down a whole source ---------------------------

def test_one_poisoned_hibid_lot_does_not_kill_the_search():
    """🚨 _row parses vendor JSON, and a single malformed lot raised straight
    out of search() - which the source-level handler then swallowed as "HiBid
    returned nothing". A silent source outage is far worse than one lost lot.

    Measured 2026-08-19: timeLeftSeconds of infinity reached
    datetime.timedelta as OverflowError and took the whole page with it.
    """
    from flipscout.hunters import HiBid
    lots = [
        {"id": 1, "lead": "Fluke 87V",
         "lotState": {"highBid": 5, "timeLeftSeconds": 3600},
         "auction": {"id": 9, "auctioneer": {}}},
        {"id": 2, "lead": "poisoned",
         "lotState": {"highBid": 5, "timeLeftSeconds": float("inf")},
         "auction": {"id": 9, "auctioneer": {}}},
        {"id": 3, "lead": "also poisoned", "lotState": None, "auction": None},
        {"id": 4, "lead": "Fluke 90",
         "lotState": {"highBid": 5, "timeLeftSeconds": 7200},
         "auction": {"id": 9, "auctioneer": {}}},
    ]
    h = HiBid()
    h._query = lambda *a, **k: lots
    rows = h.search("fluke", limit=10)
    assert len(rows) >= 3, "the healthy lots must survive"
    assert any(r["title"] == "Fluke 87V" for r in rows)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1e12, 1e12])
def test_a_nonfinite_countdown_yields_no_end_time_instead_of_raising(bad):
    from flipscout.hunters import _hibid_ends
    assert _hibid_ends(bad) == ""

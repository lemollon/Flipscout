"""The local / estate-sale sources added 2026-07-27.

Fixtures here are trimmed copies of REAL responses captured that day, because
every source bug this project has hit was invisible to invented fixtures.
"""

import json

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
    from flipscout import hunt
    row = {"source": "goodwill", "id": "9", "title": "Gunne Sax by Jessica McClintock Prairie Dress",
           "url": "u", "price": 12.99, "min_bid": 12.99, "increment": 1.0, "bids": 0,
           "handling": 0.0, "image": "i", "ends": "", "listing_type": "fixed"}
    cfg = {"sources": ["goodwill"], "target_profit": 20.0, "inbound_shipping": 9.0,
           "top": 10, "state_file": "nonexistent.json"}
    got = hunt.evaluate([row], cfg, hunters=[])
    assert got and got[0]["model"].key == "gunne_sax"
    a = hunt.to_alert(got[0])
    assert a["listing_type"] == "fixed"
    assert "Asking" in a["reason"]

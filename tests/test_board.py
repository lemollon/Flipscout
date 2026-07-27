"""The deals board - the qualifying items the web app shows."""

import json

from flipscout import board
from flipscout.bidding import advise
from flipscout.pricebook import match


def _cand(title="Fluke 87V True RMS Multimeter", price=5.0, source="hibid",
          nearby=False, pickup=False, listing_type="auction", **row_extra):
    m = match(title)
    assert m is not None, f"fixture title no longer matches the book: {title}"
    row = {"source": source, "id": "1", "title": title, "url": "http://x/1",
           "image": "http://img/1.jpg", "price": price, "min_bid": price + 1,
           "increment": 1.0, "bids": 0, "handling": 0.0, "ends": "2026-07-28",
           "listing_type": listing_type, "nearby": nearby, "local": nearby,
           "pickup_risk": pickup, "house": "King's Auction", "city": "Katy",
           "state": "TX", **row_extra}
    adv = advise(m.model.comp, units=m.units, handling=0.0,
                 inbound_shipping=0.0 if nearby else 9.0,
                 outbound_shipping=m.model.outbound_shipping,
                 target_profit=20.0, current_price=price, min_bid=price + 1,
                 increment=1.0, bid_count=0)
    return {"row": row, "model": m.model, "match": m, "advice": adv}


def test_board_item_carries_what_you_need_to_act():
    it = board.item(_cand())
    # what it is + the evidence
    assert it["model"] and it["comp"] > 0 and it["comps_url"].startswith("http")
    # what to do
    assert it["max_bid"] > 0 and it["open_bid"] is not None
    # where to do it
    assert it["url"] == "http://x/1"
    assert it["house"] == "King's Auction" and it["where"] == "Katy, TX"


def test_board_reports_nearby_and_pickup_flags():
    near = board.item(_cand(nearby=True, pickup=True))
    assert near["nearby"] is True and near["pickup_only"] is True
    far = board.item(_cand())
    assert far["nearby"] is False and far["pickup_only"] is False


def test_build_summarises_the_set():
    b = board.build([_cand(nearby=True), _cand(source="goodwill")])
    assert b["count"] == 2
    assert b["nearby_count"] == 1
    assert b["sources"] == ["goodwill", "hibid"]
    assert b["generated"]


def test_write_then_load_roundtrips(tmp_path):
    p = tmp_path / "sub" / "deals.json"        # directory does not exist yet
    assert board.write([_cand()], str(p)) == str(p)
    loaded = board.load(str(p))
    assert loaded["count"] == 1
    assert loaded["items"][0]["max_bid"] > 0
    assert json.loads(p.read_text(encoding="utf-8"))["count"] == 1


def test_load_of_a_missing_board_is_empty_not_an_error():
    b = board.load("does/not/exist.json")
    assert b["count"] == 0 and b["items"] == []


def test_fixed_price_rows_keep_their_type():
    """The page words these differently - "Asking / Don't pay over" rather than
    "Open at / MAX bid" - so the type has to survive into the board."""
    it = board.item(_cand(source="craigslist", listing_type="fixed", nearby=True))
    assert it["listing_type"] == "fixed"


def test_a_block_does_not_blank_the_board(tmp_path, monkeypatch):
    """If every source fails, run() returns before publishing, so the last good
    board stays on disk. Blanking it would look identical to "no deals"."""
    from flipscout import hunt
    p = tmp_path / "deals.json"
    board.write([_cand()], str(p))
    cfg = dict(hunt.load_config({}), board_file=str(p),
               state_file=str(tmp_path / "seen.json"),
               heartbeat_file=str(tmp_path / "hb.json"))

    class Dead:
        name = "dead"

        def search(self, q, limit=40):
            return []

    out = hunt.run(cfg, hunters=[Dead()], notifier=lambda *a, **k: [])
    assert out["blocked"] is True
    assert board.load(str(p))["count"] == 1        # untouched

"""Bid sentry: the CSV is the bid list, proxy math is the outbid detector, and
the final 90 minutes is when it's allowed to get loud."""

import json
import os
import time

from flipscout import mybids
from flipscout.mybids import (Bid, classify, decide, find_bids_csv, load_bids,
                              book_advice, to_alert)

CSV_HEADER = ('"Item ID","Item","Seller","Current Price","My Max Bid",'
              '"# Bids","Ending Date (PT)","Status"\n')


def _write_csv(path, rows, header=CSV_HEADER):
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for r in rows:
            f.write(r + "\n")


# --- parsing ----------------------------------------------------------------

def test_load_bids_parses_money_and_skips_closed_rows(tmp_path):
    p = tmp_path / "Auctions in Progress-Shopgoodwill.com.csv"
    _write_csv(p, [
        '"271711791","Pokemon Fire Red","GW","$41.00","$50.00","7","07/31/2026 05:21:00 PM ",""',
        # A closed-auctions row (Status set) must not be watched.
        '"271289129","TI-84 Plus CE","GW","$40.00","$20.00","12","07/28/2026","Auction lost"',
        # Junk line without a numeric id.
        '"","","","","","","",""',
    ])
    bids = load_bids(str(p))
    assert len(bids) == 1
    assert bids[0] == Bid("271711791", "Pokemon Fire Red", 50.0)


def test_find_bids_csv_prefers_the_newest_export(tmp_path):
    old = tmp_path / "Auctions in Progress-Shopgoodwill.com.csv"
    new = tmp_path / "Auctions in Progress-Shopgoodwill.com (1).csv"
    _write_csv(old, [])
    _write_csv(new, [])
    past = time.time() - 3600
    os.utime(old, (past, past))
    assert find_bids_csv(str(tmp_path)) == str(new)


def test_find_bids_csv_none_when_no_export(tmp_path):
    assert find_bids_csv(str(tmp_path)) is None


# --- outbid inference (proxy-bid math, no login) ----------------------------

def test_classify_proxy_math():
    assert classify(current=41.0, my_max=50.0) == "WINNING"
    assert classify(current=50.0, my_max=50.0) == "AT_CAP"   # one $1 bid kills
    assert classify(current=51.0, my_max=50.0) == "OUTBID"


# --- alert decisions --------------------------------------------------------

BID = Bid("1", "TI-84 Plus CE Color Graphing Calculator", 20.0)


def _live(current, left_min, expired=False):
    return {"id": "1", "title": BID.title, "url": "u", "image": None,
            "current": current, "min_bid": current + 1, "increment": 1.0,
            "bids": 5, "handling": 0.0, "ends": "2026-08-01T12:00",
            "left_min": left_min, "expired": expired}


def test_first_sight_of_an_outbid_alerts_even_days_out():
    kind, st = decide(BID, _live(25.0, left_min=2000), {})
    assert kind == "status_flip"
    assert st["status"] == "OUTBID"


def test_early_phase_does_not_repeat_on_every_dollar_of_walkup():
    # Already known OUTBID; the price creeping is not news until the endgame.
    kind, _ = decide(BID, _live(26.0, left_min=2000), {"status": "OUTBID"})
    assert kind is None


def test_endgame_losing_realerts_on_every_price_move_but_not_without_one():
    st = {"status": "OUTBID", "last_price": 25.0}
    kind, st = decide(BID, _live(25.0, left_min=80), st)
    assert kind == "endgame_losing"
    # Same price again -> quiet; the siren must not become wallpaper.
    kind2, st2 = decide(BID, _live(25.0, left_min=70), st)
    assert kind2 is None
    # A fresh counter-bid inside the window -> sound it again.
    kind3, _ = decide(BID, _live(27.0, left_min=60), st2)
    assert kind3 == "endgame_losing"


def test_endgame_winning_heads_up_fires_exactly_once():
    kind, st = decide(BID, _live(15.0, left_min=85), {"status": "WINNING"})
    assert kind == "endgame_winning"
    kind2, _ = decide(BID, _live(15.0, left_min=40), st)
    assert kind2 is None


def test_winning_above_the_book_ceiling_warns_exactly_once():
    # Losing an auction is free; WINNING one above the ceiling costs money.
    # Live catch 2026-07-30: a $91 lead on a camera with a ~$28 book ceiling.
    rich = Bid("1", BID.title, 30.0)     # max above the ceiling, and leading
    kind, st = decide(rich, _live(25.0, left_min=2000), {"status": "WINNING"},
                      book_max=23.5)
    assert kind == "over_ceiling"
    kind2, _ = decide(rich, _live(26.0, left_min=2000), st, book_max=23.5)
    assert kind2 is None
    a = to_alert("over_ceiling", Bid("1", BID.title, 30.0),
                 _live(25.0, left_min=2000))
    assert "ABOVE the book ceiling" in a["reason"]


def test_winning_under_the_ceiling_stays_quiet():
    kind, _ = decide(BID, _live(15.0, left_min=2000), {"status": "WINNING"},
                     book_max=23.5)
    assert kind is None


def test_closed_note_fires_exactly_once():
    kind, st = decide(BID, _live(25.0, left_min=-5, expired=True), {"status": "OUTBID"})
    assert kind == "closed"
    kind2, _ = decide(BID, _live(25.0, left_min=-10, expired=True), st)
    assert kind2 is None


# --- the raise/walk call from the book --------------------------------------

def test_alert_says_walk_away_when_my_max_already_at_book_ceiling():
    live = _live(25.0, left_min=60)
    model, adv = book_advice(BID.title, live)
    assert model is not None and adv is not None
    over = Bid("1", BID.title, adv.max_bid + 5)     # bidding above the ceiling
    a = to_alert("endgame_losing", over, live, model=model, adv=adv)
    assert "WALK AWAY" in a["reason"]
    assert a["verdict"] == "pass"                   # red: act or lose it


def test_alert_says_room_to_raise_when_next_bid_clears_the_ceiling():
    live = _live(10.0, left_min=60)                 # next valid bid = 11
    model, adv = book_advice(BID.title, live)
    low = Bid("1", BID.title, 10.0)                 # outbid... at $10? AT_CAP
    a = to_alert("endgame_losing", low, live, model=model, adv=adv)
    assert "Room to raise" in a["reason"]
    assert f"${adv.max_bid:,.2f}" in a["reason"]


def test_alert_flags_titles_the_book_cannot_judge():
    live = {**_live(30.0, left_min=60), "title": "Random Widget Deluxe"}
    a = to_alert("status_flip", Bid("1", "Random Widget Deluxe", 20.0), live,
                 model=None, adv=None)
    assert "Not in the price book" in a["reason"]


def test_closed_alert_states_likely_outcome():
    live = _live(18.0, left_min=-1, expired=True)   # ended under my max -> won
    a = to_alert("closed", BID, live, model=None, adv=None)
    assert "WON" in a["reason"]
    lost = to_alert("closed", BID, _live(33.0, left_min=-1, expired=True))
    assert "LOST" in lost["reason"]


# --- the run, end to end with a fake API ------------------------------------

class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class FakeSession:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, headers=None, timeout=None):
        return FakeResp(self.payload)


def test_run_alerts_once_then_stays_quiet_on_no_change(tmp_path):
    csv_path = tmp_path / "Auctions in Progress-Shopgoodwill.com.csv"
    _write_csv(csv_path, [
        '"1","TI-84 Plus CE Color Graphing Calculator","GW","$25.00","$20.00","5","08/01/2026 12:00:00 PM ",""',
    ])
    state = tmp_path / "state.json"
    session = FakeSession({
        "title": "TI-84 Plus CE Color Graphing Calculator",
        "currentPrice": 25.0, "minimumBid": 26.0, "bidIncrement": 1.0,
        "numberOfBids": 5, "handlingPrice": 0.0,
        "endTime": "2026-08-01T12:00:00", "serverTime": "2026-08-01T11:00:00",
        "isItemEndTimeExpire": False, "imageServer": "", "imageUrlString": "",
    })
    sent = []

    def notifier(alerts, content=""):
        sent.append((alerts, content))
        return ["webhook"]

    res = mybids.run(csv_path=str(csv_path), notifier=notifier,
                     session=session, state_file=str(state))
    assert res["tracked"] == 1 and res["alerts"] == 1
    assert "LOSING" in sent[0][1]                    # 60 min left + outbid = siren
    assert json.loads(state.read_text())["1"]["status"] == "OUTBID"

    # Same world, second run: state remembers, nothing re-fires.
    res2 = mybids.run(csv_path=str(csv_path), notifier=notifier,
                      session=session, state_file=str(state))
    assert res2["alerts"] == 0 and len(sent) == 1


def test_run_without_a_csv_reports_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIPSCOUT_BIDS_DIR", str(tmp_path))
    monkeypatch.delenv("FLIPSCOUT_BIDS_CSV", raising=False)
    res = mybids.run()
    assert res == {"tracked": 0, "alerts": 0, "sent": []}

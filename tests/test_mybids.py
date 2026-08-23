"""Bid sentry: the CSV is the bid list, proxy math is the outbid detector, and
the final 90 minutes is when it's allowed to get loud."""

import datetime as dt
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


def test_endgame_winning_heads_up_then_countdown_pings():
    # 7/31 Leron: the single early heads-up ("closes in 1.5h") was the LAST
    # thing he heard before losing to a snipe. Winners now get pinged again
    # at 60 and 30 - and only there.
    kind, st = decide(BID, _live(15.0, left_min=85), {"status": "WINNING"})
    assert kind == "endgame_winning"
    kind2, st = decide(BID, _live(15.0, left_min=70), st)
    assert kind2 is None                       # between checkpoints: quiet
    kind3, st = decide(BID, _live(15.0, left_min=58), st)
    assert kind3 == "endgame_winning"          # the 1-hour ping
    kind4, st = decide(BID, _live(15.0, left_min=45), st)
    assert kind4 is None                       # 60 already fired, 30 not reached
    kind5, st = decide(BID, _live(15.0, left_min=28), st)
    assert kind5 == "endgame_winning"          # the 30-minute ping
    kind6, _ = decide(BID, _live(15.0, left_min=10), st)
    assert kind6 is None                       # both spent - no wallpaper


def test_countdown_pings_fire_for_losers_even_without_a_price_move():
    st = {"status": "OUTBID", "last_price": 25.0}
    kind, st = decide(BID, _live(25.0, left_min=80), st)
    assert kind == "endgame_losing"            # window entry
    kind2, st = decide(BID, _live(25.0, left_min=59), st)
    assert kind2 == "endgame_losing"           # 1-hour ping, price unchanged
    kind3, st = decide(BID, _live(25.0, left_min=29), st)
    assert kind3 == "endgame_losing"           # 30-minute ping
    kind4, _ = decide(BID, _live(25.0, left_min=15), st)
    assert kind4 is None


def test_jumping_past_both_checkpoints_pings_once_not_twice():
    # Sentry was off (laptop asleep); first sight of the item is at 20 min.
    kind, st = decide(BID, _live(15.0, left_min=20), {"status": "WINNING"})
    assert kind == "endgame_winning"
    assert sorted(st["milestones"]) == [30.0, 60.0]  # both marked spent
    kind2, _ = decide(BID, _live(15.0, left_min=12), st)
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


def test_winning_with_a_hardened_max_stays_quiet():
    # Max already at/above the ceiling and price under it: nothing to say.
    hardened = Bid("1", BID.title, 24.0)
    kind, _ = decide(hardened, _live(15.0, left_min=2000), {"status": "WINNING"},
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


def test_run_alerts_once_then_stays_quiet_on_no_change(tmp_path, monkeypatch):
    # Sandbox the watchlist AND board lookups, or this test starts tracking
    # the REAL Downloads\flipscout_watchlist.txt / docs\deals.json.
    monkeypatch.setenv("FLIPSCOUT_BIDS_DIR", str(tmp_path))
    monkeypatch.delenv("FLIPSCOUT_WATCHLIST_FILE", raising=False)
    monkeypatch.setenv("FLIPSCOUT_BOARD_FILE", str(tmp_path / "no-board.json"))
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
    monkeypatch.delenv("FLIPSCOUT_WATCHLIST_FILE", raising=False)
    monkeypatch.setenv("FLIPSCOUT_BOARD_FILE", str(tmp_path / "no-board.json"))
    res = mybids.run()
    assert res == {"tracked": 0, "alerts": 0, "sent": []}


# --- watch-only items: no bid yet, waiting to snipe (7/31) -------------------

WATCH = mybids.Bid(item_id="7", title="", my_max=0.0)


def test_load_watchlist_takes_links_bare_ids_and_skips_comments(tmp_path):
    p = tmp_path / "flipscout_watchlist.txt"
    p.write_text(
        "# things to snipe\n"
        "https://shopgoodwill.com/item/230012345\n"
        "shopgoodwill.com/item/230067890?queryband=x\n"
        "230099999\n"
        "https://shopgoodwill.com/item/230012345\n"   # dupe folds
        "not a link at all\n",
        encoding="utf-8")
    got = mybids.load_watchlist(str(p))
    assert [b.item_id for b in got] == ["230012345", "230067890", "230099999"]
    assert all(b.watching for b in got)


def test_watch_item_gets_entry_alert_and_countdown_pings():
    kind, st = decide(WATCH, _live(25.0, left_min=80), {})
    assert kind == "endgame_watch"             # window entry
    kind2, st = decide(WATCH, _live(25.0, left_min=70), st)
    assert kind2 is None
    kind3, st = decide(WATCH, _live(26.0, left_min=58), st)
    assert kind3 == "endgame_watch"            # 1-hour ping
    kind4, st = decide(WATCH, _live(26.0, left_min=29), st)
    assert kind4 == "endgame_watch"            # 30-minute ping
    kind5, _ = decide(WATCH, _live(26.0, left_min=10), st)
    assert kind5 is None


def test_watch_item_quiet_outside_the_window_and_closes_once():
    kind, _ = decide(WATCH, _live(25.0, left_min=2000), {})
    assert kind is None                        # days out: watching is silent
    kind2, st = decide(WATCH, _live(33.0, left_min=-1, expired=True), {})
    assert kind2 == "closed"
    kind3, _ = decide(WATCH, _live(33.0, left_min=-1, expired=True), st)
    assert kind3 is None


def test_watch_alert_names_the_snipe_number_not_a_max():
    a = to_alert("endgame_watch", WATCH, _live(25.0, left_min=28))
    assert "WATCHING" in a["reason"] and "no bid" in a["reason"]
    assert "$0.00" not in a["reason"]          # a watcher has no max to print
    closed = to_alert("closed", WATCH, _live(33.0, left_min=-1, expired=True))
    assert "never bid" in closed["reason"]


# --- auto-watch off the deals board (7/31: "that too much manual work") -----

AUTO = mybids.Bid(item_id="9", title="Singer Featherweight", my_max=0.0, auto=True)


def _board(tmp_path, items):
    p = tmp_path / "deals.json"
    p.write_text(json.dumps({"items": items}), encoding="utf-8")
    return str(p)


def test_load_autowatch_takes_goodwill_deals_closing_in_the_window(tmp_path):
    now = dt.datetime(2026, 7, 31, 19, 0)
    path = _board(tmp_path, [
        {"source": "goodwill", "url": "https://shopgoodwill.com/item/1",
         "ends": "2026-07-31T20:19", "profit_at_open": 110.0, "title": "Featherweight"},
        {"source": "goodwill", "url": "https://shopgoodwill.com/item/2",
         "ends": "2026-08-03T18:44", "profit_at_open": 156.0, "title": "Arcteryx"},
        {"source": "craigslist", "url": "https://x/item/3",
         "ends": "2026-07-31T19:30", "profit_at_open": 900.0, "title": "G7X"},
        {"source": "goodwill", "url": "https://shopgoodwill.com/item/4",
         "ends": "2026-07-31T19:30", "profit_at_open": 40.0, "title": "TI-84"},
    ])
    got = mybids.load_autowatch(board_path=path, now_pt=now)
    # Item 2 is days out, item 3 isn't goodwill; best profit first.
    assert [b.item_id for b in got] == ["1", "4"]
    assert all(b.auto and b.watching for b in got)


def test_load_autowatch_caps_at_top_and_survives_a_missing_board(tmp_path):
    now = dt.datetime(2026, 7, 31, 19, 0)
    items = [{"source": "goodwill", "url": f"https://shopgoodwill.com/item/{i}",
              "ends": "2026-07-31T19:30", "profit_at_open": i, "title": f"t{i}"}
             for i in range(20)]
    got = mybids.load_autowatch(board_path=_board(tmp_path, items), now_pt=now, top=5)
    assert len(got) == 5 and got[0].item_id == "19"      # best profit first
    assert mybids.load_autowatch(board_path=str(tmp_path / "gone.json"),
                                 now_pt=now) == []


def test_auto_watch_fires_exactly_one_snipe_call_at_thirty():
    kind, st = decide(AUTO, _live(25.0, left_min=80), {})
    assert kind is None                        # no entry alert - board churn
    kind2, st = decide(AUTO, _live(25.0, left_min=55), st)
    assert kind2 is None                       # no 60-min ping either
    kind3, st = decide(AUTO, _live(25.0, left_min=28), st)
    assert kind3 == "endgame_watch"            # THE snipe call
    kind4, st = decide(AUTO, _live(26.0, left_min=12), st)
    assert kind4 is None                       # once means once
    kind5, _ = decide(AUTO, _live(26.0, left_min=-1, expired=True), st)
    assert kind5 is None                       # and no closed note


def test_run_merges_watchlist_and_prefers_the_real_bid(tmp_path, monkeypatch):
    csv_path = tmp_path / "Auctions in Progress-Shopgoodwill.com.csv"
    _write_csv(csv_path, [
        '"1","TI-84 Plus CE Color Graphing Calculator","GW","$25.00","$20.00","5","08/01/2026 12:00:00 PM ",""',
    ])
    wl = tmp_path / "flipscout_watchlist.txt"
    wl.write_text("https://shopgoodwill.com/item/1\n"    # already bid: CSV wins
                  "https://shopgoodwill.com/item/2\n", encoding="utf-8")
    monkeypatch.setenv("FLIPSCOUT_WATCHLIST_FILE", str(wl))
    monkeypatch.setenv("FLIPSCOUT_BOARD_FILE", str(tmp_path / "no-board.json"))
    session = FakeSession({
        "title": "TI-84 Plus CE Color Graphing Calculator",
        "currentPrice": 25.0, "minimumBid": 26.0, "bidIncrement": 1.0,
        "numberOfBids": 5, "handlingPrice": 0.0,
        "endTime": "2026-08-01T12:00:00", "serverTime": "2026-08-01T11:00:00",
        "isItemEndTimeExpire": False, "imageServer": "", "imageUrlString": "",
    })
    res = mybids.run(csv_path=str(csv_path), notifier=lambda a, content="": ["w"],
                     session=session, state_file=str(tmp_path / "s.json"))
    assert res["tracked"] == 2                 # item 1 once (as a bid) + item 2
    state = json.loads((tmp_path / "s.json").read_text())
    assert state["1"]["status"] == "OUTBID"    # proxy math, not WATCHING
    assert state["2"]["status"] == "WATCHING"


def test_winning_below_the_ceiling_says_harden_your_max():
    # The measured leak: Featherweight lost by $1, SX-70 by $0.12 - maxes set
    # below the ceiling donate wins to whoever bids $1 more.
    low = Bid("1", BID.title, 15.0)                 # winning at 10, ceiling ~23
    kind, st = decide(low, _live(10.0, left_min=2000), {"status": "WINNING"},
                      book_max=23.5)
    assert kind == "raise_max"
    # Once per (item, max): same max stays quiet...
    kind2, st2 = decide(low, _live(11.0, left_min=2000), st, book_max=23.5)
    assert kind2 is None
    # ...but a re-exported CSV with a raised (still-low) max re-arms it.
    raised = Bid("1", BID.title, 18.0)
    kind3, _ = decide(raised, _live(11.0, left_min=2000), st2, book_max=23.5)
    assert kind3 == "raise_max"


def test_raise_max_alert_names_the_ceiling():
    live = _live(10.0, left_min=2000)
    model, adv = book_advice(BID.title, live)
    a = to_alert("raise_max", Bid("1", BID.title, 15.0), live, model=model, adv=adv)
    assert "Harden your max" in a["reason"]
    assert f"${adv.max_bid:,.2f}" in a["reason"]
    assert "Room to raise" not in a["reason"]        # no duplicate tail


def test_an_endgame_alert_carries_its_category_so_it_routes(monkeypatch):
    """🚨 EVERY ENDGAME ALERT LANDED IN #deals. notify routes on the price
    book's category and this dict carried none, so a Citizen about to be lost
    posted to the general channel while the same watch from `hunt` posted to
    #watches. `model` was already in hand - it was simply never passed on."""
    from flipscout import notify, pricebook as pb
    import inspect
    from flipscout import mybids
    src = inspect.getsource(mybids)
    assert '"category": getattr(model, "category", "")' in src, \
        "the endgame alert dict must carry the book category"
    # and the value it carries actually routes
    watch = next(m for m in pb.MODELS if m.category == "watches")
    assert notify.channel_for({"category": watch.category,
                               "title": watch.label}) == "watches"

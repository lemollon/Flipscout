"""Guardrails for the ShopGoodwill sniper.

This module spends real money, so the tests are about the LIMITS, not the
happy path. Every one of these is a rule Claude must not be able to talk
itself out of:

  * it never bids without an armed max
  * it never bids ABOVE that max
  * it bids ONCE per item, ever
  * a kill switch stops everything
  * it refuses to start a bid too close to the close, because a half-placed
    bid at T-5s is worse than no bid - you cannot tell whether it landed

Nothing here touches the network or the bid form.
"""

import json
import re

import pytest

from flipscout import snipe


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(snipe, "ARMED_PATH", tmp_path / "armed.json")
    monkeypatch.setattr(snipe, "KILL_SWITCH", tmp_path / "SNIPE_DISABLED")
    monkeypatch.setattr(snipe, "PROFILE_DIR", tmp_path / "profile")
    # Most tests exercise what happens AFTER the bid form is proven, so give
    # them a verified record - the same shape tests/test_hibidsnipe.py uses.
    # The gate itself is tested separately below.
    bf = tmp_path / "sgw_bidform.json"
    bf.write_text('{"confirm_dialog": true, "buttons": ["Confirm Bid"]}',
                  encoding="utf-8")
    monkeypatch.setattr(snipe, "BIDFORM_PATH", bf)
    yield


def _armed(tmp, **over):
    a = {"id": "111", "title": "Nintendo Switch OLED Console", "max_bid": 50.0,
         "end_time": "2026-08-18T10:00:00", "status": "ARMED",
         "url": "https://shopgoodwill.com/item/111"}
    a.update(over)
    snipe.save_armed({"111": a})
    return a


def _detail(left_s=100.0, minimum=10.0, ended=False):
    return {"serverTime": "2026-08-18T09:58:20", "endTime": "2026-08-18T10:00:00",
            "minimumBid": minimum, "currentPrice": minimum - 1,
            "isItemEndTimeExpire": ended, "title": "Nintendo Switch OLED Console",
            "_left": left_s}


def _patch(monkeypatch, left, minimum=10.0, ended=False, spy=None):
    monkeypatch.setattr(snipe, "detail", lambda iid: _detail(left, minimum, ended))
    monkeypatch.setattr(snipe, "seconds_left", lambda d: left)
    monkeypatch.setattr("flipscout.notify.notify", lambda *a, **k: None)
    calls = []
    def fake_place(iid, amount, dry_run):
        calls.append((iid, amount, dry_run))
        return True, f"BID PLACED at ${amount:.2f}"
    monkeypatch.setattr(snipe, "place_bid", spy or fake_place)
    return calls


# ------------------------------------------------------------- the limits --

def test_it_never_bids_above_the_armed_max(tmp_path, monkeypatch):
    """The single most important rule in the module."""
    _armed(tmp_path, max_bid=50.0)
    calls = _patch(monkeypatch, left=120.0, minimum=10.0)
    snipe.run(dry_run=False)
    assert calls, "should have bid"
    iid, amount, _ = calls[0]
    assert amount == 50.0, "must bid exactly the armed max, never more"


def test_it_passes_when_the_required_bid_exceeds_the_max(tmp_path, monkeypatch):
    _armed(tmp_path, max_bid=20.0)
    calls = _patch(monkeypatch, left=120.0, minimum=25.0)   # needs $25, cap $20
    snipe.run(dry_run=False)
    assert not calls, "must not bid when the minimum is above the cap"
    assert snipe.load_armed()["111"]["status"] == "PASSED_TOO_HIGH"


def test_it_bids_only_once_per_item_ever(tmp_path, monkeypatch):
    """No bidding wars. Once it has acted, that item is finished."""
    _armed(tmp_path, max_bid=50.0)
    calls = _patch(monkeypatch, left=120.0)
    snipe.run(dry_run=False)
    snipe.run(dry_run=False)
    snipe.run(dry_run=False)
    assert len(calls) == 1, "armed items must be single-shot"


def test_the_kill_switch_stops_everything(tmp_path, monkeypatch):
    _armed(tmp_path, max_bid=50.0)
    calls = _patch(monkeypatch, left=60.0)
    snipe.KILL_SWITCH.write_text("stop", encoding="utf-8")
    snipe.run(dry_run=False)
    assert not calls


def test_it_will_not_start_a_bid_too_close_to_the_close(tmp_path, monkeypatch):
    """🚨 A half-placed bid at T-5s is WORSE than no bid: you cannot tell
    whether it landed, so you cannot decide anything afterwards."""
    _armed(tmp_path, max_bid=50.0)
    calls = _patch(monkeypatch, left=10.0)      # under ABORT_UNDER_S
    snipe.run(dry_run=False)
    assert not calls
    assert snipe.load_armed()["111"]["status"] == "MISSED"


def test_it_waits_until_inside_the_window(tmp_path, monkeypatch):
    _armed(tmp_path, max_bid=50.0)
    calls = _patch(monkeypatch, left=1800.0)    # 30 min out
    snipe.run(dry_run=False)
    assert not calls
    assert snipe.load_armed()["111"]["status"] == "ARMED", "must stay armed"


def test_dry_run_never_reaches_the_bid_button(tmp_path, monkeypatch):
    _armed(tmp_path, max_bid=50.0)
    calls = _patch(monkeypatch, left=120.0)
    snipe.run(dry_run=True)
    assert calls and calls[0][2] is True, "place_bid must receive dry_run=True"
    assert snipe.load_armed()["111"]["status"] == "DRY_RUN"


def test_nothing_armed_means_nothing_happens(tmp_path, monkeypatch):
    calls = _patch(monkeypatch, left=60.0)
    snipe.run(dry_run=False)
    assert not calls


def test_an_ended_auction_is_marked_not_bid(tmp_path, monkeypatch):
    _armed(tmp_path, max_bid=50.0)
    calls = _patch(monkeypatch, left=-5.0, ended=True)
    snipe.run(dry_run=False)
    assert not calls
    assert snipe.load_armed()["111"]["status"] == "ENDED_UNBID"


# ------------------------------------------------------------- mechanics ---

@pytest.mark.parametrize("s,want", [
    ("https://shopgoodwill.com/item/273876344", "273876344"),
    ("273876344", "273876344"),
    ("shopgoodwill.com/item/273876344?x=1", "273876344"),
])
def test_item_id_parsing(s, want):
    assert snipe.item_id(s) == want


def test_timing_uses_server_time_not_the_local_clock():
    """🚨 Both stamps come from the SAME response and are both Pacific, so the
    local clock and timezones never enter into it. Comparing a local wall clock
    against a remote end time is the exact bug that blinded FLASHPOINT."""
    d = {"serverTime": "2026-08-18T09:57:00", "endTime": "2026-08-18T10:00:00"}
    assert snipe.seconds_left(d) == 180.0


def test_seconds_left_is_none_when_the_stamps_are_unusable():
    assert snipe.seconds_left({"serverTime": "junk", "endTime": "10:00"}) is None


# --- a bid must be PROVEN, never assumed -------------------------------------

def test_no_confirmation_is_a_failure_not_a_win(monkeypatch, tmp_path):
    """🚨 THE WORST BUG OF 2026-08-18. place_bid ended with

        return True, "bid submitted (no confirmation text seen)"

    - claiming success with no evidence whatsoever. A real snipe reported "you
    are winning, $6.01 under your max" on a lot that read "Number of Bids: 0"
    and then closed unsold. Leron only found out because he asked.

    A money action must FAIL CLOSED: a false win is worse than a missed lot,
    because you can re-bid a lot you know you lost.
    """
    import inspect

    from flipscout import snipe as S
    src = inspect.getsource(S.place_bid)
    assert "no confirmation text seen" not in src, (
        "the fail-open branch is back - unknown must never return True")
    # the outcome is decided by the site's own bid count, not by page prose
    # 🚨 via bid_count(), NOT a raw field read. This asserted `"numBids" in src`
    # and passed for weeks while being the bug: the detail API returns
    # `numberOfBids` and never `numBids`, so the proof-of-landing compared None
    # to None on every snipe and always fell through to "UNVERIFIED".
    assert "bid_count(" in src
    assert '.get("numBids")' not in src, "raw numBids reads are the bug"


def test_place_bid_reads_the_bid_count_before_and_after():
    """Ground truth for "did it land" is the count moving, not the absence of
    an error message."""
    import inspect

    from flipscout import snipe as S
    src = inspect.getsource(S.place_bid)
    assert "bids_before" in src
    assert "landed" in src


def test_place_bid_clicks_a_confirmation_step_if_one_appears():
    import inspect

    from flipscout import snipe as S
    src = inspect.getsource(S.place_bid)
    assert re.search(r"confirm", src, re.I), "the confirm step must be handled"


def test_verify_exists_so_the_flow_is_observed_not_guessed():
    """The HiBid bid form was settled by watching Leron do it once. Guessing at
    selectors twice in one day is what caused this."""
    from flipscout import snipe as S
    assert callable(S.verify)
    assert S.BIDFORM_PATH.name.endswith(".json")


def test_it_refuses_to_bid_until_the_bid_form_is_proven(tmp_path, monkeypatch, capsys):
    """🚨 THE GATE HIBID HAD AND THIS FILE DID NOT.

    `bidform()` was written and never called, so ShopGoodwill bid BLIND:
    place_bid clicks "Place My Bid" and then guesses the confirmation control
    from a fixed list of labels. Measured 2026-08-18/19, three armed lots all
    failed identically - "submitted but could not confirm it landed" - and
    every one closed with Bids: 0. A $72.70 max lost a camcorder that ended at
    $10.00 with nobody bidding at all.

    Refusing names the one command that fixes it; bidding blind just loses the
    lot and reports a fault afterwards.
    """
    monkeypatch.setattr(snipe, "BIDFORM_PATH", tmp_path / "nope.json")
    snipe.save_armed({"1": {"item_id": "1", "max_bid": 10.0, "status": "ARMED",
                            "url": "https://shopgoodwill.com/item/1"}})
    called = []
    monkeypatch.setattr(snipe, "place_bid",
                        lambda *a, **k: called.append(a) or (True, "should not happen"))
    assert snipe.run() == 0
    assert not called, "bid was attempted with an unproven form"
    out = capsys.readouterr().out
    assert "snipe verify" in out
    # ...and the armed lot is left ARMED, not retired, so it can still be won.
    assert snipe.load_armed()["1"]["status"] == "ARMED"


def test_the_confirm_step_never_re_clicks_the_trigger_button():
    """🚨 "place my bid" WAS IN THE CONFIRM LIST, and query_selector_all
    returns DOM order - so the "confirmation" click usually landed back on the
    ORIGINAL button. The real confirm control was never pressed, which is why
    a bid could be submitted and never land.

    Asserted against the source because the behaviour lives inside a Playwright
    session that these tests deliberately never open.
    """
    import inspect
    src = inspect.getsource(snipe.place_bid)
    assert "if b == btn:" in src and "continue" in src, \
        "the confirm loop must skip the button it already clicked"


def test_the_confirm_click_uses_the_label_verify_recorded():
    """🚨 GROUND TRUTH FROM THE REAL DIALOG (Leron, 2026-08-20, item 274020144).

        heading : Confirm Bid
        body    : "Click Place Bid to confirm your bid of $7.99"
                  "Once you place your bid, you cannot cancel it."
        buttons : [Close] [Place Bid]

    So the confirm control is "Place Bid" and the TRIGGER is "Place My Bid".
    Both matched the old hardcoded list, and query_selector_all returns DOM
    order - so the "confirmation" click landed back on the trigger, the real
    confirm was never pressed, and three snipes submitted without landing.

    place_bid must prefer the RECORDED label so a re-wording is a one-line
    re-verify instead of another silent miss.
    """
    import inspect
    src = inspect.getsource(snipe.place_bid)
    assert "bidform().get(\"confirm_text\")" in src
    assert "if b == btn:" in src, "must never re-click the trigger"


def test_the_recorded_bidform_matches_what_was_observed():
    """The file is evidence, not configuration - keep it honest.

    Reads the REAL repo file, not the fixture's stand-in: the autouse fixture
    redirects BIDFORM_PATH so the other tests can run without one.
    """
    import json as _json
    import pathlib
    real = pathlib.Path(snipe.__file__).resolve().parent.parent / "sgw_bidform.json"
    if not real.exists():
        pytest.skip("sgw_bidform.json not present in this checkout")
    bf = _json.loads(real.read_text(encoding="utf-8"))
    assert bf.get("confirm_text") == "Place Bid"
    assert bf.get("trigger_button") == "Place My Bid"
    assert bf.get("confirm_dialog") is True
    assert bf.get("max_input") == "#currentBid"

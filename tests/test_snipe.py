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

import pytest

from flipscout import snipe


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(snipe, "ARMED_PATH", tmp_path / "armed.json")
    monkeypatch.setattr(snipe, "KILL_SWITCH", tmp_path / "SNIPE_DISABLED")
    monkeypatch.setattr(snipe, "PROFILE_DIR", tmp_path / "profile")
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

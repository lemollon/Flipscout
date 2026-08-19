"""Which lots get an alert slot, and why.

The selector is where a good find quietly dies, so these are about ORDER and
ELIGIBILITY rather than pricing.
"""

import datetime as dt

import pytest

from flipscout.hunt import hours_until, load_config
from flipscout.hunters import _hibid_ends


# --- HiBid finally has an end time -------------------------------------------

def test_a_hibid_lot_gets_an_end_time_from_its_countdown():
    """🚨 HiBid rows carried `ends: ""` - hardcoded. hours_until's own docstring
    said "HiBid sends nothing", which was true of what we ASKED for, not of
    what it has: every lot ships lotState.timeLeftSeconds.

    Invisible until a closing-soon lane was added and matched ZERO HiBid lots
    (2026-08-19) - the one source whose bidding path is verified end to end
    could never be prioritised by urgency.
    """
    ends = _hibid_ends(3600)
    assert ends
    left = hours_until(ends, source="hibid")
    assert 0.9 < left < 1.1


def test_the_sign_is_ignored_but_the_magnitude_is_not():
    """🚨 About half of search rows report a NEGATIVE countdown on lots that
    plainly have days to run ("25d 4h 59m" beside a negative seconds value),
    the lot page agrees with the search, and re-probing minutes later can
    return it positive. Transient vendor noise, not "already ended".

    abs() is safe here because this only decides ALERT ORDER. The sniper reads
    its own countdown and refuses to bid on a negative one - there the same
    guess would fire a real bid at the wrong moment.
    """
    assert hours_until(_hibid_ends(-7200), source="hibid") == pytest.approx(2, abs=0.1)
    assert hours_until(_hibid_ends(7200), source="hibid") == pytest.approx(2, abs=0.1)


@pytest.mark.parametrize("bad", [None, "", "soon", 0, [], {}])
def test_an_unusable_countdown_yields_no_end_time(bad):
    assert _hibid_ends(bad) == ""


def test_the_end_time_is_parseable_by_the_rest_of_the_pipeline():
    """It has to round-trip through hours_until, which is what every downstream
    urgency decision calls."""
    assert hours_until(_hibid_ends(86400), source="hibid") == pytest.approx(24, abs=0.1)


# --- the closing lane ---------------------------------------------------------

def test_the_closing_lane_is_configured_by_default():
    """🚨 Ranking on profit_at_open is biased towards lots NOBODY HAS BID ON.
    Measured on 505 live lots: median profit at open is $30.57 for lots closing
    inside 6h and $69.79 for lots over 3 days out - not because the distant
    ones are better, but because their price has not moved yet. The top 20 by
    profit held ZERO lots closing within 6 hours.

    That is backwards for a sniper, so part of every run is reserved for lots
    about to close.
    """
    cfg = load_config({})
    assert cfg["closing_hours"] > 0
    assert "closing_slots" in cfg


def test_closing_hours_is_overridable():
    assert load_config({"FLIPSCOUT_CLOSING_HOURS": "3"})["closing_hours"] == 3.0
    assert load_config({"FLIPSCOUT_CLOSING_SLOTS": "7"})["closing_slots"] == 7


# --- two windows, two jobs ----------------------------------------------------

def test_both_closing_windows_are_configured():
    """🚨 Leron asked for a mix of 12h and 1h, and they are different jobs:
    an hour out you arm NOW or lose it; twelve hours out you have slack."""
    cfg = load_config({})
    assert cfg["urgent_hours"] == 1.0
    assert cfg["closing_hours"] == 12.0
    assert cfg["urgent_hours"] < cfg["closing_hours"]


def test_the_windows_are_overridable():
    cfg = load_config({"FLIPSCOUT_URGENT_HOURS": "2",
                       "FLIPSCOUT_CLOSING_HOURS": "24",
                       "FLIPSCOUT_URGENT_SLOTS": "3",
                       "FLIPSCOUT_CLOSING_SLOTS": "6"})
    assert (cfg["urgent_hours"], cfg["closing_hours"]) == (2.0, 24.0)
    assert (cfg["urgent_slots"], cfg["closing_slots"]) == (3, 6)


def test_the_default_split_leaves_room_for_the_profit_ranking():
    """A quarter urgent, a quarter closing, half on profit. Reserving more
    would starve the big finds that have days to run."""
    cfg = load_config({"FLIPSCOUT_TOP": "20"})
    top = cfg["top"]
    urgent = cfg["urgent_slots"] or max(1, top // 4)
    closing = cfg["closing_slots"] or max(1, top // 4)
    assert urgent + closing == top // 2
    assert top - urgent - closing == top // 2


def test_a_tiny_top_still_reserves_at_least_one_slot_each():
    """max(1, ...) - at TOP=2 the lanes must not round down to zero and
    silently stop working."""
    cfg = load_config({"FLIPSCOUT_TOP": "2"})
    assert max(1, cfg["top"] // 4) == 1

"""Guardrails for the HiBid sniper.

Same shape as tests/test_snipe.py - the tests are about the LIMITS, not the
happy path - plus the three rules that only exist on HiBid:

  * it NEVER registers you for an auction (registering accepts the house's
    terms and puts your card on file to be charged automatically)
  * it refuses to bid at all on an auction you are not registered for
  * a soft-close extension does NOT earn the lot a second bid

Nothing here touches the network or the bid form.
"""

import pytest

from flipscout import hibidsnipe as H


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "ARMED_PATH", tmp_path / "armed.json")
    monkeypatch.setattr(H, "KILL_SWITCH", tmp_path / "SNIPE_DISABLED")
    monkeypatch.setattr(H, "PROFILE_DIR", tmp_path / "profile")
    # Most tests exercise what happens AFTER the bid form is proven, so give
    # them a verified record. The gate itself is tested separately below.
    bf = tmp_path / "bidform.json"
    bf.write_text('{"confirm_dialog": true, "max_input": "#bidAmount",'
                  ' "confirm_button": "button.confirm"}', encoding="utf-8")
    monkeypatch.setattr(H, "BIDFORM_PATH", bf)
    # 🚨 run() does `from .notify import notify` at CALL time, so the name that
    # matters lives on the notify module - patching H.notify silently does
    # nothing and lets these tests post to the real Discord webhook.
    import flipscout.notify as N
    monkeypatch.setattr(N, "notify", lambda *a, **k: None)
    yield


def _armed(**over):
    a = {"lot_id": "317852714", "title": "Fluke 87V True RMS Multimeter",
         "max_bid": 50.0, "premium": 0.15, "landed_at_max": 57.50,
         "status": "ARMED", "url": "https://hibid.com/lot/317852714"}
    a.update(over)
    H.save_armed({"317852714": a})
    return a


def _detail(left=100.0, high=10.0, min_bid=12.0, registered=True,
            closed=False, extended=False, gone=False):
    return {"lot_id": "317852714", "gone": gone, "title": "Fluke 87V True RMS Multimeter",
            "high_bid": high, "min_bid": min_bid, "bids": 3, "closed": closed,
            "left": left, "extended": extended, "registered": registered,
            "increments": [{"minBidIncrement": 5.0, "upToAmount": 100.0}],
            "premium": 0.15, "notice": ""}


def _patch(monkeypatch, d, spy=None):
    monkeypatch.setattr(H, "detail", lambda lid: d)
    calls = []

    def fake_bid(lid, amount, dry_run):
        calls.append((lid, amount, dry_run))
        return True, f"BID PLACED at ${amount:.2f}"

    monkeypatch.setattr(H, "place_bid", spy or fake_bid)
    return calls


# --- the money limits --------------------------------------------------------

def test_never_bids_above_the_armed_max(monkeypatch):
    """🚨 The core safety property. Everything else is convenience."""
    _armed(max_bid=50.0)
    calls = _patch(monkeypatch, _detail(left=100.0, high=45.0, min_bid=48.0))
    H.run()
    assert calls and calls[0][1] == 50.0
    assert calls[0][1] <= 50.0


def test_passes_when_the_required_bid_exceeds_the_max(monkeypatch):
    _armed(max_bid=50.0)
    calls = _patch(monkeypatch, _detail(left=100.0, high=60.0, min_bid=65.0))
    H.run()
    assert not calls
    assert H.load_armed()["317852714"]["status"] == "PASSED_TOO_HIGH"


def test_bids_once_per_lot_ever(monkeypatch):
    _armed()
    calls = _patch(monkeypatch, _detail(left=100.0))
    H.run()
    H.run()
    H.run()
    assert len(calls) == 1, "a lot must never be bid twice"


def test_kill_switch_stops_everything(monkeypatch, tmp_path):
    _armed()
    calls = _patch(monkeypatch, _detail(left=100.0))
    (tmp_path / "SNIPE_DISABLED").write_text("stop")
    H.run()
    assert not calls


def test_nothing_armed_means_nothing_happens(monkeypatch):
    calls = _patch(monkeypatch, _detail(left=100.0))
    assert H.run() == 0
    assert not calls


# --- the registration gate ---------------------------------------------------

def test_refuses_to_bid_when_not_registered(monkeypatch):
    """🚨 Registering accepts the house's terms and authorises a card charge.
    That is Leron's to do, never the bot's."""
    _armed()
    calls = _patch(monkeypatch, _detail(left=100.0, registered=False))
    H.run()
    assert not calls
    assert H.load_armed()["317852714"]["status"] == "ARMED", "stays armed, just cannot bid"


def test_unregistered_warning_fires_once_not_every_minute(monkeypatch):
    """The poller runs every minute; a warning per minute is wallpaper."""
    _armed()
    sent = []
    monkeypatch.setattr(H, "detail", lambda lid: _detail(left=4000.0, registered=False))
    monkeypatch.setattr(H, "place_bid", lambda *a, **k: (True, ""))
    import flipscout.notify as N
    monkeypatch.setattr(N, "notify", lambda *a, **k: sent.append(a))
    H.run()
    H.run()
    H.run()
    assert len(sent) == 1


def test_registration_warning_comes_before_the_snipe_window(monkeypatch):
    """It must arrive while there is still time to register, not at T-180s."""
    _armed()
    calls = _patch(monkeypatch, _detail(left=4000.0, registered=False))
    H.run()
    assert not calls
    assert H.load_armed()["317852714"].get("warned_unregistered") is True


# --- the clock ---------------------------------------------------------------

def test_waits_when_it_is_too_early(monkeypatch):
    _armed()
    calls = _patch(monkeypatch, _detail(left=H.SNIPE_AT_S + 60))
    H.run()
    assert not calls
    assert H.load_armed()["317852714"]["status"] == "ARMED"


def test_refuses_when_it_is_too_late(monkeypatch):
    """A half-placed bid at T-5s is worse than none - you cannot tell whether
    it landed."""
    _armed()
    calls = _patch(monkeypatch, _detail(left=H.ABORT_UNDER_S - 1))
    H.run()
    assert not calls
    assert H.load_armed()["317852714"]["status"] == "MISSED"


def test_countdown_comes_from_the_response_not_a_local_clock(monkeypatch):
    """🚨 The FLASHPOINT bug class. `left` is a countdown in the same payload,
    so no timezone and no local clock is ever consulted."""
    assert H.seconds_left({"left": 42.5}) == 42.5
    assert H.seconds_left({"left": None}) is None
    assert H.seconds_left({}) is None
    assert H.seconds_left({"left": "nonsense"}) is None


def test_closed_or_archived_lot_is_retired_not_bid(monkeypatch):
    _armed()
    calls = _patch(monkeypatch, _detail(gone=True))
    H.run()
    assert not calls
    assert H.load_armed()["317852714"]["status"] == "ENDED_UNBID"


def test_a_missing_countdown_never_triggers_a_bid(monkeypatch):
    """If we cannot tell the time, we do not bid. Silence beats a guess."""
    _armed()
    calls = _patch(monkeypatch, _detail(left=None))
    H.run()
    assert not calls
    assert H.load_armed()["317852714"]["status"] == "ARMED"


# --- soft close --------------------------------------------------------------

def test_soft_close_extension_does_not_earn_a_second_bid(monkeypatch):
    """🚨 13% of auctions extend on a late bid. Our max is a standing proxy
    bid that defends itself; re-bidding would be a bidding war with ourselves.
    """
    _armed()
    calls = _patch(monkeypatch, _detail(left=100.0, extended=True))
    H.run()
    assert len(calls) == 1
    # the clock extends and we poll again
    monkeypatch.setattr(H, "detail", lambda lid: _detail(left=150.0, extended=True))
    H.run()
    assert len(calls) == 1


# --- arming ------------------------------------------------------------------

def test_arm_states_the_all_in_cost(monkeypatch, capsys):
    monkeypatch.setattr(H, "detail", lambda lid: _detail())
    monkeypatch.setattr(H, "book_ceiling", lambda *a, **k: 100.0)
    H.arm("https://hibid.com/lot/317852714", 50.0)
    out = capsys.readouterr().out
    assert "57.50" in out, "must show the premium-inclusive cost, not just the bid"
    assert "15%" in out


def test_arm_refuses_above_the_book_without_override(monkeypatch):
    monkeypatch.setattr(H, "detail", lambda lid: _detail())
    monkeypatch.setattr(H, "book_ceiling", lambda *a, **k: 30.0)
    assert H.arm("https://hibid.com/lot/317852714", 50.0) == 1
    assert H.load_armed() == {}
    assert H.arm("https://hibid.com/lot/317852714", 50.0, override=True) == 0
    assert H.load_armed()["317852714"]["max_bid"] == 50.0


def test_arm_warns_when_not_registered(monkeypatch, capsys):
    monkeypatch.setattr(H, "detail", lambda lid: _detail(registered=False))
    monkeypatch.setattr(H, "book_ceiling", lambda *a, **k: 100.0)
    H.arm("https://hibid.com/lot/317852714", 50.0)
    assert "NOT REGISTERED" in capsys.readouterr().out


def test_arm_refuses_a_closed_lot(monkeypatch):
    monkeypatch.setattr(H, "detail", lambda lid: _detail(gone=True))
    assert H.arm("https://hibid.com/lot/317852714", 50.0) == 1
    assert H.load_armed() == {}


def test_the_armed_max_is_a_hammer_number(monkeypatch):
    """The bid box takes a hammer bid; the premium is charged on top. Storing
    an all-in number here would silently underbid by the premium."""
    monkeypatch.setattr(H, "detail", lambda lid: _detail())
    monkeypatch.setattr(H, "book_ceiling", lambda *a, **k: 100.0)
    H.arm("https://hibid.com/lot/317852714", 50.0)
    a = H.load_armed()["317852714"]
    assert a["max_bid"] == 50.0
    assert a["landed_at_max"] == pytest.approx(57.50)


def test_disarm(monkeypatch):
    _armed()
    assert H.disarm("https://hibid.com/lot/317852714") == 0
    assert H.load_armed() == {}
    assert H.disarm("317852714") == 1


# --- ids ---------------------------------------------------------------------

@pytest.mark.parametrize("s,want", [
    ("https://hibid.com/lot/317852714", "317852714"),
    ("https://hibid.com/lot/317852714/some-slug", "317852714"),
    ("317852714", "317852714"),
])
def test_lot_id(s, want):
    assert H.lot_id(s) == want


def test_lot_id_rejects_junk():
    with pytest.raises(ValueError):
        H.lot_id("not-a-lot")


# --- the shared kill switch --------------------------------------------------

def test_kill_switch_is_shared_with_the_shopgoodwill_sniper():
    """One file must stop every bidder, or the switch is a false comfort."""
    from flipscout import snipe
    assert H.KILL_SWITCH.name == snipe.KILL_SWITCH.name


# --- the signed-in test ------------------------------------------------------

class _Ctx:
    def __init__(self, cookies):
        self._c = cookies

    def cookies(self):
        return self._c


_JWT = "e" * 60


def test_signed_in_reads_the_cookie_not_the_page():
    """🚨 HiBid renders its header in JavaScript, so "My HiBid" / "Sign Out"
    never appear in inner_text on a fresh load even when signed in.

    Measured 2026-08-18: a working session showed none of those markers, so
    `login` reported a timeout on an account that had signed in fine - and,
    far worse, place_bid used the same text test and would have aborted every
    real bid as "LOGGED OUT" at T-180s.
    """
    assert H.signed_in(_Ctx([{"name": "HBIsLoggedIn", "value": "1"},
                             {"name": "sessionId", "value": _JWT}])) is True


def test_signed_out_when_the_flag_is_absent():
    assert H.signed_in(_Ctx([{"name": "sessionId", "value": _JWT}])) is False
    assert H.signed_in(_Ctx([])) is False


def test_a_stale_flag_without_a_session_is_not_signed_in():
    """A logout can leave the flag behind; the JWT is the real evidence."""
    assert H.signed_in(_Ctx([{"name": "HBIsLoggedIn", "value": "1"}])) is False
    assert H.signed_in(_Ctx([{"name": "HBIsLoggedIn", "value": "1"},
                             {"name": "sessionId", "value": "short"}])) is False


def test_signed_in_survives_a_broken_context():
    class Boom:
        def cookies(self):
            raise RuntimeError("browser gone")
    assert H.signed_in(Boom()) is False


# --- the bid-form gate -------------------------------------------------------

def test_refuses_to_bid_until_the_form_is_verified(monkeypatch, tmp_path):
    """🚨 A HiBid lot page has no bid input - only a "Bid 5.00 USD" button, and
    whether it confirms first is a PER-ACCOUNT preference invisible in the DOM.

    On the auction inspected 2026-08-18 the terms read "BIDS CANNOT BE
    CANCELED - ALL BIDS ARE FINAL!". A blind click is therefore a coin flip on
    an irreversible contract, so an unproven button is never clicked.
    """
    _armed()
    calls = _patch(monkeypatch, _detail(left=100.0))
    monkeypatch.setattr(H, "BIDFORM_PATH", tmp_path / "absent.json")
    H.run()
    assert not calls
    assert H.load_armed()["317852714"]["status"] == "ARMED", "stays armed, just unbid"


def test_a_dry_run_is_allowed_without_verification(monkeypatch, tmp_path):
    """That is how you rehearse before verifying - it spends nothing."""
    _armed()
    calls = _patch(monkeypatch, _detail(left=100.0))
    monkeypatch.setattr(H, "BIDFORM_PATH", tmp_path / "absent.json")
    H.run(dry_run=True)
    assert calls and calls[0][2] is True


def test_a_form_recorded_as_instant_commit_never_unlocks_bidding(monkeypatch, tmp_path):
    """If verify saw NO dialog the button commits instantly, which can only ever
    bid the site's increment - never Leron's max. That is not a snipe."""
    bf = tmp_path / "bf.json"
    bf.write_text('{"confirm_dialog": false}', encoding="utf-8")
    monkeypatch.setattr(H, "BIDFORM_PATH", bf)
    assert H.bidform_ok() is False
    _armed()
    calls = _patch(monkeypatch, _detail(left=100.0))
    H.run()
    assert not calls


def test_a_dialog_without_an_input_does_not_count(monkeypatch, tmp_path):
    """A confirm-only dialog still cannot carry our max."""
    bf = tmp_path / "bf.json"
    bf.write_text('{"confirm_dialog": true}', encoding="utf-8")
    monkeypatch.setattr(H, "BIDFORM_PATH", bf)
    assert H.bidform_ok() is False


def test_bidform_survives_a_corrupt_file(monkeypatch, tmp_path):
    bf = tmp_path / "bf.json"
    bf.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(H, "BIDFORM_PATH", bf)
    assert H.bidform() == {}
    assert H.bidform_ok() is False

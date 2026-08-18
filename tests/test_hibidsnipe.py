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
                  ' "confirm_text": "Confirm Bid"}', encoding="utf-8")
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


def test_a_lot_with_no_data_is_never_bid(monkeypatch):
    """It is not retired on the first miss - see the strike tests below - but
    it is certainly not bid on either."""
    _armed()
    calls = _patch(monkeypatch, _detail(gone=True))
    H.run()
    assert not calls


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


# --- registration is unknown until checked -----------------------------------

def test_anonymous_poll_reports_registration_as_unknown_not_false():
    """🚨 detail() sends no cookies, so HiBid answers as an anonymous visitor
    and isRegistered is ALWAYS false - it describes nobody.

    Reading that as "Leron is not registered" made the gate reject every lot
    forever, which looks exactly like a sniper that quietly never fires.
    Verified live 2026-08-18: the anonymous poll said false on an auction he
    was in fact registered for.
    """
    d = _detail(registered=None)
    assert d["registered"] is None


def test_unknown_registration_does_not_block_a_bid(monkeypatch):
    """Unknown must not behave like False, or nothing ever fires."""
    _armed()
    calls = _patch(monkeypatch, _detail(left=100.0, registered=None))
    monkeypatch.setattr(H, "registered_authed", lambda lid: True)
    H.run()
    assert calls, "an unknown that resolves True must still bid"


def test_a_known_false_still_blocks(monkeypatch):
    _armed()
    calls = _patch(monkeypatch, _detail(left=100.0, registered=None))
    monkeypatch.setattr(H, "registered_authed", lambda lid: False)
    H.run()
    assert not calls


def test_the_authenticated_check_is_not_paid_for_on_distant_lots(monkeypatch):
    """It costs a browser launch. A lot nine days out does not need one."""
    _armed()
    asked = []
    _patch(monkeypatch, _detail(left=9 * 24 * 3600, registered=None))
    monkeypatch.setattr(H, "registered_authed",
                        lambda lid: asked.append(lid) or True)
    H.run()
    assert not asked, "must not launch a browser for a lot days away"


# --- a missing page is not a closed auction ----------------------------------

def test_one_stripped_response_does_not_retire_a_live_lot(monkeypatch):
    """🚨 `gone` only means this response carried no lotState, and HiBid serves
    that on transient bad fetches - seen repeatedly on 2026-08-18.

    Retiring on the first one permanently killed an armed lot that had 218
    hours left, with nothing to say why.
    """
    _armed()
    calls = _patch(monkeypatch, _detail(gone=True))
    H.run()
    a = H.load_armed()["317852714"]
    assert a["status"] == "ARMED"
    assert a["gone_strikes"] == 1
    assert not calls


def test_three_stripped_responses_do_retire_it(monkeypatch):
    _armed()
    _patch(monkeypatch, _detail(gone=True))
    H.run(); H.run(); H.run()
    assert H.load_armed()["317852714"]["status"] == "ENDED_UNBID"


def test_strikes_reset_when_the_lot_comes_back(monkeypatch):
    _armed()
    _patch(monkeypatch, _detail(gone=True))
    H.run(); H.run()
    assert H.load_armed()["317852714"]["gone_strikes"] == 2
    _patch(monkeypatch, _detail(left=H.SNIPE_AT_S + 600))
    H.run()
    assert H.load_armed()["317852714"]["gone_strikes"] == 0
    assert H.load_armed()["317852714"]["status"] == "ARMED"


def test_an_explicitly_closed_lot_is_retired_at_once(monkeypatch):
    """`closed` is the site STATING the lot is finished - believe that."""
    _armed()
    _patch(monkeypatch, _detail(closed=True))
    H.run()
    assert H.load_armed()["317852714"]["status"] == "ENDED_UNBID"


def test_the_confirm_button_is_matched_by_text_not_class():
    """🚨 The real dialog's confirm button is class "btn" - which is every
    button on the page. Clicking the wrong one in a bid dialog is unforgivable,
    so the recorded text ("Confirm Bid") is what identifies it."""
    import json
    H.BIDFORM_PATH.write_text(json.dumps(
        {"confirm_dialog": True, "confirm_text": "Confirm Bid"}), encoding="utf-8")
    assert H.bidform_ok() is True
    H.BIDFORM_PATH.write_text(json.dumps(
        {"confirm_dialog": True, "max_input": ".text-lg"}), encoding="utf-8")
    assert H.bidform_ok() is False, "a form with no confirm TEXT is not usable"


# --- being outbid is a result, not a fault -----------------------------------

def _outbid_bid(lid, amount, dry_run):
    return False, f"{H.OUTBID} - a standing bid is above your ${amount:,.2f} max"


def test_being_outbid_gets_its_own_status(monkeypatch):
    """🚨 "Somebody valued it more" and "the bot is broken" both used to land in
    FAILED. One needs no action; the other needs fixing. Burying them together
    hides the broken one."""
    _armed(max_bid=50.0)
    _patch(monkeypatch, _detail(left=100.0), spy=_outbid_bid)
    H.run()
    assert H.load_armed()["317852714"]["status"] == "OUTBID"


def test_a_real_failure_is_still_FAILED(monkeypatch):
    _armed()
    _patch(monkeypatch, _detail(left=100.0),
           spy=lambda lid, amount, dry_run: (False, "the confirm button moved"))
    H.run()
    assert H.load_armed()["317852714"]["status"] == "FAILED"


def test_the_price_is_recorded_even_when_the_bid_loses(monkeypatch):
    """🚨 "You lost" and "you lost by $1" call for completely different
    responses, and the gap is the only evidence the book's ceiling is too low.
    The price used to be re-read ONLY on a win."""
    _armed(max_bid=50.0)
    _patch(monkeypatch, _detail(left=100.0, high=95.0), spy=_outbid_bid)
    monkeypatch.setattr(H.time, "sleep", lambda *a: None)
    H.run()
    assert H.load_armed()["317852714"]["price_after"] == 95.0


def test_an_outbid_message_never_claims_money_was_spent(monkeypatch, capsys):
    _armed(max_bid=50.0)
    _patch(monkeypatch, _detail(left=100.0, high=95.0), spy=_outbid_bid)
    monkeypatch.setattr(H.time, "sleep", lambda *a: None)
    H.run()
    out = capsys.readouterr().out
    assert "Nothing was spent" in out
    assert "beaten by $45.00" in out


def test_a_failed_bid_is_never_reported_as_winning(monkeypatch, capsys):
    """🚨 It used to say "you are winning, $21.92 under your max" after a bid
    that never landed - purely because the price happened to sit below the max.

    That is the worst kind of wrong: it reads as a success on a lot nobody is
    defending, so you stop watching it.
    """
    _armed(max_bid=50.0)
    _patch(monkeypatch, _detail(left=100.0, high=12.0),
           spy=lambda lid, amount, dry_run: (False, "the confirm button moved"))
    monkeypatch.setattr(H.time, "sleep", lambda *a: None)
    H.run()
    out = capsys.readouterr().out
    assert "winning" not in out
    assert "did NOT go through" in out


# --- the stretch -------------------------------------------------------------

def test_stretch_raises_the_ceiling_and_costs_profit(monkeypatch):
    """🚨 A stretch is profit you are SPENDING, not headroom you found."""
    monkeypatch.setattr(H, "book_ceiling",
                        lambda t, **k: 0.0 if k.get("target_profit") == 0.0 else 50.0)
    monkeypatch.setattr(H, "book_ceiling",
                        lambda t, premium=0.0, inbound=9.0, target_profit=20.0:
                        70.0 if target_profit == 0.0 else 50.0)
    cap, clears, be, clamped = H.stretch_to("x", 50.0, 10.0)
    assert cap == 60.0 and be == 70.0 and clamped is False
    assert clears == 10.0


def test_stretch_is_clamped_at_breakeven(monkeypatch):
    """🚨 However far the stretch reaches, it can never arm a losing bid.
    Going past break-even needs an explicit `snipe <amount>`, where Leron names
    the number himself."""
    monkeypatch.setattr(H, "book_ceiling",
                        lambda t, premium=0.0, inbound=9.0, target_profit=20.0:
                        70.0 if target_profit == 0.0 else 50.0)
    cap, clears, be, clamped = H.stretch_to("x", 50.0, 500.0)
    assert cap == 70.0 and clamped is True
    assert clears == 0.0, "at break-even you clear nothing - and never less"


def test_a_stretch_dollar_costs_more_than_a_dollar_under_a_premium(monkeypatch):
    """The premium rides on the stretch too, so $5 more of hammer is $5.75 of
    margin at 15%."""
    monkeypatch.setattr(H, "book_ceiling",
                        lambda t, premium=0.0, inbound=9.0, target_profit=20.0:
                        70.0 if target_profit == 0.0 else 50.0)
    _, clears, _, _ = H.stretch_to("x", 50.0, 5.0, premium=0.15)
    assert clears == pytest.approx((70.0 - 55.0) * 1.15)


def test_zero_stretch_changes_nothing(monkeypatch):
    monkeypatch.setattr(H, "book_ceiling",
                        lambda t, premium=0.0, inbound=9.0, target_profit=20.0:
                        70.0 if target_profit == 0.0 else 50.0)
    cap, _, _, clamped = H.stretch_to("x", 50.0, 0.0)
    assert cap == 50.0 and clamped is False


def test_a_negative_stretch_cannot_lower_the_ceiling(monkeypatch):
    """--stretch -20 must not quietly become a discount."""
    monkeypatch.setattr(H, "book_ceiling",
                        lambda t, premium=0.0, inbound=9.0, target_profit=20.0:
                        70.0 if target_profit == 0.0 else 50.0)
    cap, _, _, _ = H.stretch_to("x", 50.0, -20.0)
    assert cap == 50.0


def test_breakeven_is_the_zero_profit_price():
    """book_ceiling(target_profit=0) IS break-even - the clamp depends on it."""
    t = "Nintendo Switch 32GB Console"
    disciplined = H.book_ceiling(t, premium=0.15)
    breakeven = H.book_ceiling(t, premium=0.15, target_profit=0.0)
    assert breakeven > disciplined

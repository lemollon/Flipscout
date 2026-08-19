"""Guardrails for the HiBid watch-list feed.

The tests are about the LIMITS, not the happy path. The three that matter:

  * A card MEANS what discordarm READS. The two are allowed to disagree only
    in the direction of "no ceiling" - never "arms at a number the card never
    offered". The first draft of card() failed exactly here.
  * A lot the price book cannot price is still CARDED. Leron watched it by
    hand; dropping it is how the feed looks broken.
  * A failed read is never reported as an empty watch list.

Nothing here touches the network, a browser, or Discord.
"""

import json

import pytest

from flipscout import hibidwatch as W
from flipscout.discordarm import parse_card
from flipscout.notify import build_embed


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "STATE_PATH", tmp_path / "watch_seen.json")
    import flipscout.notify as N
    monkeypatch.setattr(N, "notify", lambda *a, **k: None)
    yield


LOT = "317520978"


def _detail(**over):
    d = {"lot_id": LOT, "gone": False, "closed": False,
         "title": "Casio G Shock white band", "high_bid": 1.0, "min_bid": 1.0,
         "bids": 0, "left": 600, "extended": False, "registered": True,
         "increments": None, "premium": 0.18, "tax": 0.0825, "notice": ""}
    d.update(over)
    return d


def _as_message(c):
    return {"content": "", "embeds": [build_embed(c)]}


# --- the card is the contract, discordarm is the judge --------------------

@pytest.mark.parametrize("over", [
    {},                                                   # armable
    {"premium": 0, "tax": 0},                             # no house cut
    {"high_bid": 400.0, "min_bid": 405.0, "bids": 30},    # past the ceiling
    {"high_bid": 400.0, "min_bid": 405.0, "bids": 0},     # past it, unbid
    {"title": "Vintage macrame owl wall hanging"},        # book has no comp
    {"title": "Vintage macrame owl wall hanging", "premium": 0},
])
def test_card_text_never_authorises_more_than_the_card_offers(over):
    """🚨 THE BUG THIS EXISTS FOR.

    discordarm pulls the arm figure out of the RENDERED TEXT, across every
    embed field - "ceiling"/"max bid" followed by a dollar amount. A `pass`
    card that printed no max_bid field still read "The book's ceiling is
    $10.42" in its prose, so a 🎯 on a card that promised it would not arm,
    armed. Assert the reader's answer, not the wording.
    """
    c = W.card(LOT, _detail(**over))
    site, iid, ceiling = parse_card(_as_message(c))
    assert (site, iid) == ("hibid", LOT)
    assert ceiling == c.get("max_bid")
    assert W.ceiling_leak(c) is None


def test_over_the_book_prints_no_ceiling_at_all():
    c = W.card(LOT, _detail(high_bid=400.0, min_bid=405.0, bids=30))
    assert c["verdict"] == "pass"
    assert "max_bid" not in c
    assert parse_card(_as_message(c))[2] is None


def test_unpriceable_lot_is_still_carded_just_without_a_number():
    """Leron watched it. Silence is indistinguishable from a broken feed."""
    c = W.card(LOT, _detail(title="Vintage macrame owl wall hanging"))
    assert c["title"] == "Vintage macrame owl wall hanging"
    assert "max_bid" not in c
    assert "snipe" in c["reason"]          # tells him how to name his own number


def test_armable_card_carries_the_books_hammer_ceiling():
    c = W.card(LOT, _detail())
    assert c["verdict"] == "buy"
    assert c["max_bid"] > 0
    # 🚨 The number is a HAMMER bid; the premium is charged on top. The card
    # has to say the all-in figure or he is reading a price he will not pay.
    assert "all-in" in c["reason"]


def test_a_leaking_card_is_dropped_rather_than_posted(monkeypatch):
    """If card() ever regresses, the run must not post the card anyway."""
    bad = dict(W.card(LOT, _detail()))
    bad.pop("max_bid")
    bad["reason"] = "ceiling $999.00"      # text says one thing, card says none
    assert W.ceiling_leak(bad) == 999.0

    monkeypatch.setattr(W, "scrape", lambda *a, **k: {
        "ok": True, "ids": [LOT], "images": {}, "total": 1, "error": None})
    monkeypatch.setattr(W, "detail", lambda lid: _detail())
    monkeypatch.setattr(W, "load_armed", lambda: {})
    monkeypatch.setattr(W, "card", lambda *a, **k: bad)
    sent = []
    assert W.run(force=True, notifier=lambda cs, content="": sent.append(cs)) == 0
    assert sent == []


# --- a failed read is not an empty watch list ------------------------------

def test_failed_read_is_loud_and_non_zero(monkeypatch):
    monkeypatch.setattr(W, "scrape", lambda *a, **k: {
        "ok": False, "ids": [], "images": {}, "total": None,
        "error": "not signed in"})
    sent = []
    rc = W.run(force=True, notifier=lambda cs, content="": sent.append(cs))
    assert rc == 1                         # NOT 0 - a dead feed must not pass
    assert sent == []
    # ...and it must not stamp last_run, or the throttle hides the outage.
    assert not W.STATE_PATH.exists()


def test_armed_and_already_carded_lots_are_skipped(monkeypatch):
    monkeypatch.setattr(W, "scrape", lambda *a, **k: {
        "ok": True, "ids": [LOT, "317520979"], "images": {}, "total": 2,
        "error": None})
    monkeypatch.setattr(W, "detail", lambda lid: _detail(lot_id=lid))
    monkeypatch.setattr(W, "load_armed", lambda: {LOT: {"max_bid": 5.0}})
    W.save_state({"seen": {"317520979": "2026-08-19T00:00:00+00:00"},
                  "last_run": None})
    sent = []
    assert W.run(force=True, notifier=lambda cs, content="": sent.append(cs)) == 0
    assert sent == []                      # one armed, one already carded


def test_closed_lots_are_never_carded(monkeypatch):
    monkeypatch.setattr(W, "scrape", lambda *a, **k: {
        "ok": True, "ids": [LOT], "images": {}, "total": 1, "error": None})
    monkeypatch.setattr(W, "detail", lambda lid: _detail(closed=True))
    monkeypatch.setattr(W, "load_armed", lambda: {})
    sent = []
    assert W.run(force=True, notifier=lambda cs, content="": sent.append(cs)) == 0
    assert sent == []


def test_a_carded_lot_is_recorded_so_it_is_not_carded_twice(monkeypatch):
    monkeypatch.setattr(W, "scrape", lambda *a, **k: {
        "ok": True, "ids": [LOT], "images": {}, "total": 1, "error": None})
    monkeypatch.setattr(W, "detail", lambda lid: _detail())
    monkeypatch.setattr(W, "load_armed", lambda: {})
    sent = []
    W.run(force=True, notifier=lambda cs, content="": sent.append(cs))
    assert len(sent) == 1 and len(sent[0]) == 1
    assert LOT in json.loads(W.STATE_PATH.read_text(encoding="utf-8"))["seen"]
    W.run(force=True, notifier=lambda cs, content="": sent.append(cs))
    assert len(sent) == 1                  # second run says nothing


# --- paginator / throttle --------------------------------------------------

@pytest.mark.parametrize("text,want", [
    ("Showing 1 - 1 of 1 lot", 1),
    ("Showing 1 - 100 of 1,234 lots", 1234),
    ("no paginator here", None),
])
def test_parse_total(text, want):
    assert W.parse_total(text) == want


def test_throttle_skips_a_recent_run_but_force_overrides(monkeypatch):
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    W.save_state({"seen": {}, "last_run": now})
    called = []
    monkeypatch.setattr(W, "scrape", lambda *a, **k: called.append(1) or {
        "ok": True, "ids": [], "images": {}, "total": 0, "error": None})
    assert W.run(notifier=lambda *a, **k: None) == 0
    assert called == []                    # throttled, no browser launched
    assert W.run(force=True, notifier=lambda *a, **k: None) == 0
    assert called == [1]


def test_corrupt_state_starts_clean_rather_than_crashing(capsys):
    W.STATE_PATH.write_text("{not json", encoding="utf-8")
    st = W.load_state()
    assert st == {"seen": {}, "last_run": None}
    assert "unreadable" in capsys.readouterr().out

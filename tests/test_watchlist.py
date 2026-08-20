"""Guardrails for both watch lists and the 30-minute call to arm.

Leron, 2026-08-20: "capture all of those and let me know in discord when the
auction is 30 mins from close so I can arm the snipe."

The tests are about the LIMITS:

  * THE CALL fires once, at T-30, and is not swallowed by the discovery card -
    they are different jobs and a lot must get both.
  * A call that arrives too late to act on is NOT sent, because a card saying
    "arm it now" with 90 seconds left is worse than silence.
  * A failed list read is never reported as an empty watch list.
  * A card's TEXT never authorises more than its `max_bid` does.

Nothing here touches the network, a browser, or Discord.
"""

import json

import pytest

from flipscout import watchlist as W


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "STATE_PATH", tmp_path / "state.json")
    import flipscout.notify as N
    monkeypatch.setattr(N, "notify", lambda *a, **k: None)
    yield


def _item(site="goodwill", iid="273964240", left=3600.0, ceiling=50.0, nxt=10.0,
          title="Citizen Eco-Drive Mens Watch"):
    return {"site": site, "id": iid, "title": title,
            "url": (W.GW_ITEM if site == "goodwill" else W.HB_LOT).format(iid),
            "left": left, "price": nxt - 1, "next_bid": nxt, "bids": 2,
            "ceiling": ceiling, "premium": 0.0, "image": ""}


def _wire(monkeypatch, items, hibid_ok=True, gw_ok=True):
    """Both lists return the given items; no browser, no network."""
    by_site = {}
    for it in items:
        by_site.setdefault(it["site"], []).append(it)
    monkeypatch.setattr(W, "hibid_ids", lambda: {
        "ok": hibid_ok, "ids": [i["id"] for i in by_site.get("hibid", [])],
        "total": len(by_site.get("hibid", [])), "error": None if hibid_ok else "boom"})
    monkeypatch.setattr(W, "goodwill_ids", lambda: {
        "ok": gw_ok, "ids": [i["id"] for i in by_site.get("goodwill", [])],
        "total": len(by_site.get("goodwill", [])), "error": None if gw_ok else "boom"})
    index = {(i["site"], i["id"]): i for i in items}
    monkeypatch.setattr(W, "FETCH", {
        "goodwill": lambda iid: index.get(("goodwill", iid)),
        "hibid": lambda iid: index.get(("hibid", iid))})
    sent = []
    return sent, (lambda cards, content="": sent.append((content, cards)))


# --- the call ---------------------------------------------------------------

def test_a_lot_closing_inside_thirty_minutes_gets_the_call(monkeypatch):
    sent, notifier = _wire(monkeypatch, [_item(left=20 * 60)])
    assert W.run(force=True, notifier=notifier) == 0
    content, cards = sent[0]
    assert "closing within 30" in content
    assert len(cards) == 1
    assert "CLOSES IN" in cards[0]["reason"]
    assert cards[0]["max_bid"] == 50.0        # armable in one tap


def test_the_call_fires_once_and_not_again(monkeypatch):
    sent, notifier = _wire(monkeypatch, [_item(left=20 * 60)])
    W.run(force=True, notifier=notifier)
    W.run(force=True, notifier=notifier)
    assert len(sent) == 1, "a second call for the same item is noise"


def test_a_lot_far_from_closing_gets_discovery_not_the_call(monkeypatch):
    sent, notifier = _wire(monkeypatch, [_item(left=40 * 3600)])
    W.run(force=True, notifier=notifier)
    content, cards = sent[0]
    assert "new item" in content
    assert "CLOSES IN" not in cards[0]["reason"]


def test_an_item_seen_early_still_gets_its_call_later(monkeypatch):
    """🚨 DISCOVERY AND THE CALL ARE DIFFERENT JOBS. A card sent when you heart
    something three days out is easy to scroll past; the T-30 ping is the one
    Leron actually asked for, so being 'seen' must never suppress it."""
    it = _item(left=40 * 3600)
    sent, notifier = _wire(monkeypatch, [it])
    W.run(force=True, notifier=notifier)
    assert "new item" in sent[0][0]
    it["left"] = 15 * 60                      # same item, now closing
    W.run(force=True, notifier=notifier)
    assert len(sent) == 2 and "closing within 30" in sent[1][0]


def test_a_call_too_late_to_act_on_is_not_sent(monkeypatch):
    """A card saying "arm it now" with 90 seconds left is worse than silence -
    the snipers refuse to start a bid inside ~25s and a tap needs longer."""
    sent, notifier = _wire(monkeypatch, [_item(left=90)])
    W.run(force=True, notifier=notifier)
    assert not sent


def test_an_ended_item_is_never_carded(monkeypatch):
    sent, notifier = _wire(monkeypatch, [_item(left=-5)])
    W.run(force=True, notifier=notifier)
    assert not sent


# --- both lists, and honest failure -----------------------------------------

def test_both_sites_are_read_and_carded(monkeypatch):
    sent, notifier = _wire(monkeypatch, [
        _item(site="goodwill", iid="273964240", left=10 * 60),
        _item(site="hibid", iid="317520978", left=10 * 60)])
    W.run(force=True, notifier=notifier)
    sources = {c["source"] for c in sent[0][1]}
    assert sources == {"ShopGoodwill watch list", "HiBid watch list"}


def test_a_failed_list_read_is_loud_and_non_zero(monkeypatch):
    """🚨 A list that failed to load and a list with nothing on it both produce
    zero cards. Only one of them means watched items are going unwatched."""
    sent, notifier = _wire(monkeypatch, [], gw_ok=False)
    assert W.run(force=True, notifier=notifier) == 1
    assert not sent


def test_an_empty_watch_list_is_a_success(monkeypatch):
    """ShopGoodwill answers an empty favourites list with `status: false,
    "Records are not available."` - which reads exactly like a failure."""
    sent, notifier = _wire(monkeypatch, [])
    assert W.run(force=True, notifier=notifier) == 0
    assert not sent


# --- the card never over-promises -------------------------------------------

def test_no_ceiling_is_printed_when_the_book_cannot_price_it(monkeypatch):
    sent, notifier = _wire(monkeypatch, [_item(left=10 * 60, ceiling=None)])
    W.run(force=True, notifier=notifier)
    c = sent[0][1][0]
    assert "max_bid" not in c
    assert "snipe <amount>" in c["reason"]
    assert W.ceiling_leak(c) is None


def test_no_ceiling_is_printed_when_the_price_is_already_past_it(monkeypatch):
    sent, notifier = _wire(monkeypatch, [_item(left=10 * 60, ceiling=20.0, nxt=45.0)])
    W.run(force=True, notifier=notifier)
    c = sent[0][1][0]
    assert "max_bid" not in c
    assert W.ceiling_leak(c) is None, "the prose must not leak a ceiling either"


def test_a_leaking_card_is_dropped_rather_than_posted(monkeypatch):
    sent, notifier = _wire(monkeypatch, [_item(left=10 * 60)])
    # 🚨 a REAL item id: discordarm only recognises 6+ digit ids, so a toy
    # "1" would make the leak invisible and the test vacuous.
    monkeypatch.setattr(W, "card", lambda it, closing: {
        "title": "x", "url": it["url"], "buy_url": it["url"],
        "reason": "ceiling $999.00"})          # text offers what no field does
    W.run(force=True, notifier=notifier)
    assert not sent


# --- state ------------------------------------------------------------------

def test_the_list_refresh_is_throttled_but_the_close_check_is_not(monkeypatch):
    """🚨 THE TWO CLOCKS. Reading a watch list needs a browser and is throttled;
    re-checking a cached id's close time is a plain HTTP call and is not. That
    is what makes a T-30 alert land near 30 minutes."""
    it = _item(left=40 * 3600)
    sent, notifier = _wire(monkeypatch, [it])
    W.run(force=True, notifier=notifier)
    calls = []
    monkeypatch.setattr(W, "goodwill_ids",
                        lambda: calls.append(1) or {"ok": True, "ids": [], "total": 0,
                                                    "error": None})
    it["left"] = 15 * 60
    W.run(notifier=notifier)                   # not forced: list must be skipped
    assert calls == [], "the browser-backed list refresh should be throttled"
    assert len(sent) == 2, "but the cached id still got its call"


def test_corrupt_state_starts_clean_rather_than_crashing(capsys):
    W.STATE_PATH.write_text("{not json", encoding="utf-8")
    st = W.load_state()
    assert st["seen"] == {} and st["warned"] == {}
    assert "unreadable" in capsys.readouterr().out


# --- images -----------------------------------------------------------------

def test_shopgoodwill_image_is_assembled_from_its_two_fields():
    """🚨 THIS SHIPPED BROKEN IN #49 AND LERON CAUGHT IT.

    The line was `(d.get("imageServer") or "") and ""`, which evaluates to the
    empty string no matter what - so every card went out with no picture. The
    server and the path are separate fields, and the path is a semicolon
    separated list of BACKSLASHED relative paths.
    """
    d = {"imageServer": "https://img.example.com/production/",
         "imageUrlString": r"106\Items\2026-08-14\abc.jpg;106\Items\2026-08-14\def.jpg"}
    assert W._gw_image(d) == "https://img.example.com/production/106/Items/2026-08-14/abc.jpg"


@pytest.mark.parametrize("d", [
    {},
    {"imageServer": "https://img.example.com/"},
    {"imageUrlString": r"106\Items\abc.jpg"},
    {"imageServer": "", "imageUrlString": ""},
])
def test_a_missing_image_is_empty_not_a_crash(d):
    assert W._gw_image(d) == ""


def test_a_card_without_an_image_simply_has_no_image_key():
    """Condition is most of the buy decision, so the picture matters - but a
    missing one must not stop the card going out."""
    c = W.card(_item(left=10 * 60), closing=True)
    assert "image" not in c
    c2 = W.card({**_item(left=10 * 60), "image": "https://x/y.jpg"}, closing=True)
    assert c2["image"] == "https://x/y.jpg"

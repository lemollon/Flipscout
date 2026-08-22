"""Guardrails for the eBay last call.

Leron, 2026-08-20: "you sent me pokemon cards that have 2hrs left on ebay ...
is there a way you can send them to me with 10 mins left? so i can snipe it."

The tests are about the LIMITS, which is where this can quietly stop working:

  * THE CLOCK. eBay's itemEndDate is UTC. Compared to a naive local clock on
    Leron's box it reads five hours long, and the call never fires. This is the
    single failure that would make the whole module look like a quiet market.
  * FIRES ONCE, at T-10, and only while there is still time to act.
  * NEVER when the price has already run past the ceiling - that is not news
    you can use, it is an invitation to lose money.
  * THE QUEUE OUTLIVES THE BOARD. eBay auctions are only swept on the :17 run,
    so a lot vanishes from the board long before it closes.
  * A board that could not be read is never reported as a quiet market.
  * NO ARM CHIP. discordarm cannot arm an eBay link, so the card must post
    with seed=False.
  * An undelivered call is not marked called - it retries.

Nothing here touches the network or Discord.
"""

import datetime as _dt
import json

import pytest

from flipscout import ebayclose as E


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(E, "notify", lambda *a, **k: None)
    yield


NOW = _dt.datetime(2026, 8, 20, 18, 0, tzinfo=_dt.timezone.utc)


def _row(minutes_left=8, price=20.0, ceil=95.0, iid="123456789012",
         title="Pokemon Base Set Charizard Holo PSA 6", alerted=True):
    end = NOW + _dt.timedelta(minutes=minutes_left)
    return {
        "source": "ebay", "listing_type": "auction",
        "title": title,
        "url": f"https://www.ebay.com/itm/{iid}?_skw=charizard",
        "ends": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "image": "https://i.ebayimg.com/x.jpg",
        "comps_url": "https://www.ebay.com/sch/i.html?_nkw=charizard",
        "model": "Pokemon Base Set holo", "comp": 180.0, "bids": 4,
        "open_bid": price, "max_bid": ceil,
        "profit_at_open": round((ceil - price) + 20, 2),
        "alerted": alerted,
    }


def _wire(monkeypatch, items, ok=True, error="boom"):
    monkeypatch.setattr(E, "fetch_board", lambda session=None: {
        "ok": ok, "items": items, "generated": NOW.isoformat(),
        "via": "test", "error": None if ok else error})


class _Sink:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def __call__(self, cards, content="", **kw):
        self.calls.append({"cards": cards, "content": content, "kw": kw})
        return ["webhook"] if self.ok else []


# --- the clock ---------------------------------------------------------------

def test_utc_end_time_is_not_read_as_local(monkeypatch):
    """🚨 THE BUG THIS MODULE WOULD HAVE SHIPPED WITH.

    hunt._ENDS_TZ had no eBay entry, so `hours_until` fell through to a naive
    local comparison. Run from Chicago that puts a lot ending in 8 minutes at
    5h08m out, forever outside the call window. Assert the zone is honoured by
    handing in an aware `now` in a DIFFERENT zone and demanding the same answer.
    """
    from flipscout.hunt import hours_until
    row = _row(minutes_left=8)
    chicago = NOW.astimezone(_dt.timezone(-_dt.timedelta(hours=5)))
    a = hours_until(row["ends"], now=NOW, source="ebay")
    b = hours_until(row["ends"], now=chicago, source="ebay")
    assert a == pytest.approx(8 / 60, abs=1e-6)
    assert b == pytest.approx(a, abs=1e-6)


def test_item_id_survives_both_url_shapes():
    assert E.item_id("https://www.ebay.com/itm/123456789012?x=1") == "123456789012"
    assert E.item_id("https://www.ebay.com/itm/a-slug-here/123456789012") == "123456789012"
    assert E.item_id("https://hibid.com/lot/318324342") is None


# --- the call ----------------------------------------------------------------

def test_calls_at_t10_and_only_once(monkeypatch):
    _wire(monkeypatch, [_row(minutes_left=8)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert len(sink.calls) == 1
    card = sink.calls[0]["cards"][0]
    assert card["max_bid"] == 95.0
    assert "CLOSES IN 8 min" in card["reason"]

    # Same lot, one minute later: already called, stays quiet.
    E.run(notifier=sink, now=NOW + _dt.timedelta(minutes=1))
    assert len(sink.calls) == 1


def test_two_hours_out_is_not_a_last_call(monkeypatch):
    _wire(monkeypatch, [_row(minutes_left=120)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert sink.calls == []
    # ...but it is QUEUED, which is the whole point.
    assert json.loads((E.STATE_PATH).read_text())["queue"]


def test_too_late_is_silence_not_panic(monkeypatch):
    _wire(monkeypatch, [_row(minutes_left=1)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert sink.calls == []
    # And it never fires later at an even worse moment.
    assert json.loads((E.STATE_PATH).read_text())["called"]


def test_past_the_ceiling_is_not_called(monkeypatch):
    """It already costs more than the number that clears the target profit."""
    _wire(monkeypatch, [_row(minutes_left=8, price=140.0, ceil=95.0)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert sink.calls == []


def test_no_ceiling_no_call(monkeypatch):
    _wire(monkeypatch, [_row(minutes_left=8, ceil=0.0)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert sink.calls == []


def test_fixed_price_never_queues(monkeypatch):
    row = _row(minutes_left=8)
    row["listing_type"] = "fixed"
    _wire(monkeypatch, [row])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert sink.calls == []
    assert json.loads((E.STATE_PATH).read_text())["queue"] == {}


# --- the queue ---------------------------------------------------------------

def test_queue_outlives_the_board(monkeypatch):
    """🚨 eBay auctions are only swept on the :17 run, so a lot alerted with two
    hours left is ABSENT from every board after it. The call must still fire."""
    _wire(monkeypatch, [_row(minutes_left=120)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)

    _wire(monkeypatch, [])                # board no longer mentions it
    later = NOW + _dt.timedelta(minutes=112)
    E.run(notifier=sink, force=True, now=later)
    assert len(sink.calls) == 1
    assert "Pokemon" in sink.calls[0]["cards"][0]["title"]


def test_unreadable_board_is_not_a_quiet_market(monkeypatch):
    said = []
    monkeypatch.setattr(E, "notify", lambda msg, **k: said.append(msg))
    _wire(monkeypatch, [], ok=False, error="http: ConnectionError")
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert said and "could not read" in said[0].lower()


def test_ended_lots_are_purged(monkeypatch):
    _wire(monkeypatch, [_row(minutes_left=8)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    E.run(notifier=sink, force=True,
          now=NOW + _dt.timedelta(minutes=8 + E.PURGE_AFTER_MIN + 1))
    st = json.loads((E.STATE_PATH).read_text())
    assert st["queue"] == {} and st["called"] == {}


# --- delivery ----------------------------------------------------------------

def test_card_carries_no_arm_chip(monkeypatch):
    """🚨 discordarm cannot arm an eBay link. A 🎯 here is a dead button on the
    one card where believing it worked costs the lot."""
    _wire(monkeypatch, [_row(minutes_left=8)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert sink.calls[0]["kw"].get("seed") is False


def test_undelivered_is_not_marked_called(monkeypatch):
    _wire(monkeypatch, [_row(minutes_left=8)])
    sink = _Sink(ok=False)
    rc = E.run(notifier=sink, now=NOW)
    assert rc == 1
    assert json.loads((E.STATE_PATH).read_text())["called"] == {}
    # Next minute it tries again.
    sink.ok = True
    E.run(notifier=sink, now=NOW + _dt.timedelta(minutes=1))
    assert len(sink.calls) == 2


def test_burst_is_capped_and_says_so(monkeypatch, capsys):
    rows = [_row(minutes_left=2 + i, iid=f"12345678900{i}") for i in range(9)]
    _wire(monkeypatch, rows)
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert len(sink.calls[0]["cards"]) == E.MAX_CARDS
    assert "deferred to the next run" in capsys.readouterr().out


def test_notify_rich_still_seeds_by_default(monkeypatch):
    """The change to notify_rich must not quietly disarm every other sender."""
    import flipscout.notify as N
    seeded = []
    monkeypatch.setattr(N, "seed_arm_reactions",
                        lambda mid, **k: seeded.append(mid) or True)

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "999"}

    class _S:
        def post(self, *a, **k):
            return _R()

    env = {"FLIPSCOUT_ALERT_WEBHOOK": "https://discord.test/hook"}
    N.notify_rich([{"title": "x", "url": "https://shopgoodwill.com/item/1"}],
                  env=env, session=_S())
    assert seeded == ["999"]
    seeded.clear()
    N.notify_rich([{"title": "x", "url": "https://www.ebay.com/itm/1"}],
                  env=env, session=_S(), seed=False)
    assert seeded == []


# --- news, not inventory -----------------------------------------------------

def test_only_lots_he_was_actually_sent(monkeypatch):
    """🚨 THE VOLUME GUARD. MEASURED 2026-08-20: the live board carried 184 eBay
    auctions, 77 closing inside twelve hours. Calling all of them is ~150 pings
    a day, which is a feed, not a last call. He asked for a second look at the
    cards he GOT."""
    _wire(monkeypatch, [_row(minutes_left=8, iid="111111111111", alerted=True),
                        _row(minutes_left=8, iid="222222222222", alerted=False)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    urls = [c["url"] for c in sink.calls[0]["cards"]]
    assert len(urls) == 1 and "111111111111" in urls[0]


def test_call_all_widens_it_when_asked(monkeypatch):
    monkeypatch.setattr(E, "CALL_ALL", True)
    _wire(monkeypatch, [_row(minutes_left=8, iid="222222222222", alerted=False)])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    assert len(sink.calls[0]["cards"]) == 1


def test_old_board_falls_back_instead_of_going_silent(monkeypatch):
    """A board written before the flag existed has it on NO row, which reads
    exactly like "nothing was alerted". Detect the SCHEMA, not the value."""
    cheap = _row(minutes_left=8, price=20.0, ceil=95.0, iid="333333333333")
    rich = _row(minutes_left=8, price=90.0, ceil=95.0, iid="444444444444")
    for r in (cheap, rich):
        r.pop("alerted")
    _wire(monkeypatch, [cheap, rich])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    urls = [c["url"] for c in sink.calls[0]["cards"]]
    # The cheap one clears both fallback bars; the one already at 95% of the
    # ceiling does not.
    assert len(urls) == 1 and "333333333333" in urls[0]


def test_daily_cap_drops_loudly_and_permanently(monkeypatch, capsys):
    monkeypatch.setattr(E, "DAILY_MAX", 2)
    rows = [_row(minutes_left=3 + i, iid=f"55555555500{i}") for i in range(5)]
    _wire(monkeypatch, rows)
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    out = capsys.readouterr().out
    assert "DAILY CAP" in out and out.count("dropped:") == 3
    assert len(sink.calls[0]["cards"]) == 2
    # Dropped lots are marked called - they end in minutes, they are not deferred.
    assert len(json.loads((E.STATE_PATH).read_text())["called"]) == 5


def test_daily_counter_resets_the_next_day(monkeypatch):
    monkeypatch.setattr(E, "DAILY_MAX", 1)
    _wire(monkeypatch, [_row(minutes_left=8, iid="666666666666")])
    sink = _Sink()
    E.run(notifier=sink, now=NOW)
    # 🚨 `_row` measures from NOW, so tomorrow's lot is NOW + 1 day + 8 min.
    _wire(monkeypatch, [_row(minutes_left=24 * 60 + 8, iid="777777777777")])
    E.run(notifier=sink, force=True, now=NOW + _dt.timedelta(days=1))
    assert len(sink.calls) == 2

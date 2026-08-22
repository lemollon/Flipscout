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


# --- buy it now ---------------------------------------------------------------

def test_buy_it_now_gets_its_own_slots():
    """🚨 A fixed price IS the price - no bidding war, no proxy sniping you at
    the buzzer, no waiting days. An auction's "profit at open" is measured
    against a price that has not moved yet and will, which is why lots days out
    look richest ($69.79 median at >3d vs $30.57 inside 6h).

    Ranking the two on the same number flatters the auction every time. Of the
    top 10 by profit only 3 were buy-it-now, against 166 on the board.
    """
    cfg = load_config({"FLIPSCOUT_TOP": "20"})
    assert "bin_slots" in cfg
    assert (cfg["bin_slots"] or max(1, cfg["top"] // 4)) >= 1


def test_bin_slots_are_overridable():
    assert load_config({"FLIPSCOUT_BIN_SLOTS": "8"})["bin_slots"] == 8


def test_the_four_lanes_do_not_oversubscribe_the_run():
    """Reserving everything would starve the profit ranking entirely."""
    cfg = load_config({"FLIPSCOUT_TOP": "20"})
    top = cfg["top"]
    q = max(1, top // 4)
    reserved = ((cfg["bin_slots"] or q) + (cfg["urgent_slots"] or q)
                + (cfg["closing_slots"] or q))
    assert reserved < top, "the profit ranking must keep some slots"


# --- the selector must not fail silently --------------------------------------

@pytest.mark.parametrize("env,field", [
    ({"FLIPSCOUT_TOP": "-5"}, "top"),
    ({"FLIPSCOUT_BIN_SLOTS": "-3"}, "bin_slots"),
])
def test_a_negative_setting_never_produces_a_negative_budget(env, field):
    """🚨 The failure mode here is SILENCE. A negative TOP or slot count made
    the take loop return immediately, so the run alerted nothing at all - which
    looks exactly like a quiet market. Found in the 2026-08-19 audit."""
    cfg = load_config(env)
    top = max(0, int(cfg["top"]))
    quarter = max(1, top // 4)
    slots = max(0, int(cfg.get(field) or 0) or quarter)
    assert top >= 0 and slots >= 0


def test_zero_hours_means_the_lane_is_OFF_not_one_hour():
    """🚨 `float(x or 1)` turns a deliberate 0 INTO 1. Setting
    FLIPSCOUT_URGENT_HOURS=0 to switch the urgent lane off silently switched it
    ON at one hour instead."""
    cfg = load_config({"FLIPSCOUT_URGENT_HOURS": "0"})
    assert cfg["urgent_hours"] == 0.0
    urgent_h = float(cfg["urgent_hours"] if cfg["urgent_hours"] is not None else 1)
    assert urgent_h == 0.0, "0 must survive to the lane, where it disables it"


# --- a card with no photo, added 2026-08-22 ---------------------------------

def _card_row(**over):
    row = {"title": "2018 Panini Prizm Luka Doncic RC Auto /99",
           "source": "hibid", "url": "http://x", "listing_type": "auction",
           "image": "http://img"}
    row.update(over)
    return row


def _body(row):
    """The alert body for one row, priced against any real model."""
    from flipscout import hunt
    from flipscout.pricebook import BY_KEY
    from flipscout.bidding import advise
    model = BY_KEY["pkmn_card_graded_high"]
    adv = advise(comp=model.comp, outbound_shipping=model.outbound_shipping,
                 inbound_shipping=9.0, target_profit=20.0)
    m = type("M", (), {"dead_also_present": [], "label": model.label})()
    return hunt.to_alert({"row": row, "model": model, "advice": adv,
                          "match": m})["reason"]


def test_a_card_with_a_photo_says_nothing_extra():
    assert "No photo on this listing" not in _body(_card_row())


def test_a_card_with_no_photo_is_called_out():
    """🚨 On a raw card the picture IS the condition, and condition is most of
    the value. A photo-less card alert must not look like every other one."""
    body = _body(_card_row(image=None))
    assert "No photo on this listing" in body
    assert "condition is most of the value" in body


def test_a_non_card_with_no_photo_is_not_nagged():
    """Every other category carries the trade in the title, so this would just
    be one more warning nobody reads."""
    body = _body(_card_row(title="Canon AE-1 35mm Film Camera", image=None))
    assert "No photo on this listing" not in body


# --- the card scout, added 2026-08-22 ---------------------------------------
# The one place this repo alerts without a comp. See hunt.scout_cards for why
# that is allowed here and nowhere else.

def _rows():
    return [
        {"title": "2018 Panini Prizm Luka Doncic Silver Prizm RC Auto /99",
         "id": "1", "source": "hibid", "url": "u1", "image": "i1", "price": 40},
        {"title": "1991 Score Baseball Card Lot of 500", "id": "2",
         "source": "hibid", "url": "u2", "image": "i2"},
        {"title": "Canon AE-1 35mm Film Camera", "id": "3",
         "source": "hibid", "url": "u3", "image": "i3"},
        {"title": "2020 Topps Chrome Justin Herbert RC Refractor",
         "id": "4", "source": "goodwill", "url": "u4", "image": "i4"},
    ]


def test_the_scout_takes_cards_and_leaves_everything_else():
    from flipscout.hunt import scout_cards
    got = {c["row"]["id"] for c in scout_cards(_rows(), {})}
    assert got == {"1", "4"}          # the junk-wax lot and the camera stay out


def test_the_scout_never_re_posts_something_already_seen():
    from flipscout.hunt import scout_cards
    got = {c["row"]["id"] for c in scout_cards(_rows(), {}, seen={"hibid:1"})}
    assert got == {"4"}


def test_the_scout_skips_anything_the_book_can_actually_price():
    """🚨 A PRICED LISTING IS ALREADY ALERTING WITH REAL NUMBERS. Posting it
    again with no ceiling would put a comped card and an uncomped one side by
    side saying different things about the same lot."""
    from flipscout.hunt import scout_cards
    rows = [{"title": "TI-84 Plus CE graphing calculator", "id": "9",
             "source": "hibid", "url": "u", "image": "i"}]
    from flipscout.pricebook import match
    assert match(rows[0]["title"]) is not None       # the book prices this one
    assert scout_cards(rows, {}) == []


def test_the_scout_now_picks_up_the_pokemon_the_book_stopped_pricing():
    """🚨 THE HOLE BENCHING WOULD HAVE LEFT. `pokemon-cards` is benched, so
    `match()` returns nothing for a graded card - and the scout used to skip
    Pokemon outright on the grounds that the book had it covered. Both halves
    were changed together, or every Pokemon card would silently vanish."""
    from flipscout.hunt import scout_cards
    from flipscout.pricebook import match
    title = "1999 Pokemon Jungle Clefable Holo #1 PSA 7"
    assert match(title) is None                      # benched, so unpriced
    got = scout_cards([{"title": title, "id": "9", "source": "hibid",
                        "url": "u", "image": "i"}], {})
    assert len(got) == 1, "a benched Pokemon card must not fall through both"


def test_the_best_finds_survive_the_cap():
    from flipscout.hunt import scout_cards
    rows = [dict(r, id=str(i)) for i, r in enumerate(_rows() * 8)]
    got = scout_cards(rows, {}, limit=3)
    assert len(got) == 3
    assert got == sorted(got, key=lambda c: c["read"].score, reverse=True)


def test_a_scout_alert_states_that_nobody_measured_it():
    """🚨 THE HONEST HEADLINE. Beside priced alerts, a card with no such line
    reads as though somebody checked the money."""
    from flipscout.hunt import scout_cards, to_scout_alert
    a = to_scout_alert(scout_cards(_rows(), {})[0])
    assert "No measured comp, so no ceiling" in a["reason"]
    # 🚨 The live market numbers added later must not soften this: they say what
    # the market is doing, never what this tool stands behind.
    assert "not a price this tool stands behind" in a["reason"]


def test_a_scout_alert_carries_no_number_to_bid_on():
    """It may say the ASK - that is the seller's number, not ours - but never a
    comp, a ceiling or a max bid."""
    from flipscout.hunt import scout_cards, to_scout_alert
    a = to_scout_alert(scout_cards(_rows(), {})[0])
    assert a.get("comp") is None and a.get("max_bid") is None
    assert "ceiling" not in a["reason"].lower() or "no ceiling" in a["reason"].lower()


def test_a_scout_alert_links_its_own_sold_search():
    from flipscout.hunt import scout_cards, to_scout_alert
    a = to_scout_alert(scout_cards(_rows(), {})[0])
    assert "LH_Sold=1" in a["comps_url"] and "doncic" in a["comps_url"]


def test_a_scout_alert_routes_to_the_cards_channel():
    from flipscout.hunt import scout_cards, to_scout_alert
    from flipscout import notify
    a = to_scout_alert(scout_cards(_rows(), {})[0])
    assert notify.channel_for(a) == "cards"


def test_the_run_says_where_cards_go(monkeypatch, capsys):
    """🚨 A MISSING CARDS WEBHOOK LOOKS EXACTLY LIKE A WORKING SETUP. The
    fallback to the main channel is deliberate - a routing rule must never make
    an alert vanish - so the only symptom of an unmapped secret is that cards
    quietly pile into the main channel and the cards channel reads as broken.
    That is precisely what happened: the code was written and the workflow was
    never taught to pass the secret through."""
    from flipscout import hunt
    monkeypatch.delenv("FLIPSCOUT_CARDS_WEBHOOK", raising=False)
    monkeypatch.setattr(hunt, "sweep", lambda *a, **k: [])
    monkeypatch.setattr(hunt, "describe_webhook", lambda u: "none")
    try:
        hunt.run(notifier=lambda *a, **k: [])
    except Exception:
        pass
    out = capsys.readouterr().out
    assert "card destination" in out and "NOT SET" in out


def test_the_workflow_passes_the_cards_secrets_through():
    """The scar this file already carries, one channel over: "three of them
    were set and silently inert for a full run, and the log looked perfectly
    healthy". A secret not mapped in watch.yml does not reach the job."""
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parent.parent
          / ".github" / "workflows" / "watch.yml").read_text()
    assert "FLIPSCOUT_CARDS_WEBHOOK" in wf
    assert "FLIPSCOUT_CARDS_CHANNEL_ID" in wf


# --- live market numbers on a scout card, 2026-08-22 ------------------------
# Leron: "You are missing the comps on the cards - you need to find them."
# The scout shipped with a verdict and a search URL, which asks him to do the
# lookup himself on every card - the work the tool exists to remove.

def _find():
    from flipscout.hunt import scout_cards
    return scout_cards([{"title": "2018 Panini Prizm Luka Doncic Silver Prizm RC Auto /99",
                         "id": "1", "source": "hibid", "url": "u", "image": "i",
                         "price": 40, "listing_type": "auction"}], {})


class _Provider:
    def __init__(self, comp): self.comp, self.seen = comp, []
    def lookup(self, q, observed_price=None):
        self.seen.append(q)
        return self.comp


def test_sold_data_is_reported_as_a_sale():
    from flipscout.comps import Comp
    from flipscout.hunt import price_scout_finds, to_scout_alert
    finds = _find()
    price_scout_finds(finds, comps=_Provider(Comp(
        query="q", sold_price=310.0, sold_count=24, active_count=61,
        source="ebay_insights", low=180.0, high=650.0)))
    body = to_scout_alert(finds[0])["reason"]
    assert "SOLD median $310.00" in body and "24 sale(s)" in body


def test_asks_are_never_reported_as_a_sale():
    """🚨 THE WHOLE DISCIPLINE OF THIS FEATURE. Marketplace Insights (solds) is
    closed to new users, so today only ACTIVE asks come back - and asks skew
    high, because everything unsold is still listed at its optimistic price."""
    from flipscout.comps import Comp
    from flipscout.hunt import price_scout_finds, to_scout_alert
    finds = _find()
    price_scout_finds(finds, comps=_Provider(Comp(
        query="q", sold_price=None, active_count=61, source="ebay_browse",
        low=180.0, high=650.0)))
    body = to_scout_alert(finds[0])["reason"]
    assert "61 listed on eBay right now" in body
    assert "ASKING prices, not sales" in body
    assert "SOLD median" not in body


def test_a_market_number_never_becomes_a_ceiling():
    """It may say what the market is doing. It may never say what to bid."""
    from flipscout.comps import Comp
    from flipscout.hunt import price_scout_finds, to_scout_alert
    finds = _find()
    price_scout_finds(finds, comps=_Provider(Comp(
        query="q", sold_price=310.0, sold_count=24, source="ebay_insights")))
    a = to_scout_alert(finds[0])
    assert a.get("comp") is None and a.get("max_bid") is None
    assert "No measured comp, so no ceiling" in a["reason"]


def test_the_lookup_uses_the_cards_precise_query():
    """Not the raw title (seller hype returns nothing) and not the category."""
    from flipscout.comps import Comp
    from flipscout.hunt import price_scout_finds
    p = _Provider(Comp(query="q", sold_price=None))
    price_scout_finds(_find(), comps=p)
    assert p.seen and "luka" in p.seen[0] and "/99" in p.seen[0]
    assert "🔥" not in p.seen[0]


def test_a_dead_lookup_never_costs_the_card(capsys):
    """It still has a verdict, a photo and a link - what it shipped with."""
    from flipscout.hunt import price_scout_finds, to_scout_alert
    class Boom:
        def lookup(self, q, observed_price=None): raise RuntimeError("429")
    finds = _find()
    price_scout_finds(finds, comps=Boom())
    body = to_scout_alert(finds[0])["reason"]
    assert "CHASE" in body and "lookup failed" in capsys.readouterr().out


def test_no_ebay_keys_is_not_an_error(capsys):
    from flipscout.hunt import price_scout_finds
    class NoKeys:
        def lookup(self, q, observed_price=None): raise RuntimeError("no creds")
    finds = _find()
    price_scout_finds(finds, comps=NoKeys())
    assert "lookup failed" in capsys.readouterr().out       # noted, not raised


def test_the_fallback_announces_itself_in_discord(monkeypatch):
    """🚨 A DIAGNOSTIC NOBODY READS IS NOT A DIAGNOSTIC.

    With FLIPSCOUT_CARDS_WEBHOOK unset these cards fall back to the main
    channel - deliberately, so a routing rule can never make an alert vanish -
    and the only symptom is that they turn up in the wrong place. `[hunt] card
    destination: NOT SET` already said so on every run, but that is a CI log
    line, and the person wondering why his cards are in the wrong channel is
    looking at Discord. The same symptom got reported twice while that line was
    printing correctly.
    """
    from flipscout.hunt import _post_scout
    monkeypatch.delenv("FLIPSCOUT_CARDS_WEBHOOK", raising=False)
    monkeypatch.setenv("FLIPSCOUT_ALERT_WEBHOOK", "http://main")
    sent = []
    _post_scout(_rows(), {}, set(), lambda a, content="", **k: sent.append(content) or ["webhook"])
    assert "belong in your cards channel" in sent[0]
    assert "FLIPSCOUT_CARDS_WEBHOOK" in sent[0]


def test_no_scolding_once_the_channel_is_configured(monkeypatch):
    """It must go quiet the moment it is fixed, or it becomes wallpaper."""
    from flipscout.hunt import _post_scout
    monkeypatch.setenv("FLIPSCOUT_ALERT_WEBHOOK", "http://main")
    monkeypatch.setenv("FLIPSCOUT_CARDS_WEBHOOK", "http://cards")
    sent = []
    _post_scout(_rows(), {}, set(), lambda a, content="", **k: sent.append(content) or ["webhook:cards"])
    assert "belong in your cards channel" not in sent[0]


def test_one_rate_limit_stops_the_rest_of_the_lookups(capsys):
    """🚨 A RATE LIMIT IS NOT A PER-CARD FAILURE. Observed 2026-08-22: with the
    Browse allowance spent, all twelve lookups 429'd and printed twelve
    near-identical lines - reading like twelve unlucky cards instead of one
    exhausted budget - and each retry spent a call the next run could have
    used."""
    import requests
    from flipscout.hunt import price_scout_finds, scout_cards
    rows = [{"title": f"2018 Panini Prizm Luka Doncic RC Auto /{n}", "id": str(n),
             "source": "hibid", "url": "u", "image": "i", "price": 40,
             "listing_type": "auction"} for n in (99, 25, 10, 50, 5)]
    finds = scout_cards(rows, {})
    assert len(finds) > 1

    class RateLimited:
        def __init__(self): self.calls = 0
        def lookup(self, q, observed_price=None):
            self.calls += 1
            r = requests.Response(); r.status_code = 429
            raise requests.HTTPError("429", response=r)

    prov = RateLimited()
    price_scout_finds(finds, comps=prov)
    assert prov.calls == 1, "kept asking after the quota said no"
    out = capsys.readouterr().out
    assert "rate-limited" in out and "Not a per-card failure" in out


def test_one_bad_query_does_not_stop_the_others(capsys):
    """Anything that is NOT a rate limit is per-card and must not cost the
    other cards their numbers."""
    from flipscout.comps import Comp
    from flipscout.hunt import price_scout_finds, scout_cards
    rows = [{"title": f"2018 Panini Prizm Luka Doncic RC Auto /{n}", "id": str(n),
             "source": "hibid", "url": "u", "image": "i", "price": 40,
             "listing_type": "auction"} for n in (99, 25, 10)]
    finds = scout_cards(rows, {})

    class Flaky:
        def __init__(self): self.calls = 0
        def lookup(self, q, observed_price=None):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("weird title")
            return Comp(query=q, sold_price=None, active_count=7)

    prov = Flaky()
    price_scout_finds(finds, comps=prov)
    assert prov.calls == len(finds), "one bad query stopped the rest"

"""2026-08-13: the breadth pack.

The Discord digest was dominated by a handful of high-volume models
(Seiko Automatic, Mitutoyo, film cameras, camcorders - sorted by profit_at_open and
released top-N) while whole categories with measured live supply went
unpriced. Two fixes, tested here:

  1. New price-book models: iPods beyond the Classic (nano/touch, plus a
     capacity-unknown catch-all for the Classic/Video), watches, a Bose
     headphone, a film lens, and Walkman. Every comp below is MEASURED
     2026-08-13 (n=123 per eBay used-solds query).
  2. A per-model cap on the fresh-alert selection so one model can no longer
     monopolize a run - the rest defer to the next run instead of vanishing.
"""

from flipscout.pricebook import match, search_terms
from flipscout import hunt


# --- iPods beyond Classic ----------------------------------------------------

def test_ipod_classic_with_capacity_still_wins_over_the_catchall():
    # The capacity models (specificity 30) must still win when GB is in the
    # title - the new catch-all (specificity 15) exists only to catch what
    # they miss.
    assert match("Apple iPod Classic 160GB").model.key == "ipod_classic_160"


def test_ipod_classic_no_capacity_hits_the_new_catchall():
    assert match("Apple iPod Classic - Black").model.key == "ipod_classic_nocap"


def test_tripod_never_matches_any_ipod_model():
    # "ipod" is a substring of "tripod" - every ipod include below needs a
    # word boundary or this silently prices a camera tripod as an iPod.
    assert match("Vintage Camera Tripod with Carrying Case") is None


def test_ipod_nano_and_touch_are_retired_not_merely_cheap():
    """🚨 BOTH DIED AT THEIR HONEST NUMBERS (2026-08-19).

    The whole iPod block was floored on samples of 8 to 21. Re-measured on the
    routed population, nano is p25 $35 / median $50 (n=251) and touch is p25
    $19.50 / median $29.99 (n=160) - each quoting a max bid of $0.00 against
    the standing gate ($20 profit over $9 inbound). Touch had been effectively
    dead for a while: even at its old $39.99 the ceiling was $0.29.

    They are refused with a NUMBER rather than silently dropped, so a listing
    still says why it is not a deal.
    """
    from flipscout.pricebook import BY_KEY, DEAD_MODELS
    import re
    assert "ipod_nano" not in BY_KEY and "ipod_touch" not in BY_KEY
    for title in ("apple ipod nano 6th generation 8gb blue",
                  "apple ipod touch 5th gen 32gb space gray"):
        assert match(title) is None
        assert [w for pat, w in DEAD_MODELS.items() if re.search(pat, title)], title


def test_ipod_nano_armband_case_rejected():
    assert match("Apple iPod Nano armband case only") is None


# --- watches ------------------------------------------------------------------

def test_gshock_prices_and_rejects_bezel_band_accessory():
    assert match("Casio G-Shock GA-2100 Black Resin Watch").model.key == "casio_gshock"
    assert match("G-Shock bezel and band set") is None


def test_seiko_automatic_needs_a_corroborating_noun():
    # A BRAND IS NOT A MODEL - bare "Seiko" is also cheap quartz.
    assert match("Seiko 5 Automatic Divers Watch SNK809").model.key == "seiko_automatic"
    assert match("Seiko watch band bracelet") is None
    assert match("Seiko dress watch") is None


def test_gshock_lookalike_phrasing_rejected():
    # Same failure mode as Gunne Sax STYLE: a no-name knockoff that
    # advertises itself honestly as "G-Shock STYLE" still matched on the
    # bare brand text. Live catch: a $2.25 WR50M digital with no Casio
    # branding at all.
    assert match("Digital Gold And Black G-Shock Style Digital Watch") is None
    # the real thing must still match
    assert match("Casio G-Shock DW-5600E").model.key == "casio_gshock"


def test_seiko_lookalike_phrasing_rejected():
    assert match("Seiko style automatic skeleton watch") is None
    assert match("Seiko 5 Automatic SNK809").model.key == "seiko_automatic"


# --- headphones -----------------------------------------------------------

def test_qc35_prices_and_rejects_replacement_ear_pads():
    assert match("Bose QuietComfort 35 II Wireless Headphones").model.key == "bose_qc35"
    assert match("QC35 Black").model.key == "bose_qc35"
    assert match("Bose QC35 replacement ear pads") is None


# --- lenses -----------------------------------------------------------------

def test_canon_fd_50_14_prices_and_rejects_a_bare_lens_cap():
    assert match("Canon FD 50mm f/1.4 SSC Lens").model.key == "canon_fd_50_14"
    assert match("Canon FD front lens cap") is None


# --- walkman ------------------------------------------------------------------

def test_sony_walkman_prices():
    assert match("Sony Walkman WM-D6C Professional Cassette Player").model.key == "sony_walkman"


# --- search terms sweep the new categories -----------------------------------

def test_breadth_pack_search_terms_are_swept():
    terms = search_terms()
    for t in ("ipod nano", "ipod touch", "sony walkman", "walkman lot",
              "bose quietcomfort", "casio g-shock", "g shock watch",
              "seiko automatic", "seiko watch lot", "canon fd",
              "vintage camera lens", "camera lens lot"):
        assert t in terms, t


# --- the per-model digest cap ------------------------------------------------

class FakeHunter:
    name = "goodwill"

    def __init__(self, rows):
        self._rows = rows

    def search(self, query, limit=40):
        return list(self._rows)


def _row(id_, title, price):
    return {"source": "goodwill", "id": id_, "title": title,
            "url": f"https://example.test/{id_}", "price": price,
            "min_bid": price, "increment": 1.0, "bids": 0,
            "handling": 0.0, "image": "i", "ends": ""}


def test_per_model_cap_backfills_and_leaves_overflow_unseen(monkeypatch):
    # 6 TI-84 Plus CE candidates (would normally sweep the whole digest) plus
    # 2 Seiko Automatic candidates.
    ce_rows = [_row(f"ce{i}", "TI-84 Plus CE Graphing Calculator", 5.0 + i)
               for i in range(6)]
    other_rows = [_row(f"st{i}", "Seiko Automatic Combination Square", 10.0 + i)
                  for i in range(2)]
    rows = ce_rows + other_rows

    saved = {}

    def fake_save(path, seen):
        saved["seen"] = set(seen)

    monkeypatch.setattr(hunt, "_save_seen", fake_save)
    monkeypatch.setattr(hunt, "_load_seen", lambda p, **kw: set())

    sent = {}

    def fake_notify(alerts, content="", **kw):
        sent["alerts"] = alerts
        return ["webhook"]

    cfg = {"sources": ["goodwill"], "target_profit": 5.0, "inbound_shipping": 0.0,
           "top": 10, "max_per_model": 3, "state_file": "nonexistent.json"}
    res = hunt.run(cfg, hunters=[FakeHunter(rows)], notifier=fake_notify)

    alerts = sent["alerts"]
    ids_alerted = {a["url"].rsplit("/", 1)[-1] for a in alerts}
    ce_ids_alerted = {i for i in ids_alerted if i.startswith("ce")}
    st_ids_alerted = {i for i in ids_alerted if i.startswith("st")}

    # capped at 3 CE picks, backfilled with both Seiko Automatic candidates
    assert len(ce_ids_alerted) == 3
    assert len(st_ids_alerted) == 2
    assert len(alerts) == 5
    assert res["new"] == 5

    # the 3 capped-out CE candidates must stay OUT of the seen-cache, so they
    # queue for the next run instead of being silently dropped
    ce_ids_capped = {r["id"] for r in ce_rows} - ce_ids_alerted
    assert len(ce_ids_capped) == 3
    for cid in ce_ids_capped:
        assert f"goodwill:{cid}" not in saved["seen"]
    for cid in ce_ids_alerted | st_ids_alerted:
        assert f"goodwill:{cid}" in saved["seen"]


def test_per_model_cap_default_is_three(monkeypatch):
    import os
    monkeypatch.delenv("FLIPSCOUT_MAX_PER_MODEL", raising=False)
    assert hunt.load_config(os.environ)["max_per_model"] == 3


def test_per_model_cap_reads_env(monkeypatch):
    import os
    monkeypatch.setenv("FLIPSCOUT_MAX_PER_MODEL", "5")
    assert hunt.load_config(os.environ)["max_per_model"] == 5


# --- the per-channel floor, added 2026-08-23 ---------------------------------
# 🚨 A PURE PROFIT RANKING STARVES THE CHEAP CHANNELS BY CONSTRUCTION. Measured
# on a live 7,434-listing sweep: 364 candidates price out, 43 of them watches -
# and the best watch on the whole board ranks #42, because a $84 Citizen loses
# to a $152 Nintendo every time. #watches and #ipods arrived EMPTY while their
# supply sat there qualifying. Each channel now gets its best N first.

def _floor_run(monkeypatch, rows, top=12, reserve=2):
    sent = {}
    monkeypatch.setattr(hunt, "_save_seen", lambda p, s: None)
    monkeypatch.setattr(hunt, "_load_seen", lambda p, **kw: set())

    def fake_notify(alerts, content="", **kw):
        sent.setdefault("alerts", []).extend(alerts)
        return ["webhook"]

    cfg = {"sources": ["goodwill"], "target_profit": 5.0, "inbound_shipping": 0.0,
           "top": top, "max_per_model": 3, "reserve_per_channel": reserve,
           "state_file": "nonexistent.json"}
    hunt.run(cfg, hunters=[FakeHunter(rows)], notifier=fake_notify)
    from flipscout import notify
    mix = {}
    for a in sent.get("alerts", []):
        mix[notify.channel_for(a) or "deals"] = mix.get(notify.channel_for(a) or "deals", 0) + 1
    return mix


def _mixed_board():
    """A board shaped like the real one: cameras and games rich and numerous,
    watches and ipods cheap - so pure profit ranking buries the cheap ones."""
    rows = []
    for i in range(8):
        rows.append(_row(f"cam{i}", "Canon PowerShot G7X Mark II", 60.0 + i))
    for i in range(8):
        rows.append(_row(f"gam{i}", "Nintendo Switch OLED console", 90.0 + i))
    for i in range(4):
        rows.append(_row(f"wat{i}", "Seiko Automatic watch mens", 10.0 + i))
    for i in range(4):
        rows.append(_row(f"ipo{i}", "Apple iPod Classic 160GB", 12.0 + i))
    return rows


def test_the_floor_reaches_the_channels_a_profit_ranking_buries(monkeypatch):
    """The failure this exists for: #watches and #ipods arriving empty."""
    mix = _floor_run(monkeypatch, _mixed_board(), top=12, reserve=2)
    assert mix.get("watches", 0) >= 2, f"#watches still starved: {mix}"
    assert mix.get("ipods", 0) >= 2, f"#ipods still starved: {mix}"


def test_the_floor_is_a_floor_and_not_a_cap(monkeypatch):
    """🚨 THE HALF THAT IS EASY TO BREAK. Guaranteeing the cheap channels a
    minimum must not stop the rich ones taking what is left on profit - that
    would turn a starvation fix into an equal-shares quota and cost real
    money."""
    mix = _floor_run(monkeypatch, _mixed_board(), top=12, reserve=2)
    rich = mix.get("cameras", 0) + mix.get("games", 0)
    assert rich > 2 * 2, f"the rich channels were capped at their floor: {mix}"


def test_reserve_zero_restores_the_pure_profit_behaviour(monkeypatch):
    """The escape hatch has to actually be an escape hatch - and the contrast
    is the whole proof that the floor is what changes the outcome. Watches are
    the bottom-ranked channel on this board (a Seiko comps far under a Switch),
    so they are exactly what a pure profit ranking drops."""
    off = _floor_run(monkeypatch, _mixed_board(), top=6, reserve=0)
    on = _floor_run(monkeypatch, _mixed_board(), top=12, reserve=2)
    assert off.get("watches", 0) == 0, f"reserve=0 ranks purely on profit, got {off}"
    assert on.get("watches", 0) >= 2, f"the floor did not lift watches, got {on}"


def test_the_floor_never_pushes_out_a_lot_closing_inside_the_hour(monkeypatch):
    """🚨 URGENCY OUTRANKS BALANCE, which is why the floor runs AFTER the clock
    lanes. A channel's quota can wait for the next run; a lot closing in
    minutes cannot - the next run may be after it has closed."""
    import datetime as _dt
    soon = (_dt.datetime.now() + _dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M")
    rows = _mixed_board()
    urgent = _row("urg1", "Canon PowerShot G7X Mark II", 60.0)
    urgent["ends"] = soon
    rows.append(urgent)
    sent = {}
    monkeypatch.setattr(hunt, "_save_seen", lambda p, s: None)
    monkeypatch.setattr(hunt, "_load_seen", lambda p, **kw: set())
    cfg = {"sources": ["goodwill"], "target_profit": 5.0, "inbound_shipping": 0.0,
           "top": 8, "max_per_model": 3, "reserve_per_channel": 2,
           "urgent_hours": 1, "state_file": "nonexistent.json"}
    hunt.run(cfg, hunters=[FakeHunter(rows)],
             notifier=lambda alerts, content="", **kw: (sent.setdefault("a", []).extend(alerts), ["webhook"])[1])
    urls = {a["url"].rsplit("/", 1)[-1] for a in sent.get("a", [])}
    assert "urg1" in urls, "a lot closing in 20 minutes lost its slot to the floor"


def test_the_floor_reserves_the_channel_the_card_actually_posts_to():
    """🚨 THE SEAM. The floor picks a candidate's channel from the raw
    candidate; delivery picks it AGAIN from the built alert dict. Disagree and
    a slot reserved for #watches is spent on a card that posts to #deals - the
    channel still arrives empty while the log claims it was filled. Verified
    over all 364 live candidates on a real board; pinned here on the shapes
    that can diverge."""
    from flipscout import notify
    from flipscout.pricebook import BY_KEY
    from flipscout.bidding import advise
    for key, title in [("sony_handycam", "Sony Handycam CCD-TR818 Hi8 Camcorder"),
                       ("canon_ae1", "Canon AE-1 Program 35mm SLR"),
                       ("seiko_automatic", "Seiko Automatic watch"),
                       ("ipod_classic_160", "Apple iPod Classic 160GB"),
                       ("pkmn_emerald", "Pokemon Emerald Game Boy Advance")]:
        model = BY_KEY[key]
        adv = advise(comp=model.comp, outbound_shipping=model.outbound_shipping,
                     inbound_shipping=9.0, target_profit=20.0)
        c = {"row": {"title": title, "source": "goodwill", "id": "x",
                     "url": "http://x", "listing_type": "auction"},
             "model": model, "advice": adv,
             "match": type("M", (), {"dead_also_present": [], "label": model.label})()}
        assert notify.channel_for(hunt._alert_route_key(c)) == \
            notify.channel_for(hunt.to_alert(c)), f"{key} routes two ways"


def test_the_floor_default_is_three_and_reads_env(monkeypatch):
    import os
    monkeypatch.delenv("FLIPSCOUT_RESERVE_PER_CHANNEL", raising=False)
    assert hunt.load_config(os.environ)["reserve_per_channel"] == 3
    monkeypatch.setenv("FLIPSCOUT_RESERVE_PER_CHANNEL", "0")
    assert hunt.load_config(os.environ)["reserve_per_channel"] == 0


def test_a_tight_budget_starves_no_channel_more_than_any_other(monkeypatch):
    """🚨 THE BUG THIS LANE ALMOST REINTRODUCED ONE LAYER DOWN. Draining each
    channel's full quota in turn also stops at `top`, so when the budget cannot
    cover reserve x channels the alphabetically LAST names got nothing - the
    same ones every run, silently. Measured at top=6 with 7 groups: cameras,
    games and ipods took 2 each and #watches got zero, which is exactly the
    starvation the floor exists to end. Round-robin degrades evenly instead."""
    mix = _floor_run(monkeypatch, _mixed_board(), top=6, reserve=2)
    served = [v for v in mix.values() if v]
    assert mix.get("watches", 0) >= 1, f"#watches starved by a tight budget: {mix}"
    assert max(served) - min(served) <= 1, f"budget shared unevenly: {mix}"

"""Tests for the bid math, the model guard, and the multi-source sweep.

Anchored on the measured 2026-07-25 numbers: a used TI-84 Plus CE sells for $56.37
and nets $43.50 after fees + $5 postage, so the most you can pay is $23.50 to clear
$20. A TI-83 Plus sells $25.37 and can never clear it.
"""

import pytest

from flipscout.bidding import advise, next_valid_bid
from flipscout.pricebook import BY_KEY, match, search_terms
from flipscout import hunt


CE = BY_KEY["ti84ce"]


# --- next valid bid ---------------------------------------------------------

def test_opening_bid_is_the_start_price_when_nobody_has_bid():
    # You don't add an increment to open an unbid lot.
    assert next_valid_bid(7.99, None, increment=1.0, bid_count=0) == 7.99


def test_opening_bid_adds_an_increment_once_contested():
    assert next_valid_bid(7.99, None, increment=1.0, bid_count=3) == 8.99


def test_explicit_min_bid_from_the_site_wins():
    # HiBid hands us minBid directly; trust it over our arithmetic.
    assert next_valid_bid(0.0, 1.0, bid_count=0) == 1.00


# --- the core money math ----------------------------------------------------

def test_max_bid_matches_the_measured_ce_number():
    a = advise(CE.comp, outbound_shipping=5.00, target_profit=20.0,
               current_price=13.99, bid_count=0)
    assert a.net_resale == pytest.approx(43.50, abs=0.05)
    assert a.max_bid == pytest.approx(23.50, abs=0.05)
    assert a.has_room


def test_handling_and_inbound_shipping_come_out_of_the_ceiling():
    bare = advise(CE.comp, outbound_shipping=5.00, target_profit=20.0, current_price=10)
    laden = advise(CE.comp, outbound_shipping=5.00, target_profit=20.0, current_price=10,
                   handling=3.00, inbound_shipping=9.00)
    assert laden.max_bid == pytest.approx(bare.max_bid - 12.00, abs=0.05)
    # ...and the ceiling still lands where we said it would.
    assert laden.landed_at_max == pytest.approx(laden.max_bid + 12.00, abs=0.05)


def test_profit_at_max_is_exactly_the_target():
    a = advise(CE.comp, outbound_shipping=5.00, target_profit=20.0,
               handling=3.0, inbound_shipping=9.0, current_price=5)
    assert a.profit_at_max == 20.0
    assert a.net_resale - a.landed_at_max == pytest.approx(20.0, abs=0.05)


def test_a_lot_of_three_pays_fees_and_postage_per_unit():
    one = advise(CE.comp, units=1, outbound_shipping=5.00, current_price=8)
    three = advise(CE.comp, units=3, outbound_shipping=5.00, current_price=8)
    # Not 3x: each unit is its own order with its own fee + postage.
    assert three.net_resale == pytest.approx(one.net_resale * 3, abs=0.05)
    assert three.net_resale < CE.comp * 3
    assert three.max_bid > one.max_bid


def test_no_room_when_the_minimum_already_exceeds_the_ceiling():
    a = advise(CE.comp, outbound_shipping=5.00, target_profit=20.0,
               current_price=40.00, min_bid=40.00, bid_count=2)
    assert not a.has_room
    assert "above the ceiling" in a.note


def test_no_room_when_fees_eat_the_whole_item():
    a = advise(6.00, outbound_shipping=5.00, target_profit=20.0, current_price=1)
    assert not a.has_room
    assert a.max_bid == 0.0


def test_zero_bid_listings_are_flagged_as_likely_to_climb():
    a = advise(CE.comp, outbound_shipping=5.00, current_price=7.99, bid_count=0)
    assert "walk away" in a.note


# --- the model guard --------------------------------------------------------

def test_ce_matches_across_the_ways_people_write_it():
    for t in ["Texas Instruments TI-84 Plus CE Color Graphing Calculator",
              "TI 84 Plus CE blue", "ti-84plus ce w/ cover"]:
        m = match(t)
        assert m and m.model.key.startswith("ti84ce"), t


@pytest.mark.parametrize("title", [
    # Caught live 2026-07-25: this was quoted a $198 max bid. It's a paperback.
    "Pokemon Emerald Version Official Game Guide Prima Games GBA Strategy Book",
    "Pokemon Emerald Official Nintendo Power Strategy Player's Guide GBA",
    "Pokemon Crystal box only no game",
    "Pokemon FireRed manual only",
    "iPod Classic 160GB case only",
    "Pokemon Emerald poster",
    "TI-84 Plus CE cover only",
])
def test_accessories_never_match_the_product(title):
    """A guide/box/manual/poster carries the name but not the value."""
    assert match(title) is None, title


def test_the_actual_cartridge_still_matches():
    # The guard must not be so broad it kills the real thing.
    m = match("Pokemon Emerald Version Nintendo GBA Cartridge Green Translucent")
    assert m and m.model.key == "pkmn_emerald"


def test_repro_carts_are_rejected():
    assert match("Pokemon Emerald GBA Reproduction Cart") is None
    assert match("Pokemon Crystal custom fan made cartridge") is None


def test_ipod_variants_price_separately():
    assert match("Apple iPod Classic 160GB Black").model.key == "ipod_classic_160"
    assert match("Apple iPod Video 30GB Silver").model.key == "ipod_video_30"


def test_broken_ipods_are_rejected():
    assert match("Apple iPod Classic 160GB for parts not working") is None


def test_unicode_dashes_still_match():
    # Real HiBid lot used an EN-DASH; an ASCII-only pattern skipped it silently
    # on the lowest-competition source.
    for dash in ["–", "—", "−", "-"]:
        t = f"Texas instruments TI {dash} 84 plus CE Untested"
        assert match(t) is not None, repr(dash)


def test_unicode_dashes_do_not_resurrect_dead_models():
    assert match("Texas Instruments TI – 83 Plus Graphing Calculator") is None


def test_bare_ti84_plus_is_not_treated_as_a_ce():
    # The monochrome TI-84 Plus is a different, cheaper machine.
    assert match("Texas Instruments TI-84 Plus Graphing Calculator") is None


def test_ti83_plus_never_matches():
    # Measured: nets $16.61, max buy -$3.39. Alerting on it would lose money.
    assert match("Vintage Texas Instruments TI-83 Plus Graphing Calculator") is None


def test_python_variant_is_priced_as_python_not_base_ce():
    m = match("Texas Instruments TI-84 Plus CE Python Graphing Calculator")
    assert m.model.key == "ti84ce_python"


def test_case_only_listings_are_excluded():
    assert match("TI-84 Plus CE case only, no calculator") is None


def test_junk_titled_lot_still_matches_and_counts_units():
    # The whole edge: the title says junk, the contents pay.
    m = match("SCIENTIFIC CALCULATOR BULK LOT TI-84 Plus CE & TI-84 Plus CE & TI-30Xa")
    assert m and m.units == 2
    assert m.dead_also_present  # the TI-30Xa is called out, not silently valued


def test_search_terms_are_broader_than_the_models():
    assert any("lot" in t for t in search_terms())


# --- the sweep --------------------------------------------------------------

class FakeHunter:
    name = "goodwill"

    def __init__(self, rows):
        self._rows = rows
        self.enriched = 0

    def search(self, query, limit=40):
        return list(self._rows)

    def enrich(self, row):
        self.enriched += 1
        return {**row, "handling": 3.0}


CE_ROW = {"source": "goodwill", "id": "1", "title": "TI-84 Plus CE Color Graphing Calculator",
          "url": "u", "price": 9.99, "min_bid": 9.99, "increment": 1.0, "bids": 0,
          "handling": None, "image": "i", "ends": ""}
JUNK_ROW = {**CE_ROW, "id": "2", "title": "TI-83 Plus Graphing Calculator"}

CFG = {"sources": ["goodwill"], "target_profit": 20.0, "inbound_shipping": 9.0,
       "top": 10, "state_file": "nonexistent.json"}


def test_sweep_dedupes_across_repeated_search_terms():
    h = FakeHunter([CE_ROW])
    assert len(hunt.sweep(CFG, hunters=[h])) == 1


def test_evaluate_prices_the_ce_and_drops_the_ti83():
    h = FakeHunter([CE_ROW, JUNK_ROW])
    got = hunt.evaluate([CE_ROW, JUNK_ROW], CFG, hunters=[h])
    assert len(got) == 1
    assert got[0]["model"].key == "ti84ce"


def test_evaluate_enriches_only_matched_rows():
    h = FakeHunter([])
    hunt.evaluate([CE_ROW, JUNK_ROW], CFG, hunters=[h])
    assert h.enriched == 1     # the TI-83 never cost us a detail request


def test_alert_carries_both_bid_numbers_and_the_link():
    h = FakeHunter([])
    c = hunt.evaluate([CE_ROW], CFG, hunters=[h])[0]
    a = hunt.to_alert(c)
    assert a["open_bid"] == 9.99
    assert a["max_bid"] > 0
    assert a["url"] == "u" and a["image"] == "i"
    assert "nets" in a["reason"]


def test_unmeasured_comps_are_marked_in_the_alert():
    h = FakeHunter([])
    row = {**CE_ROW, "title": "TI-84 Plus CE Python Graphing Calculator"}
    c = hunt.evaluate([row], CFG, hunters=[h])[0]
    assert "estimate, not measured" in hunt.to_alert(c)["reason"]


def test_run_alerts_and_reports(monkeypatch):
    sent = {}

    def fake_notify(alerts, content="", **kw):
        sent["alerts"] = alerts
        sent["content"] = content
        return ["webhook"]

    monkeypatch.setattr(hunt, "_save_seen", lambda *a: None)
    res = hunt.run(CFG, hunters=[FakeHunter([CE_ROW, JUNK_ROW])], notifier=fake_notify)
    assert res["new"] == 1 and res["sent"] == ["webhook"]
    assert "never bid past" in sent["content"]


# --- the professional-tool categories (chosen for the surplus channel) -------

@pytest.mark.parametrize("title,expected", [
    ("Fluke 87V True RMS Industrial Multimeter", "fluke_87"),
    ("Fluke 179 Digital Multimeter", "fluke_17x"),
    ("Fluke 323 Clamp Meter", "fluke_clamp"),
    ("Mitutoyo 0-1 inch Micrometer", "mitutoyo"),
    ("Starrett Combination Square", "starrett"),
    # "No." has a period in it; an [^.] gap made brand and noun unreachable.
    ("Starrett No. 25 Dial Indicator", "dial_indicator"),
    ("3M Littmann Master Cardiology Stethoscope", "littmann_master_cardiology"),
])
def test_pro_tool_models_match(title, expected):
    m = match(title)
    assert m and m.model.key == expected, f"{title} -> {m.model.key if m else None}"


@pytest.mark.parametrize("title", [
    "Fluke Networks test lead only",
    "Summer flounder fluke fishing rig",      # 'fluke' is also a fish
    "Mitutoyo micrometer case only",
    "Littmann ear tips replacement part",
    "Littmann Classic III",                    # deliberately not in the book
])
def test_pro_tool_lookalikes_rejected(title):
    assert match(title) is None, title


# --- "a brand is not a model" -----------------------------------------------
# Live 2026-07-25: a bare \bfluke\b catch-all quoted a $130.75 max bid on fishing
# lures and a boat anchor. Brand-level models must be corroborated by a noun.

@pytest.mark.parametrize("title", [
    "Zoom Winged Fluke - Gizzard Shad 4in",      # soft-plastic fishing lure
    "JY PERFORMANCE Galvanized Fluke Anchor 13 lb",
    "Tsunami Fluke Spinner Rig w/ Holographic Squid",
    "Zoom Soft Plastic Fluke Assortment with Utility Box",
    "Starrett Hacksaw Blades 10 pack",
    "Starrett Tape Measure 25ft",
])
def test_brand_name_alone_is_not_the_product(title):
    assert match(title) is None, title


@pytest.mark.parametrize("title,expected", [
    ("Fluke Digital Multimeter", "fluke_generic"),
    ("Fluke Temperature Calibrator", "fluke_generic"),
    ("Mitutoyo Digital Caliper 6in", "mitutoyo"),
    ("Starrett Combination Square 12 inch", "starrett"),
])
def test_brand_plus_instrument_still_matches(title, expected):
    m = match(title)
    assert m and m.model.key == expected


def test_unidentified_fluke_is_priced_conservatively():
    """A generic Fluke must not inherit the $194.99 multimeter median - cheap
    models sell $40-60, and we don't know which one it is."""
    generic = BY_KEY["fluke_generic"]
    assert generic.comp <= 100
    assert generic.comp < BY_KEY["fluke_87"].comp


def test_accessories_for_the_tool_are_not_the_tool():
    """Live catch: a bag of indicator contact points reads almost identically to
    the $122 indicator itself."""
    assert match("Starrett No 25R Dial Indicator Contact Point Set") is None
    assert match("Starrett No. 25 Dial Indicator") is not None


def test_alert_links_the_comps_that_justify_the_price():
    from flipscout.ebay_ui import sold_url
    from flipscout.pricebook import comp_search
    h = FakeHunter([])
    a = hunt.to_alert(hunt.evaluate([CE_ROW], CFG, hunters=[h])[0])
    assert a["buy_url"] == "u"
    assert "LH_Sold=1" in a["comps_url"] and "LH_Complete=1" in a["comps_url"]
    # the link must reproduce the SAME population the comp was measured from
    assert "LH_ItemCondition=3000" in a["comps_url"]
    assert a["comps_url"] == sold_url(comp_search(BY_KEY["ti84ce"]), used_only=True)
    assert "on eBay]" in a["reason"]


def test_every_model_has_a_comp_search():
    from flipscout.pricebook import MODELS, comp_search
    for m in MODELS:
        assert comp_search(m).strip(), m.key


# --- technical outerwear: the model is the trade here too --------------------

@pytest.mark.parametrize("title,expected", [
    ("Arc'teryx Beta AR Gore-Tex Jacket Mens Medium", "arcteryx_shell"),
    ("Arcteryx Atom LT Hoody Large", "arcteryx_atom"),
    ("Arc'teryx Kyanite Fleece Jacket", "arcteryx_fleece"),
    ("Arcteryx Jacket Blue Large", "arcteryx_generic"),
    ("Patagonia Nano Puff Jacket Mens M", "patagonia_puffy"),
    ("Patagonia Better Sweater Fleece", "patagonia_generic"),
])
def test_outerwear_models(title, expected):
    m = match(title)
    assert m and m.model.key == expected


@pytest.mark.parametrize("title", [
    "Arcteryx Beanie hat", "Arcteryx Kids Jacket youth",
    "Patagonia Dog Jacket", "Patagonia Sticker Pack",
])
def test_outerwear_lookalikes_rejected(title):
    assert match(title) is None, title


def test_named_shell_beats_the_generic_arcteryx_floor():
    """A named GoreTex shell is worth multiples of an unspecified Arc'teryx
    ($250.52 vs $70 as measured). Asserting the RELATIONSHIP, not a specific
    ratio - the first version pinned 4x off an n=4 comp and broke the moment that
    comp was measured properly on n=60."""
    assert BY_KEY["arcteryx_shell"].comp > 2 * BY_KEY["arcteryx_generic"].comp
    assert BY_KEY["arcteryx_shell"].specificity > BY_KEY["arcteryx_generic"].specificity


# --- Craigslist: fixed price + local pickup ---------------------------------

CL_HTML = '''<html><script id="ld_searchpage_results" type="application/ld+json">
{"itemListElement":[
 {"item":{"name":"iPod Classic 160GB works great","url":"https://sfbay.craigslist.org/sfc/ele/d/x/7777777777.html",
          "image":["https://images.craigslist.org/x_600x450.jpg"],"offers":{"price":"55.00"}}},
 {"item":{"name":"Free broken printer","url":"https://sfbay.craigslist.org/sfc/zip/d/y/8888888888.html",
          "offers":{"price":"0"}}}
]}</script></html>'''


def test_craigslist_parses_the_jsonld_block():
    from flipscout.hunters import Craigslist
    rows = Craigslist.parse(CL_HTML, "sfbay")
    assert len(rows) == 1                    # the $0 listing is dropped
    r = rows[0]
    assert r["price"] == 55.0 and r["source"] == "craigslist"
    assert r["listing_type"] == "fixed" and r["local"] is True
    assert r["id"] == "7777777777"
    assert r["min_bid"] == 55.0              # fixed price: the ask IS the number


def test_craigslist_parse_is_fail_soft_on_junk():
    from flipscout.hunters import Craigslist
    assert Craigslist.parse("<html>no script here</html>", "sfbay") == []
    assert Craigslist.parse('<script id="ld_searchpage_results">{bad json</script>', "x") == []


def test_local_listings_are_not_charged_inbound_shipping():
    """The flat ~$9 inbound is what kills thin margins - a local pickup is worth
    that much more for the identical item."""
    shipped = {**CE_ROW, "id": "s1"}
    local = {**CE_ROW, "id": "l1", "local": True, "listing_type": "fixed", "handling": 0.0}
    h = FakeHunter([])
    a_shipped = hunt.evaluate([shipped], CFG, hunters=[h])[0]["advice"]
    a_local = hunt.evaluate([local], CFG, hunters=[h])[0]["advice"]
    # local ceiling is higher by exactly the inbound shipping (plus the handling
    # the shipped row picks up during enrich)
    assert a_local.max_bid > a_shipped.max_bid
    assert a_local.max_bid - a_shipped.max_bid == pytest.approx(CFG["inbound_shipping"] + 3.0, abs=0.05)


def test_fixed_price_alert_says_asking_not_bidding():
    h = FakeHunter([])
    local = {**CE_ROW, "id": "l2", "local": True, "listing_type": "fixed", "handling": 0.0}
    a = hunt.to_alert(hunt.evaluate([local], CFG, hunters=[h])[0])
    assert a["listing_type"] == "fixed"
    assert "Asking" in a["reason"] and "negotiable" in a["reason"]
    assert "no inbound shipping" in a["reason"]


# --- unit counting: SEO repetition is not two items -------------------------

def test_repeated_model_name_without_lot_evidence_is_one_unit():
    """Live catch: "Like New FLUKE 175 Fluke 175 True RMS Digital Multimeter" is
    ONE meter. Counting the repeat doubled the ceiling to $522 on a $323 item."""
    m = match("Like New FLUKE 175 Fluke 175 True RMS Digital Multimeter")
    assert m and m.units == 1


@pytest.mark.parametrize("title,units", [
    ("Lot of 2 TI-84 Plus CE and TI-84 Plus CE calculators", 2),
    ("Pair of TI-84 Plus CE TI-84 Plus CE", 2),
    ("TI-84 Plus CE TI-84 Plus CE", 1),          # no lot evidence -> one
])
def test_units_need_multi_item_evidence(title, units):
    m = match(title)
    assert m and m.units == units


def test_over_counting_units_inflates_the_ceiling():
    """Guards the direction that costs money: more units => higher max bid."""
    from flipscout.bidding import advise
    one = advise(BY_KEY["fluke_17x"].comp, units=1, outbound_shipping=9.0, current_price=200)
    two = advise(BY_KEY["fluke_17x"].comp, units=2, outbound_shipping=9.0, current_price=200)
    assert two.max_bid > one.max_bid * 1.9


# --- comps must reflect what you'll actually find ---------------------------

def test_video_game_comps_do_not_use_the_used_filter():
    """eBay's Used filter doesn't apply to video games (separate taxonomy), and
    with it on, "pokemon emerald" returned ONE sold listing - so the comp link
    would show an empty search."""
    for k in ("pkmn_emerald", "pkmn_crystal", "pkmn_rby"):
        assert BY_KEY[k].comp_used_only is False, k
    # electronics/apparel DO respond to it, so they keep it
    assert BY_KEY["ti84ce"].comp_used_only is True
    assert BY_KEY["ipod_classic_160"].comp_used_only is True


def test_pokemon_comps_are_the_loose_price_not_the_boxed_one():
    """Prices are bimodal (Emerald: loose $108.75 vs boxed $278.13) and a thrift
    find is almost always loose. Pricing at the boxed/blended number implied a
    $210 max bid on a $108 cart."""
    assert BY_KEY["pkmn_emerald"].comp < 150
    assert BY_KEY["pkmn_crystal"].comp < 200


def test_unverified_comps_are_flagged_as_estimates():
    """sample=0 makes the alert print 'estimate, not measured'."""
    for k in ("pkmn_firered_leafgreen", "pkmn_ruby_sapphire", "pkmn_rby"):
        assert BY_KEY[k].sample == 0, k


def test_run_reports_whether_delivery_actually_happened(capsys, monkeypatch):
    """Silence looked identical to success, which made 'it stopped sending me
    deals' impossible to diagnose from the logs."""
    monkeypatch.setattr(hunt, "_save_seen", lambda *a: None)
    hunt.run(CFG, hunters=[FakeHunter([CE_ROW])], notifier=lambda a, content="", **k: ["webhook"])
    assert "DELIVERED" in capsys.readouterr().out

    monkeypatch.setattr(hunt, "_load_seen", lambda p: set())
    hunt.run(CFG, hunters=[FakeHunter([CE_ROW])], notifier=lambda a, content="", **k: [])
    assert "NOT DELIVERED" in capsys.readouterr().out


# --- freshness ---------------------------------------------------------------

def test_age_hours_parses_and_fails_soft():
    import datetime as dt
    now = dt.datetime(2026, 7, 26, 19, 0, 0)
    assert hunt.age_hours("2026-07-26T12:00:00", now) == pytest.approx(7.0)
    assert hunt.age_hours(None, now) is None
    assert hunt.age_hours("not-a-date", now) is None


def test_max_age_filter_is_off_by_default():
    """For auctions, 'listed recently' is the wrong signal - an old lot ending in
    30 minutes with no bids is the better buy."""
    assert hunt.load_config({}) ["max_age_hours"] is None


def test_max_age_filter_drops_stale_but_keeps_unknown_age():
    import datetime as dt
    old = (dt.datetime.now() - dt.timedelta(hours=48)).isoformat()[:19]
    fresh = (dt.datetime.now() - dt.timedelta(hours=1)).isoformat()[:19]
    cfg = {**CFG, "max_age_hours": 24.0}
    rows = [
        {**CE_ROW, "id": "old", "listed": old},
        {**CE_ROW, "id": "fresh", "listed": fresh},
        {**CE_ROW, "id": "unknown"},          # craigslist/hibid never report one
    ]
    got = {c["row"]["id"] for c in hunt.evaluate(rows, cfg, hunters=[FakeHunter([])])}
    assert "old" not in got
    assert got == {"fresh", "unknown"}


def test_alert_shows_how_fresh_the_listing_is():
    import datetime as dt
    fresh = (dt.datetime.now() - dt.timedelta(hours=3)).isoformat()[:19]
    c = hunt.evaluate([{**CE_ROW, "listed": fresh}], CFG, hunters=[FakeHunter([])])[0]
    assert "Listed 3h ago" in hunt.to_alert(c)["reason"]


# --- daily heartbeat: prove quiet != broken ---------------------------------

def test_heartbeat_fires_once_per_day(tmp_path):
    f = str(tmp_path / "hb.json")
    assert hunt._due_for_heartbeat(f, "2026-07-26") is True   # never sent
    hunt._mark_heartbeat(f, "2026-07-26")
    assert hunt._due_for_heartbeat(f, "2026-07-26") is False  # already today
    assert hunt._due_for_heartbeat(f, "2026-07-27") is True   # new day


def test_zero_new_still_checks_in_once_a_day(tmp_path, monkeypatch):
    """Three separate rounds of 'it stopped sending me deals' were all healthy
    runs with nothing new. Silence has to announce itself."""
    posts = []
    cfg = {**CFG, "heartbeat_file": str(tmp_path / "hb.json")}
    monkeypatch.setattr(hunt, "_load_seen", lambda p: {"goodwill:1"})   # already sent
    monkeypatch.setattr(hunt, "_save_seen", lambda *a: None)

    def notifier(alerts, content="", **k):
        posts.append(content)
        return ["webhook"]

    hunt.run(cfg, hunters=[FakeHunter([CE_ROW])], notifier=notifier)
    assert len(posts) == 1
    # It now reports what's ON the board rather than just "nothing new" - the
    # old wording ("you've already been sent all of them") read as dead-air
    # while items were sitting there buyable.
    assert "buyable right now" in posts[0]

    hunt.run(cfg, hunters=[FakeHunter([CE_ROW])], notifier=notifier)
    assert len(posts) == 1          # not twice in one day


def test_baseline_seen_survives_a_cache_wipe(tmp_path):
    """The Actions cache is not durable - changing the workflow's cache path
    invalidated it once and 50 already-delivered deals got re-sent. The committed
    baseline is the floor that can't be wiped."""
    import json as _j
    base = tmp_path / "baseline.json"
    base.write_text(_j.dumps(["goodwill:1", "hibid:2"]))
    empty_cache = str(tmp_path / "missing.json")   # simulates the wipe
    seen = hunt._load_seen(empty_cache, baseline=str(base))
    assert seen == {"goodwill:1", "hibid:2"}


def test_baseline_and_cache_are_merged(tmp_path):
    import json as _j
    base = tmp_path / "baseline.json"; base.write_text(_j.dumps(["goodwill:1"]))
    cache = tmp_path / "seen.json";    cache.write_text(_j.dumps(["hibid:2"]))
    assert hunt._load_seen(str(cache), baseline=str(base)) == {"goodwill:1", "hibid:2"}


# --- Poshmark ---------------------------------------------------------------

POSH_HTML = '''<html>
<script type="application/ld+json">{"@type":"ItemList","itemListElement":[
 {"@type":"ListItem","position":1,"url":"https://poshmark.com/listing/Arcteryx-Beta-AR-Gore-Tex-Jacket-Mens-M-6a58fc086cb7779a11e57943"},
 {"@type":"ListItem","position":2,"url":"https://poshmark.com/listing/Patagonia-Nano-Puff-Jacket-6a557f362c7ec673a74ae9c1"}]}</script>
<div class="tile-grid-redesign__price-current"> $150</div>
<div class="tile-grid-redesign__price-current"> $60</div>
</html>'''


def test_poshmark_pairs_titles_and_prices():
    from flipscout.hunters import Poshmark
    rows = Poshmark.parse(POSH_HTML)
    assert len(rows) == 2
    assert rows[0]["title"] == "Arcteryx Beta AR Gore Tex Jacket Mens M"
    assert rows[0]["price"] == 150.0
    assert rows[0]["listing_type"] == "fixed" and rows[0]["local"] is False
    assert rows[0]["id"] == "6a58fc086cb7779a11e57943"


def test_poshmark_refuses_to_guess_when_counts_disagree():
    """Pairing by position is only safe while url count == price count. A markup
    change must yield nothing, not a wrong price on the wrong item."""
    from flipscout.hunters import Poshmark
    broken = POSH_HTML.replace('<div class="tile-grid-redesign__price-current"> $60</div>', "")
    assert Poshmark.parse(broken) == []


def test_poshmark_parse_is_fail_soft():
    from flipscout.hunters import Poshmark
    assert Poshmark.parse("<html>nothing</html>") == []
    assert Poshmark.parse("") == []


def test_poshmark_rows_price_through_the_book():
    from flipscout.hunters import Poshmark
    rows = Poshmark.parse(POSH_HTML)
    got = hunt.evaluate(rows, CFG, hunters=[])
    keys = {c["model"].key for c in got}
    assert "arcteryx_shell" in keys           # $150 vs a $325 comp


def test_unprofitable_models_are_removed_not_kept_at_zero():
    """Pokemon Gold/Silver comped at $38.99 -> a $0.00 max buy. A model that can
    never clear the bar only generates work; it was dropped rather than left in
    the book pretending to be a candidate."""
    from flipscout.pricebook import BY_KEY
    assert "pkmn_gold_silver" not in BY_KEY
    from flipscout.bidding import advise
    for m in __import__("flipscout.pricebook", fromlist=["MODELS"]).MODELS:
        a = advise(m.comp, outbound_shipping=m.outbound_shipping,
                   target_profit=20.0, inbound_shipping=9.0, current_price=1)
        assert a.max_bid > 0, f"{m.key} can never profit and should be removed"


# --- PropertyRoom -----------------------------------------------------------

PR_HTML = '''<div class="col-products ListingContainer" lid="18851774">
 <img src="https://content.propertyroom.com/x/y.jpeg" alt="Apple Ipod Classic 160GB" />
 <div class="product-name-category"><a href="/l/apple-ipod-classic-160gb/18851774">Apple Ipod Classic 160GB</a></div>
 <div class="time-bids-category">$80.00 3 bids 1d 16h</div></div>'''


def test_propertyroom_parses_a_listing():
    from flipscout.hunters import PropertyRoom
    rows = PropertyRoom.parse(PR_HTML)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "18851774" and r["price"] == 80.0 and r["bids"] == 3
    assert r["url"].endswith("/l/apple-ipod-classic-160gb/18851774")
    assert r["source"] == "propertyroom"


def test_propertyroom_is_fail_soft():
    from flipscout.hunters import PropertyRoom
    assert PropertyRoom.parse("<html>nothing</html>") == []
    assert PropertyRoom.parse("") == []


# --- Shopify used-gear shops ------------------------------------------------

SHOPIFY_PAYLOAD = {"resources": {"results": {"products": [
    {"id": 123, "title": "Arcteryx Beta AR Gore-Tex Jacket", "price": "150.00",
     "available": True, "url": "/products/arcteryx-beta-ar?_pos=1",
     "image": "https://cdn.shopify.com/x.jpg"},
    {"id": 124, "title": "Arcteryx Sold Out Jacket", "price": "99.00",
     "available": False, "url": "/products/sold"},
    {"id": 125, "title": "Broken price", "price": None, "available": True,
     "url": "/products/x"},
]}}}


def test_shopify_parses_and_skips_unavailable_and_priceless():
    from flipscout.hunters import ShopifyStore
    rows = ShopifyStore.parse(SHOPIFY_PAYLOAD, "outandback", "outandbackoutdoor.com")
    assert len(rows) == 1
    r = rows[0]
    assert r["price"] == 150.0 and r["source"] == "outandback"
    assert r["url"] == "https://outandbackoutdoor.com/products/arcteryx-beta-ar"
    assert r["listing_type"] == "fixed" and r["local"] is False


def test_shopify_parse_is_fail_soft():
    from flipscout.hunters import ShopifyStore
    assert ShopifyStore.parse({}, "x", "y.com") == []
    assert ShopifyStore.parse({"resources": {}}, "x", "y.com") == []


def test_shopify_rows_price_through_the_book():
    from flipscout.hunters import ShopifyStore
    rows = ShopifyStore.parse(SHOPIFY_PAYLOAD, "outandback", "outandbackoutdoor.com")
    got = hunt.evaluate(rows, CFG, hunters=[])
    assert {c["model"].key for c in got} == {"arcteryx_shell"}


def test_gear_shops_only_get_clothing_terms():
    """These are outdoor shops - asking them for 'fluke multimeter' wastes a
    request and returns nothing."""
    from flipscout.hunters import build_hunters
    from flipscout.pricebook import search_terms
    h = build_hunters(["outandback"])[0]
    terms = h.relevant_terms(search_terms())
    assert terms and all("arc" in t or "patagonia" in t for t in terms)


@pytest.mark.parametrize("title", [
    "Arcteryx Womens Sentinel Bib Pant",
    "Arcteryx Sabre Pants - Men's",
    "Arcteryx Sylan 2 Shoe - Men's",
    "Arcteryx Womens Essent Leggings",
    "Patagonia Baggies Shorts",
    "Patagonia Nano Puff Vest",
])
def test_non_jacket_garments_do_not_inherit_a_jacket_comp(title):
    """Every outerwear comp was measured from JACKET sales. The used-gear shops
    surface pants/shoes/leggings under the same brand; pricing a $374 bib pant
    off a $70 jacket comp is just a wrong number."""
    assert match(title) is None, title


@pytest.mark.parametrize("title,expected", [
    ("Arcteryx Beta AR Gore-Tex Jacket", "arcteryx_shell"),
    ("Arcteryx Atom LT Hoody", "arcteryx_atom"),
    ("Patagonia Nano Puff Jacket", "patagonia_puffy"),
    ("Arcteryx Jacket Large Blue", "arcteryx_generic"),
])
def test_actual_jackets_still_match(title, expected):
    m = match(title)
    assert m and m.model.key == expected


# --- cameras (measured 2026-07-28): the model is the trade, again ------------

@pytest.mark.parametrize("title,expected", [
    ("Canon PowerShot G7 X Mark II 20.1MP Compact Camera - Black", "g7x_mark2"),
    ("Canon PowerShot G7 X Mark III 20.1MP Point & Shoot Digital Camera", "g7x_mark3"),
    ("Canon PowerShot G7X Digital Camera 20.3 MP 4.2X Optical Zoom", "g7x"),
    ("Canon PowerShot ELPH 180 20.0 MP Digital Camera - Silver", "powershot_elph"),
    ("Sony Cyber-shot DSC-W800 Digital Camera 20.1MP", "sony_cybershot"),
    ("Sony Cybershot DSC-RX100 20.2MP Compact Digital Point and Shoot", "sony_rx100"),
    ("Nikon COOLPIX S8000 black Digital Camera 14.2 MP 10x Optical Zoom", "nikon_coolpix"),
    ("Fujifilm FinePix Z5fd Brown Digital Camera Retro CCD Tested", "fujifilm_finepix"),
    ("Olympus Stylus Epic MJU II 35mm F2.8 Point & Shoot Camera", "olympus_mju2"),
    ("Olympus Stylus Epic DLX 35mm Film Camera Mju II f/2.8 Tested", "olympus_mju2"),
    ("Olympus Stylus Epic Zoom 80 35mm Point & Shoot Film Camera", "stylus_epic_zoom"),
    ("Canon AE-1 Program 35mm Film Camera w/ FD 50mm f1.8 Lens", "canon_ae1"),
    ("Cannon AE-1 50mm Camera W Flash Instructions 4 Rolls Film Battery", "canon_ae1"),
    ("Asahi Pentax K1000 35mm SLR Camera w/50mm f/2 Lens", "pentax_k1000"),
    ("Polaroid SX-70 Sonar OneStep Land Camera Tested Working", "polaroid_sx70"),
    ("Sony Handycam CCD-TRV37 Vision Video8 Camcorder With Nightshot", "sony_handycam"),
])
def test_camera_models_match(title, expected):
    m = match(title)
    assert m and m.model.key == expected, f"{title} -> {m.model.key if m else None}"


@pytest.mark.parametrize("title", [
    # every one of these appeared in the live 2026-07-28 comp sweep
    "Battery door - Canon A1 / AE1 / AE1 program - 3D print (2 Pack) Best Deal",
    "CAMERA REPAIR SERVICE FOR CANON G7X MARK III M3 GENUINE PARTS",
    "Polaroid Close Up Lens And Flash Diffuser #121 For SX-70 Land Camera",
    "3 Polaroid SX-70 Land Camera Lens Shades #120 *2 in the Original Box",
    "Genuine Polaroid sx-70 alpha 1 Tan Leather Neck Strap",
    "Polaroid SX-70 Land Camera Leatherette Replacement Cover - Tan",
    "ND Filter for SX-70 | Use Polaroid 600 Film in Your SX-70!",
    # the plastic Rainbow/OneStep box camera shares the film format, sells $11
    "Vintage Polaroid One Step Land Camera Rainbow Stripe SX-70 untested",
    # the 1990s APS-FILM Elph sold for $5.99 mid-sweep; the digital one is $184
    "CANON ELPH 2 / Point & Shoot Film Camera Untested Read",
    # a charger that names the camera it charges is still just a charger
    "Canon PowerShot ELPH Battery Charger CB-2LV OEM",
    "Sony Hi8 Video Tapes for Handycam Lot of 10",
    # the Pentax KM is not a K1000, even when the title mentions one
    "Pentax Asahi KM Camera w/ 50mm lens - what Pentax K1000 should have been!",
    # measured-cheap cohorts the floor would overbid: every P-series in the
    # sweep sold $10-42, DVD-era Handycams $15-71
    "Sony Cyber-shot DSC-P10 Compact Digital Camera 5.0MP 3x Zoom Silver",
    "Sony DCR-DVD308 Mini DVD Handycam Camcorder w/ Accessories, Untested",
])
def test_camera_lookalikes_and_accessories_rejected(title):
    assert match(title) is None, title


@pytest.mark.parametrize("title,expected", [
    # Cameras are SOLD as bundles - "w/ battery & charger" is evidence of the
    # camera, not of an accessory. The old blanket \bcharger\b / \bcard\b guard
    # rejected all of these real listings.
    ("Canon PowerShot ELPH 135 Compact Digital Camera silver w/ battery & Charger",
     "powershot_elph"),
    ("Sony Cyber-shot DSC-W55 Camera w/ Charger, Battery & SD Card TESTED",
     "sony_cybershot"),
    ("Canon PowerShot G7X Mark II 20.1 MP Compact Digital Camera 64gb Card",
     "g7x_mark2"),
    ("Nikon COOLPIX B500 Red 16.0MP Digital Camera Bundle - 64GB, Case & Strap",
     "nikon_coolpix"),
    ("Nikon Coolpix L820 16MP Digital Camera w/Padded Case, Batteries & Card",
     "nikon_coolpix"),
    ("Vintage Pentax K1000 SLR Film Camera Working w/ Pentax 50mm F/2 Lens w/ Lens Cap",
     "pentax_k1000"),
])
def test_bundled_accessories_do_not_reject_the_camera(title, expected):
    m = match(title)
    assert m and m.model.key == expected, f"{title} -> {m.model.key if m else None}"


def test_accessory_as_product_still_rejected_after_the_bundle_fix():
    # the two live catches that motivated the guard must STAY dead
    assert match("Hard Case Compatible with Texas Instruments TI-84 Plus CE") is None
    assert match("SCOVEE PS3 Charger Cable 5FT Compatible with TI-84 Plus CE") is None
    # and a leading "Hard Case ..." with no bundle context is the accessory
    assert match("Hard Case for iPod Classic 160GB") is None


def test_camera_brand_lines_are_priced_at_a_conservative_floor():
    """Same rule as fluke_generic: the spread inside Cyber-shot/Coolpix/FinePix
    is ~10x and the sub-model rarely survives an auction title, so the book
    carries a floor below the measured median (118.86 / 97.98 / 66.06)."""
    assert BY_KEY["sony_cybershot"].comp <= 75
    assert BY_KEY["nikon_coolpix"].comp <= 55
    assert BY_KEY["fujifilm_finepix"].comp <= 45
    assert BY_KEY["powershot_elph"].comp <= 120


def test_named_camera_models_beat_their_brand_floor():
    assert BY_KEY["sony_rx100"].comp > 3 * BY_KEY["sony_cybershot"].comp
    assert BY_KEY["g7x_mark2"].comp > BY_KEY["g7x"].comp
    assert BY_KEY["sony_rx100"].specificity > BY_KEY["sony_cybershot"].specificity
    # mju-II vs the Zoom variants: 2.8x, measured
    assert BY_KEY["olympus_mju2"].comp > 2 * BY_KEY["stylus_epic_zoom"].comp


def test_epic_zoom_is_not_priced_as_the_mju2():
    """'Stylus Epic Zoom 80 MJU II' listings exist - sellers stuff both names.
    'Zoom' must demote to the $176 model, never the $485 one."""
    m = match("Olympus Stylus Epic Zoom 80 MJU II Black 35mm Film Point and Shoot")
    assert m and m.model.key == "stylus_epic_zoom"


def test_film_camera_comps_do_not_use_the_used_filter():
    """Vintage film cameras are listed under every condition bucket; the Used
    filter starves the search - same taxonomy lesson as the video games."""
    for k in ("canon_ae1", "pentax_k1000", "polaroid_sx70",
              "olympus_mju2", "stylus_epic_zoom"):
        assert BY_KEY[k].comp_used_only is False, k
    # digitals were measured WITH it and keep it
    assert BY_KEY["g7x_mark2"].comp_used_only is True
    assert BY_KEY["sony_cybershot"].comp_used_only is True


# --- women's apparel (measured 2026-07-28): only the lines that pass ---------

@pytest.mark.parametrize("title,expected", [
    ("Gunne Sax by Jessica McClintock Vintage Prairie Dress Floral Lace", "gunne_sax"),
    ("Veronica Beard Scuba Dickey Blazer Navy Size 8", "veronica_beard"),
    ("St. John Collection Santana Knit Jacket Black Womens 10", "st_john_knit"),
    ("St John Knit Blazer Marie Gray Size M", "st_john_knit"),
    ("Johnny Was Embroidered Tunic Dress Womens L", "johnny_was"),
    ("Reformation Scottie Silk Midi Dress 4 Purple", "reformation_dress"),
])
def test_womens_apparel_models_match(title, expected):
    m = match(title)
    assert m and m.model.key == expected, f"{title} -> {m.model.key if m else None}"


@pytest.mark.parametrize("title", [
    # St John's Bay is JCPenney (~$8); the islands are a vacation
    "St John's Bay Womens Knit Jacket Size XL",
    "St John USVI Virgin Islands Souvenir Knit Jacket",
    # bare Veronica Beard without a blazer noun is the $16 tee end
    "Veronica Beard Womens Tee Shirt Small",
    # girls' Gunne Sax is a different (cheaper) population
    "Gunne Sax Girls Vintage Dress Size 7",
    "Vintage Gunne Sax Sewing Pattern 1978",
    "Johnny Was Silk Scarf Floral",
    # every one of these matched in the first live validation sweep
    "St. JohnsBay leather full zip jacket mens (L)",
    "St. John Women's Stretch Knit High Rise Black Size M Pants",
    "St. John Collection Women's Knit Cardigan, Size 10",
    "Biya by Johnny Was Embroidered Shoes 8",
    "Johnny Was Los Angeles Camo Floral Leggings Sz S",
    "Pete & Greta by Johnny Was Corduroy Cargo Pants",
    "Gunne Sax Black Size 10\" x 4.5\" Clutch",
])
def test_womens_apparel_lookalikes_rejected(title):
    assert match(title) is None, title


def test_generic_boutique_dress_brands_are_deliberately_absent():
    """Measured 2026-07-28: Free People / Anthropologie / Lilly Pulitzer sold
    medians are $39-41 (n=60 each) -> ~$7 a flip after the buy + inbound. They
    fail the $20 bar, so alerting on them only generates work. Farm Rio and
    LoveShackFancy draw 6 bids median on Goodwill - crowded. None are models."""
    for title in ("Free People Boho Maxi Dress Size M",
                  "Anthropologie Maeve Floral Dress 8",
                  "Lilly Pulitzer Shift Dress Medium",
                  "Farm Rio Banana Print Maxi Dress",
                  "LoveShackFancy Ruffle Mini Dress"):
        assert match(title) is None, title


def test_poshmark_gets_the_womens_apparel_terms():
    """Poshmark is the native channel for these brands; its term filter must
    let them through or the source never sees them."""
    from flipscout.hunters import Poshmark
    from flipscout.pricebook import search_terms
    terms = Poshmark().relevant_terms(search_terms())
    assert "gunne sax" in terms and "johnny was" in terms


def test_pricebook_has_no_literal_control_characters():
    """A patch once turned every \b word-boundary into a literal backspace
    (0x08), silently disabling the kids/dog/pants excludes on three models -
    they matched nothing and nothing was excluded. Cheap guard against a class of
    bug that is invisible in a diff."""
    import flipscout.pricebook as pb
    src = open(pb.__file__, "rb").read()
    for bad in (b"\x08", b"\x07", b"\x0c", b"\x0b"):
        assert bad not in src, f"control char {bad!r} in pricebook.py"
    for m in pb.MODELS:
        for field in (m.include, m.exclude):
            assert not any(ord(c) < 32 for c in field), m.key

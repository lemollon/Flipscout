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

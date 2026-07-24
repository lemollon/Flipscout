"""Tests for the browser-sourced comp reader.

The fixtures below are REAL data measured off eBay on 2026-07-24 for
"donkey kong 64 nintendo 64" — the search that exposed why a naive median is
dangerous (loose $28.16 vs complete $199.00, and every cheap listing a Japanese
region-locked import).
"""

import json

import pytest

from flipscout.ebay_ui import (
    STRUCTURAL_FLOOR,
    build_report,
    classify,
    contaminants,
    load_raw,
    parse_rows,
    sold_url,
    active_url,
)


# --- URL building -----------------------------------------------------------

def test_sold_url_restricts_to_sold_and_completed():
    u = sold_url("donkey kong 64")
    assert "LH_Sold=1" in u and "LH_Complete=1" in u
    assert "donkey+kong+64" in u


def test_active_url_can_sort_cheapest_first():
    assert "_sop=15" in active_url("zelda", cheapest_first=True)
    assert "_sop=15" not in active_url("zelda", cheapest_first=False)


# --- condition classification ----------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Donkey Kong 64 N64 Cartridge Only", "loose"),
    ("Donkey Kong 64 Complete In Box CIB Nintendo 64", "cib"),
    ("Donkey Kong 64 Factory Sealed", "sealed"),
    ("Donkey Kong 64 WATA 9.4 A+ Graded", "graded"),
    ("Donkey Kong 64 Nintendo 64", "unknown"),
])
def test_classify_buckets(title, expected):
    assert classify(title) == expected


def test_graded_beats_sealed_when_titles_stack_adjectives():
    # A sealed graded slab is priced as a slab, not as a sealed game.
    assert classify("Donkey Kong 64 Factory Sealed WATA 9.8") == "graded"


# --- contamination ----------------------------------------------------------

def test_japanese_import_is_flagged():
    # This is the exact failure that made cheap BINs look like free money.
    assert "import" in contaminants("Donkey Kong 64 (JPN) Nintendo 64 (Region Locked)")


@pytest.mark.parametrize("title,flag", [
    ("Donkey Kong 64 Reproduction Cartridge", "repro"),
    ("Donkey Kong 64 for parts not working", "parts"),
    ("Lot of 5 Nintendo 64 Games", "lot"),
    ("Donkey Kong 64 [Not For Resale Yellow]", "variant"),
])
def test_contaminant_flags(title, flag):
    assert flag in contaminants(title)


def test_clean_title_has_no_flags():
    assert contaminants("Donkey Kong 64 Nintendo 64 Authentic Tested") == []


def test_number_in_product_name_is_not_a_lot():
    # Real 2026-07-24 regression: "64 Games" tripped a \d+\s*games lot rule, so a
    # single game got excluded from its own comp.
    assert "lot" not in contaminants("DONKEY KONG 64 Games For Nintendo N64 US Version")
    assert "lot" not in contaminants("Mario Party 3 Games For Nintendo 64")


def test_real_lots_still_flagged():
    for t in ["Lot of 5 Nintendo 64 Games", "N64 Game Bundle", "Set of 3 games",
              "12 comics lot"]:
        assert "lot" in contaminants(t), t


def test_bare_cartridge_mention_reads_as_loose():
    # "Yellow Cartridge" is a loose cart; it was falling into `unknown`.
    assert classify("Nintendo Donkey Kong 64 N64 Video Game Cartridge Yellow NUS-006") == "loose"
    # ...but a boxed copy must still win, since cib is matched first.
    assert classify("Donkey Kong 64 complete in box with cartridge and manual") == "cib"


def test_module_output_is_ascii_safe():
    # Windows consoles are cp1252; a stray em-dash renders as a replacement char.
    from flipscout import ebay_ui
    src = open(ebay_ui.__file__, encoding="utf-8").read()
    assert all(ord(c) < 128 for c in src), "non-ASCII in ebay_ui.py breaks cp1252 terminals"


# --- parsing ----------------------------------------------------------------

def test_parse_rows_drops_junk_and_adds_shipping():
    rows = parse_rows([
        {"title": "DK64 cart only", "price": 24.00, "shipping": 4.16, "sold": "Jul 24, 2026"},
        {"title": "broken row", "price": None},
        {"title": "free ship", "price": 30.00, "shipping": 0},
        {"title": "zero price", "price": 0},
    ])
    assert len(rows) == 2
    assert rows[0].all_in == 28.16      # price + shipping, what the buyer really paid
    assert rows[1].all_in == 30.00


def test_load_raw_accepts_array_and_wrapped_and_quoted():
    assert load_raw('[{"title":"x","price":1}]')[0]["price"] == 1
    assert load_raw('{"rows":[{"title":"x","price":2}]}')[0]["price"] == 2
    assert load_raw('"[{\\"title\\":\\"x\\",\\"price\\":3}]"'.replace('\\"', '"'))[0]["price"] == 3
    assert load_raw("") == []


# --- the report -------------------------------------------------------------

DK64 = [
    # clean loose carts (the real US market, median ~$28)
    {"title": "Donkey Kong 64 Nintendo 64 Cartridge Only", "price": 24.00, "shipping": 4.16},
    {"title": "Donkey Kong 64 N64 game only", "price": 27.99, "shipping": 0},
    {"title": "Donkey Kong 64 loose cart N64", "price": 29.99, "shipping": 0},
    {"title": "Donkey Kong 64 cartridge only tested", "price": 31.00, "shipping": 0},
    # clean complete-in-box (7x the loose price)
    {"title": "Donkey Kong 64 Complete In Box N64", "price": 189.00, "shipping": 10.00},
    {"title": "Donkey Kong 64 CIB with manual Nintendo 64", "price": 199.00, "shipping": 0},
    {"title": "Donkey Kong 64 complete with box and manual", "price": 215.00, "shipping": 0},
    # contaminated — must NOT price the headline
    {"title": "Donkey Kong 64 (JPN) Nintendo 64 (Region Locked)", "price": 8.99, "shipping": 0},
    {"title": "Donkey Kong 64 Japanese Import N64", "price": 9.96, "shipping": 0},
    {"title": "Lot of 5 Nintendo 64 games incl Donkey Kong 64", "price": 60.00, "shipping": 0},
]


def test_headline_excludes_contaminated_rows():
    r = build_report("donkey kong 64", DK64)
    assert len(r.rows) == 10
    assert len(r.clean) == 7
    # If imports were included the headline would crater toward $9.
    assert r.headline > 30


def test_condition_segmentation_separates_loose_from_cib():
    conds = build_report("donkey kong 64", DK64).by_condition()
    assert conds["loose"]["n"] == 4
    assert conds["cib"]["n"] == 3
    assert conds["cib"]["median"] > 3 * conds["loose"]["median"]


def test_warns_about_condition_spread():
    ws = " ".join(build_report("donkey kong 64", DK64).warnings())
    assert "condition spread" in ws


def test_warns_about_imports():
    ws = " ".join(build_report("donkey kong 64", DK64).warnings())
    assert "import" in ws


def test_max_pay_uses_the_condition_you_are_buying():
    r = build_report("donkey kong 64", DK64, resell_shipping=4.75)
    conds = r.by_condition()
    loose_pay = r.max_pay(conds["loose"]["median"], target_profit=20)
    cib_pay = r.max_pay(conds["cib"]["median"], target_profit=20)
    # Paying loose money for a loose cart must be near-impossible; CIB has room.
    assert loose_pay < 5
    assert cib_pay > 100


def test_structural_floor_warning_fires_on_cheap_items():
    cheap = [{"title": "Panini Contenders base card lot", "price": 6.00, "shipping": 0}]
    # (a lot, so also flagged) — use a clean cheap item to isolate the floor check
    cheap = [{"title": "common football card", "price": 6.00, "shipping": 0}]
    r = build_report("common football card", cheap, resell_shipping=5.00)
    ws = " ".join(r.warnings())
    assert "BELOW THE FLOOR" in ws
    assert r.headline < STRUCTURAL_FLOOR


def test_all_flagged_is_called_out_as_unusable():
    only_bad = [
        {"title": "Donkey Kong 64 (JPN) region locked", "price": 8.99, "shipping": 0},
        {"title": "Donkey Kong 64 reproduction", "price": 12.00, "shipping": 0},
    ]
    r = build_report("donkey kong 64", only_bad)
    assert r.clean == []
    assert any("unusable" in w for w in r.warnings())


def test_empty_input_is_handled():
    r = build_report("nothing", [])
    assert r.headline is None
    assert "no sold listings parsed" in " ".join(r.warnings())
    assert "no rows parsed" in r.render()


def test_render_smoke():
    out = build_report("donkey kong 64", DK64, resell_shipping=4.75).render()
    assert "eBay SOLD comps" in out
    assert "by condition" in out
    assert "max you can pay" in out

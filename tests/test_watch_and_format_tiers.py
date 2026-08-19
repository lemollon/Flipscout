"""The 2026-08-17 tiering pass: Citizen, Seiko, Handycam, GBA SP.

WHY THIS FILE EXISTS
--------------------
Leron sent nine listings on 2026-08-16/17 and asked "is this a good deal" of
each. The book got SIX of them wrong, and every single miss was the same shape:
one comp covering a family whose sub-models differ 3-10x.

    citizen_watch   $85   plain quartz $28 · Elegance $37 · Perpetual $150
    sony_handycam  $120   DVD format $41   vs   tape format $135
    gba_sp          $80   priced a $19 CARTRIDGE as the console

This is the rule the top of pricebook.py already states with TI-84 vs TI-83 -
THE MODEL IS THE TRADE - which the watches and camcorders never had applied.

🚨 The Perpetual Calendar row is the one that matters most. A brand comp is not
merely over-generous; it also makes you WALK PAST the good stuff. That listing
was worth $150 and the book quoted $47, so it would have been skipped.

Every case below is a REAL listing with its measured population, kept verbatim
so a future change has to answer to the actual market rather than to intuition.
"""

import pytest

from flipscout.bidding import advise
from flipscout.pricebook import BY_KEY, DEAD_MODELS, MODELS, match

# ---------------------------------------------------------------- Citizen ---
# Measured 2026-08-17, eBay solds, each tier filtered then floored at p25.

CITIZEN_TIERS = [
    ("Citizen Campanola Grand Complication Mens Watch",      "citizen_campanola"),
    ("Citizen Promaster Chronograph 0610-H03281 Navy Dial",  "citizen_promaster_chrono"),
    ("Citizen Promaster Aqualand Diver 200m Mens Watch",     "citizen_promaster"),
    ("Citizen Nighthawk Eco-Drive Pilot Mens Watch",         "citizen_nighthawk"),
    ("Citizen Eco-Drive Chronograph Mens Watch Steel",       "citizen_ecodrive_chrono"),
    ("Citizen Eco-Drive Perpetual Calendar Mens Watch",      "citizen_perpetual"),
    ("Citizen Eco-Drive Mens Watch Stainless Steel",         "citizen_ecodrive_mens"),
    ("Citizen Mens Quartz Black Dial Stainless Watch",       "citizen_quartz_mens"),
]


@pytest.mark.parametrize("title,key", CITIZEN_TIERS)
def test_each_citizen_tier_prices_as_itself(title, key):
    m = match(title)
    assert m is not None, f"{title!r} matched nothing"
    assert m.model.key == key


def test_the_tiers_are_ordered_by_value_not_alphabetically():
    """A Promaster must never be priced as a plain quartz, and an Eco-Drive
    chronograph must never be priced as a plain Eco-Drive."""
    comp = lambda k: BY_KEY[k].comp
    assert comp("citizen_promaster") > comp("citizen_ecodrive_mens")
    assert comp("citizen_ecodrive_chrono") > comp("citizen_ecodrive_mens")
    assert comp("citizen_ecodrive_mens") > comp("citizen_quartz_mens")
    # A diver is worth more than the same brand's land chronograph, and both
    # beat a plain Eco-Drive. Measured 2026-08-19: p25 $183 vs $150 vs $62.
    assert comp("citizen_promaster") > comp("citizen_promaster_chrono")
    assert comp("citizen_promaster_chrono") > comp("citizen_ecodrive_chrono")
    # and specificity must break ties toward the MORE specific tier
    for high, low in [("citizen_campanola", "citizen_ecodrive_mens"),
                      ("citizen_promaster_chrono", "citizen_promaster"),
                      ("citizen_promaster", "citizen_ecodrive_mens"),
                      ("citizen_ecodrive_chrono", "citizen_ecodrive_mens"),
                      ("citizen_ecodrive_mens", "citizen_quartz_mens")]:
        assert BY_KEY[high].specificity > BY_KEY[low].specificity, f"{high} vs {low}"


# --- the 2026-08-19 re-measure ---------------------------------------------
# Leron: "fix the citizen watch comps in the price book". Every tier was
# re-measured on the population it ACTUALLY RECEIVES after routing, rather than
# on what its comp_query returns. Three tiers were over by 43-65% and one died.

def test_a_promaster_chronograph_is_not_priced_as_a_diver():
    """🚨 THE MISS THAT STARTED THIS. `citizen_promaster` includes
    `promaster|aqualand`, so every land chronograph was quoted at the DIVER
    comp. Leron asked about a Promaster 0610-H03299 whose only two solds were
    $125 and $150; the book answered $200.
    """
    m = match("Citizen Promaster 0610-H03299 Vintage 1996 Reverse Panda "
              "Chronograph Wristwatch")
    assert m.model.key == "citizen_promaster_chrono"
    assert m.model.comp == 150.00
    # ...and a real diver still gets the diver comp.
    d = match("Citizen Promaster Aqualand JP2000-08E 200m Diver Watch")
    assert d.model.key == "citizen_promaster"


def test_the_ecodrive_chrono_comp_is_not_inflated_by_halo_models():
    """🚨 THE WORST NUMBER IN THE BOOK: $145 against a routed p25 of $88.

    174 of the 329 solds its comp_query returns are Skyhawks and Promasters
    that route to a HIGHER tier and never reach this one. Measuring on the
    query instead of on the routed population inherited their prices.
    """
    assert BY_KEY["citizen_ecodrive_chrono"].comp == 88.00
    # the contaminating titles must still route away from this tier
    for t in ["Citizen Eco-Drive Skyhawk Black Eagle Chronograph Mens Watch",
              "CITIZEN PROMASTER TSUNO AV0070-57L Eco-Drive Chronograph"]:
        assert match(t).model.key != "citizen_ecodrive_chrono", t


def test_the_plain_quartz_chronograph_tier_is_dead():
    """It comped at $62 and looked alive. Re-measured it is p25 $43, which
    quotes a $0.00 max bid at the book's standing gate - it could never clear
    the bar, so it was dropped rather than left pretending to be a candidate.
    """
    assert "citizen_quartz_chrono" not in BY_KEY
    assert match("Citizen Chronograph Two-Tone Mens Watch Blue Dial") is None


def test_the_dead_quartz_chrono_rule_is_anchored():
    """🚨 It is the only watch rule carrying a NEGATIVE lookahead. Unanchored,
    `re.search` retries at every offset until `(?!.*eco.?drive)` succeeds PAST
    the word it is meant to veto - which would condemn every Eco-Drive chrono
    in the book. Assert the vetoes actually hold.
    """
    for t in ["Citizen Eco-Drive Chronograph Mens Watch Steel",
              "Citizen Eco-Drive Skyhawk Blue Angels Chronograph Watch",
              "Citizen Promaster Chronograph 0610-H03281 Navy Dial"]:
        m = match(t)
        assert m is not None, t
        assert not m.dead_also_present, f"{t} wrongly flagged dead: {m.dead_also_present}"


def test_every_citizen_comp_carries_fresh_measurement_evidence():
    """No comp without a date and a sample behind it - the book's own rule."""
    for k, mdl in BY_KEY.items():
        if not k.startswith("citizen_"):
            continue
        assert mdl.measured == "2026-08-19", f"{k} was not re-measured"
        assert mdl.sample >= 30, f"{k} sample too thin to floor a comp"


def test_the_brand_level_citizen_comp_is_gone():
    """🚨 `citizen_watch` was ONE $85 comp on `\\bcitizen\\b` for a brand whose
    solds run $19 to $2,520. It was wrong on five consecutive real listings."""
    assert "citizen_watch" not in BY_KEY


@pytest.mark.parametrize("title", [
    "Citizen Eco-Drive Ladies Stainless Steel Grey Dial Watch Date",
    "Citizen Elegance Signature Men's Quartz Analog Black Dial Steel",
    "CITIZEN Seven 2010-944801 Three-Hand Quartz Analog Women's Watch",
    "Vintage Citizen Gold-Tone Watch",
    "Citizen Brown Leather Silver-Tone Quartz Watch",
])
def test_the_dead_citizen_tiers_never_price(title):
    """Real listings, all of which the $85 brand comp said BUY. Measured:
    ladies Eco-Drive p25 $40, Elegance p25 $23.95, ladies quartz p25 $19.79 -
    every one fails the $20-profit gate."""
    assert match(title) is None


def test_dead_citizen_tiers_carry_their_number():
    import re
    hits = [w for p, w in DEAD_MODELS.items()
            if re.search(p, "citizen elegance signature mens quartz watch")]
    assert hits and any("$23.95" in w or "Elegance" in w for w in hits)


# ------------------------------------------------------- Seiko, by gender ---

def test_ladies_seiko_automatic_is_refused():
    """Women's Seiko automatic: p25 $40 / median $88 (n=223) against men's p25
    $75 / median $150 (n=218). A 25mm ladies Seiko 5 was quoted a $30 max bid
    off the ungendered comp."""
    assert match("Seiko 5 Automatic Ladies Watch 25mm Gold Tone") is None
    assert match("Seiko 5 Automatic Mens Watch 38mm") is not None


# ------------------------------------------- Handycam: the FORMAT is value ---

@pytest.mark.parametrize("title", [
    "Sony Handycam DCR-DVD92 Camcorder w/Carl Zeiss Lens",
    "Sony Handycam DCR-DVD810 Camcorder Carl Zeiss Lens 25x Zoom",
    "Sony DCR-DVD101E Handheld DVD-RW Camcorder Blue",
])
def test_dvd_camcorders_never_price_as_tape_units(title):
    """🚨 `\\bdvd\\b` COULD NOT MATCH "DCR-DVD92" - the trailing \\b needs a
    non-word char and "92" is one. Every DVD Handycam sailed through onto the
    TAPE comp; one reached the live board claiming $73.72 of profit against a
    real number near $8.

    Tape sells at a $134.99 median because buyers DIGITISE cassettes. A DVD
    camcorder has no such job - $41.16 median for the DVD92 exactly.
    """
    assert match(title) is None


@pytest.mark.parametrize("title", [
    "Sony DCR-TRV225 Digital8 Hi8 Camcorder 25x Zoom NightShot",
    "Sony DCR-TRV11 MiniDV Digital Handycam Video Camera Recorder",
    "Sony CCD-TR818 Hi8 Handycam Camcorder",
])
def test_tape_camcorders_still_price(title):
    m = match(title)
    assert m is not None and m.model.key == "sony_handycam"


# --------------------------------------------------- the nine real listings --
# End-to-end: what the book NOW says about every listing Leron actually asked
# about. `None` means "does not price", which for most of these is the correct
# answer and was not what the old book said.

REAL_LISTINGS = [
    # (title, what the OLD book quoted, what it is worth)
    ("Citizen Brown Leather Silver-Tone Quartz Watch",                 "$42 max", "$40-55"),
    ("Citizen Eco-Drive 005193 Perpetual Calendar Silver Tone Watch",  "$47 max", "$150 med"),
    ("Citizen Elegance Signature Men's Quartz Analog Black Dial",      "$39 max", "$37 med"),
    ("CITIZEN Seven 2010-944801 Quartz Analog Women's Watch",          "$41 max", "$28 med"),
    ("Citizen Eco-Drive Ladies Stainless Steel Grey Dial Watch",       "$39 max", "$85 med"),
    ("Sony Handycam DCR-DVD92 Camcorder w/Carl Zeiss Lens",            "$60 max", "$41 med"),
    ("Super Mario Advance 4 Super Mario Bros 3 Nintendo Game Boy "
     "Advance SP Gameboy",                                             "$35 max", "$19 med"),
]


@pytest.mark.parametrize("title,was,worth", REAL_LISTINGS)
def test_no_real_listing_is_overpriced_any_more(title, worth, was):
    """Each of these was quoted a max bid ABOVE what the item is worth, except
    the Perpetual Calendar, which was quoted far BELOW. Either the model now
    declines to price it, or it prices it in the right tier."""
    m = match(title)
    if m is None:
        return                      # correctly refuses
    # If it does price, it must be through a TIER, never the removed
    # brand-level model. (Keying on comp != 85.00 was a bad test: the
    # perpetual-calendar floor legitimately landed on $85 too.)
    assert m.model.key != "citizen_watch"
    assert m.model.key in BY_KEY


def test_the_perpetual_calendar_is_now_worth_more_not_less():
    """The one the old book UNDER-priced. A brand comp does not just overpay -
    it makes you walk past the good stuff."""
    m = match("Citizen Eco-Drive Perpetual Calendar Silver Tone Blue Face Watch")
    assert m is not None and m.model.key == "citizen_perpetual"
    a = advise(m.model.comp, outbound_shipping=m.model.outbound_shipping,
               target_profit=20.0, inbound_shipping=0.0, current_price=30.99)
    assert a.max_bid > 30.99, "should still be a BUY at the $30.99 it went for"


def test_every_new_tier_can_actually_clear_the_bar():
    """The book's standing gate: $20 target profit over $9 inbound."""
    for _, key in CITIZEN_TIERS:
        m = BY_KEY[key]
        a = advise(m.comp, outbound_shipping=m.outbound_shipping,
                   target_profit=20.0, inbound_shipping=9.0, current_price=1)
        assert a.max_bid > 0, f"{key} can never profit"


# --- the 2026-08-19 camera + console routed re-measure -----------------------
# Leron: "now do cameras and consoles the same way". Same method as the Citizen
# pass: measure each tier on the population it RECEIVES after routing, not on
# what its comp_query returns. Cameras had never had an accessory guard at all.

@pytest.mark.parametrize("title", [
    # 🚨 EVERY ONE OF THESE MATCHED A CAMERA TIER before 2026-08-19, and every
    # one is real - pulled from 4,292 routed sold listings. A $2.61 strap was
    # being priced against a $150 camera comp, which on a live sweep is a BUY
    # card with a ~$92 max bid on a piece of nylon.
    "[Excelle++++] Canon Genuine Neck/Shoulder Strap For AE-1 A-1 From Japan",
    "CANON FD Body CAMERA CAP for AE-1 A-1 AT-1 AV-1 TLb T50 T70 F-1 SLIP ON",
    "Canon AE-1, AE-1Program, & AT-1 Screws",
    "Canon AE-1, AE-1 Program AT-1 AV-1 Viewfinder Eyepiece Lens Clean Parts",
    "Polaroid SX-70 Land Camera Alpha SE Original Manual In English",
    "Vintage Polaroid SX-70 Tripod Mount #111 (AM08)",
    "Beautiful Pentax K-1000 Camera Manual, EX++- Condition",
    "Polaroid Instant SX-70 Land Camera #112 Remote Shutter Button In Box",
])
def test_camera_accessories_never_price_as_the_camera(title):
    assert match(title) is None


@pytest.mark.parametrize("title,key", [
    # 🚨 THE OTHER HALF, AND THE HARDER ONE. A first cut listed `cap` and
    # `strap` as junk outright and REJECTED THESE REAL CAMERAS. The word is
    # shared; what separates an accessory is that it says "for <model>".
    ("Canon AE-1 35mm SLR Film Camera W/ 50mm FD Works Great!", "canon_ae1"),
    ("Canon AE-1 Program 35mm SLR Film Camera w/ Cap and Strap", "canon_ae1"),
    ("Vintage Pentax K1000 SLR Film Camera Working w/ 50mm F/2 Lens w/ Lens Cap",
     "pentax_k1000"),
    ("Pentax K1000 35mm SLR camera with body cap", "pentax_k1000"),
    # "manual focus" describes half the film cameras ever made.
    ("Pentax K1000 35mm Manual Focus SLR Camera w/ 50mm Lens", "pentax_k1000"),
    ("Working Polaroid SX-70 Sonar Land Camera tested w/ film", "polaroid_sx70"),
])
def test_a_camera_that_merely_includes_an_accessory_still_prices(title, key):
    m = match(title)
    assert m is not None, f"{title!r} was rejected as an accessory"
    assert m.model.key == key


@pytest.mark.parametrize("title", [
    # The console equivalent: `_console_include` accepts a hardware MODEL
    # NUMBER as evidence, and accessories carry SCPH numbers too.
    "Sony PlayStation 2 PS2 RFU Adapter SCPH-10071 Official OEM",
    "OEM Sony PlayStation 2 PS2 SLIM AC Adapter Power Supply & AV Cable SCPH-7010",
    "Sony PlayStation 2 PS2 Multitap Black SCPH-10090",
    "OEM Replacement Sega Dreamcast Authentic Exterior Screws (4Pcs.)",
    "Performance Sega Dreamcast Tremor Pak Model P-20-313",
    "VGA box for Sega Dreamcast consoles - Grey (made in USA)",
])
def test_console_accessories_never_price_as_the_console(title):
    assert match(title) is None


@pytest.mark.parametrize("title,key", [
    ("Sony PlayStation 2 Slim PS2 Black Console Gaming System SCPH-70001 With Box",
     "ps2_console"),
    ("Sega Dreamcast White Console Power/Eject/Tested Great Condition", "dreamcast"),
    ("Nintendo Switch OLED Console White Joy-Con", "switch_oled"),
])
def test_real_consoles_survive_the_accessory_guard(title, key):
    m = match(title)
    assert m is not None and m.model.key == key


def test_link_awakening_tier_requires_the_dx_it_is_named_for():
    """🚨 A 985% GAP THAT WAS A ROUTING BUG, NOT A PRICE MOVE.

    The tier is the DX cart on Game Boy Color and its include was a bare
    `link's awakening`, which also caught the 1993 monochrome release - a
    different product at a tenth the price. Routed p25 was $4.61 against a $50
    comp, and the cheap end was wall-to-wall "Zelda Link's Awakening Nintendo
    GameBoy Japan" at $2-4.
    """
    assert match("The Legend of Zelda Link's Awakening Nintendo GameBoy Japan") is None
    for ok in ["The Legend of Zelda Link's Awakening DX Game Boy Color Cartridge",
               "Zelda Links Awakening DX Nintendo Game Boy Color Authentic Tested"]:
        assert match(ok).model.key == "zelda_link_awakening_dx", ok


def test_the_accessory_guards_are_applied_by_category_not_pasted_per_tier():
    """🚨 A guard pasted into 20 excludes is a guard the 21st tier ships
    without. Both are applied in one pass over MODELS, so this holds for any
    tier added later - including one added without reading this file."""
    from flipscout.pricebook import _CAMERA_JUNK, _CONSOLE_JUNK
    for m in MODELS:
        if m.category == "cameras":
            assert _CAMERA_JUNK in m.exclude, f"{m.key} has no camera accessory guard"
        if m.category == "videogames":
            assert _CONSOLE_JUNK in m.exclude, f"{m.key} has no console accessory guard"


def test_no_camera_or_console_comp_outlives_its_measurement():
    """Every re-measured tier carries the date and the sample that produced it."""
    for m in MODELS:
        if m.category not in ("cameras", "videogames"):
            continue
        assert m.measured >= "2026-07-28", f"{m.key} has no measurement date"
        assert m.sample > 0, f"{m.key} shipped on sample=0"


# --- the 2026-08-19 Pokemon + iPod pass --------------------------------------

@pytest.mark.parametrize("title", [
    # 🚨 THREE-SIDED CONTAMINATION, all real, all from 1,460 routed solds.
    # A different REGION (a Japanese cart will not play on English hardware):
    "Pokemon Red Nintendo GameBoy Japan - BC3662",
    "Pokemon Sapphire Nintendo GameBoy Advance Japan - BC4416",
    # ACCESSORIES that carry the game's name:
    "Pokemon Crystal Version Cartridge Shell - Nintendo Game Boy Color GBC",
    "GameShark Special Edition for Pokemon Crystal Game Boy Color Untested",
    "Pokemon Ruby Version Instruction Booklet Manual Nintendo Game Boy Advance",
    # TRADING CARDS - the TCG sets are NAMED after the games, so a card carries
    # the cart's exact name. The set-number form (8/100) is the reliable tell.
    "Pokemon Crystal Guardians - Manectric 8/100 Holo Rare Swirl",
])
def test_a_pokemon_cart_tier_never_prices_a_non_cart(title):
    assert match(title) is None


def test_a_console_sold_with_a_cart_prices_as_the_console():
    """🚨 THE INVERSE OF `_console_include`, AND IT HAD NEVER BEEN WRITTEN.

    That helper stops a CONSOLE tier matching a game. Nothing stopped a GAME
    tier matching a console - and the console IS the price. These two sat at or
    below their tier's measured p25, so they were setting the floor:
        $99.00  Nintendo Game Boy Color Clear Atomic Purple CGB-001 W/ Pokemon
       $120.00  Nintendo Game Boy Advance SP AGS-001 Console w/ Pokemon Ruby
    """
    m = match("Nintendo Game Boy Advance SP AGS-001 Console w/ Pokemon Ruby")
    assert m is not None and not m.model.key.startswith("pkmn_")
    assert match("Nintendo Game Boy Color Clear Atomic Purple CGB-001 W/ Pokemon "
                 "Crystal") is None or not match(
        "Nintendo Game Boy Color Clear Atomic Purple CGB-001 W/ Pokemon Crystal"
    ).model.key.startswith("pkmn_")


@pytest.mark.parametrize("title,key", [
    ("Pokemon Emerald Version Game Boy Advance GBA Authentic Loose Cartridge",
     "pkmn_emerald"),
    ("Pokemon Crystal Version Nintendo Game Boy Color GBC Tested", "pkmn_crystal"),
    # 🚨 DELIBERATE: a junk-titled lot naming a payable cart still alerts. A
    # bare `lot` exclusion was tried and reverted - see the note in _PKMN_JUNK.
    ("Lot of 10 Game Boy Advance games incl Pokemon Emerald tested", "pkmn_emerald"),
    # ...and a cart that merely INCLUDES a case is still a cart. Same shared-noun
    # lesson as the camera `cap`.
    ("Pokemon Emerald Version GBA w/ case for sale", "pkmn_emerald"),
])
def test_a_real_cart_survives_the_pokemon_guard(title, key):
    m = match(title)
    assert m is not None, f"{title!r} was wrongly rejected"
    assert m.model.key == key


def test_the_pokemon_comps_were_deliberately_not_moved():
    """🚨 THE GUARD WAS THE FIX; THE NUMBER WAS NOT TOUCHED, ON PURPOSE.

    After cleaning, the routed samples came out at n=10-52 with reproduction
    carts still in the cheap tail that no title distinguishes ($5.50 for a Blue
    that really sells $40+). That is not a floor worth moving a live ceiling
    on, in either direction, so every Pokemon comp is unchanged and each note
    records what the measurement said and why it was not acted on.
    """
    assert BY_KEY["pkmn_rby"].comp == 50.15
    assert BY_KEY["pkmn_ruby_sapphire"].comp == 71.99
    assert BY_KEY["pkmn_crystal"].comp == 145.28
    assert BY_KEY["pkmn_emerald"].comp == 108.75
    assert BY_KEY["pkmn_firered_leafgreen"].comp == 95.00
    # ...but the two that carried NOTHING behind them now carry a sample.
    assert BY_KEY["pkmn_rby"].sample == 52
    assert BY_KEY["pkmn_ruby_sapphire"].sample == 10


def test_the_ipod_block_was_floored_on_tiny_samples():
    """Every surviving iPod tier came down; the old comps rested on n=8 to 21."""
    for key, comp in (("ipod_classic_160", 100.00), ("ipod_classic_120", 100.00),
                      ("ipod_classic_80", 84.44), ("ipod_classic_nocap", 85.00)):
        assert BY_KEY[key].comp == comp, key
        assert BY_KEY[key].measured == "2026-08-19", key
    # the catch-all gained the biggest sample in the whole book
    assert BY_KEY["ipod_classic_nocap"].sample == 566

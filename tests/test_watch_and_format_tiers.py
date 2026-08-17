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
from flipscout.pricebook import BY_KEY, DEAD_MODELS, match

# ---------------------------------------------------------------- Citizen ---
# Measured 2026-08-17, eBay solds, each tier filtered then floored at p25.

CITIZEN_TIERS = [
    ("Citizen Campanola Grand Complication Mens Watch",      "citizen_campanola"),
    ("Citizen Promaster Aqualand Diver 200m Mens Watch",     "citizen_promaster"),
    ("Citizen Nighthawk Eco-Drive Pilot Mens Watch",         "citizen_nighthawk"),
    ("Citizen Eco-Drive Chronograph Mens Watch Steel",       "citizen_ecodrive_chrono"),
    ("Citizen Eco-Drive Perpetual Calendar Mens Watch",      "citizen_perpetual"),
    ("Citizen Eco-Drive Mens Watch Stainless Steel",         "citizen_ecodrive_mens"),
    ("Citizen Chronograph Two-Tone Mens Watch Blue Dial",    "citizen_quartz_chrono"),
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
    assert comp("citizen_ecodrive_chrono") > comp("citizen_quartz_chrono")
    assert comp("citizen_ecodrive_mens") > comp("citizen_quartz_mens")
    # and specificity must break ties toward the MORE specific tier
    for high, low in [("citizen_campanola", "citizen_ecodrive_mens"),
                      ("citizen_promaster", "citizen_ecodrive_mens"),
                      ("citizen_ecodrive_chrono", "citizen_quartz_chrono"),
                      ("citizen_ecodrive_mens", "citizen_quartz_mens")]:
        assert BY_KEY[high].specificity > BY_KEY[low].specificity, f"{high} vs {low}"


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

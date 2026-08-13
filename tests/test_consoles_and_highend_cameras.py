"""2026-07-30: video-game consoles + high-ticket cameras (measured comps).

Consoles beat rare carts: no repro risk, testable in person. Every comp is a
conservative FLOOR because each search population carried a cheaper cohort.
"""

from flipscout.pricebook import match, search_terms


# --- the model is (still) the trade -----------------------------------------

def test_backlit_sp_prices_above_the_plain_sp():
    m101 = match("Nintendo Game Boy Advance SP AGS-101 Backlit Silver")
    m001 = match("Nintendo Gameboy Advance SP AGS001 Cobalt Blue")
    assert m101.model.key == "gba_sp_101"
    assert m001.model.key == "gba_sp"
    assert m101.model.comp > m001.model.comp * 1.5    # the backlight IS the trade


def test_plain_sp_title_never_prices_as_the_101():
    assert match("Gameboy Advance SP with charger tested").model.key == "gba_sp"


def test_switch_oled_needs_the_console_not_an_accessory():
    assert match("Nintendo Switch OLED Console w/ Dock Tested").model.key == "switch_oled"
    # Accessory phrasings scoped to this model - never the universal guard.
    assert match("Carrying Case for Nintendo Switch OLED") is None
    assert match("Joy-Cons only for Switch OLED pair") is None
    assert match("Nintendo Switch Lite Coral") is None


def test_gamecube_requires_console_noun_so_games_never_price_as_hardware():
    assert match("Nintendo GameCube Console DOL-101 Black").model.key == "gamecube_console"
    assert match("Super Mario Sunshine (GameCube, 2002)") is None
    assert match("Nintendo Wii Console GameCube Compatible") is None


def test_n64_requires_console_noun():
    assert match("Nintendo 64 Console with cables and controller").model.key == "n64_console"
    assert match("GoldenEye 007 Nintendo 64 cartridge") is None


def test_3ds_xl_matches_but_2ds_does_not():
    assert match("New Nintendo 3DS XL Galaxy Style").model.key == "n3ds_xl"
    assert match("Nintendo 2DS XL Black Turquoise") is None


# --- high-ticket cameras -----------------------------------------------------

def test_x100_marks_do_not_bleed_into_each_other():
    assert match("Fujifilm X100V Digital Camera Silver").model.key == "fuji_x100v"
    assert match("Fujifilm X100F 24.3MP Compact").model.key == "fuji_x100f"
    # The newer VI must not price at the V's comp (word boundary does the work).
    assert match("Fujifilm X100VI 40MP") is None
    # X100S/T sell ~$500-700 - deliberately unpriced.
    assert match("Fujifilm X100S compact camera") is None


def test_5d_mark_iii_never_swallows_mark_ii_or_iv():
    assert match("Canon EOS 5D Mark III Body 22.3MP").model.key == "canon_5d3"
    assert match("Canon EOS 5D Mark II body") is None
    assert match("Canon EOS 5D Mark IV body") is None


def test_contax_t2_and_a6000_and_gopro_price():
    assert match("CONTAX T2 35mm Carl Zeiss Sonnar").model.key == "contax_t2"
    assert match("Sony Alpha a6000 Mirrorless w/ 16-50mm").model.key == "sony_a6000"
    assert match("GoPro HERO 11 Black Action Camera").model.key == "gopro_hero11"
    assert match("GoPro Hero 11 Session mount bundle") is None


def test_console_and_camera_search_terms_are_swept():
    terms = search_terms()
    for t in ("nintendo switch oled", "gameboy advance sp", "gamecube console",
              "fujifilm x100", "contax t2", "sony a6000", "canon 5d"):
        assert t in terms


# --- 2026-08-13: Contax T2 accessories were pricing as the $1,100 camera ----
# `contax` was never added to the universal "for <camera brand>" tell when
# the T2 model landed, and "data back" / a titan-or-gold "cover" / a leather
# case weren't in the accessory-as-product list either. All three live
# titles below carried $737-$837 max bids before this fix.

def test_contax_t2_accessories_rejected():
    assert match("[ Top MINT in Box ] Contax T2 Data Back Silver for T2D") is None
    assert match("[Near MINT] Contax T2/T2D Semi Hard Leather Case From "
                 "JAPAN") is None
    assert match("Rare [UNUSED] Gold Titan Cover for Contax T2 Compact "
                 "35mm") is None


def test_contax_t2_camera_and_bundle_still_price():
    # the camera itself, and a camera legitimately sold WITH its case, must
    # both keep matching - the accessory guard must not overreach.
    assert match("Contax T2 35mm Point & Shoot Film Camera "
                 "Titanium").model.key == "contax_t2"
    assert match("Contax T2 w/ Leather Case From Japan").model.key == "contax_t2"

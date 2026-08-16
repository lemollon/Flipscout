"""The 2026-08-16 platform pack: PlayStation, Xbox, Switch, DS-family, carts.

Origin: Leron sent four live ShopGoodwill links and asked why Flipscout was
missing "so many video game console". A census that day found the book matching
**18 of 1,117** live Goodwill game listings (1.6%) - the videogames book was
eight models and every one was Nintendo. Three of his four links were already
being FETCHED by existing search terms and died at match(); one was reached by
no term at all.

These tests pin the three things that made it a miss rather than a judgment
call, because each is a shape that will recur:
  1. a neighbouring model's `exclude` silently swallowing a product we sell
     (Switch Lite, 2DS XL),
  2. a platform having no model at all (PlayStation, Xbox, base Switch),
  3. a listing never entering the sweep (search_terms coverage).
"""

import re

import pytest

from flipscout.bidding import advise
from flipscout.pricebook import BY_KEY, DEAD_MODELS, MODELS, match, search_terms

PACK = [
    "ps5_console", "ps4_pro", "ps4_console", "ps3_console", "ps2_console",
    "psp_console", "xbox_series", "xbox_one", "wiiu_console", "switch_base",
    "switch_lite", "n2ds_xl", "dsi_xl", "n3ds_base", "steam_deck",
    "gba_original", "zelda_minish_cap", "castlevania_aria", "zelda_oracle",
    "zelda_link_awakening_dx",
]


# --- the four listings that started it ---------------------------------------
# Real titles, pulled from the ShopGoodwill detail API on 2026-08-16. Kept
# verbatim (typography and all) because paraphrasing a regression title is how
# you end up testing a bug you already fixed.

LERON_274075162 = ("Nintendo Game Boy Advance Pokemon Edition AGB-001 Yellow "
                   "Blue Handheld Console")
LERON_273477486 = ("Lot Of Assorted Nintendo Gameboy/Gameboy Advance Games & "
                   "Case (B67A)")
LERON_273589008 = "Nintendo Gameboy Games 5pc"
LERON_273759772 = ("Nintendo Game Boy Pocket Mgb-001 Silver Handheld Console "
                   "Lot 6 Games")


def test_the_original_gba_now_prices_instead_of_vanishing():
    """#274075162. Was fetched by "pokemon game boy advance" and dropped: the
    book knew AGS-001/AGS-101 (the SP) and had no entry for the original
    AGB-001 at all."""
    m = match(LERON_274075162)
    assert m is not None, "the listing that prompted the whole pack"
    assert m.model.key == "gba_original"


def test_gba_pokemon_edition_is_the_console_not_a_pokemon_cart():
    """"Pokemon Edition" is a SHELL COLOUR. The pokemon models all require a
    game name right after the word, so this must not price as a $50-145 cart."""
    assert not re.search(r"^pkmn_", match(LERON_274075162).model.key)


def test_a_games_lot_with_a_case_is_still_rejected():
    """#273477486. Correctly stays None: it is a cartridge lot, and `case` in
    the title is the accessory tell. Adding gba_original must not turn every
    lot that says "Gameboy Advance" into a console alert."""
    assert match(LERON_273477486) is None


def test_unnamed_game_lot_stays_unpriced():
    """#273589008. Names no model, so nothing can price it - that part is
    correct behaviour and the pack does not change it. What WAS wrong is that
    no search term reached the listing at all; see the search_terms test."""
    assert match(LERON_273589008) is None


def test_game_boy_pocket_is_refused_with_a_number():
    """#273759772. Measured $49.90 (n=154) -> a $6.29 ceiling. The pack's job
    here is to say no *out loud* rather than stay silent."""
    assert match(LERON_273759772) is None
    hits = [why for pat, why in DEAD_MODELS.items()
            if re.search(pat, LERON_273759772.lower())]
    assert any("Game Boy Pocket" in why for why in hits)


def test_every_listing_leron_sent_is_reachable_by_some_search_term():
    """The other half of the audit: match() can only reject what the sweep
    fetched. Three of the four already had a term; #273589008 did not, which
    is why "nintendo games" was added."""
    terms = [t.lower() for t in search_terms()]
    assert any("pokemon game boy advance" == t for t in terms)
    assert any("gameboy game lot" == t for t in terms)
    assert any("nintendo handheld lot" == t for t in terms)
    assert "nintendo games" in terms, "the term that reaches #273589008"


# --- the neighbour-exclude bug, both instances -------------------------------

def test_switch_lite_is_no_longer_eaten_by_switch_oled():
    """switch_oled's exclude carries `\\blite\\b`, so before 2026-08-16 a Switch
    Lite ($105.50 median, n=136) was ACTIVELY REJECTED, not merely unpriced."""
    assert match("Nintendo Switch Lite Turquoise 32GB Handheld").model.key == "switch_lite"
    assert match("Nintendo Switch OLED Console w/ Dock").model.key == "switch_oled"


# --- the noun rule, and the evidence for it ----------------------------------

@pytest.mark.parametrize("title", [
    # Every one of these is a real shape pulled from the live ShopGoodwill
    # census on 2026-08-16, and every one MATCHED under the first cut of this
    # pack - a game or an accessory quoted against a console comp.
    "Battlefield 4 Xbox One Video Game",
    "Microsoft Xbox One Video Game Lot",
    "Sony Playstation 3 Video Game Lot",
    "The Last of Us Part II Mortal Kombat X PS4 Game Lot PlayStation 4 Untested",
    "Wallace & Gromit in Project Zoo PlayStation2 Game (Untested)",
    "PSP Game Bundle: Pirates of the Caribbean, Wipeout Pure",
    "Nintendo 3DS Game Cartridges LEGO Star Wars Batman Angry Birds",
    "Nintendo Switch Dock Joy-Con Controller Racing Wheels Wrist Straps Set",
    "Lot of Nintendo Switch Accessories and PowerA Controller",
    "Warner Bros. LEGO Dimensions Video Game For Nintendo Wii U Not Tested",
    "Nintendo Switch Wii Wii U LAN Adapter HC-WII076 (Third Party)",
    "FIFA 22 PlayStation 5 PS5 EA Sports Soccer Game Untested",
])
def test_a_games_title_never_prices_as_the_console(title):
    """🚨 The 218-listing bug. Every platform sells far more GAMES than
    consoles and the game's title always names the console, so the platform
    name alone can never be evidence of hardware."""
    assert match(title) is None


@pytest.mark.parametrize("title", [
    "Yellow Nintendo Switch Lite",
    "Pokemon Nintendo 2DS XL",
    "Sony PlayStation 4 Slim 500GB",
])
def test_bare_platform_names_are_deliberately_not_enough(title):
    """These ARE hardware and we deliberately miss them.

    Audited against the live census on 2026-08-16: for every platform in the
    pack, listings carrying the bare name and no hardware noun were dominated
    by games and accessories (PS4 73/85, Switch 67/76, Xbox One 42/49). Even
    the platforms that looked safe were tiny samples whose "clean" rows turned
    out to be a replica mini-fridge, a grip and a docking station.

    So the noun is required everywhere, and the cost is a handful of terse
    hardware titles like these. That trade is correct in this direction: a
    false negative costs one skipped listing, a false positive quotes a real
    max bid on a $10 game.
    """
    assert match(title) is None


@pytest.mark.parametrize("title", [
    # All real, all were sitting on the live board on 2026-08-16 priced against
    # a WORKING comp, because every model carried its own `for parts|parts only`
    # and not one of them matched the phrasing sellers actually use.
    "PS3 Console CECH-3001A Parts or Repair",
    "Nintendo Switch Video Game Console Used Parts/repair",
    "Nintendo DSi XL Midnight Blue UTL-001-Untested P/R",
    "Nintendo Switch Lite Video Game Console Parts and Repair",
    "Sony PlayStation 2 PS2 Console SCPH-30001 Parts or Repair",
    "Sony Playstation 4 Ps4 Video Game Console Used Parts Repair",
    "Microsoft Xbox Series X 1TB Black - DEFCETIVE UNIT ONLY",
    "Nintendo Switch Console does not power on",
    "Nintendo DSi XL / 3DS XL / Game Boy Advance SP Console Farop Parts Or Repair",
])
def test_known_dead_hardware_never_prices(title):
    """🚨 DEAD_HARDWARE is universal because per-model excludes DO NOT COMPOSE.

    The comps are all measured with parts listings excluded, so quoting a dead
    unit against one is wrong by the entire working-vs-dead spread.
    """
    assert match(title) is None


@pytest.mark.parametrize("title", [
    "Sony PlayStation 4 PS4 500GB Black Console CUH-1215A Untested",
    "Nintendo Switch Lite Turquoise Handheld Console sold as is",
    "Sony PSP 2000 Ice Silver Handheld System - untested, sold as-is",
    "Nintendo DSi XL Metallic Rose Console Bundle w Charger",
])
def test_untested_and_as_is_still_price(title):
    """The other half of the guard, and the one that costs money if it slips.

    "Untested" is the DISCOUNT WE BUY - it is the whole Seiko-automatic and DSi
    thesis. And Goodwill staples "sold as is" onto working and broken lots
    alike, so banning it would blind the book to a large share of its best
    source. Neither belongs in DEAD_HARDWARE, only assert-dead language does.
    """
    assert match(title) is not None


def test_standalone_matches_agrees_with_match_after_the_guard_hoist():
    """`match()` skips `Model.matches` and calls `_body_matches`, having run the
    universal guards once itself. That split is a speed fix (3.32 -> 0.33
    ms/listing) and it introduces exactly one way to be wrong: the two paths
    disagreeing. This pins them together.
    """
    from flipscout.pricebook import MODELS, normalize, universally_excluded

    corpus = [
        # ordinary hardware
        "Sony PlayStation 4 PS4 500GB Black Console CUH-1215A",
        "Nintendo Switch Lite Turquoise 32GB Handheld",
        "Microsoft Xbox Series X 1TB Video Game Console",
        # trips ACCESSORY_EXCLUDE
        "Pokemon Emerald Version Official Game Guide - Prima Games GBA",
        "Hard Case Compatible with Texas Instruments TI-84 Plus CE",
        # trips DEAD_HARDWARE
        "PS3 Console CECH-3001A Parts or Repair",
        "Nintendo Switch Console does not power on",
        # matches nothing at all
        "Nintendo Gameboy Games 5pc",
        "",
    ]
    for title in corpus:
        t = normalize(title)
        for m in MODELS:
            standalone = m.matches(title)
            via_match = bool(t) and not universally_excluded(t) and m._body_matches(t)
            assert standalone == via_match, f"{m.key} disagrees on {title!r}"


@pytest.mark.parametrize("title", [
    # "eXtremeRate Switch OLED Shell Clear Purple" earned a [buy] alert against
    # the $175 switch_oled comp on the live board 2026-08-16. `shell only` was
    # in the guard; a bag of plastic that doesn't say "only" was not.
    "eXtremeRate Switch OLED Shell   Clear Purple",
    "Game Boy Advance SP Replacement Buttons",
    "Nintendo Switch OLED Replacement Housing Kit",
    "GBA SP Shell Kit Clear Blue",
    "Nintendo Switch Lite Button Set",
])
def test_replacement_shells_and_parts_never_price_as_hardware(title):
    assert match(title) is None


@pytest.mark.parametrize("title,key", [
    # 🚨 The guard is anchored to aftermarket/part words, NOT a bare `\bshell\b`.
    # A console described as having a damaged shell is still a console.
    ("Nintendo Switch OLED Console w/ Dock, cracked shell", "switch_oled"),
    ("Nintendo Game Boy Advance SP AGS-101 Backlit Silver", "gba_sp_101"),
    ("New Nintendo 3DS XL Galaxy Style", "n3ds_xl"),
    ("Gameboy Advance SP with charger tested", "gba_sp"),
    # PCH-1101 / PCH-1104 are real Vita hardware that the old
    # `pch-?\s*[12]0\d\d` missed. Game SKUs are PCSE-/PCSB-.
    ("untested as-is ps vita MODEL.PCH-1101", "ps_vita"),
])
def test_the_part_guard_does_not_eat_real_hardware(title, key):
    m = match(title)
    assert m is not None, f"{title!r} is real hardware and must still price"
    assert m.model.key == key


def test_variant_models_deliberately_keep_bare_name_matching():
    """🚨 The noun rule is for BASE platform names, not variant names, and the
    distinction is measured - do not "finish the job" by applying it to these.

    Games are labelled with the base platform ("Nintendo 3DS", "Nintendo
    Switch"), never the variant ("3DS XL", "Switch OLED"), so a variant name
    does not attract the 218-listing game problem that motivated the rule. What
    it DOES attract is accessories, which is the accessory guard's job.

    Applying the noun rule to these six on 2026-08-16 was tried and REVERTED:
    measured against 315 live listings it cost n3ds_xl two real listings while
    killing zero junk, and cost gba_sp one. The shell that prompted it is now
    caught by the part guard instead, where it belongs.
    """
    for key in ("switch_oled", "gba_sp", "gba_sp_101", "n3ds_xl",
                "ps_vita", "dreamcast"):
        assert not BY_KEY[key].include.startswith("^(?="), (
            f"{key} was given the noun rule; see this test's docstring")


@pytest.mark.parametrize("title", [
    # All real, all live on the PRODUCTION board 2026-08-16, all quoted against
    # a whole-machine comp. Parts sellers are the most prolific listers on
    # eBay's fixed-price shelf, so this lands hardest on the BUY-IT-NOW feed.
    "Singer  221 featherweight sewing Machine feed dogs",
    "Singer  222k 221 featherweight sewing Machine balance wheel",
    "Singer  221 featherweight sewing Machine bottom cover",
    "Singer 222 221 featherweight sewing Machine faceplate side access",
    "Singer 221 Featherweight Feed Dog SIMANCO 125261 Vintage Sewing Machine Part",
    "SINGER 221 Featherweight Stop Motion Knob In Black",
    "Canon Powershot G7X G7 X Mark II III Spring Lens Holder Plastic Ring",
    "Canon Base Plate AE-1 AT-1 Camera Replacement Part",
    "1PC Housing Shell Set For Fluke 323 Clamp Meter Front & Back Cover",
])
def test_component_parts_never_price_as_the_whole_machine(title):
    """A named component reads almost exactly like the machine it belongs to.

    `simanco` is Singer's own parts marking and the highest-signal word here.
    """
    assert match(title) is None


@pytest.mark.parametrize("title", [
    # 🚨 Generic words like "cover" and "plate" are deliberately NOT in the
    # guard - only component nouns that are never sold AS the product.
    "Singer 221 Featherweight Sewing Machine with case and attachments",
    "Canon AE-1 Program 35mm SLR with 50mm f/1.8 lens",
    "Fluke 87V True RMS Multimeter tested working",
    "Pentax K1000 35mm SLR camera with body cap",
])
def test_the_component_guard_does_not_eat_whole_machines(title):
    assert match(title) is not None, f"{title!r} is the real product"


def test_a_model_number_stands_in_for_the_noun():
    """The escape hatch for terse titles: a hardware model number is evidence
    on its own, because no game carries one."""
    assert match("Sony PlayStation 2 SCPH-70012 Black").model.key == "ps2_console"
    assert match("Nintendo Switch HAC-001 Gray 32GB").model.key == "switch_base"
    assert match("Nintendo DSi XL Midnight Blue UTL-001").model.key == "dsi_xl"


def test_2ds_xl_is_no_longer_eaten_by_3ds_xl():
    """n3ds_xl's exclude carries `\\b2ds\\b`. The 2DS XL is the highest-value
    handheld measured in the pack ($229.99 median)."""
    assert match("Nintendo 2DS XL Purple Handheld Console JAN-001").model.key == "n2ds_xl"
    assert match("New Nintendo 3DS XL Galaxy Style").model.key == "n3ds_xl"


def test_base_switch_had_no_model_at_all_and_now_does():
    assert match("Nintendo Switch HAC-001 Gray Console w/ Dock").model.key == "switch_base"
    assert match("Nintendo Switch v2 Console Gaming System HAC-001(-01)").model.key == "switch_base"


# --- variants that must not price as each other ------------------------------

@pytest.mark.parametrize("title,key", [
    ("Sony PlayStation 4 Pro CUH-7015B Black Console", "ps4_pro"),
    ("Sony PlayStation 4 PS4 500GB Black Console CUH-1215A", "ps4_console"),
    ("Sony PlayStation 5 PS5 Disc Console 825GB CFI-1015A", "ps5_console"),
    ("Sony PlayStation 3 Fat PS3 80GB Console System CECHE01", "ps3_console"),
    ("Sony PlayStation 2 Slim PS2 Console Black SCPH-70012", "ps2_console"),
    ("Sony PSP 2000 64MB Ice Silver Handheld System", "psp_console"),
    ("Microsoft Xbox Series X 1TB Video Game Console", "xbox_series"),
    ("Microsoft Xbox One S 1TB Console System Only 1681", "xbox_one"),
    ("Nintendo Wii U 32GB Console with GamePad", "wiiu_console"),
    ("Nintendo DSi XL Dark Brown Console", "dsi_xl"),
    ("Valve Steam Deck 512GB Handheld Console", "steam_deck"),
])
def test_each_platform_prices_as_itself(title, key):
    m = match(title)
    assert m is not None, f"{key}: {title!r} matched nothing"
    assert m.model.key == key


def test_ps4_pro_outranks_base_ps4():
    """A Pro is worth ~1.6x a base PS4 ($149 vs $94.99 median). Both includes
    fire on a Pro title, so specificity has to break the tie the right way."""
    assert BY_KEY["ps4_pro"].specificity > BY_KEY["ps4_console"].specificity
    assert match("PlayStation 4 Pro 1TB Console").model.key == "ps4_pro"


def test_xbox_360_never_prices_as_an_xbox_one():
    """The 360 is measured and dead ($11.76 ceiling). If it leaked into
    xbox_one's include it would be quoted a $72 comp."""
    assert match("Microsoft Xbox 360 S 250GB Console") is None


def test_3ds_xl_wins_the_tie_against_plain_3ds():
    """n3ds_base's include is bare `\\b3ds\\b` and its sample is the thinnest
    shipped (n=31), so it must never outrank the XL."""
    assert BY_KEY["n3ds_base"].specificity < BY_KEY["n3ds_xl"].specificity
    assert match("New Nintendo 3DS XL Galaxy Style").model.key == "n3ds_xl"
    assert match("Nintendo 3DS Console Flame Red").model.key == "n3ds_base"


def test_gba_sp_still_beats_the_original_gba():
    """gba_original's include must not steal SP listings - the SP comps higher
    ($80/$130 vs $60) so mispricing here loses money in the wrong direction."""
    assert BY_KEY["gba_original"].specificity < BY_KEY["gba_sp"].specificity
    assert match("Gameboy Advance SP with charger tested").model.key == "gba_sp"
    assert match("Nintendo Game Boy Advance SP AGS-101 backlit").model.key == "gba_sp_101"


@pytest.mark.parametrize("title", [
    "Carrying Case for PlayStation 5 Console",
    "DualSense Controller for PS5",
    "Charging Dock for Nintendo Switch",
    "Xbox Series X Console Skin Wrap Decal",
    "Nintendo Wii U GamePad WUP-010 Tablet Only",
    "Steam Deck Dock Station",
])
def test_accessories_never_price_as_the_hardware(title):
    assert match(title) is None


# --- dead platforms: a dead CONSOLE must not condemn its CARTS ---------------

@pytest.mark.parametrize("title,fragment", [
    ("Nintendo Wii Console RVL-001 White", "Wii"),
    ("Microsoft Xbox 360 S Console 320GB", "Xbox 360"),
    ("Super Nintendo SNES Console Tested", "SNES"),
    ("Nintendo Entertainment System NES Console NES-001", "NES"),
    ("Sega Genesis Model 1601 Game Console", "Genesis"),
    ("Nintendo DS Lite Crimson Handheld System", "DS Lite"),
    ("Nintendo Game Boy Pocket MGB-001 Blue", "Game Boy Pocket"),
    ("Gameboy DMG-01 Original Tested Working", "DMG-01"),
])
def test_measured_and_refused_platforms_carry_their_number(title, fragment):
    """Each of these was measured on 2026-08-16 and failed the ship test. They
    are recorded so the next person reads a number instead of re-measuring."""
    hits = [why for pat, why in DEAD_MODELS.items() if re.search(pat, title.lower())]
    assert hits, f"{title!r} should be recorded as refused"
    assert any(fragment.lower() in why.lower() for why in hits)


@pytest.mark.parametrize("title", [
    "Super Mario Bros 3 NES Cartridge Authentic",
    "Sonic the Hedgehog 2 Sega Genesis Game",
    "Super Mario World SNES Cartridge Only",
    "Wii Sports Resort Nintendo Wii Game Disc",
])
def test_a_dead_console_does_not_condemn_its_games(title):
    """🚨 The Game Boy Color lesson, re-applied. Bare `\\bnes\\b` / `\\bwii\\b`
    would staple "this platform is dead" onto every cartridge for it - and a
    dead console says nothing about the carts that run on it."""
    hits = [why for pat, why in DEAD_MODELS.items() if re.search(pat, title.lower())]
    assert not hits, f"{title!r} is a GAME; the console verdict must not apply"


def test_pokemon_in_a_lot_still_prices_as_pokemon():
    """The lot caution carries a named-title lookahead precisely so a junk
    -titled box naming a payable cart keeps alerting cleanly."""
    m = match("Lot of 10 Game Boy Advance games incl Pokemon Emerald tested")
    assert m is not None and m.model.key == "pkmn_emerald"
    assert not m.dead_also_present


def test_the_lot_caution_fires_on_a_priceable_item_sold_as_a_lot():
    m = match("Nintendo Switch HAC-001 Console bundle with 5 games lot")
    assert m is not None and m.model.key == "switch_base"
    assert any("prices only the one item" in why for why in m.dead_also_present)


# --- book-wide invariants for the pack ---------------------------------------

def test_pack_models_all_exist_and_are_active():
    for key in PACK:
        assert key in BY_KEY, f"{key} missing from the book"
        assert BY_KEY[key].active, f"{key} must not be benched"
        assert BY_KEY[key].category == "videogames"


def test_every_pack_comp_is_measured_with_a_real_sample():
    """No repeat of the FireRed/LeafGreen trap - a $76.49 comp carried for
    three weeks on sample=0, which turned out to be LOW, not high."""
    for key in PACK:
        m = BY_KEY[key]
        assert m.measured == "2026-08-16", f"{key} comp is not from the pack measurement"
        assert m.sample >= 30, f"{key} shipped on n={m.sample}"


def test_every_pack_model_can_actually_clear_the_bar():
    """The book's standing bar: $20 target profit over $9 inbound shipping. Fire
    Emblem (GBA) was measured for this pack, yielded $0.00 here, and was cut to
    DEAD_MODELS rather than shipped at a comp that can never pay."""
    for key in PACK:
        m = BY_KEY[key]
        a = advise(m.comp, outbound_shipping=m.outbound_shipping,
                   target_profit=20.0, inbound_shipping=9.0, current_price=1)
        assert a.max_bid > 0, f"{key} can never profit"
    assert "fire_emblem_gba" not in BY_KEY
    assert any("Fire Emblem" in why for why in DEAD_MODELS.values())


def test_the_platforms_that_had_no_terms_now_have_them():
    """276 PlayStation and 125 Xbox listings were unmatched in the 2026-08-16
    census, and the console terms were Nintendo-only."""
    terms = " | ".join(search_terms()).lower()
    for needed in ["playstation 5", "playstation 4", "playstation 3",
                   "playstation 2", "xbox series x", "xbox one",
                   "nintendo switch", "switch lite", "wii u", "steam deck"]:
        assert needed in terms, f"no search term reaches {needed!r}"


def test_search_terms_have_no_duplicates():
    """Each term spends eBay Browse quota on every hourly run; the pack added
    21 of them, which is the point at which a copy-paste starts to cost money."""
    terms = [t.lower() for t in search_terms()]
    dupes = {t for t in terms if terms.count(t) > 1}
    assert not dupes, f"duplicate search terms burn quota: {sorted(dupes)}"


# --- Singer Featherweight: the parts cottage industry -------------------------
# Audited 2026-08-16 against all 36 Singer rows on the LIVE production board.
# 23 were parts and 17 of those were priced against the $200 whole-machine
# comp: "Singer Featherweight 221 Light Switch" at $18 was quoted $124 profit.
# The old exclude required the word "only" ("case only", "foot only") and real
# listings do not say it.

SINGER_MACHINES = [   # must still price
    "Singer 221 Featherweight Sewing Machine",
    'Singer "Featherweight" Sewing Machine',
    "Singer Featherweight Portable Sewing Machine 221-1",
    "Vtg Singer Featherweight 221-1 sewing machine",
    "Singer featherweight machine, not tested, no cord",
    # 🚨 a machine sold WITH its case/attachments is MORE complete, not an
    # accessory listing - these five nouns are bundle-aware for that reason
    "Antique 1939 Singer 221 Featherweight Sewing Machine w/ Case",
    "Singer 221 Featherweight Sewing Machine with case and attachments",
    "Singer Featherweight 221 Sewing Machine includes case, pedal and manual",
]

SINGER_PARTS = [      # must not price
    "LNKA Foot Control Pedal for Singer 221",
    "Singer  221 featherweight sewing Machine bottom cover",
    "Singer  222k 221 featherweight sewing Machine balance wheel",
    "Singer 222 221 featherweight sewing Machine electric switch",
    "Singer Featherweight 221 Light Switch",
    "Genuine Singer 221 Featherweight Sewing Machine Case Bottom-Mount Oil Can Holder",
    "Vintage Original Singer Featherweight 221 & Others 3 Prong Electrical Terminal",
    "Singer Featherweight Blind Stitch & Automatic Zig-Zag Attachments",
    "Vintage Singer Attachments 121897 FOR CLASS 99 AND WILL FIT FEATHERWEIGHT 221",
    "1939 Singer FEATHERWEIGHT 221 PARTS rotating HOOK with SET SCREWS",
    "Singer 221 Featherweight Sewing Machine Accessory Accessories Case Tray Only",
    "VINTAGE SINGER BUTTONHOLE ATTACHMENT ~ Fits Featherweight 221 & 222k Machines",
    "1939 SINGER vtg FEATHERWEIGHT 221 sew machine CHROME STITCH LENGTH PLATE + scr",
    "SAMPSON-MORDAN / W.S. HICKS..1897...925 / SINGER FEATHERWEIGHT...100TH",
]


@pytest.mark.parametrize("title", SINGER_MACHINES)
def test_a_whole_featherweight_still_prices(title):
    m = match(title)
    assert m is not None, f"{title!r} is a real machine"
    assert m.model.key == "singer_featherweight"


@pytest.mark.parametrize("title", SINGER_PARTS)
def test_featherweight_parts_never_price_as_the_machine(title):
    assert match(title) is None


@pytest.mark.parametrize("title", [
    # Four DOCKS were priced against the $175 switch_oled comp on the live
    # production board 2026-08-16. `dock only` was too narrow; real listings
    # say "Dock Station" or "OEM ... Dock No Cables". HEG-007 is the DOCK's
    # model number - the console is HEG-001 - so the part number settles it.
    "Nintendo Switch OLED Dock Station Model HEG-007",
    "OEM Nintendo Switch OLED Dock No Cables Model HEG-007 White X 2",
    "Nintendo Switch OLED Model Dock HEG-007 White Official Charging HDMI Dock",
    "Benazcap Kit Nintendo Switch OLED Accessories Box - Open Box",
])
def test_a_switch_dock_never_prices_as_the_console(title):
    assert match(title) is None


@pytest.mark.parametrize("title", [
    # 🚨 The other direction: a console sold WITH its dock is COMPLETE and
    # worth more, so `dock` is bundle-aware exactly like the Singer case.
    "Nintendo Switch OLED Console w/ Dock Tested",
    "Nintendo Switch OLED Console with dock and joy-cons",
    "Nintendo Switch OLED 64GB White Console HEG-001",
])
def test_a_console_bundled_with_its_dock_still_prices(title):
    m = match(title)
    assert m is not None and m.model.key == "switch_oled"

"""What each model is actually worth, and how to tell which model you're looking at.

eBay won't serve comps to a script (see ebay_ui), so the watcher can't price things
live. Instead comps are MEASURED once through the browser and pinned here with the
date and sample size, then refreshed periodically with `flipscout comp`.

The hard-won rule this file exists to enforce: **the model is the trade.** Measured
2026-07-25, same shelf at the same thrift store:

    TI-84 Plus CE   sells $56.37 (n=58)  -> nets $43.50 -> max buy $23.50   OK
    TI-83 Plus      sells $25.37 (n=59)  -> nets $16.61 -> max buy -$3.39   never

A matcher that just sees "TI-84" or "graphing calculator" will happily alert on the
one that cannot make money, so `match()` requires positive evidence of the paying
model and rejects on the cheap look-alikes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Optional


def normalize(title: str) -> str:
    """Lowercase, collapse whitespace, and fold the dash lookalikes to '-'.

    Not cosmetic: a real HiBid lot was titled "Texas instruments TI - 84 plus CE"
    with an EN-DASH, so an ASCII-hyphen pattern silently skipped it - on the
    source with the least competition, which is exactly where we can't afford to
    miss anything.
    """
    t = (title or "").lower()
    for dash in ("‐", "‑", "‒", "–", "—", "―", "−"):
        t = t.replace(dash, "-")
    return " ".join(t.split())


# How far a platform name may sit from its console/system noun. Bounded at
# three words: wide enough for "Xbox 360 S Console" and "Sega Genesis Model
# 1601 Game Console", too narrow to reach from a game's title across to an
# unrelated console noun.
_NOUN_GAP = r"(?:[^a-z0-9]+[a-z0-9]+){0,3}[^a-z0-9]+"

_HW_NOUN = r"consoles?|systems?|handhelds?"


# "w/ case", "and attachments", "includes pedal" describe a listing that is MORE
# complete, not an accessory listing. Prefix an accessory noun with this and it
# only fires when the noun stands alone as the product.
#
# Same idea ACCESSORY_EXCLUDE already uses for "w/ SD Card" on cameras, pulled
# out as a constant because the Singer exclude needs it on five different nouns
# and the inline version was becoming unreadable. The comma matters: "includes
# case, pedal and manual" reaches `pedal` through ", ", not "and ".
_BUNDLED = (r"(?<!with )(?<!w/ )(?<!w/)(?<!and )(?<!& )(?<!\+ )(?<!\+)"
            r"(?<!includes )(?<!incl )(?<!plus )(?<!, )(?<!,)")


# --- camera accessory guard, 2026-08-19 -------------------------------------
# 🚨 THE CONSOLE LESSON, RE-LEARNED ON CAMERAS. `_console_include` below exists
# because a platform name is never hardware evidence - "Battlefield 4 Xbox One"
# is a game. The camera tiers never got the equivalent guard, and it cost the
# same way: routing 4,292 sold camera listings through the book on 2026-08-19,
# the CHEAPEST quarter of `canon_ae1` was not cheap cameras at all -
#
#     $2.61  Canon Genuine Neck/Shoulder Strap For AE-1 A-1 From Japan
#     $3.99  CANON FD Body CAMERA CAP for AE-1 A-1 AT-1 AV-1 ...
#     $7.99  Canon AE-1, AE-1Program, & AT-1 Screws
#     $7.99  Canon AE-1 ... Viewfinder Eyepiece
#     $9.50  Beautiful Pentax K-1000 Camera Manual
#    $11.45  Polaroid SX-70 Land Camera Alpha SE Original Manual In English
#    $15.00  Vintage Polaroid SX-70 Tripod Mount #111
#
# - and every one of them MATCHED, so the book would quote a $2.61 strap
# against a $150 camera comp and card it as a buy. It also dragged the measured
# p25 down, which is why the comps had to be re-measured only AFTER this guard
# was in place; measuring first bakes strap prices into the floor.
#
# 🚨 ACCESSORY_EXCLUDE did not catch these. Its `\bfor\s+(canon|nikon|...)`
# guard only fires on a BRAND name, and an accessory names the MODEL instead
# ("Strap For AE-1"). Its `manual only` and `neck strap` are likewise too
# narrow for "Original Manual In English" and "Neck/Shoulder Strap".
#
# Each term below is bundle-aware via _BUNDLED, because the same word is
# legitimate when the camera comes WITH one ("AE-1 w/ Cap" is a camera; "CAMERA
# CAP for AE-1" is a cap), and `manual` carries an extra guard because "manual
# focus" describes half the film cameras ever made.
# 🚨 THE DISCRIMINATOR IS "FOR", NOT THE NOUN. A first cut listed `cap` and
# `strap` as junk outright and it rejected REAL CAMERAS: "Pentax K1000 SLR
# camera with body cap" and "K1000 ... w/ 50mm Lens w/ Lens Cap" are cameras
# that happen to include a cap. _BUNDLED does not save them either, because the
# bundling word is not adjacent - "w/ Lens Cap" puts "Lens" between "w/" and
# "cap".
#
# What actually separates them is the word FOR: an accessory states
# compatibility ("Strap For AE-1", "CAMERA CAP for AE-1 A-1 AT-1 AV-1"), a
# camera never does. So the shared nouns are junk ONLY near a "for", and the
# nouns that are never part of a camera sale are junk outright.
_CAMERA_FOR = r"[^,;]{0,45}"
_CAMERA_JUNK = (
    # shared with real listings -> only junk when sold AS "for <model>"
    rf"\bstraps?\b{_CAMERA_FOR}\bfor\b|\bfor\b{_CAMERA_FOR}\bstraps?\b|"
    rf"\bcaps?\b{_CAMERA_FOR}\bfor\b|\bfor\b{_CAMERA_FOR}\bcaps?\b|"
    rf"\bfilters?\b{_CAMERA_FOR}\bfor\b|\bfor\b{_CAMERA_FOR}\bfilters?\b|"
    # never part of a camera sale
    r"\bscrews?\b|\beyepiece\b|\binstruction\b|tripod\s*mount|kick\s*stand|"
    r"remote\s*shutter|\bcold\s*shoe\b|lens\s*hood|battery\s*door|"
    r"focusing\s*screen\b|pressure\s*plate|take\s*-?\s*up\s*spool|\bbellows\b|"
    r"\bdata\s*back\b|\bshutter\s*button\b|\bnameplate\b|\brepair\s*manual\b|"
    # "manual" is the camera's booklet, EXCEPT where it describes the camera
    # itself - "manual focus" covers half the film cameras ever made.
    rf"{_BUNDLED}\bmanual\b(?!\s*(?:focus|wind|advance|exposure|film))"
)


# 🚨 THE CARD TIERS MUST NEVER EAT A GAME. A sealed WATA- or PSA-graded
# Pokemon cartridge is a four-figure item, and "PSA 10" appears on both a slab
# and a graded game. Matching a graded Emerald against a $92 CARD comp would be
# the most expensive mistake in that block, so every pokemon-cards tier
# excludes the console vocabulary outright.
_PKMN_GAME_WORDS = (
    r"\bgba\b|game\s*boy|gameboy|\bgbc\b|\bgbа\b|cartridge|\bcart\b|"
    r"nintendo\s*(?:ds|3ds|switch|64)|\bconsole\b|\bhandheld\b|\bwata\b|"
    r"\bvga\b|\bsealed\s+game\b|\bvideo\s*game\b|\bagb-|\bcgb-|\bdmg-"
)


# --- Pokemon cart guard, 2026-08-19 -----------------------------------------
# 🚨 THE INVERSE OF `_console_include`, AND IT HAD NEVER BEEN WRITTEN.
# That helper stops a CONSOLE tier matching a game. Nothing stopped a GAME tier
# matching a console - and a console bundled with the cart is the single most
# expensive look-alike a cart tier can have, because the console IS the price.
#
# Routing 1,460 sold Pokemon listings on 2026-08-19 found the tiers contaminated
# from BOTH SIDES at once, which is why the comps looked wrong in two directions
# depending on which half you looked at:
#
#   DRAGGING DOWN - Japanese carts, a different product that will not play on
#   English hardware. 132 of 404 routed listings, and they are genuinely cheap:
#       $2.56  Pokemon Red Nintendo GameBoy Japan - BC3662
#       $8.88  Pokemon Sapphire Nintendo GameBoy Advance Japan - BC4416
#   The console tiers have excluded `japan|ntsc-j` for months; the cart tiers
#   never did.
#
#   DRAGGING UP - consoles sold WITH a cart, and accessories:
#       $99.00  Nintendo Game Boy Color Clear Atomic Purple CGB-001 W/ Pokemon
#      $120.00  Nintendo Game Boy Advance SP AGS-001 Console w/ Pokemon Ruby
#       $99.95  Pokemon Crystal Version Cartridge SHELL
#       $85.00  GameShark Special Edition for Pokemon Crystal
#       $35.40  Pokemon Ruby Version Instruction Booklet Manual
#   Every one of those sat at or below the tier's measured p25, so they were
#   setting the floor.
#
# 🚨 The hardware terms here are deliberately NARROW - console nouns and MODEL
# NUMBERS only. A plain cart listing legitimately says "Game Boy Color" in its
# title ("Pokemon Crystal Version Nintendo Game Boy Color Authentic"), so the
# platform NAME can never be the tell. Same rule as everywhere else in this
# file, pointed the other way.
_PKMN_JUNK = (
    # a different region is a different product
    r"\bjapan\w*\b|\bjpn\b|\bjap\b|ntsc-?j|japanese|\bpal\b|\beur\b|"
    # sold WITH the hardware -> the hardware is the price
    r"\bconsoles?\b|\bsystems?\b|\bhandhelds?\b|\bags-?\d|\bagb-?001|\bcgb-?001|"
    r"\bdmg-?01|\bmgb-?001|game\s*boy\s*pocket|game\s*boy\s*micro|\bsp\s*console\b|"
    r"\bgba\s*sp\b|game\s*boy\s*advance\s*sp|\bcharger\b|"
    # 🚨 NOT `\blots?\b`, AND NOT A BARE `\bcase\b`. Both were tried and both
    # broke deliberate, tested behaviour:
    #   * test_pokemon_in_a_lot_still_prices_as_pokemon requires "Lot of 10
    #     Game Boy Advance games incl Pokemon Emerald" to keep pricing - the
    #     lot caution carries a named-title lookahead precisely so a junk-titled
    #     box naming a payable cart still alerts. A multi-cart lot is admittedly
    #     not a single-cart comp; alerting on it anyway is the considered trade.
    #   * a bare `case` killed "Pokemon Emerald Version GBA w/ case", which is
    #     a cart that happens to include one. Same shared-noun lesson as the
    #     camera `cap`; `case only` is already handled by the base exclude.
    # What IS kept is the console-plus-games shape, which is unambiguous.
    r"\+\s*games?\b|&\s*(?:asst\.?\s*)?games?\b|with\s+\d+\s+games?|"
    # accessories that carry the game's name
    r"cartridge\s*shell|\bshell\s*only\b|gameshark|game\s*shark|action\s*replay|"
    r"instruction\s*booklet|\bbooklet\b|\bgamepad\b|\bcontroller\b|\bstrategy\b|"
    r"\bno\s*label\b|label\s*only|\bsticker\b|"
    # 🚨 A GRADED OR SEALED GAME IS NOT A LOOSE CART. Caught 2026-08-20 while
    # adding the card tiers: "Pokemon Emerald Game Boy Advance WATA 9.8 Sealed"
    # priced against pkmn_emerald's $108.75 loose-cart comp, and a WATA 9.8
    # sealed Emerald is a four-figure item. The grade and the seal are the
    # whole product; refusing is right until there is a measured tier for them.
    r"\bwata\b|\bvga\s*\d|\b(?:psa|bgs|cgc)\s*\d|\bgraded\b|\bsealed\b|"
    # 🚨 AND THE TRADING CARDS, WHICH ARE NAMED AFTER THE GAMES. "Pokemon
    # Crystal Guardians" and "EX FireRed & LeafGreen" are TCG SETS, so a card
    # carries the cart's exact name and matched the cart tier:
    #     $19.99  Pokemon Crystal Guardians - Manectric 8/100 Holo Rare Swirl
    #      $2.00  Pokemon Firered & Leafgreen Regular Metapod Pokemon Reversal
    #      $9.99  Pokemon Fire Red & Leaf Green 2004 Used 4 Pocket Ultra Pro Binder
    # The set-number form (8/100) is the most reliable tell a listing is a card.
    r"\b\d{1,3}\s*/\s*\d{2,3}\b|\bholo\b|\breverse\s*holo\b|\bpsa\s*\d|\bcgc\b|"
    r"\bbgs\b|\bgraded\b|\bbinder\b|\bbooster\b|\btcg\b|\bplaymat\b|\btin\b|"
    r"\bcrystal\s*guardians\b|\bswirl\b|\bpromo\b|\bfoil\b"
)


# --- console accessory guard, 2026-08-19 ------------------------------------
# The same sweep that found the camera straps found the console equivalent.
# `_console_include` demands a console noun OR a hardware model number, and the
# MODEL NUMBER BRANCH IS THE LEAK: accessories carry SCPH numbers too.
#
#     $8.00  Sony PlayStation 2 PS2 RFU Adapter SCPH-10071 Official OEM
#    $10.98  OEM Sony PS2 SLIM AC Adapter Power Supply & AV Cable SCPH-7010
#    $24.99  Sony PlayStation 2 PS2 Multitap Black SCPH-10090
#     $5.99  OEM Replacement Sega Dreamcast Authentic Exterior Screws (4Pcs.)
#    $16.90  Performance Sega Dreamcast Tremor Pak Model P-20-313
#
# Two rules, because two shapes. Nouns that are NEVER a console sale are junk
# outright. Nouns that are legitimate as bundled extras ("PS2 Console with AC
# Adapter") are junk only when NO console noun appears in the title at all -
# expressed as an anchored negative lookahead, since a bundle-aware lookbehind
# cannot survive a compound noun ("with AC adapter" puts "AC" between the
# marker and the word, the same way "w/ Lens Cap" defeated it on cameras).
_CONSOLE_JUNK = (
    r"\bmultitap\b|\brfu\b|\bmodem\b|port\s*cover|\bvga\s*box\b|"
    r"tremor\s*pak|\bscrews?\b|supply\s*board|\bexterior\s*shell\b|"
    r"^(?!.*(?:consoles?|systems?|handhelds?))"
    r".*\b(?:adapters?|power\s*supply|av\s*cables?|power\s*cords?)\b"
)


# --- watch tiering, 2026-08-17 ----------------------------------------------
# 🚨 THE MOST EXPENSIVE LESSON IN THIS FILE, RE-LEARNED THE HARD WAY.
#
# `citizen_watch` was a SINGLE $85 comp on `\bcitizen\b`, for a brand whose
# solds run $19 to $2,520. Over nine listings Leron sent on 2026-08-16/17 it
# was wrong FIVE TIMES, and in both directions:
#
#   plain quartz, worn strap      quoted $42   worth  $40-55
#   Elegance Signature            quoted $39   worth     $37   (no room at all)
#   ladies gold-tone "Seven"      quoted $41   worth     $28   (would have LOST)
#   broken two-tone chrono        quoted $47   worth     $20   (would have LOST)
#   Eco-Drive Perpetual Calendar  quoted $47   worth    $150   (would have SKIPPED a real one)
#
# That last row is the one that matters most: a brand comp is not merely
# over-generous, it also makes you WALK PAST the good stuff. This is the same
# rule the top of this file already states with TI-84 vs TI-83 - the model is
# the trade - and the watches simply never got it applied.
#
# Every tier below is measured (eBay solds, filtered per tier, floored at p25)
# and gated on the book's real bar: $20 target profit over $9 inbound. Five
# measured tiers FAILED that gate and are in DEAD_MODELS, not here.
def _citizen(tier: str) -> str:
    """Brand AND tier must both appear, anywhere in the title.

    🚨 Must be ANCHORED. The first cut was a bare lookahead prefix,
    `(?=.*\\bcitizen\\b)(campanola|...)`, which fails on every real title:
    re.search scans, and at the position of "campanola" the lookahead is
    searching FORWARD for "citizen", which already appeared BEHIND it. Every
    one of the eight tiers matched nothing. Same anchored shape as
    `_console_include`, and with the same consequence - `count_units` sees one
    match, so every listing prices as one unit, which is the safe direction.
    """
    return rf"^(?=.*\bcitizen\b)(?=.*(?:{tier})).*$"

# Ladies' pieces run roughly HALF their men's equivalents across every tier
# measured, and every ladies tier failed the profit gate. Gender in the title
# is not decoration - it is the single biggest price variable in this category.
_LADIES = (r"\bladies\b|\blady'?s?\b|\bwomen'?s?\b|\bwomens\b|\bgirls?\b|"
           r"\bfemale\b")

# 🚨 `band` AND `strap` NEED THE "for/only" TEST, NOT A BUNDLE LOOKBEHIND.
# These were `_BUNDLED\bband\b|_BUNDLED\bstrap\b`, which fails for exactly the
# reason the camera `cap` did: the bundling word is not adjacent. "Citizen
# Sport Eco Drive Chronograph LEATHER STRAP Watch" has "Leather" sitting
# between, so the lookbehind never sees "with", and a real watch was thrown
# away. Found 2026-08-20 on an item Leron had actually favourited - it carded
# with NO ceiling, and the reason was the book refusing to price it at all.
#
# Nearly every watch title names its strap ("leather strap", "rubber strap",
# "steel bracelet"), so this was silently blinding the book to a whole shape of
# listing. Seiko and G-Shock were unaffected only because they do not use this
# constant.
#
# A strap is junk when it is what is being SOLD - "Strap for Citizen", "Band
# Only" - and not when the watch merely has one.
_WATCH_JUNK = (r"\bfor citizen\b|\bfor seiko\b|compatible\s+with|"
               r"\b(?:straps?|bands?|bracelets?)\b[^,;]{0,45}\bfor\b|"
               r"\bfor\b[^,;]{0,45}\b(?:straps?|bands?|bracelets?)\b|"
               r"\b(?:strap|band|bracelet)s?\s*only\b|"
               r"\bbezel\b|crystal only|movement only|dial only|"
               r"for parts|parts only|\bbroken\b|not working")


def _console_include(platform: str, model_numbers: str = "") -> str:
    """Require POSITIVE HARDWARE EVIDENCE: a console noun near the platform
    name, or a hardware model number.

    🚨 This is the single most expensive lesson in this file and it has now been
    learned three times - GameCube ("Super Mario Sunshine (GameCube)"), N64
    ("GoldenEye 007 Nintendo 64"), and again on 2026-08-16 when the first cut of
    the platform pack shipped bare `\\bps4\\b` includes. Checked against live
    ShopGoodwill data before merge, those bare patterns matched **218 listings**
    that were games, not hardware: "Battlefield 4 Xbox One Video Game" was being
    priced against a $72 console comp, "Sony Playstation 3 Video Game Lot"
    against $80.

    Every platform sells far more GAMES than consoles, and the game's title
    always contains the console's name. So the platform name alone is never
    evidence - only the noun or the model number is.

    The two conditions are checked ANYWHERE in the title rather than near each
    other. A proximity rule was tried first and was wrong: real hardware titles
    put four or more words between the two ("Sony PSP 2000 64MB Ice Silver
    Handheld System"), and widening the window far enough to catch those makes
    it reach across a game's title anyway. Presence is the honest test - a
    listing that says "console" is selling a console.

    🚨 Because this returns a whole-title assertion, `count_units` sees exactly
    one match and every listing prices as ONE unit. That is deliberate and it
    is the safe direction: over-counting inflates the max bid directly, which
    is the expensive way to be wrong. A genuine two-console lot is quoted for
    one and simply comes in under the ceiling.
    """
    hardware = _HW_NOUN + (f"|{model_numbers}" if model_numbers else "")
    return rf"^(?=.*(?:{platform}))(?=.*(?:{hardware})).*$"


# Things that carry the product's NAME but are not the product. Caught live on
# 2026-07-25: "Pokemon Emerald Version Official Game Guide - Prima Games GBA
# Strategy Book" matched the Emerald model and was quoted a $198 max bid. It is a
# paperback worth about $15. Applied to EVERY model, because this failure mode is
# universal - guides, boxes, manuals, cases and posters all share the title.
ACCESSORY_EXCLUDE = (
    # 🚨 CAMERA-SHAPED ACCESSORIES. Caught at the TOP of the live board
    # 2026-08-19, out-ranking every real camera and crowding the other
    # categories out of the run:
    #   "Haoge THB-X2S Metal Thumb Rest Hand Grip f/ Fujifilm X100V"
    #        -> Fujifilm X100V, $1,300 comp, for a ~$30 grip
    #   "Canon NB-13L Battery Pack For G7x, G5x, G9x"
    #        -> Canon G7X, $708 comp, for a ~$20 battery
    #   "Canon G7X - Fantasea FXG7 - Macro Flip for 67mm"
    #        -> Canon G7X, $708 comp, for an underwater lens port
    # `battery grip` was excluded but a bare hand/thumb grip was not, and the
    # battery-code pattern did not cover Canon's NB- series.
    #
    # 🚨 "f/" IS THE STRONGEST SIGNAL and it is unambiguous: nothing describes
    # itself as "f/ <a camera>". Plain "for" is NOT used here - "for parts",
    # "for sale" and "for repair" are all common on real bodies.
    r"thumb\s*rest|hand\s*grip|thumb\s*grip|\bf/\s*[a-z]|"
    r"conversion\s*lens|macro\s*(?:flip|port)|"
    # 🚨 NOT a bare battery part number. The first attempt excluded any title
    # containing one and immediately broke
    # test_a_bundled_battery_is_still_a_real_listing - a REAL $19.99 Canon
    # SD630 against a $120 comp, killed because the seller named the battery
    # that came with it. That test exists because this exact mistake was made
    # once already, and a dropped deal is worse than a phantom one.
    #
    # The discriminator is the PHRASE, not the part number:
    #   "Canon NB-13L Battery Pack FOR G7x"      -> the battery IS the item
    #   "Canon PowerShot SD630 ... NB-4L Battery" -> a camera that includes one
    r"batter(?:y|ies)\s*pack\b[^,;]{0,18}\bfor\b|"
    # 🚨 CONSOLE-SHAPED ACCESSORIES. Caught on the live board 2026-08-19:
    # "3 Sega Dreamcast jump Packs" - three ~$10 rumble packs - matched
    # the $95 Dreamcast CONSOLE, because that model's include is a bare
    # `dreamcast` with no hardware-noun requirement. `controller` and
    # `memory card` were already excluded; the pack shapes were not.
    r"jump\s*pack|rumble\s*pack|vibration\s*pack|expansion\s*pak|"
    r"\bvmu\b|memory\s*pak|"
    r"strategy\s*guide|game\s*guide|player'?s?\s*guide|prima\s*games|nintendo\s*power|"
    r"\bguide\b|\bbook\b|paperback|magazine|poster|\bposter\b|sticker|decal|"
    r"\bempty\b|box only|case only|cover only|manual only|insert only|label only|"
    r"shell only|display only|\breplica\b|\bpromo\b|advertisement|\bad\b|"
    # REPLACEMENT PARTS AND SHELLS. "shell only" above was too narrow: caught on
    # the live board 2026-08-16, "eXtremeRate Switch OLED Shell   Clear Purple"
    # - a bag of plastic - earned a [buy] alert against the $175 switch_oled
    # comp. `replacement` was already guarded for cables/chargers/batteries but
    # not for the housing itself, and "Game Boy Advance SP Replacement Buttons"
    # leaked the same way against an $80 comp.
    # 🚨 Anchored to aftermarket/part words, NOT a bare `\bshell\b` - a console
    # legitimately described as having "a cracked shell" is still a console.
    # COMPONENT NOUNS. A named part of a valuable machine reads almost exactly
    # like the machine, and the parts sellers are the most prolific listers on
    # eBay's fixed-price shelf - so this shows up worst on the BUY-IT-NOW feed,
    # which is the half Leron actually wants. Live production board 2026-08-16:
    #   "Singer 221 featherweight sewing Machine feed dogs"  $11 -> $139 profit
    #   "SINGER 221 Featherweight Stop Motion Knob"          $17 -> $126 profit
    #   "Canon Powershot G7X Mark II III Spring Lens Holder" $30 -> $952 profit
    # Ten of them, quoted against whole-machine comps.
    # 🚨 `simanco` is Singer's own parts marking - the single highest-signal
    # word here. Every noun below is a COMPONENT that is never sold as the
    # product, which is why they are safe universally; generic words like
    # "cover" and "plate" are deliberately NOT here (a camera "body cover" is a
    # part, but "Pentax K1000 w/ body cap" is a camera).
    # SERVICES AND CARRY GEAR. These are the shapes that dominate a
    # PEER-TO-PEER marketplace and barely exist on Goodwill or eBay, so the
    # guard never met them until the first live Facebook sweep on 2026-08-17.
    # Ten of twenty-six Discord alerts that run were junk of this kind:
    #   "PSP & PSVita Modding services"        $60  -> $61 "profit"
    #   "Ps5-ps4-ps vita repair"               $30  -> $91
    #   "PS5/CONSOLE TRAVEL BAG"               $20  -> $250
    #   "Cannon G7X III Silicon Case"          $10  -> $597
    #   "game boy advance sp car charger"      $25  -> $39
    # 🚨 Note the first two are not objects at all - somebody selling a REPAIR
    # SERVICE. No amount of product vocabulary catches that; it needs its own
    # rule.
    # SCREEN PROTECTORS AND "FOR <handheld>" ACCESSORIES. Two more that the
    # 2026-08-17 15:17 FB sweep posted to Discord as real finds:
    #   "Scratch Tempered Glass For Gameboy Advance SP Console"  $5 -> $59
    # 🚨 That one satisfied the hardware-NOUN rule, because the accessory
    # listing itself says "Console" - the noun rule cannot save you when the
    # accessory is described by the device it fits. "tempered glass" and the
    # "for <handheld>" tell are what actually catch it.
    # 🚨 The existing "for <platform>" guard listed ps/xbox/nintendo/wii/switch
    # but NOT game boy or gba, which is how this slipped through.
    r"tempered\s*glass|screen\s*protector|\bglass\s*(screen|protector)\b|"
    r"\bfor\s+(the\s+)?(game\s*boy|gameboy|gba|game\s*cube|gamecube|nintendo\s*64|n64|psp|ps\s*vita|steam\s*deck)\b|"
    # BLING / replica jewellery slang. "Bussdown AP or G shock *** moving
    # sale" was priced $5 as a real G-Shock on the 2026-08-17 FB sweep.
    # 🚨 I first guarded this with `moving sale|garage sale|estate sale` and
    # it BROKE THE GARAGE-SALE DIGEST, which deliberately parses listings
    # titled "Estate sale" to pull book models out of the description (see
    # test_hot_prefers_book_model_over_category_word). Those sale-type words
    # are load-bearing elsewhere in the product; the bling slang is what
    # actually identifies this listing, so only that is guarded.
    r"\bbussdown\b|\bbust\s*down\b|\biced\s*out\b|\bblinged\b|"
    r"\b(repair|modding|modded|unlock(ing)?|cleaning|installation)\s+"
    r"(service|services)\b|\bservices\b|\bwe\s+(fix|repair|buy)\b|"
    r"\brepairs?\b\s*$|\bfix\s+your\b|"
    # 🚨 Bundle-aware, for the third time in this file: "Switch OLED Console
    # WITH carrying case" is a COMPLETE console and worth more. Only reject
    # when the bag/case IS the product. (Silicone skins have no bundle form -
    # nobody sells a camera "with silicone case" as an upsell - so that one
    # stays unconditional.)
    r"silicone?\s*(case|cover|skin|sleeve)|"
    rf"{_BUNDLED}travel\s*(bag|case)|{_BUNDLED}carry(ing)?\s*(bag|case)|"
    r"\bcar\s*charger\b|wall\s*charger|charging\s*(brick|block|pad)|"
    # 🚨 "no console" and "console not included" CONTAIN the word console, so
    # the hardware-noun rule reads them as evidence OF a console. This is the
    # noun rule's blind spot and it must be closed here, not per model:
    # "Nintendo switch controller no console with RGB light" priced as a
    # $120 console.
    r"\bno\s+(console|system|handheld)\b|"
    r"(console|system)\s+not\s+included|without\s+(the\s+)?(console|system)|"
    r"\bsimanco\b|feed\s*dogs?\b|balance\s*wheel|stop\s*motion\s*knob|"
    r"\bface\s*plate\b|\bbase\s*plate\b|throat\s*plate|needle\s*plate|"
    r"\bbobbins?\b|presser\s*foot|\bbottom\s*cover\b|wire\s*holder|"
    r"lens\s*holder|\bspring\s*(?:lens|clip|kit)\b|tension\s*assembly|"
    r"\bshells?\s*(only|kit|set|replacement)|replacement\s*shell|"
    r"\bhousing\s*(only|kit|set)|replacement\s*(housing|buttons?|parts?|screen)|"
    r"\bbuttons?\s*(only|set|kit)\b|shell\s*(?:case|cover)\b|"
    r"extremerate|\bmod\s*kit\b|repair\s*kit|"
    # 🚨 THE `card` RULE MOVED OUT OF HERE ON 2026-08-22 - see _CARD_MERCH
    # below. It is applied to every category EXCEPT the card tiers now, because
    # as a UNIVERSAL guard it was rejecting the card tiers themselves.
    r"keychain|plush|figure|pin\b|"
    # Accessories FOR a tool, which read almost identically to the tool. These
    # MUST live in the universal guard, not on one model: per-model excludes do
    # not compose. "Starrett No 25R Dial Indicator Contact Point Set" was rejected
    # by `dial_indicator` and then quietly matched the broader `starrett` model,
    # which priced a ~$15 bag of tips at $81.95.
    r"contact point|point set|\btips?\s*(set|kit|assortment)|"
    r"attachment only|holder only|bezel only|crystal only|"
    # MERCHANDISE that borrows a valuable product's name. The local liquidation
    # sources added 2026-07-27 sell consumer goods by the pallet, so the book now
    # meets far more of this than the thrift/auction sources ever produced.
    # Caught live on the first sweep: "1pc Pokemon Crystal Ball Pikachu Gengar"
    # matched `pokemon\s*crystal` and quoted a **$100.63 max bid on a plastic
    # ball**, against a $145 loose-cartridge comp. Same family as the Zoom
    # Winged Fluke fishing lure and the Prima strategy guide.
    r"\bball\b|\btoys?\b|figurine|bobblehead|keyring|lanyard|\bmug\b|tumbler|"
    r"t-?shirt|hoodie|blanket|pillow|puzzle|backpack|ornament|"
    # ACCESSORIES SOLD FOR a device. "case only" already existed, but the
    # retail-returns sources sell the accessory as the product, so the title
    # reads like the device itself. Both of these priced as a $23.50 TI-84 CE
    # on the first live local sweep:
    #   "Hard Case Compatible with Texas Instruments TI-84 Plus CE"  (a ~$12 case)
    #   "SCOVEE PS3 Charger Cable ... Compatible with TI-84 Plus CE" (a cable)
    # "compatible with" is the tell: nobody describes the actual device that way.
    r"compatible\s+with|\bfor\s+use\s+with\b|replacement\s+(cable|charger|battery)|"
    r"charging\s+(cable|cord|dock|station)|usb\s+cable|"
    # `charger` and `... case` are also bundle-aware (2026-07-28): a camera sold
    # "w/ battery & Charger" or "With Hard Case" is the CAMERA, while a bare
    # "Battery Charger", "Charger for ...", or leading "Hard Case ..." is the
    # accessory sold alone. The old blanket \bcharger\b rejected half the real
    # digicam listings over their own bundled charger.
    r"\bcharger\s+(only|for)\b|\bcharger\s+(cable|cord|adapter)\b|"
    r"(?<!with )(?<!w/ )(?<!& )(?<!, )(?<!and )(?<!\+ )\b(battery|wall)\s+charger\b|"
    r"screen\s+protector|"
    # `protective`/`camera` joined the adjective list and `case for` became its
    # own tell (2026-07-31): a $6.17 "Digital Camera Case for AbergBest .../
    # Canon PowerShot ELPH 180/190/Sony..." priced as a $120 ELPH - "for" sat
    # before AbergBest (unlisted brand) and neither "protective case" nor
    # "camera case" was in the alternation. `case for sale` stays legal: that's
    # how real "Game Boy w/ case for sale" titles end.
    # `leather` joined the adjective list (2026-08-13): "Semi Hard Leather
    # Case From JAPAN" and a bare "Leather Case for ..." are the accessory,
    # sold alongside a Contax T2 that legitimately reads "w/ Leather Case" -
    # same bundle trap as `hard`/`camera`, so it gets the same lookbehind.
    r"(?<!with )(?<!w/ )(?<!& )(?<!, )(?<!and )(?<!\+ )(hard|soft|carrying|travel|storage|protective|camera|leather)\s+case|"
    r"\bcase\s+for\b(?!\s+sale)|\bsleeve\b|"
    # CAMERA accessories that carry the camera's model name (added 2026-07-28,
    # all seen in the live comp sweep): lens shades "for SX-70", neck straps,
    # leatherette skins, 3D-printed AE-1 battery doors, film twin-packs, and a
    # $375 "CAMERA REPAIR SERVICE FOR CANON G7X" that would price as a camera.
    # NOT `lens cap`: "w/ 50mm Lens w/ Lens Cap" is how a real K1000 was titled,
    # and a cap sold alone can't name a model without saying "for ..." anyway.
    r"lens\s+(only|hoods?|shades?)\b|\bnd\s+filter|flash\s+diffuser|"
    r"close\s+up\s+lens|neck\s+strap|\bleatherette\b|replacement\s+cover|"
    r"battery\s+door|door\s+cover|repair\s+service|film\s+(twin\s+|double\s+)?packs?\b|"
    # "for <camera brand>" is the same accessory tell as "compatible with" -
    # nobody titles the actual camera that way. Caught LIVE on the first
    # post-merge run: "NB-13L Battery(2 Pack) and Charger(2CH) Set,Camera
    # Accessories for Canon G9..." was quoted a $970.66 max bid as a G7X Mark
    # II. Camera brands ONLY - "Donkey Kong 64 Games For Nintendo N64" is why
    # this must never grow a console name. `contax` joined 2026-08-13: it was
    # missing when the T2 model landed, and "Contax T2 Data Back Silver for
    # T2D" / "Gold Titan Cover for Contax T2" both matched contax_t2 at the
    # full $1,100 comp with no accessory tell catching them.
    r"\bfor\s+(canon|nikon|sony|fuji(film)?|olympus|panasonic|pentax|polaroid|kodak|gopro|contax)\b|"
    # More accessory-as-product phrasings caught on the same Contax T2 sweep
    # (2026-08-13): a data back is a part FOR the camera, no bundle case to
    # protect. `cover` on its own is NOT safe as a bare word though - "TI-84
    # Plus CE w/ cover" is a real bundled calculator listing - so the tell is
    # scoped to a titanium/gold cover specifically (the actual accessory
    # phrasing seen live) and gets the same bundle-aware lookbehind as `case`
    # in case a future listing reads "w/ Titan Cover".
    r"data\s*back|"
    r"(?<!with )(?<!w/ )(?<!& )(?<!, )(?<!and )(?<!\+ )(titan(ium)?|gold)\s+cover\b|"
    # A battery GRIP is an accessory that names the body it fits ("BG-E11
    # Battery Grip for EOS 5D Mark III" priced $238 as a 5D on the 2026-08-13
    # board - "for EOS 5D" dodges the for-<brand> tell by naming the MODEL).
    # Bundle-aware like `case`: "5D Mark III w/ Battery Grip" is a real camera.
    r"(?<!with )(?<!w/ )(?<!& )(?<!, )(?<!and )(?<!\+ )battery\s+grip\b|"
    # A BATTERY PACK NAMED AFTER THE BODIES IT FITS. Caught live on the board
    # 2026-08-15: "4x NP-FW50 battery Sony a6000, a6100, A6300, A6400" was
    # priced as a $350 a6000 body. It dodges every existing tell - no "for
    # <brand>" (it just lists the bodies), no "compatible with", and
    # `battery grip` is a different accessory.
    #
    # Two independent catches, because either alone leaks:
    # POSITION IS THE TELL, not the mere mention of a battery. A first pass
    # here excluded any title containing a battery part number, and it
    # immediately ate a REAL listing off the same board: "Canon PowerShot
    # SD630 Digital ELPH 6MP Camera NB-4L Battery" - a $19.99 camera against a
    # $120 comp - because the seller helpfully named the battery that came
    # with it. Over-excluding drops real inventory and nobody ever notices,
    # which makes it the more expensive mistake of the two.
    #
    # So the battery is the PRODUCT only when it LEADS:
    #  1. QUANTIFIED - "4x NP-FW50 battery ...", "2 Pack Batteries ...".
    #     A camera listing does not count its batteries.
    #  2. TITLE-INITIAL - "NP-FW50 Battery for Sony a6000", "LP-E6 Battery
    #     Canon 5D Mark III". The thing being sold is named first.
    # A battery mentioned AFTER the body ("... Camera NB-4L Battery") is an
    # included accessory and the listing still prices.
    r"^\s*\d+\s*(x|pack|pcs|pieces?)\b[^,;]{0,28}?batter(y|ies)\b|"
    r"^\s*(np|nb|lp|en|bln|bls|dmw)\s*-?\s*[a-z]?\d{1,3}[a-z]?\b[^,;]{0,20}?"
    r"batter(y|ies)\b|"
    r"^\s*batter(y|ies)\b|"
    # MERCHANDISE that borrows a camera model's name. "Canon PowerShot G7 X
    # Name Tag From Japan" was quoted against the $708.18 G7X comp on the same
    # board - it is a novelty name tag. Same family as the Prima strategy
    # guide and the Pokemon Crystal Ball.
    r"name\s*(tag|plate|badge)|\blapel\b|\bpatch\b|\bmagnet\b|\bpostcard\b|"
    r"\(\s*\d+\s*(pack|pcs|ch)\s*\)"
)

# LOOKALIKE PHRASING - a knockoff advertising itself honestly still isn't the
# product. Same failure mode as Gunne Sax STYLE (2026-07-28: "Vintage 70s
# Contempo Casuals Pink Voile Lace Gunne Sax Style Dress" quoted the $122
# comp on a lookalike), hit again in watches 2026-08-13: "Digital Gold And
# Black G-Shock Style Digital Watch" is a $2.25 no-name WR50M digital with no
# Casio branding, and matched purely because "g-shock" appeared in the title.
# NOT universal (unlike ACCESSORY_EXCLUDE): "New Nintendo 3DS XL Galaxy
# Style" is a real console color edition, so a blanket "style" ban across
# every model in the book would misfire there. Each model that needs this
# wires it into its OWN `exclude`, scoped to sit next to that model's brand
# text, the same way `gunne_sax` already does it inline.
LOOKALIKE_PHRASING = r"\bstyle\b|\bstyled\b|\binspired\b|\bhomage\b|\btype\b|look\s*alike|\breplica\b"


# KNOWN-DEAD HARDWARE. Universal, like ACCESSORY_EXCLUDE and for the same
# reason: per-model excludes DO NOT COMPOSE, so a phrase that every model needs
# to reject has to live in exactly one place.
#
# 🚨 Found 2026-08-16 by running a real sweep instead of trusting the unit
# tests. Every model in the book carried its own `for parts|parts only`, and
# NOT ONE of them caught the phrasings sellers actually use. Nine parts units
# were sitting on the live board priced against WORKING comps:
#     "PS3 Console CECH-3001A Parts or Repair"          max bid $25.13
#     "Nintendo Switch Video Game Console Used Parts/repair"   max bid $61.71
#     "Nintendo DSi XL Midnight Blue UTL-001-Untested P/R"     max bid $38.81
# The comps are measured with parts listings EXCLUDED, so quoting one against
# a working comp is wrong by the full working-vs-dead spread - the expensive
# direction. This was a pre-existing book-wide gap; the console pack only made
# it visible, because consoles are listed parts/repair far more than cameras.
#
# 🚨 "untested" is deliberately NOT here. An untested unit is the DISCOUNT WE
# BUY - it is the entire Seiko-automatic and DSi thesis. Only assert-dead
# language belongs in this list.
# 🚨 "as is" is deliberately NOT here either. Goodwill staples "sold as is"
# onto working and broken lots alike, so banning it would blind the book to a
# large share of its own best source.
def universally_excluded(t: str) -> bool:
    """The two guards that are not overridable per model, on a normalized title.

    A guide/box/poster is never the product, and a known-dead unit is never the
    product - in any category. Kept in one function so `match()` can evaluate
    them once per listing instead of once per model.
    """
    return bool(re.search(ACCESSORY_EXCLUDE, t) or re.search(DEAD_HARDWARE, t))


DEAD_HARDWARE = (
    # separator is OPTIONAL: "Console Used Parts Repair" leaked past a version
    # that required or/and/&//, caught on the live board 2026-08-16
    r"for\s*parts|parts\s*only|parts\s*(?:(?:or|and|/|&)\s*)?repair|"
    r"repair\s*(?:(?:or|and|/|&)\s*)?parts|\bp\s*/\s*r\b|"
    r"not\s*working|non[-\s]*working|doesn'?t\s*work|does\s*not\s*work|"
    r"needs?\s*(repair|work|fixing)|for\s*repair|\bsalvage\b|"
    r"won'?t\s*(turn\s*on|power|boot)|does\s*not\s*power|no\s*power\b|"
    r"\bdefective\b|\bdefcetive\b"   # the typo is real, seen on a live listing
)


@dataclass(frozen=True)
class Model:
    """One priceable thing, with the evidence behind its number."""

    key: str
    label: str
    comp: float                 # measured resale, all-in to the buyer
    measured: str               # YYYY-MM-DD the comp was pulled
    sample: int                 # n solds behind it
    include: str                # regex: positive evidence this IS the model
    exclude: str = ""           # regex: look-alikes that must NOT match
    outbound_shipping: float = 5.00
    category: str = ""
    note: str = ""
    # The eBay search that PRODUCED `comp`. Every alert links it so the claim
    # "this sells for more" is checkable in one click instead of trusted.
    # Defaults to the label when the label is already a good search.
    comp_query: str = ""
    # True when `comp` was measured with eBay's Used filter (LH_ItemCondition=3000),
    # so the link reproduces the same population.
    comp_used_only: bool = True
    # Higher wins when several models match one title. Declared, not inferred:
    # regex length is a tempting proxy and it is wrong (the base-CE pattern is
    # longer than the CE-Python one, so Python would get priced as a base CE).
    specificity: int = 0
    # Benched, not deleted. Leron 2026-08-15: "i dont want to flip clothes, i
    # like the cameras, watches video games and consoles". The apparel comps
    # below are MEASURED and were expensive to get, so they stay in the file -
    # an inactive model is skipped by the sweep and never alerts, but
    # `flipscout item` / `comp` still price it if he checks one by hand. Flip
    # this back to True to un-bench a whole category; nothing else to change.
    #
    # This is also a QUOTA decision, not just taste: the Browse pass is 71
    # terms x 48 runs/day against a 5k/day cap (see hunters.EbayBrowse), so the
    # apparel terms were spending ~15% of the daily call budget on a category
    # he won't buy. Benching them buys that budget back for the four he wants.
    active: bool = True

    def matches(self, title: str) -> bool:
        """Does this ONE model describe `title`? Safe to call standalone."""
        if not self.active:
            return False
        t = normalize(title)
        if not t or universally_excluded(t):
            return False
        return self._body_matches(t)

    def _body_matches(self, t: str) -> bool:
        """`matches` minus the universal guards, on an ALREADY-NORMALIZED title.

        Split out for speed, not taste. The guards are the same two regexes for
        every model, and `match()` loops all 93 - so evaluating them inside the
        loop ran ACCESSORY_EXCLUDE ninety-three times per listing. Measured
        2026-08-16: that alone was 3.59 ms of a 3.32 ms match() (the rest of the
        book is noise next to it), and it got worse every time a model was
        added. Hoisted into `match()`, which checks them once.

        🚨 Callers must apply `universally_excluded()` themselves first. Only
        `match()` is allowed to skip it, because it already did it.
        """
        if not self.active:
            return False
        if self.exclude and re.search(self.exclude, t):
            return False
        return bool(re.search(self.include, t))


# --- the book ---------------------------------------------------------------
# Add a model only with a measured comp. An unmeasured guess here becomes a
# confident wrong alert downstream.

MODELS: list[Model] = [
    Model(
        key="ti84ce",
        label="TI-84 Plus CE",
        comp=56.37, measured="2026-07-25", sample=58,
        # "CE" must be present. Bare "TI-84 Plus" is the monochrome model.
        include=r"ti\s*-?\s*84\s*plus\s*ce|ti\s*-?\s*84ce|ti\s*-?\s*84\s*ce\b",
        exclude=r"\bcase only\b|\bcover only\b|charger only|for parts|parts only",
        outbound_shipping=5.00, category="calculators", comp_query="TI-84 Plus CE graphing calculator",
        specificity=10,
        note="CE Python variant comps higher; treat this as the floor. "
             "SEASONAL HOLD: back-to-school demand peaks Aug 10 - Sep 5; "
             "calculators bought in July should be LISTED then, not now.",
    ),
    Model(
        key="ti84ce_python",
        label="TI-84 Plus CE Python",
        comp=70.00, measured="2026-07-25", sample=0,
        include=r"ti\s*-?\s*84\s*plus\s*ce\s*python|ce\s*python",
        exclude=r"\bcase only\b|for parts|parts only",
        outbound_shipping=5.00, category="calculators", comp_query="TI-84 Plus CE Python",
        specificity=20,
        note="ESTIMATE, not measured - verify with `flipscout comp` before trusting. "
             "SEASONAL HOLD: list Aug 10 - Sep 5 for the back-to-school peak.",
    ),
    # --- iPods (measured 2026-07-25, eBay used solds, n=58 overall) -----------
    # Far better economics than the calculators: you can pay ~4x more per unit
    # and still clear the same $20. Outbound is $6 (heavier than a calculator).
    # Risk to watch: battery and HDD health are invisible in a photo. Both are
    # replaceable commodity parts, which is WHY "untested" units are underpriced.
    Model(
        key="ipod_classic_160",
        label="iPod Classic 160GB",
        comp=100.00, measured="2026-08-19", sample=23,
        include=r"ipod\s*(classic)?[^a-z0-9]{0,6}160\s*gb|160\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only|cable only",
        outbound_shipping=6.00, category="ipods", comp_query="ipod classic 160gb",
        specificity=30,
        note="RE-MEASURED 2026-08-19 on the ROUTED population: p25 $100.00 of a "
             "$143.90 median (n=23). Was $149.99 on n=21 - the whole iPod "
             "block was floored on samples of 8 to 21, which is why every tier was over.",
    ),
    Model(
        key="ipod_classic_120",
        label="iPod Classic 120GB",
        comp=100.00, measured="2026-08-19", sample=16,
        include=r"ipod\s*(classic)?[^a-z0-9]{0,6}120\s*gb|120\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only",
        outbound_shipping=6.00, category="ipods", comp_query="ipod classic 120gb",
        specificity=30,
        note="RE-MEASURED 2026-08-19 on the ROUTED population: p25 $100.00 of a "
             "$115.12 median (n=16). Was $136.07 on n=8 - the whole iPod "
             "block was floored on samples of 8 to 21, which is why every tier was over.",
    ),
    Model(
        key="ipod_classic_80",
        label="iPod Classic/Video 80GB",
        comp=84.44, measured="2026-08-19", sample=21,
        include=r"ipod\s*(classic|video)?[^a-z0-9]{0,6}80\s*gb|80\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only",
        outbound_shipping=6.00, category="ipods", comp_query="ipod classic 80gb",
        specificity=30,
        note="RE-MEASURED 2026-08-19 on the ROUTED population: p25 $84.44 of a "
             "$99.99 median (n=21). Was $135.60 on n=11 - the whole iPod "
             "block was floored on samples of 8 to 21, which is why every tier was over.",
    ),
    Model(
        key="ipod_video_30",
        label="iPod Video 30GB (5th gen)",
        comp=100.90, measured="2026-07-25", sample=15,
        include=r"ipod\s*(classic|video)?[^a-z0-9]{0,6}30\s*gb|30\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only",
        outbound_shipping=6.00, category="ipods", comp_query="ipod classic 30gb",
        specificity=30,
    ),
    # --- iPods beyond Classic (measured 2026-08-13, eBay used solds, n=123
    # per query) -----------------------------------------------------------
    # The digest was full of Starrett/Mitutoyo/film-camera alerts while live
    # Goodwill listings that just say "iPod Classic" with no GB in the title
    # went unpriced entirely - the capacity models above all REQUIRE a GB
    # figure. This catch-all closes that leak. Specificity is deliberately
    # below the capacity models' 30 so "iPod Classic 160GB" still prices as
    # the $149.99 160GB model, not this $131.58 unknown-capacity floor.
    #
    # `\bipod\b` everywhere below, on purpose: "ipod" is a substring of
    # "tripod", and a bare `ipod` (no word boundary) would silently price a
    # camera tripod as an iPod.
    Model(
        key="ipod_classic_nocap",
        label="iPod Classic/Video (capacity unknown)",
        comp=85.00, measured="2026-08-19", sample=566,
        include=r"\bipod\s*(classic|video)\b",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only",
        outbound_shipping=6.00, category="ipods", comp_query="ipod classic",
        comp_used_only=True, specificity=15,
        note="RE-MEASURED 2026-08-19 on the ROUTED population: p25 $85.00 of a "
             "$105.00 median (n=566). Was $131.58 on n=58 - the whole iPod "
             "block was floored on samples of 8 to 21, which is why every tier was over.",
    ),
    # 🚨 ipod_nano WAS HERE AND IS NOW DEAD (2026-08-19). Re-measured on the
    # population it actually receives it is p25 $35.00 / median $50.00 (n=251, up from n=123),
    # which quotes a max bid of $0.00 against the standing gate ($20 profit
    # over $9 inbound). See DEAD_MODELS.
    # 🚨 ipod_touch WAS HERE AND IS NOW DEAD (2026-08-19). Re-measured on the
    # population it actually receives it is p25 $19.50 / median $29.99 (n=160, up from n=123),
    # which quotes a max bid of $0.00 against the standing gate ($20 profit
    # over $9 inbound). See DEAD_MODELS.

    # --- Pokemon Game Boy carts (measured 2026-07-25) ------------------------
    # The best margins in the book. TWO real dangers, both encoded below:
    #  1. REPRODUCTION CARTS ARE EVERYWHERE. Repros are the single biggest way to
    #     lose money here, and they are hard to spot in a listing photo. `exclude`
    #     catches the honest sellers who say so; nothing catches the dishonest
    #     ones, so treat every alert as "verify before bidding", not "buy".
    #  2. RE-MEASURED 2026-07-25 after the first pass was found to be badly high.
    #     Two mistakes, both worth remembering:
    #
    #     a) eBay's Used filter (LH_ItemCondition=3000) DOES NOT APPLY to video
    #        games - they use a separate taxonomy (Very Good / Good / Acceptable).
    #        With it on, "pokemon emerald" returned ONE sold listing. Hence
    #        comp_used_only=False on every cart here, which also fixes the
    #        "see what it sold for" link, which was showing an near-empty search.
    #
    #     b) The price is BIMODAL and the median sat in the empty middle:
    #             Emerald  loose $108.75 (n=36)  vs  boxed $278.13 (n=19)
    #             Crystal  loose $145.28 (n=37)  vs  boxed $194.96 (n=21)
    #        A thrift/auction find is almost always a loose cart, so the book now
    #        carries the LOOSE number. The old unsegmented Emerald comp of $271.99
    #        implied a $210 max bid on a cart that typically sells for $108.
    #
    #     Emerald and Crystal are re-measured. The remaining four are the original
    #     un-segmented numbers from a search containing "authentic" (which skews
    #     toward sellers asserting legitimacy), cut to the ~0.6 loose share
    #     observed on Emerald/Crystal and flagged sample=0 so every alert says
    #     "estimate, not measured". Re-measure them with `flipscout comp` before
    #     bidding near their ceilings.
    Model(
        key="pkmn_emerald",
        label="Pokemon Emerald (GBA)",
        comp=108.75, measured="2026-07-25", sample=36, comp_used_only=False,
        include=r"(pok[eé]mon|pokeman)\s*emerald",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon emerald gameboy advance",
        specificity=40,
        note="Routed re-measure 2026-08-19: p25 $81.00 / median $145.00 (n=10). "
             "NO COMP CHANGE. THIN. Comp held. "
             "The guard was the fix here, not the number - see _PKMN_JUNK: this "
             "tier was matching Japanese carts, consoles sold WITH the game, "
             "cartridge shells and TCG singles named after the game.",
    ),
    Model(
        key="pkmn_crystal",
        label="Pokemon Crystal (GBC)",
        comp=145.28, measured="2026-07-25", sample=37, comp_used_only=False,
        include=r"(pok[eé]mon|pokeman)\s*crystal",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon crystal gameboy color",
        specificity=40,
        note="Routed re-measure 2026-08-19: p25 $120.00 / median $189.99 (n=22). "
             "NO COMP CHANGE. Comp held at $145.28, above this p25 but well under the $189.99 median, pending a cleaner sample. "
             "The guard was the fix here, not the number - see _PKMN_JUNK: this "
             "tier was matching Japanese carts, consoles sold WITH the game, "
             "cartridge shells and TCG singles named after the game.",
    ),
    Model(
        key="pkmn_firered_leafgreen",
        label="Pokemon FireRed / LeafGreen (GBA)",
        comp=95.00, measured="2026-08-15", sample=102, comp_used_only=False,
        include=r"(pok[eé]mon|pokeman)\s*(fire\s*red|firered|leaf\s*green|leafgreen)",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon fire red gameboy advance",
        specificity=40,
        # RE-MEASURED 2026-08-15. The old $76.49 was `sample=0` - an unmeasured
        # guess carried since 7/25, and it was LOW, not high: both halves comp
        # near $104 loose. Measured separately, no "authentic" keyword (that
        # search skews toward sellers asserting legitimacy, which is exactly the
        # population that prices high):
        #     LeafGreen loose  $104.62  (n=50, p25 $80.62, p75 $125.17)
        #     FireRed   loose  $103.77  (n=52, p25 $60.00, p75 $125.61)
        #     boxed/CIB        $331-406 (n=3 - too thin to carry, and an auction
        #                                find is a loose cart anyway)
        #     Japanese import   $42-45  (n=9 - region-locked, cheap for a reason)
        # Carried at $95, a FLOOR below both medians rather than the ~$104 pooled
        # median: FireRed's p25 of $60 is a fatter cheap tail than LeafGreen's
        # $80.62, and the two share one model here.
        note="Routed re-measure 2026-08-19: p25 $69.00 / median $120.00 (n=28). "
             "NO COMP CHANGE. Comp held at $95: this p25 is dragged by TCG singles and $1.50 carts that the guard does not catch, so it reads low. "
             "The guard was the fix here, not the number - see _PKMN_JUNK: this "
             "tier was matching Japanese carts, consoles sold WITH the game, "
             "cartridge shells and TCG singles named after the game.",
    ),
    Model(
        key="pkmn_ruby_sapphire",
        label="Pokemon Ruby / Sapphire (GBA)",
        comp=71.99, measured="2026-08-19", sample=10, comp_used_only=False,
        include=r"(pok[eé]mon|pokeman)\s*(ruby|sapphire)",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon ruby gameboy advance",
        specificity=40,
        note="Routed re-measure 2026-08-19: p25 $66.00 / median $91.00 (n=10). "
             "NO COMP CHANGE. THIN. Comp held. Was carried on sample=0, which is the FireRed trap this book already learned once. "
             "The guard was the fix here, not the number - see _PKMN_JUNK: this "
             "tier was matching Japanese carts, consoles sold WITH the game, "
             "cartridge shells and TCG singles named after the game.",
    ),
    # --- Pokemon TRADING CARDS, measured 2026-08-20 --------------------------
    # 🚨 CATEGORY "pokemon-cards", NOT "pokemon". The cart tiers carry
    # _PKMN_JUNK, which exists to keep TCG singles OUT of them - giving these
    # the same category would apply that guard to itself and reject every card
    # on sight.
    #
    # 🚨 AND THEY MUST NOT EAT GRADED GAMES. A sealed WATA/PSA-graded Pokemon
    # cartridge is a four-figure item; matching it against a $92 card comp
    # would be the most expensive mistake in this block, so every tier here
    # excludes the console/cartridge vocabulary outright.
    #
    # Measured against 2,764 sold TCG listings. The honest finding is that most
    # of this category CANNOT be priced from a title - see the lot entries in
    # DEAD_MODELS. What survives is where the title states the two things that
    # actually drive the price: a GRADE, or a named chase card from the vintage
    # era.
    Model(
        key="pkmn_card_graded_high",
        label="Pokemon card, graded 9 or 10 (PSA/BGS/CGC)",
        comp=112.50, measured="2026-08-20", sample=111,
        include=r"(?=.*pok[eé]mon|.*\bpkmn\b)(?=.*\b(?:psa|bgs|cgc)\s*(?:10|9\.5|9)\b).*",
        exclude=_PKMN_GAME_WORDS + r"|\blot\b|\bbulk\b|\breprint\b|\bproxy\b|"
                r"\bfake\b|\bcustom\b|\bmetal\b|\bcoin\b|\bsticker\b",
        outbound_shipping=5.00, category="pokemon-cards", specificity=48,
        comp_query="pokemon psa 10 card",
        note="FLOOR at p25 $112.50 of a $249.99 median (n=111). The grade IS "
             "the comp here - a slab states its condition, which is the one "
             "thing a raw card's title never does.",
    ),
    Model(
        key="pkmn_card_graded",
        label="Pokemon card, graded (any grade)",
        comp=92.00, measured="2026-08-20", sample=256,
        include=r"(?=.*pok[eé]mon|.*\bpkmn\b)(?=.*\b(?:psa|bgs|cgc|ace)\s*(?:10|9\.5|9|8|7)\b).*",
        exclude=_PKMN_GAME_WORDS + r"|\blot\b|\bbulk\b|\breprint\b|\bproxy\b|"
                r"\bfake\b|\bcustom\b|\bmetal\b|\bcoin\b|\bsticker\b",
        outbound_shipping=5.00, category="pokemon-cards", specificity=46,
        comp_query="pokemon psa graded card",
        note="FLOOR at p25 $92 of a $200 median (n=256). Wide on purpose - a "
             "PSA 7 and a PSA 10 of the same card differ 10x, so the 9/10 tier "
             "above takes the ones that say so.",
    ),
    Model(
        key="pkmn_card_vintage_chase",
        label="Pokemon vintage single, named chase card (1999-2004)",
        comp=51.00, measured="2026-08-20", sample=142,
        include=(r"(?=.*pok[eé]mon|.*\bpkmn\b)"
                 r"(?=.*\b(?:199\d|200[0-4])\b)"
                 r"(?=.*(?:charizard|blastoise|venusaur|lugia|umbreon|espeon|"
                 r"mewtwo|\bmew\b|rayquaza|gengar|dragonite|shining)).*"),
        exclude=_PKMN_GAME_WORDS + r"|\blot\b|\bbulk\b|\(\d+\)|\bx\s?\d+\b|"
                r"\b(?:psa|bgs|cgc)\b|\breprint\b|\bproxy\b|\bfake\b|\bcustom\b|"
                r"\bjumbo\b|\bsticker\b|\bcoin\b|\bbinder\b",
        outbound_shipping=5.00, category="pokemon-cards", specificity=44,
        comp_query="pokemon vintage charizard card",
        note="FLOOR at p25 $51 of a $128.26 median (n=142). 🚨 THE THINNEST "
             "MARGIN IN THE BOOK - a $9.84 ceiling at the standing gate, so "
             "this only ever pays on a cheap local lot. Condition is the whole "
             "variable and a raw card's title does not state it; buy the "
             "picture, not the words.",
    ),

    Model(
        key="pkmn_rby",
        label="Pokemon Red / Blue / Yellow (GB)",
        comp=50.15, measured="2026-08-19", sample=52, comp_used_only=False,
        include=r"(pok[eé]mon|pokeman)\s*(red|blue|yellow)\b",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon yellow gameboy",
        specificity=40,
        note="Routed re-measure 2026-08-19: p25 $83.65 / median $129.00 (n=52). "
             "NO COMP CHANGE. Comp HELD at $50.15 below that p25 on purpose: the cheap tail still carries reproduction carts that no title tells apart ($5.50 for a Blue that really sells $40+), so the floor is not trustworthy enough to raise a ceiling on. Was carried on sample=0. "
             "The guard was the fix here, not the number - see _PKMN_JUNK: this "
             "tier was matching Japanese carts, consoles sold WITH the game, "
             "cartridge shells and TCG singles named after the game.",
    ),

    # --- Game consoles (measured 2026-07-30, eBay solds, all-in). Leron asked
    # for video games beyond the carts, with budget past $100. Consoles beat
    # rare carts here: no repro risk (the #1 loss risk on Pokemon), condition
    # is testable in person, and estate/thrift sources are full of them.
    # Comps are CONSERVATIVE FLOORS below the raw medians because every console
    # search population carries a cheaper cohort (tablet-only Switches, Wii
    # bundles in the GameCube search, game carts in the N64 search).
    Model(
        key="switch_oled",
        label="Nintendo Switch OLED console",
        comp=175.00, measured="2026-08-19", sample=91,
        include=r"switch\s*oled",
        # "for switch"/"for nintendo" is the accessory tell, scoped to THIS
        # model - console names must never enter the universal camera-brand
        # guard (the Donkey Kong lesson).
        # 🚨 `dock only` was too narrow. Four DOCKS were priced against this
        # $175 console comp on the live production board 2026-08-16 - "Nintendo
        # Switch OLED Dock Station Model HEG-007", "OEM Nintendo Switch OLED
        # Dock No Cables ... X 2". HEG-007 is the DOCK's model number; the
        # console is HEG-001, so the part number alone settles it.
        # `dock` is bundle-aware because "Console w/ Dock" is a COMPLETE
        # console and worth more - same treatment as the Singer case.
        exclude=rf"\bfor\s+(the\s+)?(nintendo|switch)\b|joy.?cons?\s+only|"
                rf"{_BUNDLED}\bdocks?\b|\bheg-?\s*007\b|{_BUNDLED}\baccessor\w*|"
                rf"\bcase\b|\bskin\b|screen protector|\bstand\b|\bgrip\b|charger only|"
                rf"tablet only|console only|\blite\b|game only|for parts|parts only|"
                rf"not working|broken",
        outbound_shipping=10.00, category="videogames",
        comp_query="nintendo switch oled console", specificity=40,
        note="FLOOR below the $201.73 median (n=60): tablet-only units sell "
             "$150-170 and are EXCLUDED - complete console w/ dock+joycons only." 
             "Re-measured 2026-08-19 on the ROUTED population (n=91, p25 $165.00, median $185.00): CONFIRMED the comp.",
    ),
    Model(
        key="gba_sp_101",
        label="Game Boy Advance SP AGS-101 (backlit)",
        comp=116.50, measured="2026-08-19", sample=168,
        include=r"ags\s*-?\s*101|backlit\s*(game\s*boy|gba|sp)|(gba|sp)\s*backlit",
        exclude=r"\bips\b|modded|custom|shell|housing|repro|for parts|parts only|"
                r"not working|broken|box only|charger only",
        outbound_shipping=5.00, category="videogames",
        comp_query="gameboy advance sp ags-101", specificity=30,
        note="FLOOR below the $136.58 median (n=47, AGS-001 bleed in the tail). "
             "The BACKLIT screen is the trade: AGS-101 vs 001 is 1.6x. Verify "
             "the label says AGS-101 in the photo." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $116.50 of a $139.99 median (n=168). Was $130.00.",
    ),
    Model(
        key="gba_sp",
        label="Game Boy Advance SP (AGS-001/unspecified)",
        comp=80.00, measured="2026-08-19", sample=130,
        # 🚨 NOUN RULE RESTORED 2026-08-17. This was bare, and on a live eBay
        # listing it priced "Super Mario Advance 4 ... Game Boy Advance SP
        # Gameboy" - a $19 CARTRIDGE - against this $80 CONSOLE comp, because
        # sellers keyword-stuff platform names into game titles.
        #
        # It had the rule on 2026-08-16 and I reverted it, which was an
        # over-correction: the per-model measurement said gba_sp was NET
        # POSITIVE (6 bare titles, 5 junk killed, 1 real listing lost) and
        # only n3ds_xl was net negative (2 real lost, 0 junk killed). I
        # reverted all six instead of the one. n3ds_xl stays bare; this does
        # not.
        include=_console_include(
            r"(?:game\s*boy|gameboy)\s*advance\s*sp", r"\bags\s*-?\s*001\b"),
        exclude=r"ags\s*-?\s*101|backlit|\bips\b|modded|custom|shell|housing|repro|"
                r"for parts|parts only|not working|broken|box only|charger only",
        outbound_shipping=5.00, category="videogames",
        comp_query="gameboy advance sp", specificity=25,
        note="FLOOR below the $84.59 median (n=51). If the photo shows AGS-101 "
             "on the label, it's the $130+ backlit model - re-check." 
             "Re-measured 2026-08-19 on the ROUTED population (n=130, p25 $80.00, median $99.00): CONFIRMED the comp.",
    ),
    Model(
        key="n3ds_xl",
        label="Nintendo 3DS XL / New 3DS XL",
        comp=145.00, measured="2026-08-19", sample=198,
        include=r"3ds\s*(xl|ll)",
        exclude=r"\b2ds\b|circle pad|cradle only|stylus only|charger only|"
                r"\bcase\b|for parts|parts only|not working|broken|box only",
        outbound_shipping=6.00, category="videogames",
        comp_query="nintendo 3ds xl console", specificity=28,
        note="FLOOR at p25 of a $209.02 median (n=59) - Japanese 'New 3DS LL' "
             "imports inflate the top. US 'New 3DS XL' sells $250+." 
             "Re-measured 2026-08-19 on the ROUTED population (n=198, p25 $174.99, median $210.00): comp is BELOW p25 and stays there - raising a ceiling is the direction that loses money.",
    ),
    Model(
        key="gamecube_console",
        label="Nintendo GameCube console",
        comp=85.00, measured="2026-07-30", sample=50,
        # Require the console/system noun or a DOL model number so games
        # titled "... GameCube" never price as the console.
        include=r"game\s*cube\s*(console|system)|\bdol\s*-?\s*0?01\b|"
                r"\bdol\s*-?\s*101\b",
        exclude=r"\bwii\b|controller only|memory card|\bcase\b|for parts|"
                r"parts only|not working|broken|box only|cover|door",
        outbound_shipping=10.00, category="videogames",
        comp_query="gamecube console", specificity=26,
        note="FLOOR below the $94.49 median (n=50, Wii-bundle contamination "
             "excluded). Orange/spice and boxed units sell well over $130.",
    ),
    Model(
        key="n64_console",
        label="Nintendo 64 console",
        comp=82.99, measured="2026-08-19", sample=32,
        include=r"(nintendo\s*64|\bn\s*-?64\b)\s*(console|system)",
        exclude=r"controller only|expansion pak only|jumper pak|\bcase\b|"
                r"for parts|parts only|not working|broken|box only|cover|door",
        outbound_shipping=10.00, category="videogames",
        comp_query="nintendo 64 console", specificity=26,
        note="FLOOR at ~p25 of a $152.83 median (n=53, game-cart bleed in the "
             "search). Funtastic colors and boxed bundles sell $180+." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $82.99 of a $119.00 median (n=32). Was $95.00.",
    ),

    # === THE PLATFORM PACK (measured 2026-08-16) ==============================
    # Leron 8/16, with four live ShopGoodwill links: "flipscout miss so many
    # video game console ... why is it missing all the deals?" Diagnosed before
    # measuring anything, per the [[flipscout-breadth-audit]] playbook:
    #
    #   census of 1,117 live Goodwill game listings -> the book matched 18. 1.6%.
    #
    # It was BOOK blindness, not feed blindness. Three of his four links were
    # already being fetched by existing search terms and were dropped at
    # match(). The videogames book was 8 models and every one of them was
    # Nintendo - no PlayStation, no Xbox, no Wii, no base Switch (only OLED),
    # and Switch Lite was ACTIVELY EXCLUDED by switch_oled's own exclude.
    # Unmatched supply, by platform, that same census:
    #   PlayStation 276 · game lots 277 · Xbox 125 · Wii/Wii U 106 · Switch 80
    #   Sega 77 · orig Game Boy 59 · DS 56 · SNES/NES 51 · PSP 46
    #
    # HOW THESE COMPS WERE SET. eBay sold search, in-page via Chrome (the API
    # can't serve solds and local creds are empty by design - see ebay_ui).
    # Each population is filtered by that model's own include/exclude BEFORE
    # the median is taken, because a raw "playstation 4 console" search is 40%
    # PS5s and accessories. Every comp here is the FILTERED p25, not the
    # median: the low tail is real (untested units, missing cords, storage
    # wear) and we buy assuming we're in it. `n` is the surviving sample.
    #
    # A model only shipped if `max_pay(comp)` beat the LIVE Goodwill median for
    # that platform by >$5 with >=5 live listings. Nine measured platforms
    # failed that test and are in DEAD_MODELS instead - they are not missing,
    # they are refused, with the number that refused them.
    Model(
        key="ps5_console",
        label="Sony PlayStation 5 console",
        comp=330.00, measured="2026-08-16", sample=93,
        include=_console_include(r"\bps5\b|playstation\s*5", r"\bcfi-\d{4}\w?\b"),
        # "for PS5" is the accessory tell. Scoped here, never universal.
        exclude=r"\bfor\s+(the\s+)?(ps5|playstation)\b|controller|dualsense|"
                r"\bcase\b|\bskin\b|\bstand\b|faceplate|charging|headset|"
                r"\bportal\b|disc drive|console only shell|for parts|parts only|"
                r"not working|broken|box only|\bdigital code\b",
        outbound_shipping=15.00, category="videogames",
        comp_query="playstation 5 console", specificity=40,
        note="FLOOR at p25 of a $409.99 median (n=93). Max pay ~$176, and 71 "
             "PlayStation listings were live on Goodwill the day this was "
             "measured against zero PlayStation models - the single biggest "
             "hole in the old book.",
    ),
    Model(
        key="ps4_pro",
        label="Sony PlayStation 4 Pro console",
        comp=128.00, measured="2026-08-16", sample=99,
        # Declared ABOVE ps4_console and given higher specificity so a Pro is
        # never priced as a base PS4 - same relationship as AGS-101 vs AGS-001.
        include=_console_include(
            r"(?:ps4|playstation\s*4)[^a-z0-9]{0,6}pro\b|"
            r"pro[^a-z0-9]{0,6}(?:ps4|playstation\s*4)", r"\bcuh-7\d{3}\w?\b"),
        exclude=r"\bfor\s+(the\s+)?(ps4|playstation)\b|controller|dualshock|"
                r"\bcase\b|\bskin\b|\bstand\b|charging|headset|vertical stand|"
                r"for parts|parts only|not working|broken|box only",
        outbound_shipping=12.00, category="videogames",
        comp_query="playstation 4 pro console", specificity=36,
        note="FLOOR at p25 of a $151.34 median (n=99). The Pro is worth ~1.6x a "
             "base PS4 - CUH-7xxx on the label is the tell.",
    ),
    Model(
        key="ps4_console",
        label="Sony PlayStation 4 console",
        comp=74.00, measured="2026-08-16", sample=82,
        include=_console_include(r"\bps4\b|playstation\s*4",
                                 r"\bcuh-[12]\d{3}\w?\b"),
        exclude=r"\bpro\b|\bcuh-7\d{3}\b|ps5|playstation\s*5|"
                r"\bfor\s+(the\s+)?(ps4|playstation)\b|controller|dualshock|"
                r"\bcase\b|\bskin\b|\bstand\b|charging|headset|camera only|"
                r"for parts|parts only|not working|broken|box only",
        outbound_shipping=12.00, category="videogames",
        comp_query="playstation 4 console", specificity=30,
        note="FLOOR at p25 of a $94.99 median (n=82). Pro EXCLUDED here and "
             "priced separately at $120 - do not merge them.",
    ),
    Model(
        key="ps3_console",
        label="Sony PlayStation 3 console",
        comp=79.00, measured="2026-08-16", sample=118,
        include=_console_include(r"\bps3\b|playstation\s*3", r"\bcech-\w+\b"),
        exclude=r"ps4|ps5|playstation\s*[45]|"
                r"\bfor\s+(the\s+)?(ps3|playstation)\b|controller|dualshock|"
                r"\bcase\b|\bskin\b|charging|headset|for parts|parts only|"
                r"not working|broken|box only",
        outbound_shipping=14.00, category="videogames",
        comp_query="playstation 3 console", specificity=30,
        note="FLOOR at p25 of a $109.99 median (n=118). The backwards-compatible "
             "fat CECHA/CECHE units are the ones that sell over $200; a Super "
             "Slim is nearer the floor. Heavy - $14 outbound, not $10.",
    ),
    Model(
        key="ps2_console",
        label="Sony PlayStation 2 console",
        comp=59.99, measured="2026-08-19", sample=43,
        include=_console_include(r"\bps2\b|playstation\s*2", r"\bscph-\d{4,5}\w?\b"),
        exclude=r"ps3|ps4|ps5|playstation\s*[345]|"
                r"\bfor\s+(the\s+)?(ps2|playstation)\b|controller|dualshock|"
                r"\bcase\b|\bskin\b|memory card|network adapter|for parts|"
                r"parts only|not working|broken|box only",
        outbound_shipping=12.00, category="videogames",
        comp_query="playstation 2 console", specificity=30,
        note="FLOOR at p25 of a $95 median (n=85). Thin margin - max pay is "
             "~$28 - but Goodwill's PS2 median is $11.99, so the room is real." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $59.99 of a $89.95 median (n=43). Was $70.00.",
    ),
    Model(
        key="psp_console",
        label="Sony PSP handheld",
        comp=66.00, measured="2026-08-16", sample=92,
        include=_console_include(r"\bpsp\b|playstation\s*portable", r"\bpsp-\d{4}\b"),
        exclude=r"\bpsp\s*go\b|\bvita\b|\bps2\b|\bwii\b|xbox|umd only|"
                r"\bfor\s+(the\s+)?psp\b|\bcase\b|\bskin\b|charger|"
                r"for parts|parts only|parts or repair|not working|broken|box only",
        outbound_shipping=6.00, category="videogames",
        comp_query="sony psp console", specificity=30,
        note="FLOOR at p25 of a $100 median (n=92). PSP Go is a different "
             "(scarcer) product and is excluded rather than priced here.",
    ),
    Model(
        key="xbox_series",
        label="Xbox Series X / S console",
        comp=345.00, measured="2026-08-16", sample=106,
        include=_console_include(r"xbox\s*series\s*[sx]\b", r"\bmodel\s*188\d\b"),
        exclude=r"xbox\s*360|xbox\s*one|\bfor\s+(the\s+)?xbox\b|controller|"
                r"\bcase\b|\bskin\b|\bstand\b|faceplate|charging|headset|"
                r"for parts|parts only|not working|broken|box only",
        outbound_shipping=16.00, category="videogames",
        comp_query="xbox series x console", specificity=40,
        note="FLOOR at p25 of a $449 median (n=106). Series S sells well "
             "under Series X; the floor is set so an S still clears.",
    ),
    Model(
        key="xbox_one",
        label="Xbox One / One S / One X console",
        comp=63.92, measured="2026-08-19", sample=53,
        include=_console_include(r"xbox\s*one\b", r"\bmodel\s*1(?:540|681)\b"),
        exclude=r"xbox\s*360|series\s*[sx]\b|\bkinect\b|"
                r"\bfor\s+(the\s+)?xbox\b|controller|\bcase\b|\bskin\b|"
                r"\bstand\b|charging|headset|for parts|parts only|"
                r"not working|broken|box only",
        outbound_shipping=14.00, category="videogames",
        comp_query="xbox one console", specificity=30,
        note="FLOOR at p25 of an $89 median (n=103). Xbox 360 is MEASURED AND "
             "DEAD ($69.95, max pay $11.76) - see DEAD_MODELS. Keeping 360 out "
             "of this include is what makes the model safe." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $63.92 of a $89.99 median (n=53). Was $71.00.",
    ),
    Model(
        key="wiiu_console",
        label="Nintendo Wii U console",
        comp=69.00, measured="2026-08-16", sample=158,
        include=_console_include(r"wii\s*u\b", r"\bwup-10\d\b"),
        exclude=r"gamepad only|tablet only|\bfit\b|balance board|"
                r"\bfor\s+(the\s+)?wii\b|\bcase\b|\bskin\b|"
                r"for parts|parts only|not working|broken|box only",
        outbound_shipping=14.00, category="videogames",
        comp_query="nintendo wii u console", specificity=32,
        note="FLOOR at p25 of a $125 median (n=158) - unusually wide spread "
             "($20-$600) because a gamepad-less Wii U is nearly worthless and "
             "a complete one isn't. Floored hard at p25 for that reason. Plain "
             "Wii is MEASURED AND DEAD at $44.99 - see DEAD_MODELS.",
    ),
    Model(
        key="switch_base",
        label="Nintendo Switch console (v1/v2, non-OLED)",
        comp=105.00, measured="2026-08-19", sample=50,
        include=_console_include(r"nintendo\s*switch", r"\bhac-001\b"),
        exclude=r"\boled\b|switch\s*lite|switch\s*2\b|"
                r"\bfor\s+(the\s+)?(nintendo|switch)\b|joy.?cons?\s+only|"
                r"dock only|tablet only|console only|\bcase\b|\bskin\b|"
                r"screen protector|\bgrip\b|charger only|game only|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=10.00, category="videogames",
        comp_query="nintendo switch console", specificity=30,
        note="FLOOR at p25 of a $141.67 median (n=69). The base Switch was "
             "ENTIRELY ABSENT from the book - switch_oled only matched 'switch "
             "oled', so every plain HAC-001 on Goodwill (80 live) was invisible." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $105.00 of a $129.99 median (n=50). Was $120.00.",
    ),
    Model(
        key="switch_lite",
        label="Nintendo Switch Lite",
        comp=90.00, measured="2026-08-16", sample=152,
        include=_console_include(r"switch\s*lite", r"\bhdh-001\b"),
        exclude=r"\boled\b|switch\s*2\b|\bfor\s+(the\s+)?(nintendo|switch)\b|"
                r"\bcase\b|\bskin\b|screen protector|\bgrip\b|charger only|"
                r"for parts|parts only|not working|broken|box only",
        outbound_shipping=8.00, category="videogames",
        comp_query="nintendo switch lite console", specificity=38,
        note="FLOOR at p25 of a $105.96 median (n=152, the tightest console "
             "sample in the pack). 🚨 switch_oled's exclude contains `\\blite\\b`, "
             "so before this model existed a Switch Lite was not merely "
             "unpriced - it was ACTIVELY REJECTED by the book.",
    ),
    Model(
        key="n2ds_xl",
        label="Nintendo New 2DS XL / LL",
        comp=210.00, measured="2026-08-16", sample=171,
        include=_console_include(r"2ds\s*(?:xl|ll)\b", r"\bjan-001\b"),
        exclude=r"\bfor\s+(the\s+)?(nintendo|2ds|3ds)\b|\bcase\b|\bskin\b|"
                r"charger only|stylus only|for parts|parts only|not working|"
                r"broken|box only",
        outbound_shipping=6.00, category="videogames",
        comp_query="nintendo 2ds xl console", specificity=34,
        note="FLOOR at p25 of a $245.99 median (n=171). Highest-value handheld "
             "in the pack and the old book had no 2DS entry at all - n3ds_xl's "
             "exclude explicitly threw `\\b2ds\\b` away.",
    ),
    Model(
        key="dsi_xl",
        label="Nintendo DSi XL / LL",
        comp=89.00, measured="2026-08-16", sample=167,
        include=_console_include(r"\bdsi\s*(?:xl|ll)\b", r"\butl-001\b"),
        exclude=r"\b3ds\b|\b2ds\b|\bfor\s+(the\s+)?(nintendo|dsi)\b|\bcase\b|"
                r"\bskin\b|charger only|stylus only|for parts|parts only|"
                r"not working|broken|box only",
        outbound_shipping=6.00, category="videogames",
        comp_query="nintendo dsi xl console", specificity=32,
        note="FLOOR at p25 of a $115 median (n=167). Plain DS Lite is "
             "MEASURED AND DEAD at $58 (max pay $18.60 vs a $19.99 Goodwill "
             "median) - the XL is the only DS-era unit that clears.",
    ),
    Model(
        key="n3ds_base",
        label="Nintendo 3DS (non-XL)",
        comp=110.00, measured="2026-08-16", sample=66,
        include=_console_include(r"\b3ds\b", r"\bctr-001\b"),
        exclude=r"\b3ds\s*(xl|ll)\b|\b2ds\b|\bdsi\b|\bds\s*lite\b|"
                r"\bfor\s+(the\s+)?(nintendo|3ds)\b|\bcase\b|\bskin\b|"
                r"circle pad|cradle only|charger only|stylus only|"
                r"for parts|parts only|not working|broken|box only",
        outbound_shipping=6.00, category="videogames",
        comp_query="nintendo 3ds console", specificity=24,
        note="FLOOR at p25 of a $139.99 median (n=66). Specificity is "
             "deliberately BELOW n3ds_xl (28) so an XL always wins the tie: "
             "the include is a bare `3ds`, which every XL title also carries.",
    ),
    Model(
        key="steam_deck",
        label="Valve Steam Deck",
        comp=439.00, measured="2026-08-16", sample=119,
        include=_console_include(r"steam\s*deck"),
        exclude=r"\bfor\s+(the\s+)?steam\s*deck\b|\bdock\b|\bcase\b|\bskin\b|"
                r"screen protector|charger only|for parts|parts only|"
                r"not working|broken|box only",
        outbound_shipping=15.00, category="videogames",
        comp_query="steam deck console", specificity=40,
        note="FLOOR at p25 of a $540 median (n=119). Only 9 live on Goodwill "
             "so this fires rarely, but max pay is ~$239 - the LARGEST "
             "single-item ceiling in the book, ahead of the Series X (~$183) "
             "and the PS5 (~$176).",
    ),
    Model(
        key="gba_original",
        label="Game Boy Advance (original AGB-001)",
        comp=53.06, measured="2026-08-19", sample=105,
        # Leron's link #274075162 is exactly this: "Game Boy Advance Pokemon
        # Edition AGB-001". It was FETCHED by the existing "pokemon game boy
        # advance" term and then dropped, because the book knew only the SP.
        include=_console_include(
            r"(?:game\s*boy|gameboy)\s*advance(?!\s*(?:sp|micro))",
            r"\bagb\s*-?\s*001\b"),
        exclude=r"\bsp\b|\bags\s*-?\s*\d+\b|micro|\bds\b|\bplayer\b|"
                r"\bfor\s+(the\s+)?(nintendo|game\s*boy|gameboy)\b|"
                r"\bcase\b|\bskin\b|\bshell\b|housing|screen only|"
                r"for parts|parts only|not working|broken|box only",
        outbound_shipping=5.00, category="videogames",
        comp_query="game boy advance console", specificity=22,
        note="FLOOR at p25 of a $75 median (n=99). 🚨 Max pay is only "
             "$28.86, so most listed GBAs are ALREADY TOO EXPENSIVE - the "
             "point of this model is to price them and say no out loud, not "
             "to chase them. Specificity is below gba_sp (25) so an SP in the "
             "title always wins." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $53.06 of a $69.99 median (n=105). Was $59.00.",
    ),

    # --- Loose GBA/GBC carts that hide inside junk-titled lots ---------------
    # 277 of the 1,117 unmatched Goodwill listings were game LOTS. A generic
    # lot has NO stable price - measured 2026-08-16: GBA lots median $35 over
    # a $1.93-$450 range, DS lots $25, PS2 lots $30. So there is deliberately
    # NO "game lot" model; pricing one on a median would be the Fluke-brand
    # mistake at scale. Instead the lot TERMS were added to search_terms() so
    # the sweep surfaces those listings, and these carts price the contents
    # when a lot title happens to name one. That is the same shape as
    # "calculator lot" -> ti_84_ce.
    #
    # These five are floored at midpoint(p25, median) rather than p25: their
    # low tail is repro/untested/label-damaged listings, which the excludes
    # below already reject at match time, so p25 would double-count that risk.
    Model(
        key="zelda_minish_cap",
        label="Zelda: The Minish Cap (GBA)",
        comp=50.00, measured="2026-08-16", sample=194,
        include=r"minish\s*cap",
        exclude=r"\brepro\b|reproduction|\bfake\b|bootleg|\bsealed\b|\bcib\b|"
                r"box only|\bmanual\b|label only|for parts|parts only",
        outbound_shipping=5.00, category="videogames",
        comp_query="zelda minish cap gba", specificity=42,
        note="Median $79.99 (n=194) but p25 is $21 - strongly BIMODAL, and the "
             "cheap mode is repros and label-damaged carts. Floored at the "
             "midpoint. Verify the label and the save works before paying near "
             "the $23.65 ceiling.",
    ),
    Model(
        key="castlevania_aria",
        label="Castlevania: Aria of Sorrow (GBA)",
        comp=50.00, measured="2026-08-16", sample=130,
        include=r"aria\s*of\s*sorrow",
        exclude=r"\brepro\b|reproduction|\bfake\b|bootleg|\bsealed\b|\bcib\b|"
                r"box only|\bmanual\b|label only|for parts|parts only",
        outbound_shipping=5.00, category="videogames",
        comp_query="castlevania aria of sorrow gba", specificity=42,
        note="Median $85 (n=130), p25 $20 - same bimodal repro tail as the "
             "Minish Cap, floored the same way.",
    ),
    Model(
        key="zelda_oracle",
        label="Zelda: Oracle of Ages / Seasons (GBC)",
        comp=80.00, measured="2026-08-16", sample=41,
        include=r"oracle\s*of\s*(ages|seasons)",
        exclude=r"\brepro\b|reproduction|\bfake\b|bootleg|\bsealed\b|\bcib\b|"
                r"box only|\bmanual\b|booklet|label only|for parts|parts only",
        outbound_shipping=5.00, category="videogames",
        comp_query="zelda oracle of ages seasons gameboy", specificity=42,
        note="Median $120 (n=41 - thin). Highest-value cart in the pack. 🚨 It "
             "is a GAME BOY COLOR cart, so it will co-occur with the GBC "
             "DEAD_MODELS warning; that warning is about the CONSOLE and does "
             "not apply to this cartridge.",
    ),
    Model(
        key="zelda_link_awakening_dx",
        label="Zelda: Link's Awakening DX (GBC)",
        comp=50.00, measured="2026-08-16", sample=166,
        # 🚨 THE TIER IS THE *DX* CART AND THE PATTERN NEVER SAID SO. Bare
        # `link's awakening` also matches the 1993 monochrome Game Boy release,
        # which is a different product at a tenth the price: of 32 routed solds
        # on 2026-08-19 the p25 was $4.61 against this tier's $50 comp, and the
        # cheap end was wall-to-wall "Zelda Link's Awakening Nintendo GameBoy
        # Japan" at $2-4. Require the DX/Color evidence the label claims.
        include=r"link'?s\s*awakening(?=.*(?:\bdx\b|\bcolor\b|\bgbc\b))|"
                r"(?:\bdx\b|\bcolor\b|\bgbc\b)(?=.*link'?s\s*awakening)",
        exclude=r"\bswitch\b|\brepro\b|reproduction|\bfake\b|bootleg|\bsealed\b|"
                r"\bcib\b|box only|\bmanual\b|label only|for parts|parts only",
        outbound_shipping=5.00, category="videogames",
        comp_query="zelda links awakening dx gameboy color", specificity=42,
        note="Median $59 (n=166), a TIGHT distribution ($45-$65 interquartile) "
             "- the most predictable cart here. The Switch remake shares the "
             "name and is excluded.",
    ),
    # Fire Emblem (GBA) was measured at $51 (n=131, p25 $24.99) and CUT here,
    # not shipped: at a $38 floor it yields a $0.00 max bid against the book's
    # standing bar of $20 target profit + $9 inbound shipping. See DEAD_MODELS.

    # === MY PICKS, chosen to exploit the CHANNEL rather than a fandom ==========
    # HiBid aggregates estate, industrial and government surplus. So the best
    # categories are professional tools that FLOOD those auctions, carry a model
    # number in the title, and that eBay's hobbyist crowd doesn't camp on.
    # Shared traits: effectively zero counterfeit risk on used pro gear, condition
    # is binary (it reads or it doesn't), and buyers are tradespeople, not
    # collectors, so prices are stable rather than hype-driven.

    # --- Fluke test gear (measured 2026-07-25, used solds, n=60 overall) -------
    # Best sample-to-value ratio in the whole book: $194.99 median across n=60.
    Model(
        key="fluke_87",
        label="Fluke 87/87V multimeter",
        comp=258.95, measured="2026-07-25", sample=5,
        include=r"fluke\s*87",
        exclude=r"\bprobe|leads only|holster|test lead|\bcase\b|fish|flounder|anchor",
        outbound_shipping=9.00, category="test-gear", comp_query="fluke 87v multimeter",
        specificity=50,
    ),
    Model(
        key="fluke_17x",
        label="Fluke 175/177/179 multimeter",
        comp=169.99, measured="2026-07-26", sample=54,
        include=r"fluke\s*17[579]",
        exclude=r"\bprobe|leads only|holster|test lead|fish|anchor",
        outbound_shipping=9.00, category="test-gear", comp_query="fluke 179 multimeter",
        specificity=50,
    ),
    Model(
        key="fluke_clamp",
        label="Fluke 3xx clamp meter",
        comp=144.99, measured="2026-07-25", sample=7,
        include=r"fluke\s*3\d\d",
        exclude=r"\bprobe|leads only|holster|test lead|fish|anchor",
        outbound_shipping=9.00, category="test-gear", comp_query="fluke clamp meter",
        specificity=50,
    ),
    Model(
        key="fluke_generic",
        label="Fluke meter (unspecified model)",
        comp=90.00, measured="2026-07-25", sample=60,
        # A BRAND IS NOT A MODEL. "Fluke" is also a fish, a soft-plastic fishing
        # lure, and part of a boat anchor. The first version of this catch-all
        # quoted a $130.75 max bid on "Zoom Winged Fluke - Gizzard Shad" and on a
        # galvanized anchor, so the title must ALSO name an instrument.
        include=r"\bfluke\b.{0,40}(multimeter|multi meter|\bmeter\b|\bdmm\b|clamp|tester|"
                r"calibrator|thermometer|scopemeter|true rms|insulation)|"
                r"(multimeter|multi meter|\bmeter\b|\bdmm\b|clamp|tester|calibrator|"
                r"thermometer|scopemeter).{0,40}\bfluke\b",
        exclude=r"\bprobe|leads only|holster|test lead|fish|flounder|anchor|lure|"
                r"spinner|shad|tackle|\brig\b|swimbait|\bcase\b|carrying case|meter case",
        outbound_shipping=9.00, category="test-gear", comp_query="fluke multimeter",
        specificity=45,
        note="Unidentified Fluke: priced at a CONSERVATIVE $90 floor, not the $194.99 "
             "multimeter median, because cheap models (101/106/107) sell $40-60. "
             "Confirm the model before bidding near the max.",
    ),

    # --- Machinist metrology (measured 2026-07-25) ----------------------------
    # Estate auctions are full of these. Note the inversion vs junk lots: tool
    # SETS sell HIGHER ($146 median) than singles, because the buyer wants the set.
    Model(
        key="mitutoyo",
        label="Mitutoyo micrometer/caliper/indicator",
        comp=87.05, measured="2026-07-25", sample=44,
        # `mit[aiu]t[ou]yo` folds the common misspellings (Mitatoyo, Mititoyo,
        # Mitutuyo) - typo'd titles get no search traffic, which is exactly why
        # they close cheap. The instrument-noun requirement still applies.
        include=r"mit[aiu]t[ou]yo.{0,40}(micrometer|caliper|indicator|gage|gauge|height|depth|"
                r"bore|dial|scale|protractor)|(micrometer|caliper|indicator|gage|gauge)"
                r".{0,40}mit[aiu]t[ou]yo",
        exclude=r"\bcase only\b|box only|anvil only|spindle only|\bstand only\b",
        outbound_shipping=8.00, category="metrology", comp_query="mitutoyo micrometer",
        specificity=50,
    ),
    Model(
        key="starrett",
        label="Starrett precision tool",
        comp=81.95, measured="2026-07-25", sample=43,
        # Same rule as Fluke: Starrett also sells $5 hacksaw blades and tape
        # measures, which the bare brand would have priced at $81.95.
        # `starr?ett?` folds the single-letter typos (Starret, Starett) that
        # kill a listing's search traffic - same trade, cheaper entry.
        include=r"starr?ett?\b.{0,40}(micrometer|caliper|indicator|gage|gauge|square|level|"
                r"protractor|dial|height|depth|precision|toolmaker|surface plate)|"
                r"(micrometer|caliper|indicator|gage|gauge).{0,40}starr?ett?\b",
        exclude=r"\bcase only\b|box only|anvil only|spindle only|\bstand only\b|"
                r"hacksaw|saw blade|bandsaw|band saw|tape measure|blade only|\bblades\b",
        outbound_shipping=8.00, category="metrology", comp_query="starrett precision tool",
        specificity=50,
    ),
    Model(
        key="dial_indicator",
        label="Dial / test indicator (brand-name)",
        comp=122.50, measured="2026-07-25", sample=13,
        # `.` not `[^.]` - real titles read "Starrett No. 25 Dial Indicator", and
        # excluding periods made the brand and the noun unreachable from each other.
        include=r"(starr?ett?\b|mit[aiu]t[ou]yo|brown\s*&?\s*sharpe|interapid|federal).{0,40}indicator",
        # Live catch: "Starrett No 25R Dial Indicator Contact Point Set" is a bag
        # of ~$15 tips, not a $122 indicator. Accessories FOR the tool read almost
        # identically to the tool.
        exclude=r"\bcase only\b|box only|\bstand only\b|contact point|"
                r"\btips?\s*(set|kit|assortment)|point set|\banvil\b|"
                r"attachment only|back only|bezel|crystal only|holder only",
        outbound_shipping=8.00, category="metrology", comp_query="starrett dial indicator",
        specificity=55,
    ),

    # --- Littmann stethoscopes (measured 2026-07-25) --------------------------
    # Only the high end clears well: the generic Littmann median is $59.95 and
    # leaves just $26.61 of room, so the cheap models are deliberately NOT here.
    Model(
        key="littmann_master_cardiology",
        label="Littmann Master Cardiology",
        comp=139.99, measured="2026-07-25", sample=4,
        include=r"master\s*cardiology",
        exclude=r"ear\s?tips?|diaphragm|tubing only|replacement part|name tag",
        outbound_shipping=5.00, category="medical", comp_query="littmann master cardiology stethoscope",
        specificity=50,
    ),
    Model(
        key="littmann_cardiology_iv",
        label="Littmann Cardiology IV",
        comp=97.49, measured="2026-07-25", sample=5,
        include=r"cardiology\s*(iv|4)\b",
        exclude=r"ear\s?tips?|diaphragm|tubing only|replacement part|name tag",
        outbound_shipping=5.00, category="medical", comp_query="littmann cardiology iv stethoscope",
        specificity=50,
    ),

    # === Technical outerwear (measured 2026-07-25) ============================
    # Designer, but deliberately the UNGLAMOROUS end. Goodwill buy-side census
    # the same day shows why:
    #     Louis Vuitton  $84.50  8.5 bids   22% zero-bid   <- crowded
    #     Gucci          $53.00  5.0 bids   12% zero-bid   <- crowded
    #     Patagonia       $9.99  0.0 bids   78% zero-bid   <- nobody bidding
    #     North Face      $8.50  0.0 bids   80% zero-bid
    # The famous names draw a crowd AND carry counterfeit risk I cannot resolve
    # from a listing photo, so fashion handbags are deliberately NOT in the book.
    # Technical outerwear is barely faked and the model name is printed on the tag.
    #
    # And the usual rule bites hardest here: a Beta AR shell sells for $325 while
    # an unspecified "Arc'teryx jacket" sells for $70. The model IS the trade.
    #
    # SIZING is a real risk this book can't price: 55 of 60 sold listings named a
    # size, and an XXL sits far longer than a M. Treat these as slower flips.
    Model(
        key="arcteryx_shell",
        label="Arc'teryx GoreTex shell (Beta/Alpha)",
        comp=250.52, measured="2026-07-26", sample=60,
        include=r"arc'?\s*teryx.{0,40}(beta|alpha)\s*(sv|ar|fl|lt)?|"
                r"arc'?\s*teryx.{0,30}gore[\s-]*tex",
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|\bcap\b|glove|"
                r"\bshirt\b|\bsock|beanie|\bbag\b|backpack|\bcase\b",
        outbound_shipping=9.00, category="outerwear", specificity=60,
        comp_query="arcteryx beta jacket", note="n=4 - re-measure before bidding near the max.",
    ),
    Model(
        key="arcteryx_atom",
        label="Arc'teryx Atom (insulated)",
        comp=183.98, measured="2026-07-26", sample=60,
        include=r"arc'?\s*teryx.{0,30}atom",
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|glove|\bshirt\b|beanie|"
                r"\bpants?\b|\bbib\b|\bshorts?\b|legging|\bshoes?\b|boot|\bvest\b",
        outbound_shipping=9.00, category="outerwear", specificity=60,
        comp_query="arcteryx atom jacket",
    ),
    Model(
        key="arcteryx_fleece",
        label="Arc'teryx fleece (Delta/Kyanite)",
        comp=100.57, measured="2026-07-25", sample=8,
        include=r"arc'?\s*teryx.{0,30}(delta|kyanite|fleece)",
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|glove|\bshirt\b|beanie|"
                r"\bpants?\b|\bbib\b|\bshorts?\b|legging|\bshoes?\b|boot|\bvest\b",
        outbound_shipping=9.00, category="outerwear", specificity=60,
        comp_query="arcteryx fleece jacket",
    ),
    Model(
        key="arcteryx_generic",
        label="Arc'teryx (unspecified model)",
        comp=70.00, measured="2026-07-25", sample=60,
        include=r"arc'?\s*teryx",
        # Every outerwear comp here was measured from JACKET sales. The used-gear
        # shops surface pants, bibs, shoes and leggings under the same brand, and
        # pricing a $374 bib pant off a $70 jacket comp is simply a wrong number.
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|\bcap\b|glove|"
                r"\bshirt\b|\bsock|beanie|\bbag\b|backpack|\bcase\b|sticker|"
                r"\bpants?\b|\bbib\b|\bshorts?\b|legging|\bshoes?\b|\bboots?\b|"
                r"\bharness\b|\bbelt\b|gaiter|\bvest\b|\bskirt\b",
        outbound_shipping=9.00, category="outerwear", specificity=55,
        comp_query="arcteryx jacket",
        note="Unspecified model floor. A Beta/Alpha shell is worth 4.5x this - "
             "read the tag in the photos before settling for the generic number.",
    ),
    Model(
        key="patagonia_puffy",
        label="Patagonia Nano Puff / Down Sweater",
        comp=81.44, measured="2026-07-25", sample=9,
        include=r"patagonia.{0,40}(nano[\s-]*puff|down sweater|nano[\s-]*air)",
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|glove|\bshirt\b|beanie|"
                r"\bpants?\b|\bbib\b|\bshorts?\b|legging|\bshoes?\b|boot|\bvest\b",
        outbound_shipping=9.00, category="outerwear", specificity=60,
        comp_query="patagonia nano puff jacket",
    ),
    Model(
        key="patagonia_generic",
        label="Patagonia (unspecified)",
        comp=60.03, measured="2026-07-25", sample=60,
        include=r"patagonia",
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|\bcap\b|glove|"
                r"\bshirt\b|\bsock|beanie|\bbag\b|backpack|sticker|"
                r"\bpants?\b|\bbib\b|\bshorts?\b|legging|\bshoes?\b|\bboots?\b|"
                r"\bharness\b|\bbelt\b|gaiter|\bvest\b|\bskirt\b",
        outbound_shipping=9.00, category="outerwear", specificity=55,
        comp_query="patagonia jacket",
        note="Thin margin ($22.68 max buy) - only worth it because Goodwill "
             "Patagonia sits at $9.99 with 78% of listings drawing zero bids.",
    ),

    # === Cameras: film + digital + camcorders (measured 2026-07-28) ===========
    # Leron's ask. The Y2K digicam / film-revival trend is real and measured:
    # a Canon G7X Mark II sells for $1,149 all-in, a 20-year-old ELPH for $184,
    # and an Olympus mju-II film point-&-shoot for $485. Estate auctions and
    # Goodwill are FULL of cameras marked untested - and for digicams "untested"
    # usually means "no battery/charger on the shelf", the same commodity-part
    # discount as the iPods. Film SLRs are riskier (shutter/meter are mechanical),
    # so treat film alerts as "verify", like the Pokemon carts.
    #
    # FILM comps (AE-1, K1000, SX-70, Stylus Epic) are measured WITHOUT eBay's
    # Used filter: vintage film cameras get listed under every condition bucket
    # and the filter starves the search - same taxonomy lesson as video games.
    #
    # BRAND-LINE models (Cyber-shot / Coolpix / FinePix / ELPH) carry a
    # CONSERVATIVE FLOOR, not the median, fluke_generic-style: the spread inside
    # each line is 10x (a 2002 DSC-P10 sells $16, a DSC-W830 $189) and we often
    # can't tell the sub-model from an auction title. The named models above
    # them (G7X, RX100) carry their own measured medians.
    Model(
        key="g7x_mark3",
        label="Canon PowerShot G7X Mark III",
        comp=915.00, measured="2026-08-19", sample=109,
        include=r"g7\s*x.{0,25}mark\s*(iii\b|3\b)",
        exclude=r"for parts|parts only|not working|broken|\brepair\b",
        outbound_shipping=6.00, category="cameras",
        comp_query="canon powershot g7x mark iii", specificity=66,
        note="Vlogger-boom pricing - verify it powers on; a broken pop-up flash "
             "unit still sold for $606." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $915.00 of a $1,099.00 median (n=109). Was $1,145.90.",
    ),
    Model(
        key="g7x_mark2",
        label="Canon PowerShot G7X Mark II",
        comp=992.32, measured="2026-08-19", sample=181,
        include=r"g7\s*x.{0,25}mark\s*(ii\b|2\b)",
        exclude=r"for parts|parts only|not working|broken|\brepair\b",
        outbound_shipping=6.00, category="cameras",
        comp_query="canon powershot g7x mark ii", specificity=65,
        note="The single most valuable item in the book. TikTok made this THE "
             "camera; it sold for $699 new in 2016." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $992.32 of a $1,062.26 median (n=181). Was $1,149.35.",
    ),
    Model(
        key="g7x",
        label="Canon PowerShot G7X (Mark I / unspecified)",
        comp=708.18, measured="2026-08-19", sample=93,
        include=r"g7\s*x",
        exclude=r"for parts|parts only|not working|broken|\brepair\b",
        outbound_shipping=6.00, category="cameras",
        comp_query="canon powershot g7x", specificity=60,
        note="Mark I / unmarked floor. A Mark II/III is worth $440 more - read "
             "the photos before settling for this number." 
             "Re-measured 2026-08-19 on the ROUTED population (n=93, p25 $650.00, median $686.00): CONFIRMED the comp.",
    ),
    # --- High-ticket cameras (measured 2026-07-30). Leron's budget goes past
    # $100/item, so the book now carries the models where a single flip clears
    # $100-400. Same hype driver as the G7X: compact "vibe" cameras boom.
    Model(
        key="fuji_x100v",
        label="Fujifilm X100V",
        comp=1300.00, measured="2026-07-30", sample=8,
        # LEADING \b IS LOAD-BEARING, added 2026-08-15. `x100v\b` guarded the
        # tail (keeping the newer X100VI out) but not the head, so it matched
        # INSIDE "DSC-HX100V" - Sony's 1/2.3-inch bridge camera. That listing
        # sat at #1 on the live board: a $89.99 Sony quoted against a $1,300
        # Fuji. Same failure as HX99 riding the RX100 include below; a short
        # alphanumeric model code needs boundaries on BOTH ends.
        include=r"\bx100v\b",
        exclude=r"for parts|parts only|not working|broken|repair",
        outbound_shipping=8.00, category="cameras",
        comp_query="fujifilm x100v", specificity=64,
        note="FLOOR below the $1,580.82 used median - n=8 THIN because used "
             "X100Vs are scarce (that scarcity is the edge). The single most "
             "valuable compact in the book; verify it powers on.",
    ),
    Model(
        key="fuji_x100f",
        label="Fujifilm X100F",
        comp=800.00, measured="2026-08-19", sample=181,
        include=r"\bx100f\b",     # both-ends boundary, same reason as X100V above
        exclude=r"for parts|parts only|not working|broken|repair",
        outbound_shipping=8.00, category="cameras",
        comp_query="fujifilm x100f", specificity=63,
        note="FLOOR below the $879.99 used median (n=60). X100S/T lookalikes "
             "sell ~$500-700 and deliberately do NOT match." 
             "Re-measured 2026-08-19 on the ROUTED population (n=181, p25 $800.00, median $891.00): CONFIRMED the comp.",
    ),
    Model(
        key="contax_t2",
        label="Contax T2 (35mm compact)",
        comp=1100.00, measured="2026-08-19", sample=157,
        include=r"contax\s*t2\b",
        exclude=r"for parts|parts only|not working|broken|repair|data back only",
        outbound_shipping=8.00, category="cameras",
        comp_query="contax t2 camera", comp_used_only=False, specificity=62,
        note="FLOOR below the $1,296.49 median (n=44). THE estate-sale grail - "
             "a film point-and-shoot relatives donate for nothing. Untested "
             "units still clear $800+; T3 comps even higher (unmeasured)." 
             "Re-measured 2026-08-19 on the ROUTED population (n=157, p25 $1,023.99, median $1,129.99): CONFIRMED the comp.",
    ),
    Model(
        key="canon_5d3",
        label="Canon EOS 5D Mark III",
        comp=330.00, measured="2026-08-19", sample=163,
        include=r"5d\s*mark\s*iii|5d\s*mk\s*iii|\b5d3\b",
        # \b after ii keeps this from swallowing Mark II ($250) titles; the
        # include's explicit iii keeps Mark IV ($900) from matching either.
        exclude=r"mark\s*ii\b|mk\s*ii\b|mark\s*iv|mk\s*iv|for parts|parts only|"
                r"not working|broken|repair|shutter assembly|focusing screen|"
                r"body cap|battery grip only",
        outbound_shipping=10.00, category="cameras",
        comp_query="canon 5d mark iii", specificity=56,
        note="$416.07 used median (n=57), floored to $400. Check shutter count "
             "if stated; body-only is the normal sale." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $330.00 of a $399.00 median (n=163). Was $400.00.",
    ),
    Model(
        key="sony_a6000",
        label="Sony a6000 (mirrorless)",
        comp=350.00, measured="2026-08-19", sample=202,
        include=r"\ba6000\b|ilce\s*-?\s*6000",
        exclude=r"lens only|body cap|for parts|parts only|not working|broken|repair",
        outbound_shipping=8.00, category="cameras",
        comp_query="sony a6000 camera", specificity=55,
        note="FLOOR below the $406.07 median (n=55, mostly WITH kit lens). "
             "Body-only sells lower - price the kit, not the bare body." 
             "Re-measured 2026-08-19 on the ROUTED population (n=202, p25 $339.99, median $384.75): CONFIRMED the comp.",
    ),
    Model(
        key="gopro_hero11",
        label="GoPro HERO 11 Black",
        comp=104.50, measured="2026-08-19", sample=69,
        include=r"hero\s*-?\s*11\b",
        exclude=r"session|mount|frame only|housing only|lens cover|door|"
                r"battery only|charger only|for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="gopro hero 11 black", specificity=52,
        note="FLOOR covering the Mini variant (~$158); full-size medians "
             "$204.63 (n=17). Hero 9/10/12 are unmeasured - do not assume." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $104.50 of a $149.99 median (n=69). Was $160.00.",
    ),
    Model(
        key="sony_rx100",
        label="Sony RX100 / ZV-1 (1-inch compact)",
        comp=358.88, measured="2026-08-19", sample=211,
        # HX99 used to ride along here; it's a 1/2.3-inch travel zoom, NOT an
        # RX100-class 1-inch, and no HX99 comp was ever measured - the sentry
        # nearly advised raising toward $434 on one (2026-07-30). Unpriced
        # until someone measures it; a missing comp beats a wrong one.
        include=r"rx\s*-?\s*100|\bzv\s*-?\s*1\b",
        exclude=r"for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="sony rx100 camera", specificity=62,
        note="n=5 - thin sample, and later marks (M3-M7) comp higher than the "
             "original. Re-measure before bidding near the max." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $358.88 of a $503.00 median (n=211). Was $541.99.",
    ),
    Model(
        key="powershot_elph",
        label="Canon PowerShot ELPH / IXUS (digital)",
        comp=120.00, measured="2026-08-19", sample=202,
        include=r"\belph\b|\bixus\b|\bixy\b",
        # The 1990s APS-film Elph (Elph 2/Jr/LT/260Z/370Z) shares the name and
        # sells for $6 - one sold mid-sweep. "film camera" kills those.
        exclude=r"\baps\b|film camera|\belph\s*(2|jr|lt)\b|\b(260z?|370z?|490z?)\b|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="canon powershot elph", specificity=55,
        note="CONSERVATIVE FLOOR below the $184.03 median (n=52, range $72-365): "
             "early SD-series sell $65-140, named-ELPH models $150-350. Confirm "
             "the sub-model before bidding near the max." 
             "Re-measured 2026-08-19 on the ROUTED population (n=202, p25 $119.99, median $174.88): CONFIRMED the comp.",
    ),
    Model(
        key="sony_cybershot",
        label="Sony Cyber-shot compact (non-RX)",
        comp=75.00, measured="2026-08-19", sample=191,
        include=r"cyber\s*-?\s*shot|\bdsc\s*-?\s*[a-z]{1,2}\d",
        # The 2001-2005 P-series and single-digit H-series are measured-cheap:
        # every P-series sold in the sweep went for $10-42 all-in, below what
        # the $75 floor would bid. Exclude rather than overbid.
        # HX99 is deliberately UNPRICED (see the RX100 note): it outsells this
        # $75 floor by a lot, so the floor would misprice it in both directions.
        exclude=r"\bdsc\s*-?\s*p\d|\bdsc\s*-?\s*h\d\b|\bhx\s*-?\s*99\b|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="sony cyber-shot camera", specificity=50,
        note="CONSERVATIVE FLOOR below the $118.86 median (n=47, range $16-229): "
             "W/T-series sell $90-190. Confirm the sub-model before bidding "
             "near the max." 
             "Re-measured 2026-08-19 on the ROUTED population (n=191, p25 $95.00, median $128.99): comp is BELOW p25 and stays there - raising a ceiling is the direction that loses money.",
    ),
    Model(
        key="nikon_coolpix",
        label="Nikon Coolpix compact",
        comp=55.00, measured="2026-08-19", sample=226,
        include=r"coolpix",
        exclude=r"for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="nikon coolpix camera", specificity=50,
        note="CONSERVATIVE FLOOR below the $97.98 median (n=51, range $30-368), "
             "set under the AA-battery L-series ($40-70) so their tail can't "
             "lose money; S/P-series sell $100-180." 
             "Re-measured 2026-08-19 on the ROUTED population (n=226, p25 $56.83, median $89.99): CONFIRMED the comp.",
    ),
    # 🚨 fujifilm_finepix WAS HERE AND IS NOW DEAD (2026-08-19).
    # It survived only because its $45 comp sat ABOVE its own measured floor.
    # Re-measured on the routed population it is p25 $40.60 / median $60.88
    # (n=218, up from n=53), and at $40.60 it nets $28.82 and quotes a max bid
    # of $0.00 against the standing gate ($20 profit over $9 inbound). Even at
    # the old $45 the ceiling was $3.64. Second tier this sweep killed at the
    # honest number, after citizen_quartz_chrono. See DEAD_MODELS.
    Model(
        key="sony_handycam",
        label="Sony Handycam camcorder",
        comp=120.00, measured="2026-08-19", sample=169,
        include=r"handycam|\bdcr\s*-|\bccd\s*-\s*tr|\bhdr\s*-\s*(cx|xr|pj|sr)",
        # Tape lots borrow the name ("Hi8 tapes for Sony Handycam"), and the
        # DVD-era models are the measured-cheap end ($15-71) - excluded.
        # 🚨 `\bdvd\b` COULD NOT MATCH "DCR-DVD92" - the trailing \b needs a
        # non-word character and "92" is a word character. So every DVD
        # Handycam sailed through and got priced against the TAPE comp.
        # Caught on a live eBay listing 2026-08-17; it had already put a
        # DCR-DVD92 on the board claiming $73.72 of profit when the real
        # number was about $8.
        #
        # THE FORMAT IS THE VALUE. Tape-era units sell because buyers want to
        # DIGITISE old Video8/Hi8/MiniDV cassettes; a DVD camcorder has no
        # such job - the disc already plays in any computer:
        #     tape-era Hi8/MiniDV/Digital8   n=41    p25 $95.99  med $134.99
        #     DVD camcorders                 n=204   p25 $30     med $49.99
        #     DCR-DVD92 exactly              n=99    p25 $34.40  med $41.16
        # DVD models are DEAD - see DEAD_MODELS.
        exclude=r"\btapes?\b|cassette|dcr\s*-?\s*dvd|\bdvd\s*\d|\bdvd\b|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=10.00, category="cameras",
        comp_query="sony handycam camcorder", specificity=50,
        note="CONSERVATIVE FLOOR below the $163.18 median (n=53). Tape-era "
             "(Video8/Hi8/MiniDV) units sell $90-200 for tape-transfer use; "
             "include the charger in the photo check - proprietary batteries." 
             "Re-measured 2026-08-19 on the ROUTED population (n=169, p25 $119.00, median $150.00): CONFIRMED the comp.",
    ),
    # --- film cameras (comps measured WITHOUT the Used filter) ----------------
    Model(
        key="olympus_mju2",
        label="Olympus mju-II / Stylus Epic (non-zoom)",
        comp=359.00, measured="2026-08-19", sample=124, comp_used_only=False,
        # The fixed-lens f/2.8 Epic IS the mju-II and sells 2.8x the Zoom
        # variants. "Zoom" in the title demotes it to the model below.
        include=r"mju\s*-?\s*(ii\b|2\b)|stylus\s+epic\b",
        exclude=r"\bzoom\b|for parts|parts only|not working|broken|damaged",
        outbound_shipping=6.00, category="cameras",
        comp_query="olympus stylus epic mju", specificity=62,
        note="The model IS the trade: fixed-lens f/2.8 sells $485 (n=12, range "
             "$300-690), the Zoom versions $176. Confirm NO 'Zoom' on the body." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $359.00 of a $449.00 median (n=124). Was $484.85.",
    ),
    Model(
        key="stylus_epic_zoom",
        label="Olympus Stylus Epic Zoom 80/115/170",
        comp=139.00, measured="2026-08-19", sample=262, comp_used_only=False,
        include=r"stylus\s+(epic\s+)?zoom\s*(80|115|170)|epic\s+zoom",
        exclude=r"for parts|parts only|not working|broken|damaged",
        outbound_shipping=6.00, category="cameras",
        comp_query="olympus stylus epic zoom", specificity=58,
        note="Film-revival pricing on a 90s drugstore camera. Untested units "
             "sold $15-40, so the working comp only applies if it powers on." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $139.00 of a $169.97 median (n=262). Was $175.68.",
    ),
    Model(
        key="canon_ae1",
        label="Canon AE-1 / AE-1 Program (35mm SLR)",
        comp=89.00, measured="2026-08-19", sample=159, comp_used_only=False,
        # `can+on` also catches the constant "Cannon" misspelling - one sold
        # for full price under it during the sweep.
        include=r"can+on.{0,50}\bae\s*-?\s*1\b|\bae\s*-?\s*1\b.{0,50}can+on|"
                r"\bae\s*-?\s*1\s*program\b",
        exclude=r"for parts|parts only|not working|broken",
        outbound_shipping=9.00, category="cameras",
        comp_query="canon ae-1 camera", specificity=55,
        note="Comp is body+lens (how they're found and sold). 1/3 of solds are "
             "Japan imports at a discount. Mechanical: listen for the 'AE-1 "
             "squeal' note in the listing; 'film tested' is the magic phrase." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $89.00 of a $129.99 median (n=159). Was $150.20.",
    ),
    Model(
        key="pentax_k1000",
        label="Pentax K1000 (35mm SLR)",
        comp=85.50, measured="2026-08-19", sample=194, comp_used_only=False,
        include=r"k\s*-?\s*1000\b",
        # The Pentax KM/ME/MX read almost identically and comp differently.
        exclude=r"\bkm\b|\bme\s+super\b|\bmx\b|for parts|parts only|not working|broken",
        outbound_shipping=9.00, category="cameras",
        comp_query="pentax k1000 camera", specificity=55,
        note="The perpetual photo-class camera - demand never dies. Comp is "
             "body+50mm; body-only sold $49-85." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $85.50 of a $125.00 median (n=194). Was $139.99.",
    ),
    Model(
        key="polaroid_sx70",
        label="Polaroid SX-70 (folding)",
        comp=60.00, measured="2026-08-19", sample=97, comp_used_only=False,
        include=r"\bsx\s*-?\s*70\b",
        # The plastic OneStep/Rainbow box cameras share the SX-70 film format
        # and sell for $11-30; only the folding SLR (and its Sonar/Alpha
        # variants) carries the value.
        exclude=r"rainbow|(?<!sonar )one\s*-?\s*step|for parts|parts only|"
                r"not working|broken|damaged",
        outbound_shipping=9.00, category="cameras",
        comp_query="polaroid sx-70 camera", specificity=55,
        note="33% of solds are parts/untested - the folding mechanism and rollers "
             "die. Working sells $100 (Sonar $120-200, Alpha 1 $160-300); "
             "untested only $40-85, so bid the condition you're actually buying." 
             "RE-MEASURED 2026-08-19 on the ROUTED population: p25 $60.00 of a $100.00 median (n=97). Was $99.99.",
    ),

    # === Women's apparel (measured 2026-07-28) ================================
    # Leron asked whether boutique dresses were being missed. Measured verdict:
    # the GENERIC boutique dress fails - Free People / Anthropologie / Lilly
    # Pulitzer sold medians are $39-41 on eBay (n=60 each), which nets ~$7 a
    # flip after Goodwill's $11-15 + $9 inbound. What PASSES is the same shape
    # as the outerwear book: specific lines with a tag readable in a photo,
    # near-zero counterfeit risk, and an EMPTY buy side.
    #
    # Buy-side census 2026-07-28 across goodwill/hibid/nellis/craigslist/
    # poshmark: Free People 98% zero-bid @ $11.49, St John 70% @ $14.95 (+20
    # HiBid lots at $0), Gunne Sax 70% @ $20, Veronica Beard 51% @ $16, and
    # Johnny Was is CROWDED on Goodwill (12% zero-bid) but WIDE OPEN on HiBid
    # (78 lots, 68% zero-bid, 38 drivable from Fulshear - estate houses
    # liquidate it). Farm Rio / LoveShackFancy already draw 6 bids median on
    # Goodwill - the crowd found those; deliberately NOT in the book.
    #
    # Sell comps below are POSHMARK SOLDS (n=48/query) - eBay sign-walled its
    # sold search mid-measurement; Poshmark is the native apparel channel and
    # its sold prices print on the listing. Re-measure on eBay with
    # `flipscout comp` before trusting a ceiling to the dollar.
    #
    # Shared risks (same as outerwear, priced into the floors): SIZE (an XS or
    # XXL sits), stains/alterations that don't photograph, and slower turns
    # than electronics.
    Model(
        key="gunne_sax",
        label="Gunne Sax vintage dress",
        comp=122.00, measured="2026-07-28", sample=48,
        include=r"gunne\s+sax",
        # comp is the DRESS median - live sweeps surfaced a Gunne Sax CLUTCH,
        # an evening BAG, a 3.4oz EDP PERFUME (Jessica McClintock licenses the
        # name), a "Gunne Sax STYLE" Contempo lookalike and a HANDMADE repro,
        # all of which would have been quoted the $122 dress comp
        exclude=r"\bgirls?\b|\bkids?\b|children|\bpattern\b|sewing|"
                r"\bclutch\b|\bpurse\b|handbag|\bskirts?\b|\bbag\b|"
                r"\bedp\b|\bedt\b|perfume|parfum|cologne|fragrance|\bspray\b|"
                r"\d+(\.\d+)?\s*oz\b|gunne\s+sax\s+(style|inspired|esque|type)|"
                r"handmade|\bhand\s*made\b",
        outbound_shipping=6.00, category="womens-apparel",
        comp_query="gunne sax dress", specificity=60,
        note="1970s-80s prairie/cottagecore revival: sold median $122, p75 $210, "
             "peak $400 (Poshmark solds n=48). Nobody counterfeits it and the "
             "label is unmistakable. Vintage sizing runs 2+ sizes small - "
             "condition-check lace and zippers in photos.",
    ),
    Model(
        key="veronica_beard",
        label="Veronica Beard blazer/jacket",
        comp=150.00, measured="2026-07-28", sample=48,
        # Blazer-corroborated only: the $150 comp is the Dickey-jacket end,
        # not VB tees. Same brand-is-not-a-model rule as Fluke.
        include=r"veronica\s+beard.{0,40}(blazer|jacket|dickey)|"
                r"(blazer|jacket|dickey).{0,40}veronica\s+beard",
        exclude=r"\bkids?\b|\bdickey\s+only\b|insert only",
        outbound_shipping=7.00, category="womens-apparel",
        comp_query="veronica beard blazer", specificity=60,
        note="Sold median $150, p75 $220 (Poshmark n=48). The removable dickey "
             "being PRESENT adds value - look for it in the photos.",
    ),
    Model(
        key="st_john_knit",
        label="St. John knit jacket/suit",
        comp=99.50, measured="2026-07-28", sample=48,
        # Corroborating noun required: bare "St John" is also St John's Bay
        # (JCPenney, ~$8) and Virgin-Islands souvenir shirts.
        include=r"st\.?\s*john.{0,40}(knit|jacket|blazer|suit|santana|dress)|"
                r"(knit|jacket|blazer|suit).{0,40}st\.?\s*john\b",
        # \s* not \s+: a live listing wrote "St. JohnsBay" with no space and
        # walked straight past the first version of this exclude. And the comp
        # is knit JACKETS - pants/cardigans matched via bare "knit" and sell
        # $40-60, so they're excluded rather than overbid.
        # `top\S*` not `tops?\b`: a live listing wrote "Mock Neck TopSize 14"
        # with no space and the word boundary never fired.
        exclude=r"st\.?\s*john'?s\s*bay|virgin\s+islands|\busvi\b|\bkids?\b|"
                r"\bpants?\b|\bskirts?\b(?!\s*suit)|cardigan|\bsweater\b|"
                r"\btops?(ize)?\b|\bcami|\btank\b|\bshorts?\b|\bshells?\b|"
                r"perfume|parfum|cologne|fragrance|\bedp\b|\bedt\b",
        outbound_shipping=7.00, category="womens-apparel",
        comp_query="st john knit jacket", specificity=60,
        note="Sold median $99.50, p75 $140, Santana-knit suits to $600 (Poshmark "
             "n=48). THE estate-auction apparel brand - 20 HiBid lots sat at $0 "
             "bids on census day. Check knit for pilling in photos.",
    ),
    Model(
        key="johnny_was",
        label="Johnny Was embroidered top/dress",
        comp=65.00, measured="2026-07-28", sample=48,
        include=r"johnny\s+was",
        # comp is dresses/embroidered tops - the HiBid estate lots also carry
        # JW shoes, leggings and the cheaper Pete & Greta subline
        exclude=r"\bscarf\b|\bkids?\b|\bshoes?\b|sneaker|legging|\bsocks?\b|"
                r"pete\s*&?\s*greta|\bshorts?\b|perfume|parfum|cologne|fragrance",
        outbound_shipping=6.00, category="womens-apparel",
        comp_query="johnny was dress", specificity=55,
        note="Sold median $65, p75 $95 (Poshmark n=48). Goodwill is CROWDED for "
             "this brand (12% zero-bid) - the play is HiBid estate lots, which "
             "sat 68% zero-bid with 38 drivable on census day. Embroidery is "
             "the tell; no counterfeit market.",
    ),
    Model(
        key="reformation_dress",
        label="Reformation dress",
        comp=56.07, measured="2026-07-28", sample=60,
        include=r"reformation.{0,40}(dress|midi|maxi|mini)|"
                r"(dress|midi|maxi).{0,40}reformation",
        exclude=r"\bkids?\b|church|\bbook\b",
        outbound_shipping=6.00, category="womens-apparel",
        comp_query="reformation dress", specificity=55,
        note="eBay used solds n=60: median $56.07, p25-p75 $41-88. BORDERLINE "
             "at the median (~$18/flip) - the real targets are silk and named "
             "styles (Scottie $180, Rowe $120, silk maxi $220). 'Reformation' "
             "on a church/book title is not the brand; excludes catch some.",
    ),

    # === Cordless tools + vintage sewing (measured 2026-07-29) ================
    # The ORIGINAL 7/12 watchlist had dewalt/milwaukee/makita drills and they
    # never made it into the book. Estate and surplus auctions are full of
    # them, model lines are printed on the tool, and buyers are tradespeople.
    # LEGO was measured the same session and REJECTED: "lego lot" sold median
    # $32.98 (n=60) with contents-driven 10x variance and a p25 at the fee
    # floor - a per-listing comp would be a guess, same verdict as junk lots.
    Model(
        key="m18_combo",
        label="Milwaukee M18 combo kit",
        comp=180.00, measured="2026-07-29", sample=60,
        include=r"m18.{0,40}combo\s*kit|m18.{0,40}\d\s*-?\s*tool\b|"
                r"combo\s*kit.{0,30}m18",
        exclude=r"batter(y|ies)\s+only|charger only|\bcase only\b|tool bag only|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=15.00, category="tools",
        comp_query="milwaukee m18 combo kit", specificity=60,
        note="CONSERVATIVE FLOOR below the $226.65 median (n=60, p75 $279): "
             "kit value scales with piece count and battery count - count them "
             "in the photos.",
    ),
    Model(
        key="m18_fuel_tool",
        label="Milwaukee M18 FUEL tool",
        comp=85.00, measured="2026-07-29", sample=60,
        # FUEL is the premium brushless line; corroborating tool noun required
        # (the Fluke rule) so M18 batteries/chargers alone never price as one.
        include=r"m18\s*fuel.{0,50}(drill|driver|impact|saw|sawzall|grinder|"
                r"hammer|multi\s*tool|ratchet|router|blower)|"
                r"(drill|driver|impact|sawzall)\b.{0,40}m18\s*fuel",
        exclude=r"batter(y|ies)\s+only|charger only|\bcase only\b|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=10.00, category="tools",
        comp_query="milwaukee m18 fuel drill", specificity=58,
        note="CONSERVATIVE FLOOR below the $95 median (n=60, p25 $66 bare-tool, "
             "p75 $114 w/ battery). A battery in the photos adds ~$40.",
    ),
    Model(
        key="dewalt_20v_drill",
        label="DeWalt 20V MAX drill/driver",
        comp=60.00, measured="2026-07-29", sample=60,
        include=r"dewalt.{0,40}(20\s*v|20v|xr).{0,40}(drill|driver|impact)|"
                r"dewalt.{0,40}(drill|driver|impact).{0,30}(20\s*v|20v|xr)",
        exclude=r"batter(y|ies)\s+only|charger only|\bcase only\b|"
                r"for parts|parts only|not working|broken|\b12v\b|atomic",
        outbound_shipping=10.00, category="tools",
        comp_query="dewalt 20v max drill", specificity=58,
        note="CONSERVATIVE FLOOR below the $69.98 median (n=60): the thinnest "
             "tool margin - only worth it under ~$15 at auction.",
    ),
    Model(
        key="singer_featherweight",
        label="Singer Featherweight 221/222",
        comp=200.00, measured="2026-07-29", sample=60, comp_used_only=False,
        include=r"singer.{0,40}featherweight|featherweight.{0,30}(221|222)|"
                r"singer\s*22[12]\b",
        # The $56 p25 tail is attachments, cases, manuals and parts machines
        # sold under the same name - the classic accessory trap.
        #
        # 🚨 REWRITTEN 2026-08-16. The old version required the word "only"
        # ("attachments only", "case only", "foot only") and real listings do
        # not say it. Audited against all 36 Singer rows on the live production
        # board: 23 were parts and 17 of them were being priced against the
        # $200 whole-machine comp - "Singer Featherweight 221 Light Switch" at
        # $18 was quoted $124 of profit, an "electric switch" at $17 got $133.
        #
        # Featherweight parts are a cottage industry: switches, terminals,
        # hooks, faceplates, feed dogs, oil-can holders, buttonhole
        # attachments, case trays. Chasing them one noun at a time lost - this
        # is the whole component vocabulary plus the "fits / for Singer" tell.
        # 🚨 `\bcase\b` is safe HERE (a bare Featherweight case is a $50 item
        # people sell alone) but must never go in the universal guard, where
        # "camera w/ case" is a legitimate bundle.
        # Verified on those 36 titles: 11/11 real machines still price, 23/23
        # parts rejected, and there is a test carrying the same corpus.
        exclude=(
            # bundle-aware: "Machine w/ Case" is a COMPLETE machine and worth
            # more, so these five only fire when the accessory IS the product
            rf"{_BUNDLED}\bmanual\b|{_BUNDLED}attachments?\b|"
            rf"{_BUNDLED}\baccessor\w*|{_BUNDLED}\bcase\b|{_BUNDLED}\bpedal\b|"
            # unambiguous components - never sold as the machine
            r"bobbins?|foot control|motor only|light only|\bswitch\b|"
            r"\bterminal\b|\bhook\b|\bscrews?\b|\bknob\b|\bcover\b|"
            r"feed\s*dogs?|balance\s*wheel|face\s*plate|\bplate\b|oil can|"
            r"\btray\b|buttonhole|zig.?zag|blind stitch|stitch length|"
            r"\bhull\b|\bfits\b|\bwill fit\b|\bfor\s+(class|singer)\b|"
            r"\bparts?\b|\bwire\b|\biron\b|\bquilting\b|\b925\b|sampson|"
            r"scroll\s*plate|for parts|parts only|not working"),
        outbound_shipping=14.00, category="sewing",
        comp_query="singer featherweight 221", specificity=60,
        note="THE estate-sale machine: floor below the $238.71 median (n=60, "
             "clean machines $250-400, the rare white/222 free-arm $400+). "
             "Photo check: case, pedal and bobbin case present; decals uncracked.",
    ),

    # === Breadth pack (measured 2026-08-13, eBay used solds, n=123 per query)
    # ==========================================================================
    # The Discord digest was dominated by a handful of high-volume models
    # (Starrett/Mitutoyo/film cameras/camcorders, sorted by profit_at_open and
    # released top-N) while whole categories with measured live supply went
    # unpriced. Four new categories, all chosen for a reachable comp AND
    # visible live supply on the same channels the watcher already sweeps.

    # --- Watches -------------------------------------------------------------
    # High variance, so both comps are floored at p25 rather than the sold
    # median (the Fluke/Arc'teryx-generic convention) - a confident wrong
    # number here is worse than a conservative one.
    Model(
        key="casio_gshock",
        label="Casio G-Shock",
        comp=55.00, measured="2026-08-13", sample=123,
        include=r"g[- ]?shock",
        # "bezel and band set" / "band only" are the accessory FOR a G-Shock,
        # not the watch - same trap as the Starrett contact-point tips.
        # `g[- ]?shock` next to STYLE/INSPIRED/etc is the lookalike trap
        # instead: a bare-brand include can't tell a real Casio from a $2.25
        # no-name "G-Shock Style" digital.
        exclude=r"\bband\s+only\b|\bstrap\s+only\b|\bbezel\s+only\b|\bband\s+set\b|"
                r"\bbezel\s+set\b|\bbezel\s+and\s+band\b|\bband\s+and\s+bezel\b|"
                r"replacement\s+(band|strap|bezel)|watch\s+band\s+(for|only)|"
                r"for parts|parts only|not working|broken|"
                r"g[- ]?shock.{0,20}(" + LOOKALIKE_PHRASING + r")|"
                r"(" + LOOKALIKE_PHRASING + r").{0,20}g[- ]?shock",
        outbound_shipping=5.00, category="watches", comp_query="casio g-shock",
        comp_used_only=True, specificity=50,
        note="sold median $81, floor priced at p25; verify authenticity from "
             "caseback photo",
    ),
    Model(
        # 🚨 LADIES EXCLUDED below. Measured 2026-08-17: women's Seiko
        # automatic is p25 $40 / median $88 (n=223) against men's p25 $75 /
        # median $150 (n=218) - roughly HALF - and the ladies tier fails the
        # profit gate outright. A 25mm ladies Seiko 5 was quoted a $30 max bid
        # off this comp on a real listing. See DEAD_MODELS.
        key="seiko_automatic",
        label="Seiko Automatic watch",
        comp=67.00, measured="2026-08-13", sample=123,
        # A BRAND IS NOT A MODEL - same rule as Fluke/Starrett. Bare "Seiko"
        # is also quartz dress watches worth a fraction of this; the title
        # must name the automatic movement or a model line.
        include=r"seiko.{0,40}(automatic|divers?|5\b|presage|skx)|"
                r"(automatic|divers?|presage|skx).{0,40}seiko",
        # Same lookalike trap as G-Shock: "Seiko style automatic skeleton
        # watch" reads as a Seiko-inspired no-name, not the brand.
        exclude=_LADIES + r"|\bband\s+only\b|\bbracelet\s+only\b|\bstrap\s+only\b|"
                r"\bdial\s+only\b|\bmovement\s+only\b|\bcase\s+only\b|"
                r"\bcrown\s+only\b|for parts|parts only|not working|broken|"
                r"seiko.{0,20}(" + LOOKALIKE_PHRASING + r")|"
                r"(" + LOOKALIKE_PHRASING + r").{0,20}seiko",
        outbound_shipping=5.00, category="watches", comp_query="seiko automatic watch",
        comp_used_only=True, specificity=50,
        note="sold median $102, floor at p25; untested movement is the "
             "discount - winds/runs check; beware franken/mod dials",
    ),

    # --- Headphones ------------------------------------------------------------
    Model(
        key="bose_qc35",
        label="Bose QuietComfort 35",
        comp=59.95, measured="2026-08-13", sample=123,
        include=r"quietcomfort\s*(35|ii)|qc\s*35",
        # Ear pads are a flooded accessory market that shares the model name.
        exclude=r"ear\s*pads?|earpads?|cushions?|replacement|\bcase\s*only\b|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=5.00, category="headphones", comp_query="bose quietcomfort 35",
        comp_used_only=True, specificity=50,
    ),

    # --- Lenses ------------------------------------------------------------
    Model(
        key="canon_fd_50_14",
        label="Canon FD 50mm f/1.4",
        comp=52.00, measured="2026-08-13", sample=123,
        # Single continuous match (mm and f-stop in one span) so the pattern
        # can't fire twice on one listing - the DSC-W70 double-count trap.
        include=r"canon\s*fd[^a-z0-9]{0,12}50\s*mm[^a-z0-9]{0,15}"
                r"(1[.:]\s*1?[.]?4|f\s*/?\s*1\.4)",
        exclude=r"\bcap\s+only\b|body\s+cap|rear\s+cap|front\s+cap|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=5.00, category="lenses", comp_query="canon fd 50mm 1.4",
        comp_used_only=False, specificity=55,
        note="check for fungus/haze in photos; SSC Aspherical variant is a "
             "$500+ grail",
    ),

    # --- Walkman -----------------------------------------------------------
    Model(
        key="sony_walkman",
        label="Sony Walkman",
        comp=40.00, measured="2026-08-13", sample=123,
        # A BRAND IS NOT A MODEL, and neither is a bare product noun -
        # "walkman" needs the Sony brand or a WM- model number nearby.
        include=r"walkman.{0,40}(sony|wm[- ]?\w+)|(sony|wm[- ]?\w+).{0,40}walkman",
        exclude=r"\bcase\s*only\b|\bbelt\s*clip\s*only\b|\bheadphones\s*only\b|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=5.00, category="walkman", comp_query="sony walkman wm",
        comp_used_only=False, specificity=40,
        note="model is the trade: WM-D6C/DD/pro units sell $200-550 - read "
             "the model number off the photo before bidding; generic units "
             "are $40",
    ),

    Model(
        key="tinspire_cx",
        label="TI-Nspire CX",
        comp=45.00, measured="2026-07-25", sample=0,
        include=r"ti\s*-?\s*nspire\s*cx",
        exclude=r"\bcase only\b|for parts|parts only",
        outbound_shipping=5.00, comp_query="TI-Nspire CX",
        category="calculators",
        note="ESTIMATE, not measured - verify before trusting.",
    ),
    # === Category pivot pack (measured 2026-08-15) ==========================
    # Leron: "i dont want to flip clothes, i like the cameras, watches video
    # games and consoles". Apparel got benched below; these three spend the
    # freed Browse quota on the categories he actually wants. Every one was
    # measured the same session against eBay solds and had to clear the ~$80
    # floor to get in - a fourth candidate (stock Game Boy Color, $48.44) did
    # not and is recorded in DEAD_MODELS instead.
    Model(
        key="ps_vita",
        label="PlayStation Vita console",
        comp=150.00, measured="2026-08-19", sample=103, comp_used_only=False,
        # [12]\d{3}, not [12]0\d\d: PCH-1101 and PCH-1104 are real Vita
        # hardware and the old pattern missed both (caught on a live listing
        # 2026-08-16). Game SKUs are PCSE-/PCSB-, so this cannot swallow one.
        include=r"ps\s*vita|playstation\s*vita|\bpch-?\s*[12]\d{3}\b",
        exclude=r"\bgames?\b|\bcard\b|memory card|charger|\bcase\b|\bcover\b|"
                r"screen protector|for parts|parts only|broken|not working|"
                r"japan|japanese|\bjap\b|ntsc-j",
        outbound_shipping=8.00, category="videogames", comp_query="playstation vita console",
        specificity=40,
        # FLOOR below the $192.93 median (n=28, p25 $169.95, p75 $219.80). The
        # cheap tail is Japanese/region units and "console only Rank C" grades,
        # both of which a thrift find can easily be, so the book carries $150.
        note="Best comp of the three added 2026-08-15. Check the model: PCH-1000 "
             "(OLED) sells above PCH-2000 (LCD). Proprietary memory card is NOT "
             "included on most units and buyers know it - a bundled card is upside, "
             "its absence is not a defect. Japanese units are region-fine but comp "
             "lower; check the sticker." 
             "Re-measured 2026-08-19 on the ROUTED population (n=103, p25 $145.90, median $164.80): CONFIRMED the comp.",
    ),
    Model(
        key="dreamcast",
        label="Sega Dreamcast console",
        comp=95.00, measured="2026-08-19", sample=24, comp_used_only=False,
        # 🚨 Was a bare `dreamcast`, which is the exact mistake
        # `_console_include` exists to prevent. Routed against 5,249 sold
        # listings on 2026-08-19 it matched exterior SCREWS ($5.99), a modem
        # module, a VGA box and a Tremor Pak - all under $27, all priced
        # against a $95 console comp. This tier predates the platform pack that
        # gave every other console its hardware-evidence guard.
        include=_console_include(r"dreamcast", r"hkt-?\s*3020"),
        exclude=r"\bgames?\b|controller|\bvmu\b|\bdisc\b|memory card|"
                r"for parts|parts only|broken|not working|japan|japanese|\bjap\b|"
                r"ntsc-j|\bpal\b|region free|modded|modchip|\bgdemu\b",
        outbound_shipping=14.00, category="videogames", comp_query="sega dreamcast console",
        specificity=40,
        # FLOOR below the $123.07 median (n=9 clean of 59 matched, p25 $83.99).
        # n=9 is THIN because most Dreamcast sales are bundles - measure again
        # before paying near the ceiling.
        note="n=9 - THIN sample, re-measure before bidding near the max. Heavy: "
             "outbound is $14, not the $5 a cart costs, and that eats a third of "
             "the margin. GDEMU/modded units sell far higher and are a different "
             "product. Check the disc drive spins - it is the common failure." 
             "Re-measured 2026-08-19 on the ROUTED population (n=24, p25 $89.94, median $119.99): CONFIRMED the comp.",
    ),
    Model(
        key="citizen_campanola",
        label="Citizen Campanola / Satellite Wave / Attesa / The Citizen",
        comp=200.00, measured="2026-08-19", sample=54,
        include=_citizen(r"campanola|satellite\s*wave|\battesa\b|the\s+citizen|series\s*8"),
        exclude=_WATCH_JUNK,
        outbound_shipping=8.00, category="watches", specificity=64,
        comp_query="citizen campanola satellite wave watch",
        note="HELD at $200 on purpose, far below the measured p25 of $350 "
             "(n=54, median $840). The only tier the 2026-08-19 re-measure left "
             "alone: it is the halo tier, the spread runs to four figures, and "
             "raising a ceiling is the direction that loses money. If a title "
             "says Campanola, comp it by hand - the book is deliberately "
             "leaving money on the table here rather than guessing.",
    ),
    Model(
        key="citizen_promaster_chrono",
        label="Citizen Promaster chronograph (not a diver)",
        comp=150.00, measured="2026-08-19", sample=83,
        include=_citizen(r"promaster").replace(").*$", r")(?=.*chrono).*$"),
        exclude=_WATCH_JUNK + r"|" + _LADIES + r"|aqualand|\bdivers?\b|\bdiving\b|"
                r"\bwr\s?200\b|\bwr\s?300\b|\b200\s*m\b|\b300\s*m\b|\bbn0\d|\bny0\d",
        outbound_shipping=8.00, category="watches", specificity=63,
        comp_query="citizen promaster chronograph",
        note="FLOOR at p25 $150 of a $219 median (n=83, routed). Split OUT of "
             "the diver tier on 2026-08-19: the include there is "
             "`promaster|aqualand`, so every land chrono was being priced at the "
             "DIVER comp. Divers p25 $184, chronos p25 $150 - a real gap, "
             "though smaller than it looked from one lot. 🚨 The vintage 0610-* "
             "quartz chronos are the THIN end and disagree with each other: "
             "0610-H03299 (reverse panda) sold $125 and $150, while the "
             "0610-H03281 navy sold $225 twice. Comp the exact reference before "
             "bidding near the max.",
    ),
    Model(
        key="citizen_promaster",
        label="Citizen Promaster / Aqualand diver",
        comp=183.00, measured="2026-08-19", sample=587,
        include=_citizen(r"promaster|aqualand"),
        exclude=_WATCH_JUNK,
        outbound_shipping=8.00, category="watches", specificity=62,
        comp_query="citizen promaster diver watch",
        note="FLOOR at p25 $183 of a $260 median (n=587, routed - chronographs "
             "now go to citizen_promaster_chrono above). Dive models hold value "
             "far better than any dress Citizen. Was $200 on a n=31 sample.",
    ),
    Model(
        key="citizen_nighthawk",
        label="Citizen Nighthawk / Skyhawk / Blue Angels / Navihawk",
        comp=129.00, measured="2026-08-19", sample=307,
        include=_citizen(r"nighthawk|skyhawk|blue\s*angels|navihawk|red\s*arrows"),
        exclude=_WATCH_JUNK,
        outbound_shipping=8.00, category="watches", specificity=60,
        comp_query="citizen nighthawk skyhawk watch",
        note="FLOOR at p25 $129 of a $176 median (n=307, routed). The pilot "
             "line. Was $145 on n=12; the bigger sample moved it down, not up.",
    ),
    Model(
        key="citizen_ecodrive_chrono",
        label="Citizen Eco-Drive chronograph",
        comp=88.00, measured="2026-08-19", sample=146,
        # 🚨 the extra condition goes INSIDE the anchored group. Appending
        # `(?=.*chrono)` after `_citizen(...)` put it after the closing `.*$`,
        # i.e. at end-of-string, where it can never match.
        include=_citizen(r"eco.?drive") .replace(").*$", r")(?=.*chrono).*$"),
        exclude=_WATCH_JUNK + r"|" + _LADIES,
        outbound_shipping=8.00, category="watches", specificity=58,
        comp_query="citizen eco drive chronograph mens watch",
        note="FLOOR at p25 $88 of a $139 median (n=146, routed). 🚨 THIS WAS "
             "$145 AND IT WAS THE WORST NUMBER IN THE BOOK - 65% over. It came "
             "from running comp_query raw: 174 of those 329 solds are SKYHAWKS "
             "and PROMASTERS that route to a higher tier and never reach this "
             "one, and they dragged the median from $139 to $160. Measure a "
             "tier on what it RECEIVES, never on what its query returns.",
    ),
    Model(
        key="citizen_perpetual",
        label="Citizen Eco-Drive Perpetual Calendar",
        comp=85.00, measured="2026-08-19", sample=35,
        include=_citizen(r"perpetual\s*calendar"),
        exclude=_WATCH_JUNK,
        outbound_shipping=8.00, category="watches", specificity=56,
        comp_query="citizen eco drive perpetual calendar",
        note="HELD at $85, just under a re-measured p25 of $88 (n=35, median "
             "$152, 2026-08-19) - the routed sample CONFIRMED the old number, "
             "so it did not move. It had been set BETWEEN two samples that "
             "disagreed: broad Eco-Drive split n=21 p25 $66, targeted "
             "perpetual+bracelet n=38 p25 $99. "
             "🚨 Eco-Drive has a "
             "CAPACITOR, not a battery - a dead one is a $40-70 watchmaker job, "
             "not a $5 swap, and sellers routinely write 'needs a battery'. "
             "Charge it in sunlight for 3-5 DAYS before calling it dead.",
    ),
    Model(
        key="citizen_ecodrive_mens",
        label="Citizen Eco-Drive (men's, no complication)",
        comp=62.00, measured="2026-08-19", sample=227,
        include=_citizen(r"eco.?drive"),
        exclude=_WATCH_JUNK + r"|" + _LADIES + r"|chrono|perpetual|promaster|"
                r"nighthawk|skyhawk|blue\s*angels|navihawk|campanola|satellite\s*wave",
        outbound_shipping=8.00, category="watches", specificity=44,
        comp_query="citizen eco drive mens watch",
        note="FLOOR at p25 $62 of a $100 median (n=227, routed). Was $95 on a "
             "raw-query n=111 that still had the halo models in it. 🚨 LADIES "
             "Eco-Drive is a different product at $40 p25 and is DEAD - see "
             "DEAD_MODELS. The gender word in the title is the whole "
             "difference.",
    ),
    # 🚨 citizen_quartz_chrono WAS HERE AND IS NOW DEAD (2026-08-19).
    # Comped at $62 it looked like a live tier. Re-measured on the population it
    # actually receives it is p25 $43 / median $80 (n=43), which nets $28.90 and
    # quotes a max bid of $0.00 against the book's standing gate ($20 profit over
    # $9 inbound). It could never clear the bar; the inflated comp was the only
    # thing keeping it in the book. See DEAD_MODELS.
    Model(
        key="citizen_quartz_mens",
        label="Citizen quartz, men's (plain 3-hand)",
        comp=44.00, measured="2026-08-19", sample=149,
        include=_citizen(r"men'?s\b|mens\b"),
        exclude=_WATCH_JUNK + r"|" + _LADIES + r"|eco.?drive|chrono|perpetual|"
                r"promaster|nighthawk|skyhawk|navihawk|campanola|elegance",
        outbound_shipping=8.00, category="watches", specificity=30,
        comp_query="citizen mens quartz black dial watch",
        note="FLOOR at p25 $44 of a $75 median (n=149, routed) and the THINNEST "
             "margin in the Citizen book - max bid is about $1 before inbound "
             "shipping, so this only ever pays on a local pickup.",
    ),
]

# Models we deliberately refuse to alert on, so a lot containing one isn't
# mistaken for a payday. Keyed by why.
DEAD_MODELS = {
    r"ti\s*-?\s*83\s*plus": "TI-83 Plus sells $25.37 -> max buy -$3.39 (measured 2026-07-25)",
    r"ti\s*-?\s*30x|ti\s*-?\s*36x|ti\s*-?\s*34": "TI-30/34/36 scientifics sell under $12",
    # Measured 2026-08-15 while filling out the console category and REJECTED,
    # recorded so nobody measures it a third time. A STOCK Game Boy Color sells
    # $48.44 (n=3 clean of 44 matched) - under the ~$80 floor where eBay's fixed
    # costs leave room. The high GBC sales are all IPS/backlit/custom-shell mods,
    # which is a different product we don't buy. Contrast the GBA SP, which is in
    # the book at $84.59 precisely because it clears.
    # CONSOLE NOUN REQUIRED, both directions. Bare `game boy color|gbc` also
    # matches "Pokemon Crystal Gameboy Color" - a $145 cart we DO want - and
    # would have stapled a "this is dead" warning onto every legitimate GBC
    # cartridge alert. Same family as the console-noun guards on GameCube/N64.
    r"(game\s*boy\s*color|\bgbc\b)[^a-z0-9]{0,12}(console|system|handheld)|"
    r"(console|system|handheld)[^a-z0-9]{0,12}(game\s*boy\s*color|\bgbc\b)":
        "stock Game Boy Color sells $48.44 - under the $80 floor (measured 2026-08-15); "
        "only IPS/backlit MODS sell higher and those aren't thrift finds",

    # --- Measured 2026-08-16 in the platform pack and REFUSED ----------------
    # Nine platforms cleared nothing. Each was measured the same way as the
    # models above (eBay solds, filtered by its own include/exclude, p25 taken)
    # and then failed the ship test: max_pay(comp) had to beat the LIVE Goodwill
    # median for that platform by >$5. These are recorded rather than dropped so
    # the next person to ask "why no Wii?" reads a number instead of re-measuring.
    #
    # 🚨 EVERY PATTERN HERE REQUIRES A CONSOLE NOUN, both directions - the
    # Game Boy Color lesson. Bare `\bwii\b` would staple "this is dead" onto
    # every Wii GAME, and bare `\bnes\b` onto every NES cartridge. A dead
    # CONSOLE says nothing about the carts that run on it.
    r"(\bwii\b)(?!\s*u)<GAP>(console|system)|"
    r"(console|system)<GAP>(\bwii\b)(?!\s*u)":
        "plain Nintendo Wii sells $44.99 (n=47, measured 2026-08-16) -> max pay "
        "$7.96 against a $12.99 Goodwill median. Wii U is different and IS priced",
    r"xbox\s*360<GAP>(console|system)|"
    r"(console|system)<GAP>xbox\s*360":
        "Xbox 360 sells $69.95 (n=75, measured 2026-08-16) -> max pay $11.76 "
        "against a $9.99 Goodwill median. $1.77 of headroom is not a trade",
    r"(\bsnes\b|super\s*nintendo)<GAP>(console|system)|"
    r"(console|system)<GAP>(\bsnes\b|super\s*nintendo)":
        "SNES console sells $85 (n=56, measured 2026-08-16) -> max pay $20.43 "
        "against a $19.99 Goodwill median. Boxed/1CHIP units are a different trade",
    r"(\bnes\b|nintendo entertainment system)<GAP>(console|system)|"
    r"(console|system)<GAP>(\bnes\b|nintendo entertainment system)":
        "NES console sells $60 (n=35 THIN, measured 2026-08-16) -> max pay $8.87 "
        "against a $17 Goodwill median. Negative headroom",
    r"(sega\s*genesis|\bgenesis\b)<GAP>(console|system)|"
    r"(console|system)<GAP>(sega\s*genesis|\bgenesis\b)":
        "Sega Genesis sells $59.99 (n=52, measured 2026-08-16) -> max pay $0.00. "
        "The whole Sega shelf (77 live on Goodwill) is worth walking past",
    r"(\bds\s*lite\b|\busg\s*-?\s*001\b)":
        "Nintendo DS Lite sells $58 (n=78, measured 2026-08-16) -> max pay $18.60 "
        "against a $19.99 Goodwill median. DSi XL and 2DS XL DO clear - check which",
    r"((game\s*boy|gameboy)\s*pocket|\bmgb\s*-?\s*001\b)":
        "Game Boy Pocket sells $49.90 (n=154, measured 2026-08-16) -> max pay "
        "$6.29 against a $21 Goodwill median. Cheapest handheld measured",
    # 🚨 ANCHORED, and it MUST be - this is the only watch entry carrying a
    # NEGATIVE lookahead. Unanchored, `re.search` retries at every offset, so
    # `(?!.*eco.?drive)` would eventually succeed at a position PAST the words
    # it is meant to veto and condemn every Eco-Drive chrono. Same trap the
    # `_citizen` docstring describes, in the opposite direction.
    r"^(?=.*\bcitizen\b)(?=.*chrono)"
    r"(?!.*(eco.?drive|promaster|aqualand|nighthawk|skyhawk|blue\s*angels|"
    r"navihawk|red\s*arrows|campanola|satellite\s*wave|attesa)).*$":
        "Citizen quartz chronograph that is NOT Eco-Drive sells p25 $43 / "
        "median $80 (n=43, measured 2026-08-19) -> max bid $0.00 at the "
        "$20-over-$9 gate. Was carried at a $62 comp that came from an "
        "unrouted query; nothing at this comp can clear the bar. "
        "'Needs a battery' PROVES it is not Eco-Drive.",

    r"finepix":
        "Fujifilm FinePix sells p25 $40.60 / median $60.88 (n=218, measured "
        "2026-08-19) -> max bid $0.00 at the $20-over-$9 gate. Carried at a $45 "
        "comp that was above its own measured floor; even there the ceiling was "
        "$3.64. A compact that only ever paid as a sub-$12 shelf find.",

    r"\bipod\s*nano\b":
        "iPod Nano sells p25 $35 / median $50 (n=251, measured 2026-08-19) -> "
        "max bid $0.00 at the $20-over-$9 gate. Was carried at $49 on n=123.",
    r"\bipod\s*touch\b":
        "iPod Touch sells p25 $19.50 / median $29.99 (n=160, measured "
        "2026-08-19) -> max bid $0.00. Was carried at $39.99 and even there the "
        "ceiling was $0.29, so it had effectively been dead for a while.",

    # --- POKEMON CARD LOTS, measured 2026-08-20 and REFUSED ---------------
    # 🚨 THE HONEST ANSWER TO "make the pokemon cards price": MOST OF THEM
    # CANNOT BE PRICED FROM A TITLE, and saying so is worth more than a number.
    #
    # An unsorted vintage lot ran p25 $10.72, median $25.18, max $1,061 on 65
    # sold listings - a hundred-fold spread, because the value is in WHICH
    # cards are in the pile and what condition they are in, and the title says
    # neither. At the standing gate ($20 over $9 inbound, $5 out) a card tier
    # needs a comp near $40 before ANY bid clears, so a $10.72 floor quotes
    # $0.00 and a median-based comp would be a guess with money behind it.
    #
    # What IS priceable is where the title states the price driver: a GRADE, or
    # a named chase card from the vintage era. Those three tiers are live - see
    # pkmn_card_graded_high / pkmn_card_graded / pkmn_card_vintage_chase.
    # 🚨 DOES NOT REQUIRE THE WORD "CARD". Leron's own watch list had
    # "(3) 2000 Dark Pokémon - Charizard, Flareon, Etc", which is plainly a
    # card lot and never says "card" - it fell through with no tier AND no
    # reason, which is the one outcome worse than either.
    #
    # 🚨 AND IT MUST NOT SWALLOW A GAME LOT. "Lot of 10 Game Boy Advance games
    # incl Pokemon Emerald" is deliberately still priced (see
    # test_pokemon_in_a_lot_still_prices_as_pokemon), so the console vocabulary
    # is excluded here rather than in the caller.
    r"^(?=.*pok[eé]mon|.*\bpkmn\b)"
    r"(?=.*(?:\blots?\b|\bbulk\b|\bcollection\b|\bbinder\b|\(\d+\)))"
    r"(?!.*(?:\bgba\b|game\s*boy|gameboy|\bgbc\b|cartridge|\bcart\b|"
    r"\bconsole\b|\bhandheld\b|nintendo\s*(?:ds|3ds|switch|64)|\bvideo\s*game\b))"
    r"(?!.*\b(?:psa|bgs|cgc)\s*\d).*$":
        "An unsorted Pokemon CARD LOT sells p25 $10.72 / median $25.18 on n=65 "
        "(measured 2026-08-20) with a max of $1,061 - a 100x spread the title "
        "cannot resolve, and $0.00 of room at the $20-over-$9 gate. Value is "
        "which cards and what condition; buy these off the PHOTOS or not at "
        "all. A graded slab or a named vintage chase card DOES price - see the "
        "pokemon-cards tiers.",

    # A RAW VINTAGE SINGLE THAT NAMES NO CHASE CARD. Measured on n=357 holo
    # singles from 1999-2003: p25 $10.50, median $23.26 - which is $0.00 of
    # room at the standing gate, and non-holo commons are cheaper still. The
    # chase names carry this category; everything else is bulk with a date on
    # it. (Leron's "2002 Pokemon Pikachu 124" is exactly this shape.)
    r"^(?=.*pok[eé]mon|.*\bpkmn\b)(?=.*\b(?:199\d|200[0-4])\b)"
    r"(?!.*(?:charizard|blastoise|venusaur|lugia|umbreon|espeon|mewtwo|\bmew\b|"
    r"rayquaza|gengar|dragonite|shining))"
    r"(?!.*\b(?:psa|bgs|cgc)\s*\d)"
    r"(?!.*(?:\bgba\b|game\s*boy|gameboy|\bgbc\b|cartridge|\bcart\b|\bconsole\b|"
    r"\bhandheld\b|nintendo\s*(?:ds|3ds|switch|64)|\bvideo\s*game\b)).*$":
        "A raw vintage Pokemon single naming no chase card sells p25 $10.50 / "
        "median $23.26 (n=357, measured 2026-08-20) -> $0.00 of room at the "
        "$20-over-$9 gate. Name a chase card or show a grade and it prices; "
        "otherwise it is bulk with a date on it.",

    # --- WATCH AND CAMCORDER TIERS, measured 2026-08-17 and REFUSED -------
    # Each failed the book's bar ($20 target profit over $9 inbound). They
    # are recorded rather than dropped because every one of them was a real
    # listing Leron asked about, and the old brand-level comps said BUY.
    r"(?=.*\bcitizen\b)(?=.*eco.?drive)(?=.*(\bladies\b|\bwomen'?s?\b|\bwomens\b))":
        "LADIES Citizen Eco-Drive sells p25 $40 / median $85 (n=143, measured "
        "2026-08-17) -> $0.00 max bid. The men's equivalent is p25 $101. "
        "Gender is the biggest price variable in this category",
    r"(?=.*\bcitizen\b)(?=.*\belegance\b)":
        "Citizen Elegance / Elegance Signature sells p25 $23.95 / median $37 "
        "(n=223, measured 2026-08-17) -> $0.00 max bid. Caliber 1112 is a "
        "basic Miyota quartz; the name sounds premium and is not",
    r"(?=.*\bcitizen\b)(?=.*(\bladies\b|\bwomen'?s?\b|\bwomens\b))"
    r"(?!.*(eco.?drive|promaster|campanola|perpetual))":
        "LADIES Citizen quartz sells p25 $19.79 / median $28 (n=215, measured "
        "2026-08-17) -> $0.00 max bid. Gold-TONE is plating and it wears "
        "through; this is the cheapest tier the brand makes",
    r"dcr\s*-?\s*dvd|(?=.*handycam)(?=.*\bdvd\b)":
        "DVD Handycams sell p25 $30 / median $49.99 (n=204; DCR-DVD92 exactly "
        "$41.16, n=99, measured 2026-08-17) -> $0.00 max bid. THE FORMAT IS "
        "THE VALUE: tape-era units fetch $135 because buyers digitise "
        "cassettes, and a DVD already plays in any computer",
    r"(?=.*\bseiko\b)(?=.*(automatic|\b5\b))(?=.*(\bladies\b|\bwomen'?s?\b|\bwomens\b))":
        "LADIES Seiko automatic sells p25 $40 / median $88 (n=223, measured "
        "2026-08-17) vs men's p25 $75 / median $150 -> $0.00 max bid",
    r"(\bdmg\s*-?\s*01\b|(game\s*boy|gameboy)\s*(original|classic|brick))":
        "original DMG-01 Game Boy sells $68 (n=82, measured 2026-08-16) -> max pay "
        "$21.92 against a $25 Goodwill median. Negative headroom",
    # Carts measured 2026-08-16 alongside the four that shipped, and cut on the
    # book's real bar ($20 target profit + $9 inbound), which is stricter than
    # a raw max_pay: all four yield a max bid at or near $0.00.
    r"fire\s*emblem\b(?!.*(awakening|fates|three\s*houses|echoes))":
        "Fire Emblem (GBA) loose sells $51 (n=131, measured 2026-08-16) -> $0.00 "
        "max bid at $20 target profit. Common in GBA lots and still not a trade",
    r"golden\s*sun":
        "Golden Sun (GBA) loose sells $30 (n=113, measured 2026-08-16) -> $6.29 "
        "ceiling before inbound shipping. The Lost Age is scarcer - check which",
    r"advance\s*wars\b(?!.*(dual\s*strike|days\s*of\s*ruin))":
        "Advance Wars (GBA) loose sells $29.15 (n=69, measured 2026-08-16) - "
        "tight $19-$65 range and no room under it",
    r"metroid\s*(fusion|zero\s*mission)":
        "Metroid Fusion / Zero Mission loose sells $39.99 (n=68, measured "
        "2026-08-16) -> $10.62 ceiling. Sealed copies are a different product",
    # Not a platform - a SHAPE, and the one entry here that is a CAUTION rather
    # than a refusal. 🚨 DEAD_MODELS only annotates a listing that ALREADY
    # matched a model; it cannot reject one that matches nothing. So this never
    # fires on a bare "Nintendo Gameboy Games 5pc" (nothing matches that, and
    # nothing should). What it DOES catch is the case that misprices: a console
    # or cart we can price, listed as part of a games lot, where our comp covers
    # the ONE named item and the lot's real value is whatever else is in the box.
    # Measured 2026-08-16 for why no lot model exists: GBA lots median $35 over
    # a $1.93-$450 range (n=221), Game Boy $39.99, DS $25, PS2 $30 - no stable
    # comp at any size. The named-title lookahead keeps a "lot of GBA games
    # incl. Pokemon Emerald" alerting cleanly as Emerald.
    r"^(?=.*\b(games|video games)\b)(?=.*\b(lot|bundle|\d+\s*pcs?)\b)"
    r"(?!.*(pok[eé]mon|pokeman|zelda|minish|oracle\s*of|link'?s\s*awakening|"
    r"castlevania|aria\s*of\s*sorrow)).*$":
        "this is a LOT and the comp above prices only the one item named in the "
        "title - a generic game lot has no comp of its own (measured 2026-08-16: "
        "GBA lots median $35 across a $1.93-$450 range, n=221). Count what's in "
        "the photos before treating the rest as free upside",
}

DEAD_MODELS = {
    pat.replace("<GAP>", _NOUN_GAP): why for pat, why in DEAD_MODELS.items()
}

BY_KEY = {m.key: m for m in MODELS}


@dataclass
class Match:
    """Which paying models a listing appears to contain, and how many."""

    model: Model
    units: int = 1
    evidence: str = ""
    dead_also_present: list = field(default_factory=list)


# A repeated model name only means multiple units when the listing is actually a
# multi-item one. Sellers repeat the model for search ranking: "Like New FLUKE 175
# Fluke 175 True RMS Digital Multimeter" is ONE meter, and counting it as two
# doubled the ceiling to $522 on a $323 item.
MULTI_EVIDENCE = re.compile(
    # "3X Zoom" / "5x Optical" is a LENS SPEC, not a lot of three - the camera
    # models added 2026-07-28 put a zoom spec in nearly every title, and
    # "DSC-W70 3X Zoom" was counted as two cameras (see count_units below).
    r"\blot\b|\bpair\b|\bset of\b|\bbundle\b|\bboth\b|\bx\s*[2-9]\b|"
    r"\b[2-9]\s*x\b(?!\s*(optical|zoom|digital|wide|telephoto))|"
    r"\(\s*[2-9]\s*\)|\b[2-9]\s*(pc|pcs|piece|pieces|units?|calculators?|meters?|games?)\b|"
    r"\bqty\s*[2-9]\b|\btwo\b|\bthree\b|\bfour\b"
)


# An explicit quantity at the HEAD of the title: "LOT (4) MITUTOYO ...",
# "Lot of 4 Mitutoyo ...", "4x Mitutoyo ...", "(4) Mitutoyo ...".
#
# 🚨 THE COUNT MUST SIT IMMEDIATELY BEFORE THE MODEL, and that restriction is
# the whole safety of this. A quantity describes the LOT, not necessarily the
# thing we matched: "LOT (4) CAMERA BAGS AND A CANON AE-1" is four bags and ONE
# camera, and counting four cameras would quadruple the ceiling - the expensive
# direction to be wrong in. So the model's matched text has to start within a
# few characters of the count, which is true of "LOT (4) MITUTOYO" and false of
# anything with other goods in between.
_LEAD_QTY = re.compile(
    r"^\s*(?:lot\s*(?:of\s*)?)?\(?\s*(\d{1,2})"
    # 🚨 A leading number is often NOT a count. Measured on the live board:
    #   6.75" Silver Toned Citizen Eco-Drive   -> a WRIST SIZE, read as x6
    #   2-Tone Stainless CITIZEN Eco Drive     -> a COLOUR, read as x2
    # So reject a decimal, a compound adjective, and any dimension unit.
    r"""(?![\d.\-"'′″]|\s*(?:mm|cm|in\b|inch|ft\b|k\b))"""
    r"\s*\)?\s*(?:x\b\s*|pc\.?s?\b\s*|pieces?\b\s*)?", re.I)

# Above this a "lot of N" is bulk junk (screws, cards) rather than N sellable
# units, and pricing it per-unit would be absurd.
_MAX_LEAD_QTY = 12


def _leading_quantity(t: str, model: Model) -> int:
    """N from an explicit head-of-title count, or 1."""
    m = _LEAD_QTY.match(t)
    if not m:
        return 1
    try:
        n = int(m.group(1))
    except (TypeError, ValueError):
        return 1
    if not 2 <= n <= _MAX_LEAD_QTY:
        return 1
    # 🚨 A WHOLE-TITLE ASSERTION MATCHES AT POSITION 0, ALWAYS. Models built by
    # _citizen / _console_include compile to "^(?=.*a)(?=.*b).*$", so the
    # adjacency test below is meaningless for them - everything looks adjacent.
    # That produced exactly the over-count this guard exists to prevent, on the
    # live board: "Lot of 4 Watches Seiko Quartz ... Citizen Eco-Drive" priced
    # FOUR Citizens out of a mixed-brand lot. For assertion models the count
    # cannot be tied to the thing matched, so it is not trusted at all.
    if model.include.lstrip().startswith("^(?="):
        return 1
    hit = re.search(model.include, t)
    if not hit:
        return 1
    # The model must be what the count is counting - i.e. it starts right where
    # the count phrase ends, not after a list of other goods.
    if hit.start() - m.end() > 12:
        return 1
    return n


def count_units(title: str, model: Model) -> int:
    """How many of `model` the title claims.

    Repeated mentions only count when the title ALSO shows multi-item evidence
    (lot / pair / set of / x3 / "4 pcs"). Without that, a second mention is SEO
    keyword stuffing, not a second unit - and over-counting inflates the max bid
    directly, which is the expensive direction to be wrong in.
    """
    t = normalize(title)
    # Count repeats of the SAME matched text, not total alternation hits. An
    # include like `cyber-?shot|dsc-\w+` matches "Sony Cybershot DSC-W70"
    # TWICE - two different words naming ONE camera - and with any lot word in
    # the title that doubled the ceiling on a single unit ($85 max bid on a
    # $75-comp camera, caught on the live board 2026-07-28). Only the same
    # string appearing again ("TI-84 Plus CE & TI-84 Plus CE") is a repeat.
    counts: dict[str, int] = {}
    for m in re.finditer(model.include, t):
        s = " ".join(m.group(0).split())
        counts[s] = counts.get(s, 0) + 1
    hits = max(counts.values(), default=0)
    if hits > 1 and MULTI_EVIDENCE.search(t):
        return hits
    # 🚨 An explicit count beats repetition, and used to be ignored entirely:
    # "LOT (4) MITUTOYO 0-1\" DIGITAL MICROMETERS" names the model once, so
    # this returned 1 and priced four micrometers as one - a $30 ceiling on a
    # lot worth $191 (measured 2026-08-19). Every multi-item lot was
    # under-priced the same way.
    return _leading_quantity(t, model)

# --- benched categories -----------------------------------------------------
# Leron 2026-08-15: "i dont want to flip clothes, i like the cameras, watches
# video games and consoles". Benched by CATEGORY rather than by picking off 11
# keys, so this survives someone adding a twelfth jacket later.
#
# Benched, not deleted, on purpose. These comps were MEASURED (Arc'teryx Beta
# $250.52, Veronica Beard $150, Gunne Sax $122) and remeasuring costs a browser
# session each. `active=False` means: never swept, never alerted, still priceable
# by hand with `flipscout item`. Un-bench by emptying this set.
BENCHED_CATEGORIES = {"outerwear", "womens-apparel"}

# The categories where a card IS the product rather than merchandise borrowing
# a product's name. See _CARD_MERCH.
CARD_CATEGORIES = {"pokemon-cards", "sports-cards", "cards"}

# 🚨 THE GUARD THAT WAS SILENTLY EATING THE CARD TIERS. Measured 2026-08-22:
# five of seven realistic graded-Pokemon titles were rejected before any model
# was consulted, and the only two that survived did so by NOT containing the
# word "card" -
#
#     REJECT  Pokemon Charizard PSA 10 Card
#     REJECT  1999 Pokemon Base Set Charizard Holo PSA 9 Trading Card
#     REJECT  Pokemon Card PSA 10 Umbreon VMAX Alt Art
#     MATCH   Pokemon Charizard PSA 10
#
# The rule is right and its reasons are real: a Pokemon PROMO CARD must never
# price against a $145 cartridge comp (same family as the Pikachu crystal ball
# that quoted a $100 max bid on a plastic toy), and cameras legitimately read
# "w/ SD Card", which is why it is bundle-aware.
#
# What was wrong is that it lived in ACCESSORY_EXCLUDE, which `match()`
# evaluates ONCE per listing before any model is consulted and which no model
# can override. So the three card tiers - measured 2026-08-20 on n=111/256/142,
# a browser session each - could only ever match a card listing that does not
# say "card". They were built, comped, and then made unreachable.
#
# Applied by category instead, exactly as _CAMERA_JUNK and _CONSOLE_JUNK are
# ("the rule belongs to the category, so state it once"). Every non-card model
# keeps the identical guard; the card tiers are freed from it.
_CARD_MERCH = (
    r"trading card|(?<!sd )(?<!cf )(?<!xd )(?<!gb )(?<!memory )(?<!sim )"
    r"(?<!& )(?<!, )(?<!and )(?<!w/ )(?<!with )(?<!\+ )(?<!\+)(?<!user )\bcards?\b"
)

MODELS = [replace(m, active=False) if m.category in BENCHED_CATEGORIES else m
          for m in MODELS]

# 🚨 THE ACCESSORY GUARD IS APPLIED BY CATEGORY, NOT PASTED INTO 20 EXCLUDES.
# Every camera tier needs it (see _CAMERA_JUNK for what it cost not to have
# one), and pasting it into each `exclude=` means the twenty-first tier someone
# adds next month silently ships without it. Same reasoning as benching by
# category above: the rule belongs to the category, so state it once.
MODELS = [replace(m, exclude=(m.exclude + "|" + _CAMERA_JUNK) if m.exclude
                             else _CAMERA_JUNK)
          if m.category == "cameras" else m
          for m in MODELS]

# Same rule, same reason, for consoles - see _CONSOLE_JUNK.
MODELS = [replace(m, exclude=(m.exclude + "|" + _CONSOLE_JUNK) if m.exclude
                             else _CONSOLE_JUNK)
          if m.category == "videogames" else m
          for m in MODELS]

# ...and for the Pokemon carts, which needed the guard pointed the other way -
# see _PKMN_JUNK.
MODELS = [replace(m, exclude=(m.exclude + "|" + _PKMN_JUNK) if m.exclude
                             else _PKMN_JUNK)
          if m.category == "pokemon" else m
          for m in MODELS]

# ...and the card-merchandise guard everywhere a card is NOT the product. This
# is the pass that used to live in ACCESSORY_EXCLUDE - see _CARD_MERCH.
MODELS = [replace(m, exclude=(m.exclude + "|" + _CARD_MERCH) if m.exclude
                             else _CARD_MERCH)
          if m.category not in CARD_CATEGORIES else m
          for m in MODELS]

# Rebuilt AFTER both passes - the dict above was keyed off the pre-replace
# objects, so anything reading BY_KEY would otherwise see a model whose
# excludes and active flag disagree with the one `match()` actually uses.
BY_KEY = {m.key: m for m in MODELS}


def match(title: str) -> Optional[Match]:
    """Best paying model in this title, or None.

    Prefers the most specific model (CE Python over CE) so a Python doesn't get
    priced as a base CE.
    """
    t = normalize(title)
    if not t:
        return None
    # Universal guards ONCE, not once per model - see Model._body_matches.
    if universally_excluded(t):
        return None
    hits = [m for m in MODELS if m._body_matches(t)]
    if not hits:
        return None
    # most specific wins, by declared specificity (see Model.specificity)
    best = max(hits, key=lambda m: m.specificity)
    dead = [why for pat, why in DEAD_MODELS.items() if re.search(pat, t)]
    return Match(model=best, units=count_units(t, best),
                 evidence=best.label, dead_also_present=dead)


def comp_search(model: Model) -> str:
    """The search phrase that reproduces this model's measured comp."""
    return model.comp_query or model.label


def search_terms() -> list[str]:
    """Queries to push at every source. Deliberately broader than the models -
    junk-titled lots ("SCIENTIFIC CALCULATOR BULK LOT") hide the good models, and
    that mismatch between title and contents is where the edge lives."""
    return [
        # calculators
        "ti-84 plus ce", "ti-84", "ti 84 plus ce", "ti-nspire",
        "graphing calculator", "calculator lot", "texas instruments calculator",
        "scientific calculator lot",
        # ipods - the generic terms matter, sellers rarely put the capacity first
        "ipod classic", "ipod video", "apple ipod", "ipod lot",
        "ipod nano", "ipod touch",
        # pokemon carts - include the junk-title phrasings, since a "game boy lot"
        # that happens to contain Emerald is the whole point
        "pokemon gameboy", "pokemon game boy advance", "pokemon gba",
        "gameboy game lot", "game boy advance lot", "nintendo handheld lot",
        "pokemon game",
        # test gear / metrology / medical - these live in estate, industrial and
        # government surplus, which is exactly what HiBid aggregates
        "fluke multimeter", "fluke meter", "fluke", "multimeter",
        "mitutoyo", "starrett", "micrometer", "dial indicator", "machinist tools",
        "machinist tool lot", "precision tools lot",
        "littmann", "stethoscope",
        # APPAREL BENCHED 2026-08-15 (Leron: "i dont want to flip clothes").
        # Removed here, not just deactivated in the book: these 11 terms were
        # spending ~15% of the 5k/day Browse quota fetching listings that can
        # now never alert. The comps stay in MODELS behind BENCHED_CATEGORIES.
        #   was: "arcteryx", "arc'teryx", "patagonia", "patagonia jacket",
        #        "gore-tex jacket", "gunne sax", "st john knit", "johnny was",
        #        "veronica beard", "reformation dress", "womens dress lot"
        # cameras - estate sales and thrift shelves are full of them, and the
        # junk-titled boxes ("vintage camera lot") are where the mju-II hides
        "canon powershot", "canon g7x", "canon elph", "sony cybershot",
        "sony cyber-shot", "nikon coolpix", "fujifilm finepix",
        "digital camera", "digital camera lot", "camera lot", "vintage camera lot",
        "canon ae-1", "pentax k1000", "olympus stylus", "polaroid sx-70",
        "35mm film camera", "point and shoot camera",
        "sony handycam", "camcorder",
        # cordless tools + vintage sewing - estate/surplus staples
        "milwaukee m18", "m18 fuel", "milwaukee combo kit",
        "dewalt 20v", "dewalt drill", "cordless drill", "power tool lot",
        "singer featherweight", "vintage sewing machine",
        # game consoles (added 2026-07-30 - "add video games", budget past $100)
        "nintendo switch oled", "gameboy advance sp", "game boy advance sp",
        "nintendo 3ds xl", "gamecube console", "nintendo 64 console",
        "video game console", "game console lot", "nintendo console",
        # high-ticket cameras (added 2026-07-30 - single flips clearing $100-400)
        "fujifilm x100", "fuji x100", "contax t2", "contax camera",
        "canon 5d", "sony a6000", "sony alpha camera", "gopro hero",
        "mirrorless camera", "dslr camera",
        # MISSPELLINGS, on purpose (2026-07-30): typo'd titles get no search
        # traffic, so they close cheap - the classic dead-listing edge. Each
        # term here has a book include that still matches the typo'd title
        # (brand-agnostic patterns like "sx-70"/"stylus"/"coolpix", `can+on`,
        # `mit[aiu]t[ou]yo`, `starr?ett?`, `(pok[eé]mon|pokeman)`).
        "cannon ae-1", "cannon camera", "olympis stylus", "olimpus stylus",
        "mitatoyo", "mititoyo", "starret", "polariod sx-70", "polariod camera",
        "nikkon coolpix", "pokeman", "gameboy advanced pokemon",
        "cybershot camera", "handy cam sony",
        # breadth pack (added 2026-08-13): watches, headphones, lenses, walkman -
        # categories with measured live supply that the digest never priced
        "sony walkman", "walkman lot", "bose quietcomfort", "casio g-shock",
        "g shock watch", "seiko automatic", "seiko watch lot", "canon fd",
        "vintage camera lens", "camera lens lot",
        # category pivot pack (added 2026-08-15, paid for by benching apparel):
        # handhelds/consoles and the second watch brand. "watch lot" earns its
        # place the way "calculator lot" does - a junk-titled box of watches is
        # where a $124 Citizen hides behind a $10 title.
        "ps vita", "playstation vita", "sega dreamcast", "dreamcast console",
        "citizen eco-drive", "citizen watch", "watch lot", "wristwatch lot",
        # === platform pack (added 2026-08-16) ================================
        # The console terms above were Nintendo-only, so 276 live PlayStation
        # and 125 Xbox listings were never even fetched. Measured live on
        # 2026-08-16 against Leron's four links: 3 of the 4 WERE already being
        # fetched and died at match(), but #273589008 ("Nintendo Gameboy Games
        # 5pc") was reached by no term at all - "nintendo games" fixes that.
        "playstation 5", "playstation 4", "ps4 console", "playstation 3",
        "playstation 2", "ps2 console", "sony psp",
        "xbox series x", "xbox one", "xbox console",
        "nintendo switch", "switch lite", "wii u",
        "nintendo 2ds", "nintendo dsi", "steam deck",
        # Goodwill's search is substring-fuzzy, so the bare-platform terms above
        # pull the accessories too; that is fine, the book rejects them. What we
        # cannot recover from is never fetching the listing at all.
        "nintendo games", "video game lot", "retro video games",
        "handheld game console",
        # === card pack (added 2026-08-22) ====================================
        # 🚨 NOTHING HERE SEARCHED FOR CARDS. Measured on 2026-08-22: of 133
        # terms, the only ones containing "card" or "pokemon" were VIDEO GAME
        # searches ("pokemon gba", "gameboy advanced pokemon"). So the three
        # MEASURED Pokemon card tiers - pkmn_card_graded_high, pkmn_card_graded,
        # pkmn_card_vintage_chase, comped 2026-08-20 on n=111/256/142 - could
        # only ever fire BY ACCIDENT, when a card happened to land in a Game Boy
        # search. Pricing was built, measured and then never fed.
        #
        # The first three terms below fix exactly that and are the ones that pay
        # today, because they are the only card terms with a comp behind them.
        "pokemon cards", "pokemon card lot", "pokemon psa",
        # The rest feed hunt.scout_cards(), which has NO comp and posts no
        # ceiling - see that function for why an unpriced alert is allowed to
        # exist in this repo at all. Deliberately lot- and grade-shaped: a
        # junk-titled box of cards is where the one card that matters hides
        # behind a $10 title, which is the same reason "calculator lot" and
        # "watch lot" earn their place above.
        "sports card lot", "baseball card lot", "basketball card lot",
        "football card lot", "trading card lot", "graded card lot",
        "psa 10", "psa graded card",
        "topps chrome", "panini prizm", "bowman chrome",
        # Sealed wax is the one card product a TITLE can fully identify - no
        # condition variable at all - so it is the only card category that could
        # ever carry a measured comp. Fetching it now so there is a population
        # to measure when that happens.
        "sealed hobby box", "factory sealed cards",
    ]

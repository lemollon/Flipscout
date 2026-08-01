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
from dataclasses import dataclass, field
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


# Things that carry the product's NAME but are not the product. Caught live on
# 2026-07-25: "Pokemon Emerald Version Official Game Guide - Prima Games GBA
# Strategy Book" matched the Emerald model and was quoted a $198 max bid. It is a
# paperback worth about $15. Applied to EVERY model, because this failure mode is
# universal - guides, boxes, manuals, cases and posters all share the title.
ACCESSORY_EXCLUDE = (
    r"strategy\s*guide|game\s*guide|player'?s?\s*guide|prima\s*games|nintendo\s*power|"
    r"\bguide\b|\bbook\b|paperback|magazine|poster|\bposter\b|sticker|decal|"
    r"\bempty\b|box only|case only|cover only|manual only|insert only|label only|"
    r"shell only|display only|\breplica\b|\bpromo\b|advertisement|\bad\b|"
    # `card` is bundle-aware: camera listings legitimately read "w/ SD Card" /
    # "64GB Memory Card", and the cameras added 2026-07-28 were being silently
    # rejected over their own bundled storage. A trading/promo card still trips.
    r"trading card|(?<!sd )(?<!cf )(?<!xd )(?<!gb )(?<!memory )(?<!sim )"
    r"(?<!& )(?<!, )(?<!and )(?<!w/ )(?<!with )(?<!\+ )(?<!\+)(?<!user )\bcards?\b|"
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
    r"(?<!with )(?<!w/ )(?<!& )(?<!, )(?<!and )(?<!\+ )(hard|soft|carrying|travel|storage|protective|camera)\s+case|"
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
    # this must never grow a console name.
    r"\bfor\s+(canon|nikon|sony|fuji(film)?|olympus|panasonic|pentax|polaroid|kodak|gopro)\b|"
    r"\(\s*\d+\s*(pack|pcs|ch)\s*\)"
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

    def matches(self, title: str) -> bool:
        t = normalize(title)
        if not t:
            return False
        # The universal accessory guard runs first and is not overridable per
        # model: a guide/box/poster is never the thing, in any category.
        if re.search(ACCESSORY_EXCLUDE, t):
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
        comp=149.99, measured="2026-07-25", sample=21,
        include=r"ipod\s*(classic)?[^a-z0-9]{0,6}160\s*gb|160\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only|cable only",
        outbound_shipping=6.00, category="ipods", comp_query="ipod classic 160gb",
        specificity=30,
    ),
    Model(
        key="ipod_classic_120",
        label="iPod Classic 120GB",
        comp=136.07, measured="2026-07-25", sample=8,
        include=r"ipod\s*(classic)?[^a-z0-9]{0,6}120\s*gb|120\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only",
        outbound_shipping=6.00, category="ipods", comp_query="ipod classic 120gb",
        specificity=30,
    ),
    Model(
        key="ipod_classic_80",
        label="iPod Classic/Video 80GB",
        comp=135.60, measured="2026-07-25", sample=11,
        include=r"ipod\s*(classic|video)?[^a-z0-9]{0,6}80\s*gb|80\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only",
        outbound_shipping=6.00, category="ipods", comp_query="ipod classic 80gb",
        specificity=30,
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
        note="HIGH VALUE = high repro risk. Verify the cart before bidding; "
             "comp sample is small (n=7) and search-biased.",
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
        note="n=2 - treat as an ESTIMATE. Re-measure before trusting.",
    ),
    Model(
        key="pkmn_firered_leafgreen",
        label="Pokemon FireRed / LeafGreen (GBA)",
        comp=76.49, measured="2026-07-25", sample=0, comp_used_only=False,
        include=r"(pok[eé]mon|pokeman)\s*(fire\s*red|leaf\s*green)",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon fire red gameboy advance",
        specificity=40,
        note="Verify authenticity before bidding.",
    ),
    Model(
        key="pkmn_ruby_sapphire",
        label="Pokemon Ruby / Sapphire (GBA)",
        comp=71.99, measured="2026-07-25", sample=0, comp_used_only=False,
        include=r"(pok[eé]mon|pokeman)\s*(ruby|sapphire)",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon ruby gameboy advance",
        specificity=40,
        note="Verify authenticity before bidding.",
    ),
    Model(
        key="pkmn_rby",
        label="Pokemon Red / Blue / Yellow (GB)",
        comp=50.15, measured="2026-07-25", sample=0, comp_used_only=False,
        include=r"(pok[eé]mon|pokeman)\s*(red|blue|yellow)\b",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon yellow gameboy",
        specificity=40,
        note="Save battery is usually dead - it does not stop a sale but mention it.",
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
        comp=175.00, measured="2026-07-30", sample=60,
        include=r"switch\s*oled",
        # "for switch"/"for nintendo" is the accessory tell, scoped to THIS
        # model - console names must never enter the universal camera-brand
        # guard (the Donkey Kong lesson).
        exclude=r"\bfor\s+(the\s+)?(nintendo|switch)\b|joy.?cons?\s+only|dock only|"
                r"\bcase\b|\bskin\b|screen protector|\bstand\b|\bgrip\b|charger only|"
                r"tablet only|console only|\blite\b|game only|for parts|parts only|"
                r"not working|broken",
        outbound_shipping=10.00, category="videogames",
        comp_query="nintendo switch oled console", specificity=40,
        note="FLOOR below the $201.73 median (n=60): tablet-only units sell "
             "$150-170 and are EXCLUDED - complete console w/ dock+joycons only.",
    ),
    Model(
        key="gba_sp_101",
        label="Game Boy Advance SP AGS-101 (backlit)",
        comp=130.00, measured="2026-07-30", sample=47,
        include=r"ags\s*-?\s*101|backlit\s*(game\s*boy|gba|sp)|(gba|sp)\s*backlit",
        exclude=r"\bips\b|modded|custom|shell|housing|repro|for parts|parts only|"
                r"not working|broken|box only|charger only",
        outbound_shipping=5.00, category="videogames",
        comp_query="gameboy advance sp ags-101", specificity=30,
        note="FLOOR below the $136.58 median (n=47, AGS-001 bleed in the tail). "
             "The BACKLIT screen is the trade: AGS-101 vs 001 is 1.6x. Verify "
             "the label says AGS-101 in the photo.",
    ),
    Model(
        key="gba_sp",
        label="Game Boy Advance SP (AGS-001/unspecified)",
        comp=80.00, measured="2026-07-30", sample=51,
        include=r"game\s*boy\s*advance\s*sp|gameboy\s*advance\s*sp|\bags\s*-?\s*001\b",
        exclude=r"ags\s*-?\s*101|backlit|\bips\b|modded|custom|shell|housing|repro|"
                r"for parts|parts only|not working|broken|box only|charger only",
        outbound_shipping=5.00, category="videogames",
        comp_query="gameboy advance sp", specificity=25,
        note="FLOOR below the $84.59 median (n=51). If the photo shows AGS-101 "
             "on the label, it's the $130+ backlit model - re-check.",
    ),
    Model(
        key="n3ds_xl",
        label="Nintendo 3DS XL / New 3DS XL",
        comp=145.00, measured="2026-07-30", sample=59,
        include=r"3ds\s*(xl|ll)",
        exclude=r"\b2ds\b|circle pad|cradle only|stylus only|charger only|"
                r"\bcase\b|for parts|parts only|not working|broken|box only",
        outbound_shipping=6.00, category="videogames",
        comp_query="nintendo 3ds xl console", specificity=28,
        note="FLOOR at p25 of a $209.02 median (n=59) - Japanese 'New 3DS LL' "
             "imports inflate the top. US 'New 3DS XL' sells $250+.",
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
        comp=95.00, measured="2026-07-30", sample=53,
        include=r"(nintendo\s*64|\bn\s*-?64\b)\s*(console|system)",
        exclude=r"controller only|expansion pak only|jumper pak|\bcase\b|"
                r"for parts|parts only|not working|broken|box only|cover|door",
        outbound_shipping=10.00, category="videogames",
        comp_query="nintendo 64 console", specificity=26,
        note="FLOOR at ~p25 of a $152.83 median (n=53, game-cart bleed in the "
             "search). Funtastic colors and boxed bundles sell $180+.",
    ),

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
        comp=1145.90, measured="2026-07-28", sample=11,
        include=r"g7\s*x.{0,25}mark\s*(iii\b|3\b)",
        exclude=r"for parts|parts only|not working|broken|\brepair\b",
        outbound_shipping=6.00, category="cameras",
        comp_query="canon powershot g7x mark iii", specificity=66,
        note="Vlogger-boom pricing - verify it powers on; a broken pop-up flash "
             "unit still sold for $606.",
    ),
    Model(
        key="g7x_mark2",
        label="Canon PowerShot G7X Mark II",
        comp=1149.35, measured="2026-07-28", sample=27,
        include=r"g7\s*x.{0,25}mark\s*(ii\b|2\b)",
        exclude=r"for parts|parts only|not working|broken|\brepair\b",
        outbound_shipping=6.00, category="cameras",
        comp_query="canon powershot g7x mark ii", specificity=65,
        note="The single most valuable item in the book. TikTok made this THE "
             "camera; it sold for $699 new in 2016.",
    ),
    Model(
        key="g7x",
        label="Canon PowerShot G7X (Mark I / unspecified)",
        comp=708.18, measured="2026-07-28", sample=13,
        include=r"g7\s*x",
        exclude=r"for parts|parts only|not working|broken|\brepair\b",
        outbound_shipping=6.00, category="cameras",
        comp_query="canon powershot g7x", specificity=60,
        note="Mark I / unmarked floor. A Mark II/III is worth $440 more - read "
             "the photos before settling for this number.",
    ),
    # --- High-ticket cameras (measured 2026-07-30). Leron's budget goes past
    # $100/item, so the book now carries the models where a single flip clears
    # $100-400. Same hype driver as the G7X: compact "vibe" cameras boom.
    Model(
        key="fuji_x100v",
        label="Fujifilm X100V",
        comp=1300.00, measured="2026-07-30", sample=8,
        include=r"x100v\b",        # \b keeps the newer X100VI out of this comp
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
        comp=800.00, measured="2026-07-30", sample=60,
        include=r"x100f\b",
        exclude=r"for parts|parts only|not working|broken|repair",
        outbound_shipping=8.00, category="cameras",
        comp_query="fujifilm x100f", specificity=63,
        note="FLOOR below the $879.99 used median (n=60). X100S/T lookalikes "
             "sell ~$500-700 and deliberately do NOT match.",
    ),
    Model(
        key="contax_t2",
        label="Contax T2 (35mm compact)",
        comp=1100.00, measured="2026-07-30", sample=44,
        include=r"contax\s*t2\b",
        exclude=r"for parts|parts only|not working|broken|repair|data back only",
        outbound_shipping=8.00, category="cameras",
        comp_query="contax t2 camera", comp_used_only=False, specificity=62,
        note="FLOOR below the $1,296.49 median (n=44). THE estate-sale grail - "
             "a film point-and-shoot relatives donate for nothing. Untested "
             "units still clear $800+; T3 comps even higher (unmeasured).",
    ),
    Model(
        key="canon_5d3",
        label="Canon EOS 5D Mark III",
        comp=400.00, measured="2026-07-30", sample=57,
        include=r"5d\s*mark\s*iii|5d\s*mk\s*iii|\b5d3\b",
        # \b after ii keeps this from swallowing Mark II ($250) titles; the
        # include's explicit iii keeps Mark IV ($900) from matching either.
        exclude=r"mark\s*ii\b|mk\s*ii\b|mark\s*iv|mk\s*iv|for parts|parts only|"
                r"not working|broken|repair|shutter assembly|focusing screen|"
                r"body cap|battery grip only",
        outbound_shipping=10.00, category="cameras",
        comp_query="canon 5d mark iii", specificity=56,
        note="$416.07 used median (n=57), floored to $400. Check shutter count "
             "if stated; body-only is the normal sale.",
    ),
    Model(
        key="sony_a6000",
        label="Sony a6000 (mirrorless)",
        comp=350.00, measured="2026-07-30", sample=55,
        include=r"\ba6000\b|ilce\s*-?\s*6000",
        exclude=r"lens only|body cap|for parts|parts only|not working|broken|repair",
        outbound_shipping=8.00, category="cameras",
        comp_query="sony a6000 camera", specificity=55,
        note="FLOOR below the $406.07 median (n=55, mostly WITH kit lens). "
             "Body-only sells lower - price the kit, not the bare body.",
    ),
    Model(
        key="gopro_hero11",
        label="GoPro HERO 11 Black",
        comp=160.00, measured="2026-07-30", sample=17,
        include=r"hero\s*-?\s*11\b",
        exclude=r"session|mount|frame only|housing only|lens cover|door|"
                r"battery only|charger only|for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="gopro hero 11 black", specificity=52,
        note="FLOOR covering the Mini variant (~$158); full-size medians "
             "$204.63 (n=17). Hero 9/10/12 are unmeasured - do not assume.",
    ),
    Model(
        key="sony_rx100",
        label="Sony RX100 / ZV-1 (1-inch compact)",
        comp=541.99, measured="2026-07-28", sample=5,
        # HX99 used to ride along here; it's a 1/2.3-inch travel zoom, NOT an
        # RX100-class 1-inch, and no HX99 comp was ever measured - the sentry
        # nearly advised raising toward $434 on one (2026-07-30). Unpriced
        # until someone measures it; a missing comp beats a wrong one.
        include=r"rx\s*-?\s*100|\bzv\s*-?\s*1\b",
        exclude=r"for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="sony rx100 camera", specificity=62,
        note="n=5 - thin sample, and later marks (M3-M7) comp higher than the "
             "original. Re-measure before bidding near the max.",
    ),
    Model(
        key="powershot_elph",
        label="Canon PowerShot ELPH / IXUS (digital)",
        comp=120.00, measured="2026-07-28", sample=52,
        include=r"\belph\b|\bixus\b|\bixy\b",
        # The 1990s APS-film Elph (Elph 2/Jr/LT/260Z/370Z) shares the name and
        # sells for $6 - one sold mid-sweep. "film camera" kills those.
        exclude=r"\baps\b|film camera|\belph\s*(2|jr|lt)\b|\b(260z?|370z?|490z?)\b|"
                r"for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="canon powershot elph", specificity=55,
        note="CONSERVATIVE FLOOR below the $184.03 median (n=52, range $72-365): "
             "early SD-series sell $65-140, named-ELPH models $150-350. Confirm "
             "the sub-model before bidding near the max.",
    ),
    Model(
        key="sony_cybershot",
        label="Sony Cyber-shot compact (non-RX)",
        comp=75.00, measured="2026-07-28", sample=47,
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
             "near the max.",
    ),
    Model(
        key="nikon_coolpix",
        label="Nikon Coolpix compact",
        comp=55.00, measured="2026-07-28", sample=51,
        include=r"coolpix",
        exclude=r"for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="nikon coolpix camera", specificity=50,
        note="CONSERVATIVE FLOOR below the $97.98 median (n=51, range $30-368), "
             "set under the AA-battery L-series ($40-70) so their tail can't "
             "lose money; S/P-series sell $100-180.",
    ),
    Model(
        key="fujifilm_finepix",
        label="Fujifilm FinePix compact",
        comp=45.00, measured="2026-07-28", sample=53,
        include=r"finepix",
        exclude=r"for parts|parts only|not working|broken",
        outbound_shipping=6.00, category="cameras",
        comp_query="fujifilm finepix camera", specificity=50,
        note="THINNEST margin in the book: floor below the $66.06 median (n=53). "
             "Only worth it under ~$12 all-in - a $5 Goodwill shelf find, "
             "nothing more.",
    ),
    Model(
        key="sony_handycam",
        label="Sony Handycam camcorder",
        comp=120.00, measured="2026-07-28", sample=53,
        include=r"handycam|\bdcr\s*-|\bccd\s*-\s*tr|\bhdr\s*-\s*(cx|xr|pj|sr)",
        # Tape lots borrow the name ("Hi8 tapes for Sony Handycam"), and the
        # DVD-era models are the measured-cheap end ($15-71) - excluded.
        exclude=r"\btapes?\b|cassette|\bdvd\b|for parts|parts only|not working|broken",
        outbound_shipping=10.00, category="cameras",
        comp_query="sony handycam camcorder", specificity=50,
        note="CONSERVATIVE FLOOR below the $163.18 median (n=53). Tape-era "
             "(Video8/Hi8/MiniDV) units sell $90-200 for tape-transfer use; "
             "include the charger in the photo check - proprietary batteries.",
    ),
    # --- film cameras (comps measured WITHOUT the Used filter) ----------------
    Model(
        key="olympus_mju2",
        label="Olympus mju-II / Stylus Epic (non-zoom)",
        comp=484.85, measured="2026-07-28", sample=12, comp_used_only=False,
        # The fixed-lens f/2.8 Epic IS the mju-II and sells 2.8x the Zoom
        # variants. "Zoom" in the title demotes it to the model below.
        include=r"mju\s*-?\s*(ii\b|2\b)|stylus\s+epic\b",
        exclude=r"\bzoom\b|for parts|parts only|not working|broken|damaged",
        outbound_shipping=6.00, category="cameras",
        comp_query="olympus stylus epic mju", specificity=62,
        note="The model IS the trade: fixed-lens f/2.8 sells $485 (n=12, range "
             "$300-690), the Zoom versions $176. Confirm NO 'Zoom' on the body.",
    ),
    Model(
        key="stylus_epic_zoom",
        label="Olympus Stylus Epic Zoom 80/115/170",
        comp=175.68, measured="2026-07-28", sample=28, comp_used_only=False,
        include=r"stylus\s+(epic\s+)?zoom\s*(80|115|170)|epic\s+zoom",
        exclude=r"for parts|parts only|not working|broken|damaged",
        outbound_shipping=6.00, category="cameras",
        comp_query="olympus stylus epic zoom", specificity=58,
        note="Film-revival pricing on a 90s drugstore camera. Untested units "
             "sold $15-40, so the working comp only applies if it powers on.",
    ),
    Model(
        key="canon_ae1",
        label="Canon AE-1 / AE-1 Program (35mm SLR)",
        comp=150.20, measured="2026-07-28", sample=31, comp_used_only=False,
        # `can+on` also catches the constant "Cannon" misspelling - one sold
        # for full price under it during the sweep.
        include=r"can+on.{0,50}\bae\s*-?\s*1\b|\bae\s*-?\s*1\b.{0,50}can+on|"
                r"\bae\s*-?\s*1\s*program\b",
        exclude=r"for parts|parts only|not working|broken",
        outbound_shipping=9.00, category="cameras",
        comp_query="canon ae-1 camera", specificity=55,
        note="Comp is body+lens (how they're found and sold). 1/3 of solds are "
             "Japan imports at a discount. Mechanical: listen for the 'AE-1 "
             "squeal' note in the listing; 'film tested' is the magic phrase.",
    ),
    Model(
        key="pentax_k1000",
        label="Pentax K1000 (35mm SLR)",
        comp=139.99, measured="2026-07-28", sample=45, comp_used_only=False,
        include=r"k\s*-?\s*1000\b",
        # The Pentax KM/ME/MX read almost identically and comp differently.
        exclude=r"\bkm\b|\bme\s+super\b|\bmx\b|for parts|parts only|not working|broken",
        outbound_shipping=9.00, category="cameras",
        comp_query="pentax k1000 camera", specificity=55,
        note="The perpetual photo-class camera - demand never dies. Comp is "
             "body+50mm; body-only sold $49-85.",
    ),
    Model(
        key="polaroid_sx70",
        label="Polaroid SX-70 (folding)",
        comp=99.99, measured="2026-07-28", sample=35, comp_used_only=False,
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
             "untested only $40-85, so bid the condition you're actually buying.",
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
        exclude=r"\bmanual\b|attachments?\s+(only|lot)|bobbins?|\bcase only\b|"
                r"foot only|pedal only|motor only|light only|"
                r"for parts|parts only|not working|\bscroll\s*plate",
        outbound_shipping=14.00, category="sewing",
        comp_query="singer featherweight 221", specificity=60,
        note="THE estate-sale machine: floor below the $238.71 median (n=60, "
             "clean machines $250-400, the rare white/222 free-arm $400+). "
             "Photo check: case, pedal and bobbin case present; decals uncracked.",
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
]

# Models we deliberately refuse to alert on, so a lot containing one isn't
# mistaken for a payday. Keyed by why.
DEAD_MODELS = {
    r"ti\s*-?\s*83\s*plus": "TI-83 Plus sells $25.37 -> max buy -$3.39 (measured 2026-07-25)",
    r"ti\s*-?\s*30x|ti\s*-?\s*36x|ti\s*-?\s*34": "TI-30/34/36 scientifics sell under $12",
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
    if hits <= 1:
        return 1
    return hits if MULTI_EVIDENCE.search(t) else 1


def match(title: str) -> Optional[Match]:
    """Best paying model in this title, or None.

    Prefers the most specific model (CE Python over CE) so a Python doesn't get
    priced as a base CE.
    """
    t = normalize(title)
    if not t:
        return None
    hits = [m for m in MODELS if m.matches(t)]
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
        # technical outerwear - the unglamorous end, where nobody is bidding
        "arcteryx", "arc'teryx", "patagonia", "patagonia jacket", "gore-tex jacket",
        # cameras - estate sales and thrift shelves are full of them, and the
        # junk-titled boxes ("vintage camera lot") are where the mju-II hides
        "canon powershot", "canon g7x", "canon elph", "sony cybershot",
        "sony cyber-shot", "nikon coolpix", "fujifilm finepix",
        "digital camera", "digital camera lot", "camera lot", "vintage camera lot",
        "canon ae-1", "pentax k1000", "olympus stylus", "polaroid sx-70",
        "35mm film camera", "point and shoot camera",
        "sony handycam", "camcorder",
        # women's apparel - the measured-empty channels (Johnny Was and St John
        # specifically live in HiBid estate lots)
        "gunne sax", "st john knit", "johnny was", "veronica beard",
        "reformation dress", "womens dress lot",
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
    ]

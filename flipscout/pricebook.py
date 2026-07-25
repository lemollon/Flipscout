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
    r"trading card|\bcard\b|keychain|plush|figure|pin\b|"
    # Accessories FOR a tool, which read almost identically to the tool. These
    # MUST live in the universal guard, not on one model: per-model excludes do
    # not compose. "Starrett No 25R Dial Indicator Contact Point Set" was rejected
    # by `dial_indicator` and then quietly matched the broader `starrett` model,
    # which priced a ~$15 bag of tips at $81.95.
    r"contact point|point set|\btips?\s*(set|kit|assortment)|"
    r"attachment only|holder only|bezel only|crystal only"
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
        note="CE Python variant comps higher; treat this as the floor.",
    ),
    Model(
        key="ti84ce_python",
        label="TI-84 Plus CE Python",
        comp=70.00, measured="2026-07-25", sample=0,
        include=r"ti\s*-?\s*84\s*plus\s*ce\s*python|ce\s*python",
        exclude=r"\bcase only\b|for parts|parts only",
        outbound_shipping=5.00, category="calculators", comp_query="TI-84 Plus CE Python",
        specificity=20,
        note="ESTIMATE, not measured - verify with `flipscout comp` before trusting.",
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
        include=r"pokemon\s*emerald",
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
        include=r"pokemon\s*crystal",
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
        include=r"pokemon\s*(fire\s*red|leaf\s*green)",
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
        include=r"pokemon\s*(ruby|sapphire)",
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
        include=r"pokemon\s*(red|blue|yellow)\b",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon yellow gameboy",
        specificity=40,
        note="Save battery is usually dead - it does not stop a sale but mention it.",
    ),
    Model(
        key="pkmn_gold_silver",
        label="Pokemon Gold / Silver (GBC)",
        comp=38.99, measured="2026-07-25", sample=0, comp_used_only=False,
        include=r"pokemon\s*(gold|silver)\b",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", comp_query="pokemon gold gameboy color",
        specificity=40,
        note="Save battery is usually dead - it does not stop a sale but mention it.",
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
        comp=323.62, measured="2026-07-25", sample=2,
        include=r"fluke\s*17[579]",
        exclude=r"\bprobe|leads only|holster|test lead|fish|anchor",
        outbound_shipping=9.00, category="test-gear", comp_query="fluke 179 multimeter",
        specificity=50,
        note="n=2 - ESTIMATE, re-measure before leaning on it.",
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
        include=r"mitutoyo.{0,40}(micrometer|caliper|indicator|gage|gauge|height|depth|"
                r"bore|dial|scale|protractor)|(micrometer|caliper|indicator|gage|gauge)"
                r".{0,40}mitutoyo",
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
        include=r"starrett.{0,40}(micrometer|caliper|indicator|gage|gauge|square|level|"
                r"protractor|dial|height|depth|precision|toolmaker|surface plate)|"
                r"(micrometer|caliper|indicator|gage|gauge).{0,40}starrett",
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
        include=r"(starrett|mitutoyo|brown\s*&?\s*sharpe|interapid|federal).{0,40}indicator",
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
        comp=325.00, measured="2026-07-25", sample=4,
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
        comp=140.80, measured="2026-07-25", sample=1,
        include=r"arc'?\s*teryx.{0,30}atom",
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|glove|\bshirt\b|beanie",
        outbound_shipping=9.00, category="outerwear", specificity=60,
        comp_query="arcteryx atom jacket",
        note="n=1 - ESTIMATE ONLY. Verify with `flipscout comp` before trusting.",
    ),
    Model(
        key="arcteryx_fleece",
        label="Arc'teryx fleece (Delta/Kyanite)",
        comp=100.57, measured="2026-07-25", sample=8,
        include=r"arc'?\s*teryx.{0,30}(delta|kyanite|fleece)",
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|glove|\bshirt\b|beanie",
        outbound_shipping=9.00, category="outerwear", specificity=60,
        comp_query="arcteryx fleece jacket",
    ),
    Model(
        key="arcteryx_generic",
        label="Arc'teryx (unspecified model)",
        comp=70.00, measured="2026-07-25", sample=60,
        include=r"arc'?\s*teryx",
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|\bcap\b|glove|"
                r"\bshirt\b|\bsock|beanie|\bbag\b|backpack|\bcase\b|sticker",
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
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|glove|\bshirt\b|beanie",
        outbound_shipping=9.00, category="outerwear", specificity=60,
        comp_query="patagonia nano puff jacket",
    ),
    Model(
        key="patagonia_generic",
        label="Patagonia (unspecified)",
        comp=60.03, measured="2026-07-25", sample=60,
        include=r"patagonia",
        exclude=r"\bkids?\b|toddler|baby|youth|\bdog\b|\bhat\b|\bcap\b|glove|"
                r"\bshirt\b|\bsock|beanie|\bbag\b|backpack|sticker|\bshorts?\b",
        outbound_shipping=9.00, category="outerwear", specificity=55,
        comp_query="patagonia jacket",
        note="Thin margin ($22.68 max buy) - only worth it because Goodwill "
             "Patagonia sits at $9.99 with 78% of listings drawing zero bids.",
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
    r"\blot\b|\bpair\b|\bset of\b|\bbundle\b|\bboth\b|\bx\s*[2-9]\b|\b[2-9]\s*x\b|"
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
    hits = len(re.findall(model.include, t))
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
    ]

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
    r"trading card|\bcard\b|keychain|plush|figure|pin\b"
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
        outbound_shipping=5.00, category="calculators", specificity=10,
        note="CE Python variant comps higher; treat this as the floor.",
    ),
    Model(
        key="ti84ce_python",
        label="TI-84 Plus CE Python",
        comp=70.00, measured="2026-07-25", sample=0,
        include=r"ti\s*-?\s*84\s*plus\s*ce\s*python|ce\s*python",
        exclude=r"\bcase only\b|for parts|parts only",
        outbound_shipping=5.00, category="calculators", specificity=20,
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
        outbound_shipping=6.00, category="ipods", specificity=30,
    ),
    Model(
        key="ipod_classic_120",
        label="iPod Classic 120GB",
        comp=136.07, measured="2026-07-25", sample=8,
        include=r"ipod\s*(classic)?[^a-z0-9]{0,6}120\s*gb|120\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only",
        outbound_shipping=6.00, category="ipods", specificity=30,
    ),
    Model(
        key="ipod_classic_80",
        label="iPod Classic/Video 80GB",
        comp=135.60, measured="2026-07-25", sample=11,
        include=r"ipod\s*(classic|video)?[^a-z0-9]{0,6}80\s*gb|80\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only",
        outbound_shipping=6.00, category="ipods", specificity=30,
    ),
    Model(
        key="ipod_video_30",
        label="iPod Video 30GB (5th gen)",
        comp=100.90, measured="2026-07-25", sample=15,
        include=r"ipod\s*(classic|video)?[^a-z0-9]{0,6}30\s*gb|30\s*gb[^a-z0-9]{0,6}ipod",
        exclude=r"for parts|parts only|not working|broken|\bcase only\b|charger only",
        outbound_shipping=6.00, category="ipods", specificity=30,
    ),

    # --- Pokemon Game Boy carts (measured 2026-07-25) ------------------------
    # The best margins in the book. TWO real dangers, both encoded below:
    #  1. REPRODUCTION CARTS ARE EVERYWHERE. Repros are the single biggest way to
    #     lose money here, and they are hard to spot in a listing photo. `exclude`
    #     catches the honest sellers who say so; nothing catches the dishonest
    #     ones, so treat every alert as "verify before bidding", not "buy".
    #  2. The comps below came from a search containing the word "authentic",
    #     which skews toward sellers asserting legitimacy - i.e. these medians are
    #     probably optimistic. Small n on several. Re-measure with `flipscout comp`
    #     before leaning on the high-value ones.
    Model(
        key="pkmn_emerald",
        label="Pokemon Emerald (GBA)",
        comp=271.99, measured="2026-07-25", sample=7,
        include=r"pokemon\s*emerald",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", specificity=40,
        note="HIGH VALUE = high repro risk. Verify the cart before bidding; "
             "comp sample is small (n=7) and search-biased.",
    ),
    Model(
        key="pkmn_crystal",
        label="Pokemon Crystal (GBC)",
        comp=276.30, measured="2026-07-25", sample=2,
        include=r"pokemon\s*crystal",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", specificity=40,
        note="n=2 - treat as an ESTIMATE. Re-measure before trusting.",
    ),
    Model(
        key="pkmn_firered_leafgreen",
        label="Pokemon FireRed / LeafGreen (GBA)",
        comp=127.49, measured="2026-07-25", sample=10,
        include=r"pokemon\s*(fire\s*red|leaf\s*green)",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", specificity=40,
        note="Verify authenticity before bidding.",
    ),
    Model(
        key="pkmn_ruby_sapphire",
        label="Pokemon Ruby / Sapphire (GBA)",
        comp=119.99, measured="2026-07-25", sample=5,
        include=r"pokemon\s*(ruby|sapphire)",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", specificity=40,
        note="Verify authenticity before bidding.",
    ),
    Model(
        key="pkmn_rby",
        label="Pokemon Red / Blue / Yellow (GB)",
        comp=83.59, measured="2026-07-25", sample=10,
        include=r"pokemon\s*(red|blue|yellow)\b",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", specificity=40,
        note="Save battery is usually dead - it does not stop a sale but mention it.",
    ),
    Model(
        key="pkmn_gold_silver",
        label="Pokemon Gold / Silver (GBC)",
        comp=64.99, measured="2026-07-25", sample=9,
        include=r"pokemon\s*(gold|silver)\b",
        exclude=r"repro|reproduction|fake|custom|not authentic|\bcase only\b|"
                r"box only|manual only|for parts|parts only",
        outbound_shipping=5.00, category="pokemon", specificity=40,
        note="Save battery is usually dead - it does not stop a sale but mention it.",
    ),

    Model(
        key="tinspire_cx",
        label="TI-Nspire CX",
        comp=45.00, measured="2026-07-25", sample=0,
        include=r"ti\s*-?\s*nspire\s*cx",
        exclude=r"\bcase only\b|for parts|parts only",
        outbound_shipping=5.00, category="calculators",
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


def count_units(title: str, model: Model) -> int:
    """How many of `model` the title claims.

    Titles like "Lot of 4 Calculators: TI-84 Plus, TI-84 Plus CE, Casio" name the
    models present, so a repeated mention is a genuine count. We deliberately do
    NOT read a leading "Lot of 4" as 4 units of the paying model - that number
    counts everything in the box, most of which is worthless.
    """
    t = normalize(title)
    hits = len(re.findall(model.include, t))
    return max(1, hits)


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
    ]

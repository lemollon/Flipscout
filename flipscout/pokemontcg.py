"""What a Pokemon card sells for - keyed on THE CARD, not on the category.

WHY THIS EXISTS
---------------
Leron, 2026-08-22: *"the ebay range is nice but its wide for every card"*. He was
being generous. Measured against the live board the same afternoon, the two
`pokemon-cards` tiers in `pricebook` were not merely wide, they were **blanket
numbers applied to a population that spans four orders of magnitude**:

    1999 Pokemon Abra #43 PSA 8          book comp $92.00   max bid $35.50
    1999 Pokemon Poliwhirl #38 PSA 7     book comp $92.00   max bid $35.50
    Pokemon PSA 10 Litten                book comp $112.50  max bid $49.41
    1999 Jungle Clefable Holo #1 PSA 7   book comp $92.00   max bid $35.50

The first three are commons. Their ungraded market prices are **$1.54, $1.84 and
$0.32**. The fourth is a 1st-edition Jungle holo worth **$122.03** raw, and it
was capped at the same $35.50 as the Abra - so the book was set up to overpay
30x on the junk and lose the one card in the box that mattered. 29 of those 30
listings were HiBid lots, which is the armable path.

🚨 A CATEGORY-WIDE COMP CANNOT WORK IN THIS CATEGORY, EVER. A card's price is
its IDENTITY - set, card number, printing, grade - and no percentile of a
mixed population approximates it. This is the same lesson as
`pricebook`'s routed re-measures, except no amount of routing fixes it: the
population is genuinely 10,000 different products wearing one category name.

THE SOURCE
----------
`api.pokemontcg.io` - free, no key required (a free key raises the daily
allowance), updated daily, and **not eBay**. Given a card it returns TCGplayer's
market price per PRINTING:

    Umbreon #32, Neo Discovery 2001   1st Edition $300.00   Unlimited  $85.60
    Umbreon #32, Skyridge 2003        normal      $279.99   reverse holo $499.99

That is one number per printing rather than a range across a category, which is
exactly the thing that was missing.

🚨 THE MARKET PRICE IS FOR THE RAW CARD. It is NOT a graded comp, and this
module never pretends otherwise: for a slab it is reported as the FLOOR the
cardboard is worth before grading, and `verdict()` refuses to set a ceiling on
a graded card rather than invent a grade multiplier. A PSA 8 of a $1.54 Abra is
worth the slab, not $35.50; a PSA 8 of a $122 Clefable is worth several hundred.
The raw number separates those two cases decisively without guessing either one.

WHAT IT DOES NOT COVER
----------------------
* Japanese cards - the API is English-only. They are flagged, not priced, and
  🚨 unlike video games a Japanese Pokemon card is NOT junk (`pricebook` excludes
  Japanese CARTRIDGES for good reason; do not carry that rule across).
* Graded prices - needs a grade-keyed source (PriceCharting's columns, PSA's
  auction prices realized). Named in the memory note, not wired here.
* Sealed product - booster boxes and ETBs are a different market.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from .pokemon_names import SPECIES

API = "https://api.pokemontcg.io/v2/cards"
_TIMEOUT = 20

# Longest first, so "ho-oh" is found before "ho" and "mr-mime" before "mr".
_BY_LEN = tuple(sorted(SPECIES, key=len, reverse=True))
_SPECIES_SET = frozenset(SPECIES)

# The mechanic suffixes that are part of the card's NAME in the API
# ("Gardevoir-GX", "Dragapult VMAX"), not decoration on it.
_SUFFIX = (r"vmax|vstar|v-?union|\bv\b|gx|ex|break|prime|lv\.?\s*x|"
           r"delta\s*species|star")

_GRADER = re.compile(
    r"\b(psa|bgs|cgc|sgc|ace|tag|hga|csg)\s*\.?\s*"
    r"(10|9\.5|9|8\.5|8|7\.5|7|6|5|4|3|2|1)\b", re.I)
# "#43" or "43/102". 🚨 The second form's DENOMINATOR is the set size, not the
# card - taking it would look up card #102 of Base Set for every Base card.
_NUMBER = re.compile(r"#\s*0*(\d{1,3})\b|\b0*(\d{1,3})\s*/\s*\d{1,3}\b")
_YEAR = re.compile(r"\b(19[89]\d|20[0-5]\d)\b")

# Printing, in the order a title states it. 1st Edition and Shadowless are the
# two that move the price by multiples rather than percentages.
_FIRST_ED = re.compile(r"\b1st\s*ed(?:ition)?\b|\bfirst\s*ed(?:ition)?\b", re.I)
_SHADOWLESS = re.compile(r"\bshadowless\b", re.I)
_REVERSE = re.compile(r"\breverse\s*(?:holo|foil)\b|\brev\s*holo\b", re.I)
_HOLO = re.compile(r"\bholo(?:foil|graphic)?\b|\bfoil\b", re.I)
_JAPANESE = re.compile(r"\bjapan(?:ese)?\b|\bjpn?\b|\bntsc-?j\b", re.I)

# 🚨 A CODE CARD IS NOT A CARD. "Pokemon TCG Online Code Card" is a printed
# password with no collectible value whatever, it is sold by the hundred, and
# its title reads exactly like a real card's. This is the single cheapest
# mistake available in this category.
_JUNK = re.compile(
    r"\bcode\s*cards?\b|\bonline\s*code\b|\bqr\s*code\b|"
    r"\bproxy\b|\borica\b|\bcustom\s*(?:made|card)\b|\bfan\s*art\b|"
    r"\breprints?\b|\bfake\b|\bcounterfeit\b|\bnot\s*(?:real|authentic)\b|"
    r"\bsticker\b|\bcoin\b|\bbinder\s*only\b|\bsleeves?\b|\btoploader\b",
    re.I)
# Piles. A lot cannot be priced from its title - `pricebook`'s DEAD_MODELS note
# on card lots is the measured finding, not an opinion.
_PILE = re.compile(
    r"\blots?\s*(?:of\b|#|\d)|\blot\b|\bbulk\b|\bbundle\b|\bcollection\b|"
    r"\byou\s*pick\b|\bu\s*pick\b|\bmystery\b|\brepack\b|\bbinder\b|"
    r"\b\d{2,}\s*(?:cards?|pcs?|pieces?)\b", re.I)
_SEALED = re.compile(
    r"\bbooster\s*(?:box|bundle|pack)\b|\belite\s*trainer\s*box\b|\betb\b|"
    r"\bsealed\b|\bblister\b|\btin\b", re.I)


@dataclass(frozen=True)
class PokeId:
    """What the title says the card IS. No prices here."""

    title: str
    name: Optional[str] = None          # "charizard", "gardevoir gx"
    number: Optional[str] = None        # "4" from "#4" or "4/102"
    year: Optional[int] = None
    grade: Optional[str] = None         # "PSA 10"
    first_edition: bool = False
    shadowless: bool = False
    reverse_holo: bool = False
    holo: bool = False
    japanese: bool = False
    sealed: bool = False
    pile: bool = False
    junk: bool = False
    fuzzy: bool = False                 # the name needed a typo correction

    @property
    def graded(self) -> bool:
        return bool(self.grade)

    @property
    def priceable(self) -> bool:
        """Can this even be looked up? A pile of 400 cards cannot."""
        return bool(self.name) and not (self.junk or self.pile or self.sealed
                                        or self.japanese)


@dataclass(frozen=True)
class PokeComp:
    """One card's market price, and the evidence for which card it is."""

    card_id: str                        # "base1-43"
    name: str
    set_name: str
    released: str                       # "1999/01/09"
    number: str
    rarity: Optional[str]
    printing: str                       # "1stEditionHolofoil", "normal", ...
    market: float                       # TCGplayer market price, USD
    prices: dict = field(default_factory=dict)
    candidates: int = 1
    low: Optional[float] = None         # cheapest candidate, when ambiguous
    high: Optional[float] = None        # dearest candidate, when ambiguous
    # 🚨 THE PAGE THE NUMBER CAME FROM. Leron, 2026-08-22: "there should be a
    # link to the comp on each card". A price with no way to check it is a
    # price you have to take on trust, and this repo's whole posture is the
    # opposite - every claim links its own evidence.
    tcg_url: str = ""
    source: str = "tcgplayer/pokemontcg.io"

    @property
    def ambiguous(self) -> bool:
        """More than one card matched, so `market` is a FLOOR, not a comp.

        🚨 AN AMBIGUOUS MATCH MAY NEVER SET A CEILING. "Pokemon PSA 10 Litten"
        matches fourteen Littens between $0.25 and $40.66; picking the dearest
        is how a $0.32 common gets a $49 max bid, which is the bug this module
        exists to kill. When the title does not pin the card, the honest output
        is the range and a refusal - see `verdict`.
        """
        return self.candidates > 1

    @property
    def range_text(self) -> str:
        if not self.ambiguous or self.low is None:
            return f"${self.market:,.2f}"
        return f"${self.low:,.2f}-${self.high:,.2f} across {self.candidates} printings"

    @property
    def url(self) -> str:
        """Where to check this price - a TCGplayer search for THIS card.

        🚨 NOT the API's own `tcgplayer.url`. It looks like the obvious answer
        and it is dead: `prices.pokemontcg.io/tcgplayer/base1-1` returns 502.
        A comp link that 404s is worse than no link, because it reads as
        evidence right up until you click it. Checked live 2026-08-22.
        """
        from urllib.parse import quote_plus
        q = quote_plus(f"{self.name} {self.set_name} {self.number}".strip())
        return ("https://www.tcgplayer.com/search/pokemon/product"
                f"?productLineName=pokemon&q={q}")


def _norm(title: str) -> str:
    """Lowercase, hyphens kept, everything else to spaces - so "CHARIZARD-HOLO"
    still yields the token "charizard" and "ho-oh" survives intact."""
    t = (title or "").lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9#/'\s-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _species_in(t: str) -> tuple:
    """(name, fuzzy) - the species this title names, correcting one typo.

    🚨 THE TYPO IS NOT AN EDGE CASE, IT IS THE EDGE. A misspelled title gets no
    search traffic and closes cheap - `pricebook.search_terms` deliberately
    hunts "pokeman" and "cannon ae-1" for exactly this reason. "Pokemon 1999
    Fossil **Zapado** Graded PSA 9" is a real HiBid lot and Zapdos is a real
    card; a reader that only matches correct spellings is blind to the listings
    that are cheap BECAUSE they are misspelled.
    """
    padded = f" {t} "
    for n in _BY_LEN:
        if f" {n} " in padded or f" {n}-" in padded or f"-{n} " in padded:
            return n, False
    # Nothing exact. Try each word against the dex, longest words first so
    # "zapado" is tested before "psa".
    words = [w for w in re.split(r"[\s/#-]+", t) if len(w) >= 5]
    for w in sorted(words, key=len, reverse=True):
        near = difflib.get_close_matches(w, SPECIES, n=1, cutoff=0.8)
        if near:
            return near[0], True
    return None, False


def identify(title: str) -> PokeId:
    """Read a listing title into the card it claims to be."""
    t = _norm(title)
    if not t:
        return PokeId(title=title or "")
    name, fuzzy = _species_in(t)
    if name:
        # Pull in the mechanic suffix when the title carries one, because the
        # API's name for the card includes it ("Gardevoir-GX", not "Gardevoir").
        m = re.search(rf"{re.escape(name)}\s*[- ]?\s*({_SUFFIX})\b", t)
        if m:
            name = f"{name} {m.group(1).strip()}"
    num = _NUMBER.search(t)
    yr = _YEAR.search(t)
    g = _GRADER.search(title or "")
    return PokeId(
        title=title,
        name=name,
        number=(num.group(1) or num.group(2)) if num else None,
        year=int(yr.group(1)) if yr else None,
        grade=f"{g.group(1).upper()} {g.group(2)}" if g else None,
        first_edition=bool(_FIRST_ED.search(t)),
        shadowless=bool(_SHADOWLESS.search(t)),
        reverse_holo=bool(_REVERSE.search(t)),
        holo=bool(_HOLO.search(t)) and not _REVERSE.search(t),
        japanese=bool(_JAPANESE.search(t)),
        sealed=bool(_SEALED.search(t)),
        pile=bool(_PILE.search(t)),
        junk=bool(_JUNK.search(t)),
        fuzzy=fuzzy,
    )


# --- the network half -------------------------------------------------------
# 🚨 THE API 500s AT RANDOM AND RETRIES FIX IT. Measured 2026-08-22: the same
# query returned 500, 502 and 200 within seconds of each other, and a probe
# without retries scored 0/19 on titles that resolve 14/19 with them. Anything
# reading this API without a retry loop will report "no such card" for cards
# that plainly exist.

_CACHE_PATH = os.environ.get("FLIPSCOUT_POKE_CACHE", "flipscout_poke_cache.json")
_CACHE_TTL = 7 * 24 * 3600          # a card's market price moves slowly
_cache: Optional[dict] = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_CACHE_PATH, encoding="utf-8") as fh:
                _cache = json.load(fh)
        except Exception:
            _cache = {}
    return _cache


def _save_cache() -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(_cache or {}, fh)
    except Exception:
        pass                            # a cold cache costs a call, never a run


def _headers() -> dict:
    key = (os.environ.get("POKEMONTCG_API_KEY") or "").strip()
    return {"X-Api-Key": key} if key else {}


def _query(q: str, session=None, tries: int = 4) -> Optional[list]:
    """Ask the API, riding out its 5xx. None means "could not ask", which is
    NOT the same as [] ("asked, no such card") - the caller must not report a
    network failure as a worthless card."""
    session = session or requests
    for i in range(tries):
        try:
            r = session.get(API, params={"q": q, "pageSize": 25},
                            headers=_headers(), timeout=_TIMEOUT)
            if r.status_code == 200:
                return (r.json() or {}).get("data") or []
            if r.status_code == 429:
                time.sleep(2.0 * (i + 1))
                continue
        except Exception:
            pass
        time.sleep(0.5 * (i + 1))
    return None


def _printing_for(pid: PokeId, prices: dict) -> Optional[str]:
    """Which of TCGplayer's price columns this listing is.

    🚨 1ST EDITION IS A DIFFERENT PRODUCT, NOT A CONDITION. Neo Discovery
    Umbreon is $300 first-edition and $85.60 unlimited - reading the wrong
    column is a 3.5x error in whichever direction you got it wrong.
    """
    if not prices:
        return None
    order = []
    if pid.first_edition:
        order += ["1stEditionHolofoil", "1stEdition", "1stEditionNormal"]
    if pid.reverse_holo:
        order += ["reverseHolofoil"]
    if pid.holo or pid.first_edition:
        order += ["holofoil", "unlimitedHolofoil"]
    order += ["normal", "unlimited", "holofoil", "reverseHolofoil",
              "1stEditionHolofoil", "1stEdition"]
    for k in order:
        if prices.get(k, {}).get("market") is not None:
            return k
    for k, v in prices.items():         # whatever it has
        if v.get("market") is not None:
            return k
    return None


def lookup(pid: PokeId, session=None, use_cache: bool = True) -> Optional[PokeComp]:
    """The market price for the card this title names, or None.

    None means "not priced" for any reason - not a card, a pile, Japanese, the
    API was down. The caller says so out loud rather than substituting a
    category average, which is the entire point of this module.
    """
    if not pid.priceable:
        return None
    ck = f"{pid.name}|{pid.number}|{pid.year}|{int(pid.first_edition)}|{int(pid.reverse_holo)}|{int(pid.holo)}"
    cache = _load_cache() if use_cache else {}

    def _remember(value):
        """Write through only when caching is on - a test must never leave a
        file behind, and `use_cache=False` has to mean it."""
        if use_cache:
            cache[ck] = {"at": time.time(), "comp": value}
            _save_cache()

    hit = cache.get(ck)
    if hit and (time.time() - hit.get("at", 0)) < _CACHE_TTL:
        return PokeComp(**hit["comp"]) if hit.get("comp") else None

    name = " ".join(w.capitalize() for w in pid.name.split())
    rows = None
    if pid.number:
        rows = _query(f'name:"{name}" number:{pid.number}', session)
    if not rows:
        got = _query(f'name:"{name}"', session)
        # The API spells mechanic suffixes with a hyphen ("Gardevoir-GX") for
        # some and a space ("Dragapult VMAX") for others, and there is no rule
        # to it - so when one spelling finds nothing, try the other.
        if not got and " " in name:
            got = _query(f'name:"{name.replace(" ", "-")}"', session)
        if got is None and rows is None:
            return None                 # API down - do NOT cache a non-answer
        rows = got or []
    if not rows:
        _remember(None)
        return None

    # A year in the title is the strongest disambiguator a card title carries.
    # 🚨 A YEAR THAT MATCHES NOTHING IS EVIDENCE, NOT NOISE. "VENUSAUR HOLO
    # 2021" against a candidate list with no 2021 card means the name matched
    # something else; keeping the other 25 and taking the dearest returned a
    # 2009 Pokemon Rumble Venusaur at $279.92. Contradicted year -> stay
    # ambiguous rather than substitute a card the title never mentioned.
    year_conflict = False
    if pid.year:
        same = [c for c in rows
                if (c.get("set", {}).get("releaseDate") or "")[:4] == str(pid.year)]
        if same:
            rows = same
        else:
            year_conflict = True

    priced = []
    for c in rows:
        prices = ((c.get("tcgplayer") or {}).get("prices") or {})
        col = _printing_for(pid, prices)
        if col:
            priced.append((c, prices[col]["market"], col, prices))
    if not priced:
        _remember(None)
        return None

    # 🚨 THE LOWEST, NOT THE HIGHEST, WHENEVER THE TITLE DID NOT PIN THE CARD.
    # This is the whole correction. Every wrong number this module produced in
    # testing came from taking the dearest of a candidate list - a $0.32 Crown
    # Zenith Lycanroc read as a $1.03 Paldea Evolved one, a common Litten read
    # as a $40 Temporal Forces holo. The cheapest candidate is the only choice
    # that cannot invent value the listing never claimed, and the range is
    # printed beside it so nothing is hidden.
    priced.sort(key=lambda x: x[1])
    lo, hi = priced[0][1], priced[-1][1]
    pinned = len(priced) == 1 and not year_conflict
    c, mk, col, prices = priced[0]
    if year_conflict:
        priced = priced * 2 if len(priced) == 1 else priced   # force ambiguous
    comp = PokeComp(
        card_id=c["id"], name=c.get("name") or name,
        set_name=(c.get("set") or {}).get("name") or "",
        released=(c.get("set") or {}).get("releaseDate") or "",
        number=str(c.get("number") or ""), rarity=c.get("rarity"),
        printing=col, market=round(float(mk), 2),
        prices={k: v.get("market") for k, v in prices.items()
                if v.get("market") is not None},
        tcg_url=((c.get("tcgplayer") or {}).get("url") or ""),
        candidates=1 if pinned else max(2, len(priced)),
        low=None if pinned else round(lo, 2),
        high=None if pinned else round(hi, 2),
    )
    _remember(dict(comp.__dict__))
    return comp


# --- the deal logic ---------------------------------------------------------
# Leron, 2026-08-22: *"we need to find deals i dont know how to identify them
# but im sure you can find the logic"*.
#
# The logic is not a secret, it is just never written down in one place. A card
# is worth money for exactly four reasons, and a title states all four:
#
#   1. WHICH CARD IT IS         - the identity carries the price (Charizard
#                                 4/102 is $855; Abra 43/102 is $1.54)
#   2. WHICH PRINTING           - 1st Edition / Shadowless / holo, worth
#                                 MULTIPLES of the same card unlimited
#   3. WHAT CONDITION           - which a raw title never states, and a slab
#                                 states exactly
#   4. WHETHER IT IS EVEN REAL  - code cards, proxies, reprints, piles
#
# 🚨 THE ONE RULE THAT PAYS FOR ITSELF: A SLABBED COMMON IS NOT A DEAL. Grading
# costs more than the card, so a PSA 10 of a $0.32 Litten is worth the plastic
# it is in. Thirty of these were on the board carrying $49-$53 max bids. The
# raw market price separates them from a PSA 7 Jungle Clefable in one number,
# without needing a graded comp for either.

PASS, LOOK, CHASE, PRICED = "PASS", "LOOK", "CHASE", "PRICED"

# A slab is worth roughly the grading fee even when the card is worthless, so
# "cheap" here means "the CARD adds nothing" - not "the lot is free".
_SLAB_FLOOR = 5.00        # raw market under this: you are buying plastic
_SLAB_REAL = 50.00        # raw market over this: the grade is on something real
_RAW_WORTH_PRICING = 20.00


@dataclass(frozen=True)
class PokeVerdict:
    verdict: str
    why: str
    comp: Optional[PokeComp] = None
    ident: Optional[PokeId] = None

    @property
    def is_find(self) -> bool:
        return self.verdict in (LOOK, CHASE, PRICED)


def verdict(pid: PokeId, comp: Optional[PokeComp] = None,
            graded=None) -> PokeVerdict:
    """Should he open this listing, and what is the card actually worth?

    🚨 NEVER RETURNS A CEILING FOR A GRADED CARD. TCGplayer's market price is
    the RAW card; turning it into a slab price needs a grade multiplier that
    nobody here has measured, and inventing one is how the $92 blanket comp
    happened in the first place. A graded card gets a verdict and a floor, and
    the bid stays a human decision.
    """
    if pid.junk:
        return PokeVerdict(PASS, "Code card, proxy or reprint - not a "
                                 "collectible card at any price.", None, pid)
    if pid.pile:
        return PokeVerdict(PASS, "A lot or binder. A pile cannot be priced from "
                                 "its title - buy it off the photos or not at "
                                 "all.", None, pid)
    if pid.sealed:
        return PokeVerdict(LOOK, "Sealed product - a different market from "
                                 "singles and not priced here. Check a sealed "
                                 "comp before bidding.", None, pid)
    if pid.japanese:
        return PokeVerdict(LOOK, "Japanese card. 🚨 Japanese is NOT junk in the "
                                 "TCG (unlike cartridges) - but this price "
                                 "source is English-only, so it cannot value "
                                 "it. Worth opening.", None, pid)
    if not pid.name:
        return PokeVerdict(LOOK, "Reads as a Pokemon card but no card name was "
                                 "recognised - a human has to look.", None, pid)
    if comp is None:
        return PokeVerdict(LOOK, f"Named {pid.name.title()} but no market price "
                                 f"came back - unlisted printing, or the price "
                                 f"source was unreachable. Not a judgement.",
                           None, pid)

    ident = (f"{comp.name} #{comp.number}, {comp.set_name} "
             f"{comp.released[:4]}")
    if comp.ambiguous:
        return PokeVerdict(
            LOOK,
            f"Could be any of {comp.candidates} printings of {comp.name} - "
            f"{comp.range_text}. The title does not say which, so the low end "
            f"is what it is worth if it is the common one.", comp, pid)

    # 🚨 A 1ST-EDITION CLAIM WITH NO 1ST-EDITION COLUMN IS AN UNDERSTATEMENT,
    # and saying so matters more than the number: 1st ed Base Charizard is a
    # multiple of the unlimited price this would otherwise print.
    understated = pid.first_edition and not comp.printing.startswith("1stEdition")
    tail = (" 🚨 The title claims 1st Edition and this source has no "
            "1st-edition price for it, so the figure below is the UNLIMITED "
            "card and is too low." if understated else "")

    if pid.graded:
        # 🚨 THE GRADE ITSELF CARRIES VALUE, AND THE RAW PRICE CANNOT SEE IT.
        # Measured 2026-08-22 the day the PriceCharting token went in: a PSA 10
        # Litten comps at $35.00 while the raw card is $0.25. The raw-only rule
        # below called that a PASS - "a slabbed common worth the plastic" - and
        # it was wrong by 140x. When a grade-keyed price exists it decides,
        # because it is measuring the thing actually being sold.
        if graded is not None and graded.price:
            gp = graded.price
            thin = (f" 🚨 Thin: about {graded.volume} sold in a year."
                    if graded.volume is not None and graded.volume < 12 else "")
            return PokeVerdict(
                CHASE if gp >= _SLAB_REAL else (LOOK if gp >= _SLAB_FLOOR else PASS),
                f"**{graded.name}** - {graded.set_name} - **${gp:,.2f} in "
                f"{pid.grade}** (PriceCharting), against ${graded.ungraded:,.2f} "
                f"raw. That is a comp for the slab you are actually bidding "
                f"on.{tail}{thin}", comp, pid)
        if comp.market < _SLAB_FLOOR and not understated:
            return PokeVerdict(
                PASS,
                f"{ident} is a {comp.market:,.2f}-dollar card ungraded. A "
                f"{pid.grade} slab of a common is worth the plastic - grading "
                f"costs more than the card. This is what most graded Pokemon "
                f"on an auction site is.", comp, pid)
        if comp.market >= _SLAB_REAL or understated:
            return PokeVerdict(
                CHASE,
                f"{ident} - **${comp.market:,.2f} raw**, and this one is in a "
                f"{pid.grade} slab. The grade is sitting on a genuinely "
                f"valuable card.{tail}", comp, pid)
        return PokeVerdict(
            LOOK,
            f"{ident} - ${comp.market:,.2f} raw, in a {pid.grade} slab. The "
            f"grade decides this one: a high grade on a mid card can be worth "
            f"several times raw, a low grade barely more.{tail}", comp, pid)

    if comp.market >= _RAW_WORTH_PRICING:
        return PokeVerdict(
            PRICED,
            f"{ident} - **${comp.market:,.2f}** ({comp.printing}). Raw and "
            f"pinned to one card, so this figure is a real comp.{tail} "
            f"🚨 Condition is still yours to judge from the photos.", comp, pid)
    if comp.market >= _SLAB_FLOOR:
        return PokeVerdict(
            LOOK, f"{ident} - ${comp.market:,.2f}. Cheap, but real; only worth "
                  f"it inside a lot you wanted anyway.{tail}", comp, pid)
    return PokeVerdict(
        PASS, f"{ident} - ${comp.market:,.2f}. A common. It comes in every "
              f"pack and it always will.", comp, pid)


def read(title: str, session=None) -> PokeVerdict:
    """Title in, verdict + real price out. One call, fail-soft.

    Two sources, each doing the half it is good at: `pokemontcg` reads the
    card's IDENTITY out of a scrappy title (set and card number, free), and
    PriceCharting prices the SLAB by grade (paid, needs SPORTSCARDSPRO_TOKEN).
    Neither can do the other's job - see the note in `verdict`.
    """
    pid = identify(title)
    comp = None
    if pid.priceable:
        try:
            comp = lookup(pid, session=session)
        except Exception:
            comp = None                 # never let a price source break a run
    graded = None
    if pid.graded and comp is not None and not comp.ambiguous:
        try:
            from .sportscards import pokemon_price
            graded = pokemon_price(
                comp.name, comp.set_name, comp.number, pid.grade,
                variant=("shadowless" if pid.shadowless else
                         "1st edition" if pid.first_edition else ""))
        except Exception:
            graded = None               # a missing grade price is not a failure
    return verdict(pid, comp, graded=graded)

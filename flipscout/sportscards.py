"""What a sports card sells for - keyed on the card, the PARALLEL and the GRADE.

WHY THIS EXISTS
---------------
Leron, 2026-08-22: *"we need a source outside of ebay for the sports cards
too"*. The honest answer has two halves, and the second one matters more.

**Half one: there is no self-serve sports-card price API that is independent of
eBay, because eBay is where sports cards trade.** SportsCardsPro (this module's
source) computes its prices from eBay sold listings plus its own marketplace.
Card Ladder and Card Hedge aggregate Goldin, Heritage and Fanatics as well, and
are the closest thing to independent - but both are contact-sales enterprise
deals, not something you can wire up this week. Nobody should pretend otherwise.

**Half two: the eBay problem was never the marketplace, it was the KEY.** A
title search returns a population and a population has a range. This source is
addressed by CARD - player, set, year, parallel, grade - so a lookup returns one
number per parallel instead of a spread across everything sharing a keyword.

    Shane Bieber [Refractor]       #HMT59   Baseball Cards 2018 Topps Chrome Update
    Shane Bieber [Gold Refractor]  #HMT59   Baseball Cards 2018 Topps Chrome Update
    Shane Bieber [Pink Refractor]  #HMT59   Baseball Cards 2018 Topps Chrome Update

🚨 THE HOST IS THE WHOLE GAME, AND THE DOCS TELL YOU THE WRONG ONE. Their
documentation says "make an HTTP request to the base URL
https://www.pricecharting.com". Measured 2026-08-22 on the same query:

    sportscardspro.com/api/products  ->  7 matches, every one the right card
    pricecharting.com/api/products   -> 97 matches, top six Garbage Pail Kids

pricecharting.com is the shared backend for video games, comics, Funko and
every TCG, so an unscoped search happily prices "Shane Bieber Topps Chrome" as
"Barbaric Bieber [Red] #13a, 2024 Garbage Pail Kids". Following the docs would
have shipped that. ALWAYS `_HOST`.

🚨 PRICES ARE INTEGER PENNIES. `1732` is $17.32. Reading them as dollars
overstates every comp by 100x.

WHAT THE TITLES CANNOT DO
-------------------------
Measured against 22 real cards from the #cards channel: with the player, the
set and the parallel colour all filtered, only **3 of 22 pinned to a single
product**. The rest stayed 2-44 wide, and that is not the source's fault - the
year and the card number are printed ON the card and simply are not in a HiBid
title:

    MERRILL KELLY TOPPS CHROME REFRACTOR PARALLEL   -> 18 real products, several years

So this module does NOT try to pretend it knows which one. It prices the whole
candidate set and lets the SPREAD decide, which is a rule that works precisely
when the identity does not (see `verdict`).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

# 🚨 NEVER pricecharting.com - see the module docstring.
_HOST = "https://www.sportscardspro.com"
_TIMEOUT = 20

# Which price column is this card's grade? Straight from their "Key
# Descriptions" table, read on 2026-08-22.
#
# 🚨 THE COLUMN NAMES ARE REPURPOSED VIDEO-GAME COLUMNS AND DO NOT MEAN WHAT
# THEY SAY. `new-price` is not a new card, it is a Grade 8. `manual-only-price`
# is a PSA 10. Reading them at face value prices a raw card at the PSA 10
# number - which is the same shape of error as the blanket Pokemon comp this
# repo just removed, and would be worse because it looks so plausible.
UNGRADED = "loose-price"
GRADE_COLUMN = {
    "1": "condition-9-price",    "2": "condition-10-price",
    "3": "condition-13-price",   "4": "condition-14-price",
    "5": "condition-15-price",   "6": "condition-16-price",
    "7": "cib-price",            "7.5": "cib-price",
    "8": "new-price",            "8.5": "new-price",
    "9": "graded-price",         "9.5": "box-only-price",
    "10": "manual-only-price",                    # PSA 10 is the default 10
}
# A 10 from a grader that is not PSA has its own column.
GRADER_10 = {"BGS": "bgs-10-price", "CGC": "condition-17-price",
             "SGC": "condition-18-price", "TAG": "condition-21-price",
             "ACE": "condition-22-price"}

_GRADER = re.compile(
    r"\b(psa|bgs|cgc|sgc|ace|tag|hga|csg)\s*\.?\s*"
    r"(10|9\.5|9|8\.5|8|7\.5|7|6|5|4|3|2|1)\b", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")
# The parallel is what separates a $2 card from a $200 one in the same set, and
# SportsCardsPro puts it in brackets: "Shane Bieber [Gold Refractor] #HMT59".
_COLOUR = re.compile(
    r"\b(gold|blue|red|orange|green|purple|pink|black|aqua|sepia|bronze|"
    r"silver|teal|yellow|white|negative|prism|x-?fractor|superfractor|"
    r"atomic|wave|speckle|mojo|shimmer|lava|padparadscha|sapphire|"
    r"printing\s*plate|autograph|auto|patch|relic)\b", re.I)
_SPORT = re.compile(
    r"baseball|basketball|football|hockey|soccer|racing|wrestling|ufc|"
    r"boxing|golf|tennis", re.I)
# Seller noise that only widens the search.
_NOISE = re.compile(
    r"\b(mint|nm|near\s*mint|gem|pack\s*fresh|invest|hot|rare|sharp|clean|"
    r"look|wow|l@@k|nice|beautiful|centered|psa\s*ready|parallel|sports?|"
    r"card|cards|rookie\s*card|rc|ssp|sp|case\s*hit)\b", re.I)


@dataclass(frozen=True)
class Candidate:
    product_id: str
    name: str                 # "Shane Bieber [Gold Refractor] #HMT59"
    set_name: str             # "Baseball Cards 2018 Topps Chrome Update"
    price: Optional[float] = None      # at the grade asked for, in DOLLARS
    ungraded: Optional[float] = None
    volume: Optional[int] = None       # yearly units sold - liquidity

    @property
    def parallel(self) -> str:
        m = re.search(r"\[([^\]]+)\]", self.name)
        return m.group(1) if m else "base"

    @property
    def url(self) -> str:
        return f"{_HOST}/game/{self.product_id}"


@dataclass(frozen=True)
class SportsComp:
    """The candidate set for one listing, priced. Not a single answer, because
    the title usually does not contain one."""

    query: str
    grade: Optional[str]
    candidates: list = field(default_factory=list)
    priced: bool = False               # False = identity only, no token

    @property
    def n(self) -> int:
        return len(self.candidates)

    @property
    def prices(self) -> list:
        return sorted(c.price for c in self.candidates if c.price is not None)

    @property
    def low(self) -> Optional[float]:
        p = self.prices
        return p[0] if p else None

    @property
    def high(self) -> Optional[float]:
        p = self.prices
        return p[-1] if p else None

    @property
    def pinned(self) -> bool:
        return self.n == 1

    @property
    def player_and_set(self) -> str:
        if not self.candidates:
            return ""
        c = self.candidates[0]
        who = re.sub(r"\s*\[.*", "", c.name).strip()
        return f"{who} - {c.set_name}"

    @property
    def range_text(self) -> str:
        if self.low is None:
            return "no price"
        if self.low == self.high:
            return f"${self.low:,.2f}"
        return f"${self.low:,.2f}-${self.high:,.2f} across {self.n} parallels"


def token(env=None) -> str:
    env = env if env is not None else os.environ
    return (env.get("SPORTSCARDSPRO_TOKEN") or "").strip()


def query_for(title: str) -> str:
    """The search string for a scrappy auction title.

    Kept CLOSE to the original: measured 2026-08-22, passing the raw title to
    the sport-scoped host put 16 of 16 real listings on the right player in the
    right sport. Only seller noise is stripped - aggressive rewriting lost the
    set name, which is the second-strongest signal after the player.
    """
    t = re.sub(r"[^\w\s'-]", " ", title or "")
    t = _NOISE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def grade_of(title: str) -> Optional[str]:
    m = _GRADER.search(title or "")
    return f"{m.group(1).upper()} {m.group(2)}" if m else None


def _column(grade: Optional[str]) -> str:
    """Which price key to read for this grade. Ungraded when there is none."""
    if not grade:
        return UNGRADED
    grader, _, num = grade.partition(" ")
    if num == "10" and grader in GRADER_10:
        return GRADER_10[grader]
    return GRADE_COLUMN.get(num, UNGRADED)


def _pennies(v) -> Optional[float]:
    """🚨 Their prices are an INTEGER NUMBER OF PENNIES. 1732 is $17.32."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return round(n / 100.0, 2) if n > 0 else None


_CACHE_PATH = os.environ.get("FLIPSCOUT_SPORTS_CACHE",
                             "flipscout_sports_cache.json")
_CACHE_TTL = 7 * 24 * 3600
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
        pass


# Pokemon lives on the pricecharting.com side of the same account. Sports is
# scoped by the sportscardspro host (see the module docstring); Pokemon has no
# such host, so its results are filtered on console-name instead.
_POKE_HOST = "https://www.pricecharting.com"

# A regex word boundary, held in a NAMED CONSTANT on purpose.
# A patch script written through a shell heredoc collapses the
# two-character escape into byte 0x08 (backspace) before Python
# ever sees it - `pricebook` has a CI guard for exactly this and
# this file just repeated the mistake. Referencing a constant
# means the escape is written once, in one place, by hand.
WORD_END = chr(92) + "b"


def _get(path: str, params: dict, session=None, tries: int = 3,
         host: Optional[str] = None) -> Optional[dict]:
    """None means "could not ask" - never "no such card"."""
    session = session or requests
    for i in range(tries):
        try:
            r = session.get(f"{host or _HOST}{path}", params=params,
                            timeout=_TIMEOUT)
            if r.status_code == 200:
                d = r.json() or {}
                return d if d.get("status") != "error" else {}
            if r.status_code in (401, 403):
                return {}              # bad/absent token: a real answer, not a blip
        except Exception:
            pass
        time.sleep(0.5 * (i + 1))
    return None


def _narrow(title: str, products: list) -> list:
    """Cut the candidate list down with what the title actually states.

    Two filters, both evidence-based and both reversible - if a filter empties
    the list it is DROPPED rather than trusted, because a title that disagrees
    with every product is a title we misread, not a card that does not exist.
    """
    out = [p for p in products if _SPORT.search(p.get("console-name") or "")]
    out = out or list(products)

    y = _YEAR.search(title or "")
    if y:
        same = [p for p in out if y.group(1) in (p.get("console-name") or "")]
        out = same or out

    # 🚨 THE PARALLEL SET MUST MATCH EXACTLY, BOTH WAYS. A title saying
    # "REFRACTOR" is the base refractor, NOT the Gold one - and a title saying
    # "BLUE REFRACTOR" is not the base. Requiring only that the title's words
    # appear in the bracket would let every colour through on a bare
    # "Refractor" and pick the dearest by accident.
    want = {c.lower().replace("-", "") for c in _COLOUR.findall(title or "")}
    def bracket_colours(p):
        m = re.search(r"\[([^\]]+)\]", p.get("product-name") or "")
        return {c.lower().replace("-", "")
                for c in _COLOUR.findall(m.group(1) if m else "")}
    same = [p for p in out if bracket_colours(p) == want]
    return same or out


def look_up(title: str, session=None, env=None, use_cache: bool = True,
            limit: int = 12) -> Optional[SportsComp]:
    """Price one listing. None means the source could not be reached at all.

    Works WITHOUT a token for identity: the search endpoint still names the
    player, set and parallels, so a card can say what it is even when it cannot
    say what it is worth. `priced` reports which of the two happened.
    """
    q = query_for(title)
    if not q:
        return None
    tok = token(env)
    ck = f"{q}|{tok[:6]}"
    cache = _load_cache() if use_cache else {}
    hit = cache.get(ck)
    if hit and (time.time() - hit.get("at", 0)) < _CACHE_TTL:
        c = hit.get("comp")
        return _revive(c) if c else None

    d = _get("/api/products", {"t": tok or _DEMO, "q": q}, session=session)
    if d is None:
        return None
    products = _narrow(title, (d or {}).get("products") or [])[:limit]
    grade = grade_of(title)
    col = _column(grade)

    cands, priced = [], False
    for p in products:
        price = ungraded = volume = None
        if tok:
            # The list endpoint returns identity only; prices come per product.
            full = _get("/api/product", {"t": tok, "id": p.get("id")},
                        session=session) or {}
            price = _pennies(full.get(col))
            ungraded = _pennies(full.get(UNGRADED))
            try:
                volume = int(full.get("sales-volume") or 0) or None
            except (TypeError, ValueError):
                volume = None
            priced = priced or price is not None
        cands.append(Candidate(product_id=str(p.get("id") or ""),
                               name=p.get("product-name") or "",
                               set_name=p.get("console-name") or "",
                               price=price, ungraded=ungraded, volume=volume))
    comp = SportsComp(query=q, grade=grade, candidates=cands, priced=priced)
    if use_cache:
        cache[ck] = {"at": time.time(), "comp": _freeze(comp)}
        _save_cache()
    return comp


# Their own published demo token. Identity only - it returns no price keys,
# which is exactly why `priced` exists and why a real token is a paid tier.
_DEMO = "c0b53bce27c1bdab90b1605249e600dc43dfd1d5"


def _freeze(c: SportsComp) -> dict:
    return {"query": c.query, "grade": c.grade, "priced": c.priced,
            "candidates": [x.__dict__ for x in c.candidates]}


def _revive(d: dict) -> SportsComp:
    return SportsComp(query=d["query"], grade=d.get("grade"),
                      priced=d.get("priced", False),
                      candidates=[Candidate(**x) for x in d.get("candidates", [])])


# --- the decision ----------------------------------------------------------
# 🚨 THE SPREAD DECIDES, BECAUSE THE IDENTITY USUALLY CANNOT.
#
# Only 3 of 22 real titles pinned to one product. That would be fatal for a
# "look up the comp" design - but it is not fatal for a DECISION, because two
# of the three answers do not need to know which parallel it is:
#
#   * if even the DEAREST candidate is worth less than the current bid, the
#     listing is a loss whichever card it turns out to be. Certain PASS.
#   * if even the CHEAPEST candidate is worth multiples of the current bid, it
#     pays whichever card it turns out to be. CHASE.
#   * only the middle needs a human to read the photo - and the photo is where
#     the year and card number are, so that is the right place to send it.
#
# No multiplier is invented anywhere here: it is their numbers and the ask.

PASS, LOOK, CHASE, UNKNOWN = "PASS", "LOOK", "CHASE", "UNKNOWN"

# HiBid takes ~18% and shipping is real, so "worth the same as the bid" is
# already a loss. Ask for a clear multiple before calling anything a CHASE.
_CHASE_MULTIPLE = 2.5


@dataclass(frozen=True)
class SportsVerdict:
    verdict: str
    why: str
    comp: Optional[SportsComp] = None


def verdict(title: str, ask: Optional[float],
            comp: Optional[SportsComp]) -> SportsVerdict:
    if comp is None or not comp.candidates:
        return SportsVerdict(UNKNOWN, "No match at the price source - an odd "
                                      "title, or a card too new to be listed. "
                                      "Open it yourself.", comp)
    who = comp.player_and_set
    grade = f" in a {comp.grade} slab" if comp.grade else ""
    if not comp.priced:
        pars = sorted({c.parallel for c in comp.candidates})
        which = (f"**{comp.candidates[0].name}**" if comp.pinned
                 else f"**{who}**{grade} - one of {comp.n} parallels: "
                      f"{', '.join(pars)[:180]}")
        return SportsVerdict(
            LOOK,
            f"{which} - 🚨 no price: SPORTSCARDSPRO_TOKEN is not set, so this "
            f"names the card but cannot value it.", comp)

    lo, hi = comp.low, comp.high
    if lo is None:
        return SportsVerdict(LOOK, f"**{who}**{grade} - matched {comp.n} "
                                   f"parallel(s); the source has no price for "
                                   f"that grade.", comp)
    thin = [c.volume for c in comp.candidates if c.volume is not None]
    liq = (" 🚨 Thin: about "
           f"{min(thin)} sold in a year - a price with nobody behind it."
           if thin and min(thin) < 12 else "")

    if comp.pinned:
        head = f"**{comp.candidates[0].name}** - {comp.candidates[0].set_name}"
        body = f"**${lo:,.2f}**{grade or ' ungraded'} (SportsCardsPro)."
    else:
        head = f"**{who}**{grade} - one of {comp.n} parallels"
        body = (f"the whole range is **{comp.range_text}**, so the card is "
                f"worth at least ${lo:,.2f} and at most ${hi:,.2f}.")

    if ask is not None and ask > 0:
        if hi < ask:
            return SportsVerdict(
                PASS,
                f"{head} - {body} 🚨 **Even the dearest parallel (${hi:,.2f}) "
                f"is worth less than the ${ask:,.2f} bid**, before the "
                f"auctioneer's cut. This one is a loss whichever card it is - "
                f"no photo needed.{liq}", comp)
        if lo >= ask * _CHASE_MULTIPLE:
            return SportsVerdict(
                CHASE,
                f"{head} - {body} **Even the cheapest parallel (${lo:,.2f}) is "
                f"{lo / ask:.1f}x the ${ask:,.2f} bid**, so it pays whichever "
                f"card it is.{liq}", comp)
    return SportsVerdict(
        LOOK,
        f"{head} - {body} The spread does not decide it, so this is a photo "
        f"job: the year and card number are printed on the card and are not in "
        f"the title.{liq}", comp)


def read(title: str, ask: Optional[float] = None, session=None,
         env=None) -> SportsVerdict:
    """Title (+ what it costs right now) in, verdict out. Fail-soft."""
    try:
        comp = look_up(title, session=session, env=env)
    except Exception:
        comp = None
    return verdict(title, ask, comp)


# --- Pokemon, graded ---------------------------------------------------------
# 🚨 THIS CLOSES THE GAP `pokemontcg` DELIBERATELY LEFT OPEN. TCGplayer's market
# price is the RAW card, so a slab got a verdict and no ceiling - "the grade is
# sitting on a genuinely valuable card, you set the number". The same $49
# subscription that prices sports cards prices Pokemon BY GRADE:
#
#     1999 Alakazam #1 Base Set   raw $35.56   G7 $84.13   G9 $299.50
#                                 PSA 10 $2,675.00   BGS 10 $3,478.00
#
# A live HiBid lot - "1999 Pokemon Alakazam Holo #1 PSA 7" at a $32 bid - went
# from "no ceiling" to a card that comps at $84.13 in the grade it is actually
# in. That is the difference between a hint and a decision.
#
# 🚨 THE TWO SOURCES DISAGREE ON RAW, AND BY A LOT: TCGplayer says $69.45 for
# that Alakazam, PriceCharting says $35.56. Neither is wrong - they measure
# different marketplaces - but do NOT mix them in one sentence. Graded numbers
# come from here; the raw number stays whichever source is being quoted.



def _set_tokens(name: str) -> frozenset:
    """A set's identity as a bag of words, so two spellings of it can be
    compared.

    pokemontcg calls it "Base"; PriceCharting calls it "Pokemon Base Set". The
    words "pokemon" and "set" carry no information and appear on one side only,
    so they come out - what is left ("base") matches, while "Base Set 2" keeps
    its "2" and correctly does not.
    """
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return frozenset(w for w in words if w not in {"pokemon", "set", "cards",
                                                   "card", "the"})


def pokemon_price(name: str, set_name: str, number: str,
                  grade: Optional[str] = None, session=None, env=None,
                  variant: str = "") -> Optional[Candidate]:
    """PriceCharting's price for one Pokemon card, at `grade`. None if unknown.

    Identity comes from `pokemontcg` (which reads set and card number out of a
    scrappy title far better than a keyword search can); this only prices what
    that already identified.
    """
    tok = token(env)
    if not tok or not name:
        return None
    q = " ".join(x for x in ("pokemon", name, set_name, number) if x)
    d = _get("/api/products", {"t": tok, "q": q}, session=session,
             host=_POKE_HOST)
    if not d:
        return None
    poke = [p for p in (d.get("products") or [])
            if "pokemon" in (p.get("console-name") or "").lower()]
    if not poke:
        return None
    # 🚨 THE CARD NUMBER IS THE DISAMBIGUATOR, and it is in the product name as
    # "#4". Without it "Charizard Base Set" matches nine products across
    # reprints and the top hit is whatever the search ranks first.
    if number:
        exact = [p for p in poke
                 if re.search(r"#\s*" + re.escape(str(number)) + WORD_END,
                              p.get("product-name") or "")]
        poke = exact or poke
    # 🚨 THE NUMBER ALONE IS NOT ENOUGH - THE SET IS THE OTHER HALF. "Alakazam
    # #1" is a real card in Base Set, Base Set 2, Expedition, Shadowless AND
    # Team Rocket (as Dark Alakazam). Filtering on the number alone left nine
    # candidates and the ambiguity guard then refused every Pokemon card, which
    # looked exactly like "the source has no data".
    if set_name:
        want = _set_tokens(set_name)
        same = [p for p in poke
                if _set_tokens(p.get("console-name") or "") == want]
        poke = same or poke
    # 🚨 AND THE VARIANT IS THE THIRD HALF. One card number in one set is still
    # several products: Base Set Alakazam #1 exists plain, as [Shadowless], and
    # as [1999-2000]. Shadowless is a different print run worth a multiple, so
    # this is not cosmetic - it is the same lesson as a sports parallel.
    #
    # No variant stated in the title -> take the PLAIN one. A title that does
    # not say "shadowless" is not describing a shadowless card, and guessing
    # upward is how a comp inflates.
    if len(poke) > 1:
        def bracket(pr):
            m = re.search(r"\[([^\]]+)\]", pr.get("product-name") or "")
            return (m.group(1) if m else "").lower()
        if variant:
            v = variant.lower()
            want = [pr for pr in poke if v in bracket(pr)]
        else:
            want = [pr for pr in poke if not bracket(pr)]
        poke = want or poke
    if len(poke) > 1:
        return None                    # still ambiguous -> no price, as ever
    p0 = poke[0]
    full = _get("/api/product", {"t": tok, "id": p0.get("id")}, session=session,
                host=_POKE_HOST) or {}
    price = _pennies(full.get(_column(grade)))
    if price is None:
        return None
    try:
        vol = int(full.get("sales-volume") or 0) or None
    except (TypeError, ValueError):
        vol = None
    return Candidate(product_id=str(p0.get("id") or ""),
                     name=p0.get("product-name") or name,
                     set_name=p0.get("console-name") or set_name,
                     price=price, ungraded=_pennies(full.get(UNGRADED)),
                     volume=vol)

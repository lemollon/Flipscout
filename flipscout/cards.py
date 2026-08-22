"""Reading a SPORTS CARD title the way a card shop reads it.

WHERE THIS CAME FROM
--------------------
Leron's friend works the counter at a card shop, and on 2026-08-22 he gave the
whole buy box in about ninety seconds:

    "You just want to avoid 80s and '90s - anything in the 2000s or later.
     [...] autographed cards, numbered cards, that's what you're looking for.
     If you see cards that are just BASE, those are base cards, they're easily
     found, they come in all packs. So you're looking for chase cards and hit
     cards. [A chase card is] something inside packs that don't come in every
     pack. [...] There's brands of cards that are more expensive than other
     cards. There's players that are better than other players. There's
     rookies - when you find rookies those are worth more."

That is five independent rules, and every one of them is READABLE FROM A TITLE.
This module is those rules and nothing else.

🚨 THIS MODULE DOES NOT PRICE ANYTHING, AND THAT IS THE POINT.
`pricebook` has one law - a model ships only with a MEASURED comp - and the
honest reading of the advice above is that it never produces a dollar figure.
It produces the thing a card shop actually does in the first two seconds: keep
this one, bin that one. So `read()` returns a VERDICT (CHASE / LOOK / PASS),
not a comp, and nothing here is allowed to set a bid ceiling. The Pokemon TCG
tiers in `pricebook` stay the only cards in this repo carrying a number,
because those three are the only ones anyone measured (see the DEAD_MODELS note
on card lots for what measuring the rest actually found).

The division of labour, stated once:

    pricebook  -> "this is worth $112.50, don't pay over $34"   (measured)
    cards      -> "this is worth PHOTOGRAPHING"                  (triage)

WHY TRIAGE IS WORTH SHIPPING ON ITS OWN
---------------------------------------
Because the losing move in this category is not mispricing a card, it is
sinking an hour into a shoebox of 1990 Score. A card table at an estate sale is
a thousand titles and one of them matters; the friend's rules are exactly the
filter that gets a thousand down to five, and five is a number a human can
price by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Optional

from .ebay_ui import sold_url
from .pricebook import normalize


# --- is this even a card? ---------------------------------------------------
# 🚨 EVERY SIGNAL BELOW IS A FALSE POSITIVE SOMEWHERE ELSE. "auto" is a car,
# "RC" is a hobby helicopter, "patch" is a jacket, "jersey" is a state, "prizm"
# is a Geo, "1/1" is a fraction, "rookie" is a nickname. They are safe ONLY
# because nothing is read until the title has proved it is a card first, so
# this gate is the load-bearing part of the file and not a formality.
#
# Proof is a manufacturer, a slab, or the words themselves. A bare "Michael
# Jordan Auto" gets no verdict from us - and should not, because it is equally
# a signed photo, a signed ball, or a $9 print.
_MAKERS = (
    r"topps|panini|bowman|upper\s*deck|\bupperdeck\b|fleer|donruss|score\b|"
    r"leaf\b|pinnacle|skybox|o-?pee-?chee|\bopc\b|stadium\s*club|allen\s*&?\s*"
    r"ginter|goodwin|sage\b|press\s*pass|playoff\b|sp\s*authentic|fleer\s*ultra|"
    r"metal\s*universe|collector'?s?\s*choice|\bsportkings\b|futera|wild\s*card"
)
# 🚨 "MEMORY CARD" IS NOT A TRADING CARD, AND THIS FILE HAD TO LEARN IT THE
# SAME WAY pricebook DID. Caught by CI on 2026-08-22 against a refreshed board:
#
#     Nintendo 64 Console w/ Cord, Cable, 2 Controllers Memory Card & 4 Games
#
# opened the gate on the bare word "card" and drew a Card read: line on a games
# console - exactly the wallpaper the board test exists to prevent. pricebook's
# own `card` guard has been bundle-aware since the cameras were added
# 2026-07-28 ("w/ SD Card", "64GB Memory Card"); the lesson simply never
# crossed over to this file.
#
# Graphics/video/sound cards are here for the same reason and are not
# hypothetical: the liquidation and estate sources sell PC parts by the pallet.
_STORAGE = (r"(?<!sd )(?<!cf )(?<!xd )(?<!gb )(?<!tb )(?<!mb )(?<!memory )"
            r"(?<!sim )(?<!micro )(?<!microsd )(?<!smart )(?<!user )"
            r"(?<!graphics )(?<!video )(?<!sound )(?<!network )(?<!capture )"
            r"(?<!credit )(?<!gift )(?<!id )(?<!key )(?<!pc )(?<!pcmcia )")
_CARD_WORDS = (
    rf"trading\s*cards?|{_STORAGE}\bcards?\b|\bslabb?e?d?\b|\bgraded\b|"
    r"\brookie\s*cards?\b|\bnon-?sports?\b|\btcg\b"
)
_GRADER = r"\b(psa|bgs|sgc|cgc|hga|ace|csg|tag)\s*\.?\s*(10|9\.5|9|8\.5|8|7|6|5|4|3|2|1)\b"

# 🚨 PARALLEL VOCABULARY IS NOT PROOF OF A CARD. "Prizm" is a Geo, "refractor"
# is a telescope part, "chrome" is a bumper - and the first cut opened the whole
# read on any of them, which handed "1998 Chevy Prizm 4 door sedan" a
# pre-1980-vintage bonus and a verdict. But sellers really do title cards with
# no maker at all ("2019 Prizm Zion Williamson RC #/25"), so deleting the words
# loses real cards.
#
# So they are WEAK evidence: they open the gate only alongside a second thing
# that is card-shaped and almost nothing else - a rookie tag, a hit, a print
# run, or a card number. The Chevy has none of those; the Zion has three.
_WEAK_CARD_WORDS = (
    r"\brefractors?\b|\bparallel\b|\binserts?\b|\bprizms?\b|\bx-?fractors?\b|"
    r"\bchrome\b|\boptic\b|\bmosaic\b"
)
_CARD_CONTEXT = (
    r"\brookie\b|\brc\b|\bauto(?:graph(?:ed)?)?\b|\bpatch\b|\brelics?\b|"
    r"\bgame[- ]?(?:used|worn)\b|#\s*\d|/\s*\d{1,4}\b|\bnumbered\b|\bhobby\b"
)

_STRONG = re.compile(rf"{_MAKERS}|{_CARD_WORDS}|{_GRADER}")
_WEAK = re.compile(_WEAK_CARD_WORDS)
_CONTEXT = re.compile(_CARD_CONTEXT)


def _is_card(t: str) -> bool:
    """Has this title proved it is a trading card? See the note above."""
    if _STRONG.search(t):
        return True
    return bool(_WEAK.search(t) and _CONTEXT.search(t))

# The other trading-card families. They are recognised so the reader can HAND
# THEM BACK rather than judge them: Pokemon already has three measured tiers in
# pricebook, and a triage verdict beside a real comp is noise at best and a
# contradiction at worst.
_TCG = re.compile(
    r"pok[eé]mon|\bpkmn\b|magic\s*the\s*gathering|\bmtg\b|yu-?gi-?oh|\bygo\b|"
    r"digimon|one\s*piece\s*card|lorcana|flesh\s*and\s*blood|weiss\s*schwarz"
)
# Pokemon specifically: the one TCG this repo has a real price source for.
_POKEMON = re.compile(r"pok[eé]mon|\bpkmn\b")

# 🚨 "POKEMON" ON ITS OWN IS NOT A CARD, AND THE CARDS CHANNEL PROVED IT.
# Leron, 2026-08-22: "The cards channel is getting pokemon video games, is only
# supposed to get pokemon cards". Five cartridges were sitting in #cards:
#
#     Pokemon Ruby for GameBoy Advance Cartridge Only
#     Pokemon Silver Version Gameboy Color Game
#     Gameboy Pokemon Special Edition Game Cartridge (Untested)
#
# `_TCG` opens on the bare word, `read()` then returns family="tcg", and
# `notify.channel_for` routes anything `is_card` to the cards lane - so every
# Pokemon CARTRIDGE routed as a Pokemon CARD.
#
# `pricebook._PKMN_GAME_WORDS` is the same guard pointed the other way: it
# keeps cartridges out of the card TIERS. Imported rather than re-written, so
# the two can never drift - the last time this vocabulary was duplicated, one
# copy learned "console sold WITH the game" and the other did not.
from .pricebook import _PKMN_GAME_WORDS as _GAME_WORDS
_VIDEO_GAME = re.compile(_GAME_WORDS, re.I)

# 🚨 AND NEITHER IS A PLUSH TOY. Blocking cartridges just moved the leak: the
# very next run put these in #cards, all on the strength of the word "Pokemon":
#
#     Monopoly Pokemon Johto Edition Board Game
#     Funko Pop! Games: Pokemon Jumbo Cresselia #965 Limited Edition
#     Pokemon Psyduck Plush Toy 2024 Nintendo Creatures Game Freak
#     2022 Pokemon TCG Battle Academy Board Game Set 3 Decks Pikachu
#
# The last one even says "TCG" - it is a boxed board game that contains decks,
# not a card. This is the same family as the "1pc Pokemon Crystal Ball Pikachu"
# that once quoted a $100 max bid on a plastic toy: MERCHANDISE BORROWING A
# VALUABLE NAME. `pricebook` keeps this vocabulary in ACCESSORY_EXCLUDE for
# every non-card model; the card reader needs its own because the card tiers
# are deliberately exempt from that one.
_MERCH = re.compile(
    r"\bplush\b|\bplushie\b|\bstuffed\b|\bfunko\b|\bpop!|\bbobblehead\b|"
    r"\bboard\s*game\b|\bmonopoly\b|\bjigsaw\b|\bpuzzle\b|\bkeychain\b|"
    r"\bkey\s*chain\b|\blanyard\b|\bbackpack\b|\blunch\s*box\b|\bmug\b|"
    r"\bblanket\b|\bpillow\b|\bposter\b|\bt-?shirt\b|\bhoodie\b|\bhat\b|"
    r"\bfigurine\b|\baction\s*figure\b|\bcrystal\s*ball\b|\bnight\s*light\b|"
    r"\bslippers\b|\bcostume\b|\bplate\b|\bwallet\b|\bumbrella\b",
    re.I)


# --- the era rule -----------------------------------------------------------
# "Avoid 80s and '90s" is the JUNK WAX ERA and it is the most reliable rule in
# the hobby: the manufacturers printed to meet demand that never came, so the
# supply is effectively infinite and a mint 1990 common is worth less than the
# sleeve you put it in.
#
# 🚨 THE WHOLE DECADE IS OUT, AND THE ESCAPE IS A NAMED CARD.
#
# The first cut of this file refined "avoid the 80s" into a 1987 cutoff, on the
# reasoning that the print-run explosion starts in 1987 and a literal reading
# bins the 1986 Fleer Jordan. Leron asked him directly, 2026-08-22:
#
#     "80's and 90s unless Kobe 96 or Jordan 86"
#
# So the boundary was wrong and the exceptions are SPECIFIC CARDS. That is both
# more faithful and more accurate: a 1986 Donruss common is exactly as
# worthless as a 1991 one, and no date range separates them - what separates
# them is being one of two particular rookies.
_JUNK_WAX = (1980, 1999)

# player -> the years that escape the veto, and why. One line each.
#
# 🚨 THE YEAR IS WHAT MAKES THE BARE SURNAME SAFE. `\bjordan\b` alone would
# match Jordan Love and Jordan Spieth; paired with 1986 it can only be the one
# Jordan. Same reason the list is keyed on the pair rather than the name: this
# is emphatically NOT "Jordan is good", it is "the 1986 Jordan is good". A 1991
# Fleer Jordan is junk wax and still gets the full veto, which is precisely the
# distinction he was drawing.
_ERA_EXCEPTIONS = (
    ("jordan", {1986}, "the 1986 Fleer rookie - the card of the era"),
    # His rookie season is 1996-97 and _YEAR reads the LEADING year, so both
    # "1996 Topps Chrome" and "1996-97 Topps Chrome" land on 1996. Kept to the
    # year he named: a 1997-98 Kobe is his second year, not a rookie.
    ("kobe", {1996}, "the 1996-97 rookie year"),
)

# 🚨 A YEAR IS NOT A CARD NUMBER. "#1952" and "124/1999" are positions in a
# set; the lookarounds keep both out. Season spans ("2023-24 Prizm") are read
# on the leading year, which is how the hobby names them.
_YEAR = re.compile(r"(?<![\d/#-])(19[2-9]\d|20[0-4]\d)(?![\d])")


def _era(year: Optional[int]) -> str:
    if year is None:
        return "unknown"
    if year <= 1979:
        return "vintage"
    if _JUNK_WAX[0] <= year <= _JUNK_WAX[1]:
        return "junk wax"
    return "modern"


def _era_exception(t: str, year: Optional[int]):
    """The named card that escapes the era veto, or None. See _ERA_EXCEPTIONS."""
    if year is None:
        return None
    for name, years, why in _ERA_EXCEPTIONS:
        if year in years and re.search(rf"\b{name}\b", t):
            return why
    return None


# --- hits: the cards that are GUARANTEED not to be in every pack ------------
# The friend's "hit card". A hit is manufactured scarce - the autograph and the
# swatch physically cannot be in every pack - so it is the one signal that
# needs no other evidence to be worth a look.
_AUTO = re.compile(
    r"\bauto(?:graph(?:ed)?)?\b|\bsigned\b|\bon-?card\s*auto|\bsticker\s*auto|"
    r"\brpa\b|\bdual\s*auto|\btriple\s*auto"
)
# 🚨 "AUTHENTIC" AND "AUTOMATIC" BOTH START WITH AUTO. \b would happily match
# neither, but `auto` inside `sp authentic` (a real Upper Deck brand) does end
# on a word boundary in "sp auto"... it does not, and the tests below pin it.
# What DOES need excluding is the sticker-auto redemption that never arrived.
_AUTO_VOID = re.compile(r"\bredemption\s*(?:expired|only)|\bunsigned\b|\bno\s*auto\b")

_PATCH = re.compile(r"\bpatch\b|\bprime\s*patch\b|\blaundry\s*tag\b|\bbutton\b|\btag\b")
_RELIC = re.compile(
    r"\brelics?\b|\bmem(?:orabilia)?\b|\bjersey\s*(?:card|swatch|relic)|"
    r"\bgame[- ]?(?:used|worn)\b|\bswatch\b|\bbat\s*(?:barrel|knob|relic)\b"
)

# --- chase: in packs, but not every pack ------------------------------------
# The friend's "chase card". Parallels, inserts, short prints - the reason
# people rip a box they already have the base set of.
_PARALLEL_NOUN = (
    r"refractors?|x-?fractors?|superfractors?|prizms?|parallels?|inserts?|"
    r"holo(?:foil)?s?|cracked\s*ice|shimmers?|mojo|pulsar|disco|velocity|"
    r"scope|laser|hyper|wave|sapphire|die-?cut|atomic|finite|foilboard"
)
_CHASE = re.compile(
    rf"\b(?:{_PARALLEL_NOUN})\b|\bshort\s*prints?\b|\bssp\b|\bcase\s*hit\b|"
    r"\bvariation\b|\bphoto\s*variation\b|\bimage\s*variation\b"
)
# 🚨 A COLOUR IS NOT A CHASE CARD BY ITSELF. Every parallel has a colour name
# and so does every base card's photo: "Gold" alone matched "Golden State
# Warriors" and "Blue Jays" on the first cut. A colour only counts when it sits
# beside a parallel noun ("Silver Prizm", "Gold Refractor") or a print run,
# which is exactly how the hobby writes them.
_COLOUR_PARALLEL = re.compile(
    rf"\b(?:gold|silver|red|blue|green|orange|purple|pink|black|teal|bronze|"
    rf"camo|tie-?dye|rainbow|neon|aqua|copper)\s+(?:{_PARALLEL_NOUN})\b"
)

# --- numbered: the friend's second-named signal -----------------------------
# 🚨 SERIAL NUMBERING DID NOT EXIST BEFORE ~1996. That single fact is what
# makes this pattern safe: "124/165" on a 1990 card is the card's POSITION IN
# THE SET, printed on the front of nearly every base card of that era, and
# reading it as a print run of 165 would turn the junk-wax bin into a wall of
# false CHASEs. So a slash pair is only a serial when the card is modern enough
# for serials to be a thing, or when nothing dates it at all.
_SERIAL = re.compile(r"(?<![\d/#])(\d{1,4})\s*/\s*(\d{1,4})\b(?!\s*/)")
# 🚨 THE NUMERATOR IS USUALLY MISSING. Sellers write the RUN and drop the copy
# number - "Auto /99", "RC #/25", "numbered to 50" - so a pattern that only
# reads "n/d" misses the commonest way the hobby states the one signal the
# friend named out loud. Caught on the first smoke test: "Luka ... Auto /99"
# scored as an un-numbered auto.
# 🚨 "w/" IS NOT A PRINT RUN. The first cut read "Card lot w/ 50 cards" as a
# run of 50 - the abbreviation for "with" is a letter and a slash, which is
# exactly the shape being matched. Excluding a preceding `w` costs nothing (no
# parallel name ends in w) and kills the whole family of false serials.
_BARE_RUN = re.compile(
    r"(?<![\dw/])/\s*(\d{1,4})\b|#\s*/\s*(\d{1,4})\b|"
    r"\bnumbered\s*(?:to\s*)?(\d{1,4})\b|\bser(?:ial)?\.?\s*#?\s*/\s*(\d{1,4})\b"
)
_ONE_OF_ONE = re.compile(r"\b1\s*(?:/|of)\s*1\b|\bone\s*of\s*one\b")
_SERIAL_ERA_FLOOR = 1996
# Above this the "numbered" claim stops meaning scarce - a /5000 print run is a
# base card with a number stamped on it.
_MAX_MEANINGFUL_RUN = 999


# --- rookies ----------------------------------------------------------------
# 🚨 \bRC\b IS THE MOST DANGEROUS TOKEN IN THE FILE and it is only tolerable
# behind the card gate. "1st Bowman" is in here because Bowman's first-year
# prospect card is the rookie card in baseball whether or not it says RC.
_ROOKIE = re.compile(
    r"\brookie\b|\brc\b|\brpa\b|\b1st\s*bowman\b|\bfirst\s*bowman\b|\bybc\b|"
    r"\byoung\s*guns\b|\bprospects?\b|\bdebut\b|\brated\s*rookie\b"
)


# --- brands: "there's brands of cards that are more expensive" --------------
# Three tiers, because that is as fine as a title can honestly cut it. Brand is
# a BUMP, never a verdict: a National Treasures base card is still a base card,
# and a Topps base rookie auto is still an auto.
_BRAND_ULTRA = re.compile(
    r"national\s*treasures?|\bflawless\b|\bimmaculate\b|\bexquisite\b|"
    r"\bdominion\b|\bopulence\b|\bimpeccable\b|\bnoir\b|\btranscendent\b|"
    r"\bmuseum\s*collection\b|\bsuperfractor\b"
)
_BRAND_PREMIUM = re.compile(
    r"topps\s*chrome|bowman\s*chrome|\b1st\s*bowman\b|\bfinest\b|\bprizm\b|"
    r"\boptic\b|\bselect\b|\bmosaic\b|\bspectra\b|\bobsidian\b|\bcontenders\b|"
    r"\bsapphire\b|\bsp\s*authentic\b|\bthe\s*cup\b|\bultimate\s*collection\b|"
    r"\bchrome\b|\bcertified\b|\bpanini\s*one\b"
)
# 🚨 THE JUNK WAX ROLL CALL. These are not bad brands, they are brands whose
# BASE product was printed without limit. Naming them is the difference between
# "1991 card" (maybe something) and "1991 Score" (a coaster).
_BRAND_BULK = re.compile(
    r"\bscore\b|\bfleer\b|\bpinnacle\b|\bskybox\b|\bcollector'?s?\s*choice\b|"
    r"\bpacific\b|\bstarting\s*lineup\b|\bpro\s*set\b|\bpro\s*cards\b"
)


# --- the things that end the conversation -----------------------------------
_FAKE = re.compile(
    r"\breprints?\b|\bproxy\b|\bproxies\b|\bfake\b|\bcustom\b|\bnovelty\b|"
    r"\bfacsimile\b|\bpreprint\b|\b\brp\b\b|\baceo\b|\bart\s*card\b"
)
# A pile is not a card. This is the same finding pricebook's DEAD_MODELS
# recorded for Pokemon lots (p25 $10.72, median $25.18, max $1,061 on n=65):
# the value is WHICH cards are in the box and the title never says.
_PILE = re.compile(
    r"\blots?\b|\bbulk\b|\bcollections?\b|\bbinders?\b|\bboxe?s?\s*of\b|"
    r"\b\d{2,}\s*cards?\b|\bcomplete\s*set\b|\bteam\s*set\b|\brepack\b|"
    r"\bmystery\s*pack\b|\(\s*\d{2,}\s*\)"
)


# --- players: the rule that a title reader cannot finish --------------------
# 🚨 "THERE'S PLAYERS THAT ARE BETTER THAN OTHER PLAYERS" IS TRUE AND MOSTLY
# UNENCODABLE. A real player list is thousands of names, it re-ranks every
# season, and a stale one is worse than none - it would confidently PASS
# whoever broke out last year. So this is a deliberately SHORT list of names
# whose value has been stable for a decade or more, it only ever ADDS, and the
# absence of a name here is never held against a card.
_HOBBY_NAMES = re.compile(
    r"\bjordan\b|\blebron\b|\bkobe\b|\bbrady\b|\bmahomes\b|\bluka\b|"
    r"\bdon[cč]i[cć]\b|\bwembanyama\b|\bwemby\b|\bohtani\b|\btrout\b|"
    r"\bgriffey\b|\bmantle\b|\bjeter\b|\bcurry\b|\bmessi\b|\bronaldo\b|"
    r"\bgretzky\b|\bmcdavid\b|\bmontana\b|\bpel[eé]\b|\bjudge\b|\bacuna\b|"
    r"\bdurant\b|\bgiannis\b|\bjokic\b|\bburrow\b|\bjefferson\b"
)


@dataclass(frozen=True)
class Signal:
    """One thing the title said, what kind of thing it is, and what it moved."""

    kind: str        # hit | chase | numbered | rookie | grade | brand | era | player | stop
    detail: str      # human-readable, goes straight into an alert
    points: int


@dataclass
class CardRead:
    """A card shop's first two seconds, as data."""

    title: str
    family: str = ""                 # "sports" | "tcg" | "" (not a card)
    year: Optional[int] = None
    era: str = "unknown"
    grade: Optional[str] = None      # "PSA 10"
    print_run: Optional[int] = None  # 99 from "/99"
    score: int = 0
    verdict: str = "UNKNOWN"         # CHASE | LOOK | PASS | PRICED | UNKNOWN
    signals: list = field(default_factory=list)

    @property
    def is_card(self) -> bool:
        return bool(self.family)

    @property
    def reasons(self) -> list:
        return [s.detail for s in self.signals]


# The signals that are REASONS on their own, as opposed to modifiers. A card
# earns its verdict from these; brand and player only ever adjust one.
_CORE_KINDS = ("hit", "chase", "numbered", "rookie", "grade")

# Where a verdict flips. CHASE means pull it out of the box and photograph it;
# LOOK means one signal fired and a human has to finish the job; PASS means the
# friend's rules say this is what the box is FULL of.
_CHASE_AT = 45
_LOOK_AT = 18


@dataclass(frozen=True)
class CardTier:
    """A sports-card tier that is READY to be priced but is not priced yet.

    Everything a pricebook.Model needs except the one thing that matters: a
    measured comp. See CARD_TIERS.
    """

    key: str
    label: str
    query: str          # the eBay SOLD search that measures this tier
    include: str        # regex: this listing is one of these
    specificity: int
    why: str


def read(title: str) -> CardRead:
    """Triage one card title against the shop's five rules.

    Returns a verdict, never a price. `verdict == "PRICED"` means the title is
    a Pokemon/TCG card that `pricebook.match()` may already carry a measured
    comp for - ask it, not this.
    """
    t = normalize(title)
    if not t:
        return CardRead(title=title or "")

    if _TCG.search(t):
        # 🚨 PARSE THE GRADE EVEN THOUGH WE REFUSE TO JUDGE. The verdict is
        # handed on, but `comp_query` still builds this card's sold search -
        # and the grade is the single biggest price variable a card title
        # carries. Skipping it here dropped "PSA 9" from the lookup for exactly
        # the cards where the grade IS the comp.
        gm = re.search(_GRADER, t)
        grade = f"{gm.group(1).upper()} {gm.group(2)}" if gm else None

        # 🚨 "HAND IT BACK TO THE BOOK" STOPPED BEING TRUE ON 2026-08-22, and a
        # stale handoff is worse than none: `pokemon-cards` is benched (its
        # blanket $92/$112.50 comps were arming $49 bids on $0.14 cards), so a
        # "PRICED" verdict here now means the card is dropped by the scout AND
        # priced by nobody. Pokemon goes to `pokemontcg`, which prices the CARD
        # rather than the category.
        #
        # Offline only - this runs on every listing in a 10,000-row sweep, and
        # the price lookup belongs in the scout where it is capped at the dozen
        # cards actually posted.
        # A cartridge is not a card, whatever else the title says. Checked
        # BEFORE the price sources, because both of them would happily look up
        # "Pokemon Ruby" and hand back a trading card of the same name.
        if _VIDEO_GAME.search(t) or _MERCH.search(t):
            return CardRead(title=title)
        if _POKEMON.search(t):
            from .pokemontcg import identify as _pid, verdict as _pv
            v = _pv(_pid(title))
            return CardRead(title=title, family="tcg", verdict=v.verdict,
                            grade=grade, signals=[Signal("stop", v.why, 0)])
        return CardRead(title=title, family="tcg", verdict="PRICED",
                        grade=grade,
                        signals=[Signal("stop", "Non-Pokemon TCG single - no "
                                        "measured comp and no price source "
                                        "wired; this triage is for sports "
                                        "cards.", 0)])
    if not _is_card(t):
        return CardRead(title=title)

    r = CardRead(title=title, family="sports")
    sig: list = []

    # --- the stoppers, first, because nothing after them matters ------------
    if _FAKE.search(t):
        sig.append(Signal("stop", "Says REPRINT / custom / proxy - that is the "
                                  "whole card, whoever is on it.", -100))
    if _PILE.search(t):
        sig.append(Signal("stop", "This is a PILE, not a card. Value is which "
                                  "cards are in it and the title never says - "
                                  "buy it off the photos or not at all.", -40))

    # --- rule 1: the era ----------------------------------------------------
    ym = _YEAR.search(t)
    if ym:
        r.year = int(ym.group(1))
    r.era = _era(r.year)

    # --- rule 2 + 3: hits and chase cards -----------------------------------
    is_auto = bool(_AUTO.search(t)) and not _AUTO_VOID.search(t)
    if is_auto:
        sig.append(Signal("hit", "AUTOGRAPH - a hit card. Cannot be in every "
                                 "pack, so it is never base.", 40))
    if _PATCH.search(t):
        sig.append(Signal("hit", "PATCH - the top half of the memorabilia tier "
                                 "(a multi-colour patch is its own market).", 30))
    elif _RELIC.search(t):
        sig.append(Signal("hit", "RELIC / game-used swatch - a hit card.", 25))

    if _ONE_OF_ONE.search(t):
        r.print_run = 1
        sig.append(Signal("numbered", "ONE OF ONE. There is no second copy.", 60))
    else:
        run = _print_run(t, r.year)
        if run is not None:
            r.print_run = run
            sig.append(Signal("numbered", f"SERIAL NUMBERED /{run} - "
                                          f"{_run_words(run)}", _run_points(run)))

    if _CHASE.search(t) or _COLOUR_PARALLEL.search(t):
        sig.append(Signal("chase", "PARALLEL / INSERT - a chase card: in packs, "
                                   "but not every pack.", 12))

    # --- rule 4: rookies ----------------------------------------------------
    if _ROOKIE.search(t):
        sig.append(Signal("rookie", "ROOKIE / first-year card - the year the "
                                    "hobby actually pays for.", 20))

    # --- rule 5: brand ------------------------------------------------------
    if _BRAND_ULTRA.search(t):
        sig.append(Signal("brand", "ULTRA-PREMIUM brand - the boxes that cost "
                                   "four figures and are sold on their hits.", 20))
    elif _BRAND_PREMIUM.search(t):
        sig.append(Signal("brand", "PREMIUM brand - chrome/optic-class product, "
                                   "where the parallels carry the value.", 10))
    elif _BRAND_BULK.search(t):
        sig.append(Signal("brand", "BULK-ERA brand - this product's base cards "
                                   "were printed without limit.", -5))

    # --- grade --------------------------------------------------------------
    gm = re.search(_GRADER, t)
    if gm:
        r.grade = f"{gm.group(1).upper()} {gm.group(2)}"
        sig.append(Signal("grade", f"GRADED {r.grade} - {_grade_words(gm.group(2))}",
                          _grade_points(gm.group(2))))

    # --- era scoring, applied LAST because the grade can soften it ----------
    # 🚨 A GRADE IS THE ONE THING THAT BEATS THE ERA VETO. Junk wax was printed
    # without limit, so the only scarcity left in it is CONDITION - which is
    # precisely what a 9 or a 10 certifies. A PSA 10 1989 rookie is a real card
    # in a decade of coasters, and a blanket era veto would bin it.
    if r.era == "junk wax":
        exception = _era_exception(t, r.year)
        graded_high = bool(gm and float(gm.group(2)) >= 9)
        if exception:
            # 🚨 A NAMED EXCEPTION WAIVES THE ERA RULE OUTRIGHT, not partially.
            # He named these against a decade he otherwise refuses, so a card
            # that clears that bar is not a junk-wax card wearing a discount.
            sig.append(Signal("era", f"{r.year} is junk wax, EXCEPT this one - "
                                     f"{exception}. The named exception to the "
                                     f"decade rule.", 25))
            # ...and so is its brand. "1986 Fleer" is the exact product that
            # matters; carding it as a bulk-era brand contradicts the exception.
            sig = [x for x in sig if not (x.kind == "brand" and x.points < 0)]
        else:
            sig.append(Signal("era", f"{r.year} is JUNK WAX (1980-1999) - printed "
                                     f"without limit, so the commons are worthless"
                              + (". The grade is the exception: condition is the "
                                 "only scarcity this era has left." if graded_high
                                 else " and the stars are barely better."),
                              -17 if graded_high else -35))
    elif r.era == "vintage":
        sig.append(Signal("era", f"{r.year} is PRE-1980 vintage - a different "
                                 f"game from the junk wax rule: these were "
                                 f"printed small and thrown away.", 15))

    if _HOBBY_NAMES.search(t):
        sig.append(Signal("player", "Names a card-shop-tier player - a bump, "
                                    "not a verdict; the player list is the part "
                                    "a title reader cannot finish.", 10))

    # 🚨 A BUMP NEEDS SOMETHING TO BUMP. "There's brands of cards that are more
    # expensive" and "there's players that are better" are both true and both
    # MODIFIERS: a National Treasures BASE card is still a base card, and
    # Jordan's name is on more worthless cardboard than anybody's. Left
    # ungated, an ultra-premium brand alone scored a bare base card into LOOK.
    # So a POSITIVE brand or player bump only counts once a core signal has
    # fired. The negative brand note is not a bump and always stands - a
    # bulk-era brand is a warning that needs no permission.
    if not any(s.kind in _CORE_KINDS for s in sig):
        sig = [replace(s, points=0,
                       detail=s.detail + " (scored 0 - a bump needs a signal to "
                                         "bump, and this card has none)")
               if s.kind in ("brand", "player") and s.points > 0 else s
               for s in sig]

    r.signals = sig
    r.score = sum(s.points for s in sig)

    if any(s.kind == "stop" and s.points <= -100 for s in sig):
        r.verdict = "PASS"
    elif r.score >= _CHASE_AT:
        r.verdict = "CHASE"
    elif r.score >= _LOOK_AT:
        r.verdict = "LOOK"
    else:
        r.verdict = "PASS"
        if not [s for s in sig if s.kind in _CORE_KINDS]:
            r.signals = sig + [Signal("stop", "BASE CARD - no auto, no serial, "
                                              "no parallel, no rookie. Comes in "
                                              "every pack; this is what the box "
                                              "is full of.", 0)]
    return r


def _print_run(t: str, year: Optional[int]) -> Optional[int]:
    """The `/99` print run, or None when the slash pair is a set position.

    A BARE run ("/99") is unambiguous - nothing else in a card title is written
    that way - so it is read whatever the year. A PAIR ("124/165") is only a
    serial once serials existed; see _SERIAL_ERA_FLOOR.
    """
    bare = _BARE_RUN.search(t)
    if bare:
        run = int(next(g for g in bare.groups() if g))
        if 2 <= run <= _MAX_MEANINGFUL_RUN:
            return run
    for m in _SERIAL.finditer(t):
        num, run = int(m.group(1)), int(m.group(2))
        if run < 2 or num > run or run > _MAX_MEANINGFUL_RUN:
            continue
        # See _SERIAL_ERA_FLOOR: before serials existed, n/d is where the card
        # sits in the set, not how many were made.
        if year is not None and year < _SERIAL_ERA_FLOOR:
            continue
        return run
    return None


def _run_points(run: int) -> int:
    if run <= 10:
        return 40
    if run <= 25:
        return 32
    if run <= 99:
        return 25
    if run <= 249:
        return 15
    return 8


def _run_words(run: int) -> str:
    if run <= 10:
        return "that is case-hit scarce."
    if run <= 25:
        return "low enough that collectors chase the number itself."
    if run <= 99:
        return "the range where the serial starts carrying the price."
    if run <= 249:
        return "numbered, but not scarce - it still needs a second reason."
    return "numbered in name only; a run this big is near-base."


def _grade_points(g: str) -> int:
    v = float(g)
    if v >= 10:
        return 35
    if v >= 9:
        return 15
    return 2


def _grade_words(g: str) -> str:
    v = float(g)
    if v >= 10:
        return ("the top pop. The grade IS the price here - it is the one thing "
                "a raw card's title never states.")
    if v >= 9:
        return "a collector-grade slab; the step to a 10 is often several times the money."
    return ("a low slab. Graded proves it is real and states its condition, but "
            "a 7 and a 10 of the same card differ tenfold.")


# --- what it is worth: the one-click check ----------------------------------
# 🚨 THIS FILE STILL DOES NOT PRICE ANYTHING, and it still must not. What it
# CAN do is aim the question. Leron, 2026-08-22: "There is no value assessment
# on the cards" - and he is right that a verdict with no number attached is
# half an answer.
#
# The rest of the book answers this by linking the eBay SOLD search that
# produced its comp, so the claim "this sells for more" is checkable in one
# click instead of trusted (see hunt.to_alert). A card has no comp to link,
# but it has something the other categories do not: the title states the exact
# identity - year, player, set, parallel, serial, grade - and that identity is
# a sold search that returns THIS card rather than its category.
#
# So the value assessment is the real sold prices, one tap away, aimed
# precisely. Which is also what the shop does: it does not know the number, it
# knows where to look it up.
#
# 🚨 THE QUERY IS THE WHOLE JOB. "pokemon card" returns the category and is
# worthless; the full raw title returns nothing at all, because sellers stuff
# it with hype no other listing shares. Both failure modes are silent - a
# link that returns 0 results looks exactly like a link that works.
_QUERY_NOISE = re.compile(
    r"\b(?:l@@k|look|wow|hot|rare|scarce|invest(?:ment)?|grail|stunning|"
    r"beautiful|gorgeous|sharp|clean|centered|nm|near\s*mint|mint|gem|pack\s*"
    r"fresh|fresh|sweet|nice|great|excellent|vintage|htf|hard\s*to\s*find|"
    r"free\s*ship(?:ping)?|ships?\s*fast|fast\s*ship(?:ping)?|see\s*pics?|"
    r"see\s*photos?|read|nr|no\s*reserve|combined\s*shipping|must\s*see|"
    r"low\s*pop|pop\s*\d+|for\s*sale|sale|deal|steal|wysiwyg|pictured|"
    r"actual\s*card|you\s*get|mystery|repack)\b|[^\w\s/#.-]"
)
# Emoji and decoration sellers pad titles with. Stripped separately because the
# class above would otherwise have to enumerate them.
_QUERY_TRIM = re.compile(r"\s+")

# eBay's own relevance falls apart past roughly a dozen words - the engine ANDs
# them, so one stray token returns nothing. Short and identifying beats long
# and faithful.
_MAX_QUERY_WORDS = 12


def comp_query(r: CardRead) -> str:
    """The eBay SOLD search that prices THIS card, not its category.

    Built from the listing title with seller hype removed, because the title is
    where the card's identity actually lives - and the identity IS the price in
    this category, which is the whole reason a category-level comp is useless
    here.
    """
    if not r.is_card:
        return ""
    t = _QUERY_NOISE.sub(" ", normalize(r.title))
    words = [w for w in _QUERY_TRIM.sub(" ", t).split() if w]
    # 🚨 KEEP THE GRADE, WHEREVER IT SAT. It is the single biggest price
    # variable a card title carries and it is often at the very end, past the
    # word cap - "1999 Pokemon Base Charizard Holo 4/102 Unlimited PSA 9" loses
    # exactly the token that matters if the tail is simply truncated.
    grade = r.grade.lower().split() if r.grade else []
    if grade:
        words = [w for w in words if w not in grade]
    kept = words[:max(1, _MAX_QUERY_WORDS - len(grade))] + grade
    return " ".join(kept)


def explain(r: CardRead) -> str:
    """The read as a card shop would say it out loud."""
    if not r.is_card:
        return "Not recognised as a trading card - no maker, no slab, no card wording."
    if r.family == "tcg":
        return r.reasons[0]
    head = {
        "CHASE": "CHASE - pull it out and photograph it.",
        "LOOK":  "LOOK - one signal fired; a human has to finish this one.",
        "PASS":  "PASS - the rules say this is what the box is full of.",
    }.get(r.verdict, r.verdict)
    lines = [f"{head}  (score {r.score})"]
    for s in r.signals:
        lines.append(f"  - {s.detail}")
    lines.append("  - No price here on purpose: a title cannot carry condition, "
                 "and condition is most of a raw card's value. This says whether "
                 "to look, not what to pay.")
    lines.append(f"\n  WHAT IT SELLS FOR - real sold prices for THIS card:\n"
                 f"  {comp_url(r)}")
    return "\n".join(lines)


def comp_url(r: CardRead) -> str:
    """The eBay SOLD listings for this exact card. "" when it is not a card.

    🚨 NOT `used_only`. The price-book links pin eBay's Used filter so the
    population matches the measured comp - but nothing here was measured, and a
    graded slab is listed under half a dozen different conditions. Filtering
    would quietly hide most of the very sales being looked up.
    """
    q = comp_query(r)
    return sold_url(q) if q else ""


def one_liner(r: CardRead) -> str:
    """A single alert-sized line, or "" when there is nothing worth saying."""
    if not r.is_card or r.family == "tcg":
        return ""
    top = [s.detail.split(" - ")[0] for s in r.signals
           if s.kind in ("hit", "numbered", "rookie", "grade", "chase")][:3]
    if r.verdict == "PASS" and not top:
        return "Card read: PASS - base card, no auto/serial/parallel/rookie."
    return f"Card read: {r.verdict}" + (" - " + "; ".join(top) if top else "")


# --- CANDIDATE SPORTS-CARD TIERS -------------------------------------------
# 🚨 THESE ARE NOT MODELS AND MUST NOT BECOME ONE UNTIL THEY ARE MEASURED.
# The book's one law is that a model ships with a measured comp; these carry
# the matching half of a tier - the include/exclude that says "this listing is
# one of these" - and no number at all. `flipscout cardcomp` turns one into a
# real Model once Leron has pasted a browser measurement for it.
#
# Leron, 2026-08-22: "I'm looking for deals. I want value." Fair, and the
# triage alone does not give him either. What stands between the scout and a
# real ceiling is exactly four browser pastes, so this exists to make those
# four pastes the only work left.
#
# WHY THESE FIVE. Each is a tier the shop's rules say carries price, chosen so
# a TITLE can identify it - the same test the Pokemon tiers had to pass. And
# each needs a comp near $40 before any bid clears the standing $20-over-$9
# gate, which rules out everything softer: a bare "rookie card" or a bare
# parallel sits in single digits and would measure to a $0.00 ceiling, exactly
# as the raw-vintage-single tier did in DEAD_MODELS.
CARD_TIERS = [
    CardTier(
        key="sports_rpa",
        label="Modern rookie PATCH AUTO (RPA)",
        query="rookie patch auto rpa card",
        include=(r"(?=.*\b(?:rpa|rookie\s*patch\s*auto)\b|"
                 r"(?=.*\brookie\b|.*\brc\b)(?=.*\bpatch\b)(?=.*\bauto"
                 r"(?:graph(?:ed)?)?\b)).*"),
        specificity=60,
        why="The top of the hobby and the one the shop names first: a rookie, "
            "a patch and an autograph on one card. All three are stated in the "
            "title or they are not there.",
    ),
    CardTier(
        key="sports_rookie_auto",
        label="Modern rookie AUTOGRAPH",
        query="rookie auto card 2020 2021 2022 2023",
        include=(r"(?=.*\b(?:rookie|rc|1st\s*bowman)\b)"
                 r"(?=.*\bauto(?:graph(?:ed)?)?\b)"
                 r"(?=.*\b20[0-2]\d\b).*"),
        specificity=54,
        why="An autograph cannot be in every pack, so it is the one signal "
            "that needs no other evidence. Paired with a rookie year, which is "
            "the year the hobby actually pays for.",
    ),
    CardTier(
        key="sports_low_numbered",
        label="Serial numbered /25 or lower",
        query="card numbered 25 or less auto refractor prizm",
        include=(r"(?=.*\b20[0-2]\d\b)"
                 r"(?=.*(?:/\s*(?:[1-9]|1\d|2[0-5])\b|\b1\s*of\s*1\b)).*"),
        specificity=52,
        why="The shop's second named signal. Under /25 the print run itself "
            "carries the price, and the run is always stated in the title "
            "because it is the reason to buy the card.",
    ),
    CardTier(
        key="sports_graded_rookie",
        label="Graded 9 or 10 modern rookie",
        query="psa 10 rookie card 2018 2019 2020 2021 2022",
        include=(r"(?=.*\b(?:psa|bgs|sgc|cgc)\s*(?:10|9\.5|9)\b)"
                 r"(?=.*\b(?:rookie|rc)\b)(?=.*\b20[0-2]\d\b).*"),
        specificity=50,
        why="The shape that already works for Pokemon: the grade IS the comp, "
            "because it is the one time a card's condition is in the words. "
            "Mirrors pkmn_card_graded_high deliberately.",
    ),
    CardTier(
        key="sports_sealed_box",
        label="Factory sealed hobby box",
        query="factory sealed hobby box basketball football baseball",
        include=(r"(?=.*\b(?:hobby|blaster|mega|jumbo)\s*box\b|"
                 r".*\bfactory\s*sealed\b)"
                 r"(?=.*\b(?:topps|panini|bowman|upper\s*deck|prizm|optic|"
                 r"select|mosaic|chrome)\b).*"),
        specificity=56,
        why="🚨 THE ONE CARD PRODUCT WITH NO CONDITION VARIABLE. A sealed box "
            "is fully identified by its title - year, brand, product, format - "
            "so it is the only card category that can carry a stable comp at "
            "all. If exactly one of these gets measured, make it this one.",
    ),
]

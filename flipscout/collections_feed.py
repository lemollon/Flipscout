"""Whole collections listed for sale on PriceCharting, and what they're worth.

WHY THIS READS THE PAGE AND NOT THE EMAIL
-----------------------------------------
Leron, 2026-08-23, forwarding a PriceCharting "A user listed their collection
for sale" mail: *"I'm getting collections for sale going to my emails I'm
talking about adding them to my discord"*.

Built from `/collections-for-sale`, not the mail, for the same reason
`hibidwatch` reads the watch-list page (see that module's docstring). Measured
2026-08-23, anonymously, no login and no API key:

  * The page carries **all 50 open collections** with total value, item count,
    location, age, photo and seller link. The email announces ONE, the newest.
  * **22 of the 50 were posted within days.** Every one of the other 21 was
    invisible to an email-fed pipeline, because the mail only fires for
    listings created after you subscribed.
  * The mail's own numbers are a strict subset of the row.

Email = notification, page = feed.

🚨 WE NEVER PRINT THEIR "EST. BUY VALUE". The mail quotes one ($530 against a
$1,252.03 collection) and it is a flat fraction of their own guide total -
their FAQ says sellers "generally get about 40-60%" and the number is that
band applied blind to 64 different products. That is exactly the shape of the
blanket-comp bug that armed $49 bids on $0.14 Pokemon cards: a percentile of a
mixed population is not a comp. See `pokemon-card-blanket-comp-money-bug`.

🚨 THE BOOK PRICES NONE OF THIS, AND THAT IS NOT A GAP TO FIX. Measured on the
64-item collection from Leron's mail: `pricebook.match()` priced **0 of 30**
visible items, $0.00 of $699.36. It is not blindness - the book REFUSED NES
($60), SNES ($85) and Genesis ($59.99) with measured numbers because they
carry no margin, and it stocks four GBA carts. Retro carts were never in it.
So this module does not ask the book what a collection is worth. It asks the
completed-sales history, which is keyed to the exact product instead of a
keyword.

🚨 AND THE GUIDE PRICE ALREADY *IS* THE SOLD MEDIAN. Measured 2026-08-23 on
the top 12 items of that collection, guide vs the median of the 30 most recent
completed eBay sales: **median absolute error 1%**, median signed error -0%,
several exact to the cent (SNES $114.50 vs $114.50). Fetching every product
page to recompute a value would be ~400KB apiece to reproduce a number the
collection page hands over for free.

So the per-item fetch is NOT for the price. It is for the two things a median
cannot say:

    SNES console   30 sales in  23 days · last 2026-08-16 · $41.00-$194.99
    HardBall III   30 sales in 860 days · last 2026-07-25 · $ 1.00-$ 65.00

Same guide-price machinery behind both; one is money next week and the other
is a shelf ornament with a price tag. **Liquidity is the finding here, and it
is only visible because a completed sale carries a DATE.**

🚨 IT HAS ITS OWN CHANNEL, AND FALLS BACK TO DEALS. Leron, 2026-08-23:
"push anything not a card to the deals channel"; 2026-09-03: "Should we give
it its own channel?". A #collections channel was built and reverted once
because every extra webhook/id pair can be left unset and fall back silently;
it came back once `hunt` printed every channel's destination each run, so an
unset webhook is a log line, not a mystery. Webhook only - the card carries
no arming number, so no channel id. `notify.NEVER_CARDS` still carries
"collections" so the card reader never sees a Pokemon-heavy item list and
files the whole offer under cards.

🚨 NO CEILING WORDS ON THE CARD. A collection has no lot id and no clock - the
action is "contact the seller with an offer", not a bid. `discordarm._CEILING`
pulls an arm figure out of a card's RENDERED TEXT across every field, so any
card saying "max bid" or "don't pay over" invites the bot to arm something it
cannot arm. See `flipscout-hibid-watchlist-feed` on that bug class.

WHAT THE CARD LEADS WITH, AND WHY IT IS NOT THE GUIDE VALUE
-----------------------------------------------------------
Leron, 2026-09-03: *"there is so much data and offer I find it hard to see
what I can truly make money off flipping in eBay"*. The first cut led with
the guide total and listed the dearest items - the GROSS, twice. Nobody buys
a collection for its guide value; you buy it for what the pieces that will
actually sell put in your pocket after eBay's cut and the postage. So:

  * Every money number on the item lines is the eBay NET - guide price run
    through the same `fees.net_proceeds` the CLI and the web app use, minus
    a flat postage estimate per unit, floored at zero (a $3.61 common after a
    $5 label is not a negative flip, it is a thing you bundle or bin).
  * The card leads with ONE number: the most to offer the seller so that the
    FAST MOVERS ALONE pay it back at the analyzer's default goal. Slow stock
    is upside you did not pay for, never something the offer counts on.
  * The verdict is a word - FLIP / PASS / unproven - and the batch is
    posted flips first, so the one worth an email is the one on top.

This is NOT PriceCharting's "Est. Buy Value" by another route. Theirs is a
flat 40-60% of the guide total applied blind to every product in the lot
(the blanket-comp shape, see the top of this file). Ours is summed from
items that were each measured to sell, at what each nets. The 40-60% band is
quoted on the card for one reason only: it is what THEIR sellers expect, so
an offer far under it is going to be turned down, and the card says so
rather than letting you write the email.

SHIPPING
--------
Their seller flow ends at "6 - Ship Items & Receive Payment", and the FAQ says
"generally buyers will require shipment and delivery before payment". Shipping
is the norm and the seller ships first, so **distance is not a gate** and the
whole national feed is in play. What distance does decide is the big ones:
PriceCharting itself recommends meeting in person for large collections, and
nobody mails 6,038 items. So the card flags WEIGHT by item count, not by
drive time.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import re
import statistics
from dataclasses import dataclass, field
from typing import Optional

import requests

from .analyzer import Thresholds
from .fees import FeeModel, net_proceeds
from .sportscards import _rows_in as _sale_rows

FEED_URL = "https://www.pricecharting.com/collections-for-sale"
_HOST = "https://www.pricecharting.com"
_TIMEOUT = 25
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}

# How many product pages one collection is allowed to fetch. Each is ~400KB and
# the value number does NOT depend on them (see the docstring), so this buys
# liquidity on the items that carry the money and nothing else. Items are taken
# highest-value first, and `Summary.unmeasured` says how many were left out -
# a silent cap reads as "that was everything".
ITEM_FETCH_CAP = 25

# Anything selling this often is money you can actually realise; anything under
# `DEAD_PER_90` has not got a market, whatever its guide price says. Chosen off
# the measured split rather than tuned: the three consoles ran 30 sales in
# under a month, HardBall III managed one in ninety days.
LIQUID_PER_90 = 8
DEAD_PER_90 = 2

# Postage per UNIT, a flat estimate the card states out loud: a loose cart or
# a slab in a bubble mailer is ~$5 USPS Ground Advantage. A console is three
# times that and a card in a plain envelope a fifth of it, so this is a knob
# (`summarize(ship=...)`), not a fact - and every net on the card is "after
# fees and ~$5 postage each" so the reader can re-do the sum for a console.
SHIP_PER_UNIT = 5.0
# Below this there is no email worth writing. The feed will not list a lot
# under $100, and a seller who put 20+ items up is not taking a $20 offer -
# so an offer under this floor is rendered as PASS, not as a number to send.
MIN_OFFER = 50.0

# What is worth a card at all. Measured against the live feed 2026-08-23: of 50
# open collections, 17 are under a fortnight old and 16 of those clear $300.
# The floor barely bites (PriceCharting will not accept a listing under $100 or
# 20 items) - AGE is the real filter, because a collection sitting unsold for
# three years has already been passed on by every retail buyer on their list.
MIN_VALUE = 300.0
MAX_AGE_DAYS = 14
# 🚨 AND A BACKLOG IS NOT NEWS. The first run sees all 16 at once; posting them
# together buries the one that matters and costs 190 product fetches. Highest
# value first, a few per run, and the header says how many are still queued.
POST_PER_RUN = 3

_AGE = re.compile(r"(\d+)\s*(hour|day|week|month|year)", re.I)
_AGE_DAYS = {"hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def age_days(age: str) -> float:
    """PriceCharting prints "10 hours" / "8 days" / "2 years", never a date.

    Unparseable reads as ANCIENT, not as fresh: the failure that costs money
    here is treating a three-year-old listing as new, and a wording we have
    never seen is far more likely to be "13 months" than "13 minutes".
    """
    m = _AGE.search(age or "")
    if not m:
        return 9e9
    return int(m.group(1)) * _AGE_DAYS[m.group(2).lower()]


# --- condition ---------------------------------------------------------------
# 🚨 THE SALES TABS ARE CONDITIONS HERE, NOT GRADES. `sportscards._SALES_CLASS`
# maps a card grade onto these same slugs ("7" -> cib, "8" -> new), which is
# right for a slab and nonsense for a cartridge - it would print "Grade 7" over
# rows that mean "complete in box". Games get their own map and their own
# labels, so the card never says something false about what it is quoting.
# 🚨 THIS MAP WAS FIRST WRITTEN BY GUESSING AT THE WORDING, AND FOUR OF ITS
# SEVEN ROWS WERE WRONG. Surveyed 2026-08-23 across 14 live collections - this
# is the ENTIRE vocabulary the column actually uses, with counts:
#
#     170  Ungraded                    <- a CARD, raw. Was labelled "Loose".
#     167  Item only
#      32  Item, Box, and Manual       <- CIB. Was read as loose (undervalues).
#       5  Item and Box only           <- was read as BOX ONLY (wrong tab).
#       3  Ungraded Qty: 2             <- quantity was ignored entirely.
#       2  Graded 9                    <- 🚨 was priced against RAW sales.
#       2  Graded 9.5
#       1  Graded 7
#       1  New Item, Box, and Manual
#
# ⛔ "Item, Box, and Manual" is the comma bug for the FOURTH time (Singer
# "includes case, pedal and manual", the _BUNDLED constant). `box\s*and\s*
# manual` cannot reach "and" through ", ". Any pattern joining two words must
# allow a comma between them.
#
# 🚨 AND THE GRADED ROWS WERE THE MONEY BUG. "Graded 9" fell through to the
# raw-card tab, quoting a slab against ungraded sales - the same shape as the
# blanket $92 any-PSA-7-9 comp, arrived at from the opposite direction.
_QTY = re.compile(r"\bqty:\s*(\d+)", re.I)
_GRADED = re.compile(r"\bgraded\s*(\d+(?:\.\d)?)", re.I)
_UNGRADED = re.compile(r"\bungraded\b", re.I)

# Games, most specific wording first - the compound forms have to be tested
# before the bare ones, because "Item and Box only" CONTAINS "Box only".
_GAME_CONDITION = (
    (re.compile(r"\bnew\b|sealed|\bnib\b", re.I), "new", "New"),
    (re.compile(r"item\s*(,|and|&)\s*box\s*only", re.I),
     "loose-and-box", "Item + box"),
    (re.compile(r"item\s*(,|and|&)\s*manual\s*only", re.I),
     "loose-and-manual", "Item + manual"),
    (re.compile(r"\bcib\b|complete|box\s*(,\s*)?(and|&|\+)\s*manual", re.I),
     "cib", "Complete"),
    (re.compile(r"box\s*only", re.I), "box-only", "Box only"),
    (re.compile(r"manual\s*only", re.I), "manual-only", "Manual only"),
    (re.compile(r"item\s*only|loose|cart(ridge)?\s*only|disc\s*only", re.I),
     "used", "Loose"),
)

# 🚨 THE SITE REUSES THE GAME SLUGS FOR CARD GRADES, AND ONLY THE PAGE KNOWS
# THE MAPPING. On a card page `completed-auctions-cib` is labelled "Grade 7"
# and `manual-only` is "PSA 10" - nothing in the slug says so. `_tabs()` reads
# the mapping off the page's own <option> list so it CANNOT drift; this table
# is only the fallback for a page whose tab list we failed to parse.
_GRADE_SLUG = {"1": "loose-and-manual", "2": "box-and-manual",
               "3": "grade-three", "4": "grade-four", "5": "grade-five",
               "6": "grade-six", "7": "cib", "8": "new", "9": "graded",
               "9.5": "box-only", "10": "manual-only"}

_TAB = re.compile(r'<option[^>]*value="completed-auctions-?([a-z0-9-]*)"[^>]*>'
                  r'(.*?)</option>', re.S | re.I)


def tabs(page: str) -> dict:
    """{human label -> sales-table slug}, read off the page's own tab list.

    "Ungraded" -> used · "Grade 9" -> graded · "PSA 10" -> manual-only. Empty
    when the list is missing, which is the caller's cue to fall back.
    """
    out = {}
    for slug, label in _TAB.findall(page):
        name = _html.unescape(re.sub(r"<[^>]+>", "", label))
        name = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()   # drop the count
        if name:
            out[name.lower()] = slug or "used"
    return out


def quantity(includes: str) -> int:
    """🚨 THE VALUE COLUMN IS GUIDE x QTY, NOT A UNIT PRICE. Measured: a
    "Qty: 5" Gamecube controller carries $149.95 against a $29.99 sold median -
    exactly 5x. Ignoring it makes one row look like a $150 item and makes its
    sell-through look five times better than it is."""
    m = _QTY.search(includes or "")
    return max(1, int(m.group(1))) if m else 1


def condition_of(includes: str, page_tabs: Optional[dict] = None) -> tuple:
    """(sales-table slug, human label) for what a collection row says it has.

    `page_tabs` is the product page's own label->slug map when we have it, so a
    graded card is quoted against ITS grade rather than a slug we guessed.
    Unknown wording falls back to loose/raw - the cheapest reading, so a phrase
    we have never seen under-values rather than over.
    """
    text = _QTY.sub(" ", includes or "")
    tabs_ = page_tabs or {}

    g = _GRADED.search(text)
    if g:
        num = g.group(1).rstrip("0").rstrip(".") if "." in g.group(1) \
            else g.group(1)
        # 10 has a grader-specific tab per company; PSA is the deepest market
        # and the only one with rows on most cards, so it is the 10 we quote.
        wanted = "psa 10" if num == "10" else f"grade {num}"
        if wanted in tabs_:
            return tabs_[wanted], wanted.upper() if num == "10" \
                else f"Grade {num}"
        return _GRADE_SLUG.get(num, "used"), f"Grade {num}"

    if _UNGRADED.search(text):
        # 🚨 A RAW CARD IS NOT "LOOSE". Same tab, different word, and printing
        # a cartridge word over a card is how a card ends up read as a game.
        return tabs_.get("ungraded", "used"), "Ungraded"

    for rx, slug, label in _GAME_CONDITION:
        if rx.search(text):
            return slug, label
    return "used", "Loose"


# --- the feed ----------------------------------------------------------------
@dataclass(frozen=True)
class Collection:
    seller: str
    url: str
    total_value: float
    item_count: int
    location: str
    age: str
    photo: Optional[str] = None

    @property
    def heavy(self) -> bool:
        """Too many items to post. Nobody mails 463 cartridges casually - these
        are the ones PriceCharting itself says to go and collect in person."""
        return self.item_count >= 250


_ROW_TEXT = re.compile(
    r"\$([\d,]+\.\d\d)\s+(\d+)\s+(.*?)\s+Age:\s*(.*)$")


def _text(fragment: str) -> str:
    return _html.unescape(re.sub(r"\s+", " ",
                                 re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_feed(page: str) -> list:
    """Every collection currently listed for sale, newest-value order as given."""
    if "<tbody>" not in page:
        return []
    body = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        m = _ROW_TEXT.match(_text(row))
        if not m:
            continue
        href = re.search(r'href="([^"]+)"', row)
        img = re.search(r'<img src="([^"]+)"', row)
        if not href:
            continue
        seller = re.search(r"seller=([^&\"]+)", href.group(1))
        out.append(Collection(
            seller=seller.group(1) if seller else href.group(1),
            url=_HOST + href.group(1),
            total_value=float(m.group(1).replace(",", "")),
            item_count=int(m.group(2)),
            location=m.group(3),
            age=m.group(4),
            photo=img.group(1) if img else None,
        ))
    return out


def feed(session=None) -> list:
    """Live collections for sale. [] means "could not ask", never "none listed"."""
    try:
        r = (session or requests).get(FEED_URL, headers=_UA, timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        return parse_feed(r.text)
    except Exception:
        return []


# --- one collection's contents ----------------------------------------------
@dataclass
class Item:
    name: str
    includes: str
    value: float
    game_id: Optional[str] = None
    # Filled by `measure`. `sales` is [(iso date, price)], newest first.
    sales: list = field(default_factory=list)
    label: str = "Loose"
    # 🚨 "COULD NOT ASK" IS NOT "DOES NOT SELL". Seen live 2026-08-23: the same
    # collection reported real sale rates on one run and "No sale history
    # found" on the next, because `measure` swallowed a throttled fetch and an
    # empty `sales` list reads identically either way. sportscards._get already
    # carries this rule for the API ("None means could not ask"); the card has
    # to carry it too, or a rate limit renders as a dead market.
    unreachable: bool = False

    @property
    def product_url(self) -> Optional[str]:
        return f"{_HOST}/game/{self.game_id}" if self.game_id else None

    @property
    def last_sold(self) -> Optional[str]:
        return self.sales[0][0] if self.sales else None

    @property
    def low(self) -> Optional[float]:
        return min(p for _, p in self.sales) if self.sales else None

    @property
    def high(self) -> Optional[float]:
        return max(p for _, p in self.sales) if self.sales else None

    @property
    def qty(self) -> int:
        return quantity(self.includes)

    @property
    def unit_value(self) -> float:
        """The guide price of ONE, because `value` is guide x qty."""
        return self.value / self.qty

    def net(self, fees: FeeModel = FeeModel(),
            ship: float = SHIP_PER_UNIT) -> float:
        """What the whole row puts in your pocket sold on eBay one unit at a
        time: qty x (unit guide price - eBay fees - postage), floored at zero.

        🚨 GUIDE IS WHAT THE BUYER PAYS, NOT WHAT YOU KEEP. A $114.50 SNES is
        $93.93 after 13.25% + $0.40 and a $5 label; a $3.61 common is $0.00,
        not -$1.87 - you would never ship it alone, so it counts for nothing
        rather than for less than nothing.
        """
        per_unit = net_proceeds(self.unit_value, fees=fees,
                                shipping_cost=ship).net
        return round(max(0.0, per_unit) * self.qty, 2)

    def per_90(self, today: _dt.date) -> Optional[int]:
        """How many of these actually sold in the last 90 days.

        🚨 CAPPED SAMPLES CANNOT BE READ AS A RATE WITHOUT SAYING SO. The page
        gives the 30 most recent sales and no more, so an item whose 30 sales
        all landed inside the window returns 30 and means "at least 30" - which
        is why the card prints ">=" once the sample is exhausted.
        """
        if not self.sales:
            return None
        return sum(1 for d, _ in self.sales
                   if (today - _dt.date.fromisoformat(d)).days <= 90)

    def saturated(self, today: _dt.date) -> bool:
        n = self.per_90(today)
        return bool(n is not None and self.sales and n == len(self.sales))


def parse_items(page: str) -> list:
    """The itemised contents of one collection.

    🚨 THIS IS THE HALF THE EMAIL DOES NOT HAVE. A mail says "64 items,
    $1,252.03"; the page says WHICH 64, each with its console, its condition and
    a `/game/<id>` that joins straight to the sold history.

    ⛔ ANONYMOUS ACCESS SEES 30 ITEMS, whatever the count says. The header table
    still gives the true total, so `Summary` reports against the collection's
    real value and never pretends 30 was all of it.
    """
    tables = re.findall(r"<table[^>]*>(.*?)</table>", page, re.S)
    if len(tables) < 2:
        return []
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tables[1], re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 4:
            continue
        price = re.search(r"\$([\d,]+\.\d\d)", _text(cells[3]))
        if not price:
            continue
        gid = re.search(r"/game/(\d+)", row)
        includes = _text(cells[2])
        _cls, label = condition_of(includes)
        out.append(Item(name=_text(cells[1]), includes=includes,
                        value=float(price.group(1).replace(",", "")),
                        game_id=gid.group(1) if gid else None, label=label))
    return out


def items_of(collection: Collection, session=None) -> list:
    try:
        r = (session or requests).get(collection.url, headers=_UA,
                                      timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        return parse_items(r.text)
    except Exception:
        return []


# --- liquidity ---------------------------------------------------------------
def measure(items: list, session=None, cap: int = ITEM_FETCH_CAP) -> int:
    """Attach completed-sale history to the highest-value items, in place.

    Returns how many were left unmeasured, so the caller can say so out loud.
    Fail-soft per item: one dead product page never costs the other 24.
    """
    ranked = sorted(items, key=lambda i: -i.value)
    for item in ranked[:cap]:
        if not item.product_url:
            continue
        try:
            r = (session or requests).get(item.product_url, headers=_UA,
                                          timeout=_TIMEOUT)
            if r.status_code != 200:
                item.unreachable = True
                continue
            # 🚨 RESOLVE THE TAB AGAINST THE PAGE, NOT AGAINST A GUESS. The
            # slug for "Grade 9" is `graded` on a card and means something else
            # entirely on a game; only the page says which.
            page_tabs = tabs(r.text)
            cls, label = condition_of(item.includes, page_tabs)
            item.label = label
            rows = _sale_rows(r.text, cls)
            # 🚨 THE CONDITION DEGRADES, IT DOES NOT VANISH - the same rule the
            # card scraper learned about grades. A box-only listing with no
            # box-only sales is not unpriceable; the loose rows still say what
            # the market is doing, as long as the card admits it fell back.
            if not rows and cls != "used":
                rows = _sale_rows(r.text, "used")
                if rows:
                    # Name the tab we actually READ, never the one we wanted.
                    fell_to = "ungraded" if _UNGRADED.search(item.includes or "") \
                        or _GRADED.search(item.includes or "") else "loose"
                    item.label = f"{label} ({fell_to} sales)"
            item.sales = rows
        except Exception:
            item.unreachable = True
            continue
    return max(0, len(ranked) - cap)


def bucket(item: Item, today: _dt.date) -> Optional[str]:
    """"fast" | "slow" | "mid" | None (no history). One rule, used by the
    summary AND the card, so the lines and the totals can never disagree.

    🚨 QTY SCALES THE BAR, NOT THE RATE. Five copies of a thing that sells
    eight times a quarter is not five times as liquid - you need five sales
    to clear the position, so the threshold multiplies.
    """
    n = item.per_90(today)
    if n is None:
        return None
    if n >= LIQUID_PER_90 * item.qty:
        return "fast"
    if n <= DEAD_PER_90 * item.qty:
        return "slow"
    return "mid"


def offer_for(liquid_net: float,
              thresholds: Thresholds = Thresholds()) -> float:
    """The most to pay the seller so the fast movers ALONE return the goal.

    With N = what the fast movers net on eBay and the offer the cash tied up:
        N - offer >= min_profit          ->  offer <= N - min_profit
        (N - offer) / offer >= min_roi   ->  offer <= N / (1 + min_roi)
    Tighter one wins; never below zero. Same shape as `analyzer.max_pay`,
    summed over a lot instead of solved for one item.
    """
    if liquid_net <= 0:
        return 0.0
    cap = min(liquid_net - thresholds.min_profit,
              liquid_net / (1 + thresholds.min_roi))
    return round(max(0.0, cap), 2)


@dataclass(frozen=True)
class Summary:
    total_value: float          # what the collection is worth, per the guide
    measured_value: float       # the part we have sale history for
    liquid_value: float         # sells >= LIQUID_PER_90 times a quarter
    dead_value: float           # sells <= DEAD_PER_90 times a quarter
    liquid_items: int
    dead_items: int
    unmeasured: int             # items we did not fetch (the cap)
    unlisted: int               # items the page never showed us (the 30 cap)
    unreachable: int            # items whose product page would not load
    liquid_net: float = 0.0     # what the fast movers NET on eBay, all in
    offer: float = 0.0          # most to offer so the fast movers pay it back

    @property
    def verdict(self) -> str:
        """"flip" | "pass" | "unproven" - the one word that decides the email.

        Unproven beats everything: a lot whose priced share is thin, or that
        we could not price at all, gets no offer from us however good the
        slice we saw looked (see `coverage`).
        """
        if not self.measured_value or self.thin:
            return "unproven"
        return "flip" if self.offer >= MIN_OFFER else "pass"

    @property
    def coverage(self) -> float:
        """Share of the collection's VALUE we have sale history for.

        🚨 THIS IS THE NUMBER THAT SAYS WHETHER THE REST OF THE CARD MEANS
        ANYTHING, and it is not `measured / len(items)`. The public page shows
        thirty rows and they are not the dearest thirty: a $4,533.18 collection
        on the live feed exposed thirty Pokemon commons totalling under $21.
        Counting items would have called that 55% covered; counting money calls
        it 0%, which is the truth.
        """
        if not self.total_value:
            return 0.0
        return min(1.0, self.measured_value / self.total_value)

    @property
    def thin(self) -> bool:
        """Too little of the money priced for the liquidity read to carry."""
        return self.coverage < 0.25


def summarize(collection: Collection, items: list, unmeasured: int,
              today: Optional[_dt.date] = None,
              fees: FeeModel = FeeModel(), ship: float = SHIP_PER_UNIT,
              thresholds: Thresholds = Thresholds()) -> Summary:
    today = today or _dt.date.today()
    liquid = dead = measured = liquid_net = 0.0
    nl = nd = 0
    for it in items:
        b = bucket(it, today)
        if b is None:
            continue
        measured += it.value
        if b == "fast":
            liquid += it.value
            liquid_net += it.net(fees, ship)
            nl += 1
        elif b == "slow":
            dead += it.value
            nd += 1
    return Summary(
        total_value=collection.total_value,
        measured_value=round(measured, 2),
        liquid_value=round(liquid, 2), dead_value=round(dead, 2),
        liquid_items=nl, dead_items=nd, unmeasured=unmeasured,
        unlisted=max(0, collection.item_count - len(items)),
        unreachable=sum(1 for i in items if i.unreachable),
        liquid_net=round(liquid_net, 2),
        offer=offer_for(liquid_net, thresholds),
    )


# --- the card ----------------------------------------------------------------
_VERDICT_WORD = {"flip": "FLIP", "pass": "PASS", "unproven": "Collection"}
# 🚨 notify.build_embed CUTS `reason` AT 1000 CHARACTERS, SILENTLY. Measured
# on the first draft of this card: two fast movers and two slow ones ran to
# 1,056, and what fell off the end was the ownership warning. So the card
# fits itself: the fast-mover lines give way, one at a time, and say how
# many they dropped. Nothing else on the card is optional.
FIELD_CAP = 1000
# Discord embed colour, via notify.VERDICT_COLORS: green for the one worth an
# email, red for the one that is not, grey for the one we could not price.
_VERDICT_COLOUR = {"flip": "buy", "pass": "pass", "unproven": "watch"}
# What PriceCharting's own FAQ says their sellers "generally get". Quoted so
# an offer far under it is called out as a long shot - NOT used to price
# anything (see the module docstring).
SELLER_BAND = (0.40, 0.60)


def _line(item: Item, today: _dt.date, fees: FeeModel = FeeModel(),
          ship: float = SHIP_PER_UNIT) -> str:
    n = item.per_90(today)
    if n is None:
        return f"`${item.value:>7,.2f}`  {item.name}"
    rate = f"{'>=' if item.saturated(today) else ''}{n}/90d"
    rng = (f" ${item.low:,.0f}-{item.high:,.0f}"
           if item.low is not None and item.high != item.low else "")
    # 🚨 SAY THE QUANTITY AND THE CONDITION. Without qty the value column
    # lies (it is guide x qty); without the condition a Grade 9 slab and a raw
    # card print the same line against very different sales.
    qty = f" x{item.qty}" if item.qty > 1 else ""
    # The NET leads: it is the number you would actually bank. The guide
    # price rides behind it so the fee bite is visible, not hidden. Every
    # character here is paid for out of Discord's 1000-char field (see
    # FIELD_CAP), so the date drops its year and the name is capped.
    return (f"`${item.net(fees, ship):>7,.2f}` net{qty}  {item.name[:40]} "
            f"[{item.label}] — {rate}, last {item.last_sold[5:]} · guide "
            f"${item.value:,.2f}{rng}")


def _slow_line(slow: list, shown: int = 3) -> str:
    """One line for everything that does not move, so it cannot be mistaken
    for money. Named, because "3 slow items" invites "which three?"."""
    names = [f"{i.name} ${i.value:,.0f}" for i in slow[:shown]]
    more = f" +{len(slow) - shown} more" if len(slow) > shown else ""
    return (f":hourglass: **Slow, pay nothing for these:** "
            f"{', '.join(names)}{more}")


def to_alert(collection: Collection, items: list, summary: Summary,
             today: Optional[_dt.date] = None, top: int = 8,
             fees: FeeModel = FeeModel(), ship: float = SHIP_PER_UNIT,
             thresholds: Thresholds = Thresholds()) -> dict:
    """One collection -> the Discord embed payload.

    In the order they change the decision: what to offer (or why not), what
    the lot is worth and how much of that moves, the items carrying the
    money at what they NET, the slow stock named so it is never counted,
    and what would stop you.
    """
    today = today or _dt.date.today()
    verdict = summary.verdict
    fast = sorted([i for i in items if bucket(i, today) == "fast"],
                  key=lambda i: -i.net(fees, ship))
    mid = [i for i in items if bucket(i, today) == "mid"]
    slow = sorted([i for i in items if bucket(i, today) == "slow"],
                  key=lambda i: -i.value)

    bits = [f"**${collection.total_value:,.2f}** across "
            f"**{collection.item_count}** items · _{collection.location}_ · "
            f"listed {collection.age}"]

    if summary.measured_value:
        nf = summary.liquid_items
        movers = f"{nf} fast mover{'s' if nf != 1 else ''}"
        if verdict == "flip":
            share = summary.offer / summary.total_value
            bits.append(
                f":moneybag: **Offer up to ${summary.offer:,.2f}** "
                f"({share:.0%} of guide) — {movers} net "
                f"**${summary.liquid_net:,.2f}** after eBay fees + ~${ship:.0f} "
                f"postage each; that pays the offer back at "
                f"{thresholds.min_roi:.0%}+ ROI. Slow stock is free upside.")
            if share < SELLER_BAND[0]:
                bits.append(
                    f"_Under the {SELLER_BAND[0]:.0%}-{SELLER_BAND[1]:.0%} of "
                    f"guide sellers here usually take - expect a no or a "
                    f"counter._")
        elif verdict == "pass":
            why = (f"{movers} net only ${summary.liquid_net:,.2f} after eBay "
                   f"fees" if nf else
                   "nothing we priced sells fast enough to pay an offer back")
            bits.append(f":no_entry_sign: **Pass** — {why}. No offer worth "
                        f"emailing; the guide total is mostly slow stock.")
        else:   # unproven, i.e. thin: the coverage warning below carries it
            bits.append(f":grey_question: **Unproven** — only "
                        f"{summary.coverage:.0%} of the value is priced, so "
                        f"no offer from this card.")
        # 🚨 THE DENOMINATOR IS THE COLLECTION, NOT OUR SAMPLE. The first cut
        # said "71% of what we measured", which sounds like coverage and is
        # not. Caught on the live sweep: a $4,533.18 collection whose thirty
        # PUBLIC rows were $0.74-$3.61 Pokemon commons reported "$10.54 moves
        # quarterly (64% of what we measured)" - a confident-looking read of
        # four tenths of one percent of the money. The page does NOT show the
        # dearest thirty items, so a sample is never a summary here.
        item = "item" if summary.liquid_items == 1 else "items"
        bits.append(
            f":dart: **${summary.liquid_value:,.2f} guide moves quarterly** "
            f"({summary.liquid_items} {item})"
            + (f" · :hourglass: **${summary.dead_value:,.2f} is slow** "
               f"({summary.dead_items} items)" if summary.dead_items else ""))
        bits.append(f"_Priced {summary.coverage:.0%} of the collection's "
                    f"${summary.total_value:,.2f}._")
    elif any(i.unreachable for i in items):
        # 🚨 SAY WHICH SILENCE THIS IS. "No sales" is a verdict on the market;
        # "could not reach the page" is a verdict on our run, and reading the
        # second as the first turns a rate limit into a dead collection.
        bits.append("_Could not reach PriceCharting for these items - no "
                    "liquidity read this run, and that is OUR gap, not the "
                    "market's._")
    else:
        bits.append("_No sale history found - treat every number as unproven._")

    # 🚨 ONLY THE FAST MOVERS GET A LINE. The old card listed the dearest
    # eight by guide price, which put a $65 HardBall III (one sale in 90
    # days) above a $30 controller that sells weekly - the noise Leron was
    # reading past. Money first, slow stock in one line, the middle counted.
    lines = [_line(i, today, fees, ship) for i in fast[:top]]
    tail = []
    if slow:
        tail.append("")
        tail.append(_slow_line(slow))
    if mid:
        tail.append(f"_{len(mid)} more sell at a middling pace "
                    f"(${sum(i.value for i in mid):,.2f} guide) - not counted "
                    f"in the offer._")

    # Only what would stop the buy.
    warn = []
    if summary.measured_value and summary.thin:
        # First in the list on purpose: it governs how to read everything above
        # it, so it cannot sit under two other warnings.
        warn.append(f"**Only {summary.coverage:.0%} of the value is priced** - "
                    f"the numbers above do not describe this collection")
    if summary.unlisted:
        warn.append(f"**{summary.unlisted} of {collection.item_count} items "
                    f"are not shown** on the public page")
    if summary.unreachable:
        warn.append(f"{summary.unreachable} product page(s) unreachable")
    if summary.unmeasured:
        warn.append(f"{summary.unmeasured} lower-value items unmeasured")
    if collection.heavy:
        warn.append("**Big lot** - likely a pickup, not a shipment")
    if warn:
        bits.append("")
        bits.append(":warning: " + " · ".join(warn))
    # 🚨 STANDING LINE, EVERY CARD. PriceCharting's own page says to verify
    # ownership before paying, and their flow has the SELLER ship first - which
    # is the half of the risk that lands on them, not on you.
    tail.append("_Ask for a photo with the seller's username written on paper "
                "before sending money._")

    # Fit the field: drop fast-mover lines from the bottom until it does,
    # and say how many went. The head and the tail are never cut.
    def _body(k: int) -> str:
        shown = lines[:k]
        if shown:
            shown = [""] + shown
            if len(fast) > k:
                shown.append(f"_+{len(fast) - k} more fast movers_")
        return "\n".join(bits + shown + tail)
    k = len(lines)
    while k > 0 and len(_body(k)) > FIELD_CAP:
        k -= 1

    title = (f"{_VERDICT_WORD[verdict]} · {collection.item_count} items · "
             f"${collection.total_value:,.2f}")
    if verdict == "flip":
        # The offer in the title too: on a phone the title is what you see
        # in the channel list, and it is the only number that matters.
        title += f" · offer up to ${summary.offer:,.0f}"
    return {
        "title": title,
        "url": collection.url,
        "image": collection.photo,
        "verdict": _VERDICT_COLOUR[verdict],
        "source": "pricecharting",
        "buy_url": collection.url,
        "category": "collections",
        # 🚨 DELIBERATELY NO max_bid / open_bid / ends. There is no lot and no
        # clock, and `build_embed` would render an arming grid for a listing
        # that cannot be armed. The prose avoids the trigger words for the same
        # reason - see the module docstring. "Offer up to" is not one of them
        # and the guard test runs the real parser over the rendered embed.
        "listing_type": "offer",
        "reason": _body(k),
    }


def key(collection: Collection) -> str:
    return f"pricecharting-collection:{collection.seller}"

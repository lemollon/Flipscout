"""Turn an auction house's free-text fee blurb into a number.

WHY THIS EXISTS
---------------
Every HiBid lot carries a buyer's premium that is NOT in the bid: 10-20% of the
hammer, plus a card surcharge, added at checkout. Until this module existed
Flipscout priced HiBid lots as if the hammer were the whole cost, so all 330
HiBid cards on the board overstated profit by roughly a fifth. On a $200 hammer
at 20% + 4% card that is $48 of invisible cost - larger than the target profit
on most of these lots.

🚨 THE PREMIUM IS A PERCENTAGE OF THE BID, NOT A FLAT COST. It cannot be added
to handling and subtracted; it scales with whatever you end up bidding, so the
ceiling has to DIVIDE by (1 + rate). See bidding.advise.

🚨 THERE IS NO STRUCTURED FIELD. `buyerPremium` is a text box the auctioneer
types into, and 290 distinct spellings turned up in a 457-auction sample. This
module is a parser for human prose, so it is written defensively and every rule
below comes from a string that really appeared.

THE RULE THIS ENCODES: price what Leron actually pays, which is BY CARD.
  * "15% (3% DISCOUNT FOR CASH)"      -> 15%   discounts are for cash; ignore
  * "12% Cash / 17% CC"               -> 17%   alternatives -> take the card one
  * "10% Buyer's Premium + 4% for card" -> 14%  genuine surcharge -> add
Reading the cash rate off a card-paying purchase is the expensive mistake, so
where the prose is ambiguous this rounds AGAINST the deal.
"""

from __future__ import annotations

import re
import os
from typing import Optional

# Applied when the blurb says a premium exists but never says how much
# ("Buyers Premium", "Convenience Fee") or is blank/unparseable. Grounded in the
# measured distribution - see tests. NEVER default to zero: 15% of auctions land
# here, and a silent 0% would quietly restore the very bug this module fixes.
DEFAULT_PREMIUM = 0.15

# Above this a parse is a misread, not a real premium (the highest genuine rate
# in the sample was 25%). Falls back to DEFAULT_PREMIUM rather than trusting it.
_SANE_MAX = 0.30

# Below this a nonzero parse is a misread rather than a real premium - see
# parse_premium. An explicit zero is handled separately and is still believed.
_SANE_MIN = 0.01

# Clauses that REDUCE the price if you pay cash. Leron pays by card, so these
# never apply - and they must be cut out before any percentage is read, or
# "15% (3% DISCOUNT FOR CASH)" parses as 3%.
_DISCOUNT = re.compile(
    r"[-(,&]?\s*\d+(?:\.\d+)?\s*%?\s*(?:\w+\s+){0,3}?discount[^.;]*"
    r"|discount[^.;]*?\d+(?:\.\d+)?\s*%[^.;]*", re.I)

# A percentage, however it is spelled: "15%", "%15", "18 percent".
_PCT = re.compile(r"%\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.I)

_CARD = re.compile(r"credit\s*card|card|\bcc\b|visa|mastercard", re.I)
# "+ 3% card fee" / "& 4% CC surcharge" / "Add 3% for Credit Cards"
_ADDITIVE = re.compile(r"[+&]|\bplus\b|\badd(?:ed|s|itional)?\b|\band\b|\bextra\b"
                       r"|\bsurcharge\b|\bprocessing\b|\bfee\b", re.I)
# A rate sitting next to "cash" or "check" is the CASH ALTERNATIVE, never a
# surcharge to add on. Without this "15% Cards & 10% Cash." reads as 25%.
_CASH = re.compile(r"\bcash\b|\bcheck\b", re.I)
_NO_PREMIUM = re.compile(r"\bno\s+buyer'?s?\s+premium|\bnone\b", re.I)
# A premium is named but no figure given -> unknown, not absent.
_NAMED = re.compile(r"premium|\bbp\b|\bfee\b|commission|charge|expenses", re.I)


def _pcts(text: str) -> list:
    out = []
    for m in _PCT.finditer(text):
        raw = m.group(1) or m.group(2)
        try:
            out.append((float(raw), m.start()))
        except ValueError:
            pass
    return out


def premium_is_stated(text: Optional[str]) -> bool:
    """True when the house actually gave a figure we could read.

    Drives the hedge on the alert: a rate we PARSED is a fact, a rate we
    defaulted to is a guess, and a card that presents the two identically
    teaches you to trust both equally.
    """
    s = (text or "").strip()
    if not s:
        return False
    if _NO_PREMIUM.search(s):
        return True
    body = _DISCOUNT.sub(" ", s)
    if _pcts(body):
        return True
    bare = re.search(r"(?<![\d.$])(\d{1,2}(?:\.\d+)?)(?![\d.%])", body)
    return bool(bare and _NAMED.search(body) and 0 < float(bare.group(1)) / 100.0
                <= _SANE_MAX)


def parse_premium(text: Optional[str]) -> float:
    """Buyer's premium as a fraction of the hammer, for a card payment.

    Returns DEFAULT_PREMIUM when the text names a premium but no figure, is
    blank, or parses to something implausible. Only an explicit zero
    ("No Buyer's Premium", "0%") returns 0.0.
    """
    s = (text or "").strip()
    if not s:
        return DEFAULT_PREMIUM

    body = _DISCOUNT.sub(" ", s)          # cash discounts never apply to us
    found = _pcts(body)

    # 🚨 "NO BUYERS PREMIUM" ONLY COUNTS IF NOTHING ELSE IS STATED.
    #
    # "no buyers premium for cash, 18% for cards" used to return 0% - the
    # zero-check ran first and short-circuited the whole parser. That is the
    # cash-versus-card split this module exists to resolve, and getting it
    # backwards sets the ceiling 18% too high on a real auction.
    #
    # A free waiver plus a card rate means the card rate, because that is what
    # Leron pays.
    if _NO_PREMIUM.search(s) and not [v for v, _ in found if v > 0]:
        return 0.0

    if not found:
        # "18 percent bp" is caught above; this is for "15", "Buyers premium-16"
        bare = re.search(r"(?<![\d.$])(\d{1,2}(?:\.\d+)?)(?![\d.%])", body)
        if bare and _NAMED.search(body):
            v = float(bare.group(1)) / 100.0
            # Same floor as the percent path: "Buyer's Premium: 0.15" is a
            # FRACTION typed as a decimal, and 0.15% is functionally free.
            return v if _SANE_MIN <= v <= _SANE_MAX else DEFAULT_PREMIUM
        # 🚨 A bare "0.00" or "0" is NOT a promise of no premium - it is an
        # empty box with a default in it. A real HiBid auction on 2026-08-18
        # ("Playstation 5 + 2 Controllers") carried buyerPremium='0.00' while
        # every comparable house charged 15-20%. Only prose that actually
        # STATES a zero ("No Buyer's Premium", "0% Buyer's Premium") is
        # believed; anything else falls back.
        return DEFAULT_PREMIUM

    rates = [v / 100.0 for v, _ in found]
    if len(rates) == 1:
        v = rates[0]
        if v == 0.0:
            return 0.0                     # "0% Buyer's Premium"
        # 🚨 A nonzero rate under 1% is a misread, not a bargain. "Buyer's
        # Premium: 0.15" is a FRACTION written as a decimal, and taking it at
        # face value gives 0.15% - functionally free, and a ceiling far too
        # high. No auction house charges half a percent.
        if v < _SANE_MIN:
            return DEFAULT_PREMIUM
        return v if v <= _SANE_MAX else DEFAULT_PREMIUM

    # Two or more figures. Either they are ALTERNATIVES (cash rate vs card rate)
    # or a base plus a genuine card SURCHARGE. Tell them apart by what sits
    # between them, and when in doubt take the dearer reading.
    base, first_at = max(found, key=lambda t: t[0])
    base /= 100.0
    surcharge = 0.0
    for v, at in found:
        if at == first_at or v / 100.0 >= base:
            continue
        near = body[max(0, at - 45):at + 45]
        if _CASH.search(near):
            continue                       # the cash rate, not a card add-on
        # The additive word often trails the figure ("4% CC surcharge",
        # "4% extra"), so look either side of it rather than only in the gap.
        if _CARD.search(near) and _ADDITIVE.search(near):
            surcharge = max(surcharge, v / 100.0)
    total = base + surcharge
    if total < _SANE_MIN or total > _SANE_MAX:
        return DEFAULT_PREMIUM
    return total


def min_increment(amount: float, table: Optional[list]) -> float:
    """The legal bid step at `amount`, from the auction's increment table.

    🚨 HiBid increments are NOT $1. The hunter used to hardcode 1.0, so a lot
    sitting at $250 in a house whose table jumps by $50 was quoted a next bid of
    $251 - not a bid that exists. Tables in the sample stepped as high as $550.
    """
    if not table:
        return 1.0
    top = None
    for row in sorted(table, key=lambda r: float(r.get("upToAmount") or 0)):
        try:
            up = float(row.get("upToAmount") or 0)
            inc = float(row.get("minBidIncrement") or 0)
        except (TypeError, ValueError):
            continue
        if inc <= 0:
            continue
        top = inc                          # last valid band seen, i.e. the largest
        if amount <= up:
            return inc
    # Past the end of the table the step is the TOP band's, never the first
    # one - returning the opening $1 step for a $5,000 lot quotes a bid the
    # site will reject. Real tables end at 9999999.99 so this is the guard for
    # a truncated one.
    return top or 1.0


# --------------------------------------------------------------------------
# SALES TAX
#
# 🚨 IT IS CHARGED ON THE HAMMER *PLUS* THE PREMIUM, so it compounds with it -
# a $200 hammer under a 20% premium and 8.25% tax lands at $259.80, not $248.
# Like the premium it scales with the bid, so the ceiling divides again.
#
# There is no structured field for it either. 217 sampled auctions: only 18
# state a rate in `paymentInfo`, 18 say there is none, and 153 never mention
# it. So the fallback is the AUCTIONEER'S STATE, which is always present.
#
# 🚨 THE TABLE IS COMBINED STATE+LOCAL, NOT THE BASE RATE. Houses charge what
# they actually owe: the sampled Texas auctions say 8.25% where the state base
# is 6.25%, and North Carolina says 6.75% against a 4.75% base. Using base
# rates would understate every ceiling by the local share.
#
# 🚨 IF LERON HAS A RESALE CERTIFICATE none of this applies - he is buying to
# resell, which is exactly what an exemption is for. Set FLIPSCOUT_RESALE_EXEMPT=1
# and every rate below becomes zero. It is OFF by default because assuming an
# exemption he has not filed would understate every cost he has.
# --------------------------------------------------------------------------

RESALE_EXEMPT = os.environ.get("FLIPSCOUT_RESALE_EXEMPT", "").strip().lower() in (
    "1", "true", "yes", "y")

# Applied when the state is unknown and nothing is stated. The sampled median.
DEFAULT_TAX = 0.07

# Combined state + average local rate. Estimates, and deliberately so: a stated
# rate always wins over this table.
STATE_TAX = {
    "AL": .0929, "AK": .0182, "AZ": .0840, "AR": .0945, "CA": .0885, "CO": .0780,
    "CT": .0635, "DE": .0000, "DC": .0600, "FL": .0700, "GA": .0740, "HI": .0450,
    "ID": .0603, "IL": .0886, "IN": .0700, "IA": .0694, "KS": .0870, "KY": .0600,
    "LA": .0956, "ME": .0550, "MD": .0600, "MA": .0625, "MI": .0600, "MN": .0804,
    "MS": .0706, "MO": .0839, "MT": .0000, "NE": .0697, "NV": .0824, "NH": .0000,
    "NJ": .0660, "NM": .0762, "NY": .0853, "NC": .0700, "ND": .0704, "OH": .0724,
    "OK": .0899, "OR": .0000, "PA": .0634, "RI": .0700, "SC": .0750, "SD": .0611,
    "TN": .0955, "TX": .0820, "UT": .0725, "VT": .0636, "VA": .0577, "WA": .0938,
    "WV": .0657, "WI": .0570, "WY": .0544,
    # 🚨 CANADIAN LOTS ARE IN THE FEED. HiBid carries Ontario, PEI and Alberta
    # houses, and without these they fell through to the US default of 7% while
    # really charging 13-15% HST - the largest single mispricing in this table.
    # GST/HST/PST combined.
    "AB": .0500, "BC": .1200, "MB": .1200, "NB": .1500, "NL": .1500,
    "NS": .1400, "NT": .0500, "NU": .0500, "ON": .1300, "PE": .1500,
    "QC": .1498, "SK": .1100, "YT": .0500,
}

# 🚨 A CANADIAN LOT ALSO PRICES IN CAD, and nothing here converts currency, so
# its comp comparison is wrong by the exchange rate on top of the tax. Treat
# Canadian cards as indicative only until that is handled.
CA_PROVINCES = frozenset({"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU",
                          "ON", "PE", "QC", "SK", "YT"})

_NO_TAX = re.compile(r"no\s+sales\s*tax|tax[- ]?exempt|sales\s*tax[^.]{0,25}\bnot\b",
                     re.I)
# 🚨 THE FIGURE SITS ON EITHER SIDE OF THE WORD, and picking the wrong one
# grabs the buyer's premium instead. Both orderings are real:
#     "8.25% Texas sales tax"        -> number BEFORE
#     "Sales Tax : 6.75%"            -> number AFTER
# and "Internet Premium: 15% Sales Tax : 6.75%" contains both, where the first
# percentage is the PREMIUM. So candidates adjacent to premium wording are
# thrown out rather than trusted.
_TAX_WORD = re.compile(r"\b(?:sales\s*)?tax\b", re.I)
_ANY_PCT = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%")
_PREMIUMISH = re.compile(r"premium|\bbp\b|buyer'?s?\s+fee|surcharge|\bcc\b"
                         r"|credit|card|internet\s+fee", re.I)

# No US combined state+local rate reaches 12%. Above that it is a misread.
_TAX_MAX = 0.16   # HST reaches 15% in Atlantic Canada


def parse_tax(payment_info=None, state=None) -> float:
    """Sales tax as a fraction. Stated rate wins, then the state, then default.

    Returns 0.0 when the auction says there is no sales tax, or when
    FLIPSCOUT_RESALE_EXEMPT is set.
    """
    if RESALE_EXEMPT:
        return 0.0
    text = (payment_info or "").strip()
    if text:
        if _NO_TAX.search(text):
            return 0.0
        v = _tax_near_the_word(text)
        if v is not None:
            return v
    key = (state or "").strip().upper()[:2]
    if key in STATE_TAX:
        return STATE_TAX[key]
    return DEFAULT_TAX


def _tax_near_the_word(text: str):
    """The percentage that actually belongs to the word "tax", or None.

    Two orderings occur, and the ordering is the discriminator:

        "8.25% Texas sales tax"                 number BEFORE the word
        "Sales Tax : 6.75%"                     number AFTER the word

    A figure that FOLLOWS the word wins, because the only strings carrying both
    shapes look like "Internet Premium: 15% Sales Tax : 6.75%" - where the
    leading percentage is the premium and the trailing one is the tax.

    Anything with premium or card-surcharge wording BETWEEN it and the word is
    discarded outright. Note the rejection is on what sits between them, not on
    what precedes the number: "a 15% buyer's premium and 8.25% Texas sales tax"
    mentions the premium earlier in the same sentence, and 8.25 is still the
    tax.
    """
    after, before = [], []
    for w in _TAX_WORD.finditer(text):
        for m in _ANY_PCT.finditer(text):
            lo, hi = min(m.end(), w.start()), max(m.start(), w.end())
            if hi - lo > 30:
                continue                   # too far apart to be related
            if _PREMIUMISH.search(text[lo:hi]):
                continue                   # that figure is the premium
            try:
                v = float(m.group(1)) / 100.0
            except (TypeError, ValueError):
                continue
            if not 0.0 < v <= _TAX_MAX:
                continue
            (after if m.start() >= w.end() else before).append((hi - lo, v))
    pick = sorted(after or before)
    return pick[0][1] if pick else None


def tax_is_stated(payment_info=None) -> bool:
    """True when the auction itself gave the figure, rather than the table."""
    text = (payment_info or "").strip()
    if not text:
        return False
    if _NO_TAX.search(text):
        return True
    return _tax_near_the_word(text) is not None

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

"""The buyer's premium parser, and the ceiling that has to divide by it.

Every string in `REAL` was copied out of a live HiBid `buyerPremium` field on
2026-08-18, from a 457-auction sample that contained 290 distinct spellings.
That is the point of this file: the field is prose typed by an auctioneer, so
the only defence is a corpus.
"""

import pytest

from flipscout.auctionfees import (
    DEFAULT_PREMIUM,
    min_increment,
    parse_premium,
    premium_is_stated,
)
from flipscout.bidding import advise


# --- the corpus --------------------------------------------------------------

REAL = [
    # plain
    ("10%", 0.10),
    ("20% BUYERS PREMIUM", 0.20),
    ("10% Buyer's Premium", 0.10),
    ("Buyers premium of 11.5% is added to each", 0.115),
    # spelling variants
    ("%10 Buyers Premium", 0.10),
    ("18 percent bp", 0.18),
    ("Flat rate of 10 percent", 0.10),
    ("Buyers premium-16", 0.16),
    # explicit zero - believed
    ("NO BUYERS PREMIUM", 0.0),
    ("No Buyer's Premium Sale", 0.0),
    ("0% Buyer's Premium", 0.0),
    # caps and tiers: the rate is what matters at Leron's price points
    ("10% BP capped at $500 per lot", 0.10),
    ("5% Internet BP with a $750 cap per item", 0.05),
    ("0-$5000 = 15% $5000and up 10%  $1000 cap", 0.15),
]

# 🚨 A cash discount is not a premium. Reading the discount as the rate would
# under-price the cost and quietly raise the ceiling.
DISCOUNTS = [
    ("15% (3% DISCOUNT FOR CASH)", 0.15),
    ("13% BP Credit Cards 3% Discount Cash", 0.13),
    ("18% & 5% Discount for Cash Payment", 0.18),
    ("18.5% with 3.5% discount for cash paymen", 0.185),
    ("Buyer's Premium: 13% (3% cash discount)", 0.13),
    # a discount is ALL this one states, so there is no premium figure at all
    ("3% discount for cash or check on 8/21", DEFAULT_PREMIUM),
]

# Two figures. Either alternatives (cash rate vs card rate) or base + surcharge.
# Leron pays by card, so the card reading is the true one both times.
TWO_RATE = [
    # alternatives -> take the card one
    ("10% BP cash/check 14% BP for Credit Card", 0.14),
    ("12% Cash / 17% CC", 0.17),
    ("18% Credit card 15% cash", 0.18),
    ("16% for Credit 12% for Cash/Check", 0.16),
    ("BP 15%  for CARD 10%  w/ CASH OR CHECK", 0.15),
    # 🚨 "&" joins alternatives here, not addends - 25% would be wrong
    ("15% Cards & 10% Cash.", 0.15),
    # genuine surcharges -> add
    ("10% Buyer's Premium  +4% for card", 0.14),
    ("10% Buyer's Premium + 3.6% for CC Fee", 0.136),
    ("18% Buyers Premium + 3% Credit Card Fee", 0.21),
    ("12% BP/ 4% CC surcharge", 0.16),
    ("15% BP and a 3% CC Processing Fee", 0.18),
    # the additive word trails the figure rather than sitting between them
    ("10% card charge is 4% extra", 0.14),
    ("3.5% buyer premium, CC is an extra 2.5%", 0.06),
]


@pytest.mark.parametrize("text,want", REAL + DISCOUNTS + TWO_RATE)
def test_parses_real_strings(text, want):
    assert parse_premium(text) == pytest.approx(want)


# --- the safe direction ------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "Buyers Premium", "Convenience Fee",
                                  "Auctioneer's fees", ".", "Commmission"])
def test_unknown_falls_back_never_to_zero(text):
    """🚨 A premium named without a figure is UNKNOWN, not absent.

    15% of sampled auctions land here. Defaulting to zero would silently
    restore the exact bug this module exists to fix, on one lot in seven.
    """
    assert parse_premium(text) == DEFAULT_PREMIUM
    assert parse_premium(text) > 0
    assert premium_is_stated(text) is False


@pytest.mark.parametrize("text", ["0.00", "0", "0.0"])
def test_a_bare_zero_is_an_empty_box_not_a_promise(text):
    """Measured 2026-08-18: a live PS5 auction carried buyerPremium='0.00'
    while every comparable house charged 15-20%. Only prose that actually says
    zero is believed."""
    assert parse_premium(text) == DEFAULT_PREMIUM
    assert premium_is_stated(text) is False


def test_explicit_zero_is_believed():
    assert parse_premium("No Buyer's Premium") == 0.0
    assert premium_is_stated("No Buyer's Premium") is True


@pytest.mark.parametrize("text", ["95% buyers premium", "500% BP", "60%"])
def test_implausible_rates_fall_back(text):
    assert parse_premium(text) == DEFAULT_PREMIUM


def test_stated_and_guessed_are_distinguishable():
    assert premium_is_stated("15% buyers premium") is True
    assert premium_is_stated("Buyers Premium") is False


# --- increments --------------------------------------------------------------

TABLE = [
    {"minBidIncrement": 1.0, "upToAmount": 9.0},
    {"minBidIncrement": 5.0, "upToAmount": 95.0},
    {"minBidIncrement": 25.0, "upToAmount": 975.0},
]


@pytest.mark.parametrize("amount,want", [
    (0, 1.0), (9, 1.0), (10, 5.0), (95, 5.0), (96, 25.0), (5000, 25.0),
])
def test_increment_table(amount, want):
    assert min_increment(amount, TABLE) == want


def test_increment_without_a_table_is_a_dollar():
    assert min_increment(50, None) == 1.0
    assert min_increment(50, []) == 1.0


def test_increment_ignores_junk_rows():
    assert min_increment(50, [{"minBidIncrement": 0, "upToAmount": 100},
                              {"minBidIncrement": 5.0, "upToAmount": 100}]) == 5.0


# --- the ceiling -------------------------------------------------------------

def test_premium_lowers_the_ceiling_but_not_the_landed_cost():
    """The whole point: you may bid LESS, but you still pay the same total."""
    kw = dict(inbound_shipping=9.0, outbound_shipping=15.0, target_profit=20.0,
              current_price=50.0)
    plain = advise(330.0, buyer_premium_rate=0.0, **kw)
    prem = advise(330.0, buyer_premium_rate=0.20, **kw)
    assert prem.max_bid < plain.max_bid
    assert prem.landed_at_max == pytest.approx(plain.landed_at_max, abs=0.02)


@pytest.mark.parametrize("rate", [0.0, 0.10, 0.15, 0.21])
def test_target_profit_is_still_exact_at_the_ceiling(rate):
    """🚨 The invariant. If this drifts, every ceiling is wrong by the drift."""
    a = advise(330.0, inbound_shipping=9.0, outbound_shipping=15.0,
               target_profit=20.0, current_price=50.0, buyer_premium_rate=rate)
    assert a.net_resale - a.landed_at_max == pytest.approx(20.0, abs=0.02)


def test_premium_is_charged_on_the_bid_not_the_asking_price():
    """It scales with what you bid, so it cannot be a fixed subtraction."""
    a = advise(330.0, target_profit=20.0, current_price=10.0,
               buyer_premium_rate=0.15)
    assert a.buyer_premium_at_max == pytest.approx(a.max_bid * 0.15, abs=0.02)


def test_profit_at_open_includes_the_premium():
    kw = dict(target_profit=20.0, current_price=100.0, bid_count=0)
    plain = advise(330.0, buyer_premium_rate=0.0, **kw)
    prem = advise(330.0, buyer_premium_rate=0.20, **kw)
    assert prem.profit_at_open == pytest.approx(plain.profit_at_open - 20.0, abs=0.02)


def test_a_lot_can_lose_its_room_once_the_premium_is_counted():
    """Measured on a live lot: a PS5 at $232.50 showed $9 of room on the old
    maths and is $20 PAST the ceiling once a 14% premium is priced in."""
    kw = dict(inbound_shipping=9.0, outbound_shipping=15.0, target_profit=20.0,
              current_price=232.50, bid_count=5, increment=5.0)
    assert advise(330.0, buyer_premium_rate=0.0, **kw).has_room is True
    assert advise(330.0, buyer_premium_rate=0.14, **kw).has_room is False


def test_default_rate_is_zero_so_non_auction_sources_are_untouched():
    """ShopGoodwill and fixed-price listings have no premium; they must not
    move because this module exists."""
    kw = dict(inbound_shipping=9.0, target_profit=20.0, current_price=50.0)
    assert advise(200.0, **kw).max_bid == advise(200.0, buyer_premium_rate=0.0,
                                                 **kw).max_bid
    assert advise(200.0, **kw).buyer_premium_rate == 0.0


def test_summary_mentions_the_premium_only_when_there_is_one():
    kw = dict(target_profit=20.0, current_price=50.0)
    assert "premium" not in advise(200.0, **kw).summary()
    assert "premium" in advise(200.0, buyer_premium_rate=0.15, **kw).summary()


# --- the waiver trap ---------------------------------------------------------

def test_a_waiver_never_beats_a_stated_card_rate():
    """🚨 "no buyers premium for cash, 18% for cards" returned 0%.

    The zero-check ran before anything else and short-circuited the parser -
    on exactly the cash-versus-card split this module exists to resolve. The
    result was a ceiling 18% too high on a real auction.
    """
    assert parse_premium("no buyers premium for cash, 18% for cards") == 0.18
    assert parse_premium("No Buyer's Premium with cash / 13% credit") == 0.13


@pytest.mark.parametrize("text", [
    "NO BUYERS PREMIUM", "No Buyer's Premium Sale", "No Buyers Premium",
])
def test_a_bare_waiver_is_still_zero(text):
    """A waiver with no competing figure means what it says."""
    assert parse_premium(text) == 0.0


def test_an_explicit_zero_percent_is_still_zero():
    assert parse_premium("0% Buyer's Premium") == 0.0


# --- a fraction typed as a decimal -------------------------------------------

@pytest.mark.parametrize("text", ["Buyer's Premium: 0.15", "0.5%", "0.15", "0.9%"])
def test_a_sub_one_percent_rate_is_a_misread_not_a_bargain(text):
    """🚨 "Buyer's Premium: 0.15" is a FRACTION written as a decimal. Read
    literally it is 0.15% - functionally free, and a ceiling far too high. No
    auction house charges half a percent."""
    assert parse_premium(text) == DEFAULT_PREMIUM


def test_the_floor_does_not_swallow_a_real_low_rate():
    assert parse_premium("3% buyers premium") == 0.03
    assert parse_premium("1% BP") == 0.01

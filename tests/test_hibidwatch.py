"""Guardrails for the HiBid watch-list READER.

🚨 This module used to own the whole feed. On 2026-08-20 Leron asked for
ShopGoodwill's watch list too, plus a 30-minute call to arm, so the entrypoint
moved to watchlist.py - a second module that also read HiBid would have carded
every lot twice. What stays here is the HiBid scrape and `ceiling_leak`, and
`ceiling_leak` is the one that must never regress: it is what stops a card's
TEXT authorising more than its `max_bid` does.

The behaviour tests for cards, calls and state live in tests/test_watchlist.py.

Nothing here touches the network, a browser, or Discord.
"""

import pytest

from flipscout import hibidwatch as W
from flipscout.discordarm import parse_card
from flipscout.notify import build_embed

LOT = "317520978"


@pytest.mark.parametrize("text,want", [
    ("Showing 1 - 1 of 1 lot", 1),
    ("Showing 1 - 100 of 1,234 lots", 1234),
    ("no paginator here", None),
])
def test_parse_total(text, want):
    """The paginator's own count, used to prove nothing was silently dropped."""
    assert W.parse_total(text) == want


def test_ceiling_leak_catches_a_card_whose_text_offers_more_than_its_field():
    """🚨 THE GUARD THIS FILE EXISTS FOR.

    discordarm pulls the arm figure out of a card's RENDERED TEXT, across every
    embed field - so omitting `max_bid` is not enough. The first draft of the
    "already past what it is worth" branch printed no max_bid and still said
    "The book's ceiling is $10.42" in prose, which parsed as a live ceiling: a
    card promising 🎯 would not arm it, armed it.
    """
    bad = {"title": "x", "url": f"https://hibid.com/lot/{LOT}",
           "buy_url": f"https://hibid.com/lot/{LOT}",
           "reason": "ceiling $999.00"}
    assert W.ceiling_leak(bad) == 999.0

    good = {"title": "x", "url": f"https://hibid.com/lot/{LOT}",
            "buy_url": f"https://hibid.com/lot/{LOT}",
            "max_bid": 10.42, "reason": "Tap to arm at **$10.42**"}
    assert W.ceiling_leak(good) is None
    assert parse_card({"content": "", "embeds": [build_embed(good)]})[2] == 10.42


def test_ceiling_leak_is_clean_when_no_number_is_offered_at_all():
    c = {"title": "x", "url": f"https://hibid.com/lot/{LOT}",
         "buy_url": f"https://hibid.com/lot/{LOT}",
         "reason": "the book has no comp for this - reply `snipe <amount>`"}
    assert W.ceiling_leak(c) is None
    assert parse_card({"content": "", "embeds": [build_embed(c)]})[2] is None


def test_the_entrypoint_really_did_move():
    """Both modules reading HiBid would card every lot twice."""
    assert not hasattr(W, "run"), "run() belongs to watchlist.py now"
    assert not hasattr(W, "card"), "card() belongs to watchlist.py now"
    from flipscout import watchlist
    assert callable(watchlist.run) and callable(watchlist.card)

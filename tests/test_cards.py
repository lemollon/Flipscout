"""The card-shop buy box, pinned.

Every test here traces to one of the five rules Leron's friend gave on
2026-08-22 (see flipscout/cards.py for the transcript). The point of pinning
them is that the rules are cheap to state and easy to break with a regex tweak:
`\bRC\b`, `auto`, `1/1` and `/99` are all tokens that mean something else
outside this category, and the guards that keep them honest are invisible until
one of them regresses.
"""

from __future__ import annotations

import pytest

from flipscout import cards
from flipscout.cards import read


def v(title: str) -> str:
    return read(title).verdict


# --- the gate: nothing is read until it proves it is a card -----------------

@pytest.mark.parametrize("title", [
    "Michael Jordan Autographed Basketball",
    "1998 Chevy Prizm 4 door sedan",
    "RC helicopter parts lot rookie pilot",
    "New Jersey patch iron on",
    "Vintage 1975 gold refractor telescope mirror",
])
def test_non_cards_get_no_verdict(title):
    """🚨 THE LOAD-BEARING GUARD. Every signal in the file is a false positive
    somewhere else; they are safe only because the maker/slab/card-word gate
    runs first."""
    r = read(title)
    assert not r.is_card
    assert r.verdict == "UNKNOWN"


@pytest.mark.parametrize("title", [
    "2020 Topps Chrome Justin Herbert RC",
    "1986 Fleer Michael Jordan #57 Rookie Card",
    "Trading card PSA 10 Tom Brady",
])
def test_card_evidence_opens_the_read(title):
    assert read(title).is_card


# --- rule 1: "avoid 80s and 90s" --------------------------------------------

def test_junk_wax_base_card_is_a_pass():
    r = read("1991 Score Ken Griffey Jr #1 Baseball Card")
    assert r.verdict == "PASS"
    assert r.era == "junk wax"
    assert any("JUNK WAX" in why for why in r.reasons)


def test_junk_wax_veto_applies_to_the_stars_too():
    """The friend's rule is about the PRINT RUN, not the player - a 1990 Nolan
    Ryan common is as printed as everything else that year."""
    assert v("1990 Topps Nolan Ryan #1 Baseball Card") == "PASS"


def test_a_high_grade_beats_the_era_veto():
    """🚨 THE ONE DOCUMENTED EXCEPTION. Condition is the only scarcity junk wax
    has left, and a 9/10 slab is exactly the card that certifies it."""
    raw = read("1989 Upper Deck Ken Griffey Jr RC #1")
    slabbed = read("1989 Upper Deck Ken Griffey Jr RC #1 PSA 10 GEM MINT")
    assert raw.verdict == "PASS"
    assert slabbed.verdict == "CHASE"
    assert slabbed.score > raw.score


# --- rule 1a: the named exceptions ------------------------------------------
# Asked directly whether he really avoids the whole of the 80s and 90s,
# 2026-08-22: "80's and 90s unless Kobe 96 or Jordan 86". The escape is a
# NAMED CARD, not a date boundary - which is why this file no longer carries
# the 1987 cutoff its first cut invented.

@pytest.mark.parametrize("title", [
    "1986 Fleer Michael Jordan #57 Rookie Card",
    "1996 Topps Chrome Kobe Bryant RC #138",
    "1996-97 Topps Chrome Kobe Bryant Refractor Rookie",
])
def test_the_two_named_cards_escape_the_era_veto(title):
    r = read(title)
    assert r.era == "junk wax"          # the era is not in dispute
    assert r.verdict == "CHASE"         # the exception is
    assert any("EXCEPT this one" in why for why in r.reasons)


@pytest.mark.parametrize("title", [
    "1991 Fleer Michael Jordan #29 Basketball Card",   # right player, wrong year
    "1998 Topps Kobe Bryant #68",                      # right player, wrong year
    "1986 Donruss Baseball Card #55",                  # right year, wrong player
])
def test_the_exception_is_a_card_not_a_player_or_a_year(title):
    """🚨 NOT "Jordan is good" - "the 1986 Jordan is good". A 1991 Fleer Jordan
    is junk wax and takes the full veto, which is exactly the distinction he
    was drawing. The year pairing is also what makes a bare surname safe:
    `jordan` alone would match Jordan Love."""
    assert v(title) == "PASS"


def test_a_named_exception_also_waives_its_bulk_brand_penalty():
    """1986 Fleer IS the product that matters; scoring it as a bulk-era brand
    would contradict the exception in the same breath as granting it."""
    r = read("1986 Fleer Michael Jordan #57 Rookie Card")
    assert not any(s.kind == "brand" and s.points < 0 for s in r.signals)


def test_the_1987_boundary_is_gone():
    """The first cut treated 1980-1986 as ordinary. He said the decade is out,
    so a 1986 common is now as dead as a 1991 one."""
    assert read("1986 Donruss Baseball Card #55").era == "junk wax"


def test_pre_1980_vintage_is_its_own_game():
    r = read("1968 Topps Nolan Ryan Rookie Card #177")
    assert r.era == "vintage"
    assert r.verdict in ("LOOK", "CHASE")


def test_modern_is_the_hunting_ground_but_not_a_free_pass():
    """"Anything in the 2000s" opens the door; it does not walk you through it.
    A modern BASE card is still a base card."""
    r = read("2023 Topps Series 1 Aaron Judge #99 Baseball Card")
    assert r.era == "modern"
    assert r.verdict == "PASS"
    assert any("BASE CARD" in why for why in r.reasons)


# --- rule 2 + 3: hits and chase cards ---------------------------------------

def test_autograph_is_a_hit():
    r = read("2022 Topps Chrome Julio Rodriguez Rookie Auto")
    assert r.verdict == "CHASE"
    assert any("AUTOGRAPH" in why for why in r.reasons)


def test_patch_auto_rookie_is_the_top_of_the_hobby():
    r = read("2021 Panini National Treasures Trevor Lawrence RPA Patch Auto /25")
    assert r.verdict == "CHASE"
    kinds = {s.kind for s in r.signals}
    assert {"hit", "numbered", "rookie", "brand"} <= kinds


def test_relic_is_a_hit_but_a_smaller_one_than_a_patch():
    patch = read("2020 Panini Prizm Patch Card Joe Burrow")
    relic = read("2020 Panini Prizm Game Used Jersey Relic Joe Burrow")
    assert patch.score > relic.score


def test_a_parallel_is_a_chase_card():
    r = read("2021 Panini Prizm Josh Allen Silver Prizm")
    assert any("PARALLEL" in why for why in r.reasons)


def test_a_bare_colour_is_not_a_chase_card():
    """🚨 Every parallel has a colour name and so does every team. 'Gold' alone
    matched 'Golden State Warriors' and 'Blue Jays' on the first cut."""
    r = read("2023 Topps Golden State Warriors Blue Jays team card")
    assert not any(s.kind == "chase" for s in r.signals)


def test_redemption_that_never_arrived_is_not_an_auto():
    r = read("2019 Topps Chrome Auto Redemption Expired Wander Franco")
    assert not any(s.kind == "hit" for s in r.signals)


# --- rule 2: numbered cards -------------------------------------------------

def test_bare_print_run_is_read():
    """Sellers drop the copy number and write only the run - '/99', '#/25',
    'numbered to 150' - which is the commonest way the signal appears."""
    assert read("2020 Topps Chrome Refractor Auto/99 Justin Herbert RC").print_run == 99
    assert read("2019 Prizm Zion Williamson RC #/25 Gold").print_run == 25
    assert read("2022 Bowman Chrome 1st Bowman Auto numbered to 150").print_run == 150


def test_one_of_one_outranks_every_other_serial():
    one = read("2022 Panini Select Ja Morant 1/1 Black Prizm")
    ten = read("2022 Panini Select Ja Morant /10 Gold Prizm")
    assert one.print_run == 1
    assert one.score > ten.score


def test_scarcer_runs_score_higher():
    runs = [read(f"2021 Topps Chrome Refractor Wander Franco /{n}").score
            for n in (10, 25, 99, 250, 999)]
    assert runs == sorted(runs, reverse=True)


def test_set_position_is_not_a_print_run():
    """🚨 THE TRAP THIS CATEGORY SETS. '124/165' is printed on the front of
    nearly every junk-wax base card and means WHERE IT SITS IN THE SET.
    Serial numbering did not exist before ~1996, so reading it as a run of 165
    would turn the whole junk-wax bin into false CHASEs."""
    r = read("1990 Topps Baseball Card 124/165 Nolan Ryan")
    assert r.print_run is None
    assert r.verdict == "PASS"


def test_with_abbreviation_is_not_a_print_run():
    """'w/' is a letter and a slash, which is the exact shape being matched."""
    assert read("Baseball card lot w/ 50 cards 1990 Topps").print_run is None


def test_a_run_too_big_to_mean_anything_is_not_numbered():
    assert read("2021 Topps Chrome Refractor /5000 Wander Franco").print_run is None


# --- rule 4: rookies --------------------------------------------------------

def test_rookie_scores_and_names_itself():
    r = read("2018 Panini Donruss Optic Luka Doncic Rated Rookie RC")
    assert any("ROOKIE" in why for why in r.reasons)


def test_first_bowman_counts_as_a_rookie():
    r = read("2022 Bowman Chrome 1st Bowman Elly De La Cruz")
    assert any(s.kind == "rookie" for s in r.signals)


# --- rule 5: brands ---------------------------------------------------------

def test_brand_is_a_bump_not_a_verdict():
    """🚨 A National Treasures BASE card is still a base card. The brand tier
    must never be able to carry a card on its own."""
    assert v("2021 Panini National Treasures base card Mac Jones") == "PASS"


def test_premium_brand_lifts_an_otherwise_equal_card():
    prem = read("2021 Topps Chrome Refractor Shohei Ohtani")
    plain = read("2021 Topps Refractor Shohei Ohtani")
    assert prem.score > plain.score


def test_bulk_era_brand_is_scored_against():
    r = read("1991 Fleer Baseball Card #100")
    assert any(s.kind == "brand" and s.points < 0 for s in r.signals)


# --- the stoppers -----------------------------------------------------------

def test_reprint_ends_the_conversation_whoever_is_on_it():
    r = read("1952 Topps Mickey Mantle #311 REPRINT Rookie Card")
    assert r.verdict == "PASS"
    assert any("REPRINT" in why for why in r.reasons)


def test_a_pile_is_not_a_card():
    """The same finding pricebook's DEAD_MODELS recorded for Pokemon lots: a
    hundred-fold spread the title cannot resolve."""
    r = read("Lot of 500 Baseball Cards 1988-1992 Topps Fleer Donruss")
    assert r.verdict == "PASS"
    assert any("PILE" in why for why in r.reasons)


# --- the boundary with the priced book --------------------------------------

def test_tcg_is_handed_back_to_the_measured_tiers():
    """🚨 Pokemon already has three MEASURED comps in pricebook. A triage
    verdict beside a real number is noise at best and a contradiction at
    worst, so this refuses to judge them."""
    r = read("Pokemon Charizard PSA 10 1999 Base Set Holo")
    assert r.family == "tcg"
    assert r.verdict == "PRICED"
    assert cards.one_liner(r) == ""


def test_the_read_never_produces_a_price():
    """The law this module lives under: pricebook prices, cards triages."""
    r = read("2021 Panini National Treasures Trevor Lawrence RPA Patch Auto /25")
    text = cards.explain(r)
    assert "$" not in text
    assert not hasattr(r, "comp")
    assert not hasattr(r, "max_bid")


# --- the alert line ---------------------------------------------------------

def test_one_liner_is_empty_for_everything_that_is_not_a_card():
    """It runs on every alerted listing, so silence on non-cards is the
    contract - otherwise every camera alert grows a card note."""
    assert cards.one_liner(read("Canon AE-1 35mm Film Camera")) == ""


def test_one_liner_leads_with_the_verdict_and_the_signals():
    line = cards.one_liner(read("2022 Topps Chrome Julio Rodriguez Rookie Auto"))
    assert line.startswith("Card read: CHASE")
    assert "AUTOGRAPH" in line


def test_one_liner_says_base_card_out_loud():
    line = cards.one_liner(read("2023 Topps Series 1 Aaron Judge #99 Baseball Card"))
    assert "PASS" in line and "base card" in line.lower()


# --- the false positives that were actually found ---------------------------
# Both were caught by running the reader over the 521 listings on the live
# board (docs/deals.json) rather than over invented titles, which is the only
# way this class of bug shows up: the words are perfectly good card vocabulary
# and perfectly good English.

def test_ultra_compact_camera_is_not_a_fleer_ultra_card():
    """`ultra\\b` was in the maker gate for Fleer Ultra and matched
    "Vintage Canon ELPH LT260 Ultra-Compact Camera" on the live board. `fleer`
    already opens the gate for that brand, so the bare word bought nothing."""
    assert not read("Vintage Canon ELPH LT260 Ultra-Compact Camera w/ Accessories").is_card
    assert read("1991 Fleer Ultra Baseball #100").is_card


def test_chevy_prizm_is_a_car():
    """Parallel vocabulary is weak evidence - it needs a card-shaped second
    signal, and a sedan has none."""
    assert not read("1998 Chevy Prizm 4 door sedan").is_card
    assert read("2019 Prizm Zion Williamson RC #/25 Gold").is_card


def test_the_reader_stays_silent_on_the_whole_live_board():
    """🚨 THE CONTRACT FOR THE ALERT WIRING. `hunt` calls one_liner() on every
    listing it alerts on, so a reader that speaks up on cameras and calculators
    turns every alert into wallpaper. Measured 2026-08-22: 0 of the 521
    listings on the board draw a card line."""
    import json
    import pathlib
    board = pathlib.Path(__file__).resolve().parent.parent / "docs" / "deals.json"
    if not board.exists():                       # board is refreshed by CI
        pytest.skip("no board snapshot checked in")
    titles = [i.get("title", "") for i in json.loads(board.read_text())["items"]]
    spoke = [t for t in titles if cards.one_liner(read(t))]
    assert not spoke, f"card line fired on non-cards: {spoke[:5]}"


# --- the value assessment, added 2026-08-22 ---------------------------------
# Leron: "There is no value assessment on the cards." He is right that a
# verdict with no number attached is half an answer. This file still refuses to
# INVENT a number - what it does instead is aim the lookup precisely, which is
# the same promise every priced alert makes minus the claim we have not earned.

def test_the_query_names_the_card_not_the_category():
    """🚨 "pokemon card" returns the category and is worthless. The identity IS
    the price in this category, so the query has to carry it."""
    q = cards.comp_query(read("2018 Panini Prizm Luka Doncic Silver Prizm RC Auto /99"))
    for token in ("2018", "panini", "prizm", "luka", "doncic", "rc", "auto", "/99"):
        assert token in q, f"{token} missing from {q!r}"


def test_seller_hype_is_stripped_from_the_query():
    """The other silent failure: a query carrying words no other listing shares
    returns zero results, and a link with no results looks exactly like a link
    that works."""
    q = cards.comp_query(read(
        "🔥 WOW 2018 Panini Prizm Luka Doncic Silver Prizm RC Auto /99 GEM MINT INVEST L@@K"))
    for junk in ("wow", "gem", "mint", "invest", "l@@k", "🔥"):
        assert junk not in q, f"{junk!r} survived into {q!r}"
    assert "luka" in q and "/99" in q


def test_the_grade_survives_the_word_cap():
    """🚨 THE GRADE IS THE BIGGEST PRICE VARIABLE A TITLE CARRIES, and it is
    usually at the very end - past the cap. Truncating the tail drops exactly
    the token that matters."""
    q = cards.comp_query(read(
        "1999 Pokemon Base Set Charizard Holo Unlimited Rare Card Number 4 of 102 PSA 9"))
    assert q.endswith("psa 9")


def test_the_query_is_short_enough_for_ebay_to_and_together():
    long = read("2021 Panini National Treasures Trevor Lawrence Rookie Patch "
                "Autograph RPA Jacksonville Jaguars Football Card /25 BGS 9.5")
    assert len(cards.comp_query(long).split()) <= 12


def test_comp_url_is_a_sold_search_and_is_not_condition_filtered():
    """🚨 NOT used_only. The price-book links pin eBay's Used filter to match a
    measured population; nothing here was measured, and a slab is listed under
    half a dozen conditions - filtering would hide most of the sales."""
    u = cards.comp_url(read("2020 Topps Chrome Justin Herbert RC Refractor"))
    assert "LH_Sold=1" in u and "LH_Complete=1" in u
    assert "LH_ItemCondition" not in u


def test_a_non_card_gets_no_query_and_no_url():
    r = read("Canon AE-1 35mm Film Camera")
    assert cards.comp_query(r) == "" and cards.comp_url(r) == ""


def test_explain_carries_the_lookup_but_still_never_a_price():
    text = cards.explain(read("2018 Panini Prizm Luka Doncic RC Auto /99"))
    assert "WHAT IT SELLS FOR" in text and "ebay.com" in text
    assert "$" not in text


# --- the candidate tiers, added 2026-08-22 ----------------------------------
# Leron: "I'm looking for deals. I want value." The triage gives neither on its
# own. What stands between the scout and a real ceiling is one browser
# measurement per tier, so CARD_TIERS carries the matching half of a Model and
# `flipscout cardcomp` supplies the other half from a paste.

_TIER_HITS = {
    "sports_rpa": "2021 Panini National Treasures Trevor Lawrence RPA Patch Auto /25",
    "sports_rookie_auto": "2022 Topps Chrome Julio Rodriguez Rookie Auto",
    "sports_low_numbered": "2019 Panini Prizm Zion Williamson Gold Prizm /10",
    "sports_graded_rookie": "2018 Panini Prizm Luka Doncic RC PSA 10",
    "sports_sealed_box": "2021 Panini Prizm Basketball Factory Sealed Hobby Box",
}


def test_every_candidate_tier_matches_its_own_shape():
    import re
    for t in cards.CARD_TIERS:
        title = _TIER_HITS[t.key]
        assert re.search(t.include, title.lower()), f"{t.key} missed {title!r}"


@pytest.mark.parametrize("title", [
    "2023 Topps Series 1 Aaron Judge #99 Baseball Card",   # base card
    "1991 Score Baseball Card Lot of 500",                 # junk wax pile
    "Canon AE-1 35mm Film Camera",                         # not a card at all
])
def test_no_candidate_tier_matches_something_worthless(title):
    """🚨 A tier that eats base cards would price a $0.30 common at a rookie-auto
    comp - the single most expensive way to be wrong in this category."""
    import re
    for t in cards.CARD_TIERS:
        assert not re.search(t.include, title.lower()), f"{t.key} ate {title!r}"


def test_the_tiers_carry_no_comp_at_all():
    """🚨 THE BOOK'S ONE LAW. These are ready to be priced, not priced. An
    unmeasured number here becomes a confident wrong ceiling downstream."""
    for t in cards.CARD_TIERS:
        assert not hasattr(t, "comp")
        assert not hasattr(t, "measured")


def test_the_tiers_land_in_a_category_the_merch_guard_skips():
    """They will be pasted into MODELS as category "sports-cards", which must
    be one of the card categories - otherwise _CARD_MERCH rejects them on the
    word "card" exactly as it did the Pokemon tiers."""
    from flipscout.pricebook import CARD_CATEGORIES
    assert "sports-cards" in CARD_CATEGORIES

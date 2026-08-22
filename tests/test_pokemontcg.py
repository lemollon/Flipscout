"""Pricing a Pokemon card by WHICH CARD IT IS.

🚨 THE BUG THESE TESTS EXIST TO PREVENT COMING BACK. On 2026-08-22 the two
`pokemon-cards` tiers in `pricebook` carried one blanket comp each - $112.50 for
any PSA 9/10, $92.00 for any PSA 7-9 - and 29 of the 30 graded Pokemon lots on
the live board were HiBid, which is the armable path. Against TCGplayer market
prices for the actual cards:

    Pokemon PSA 10 Litten        max bid $49.41    the card is worth $0.14
    Oricorio #024 PSA 9          max bid $48.63    the card is worth $0.06
    1999 Alakazam Holo #1 PSA 7  max bid $35.50    the card is worth $69.45

Every test below is offline. The price source is a real HTTP API and a suite
that needs it is a suite that fails when someone else's server has a bad day.
"""

import pytest

from flipscout import pokemontcg as pk


# --- reading the title ------------------------------------------------------

def test_the_set_number_is_the_card_not_the_set_size():
    """🚨 "4/102" is card FOUR of a hundred-and-two. Taking the denominator
    looks up card #102 of Base Set for every Base Set card there is."""
    assert pk.identify("1999 Pokemon Base Set Charizard 4/102 Holo").number == "4"
    assert pk.identify("Pokemon Abra #43 PSA 8").number == "43"


def test_a_leading_zero_is_not_a_different_card():
    """A real HiBid title: "Pokemon - Oricorio - #024 PSA 9"."""
    assert pk.identify("Pokemon -  Oricorio  - #024  PSA 9").number == "24"


def test_a_misspelled_name_still_resolves():
    """🚨 THE TYPO IS THE EDGE, NOT AN EDGE CASE. A misspelled title gets no
    search traffic and closes cheap - `pricebook.search_terms` hunts "pokeman"
    and "cannon ae-1" for exactly this reason. "Zapado" is a real lot."""
    pid = pk.identify("Pokemon 1999 Fossil Zapado Graded PSA 9")
    assert pid.name == "zapdos" and pid.fuzzy


def test_a_hyphen_does_not_hide_the_name():
    assert pk.identify("2022 POKEMON GO CHARIZARD-HOLO PSA 10").name == "charizard"


def test_the_grade_is_read_wherever_it_sits():
    assert pk.identify("PSA 9 Holographic Ho-Oh Pokemon Card").grade == "PSA 9"
    assert pk.identify("Pokemon TCG Charizard BGS 9.5 Card").grade == "BGS 9.5"


def test_first_edition_is_read_because_it_is_a_different_product():
    """Neo Discovery Umbreon is $300 first-edition and $85.60 unlimited."""
    assert pk.identify("1999 Pokemon Base Charizard 1st Edition").first_edition


def test_japanese_is_flagged_and_never_treated_as_junk():
    """🚨 THE CARTRIDGE RULE DOES NOT CROSS OVER. `pricebook` excludes Japanese
    GAMES because they are region-locked; a Japanese Pokemon CARD is a
    collectible in its own right. It is unpriceable here only because the
    source is English-only."""
    pid = pk.identify("2022 POKEMON GO JAPANESE CHARIZARD HOLO PSA 10")
    assert pid.japanese and not pid.priceable
    assert pk.verdict(pid).verdict == pk.LOOK       # look, not pass


def test_a_code_card_is_refused():
    """The cheapest mistake available: a printed password worth nothing whose
    title reads exactly like a card's."""
    pid = pk.identify("Pokemon TCG Online Code Card Charizard VMAX")
    assert pid.junk and pk.verdict(pid).verdict == pk.PASS


def test_a_pile_is_refused_because_a_title_cannot_price_it():
    for t in ("Lot Of 1999-2002 Pokemon Cards", "Pokemon binder 400 cards",
              "Pokemon card mystery repack"):
        assert pk.verdict(pk.identify(t)).verdict == pk.PASS, t


# --- the deal logic ---------------------------------------------------------

def _comp(market, candidates=1, **kw):
    kw.setdefault("card_id", "base1-43")
    kw.setdefault("name", "Abra")
    kw.setdefault("set_name", "Base")
    kw.setdefault("released", "1999/01/09")
    kw.setdefault("number", "43")
    kw.setdefault("rarity", "Common")
    kw.setdefault("printing", "normal")
    return pk.PokeComp(market=market, candidates=candidates, **kw)


def test_a_slabbed_common_is_a_PASS():
    """🚨 THE RULE THAT PAYS FOR ITSELF. Grading costs more than the card, so a
    PSA 10 of a $0.32 common is worth the plastic. Ten of these were on the
    board carrying $49-$53 max bids."""
    pid = pk.identify("Pokemon PSA 10 Litten")
    assert pk.verdict(pid, _comp(0.14)).verdict == pk.PASS


def test_a_slab_on_a_real_card_is_a_CHASE():
    pid = pk.identify("1999 Pokemon Alakazam Holo #1 PSA 7")
    assert pk.verdict(pid, _comp(69.45)).verdict == pk.CHASE


def test_a_graded_card_NEVER_gets_a_ceiling():
    """🚨 TCGplayer's price is the RAW card. Turning it into a slab price needs
    a grade multiplier nobody here has measured, and inventing one is exactly
    how the $92 blanket comp happened. Say so on the card instead."""
    for market in (0.14, 25.0, 900.0):
        v = pk.verdict(pk.identify("Pokemon Charizard PSA 10"), _comp(market))
        # PRICED is the only verdict meaning "there is a comp to bid against".
        # A slab must never earn it off a raw price.
        assert v.verdict != pk.PRICED, market
        if v.verdict in (pk.LOOK, pk.CHASE):
            assert "you set the number" in v.why.lower(), market


def test_an_ambiguous_match_refuses_to_price():
    """"Pokemon PSA 10 Litten" matches fourteen Littens from $0.25 to $40.66.
    Picking the dearest is how a common gets a $49 max bid."""
    v = pk.verdict(pk.identify("Pokemon PSA 10 Litten"),
                   _comp(0.14, candidates=14, low=0.14, high=40.66))
    assert v.verdict == pk.LOOK
    assert "no comp and no ceiling" in v.why


def test_a_raw_card_worth_real_money_is_PRICED():
    v = pk.verdict(pk.identify("1999 Pokemon Base Set Charizard 4/102 Holo"),
                   _comp(855.52, printing="holofoil"))
    assert v.verdict == pk.PRICED


def test_a_first_edition_claim_with_no_first_edition_price_says_so():
    """Printing the UNLIMITED number for a card the title calls 1st Edition is
    an understatement of a multiple, so it is called out rather than shown
    bare."""
    v = pk.verdict(pk.identify("1999 Pokemon Base Charizard 1st Edition Holo"),
                   _comp(855.52, printing="holofoil"))
    assert "1st Edition" in v.why and "too low" in v.why


# --- talking to the API (faked) ---------------------------------------------

class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.asked = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.asked.append((params or {}).get("q"))
        return self.responses.pop(0) if self.responses else FakeResp({"data": []})


def _card(cid, name, number, prices, released="1999/01/09", set_name="Base"):
    return {"id": cid, "name": name, "number": number,
            "rarity": "Rare Holo", "set": {"name": set_name, "releaseDate": released},
            "tcgplayer": {"prices": prices}}


def test_one_card_one_number():
    s = FakeSession(FakeResp({"data": [
        _card("base1-43", "Abra", "43", {"normal": {"market": 1.54}})]}))
    c = pk.lookup(pk.identify("1999 Pokemon Abra #43 PSA 8"), session=s,
                  use_cache=False)
    assert c.card_id == "base1-43" and c.market == 1.54
    assert not c.ambiguous


def test_the_cheapest_candidate_wins_not_the_dearest():
    """🚨 THE CORRECTION THAT MATTERS MOST. Every wrong number this module
    produced in testing came from taking the dearest of a candidate list - a
    $0.32 Crown Zenith Lycanroc read as a $1.03 Paldea Evolved one. The
    cheapest cannot invent value the listing never claimed."""
    s = FakeSession(FakeResp({"data": [
        _card("a-1", "Litten", "1", {"normal": {"market": 40.66}},
              released="2024/03/22", set_name="Temporal Forces"),
        _card("b-2", "Litten", "2", {"normal": {"market": 0.14}},
              released="2018/09/07", set_name="Dragon Majesty")]}))
    c = pk.lookup(pk.identify("Pokemon PSA 10 Litten"), session=s, use_cache=False)
    assert c.market == 0.14
    assert c.ambiguous and c.low == 0.14 and c.high == 40.66


def test_a_year_that_matches_nothing_stays_ambiguous():
    """🚨 A CONTRADICTED YEAR IS EVIDENCE THE NAME MATCHED SOMETHING ELSE.
    "VENUSAUR HOLO 2021" against a list with no 2021 card returned a 2009
    Pokemon Rumble Venusaur at $279.92 when the rule was "take the dearest"."""
    s = FakeSession(FakeResp({"data": [
        _card("ru1-1", "Venusaur", "1", {"holofoil": {"market": 279.92}},
              released="2009/12/02", set_name="Pokemon Rumble")]}))
    c = pk.lookup(pk.identify("PSA 9 VENUSAUR HOLO 2021 POKEMON CARD"),
                  session=s, use_cache=False)
    assert c.ambiguous, "the title says 2021 and nothing here is from 2021"


def test_the_first_edition_column_is_preferred_when_the_title_claims_it():
    s = FakeSession(FakeResp({"data": [
        _card("neo2-32", "Umbreon", "32",
              {"1stEditionHolofoil": {"market": 300.0},
               "unlimitedHolofoil": {"market": 85.6}},
              released="2001/06/01", set_name="Neo Discovery")]}))
    c = pk.lookup(pk.identify("Pokemon Umbreon 32/75 1st Edition Holo"),
                  session=s, use_cache=False)
    assert c.printing == "1stEditionHolofoil" and c.market == 300.0


def test_a_dead_api_is_not_a_worthless_card():
    """🚨 None means "could not ask", NOT "no such card". Measured 2026-08-22:
    the API returns 500 and 502 at random and a probe without retries scored
    0/19 on titles that resolve with them. Reporting that as "not a card" would
    quietly turn every Pokemon lot into a PASS on a bad afternoon."""
    s = FakeSession(*[FakeResp({}, status=500)] * 12)
    assert pk.lookup(pk.identify("1999 Pokemon Abra #43 PSA 8"), session=s,
                     use_cache=False) is None
    assert len(s.asked) > 1, "it must retry rather than believe one 500"


def test_a_pile_never_reaches_the_network():
    s = FakeSession()
    assert pk.lookup(pk.identify("Lot of 300 Pokemon cards"), session=s,
                     use_cache=False) is None
    assert s.asked == [], "a lot cannot be priced, so do not spend a call on it"


def test_the_comp_link_is_a_tcgplayer_search_not_the_dead_api_url():
    """🚨 The API's own `tcgplayer.url` (prices.pokemontcg.io/tcgplayer/<id>)
    returns 502. A comp link that fails reads as evidence right up until you
    click it, which is worse than no link at all. Checked live 2026-08-22."""
    c = pk.PokeComp(card_id="base1-1", name="Alakazam", set_name="Base",
                    released="1999/01/09", number="1", rarity="Rare Holo",
                    printing="holofoil", market=69.45,
                    tcg_url="https://prices.pokemontcg.io/tcgplayer/base1-1")
    assert "prices.pokemontcg.io" not in c.url
    assert c.url.startswith("https://www.tcgplayer.com/search/pokemon/product")
    assert "Alakazam" in c.url


def test_a_grade_keyed_price_overrides_the_raw_slab_rule():
    """🚨 THE GRADE ITSELF CARRIES VALUE AND THE RAW PRICE CANNOT SEE IT.
    Measured the day the PriceCharting token went in: a PSA 10 Litten comps at
    $35.00 while the raw card is $0.25. The raw-only rule called that a PASS -
    "a slabbed common worth the plastic" - and was wrong by 140x."""
    from flipscout.sportscards import Candidate
    g = Candidate(product_id="1", name="Litten #32", set_name="Pokemon Temporal Forces",
                  price=35.00, ungraded=0.25, volume=87)
    v = pk.verdict(pk.identify("Pokemon PSA 10 Litten #32"), _comp(0.25), graded=g)
    assert v.verdict != pk.PASS
    assert "$35.00 in PSA 10" in v.why and "PriceCharting" in v.why


def test_without_a_grade_price_the_raw_slab_rule_still_applies():
    """The token is optional; the old conservative rule is the fallback."""
    v = pk.verdict(pk.identify("Pokemon PSA 10 Litten #32"), _comp(0.25), graded=None)
    assert v.verdict == pk.PASS

"""Pricing a sports card by the card, the parallel and the grade.

Every test is offline. The two traps below would each have shipped confidently
wrong comps, and neither is visible in a diff.
"""

import pytest

from flipscout import sportscards as sc


# --- the two traps ----------------------------------------------------------

def test_the_host_is_the_sports_site_not_pricecharting():
    """🚨 THE DOCS TELL YOU THE WRONG HOST. They say "make an HTTP request to
    the base URL https://www.pricecharting.com" - which is the shared backend
    for video games, comics, Funko and every TCG. Measured 2026-08-22 on
    "Shane Bieber 2018 Topps Chrome Refractor":

        sportscardspro.com  ->  7 matches, every one the right card
        pricecharting.com   -> 97 matches, top six Garbage Pail Kids

    Following the documentation would have priced a baseball card as
    "Barbaric Bieber [Red] #13a, 2024 Garbage Pail Kids".
    """
    assert sc._HOST == "https://www.sportscardspro.com"
    assert "pricecharting" not in sc._HOST


def test_prices_are_pennies():
    """🚨 An integer number of pennies. 1732 is $17.32, not $1,732."""
    assert sc._pennies(1732) == 17.32
    assert sc._pennies(0) is None          # 0 means "no price", not "free"
    assert sc._pennies(None) is None
    assert sc._pennies("bogus") is None


# --- the grade columns, which do not mean what they say ---------------------

@pytest.mark.parametrize("grade,column", [
    (None, "loose-price"),                 # ungraded
    ("PSA 7", "cib-price"),
    ("PSA 7.5", "cib-price"),
    ("PSA 8", "new-price"),
    ("PSA 9", "graded-price"),
    ("PSA 9.5", "box-only-price"),
    ("PSA 10", "manual-only-price"),
    ("BGS 10", "bgs-10-price"),
    ("CGC 10", "condition-17-price"),
    ("SGC 10", "condition-18-price"),
])
def test_the_grade_maps_to_the_right_repurposed_column(grade, column):
    """🚨 THESE ARE VIDEO-GAME COLUMN NAMES REUSED FOR CARDS. `new-price` is a
    Grade 8, not a new card. `manual-only-price` is a PSA 10. `cib-price` is a
    Grade 7. Read at face value they price a raw card at the PSA 10 number -
    the same shape of error as the blanket Pokemon comp this repo just removed,
    and worse because it looks so plausible. Mapping is from their own Key
    Descriptions table, read 2026-08-22."""
    assert sc._column(grade) == column


def test_an_ungraded_card_never_reads_a_graded_column():
    assert sc._column(None) == sc.UNGRADED
    assert sc._column("") == sc.UNGRADED


# --- reading the title ------------------------------------------------------

def test_the_grade_is_read_wherever_it_sits():
    assert sc.grade_of("2016 Panini #15 Ben Roethlisberger PSA 10 Sports") == "PSA 10"
    assert sc.grade_of("Kevin Pillar 2018 Topps Chrome Refractor") is None


def test_seller_noise_is_stripped_but_the_set_is_not():
    """The set name is the second-strongest signal after the player, so the
    query stays close to the title - only hype comes out."""
    q = sc.query_for("MERRILL KELLY TOPPS CHROME REFRACTOR PARALLEL MINT L@@K")
    assert "Topps" in q.title() or "topps" in q.lower()
    assert "refractor" in q.lower()
    assert "parallel" not in q.lower() and "mint" not in q.lower()


# --- narrowing --------------------------------------------------------------

def _p(name, console, pid="1"):
    return {"id": pid, "product-name": name, "console-name": console}


def test_off_sport_products_are_dropped():
    got = sc._narrow("Shane Bieber Topps Chrome Refractor", [
        _p("Barbaric Bieber [Red] #13a", "2024 Garbage Pail Kids Chrome"),
        _p("Shane Bieber [Refractor] #HMT59", "Baseball Cards 2018 Topps Chrome"),
    ])
    assert len(got) == 1 and "Shane Bieber" in got[0]["product-name"]


def test_the_parallel_must_match_exactly_in_both_directions():
    """🚨 A bare "REFRACTOR" is the base refractor, NOT the Gold one. Requiring
    only that the title's words appear in the bracket lets every colour through
    and picks a dearer card than the listing ever claimed."""
    prods = [
        _p("Kyle Seager [Refractor] #159", "Baseball Cards 2018 Topps Chrome"),
        _p("Kyle Seager [Gold Refractor] #159", "Baseball Cards 2018 Topps Chrome"),
        _p("Kyle Seager [Blue Refractor] #159", "Baseball Cards 2018 Topps Chrome"),
    ]
    plain = sc._narrow("Kyle Seager 2018 Topps Chrome Refractor", prods)
    assert [p["product-name"] for p in plain] == ["Kyle Seager [Refractor] #159"]

    gold = sc._narrow("Kyle Seager 2018 Topps Chrome Gold Refractor", prods)
    assert [p["product-name"] for p in gold] == ["Kyle Seager [Gold Refractor] #159"]


def test_a_filter_that_empties_the_list_is_dropped():
    """A title that disagrees with every product is a title we misread, not a
    card that does not exist."""
    prods = [_p("Josh Donaldson [Refractor] #8", "Baseball Cards 2018 Topps Chrome")]
    got = sc._narrow("Josh Donaldson 1994 Topps Chrome Refractor", prods)
    assert got == prods                    # the 1994 filter matched nothing -> ignored


# --- the decision under ambiguity -------------------------------------------

def _comp(prices, grade=None, priced=True):
    cands = [sc.Candidate(product_id=str(i), name=f"Player [P{i}] #{i}",
                          set_name="Baseball Cards 2018 Topps Chrome",
                          price=p, volume=500)
             for i, p in enumerate(prices)]
    return sc.SportsComp(query="q", grade=grade, candidates=cands, priced=priced)


def test_a_certain_loss_is_a_PASS_without_needing_the_photo():
    """🚨 THE RULE THAT WORKS WHEN THE IDENTITY DOES NOT. Only 3 of 22 real
    titles pinned to one product - the year and card number are printed on the
    card, not in the title. But if even the DEAREST parallel is worth less than
    the current bid, the listing loses money whichever card it turns out to be.
    No identity required."""
    v = sc.verdict("t", ask=40.0, comp=_comp([2.10, 6.40, 11.00]))
    assert v.verdict == sc.PASS
    assert "whichever card it is" in v.why


def test_a_certain_win_is_a_CHASE_without_needing_the_photo():
    v = sc.verdict("t", ask=5.0, comp=_comp([61.00, 240.00]))
    assert v.verdict == sc.CHASE


def test_the_undecided_middle_is_sent_to_the_photo():
    v = sc.verdict("t", ask=10.0, comp=_comp([4.00, 90.00]))
    assert v.verdict == sc.LOOK
    assert "photo" in v.why


def test_worth_the_same_as_the_bid_is_not_a_chase():
    """The auctioneer takes ~18% and shipping is real, so break-even is a
    loss. A CHASE has to clear a multiple, not a hair."""
    assert sc.verdict("t", ask=50.0, comp=_comp([52.00])).verdict != sc.CHASE


def test_a_thin_market_is_called_out():
    c = _comp([80.0])
    c = sc.SportsComp(query="q", grade=None, priced=True, candidates=[
        sc.Candidate(product_id="1", name="Player [Refractor] #1",
                     set_name="Baseball Cards 2018 Topps Chrome",
                     price=80.0, volume=3)])
    v = sc.verdict("t", ask=5.0, comp=c)
    assert "Thin" in v.why, "a price with nobody behind it is not a price"


def test_no_token_names_the_card_but_refuses_to_value_it():
    v = sc.verdict("t", ask=10.0, comp=_comp([None, None], priced=False))
    assert v.verdict == sc.LOOK
    assert "SPORTSCARDSPRO_TOKEN" in v.why


def test_no_match_is_not_a_verdict():
    v = sc.verdict("t", ask=10.0, comp=None)
    assert v.verdict == sc.UNKNOWN


# --- talking to the API (faked) ---------------------------------------------

class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.urls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        return self.responses.pop(0) if self.responses else FakeResp({"products": []})


def test_a_lookup_without_a_token_asks_only_the_search_endpoint():
    """No token means no price columns come back, so spending a call per
    candidate on /api/product would buy nothing."""
    s = FakeSession(FakeResp({"status": "success", "products": [
        _p("Kevin Pillar [Refractor] #11", "Baseball Cards 2018 Topps Chrome")]}))
    comp = sc.look_up("Kevin Pillar 2018 Topps Chrome Refractor", session=s,
                      env={}, use_cache=False)
    assert comp.n == 1 and comp.priced is False
    assert all("/api/products" in u for u in s.urls)


def test_a_lookup_with_a_token_reads_the_grade_column_in_dollars():
    s = FakeSession(
        FakeResp({"status": "success", "products": [
            _p("Ben Roethlisberger #15", "Football Cards 2016 Panini Absolute")]}),
        FakeResp({"status": "success", "manual-only-price": 4325,
                  "loose-price": 380, "sales-volume": 44}),
    )
    comp = sc.look_up("2016 Panini #15 Ben Roethlisberger PSA 10", session=s,
                      env={"SPORTSCARDSPRO_TOKEN": "t" * 40}, use_cache=False)
    assert comp.priced and comp.grade == "PSA 10"
    assert comp.candidates[0].price == 43.25      # manual-only-price, in dollars
    assert comp.candidates[0].ungraded == 3.80
    assert comp.candidates[0].volume == 44


def test_a_dead_source_is_not_an_empty_result():
    """🚨 None means "could not ask", never "worthless card"."""
    s = FakeSession(*[FakeResp({}, status=500)] * 9)
    assert sc.look_up("anything at all", session=s, env={},
                      use_cache=False) is None


def test_a_rejected_token_is_a_real_answer_not_a_blip():
    """A 401/403 will not get better by retrying - stop, do not hammer."""
    s = FakeSession(FakeResp({}, status=403))
    comp = sc.look_up("Kevin Pillar 2018 Topps Chrome", session=s,
                      env={"SPORTSCARDSPRO_TOKEN": "bad"}, use_cache=False)
    assert comp is not None and comp.n == 0
    assert len(s.urls) == 1


def test_the_product_url_is_the_page_the_price_came_from():
    """🚨 A comp you cannot click is a comp you have to take on trust.
    /game/<id> redirects to the real product page - verified live 2026-08-22."""
    c = sc.Candidate(product_id="72584", name="Michael Jordan #57",
                     set_name="Basketball Cards 1986 Fleer")
    assert c.url == "https://www.sportscardspro.com/game/72584"


def test_the_set_and_the_variant_both_disambiguate_a_pokemon_card():
    """🚨 "Alakazam #1" is a real card in Base Set, Base Set 2, Expedition,
    Shadowless AND Team Rocket. Filtering on the card number alone left nine
    candidates, and the ambiguity guard then refused every Pokemon card - which
    looked exactly like "the source has no data"."""
    assert sc._set_tokens("Base") == sc._set_tokens("Pokemon Base Set")
    assert sc._set_tokens("Base") != sc._set_tokens("Pokemon Base Set 2")
    assert sc._set_tokens("Neo Discovery") == sc._set_tokens("Pokemon Neo Discovery")


def test_no_stray_control_characters_in_this_module():
    """🚨 A patch script run through a shell heredoc collapsed a regex word
    boundary into byte 0x08 here on 2026-08-22 - the exact bug `pricebook`
    already guards against. The escape now lives in the WORD_END constant."""
    src = open(sc.__file__, "rb").read()
    for bad in (b"\x08", b"\x07", b"\x0b", b"\x0c"):
        assert bad not in src, f"control char {bad!r} in sportscards.py"

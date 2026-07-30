"""2026-07-30 additions: misspelling sweeps, seasonal hold notes, garage digest."""

import datetime as dt

from flipscout import hunt
from flipscout.garagesales import YardSaleSearch, digest
from flipscout.pricebook import match, search_terms


# --- misspellings: typo'd titles get no traffic, so they close cheap --------

def test_typod_titles_still_price_in_the_book():
    assert match("Cannon AE-1 35mm Film Camera w/ 50mm").model.key == "canon_ae1"
    assert match("Mitatoyo Micrometer Set 0-1 inch").model.key == "mitutoyo"
    assert match("Mititoyo digital caliper 6 inch").model.key == "mitutoyo"
    assert match("Starret Dial Caliper machinist").model.key == "starrett"
    assert match("Pokeman Emerald Version GBA").model.key == "pkmn_emerald"
    # Brand-agnostic includes already covered these; pin them so they stay.
    assert match("Polariod SX-70 Land Camera").model.key == "polaroid_sx70"
    assert match("Nikkon Coolpix P510 camera").model.key == "nikon_coolpix"
    assert match("Olympis Stylus Epic 35mm").model.key == "olympus_mju2"


def test_correct_spellings_still_match_after_the_folds():
    assert match("Mitutoyo Micrometer 103-135").model.key == "mitutoyo"
    assert match("Starrett No. 25 Dial Indicator").model.key == "dial_indicator"
    assert match("Pokemon Emerald authentic").model.key == "pkmn_emerald"


def test_misspelled_search_terms_are_swept():
    terms = search_terms()
    for t in ("cannon ae-1", "mitatoyo", "starret", "polariod sx-70", "pokeman"):
        assert t in terms


def test_fishing_fluke_still_dead_after_typo_folds():
    # The typo folds must not loosen the brand-noun corroboration rule.
    assert match("Zoom Winged Fluke - Gizzard Shad 5 pack") is None


# --- seasonal hold: the note rides every alert ------------------------------

def test_calculator_alert_carries_the_seasonal_hold_window():
    from tests.test_bidding_and_pricebook import CE_ROW, CFG, FakeHunter
    c = hunt.evaluate([CE_ROW], CFG, hunters=[FakeHunter([])])[0]
    assert "SEASONAL HOLD" in hunt.to_alert(c)["reason"]


# --- garage-sale digest ------------------------------------------------------

CARD = '''
<div class="listing"><h2 itemprop="name">
<a itemprop="url" href="https://www.yardsalesearch.com/yss-garage-sale.jsp?id=1">Huge Multi-Family Sale</a></h2>
<span itemprop="streetAddress">123 Oak Ln</span>,
<span itemprop="addressLocality">Fulshear</span>,
<meta itemprop="startDate" content="2026-08-01" />
<meta itemprop="endDate" content="2026-08-02" />
<h2 itemprop="name">
<a itemprop="url" href="https://www.yardsalesearch.com/yss-garage-sale.jsp?id=2">Old Estate Cleanout</a></h2>
<span itemprop="streetAddress">9 Elm St</span>,
<span itemprop="addressLocality">Katy</span>,
<meta itemprop="startDate" content="2026-07-01" />
<meta itemprop="endDate" content="2026-07-02" />
'''


def test_sales_parse_and_drop_already_over():
    got = YardSaleSearch("77441").sales(html=CARD, today=dt.date(2026, 7, 30))
    assert len(got) == 1
    assert got[0]["title"] == "Huge Multi-Family Sale"
    assert got[0]["city"] == "Fulshear"


def test_digest_reads_as_a_drive_list_not_bids():
    got = YardSaleSearch("77441").sales(html=CARD, today=dt.date(2026, 7, 30))
    body = digest(got, "77441")
    assert "77441" in body and "123 Oak Ln" in body
    assert "max bid" not in body.lower().replace("no max bids", "")


def test_garage_digest_posts_once_a_day_and_needs_a_zip(tmp_path):
    hb = tmp_path / "hb.json"
    cfg = {"zip": "77441", "heartbeat_file": str(hb)}
    posted = []

    class Feed:
        def sales(self):
            return [{"title": "Sale", "url": "u", "street": "s", "city": "c",
                     "start": "2026-08-01", "end": "2026-08-02"}]

    def notifier(alerts, content=""):
        posted.append(content)
        return ["webhook"]

    assert hunt.post_garage_digest(cfg, notifier, feed=Feed()) is True
    assert hunt.post_garage_digest(cfg, notifier, feed=Feed()) is False  # same day
    assert len(posted) == 1
    assert hunt.post_garage_digest({"zip": "", "heartbeat_file": str(hb)},
                                   notifier, feed=Feed()) is False

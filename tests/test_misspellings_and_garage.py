"""2026-07-30 additions: misspelling sweeps, seasonal hold notes, garage digest."""

import datetime as dt

from flipscout import hunt
from flipscout.garagesales import (YardSaleSearch, Gsalr, GarageSaleFinder,
                                   merged_sales, hot, digest, split_for_discord)
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


# --- gsalr + garagesalefinder (added 2026-07-31) ----------------------------
# Trimmed from the LIVE pages the day they shipped; if either site reshapes
# its markup, these fixtures say what the parser was built against.

GSALR_CARD = '''
<div id="l-38855171" class="listing"><span itemscope itemtype="http://schema.org/Event">
<div class="title"><h2 itemprop="name"><a href="https://gsalr.com/tomball-estate-sale-tomball-tx-38855171.html" target="_blank" class="sale-title" itemprop="url">Tomball Estate Sale!</a></h2></div>
<span itemprop="addressLocality">Tomball</span>,&nbsp;<span itemprop="addressRegion">TX</span>
<meta itemprop="startDate" content="2026-08-08"><meta itemprop="endDate" content="2026-08-09">
<div class="description" itemprop="description">Furniture, sewing machines, and a Fluke 87 multimeter.</div>
</span>
<span itemscope itemtype="http://schema.org/Event">
<div class="title"><h2 itemprop="name"><a href="https://gsalr.com/old-sale-tx-1.html" target="_blank" class="sale-title" itemprop="url">Long Gone Sale</a></h2></div>
<span itemprop="addressLocality">Katy</span>
<meta itemprop="startDate" content="2026-07-01"><meta itemprop="endDate" content="2026-07-02">
<div class="description" itemprop="description">nothing</div>
</span>
'''

GSF_CARD = '''
<div id="d-21693135" class="row collapse record">
<meta itemprop="startDate" content="2026-08-01"><meta itemprop="endDate" content="2026-08-02">
<div class="sale-address"><strong><span itemprop="address" class="sale-click">19222 TX-249, Houston, TX 77070</span></strong></div>
<div class="sale-title text-left" itemprop="name"><h2><a class="sale-url" href="https://garagesalefinder.com/s/N9N0G/19222-tx249" target="_blank">Prince of Peace Back to School Garage Sale</a></h2></div>
<div class="clearfix sale-desc text-left hide" itemprop="description">
Clothes, toys, and lots of misc.
</div></div>
'''


def test_gsalr_parses_and_drops_already_over():
    got = Gsalr("katy-tx").sales(html=GSALR_CARD, today=dt.date(2026, 7, 31))
    assert len(got) == 1
    assert got[0]["title"] == "Tomball Estate Sale!"
    assert got[0]["city"] == "Tomball"
    assert got[0]["start"] == "2026-08-08"
    assert "Fluke 87" in got[0]["desc"]
    assert got[0]["source"] == "gsalr"


def test_garagesalefinder_parses_zip_page():
    got = GarageSaleFinder("77441").sales(html=GSF_CARD, today=dt.date(2026, 7, 31))
    assert len(got) == 1
    assert got[0]["title"].startswith("Prince of Peace")
    assert got[0]["street"] == "19222 TX-249, Houston, TX 77070"
    assert got[0]["end"] == "2026-08-02"


class _Fixed:
    def __init__(self, name, rows):
        self.name, self.rows = name, rows

    def sales(self):
        return [dict(r) for r in self.rows]


def test_merged_sales_dedupes_the_shared_backend():
    # gsalr and garagesalefinder are the same EstateSales.NET family: the
    # Richmond estate sale appeared on BOTH the day this shipped. One line in
    # the digest, and the copy WITH the street wins.
    a = _Fixed("gsalr", [{"title": "Richmond Estate Sale!", "url": "g1",
                          "street": "", "city": "Richmond", "start": "2026-08-01", "end": ""}])
    b = _Fixed("garagesalefinder", [{"title": "Richmond Estate Sale!", "url": "f1",
                                     "street": "11011 Brighton Gardens Dr", "city": "",
                                     "start": "2026-08-01", "end": ""}])
    got = merged_sales([a, b])
    assert len(got) == 1
    assert got[0]["street"] == "11011 Brighton Gardens Dr"


def test_merged_sales_keeps_distinct_generic_titles():
    # "Garage Sale" is too generic to dedupe on alone - different city or
    # weekend means a different sale.
    a = _Fixed("gsalr", [{"title": "Garage Sale", "url": "g1", "street": "",
                          "city": "Katy", "start": "2026-08-01", "end": ""}])
    b = _Fixed("gsalr", [{"title": "Garage Sale", "url": "g2", "street": "",
                          "city": "Spring", "start": "2026-08-01", "end": ""}])
    assert len(merged_sales([a, b])) == 2


def test_hot_prefers_book_model_over_category_word():
    row = {"title": "Estate sale", "desc": "Fluke 87 multimeter, cameras, tools"}
    assert "Fluke 87" in hot(row)           # exact book model, not just "cameras"
    assert hot({"title": "Moving sale", "desc": "computers and laptops"}) == "computers"
    assert hot({"title": "Yard sale", "desc": "clothes and toys"}) == ""


def test_digest_floats_hot_sales_to_the_top():
    sales = [
        {"title": "Plain Clothes Sale", "url": "u1", "street": "1 A St", "city": "Katy",
         "start": "2026-08-01", "end": "", "desc": "clothes"},
        {"title": "Nerd Estate Sale", "url": "u2", "street": "2 B St", "city": "Fulshear",
         "start": "2026-08-02", "end": "", "desc": "TI-84 Plus CE calculators and cameras"},
    ]
    body = digest(sales, "77441")
    assert "🎯" in body
    assert body.index("Nerd Estate Sale") < body.index("Plain Clothes Sale")
    assert "1 mention book territory" in body


def test_digest_shows_what_each_sale_has():
    # Leron, 7/31: titles+links alone are useless - the digest must carry the
    # description so he can judge the drive from Discord.
    sales = [{"title": "Estate Sale", "url": "u1", "street": "1 A St", "city": "Katy",
              "start": "2026-08-01", "end": "", "desc": "Tools, furniture, old cameras"}]
    body = digest(sales, "77441")
    assert "↳ Tools, furniture, old cameras" in body


def test_digest_expand_hook_refreshes_hot_flags():
    # The truncated list-page desc hides the TI-84; the detail page names it.
    sales = [{"title": "Boring Sale", "url": "u1", "street": "", "city": "Katy",
              "start": "2026-08-01", "end": "", "desc": "misc household"}]

    def expand(row):
        row["desc"] = "misc household plus a TI-84 Plus CE calculator"

    body = digest(sales, "77441", expand=expand)
    assert "🎯" in body and "TI-84" in body


def test_split_for_discord_keeps_sale_blocks_whole():
    body = digest(
        [{"title": f"Sale number {i} with a fairly long name attached", "url": f"https://x/{i}",
          "street": f"{i} Long Street Name Dr", "city": "Houston",
          "start": "2026-08-01", "end": "2026-08-02",
          "desc": "furniture, clothes, kitchenware, books, toys, tools, "
                  "holiday decorations and a whole lot of other things"}
         for i in range(12)], "77441")
    parts = split_for_discord(body, limit=1900)
    assert len(parts) > 1                       # it genuinely needed splitting
    for p in parts:
        assert len(p) <= 1900                   # every part clears notify's cap
    # No sale got cut in half: every bullet still has its ↳ items line.
    joined = "\n".join(parts)
    assert joined.count("• ") == joined.count("↳ ")


def test_yss_desc_is_parsed_and_cleaned():
    card = '''<h2 itemprop="name">
<a itemprop="url" href="https://www.yardsalesearch.com/x?id=1">Sale</a></h2>
<span itemprop="addressLocality">Katy</span>,
<meta itemprop="startDate" content="2026-08-01" />
<meta itemprop="endDate" content="2026-08-02" />
<span itemprop="description" class="eventdesc">Cypress TWO DAY SALE! tools and cameras&hellip;&nbsp;<a href="https://x">Read&nbsp;More&nbsp;&rarr;</a></span>
'''
    got = YardSaleSearch("77441").sales(html=card, today=dt.date(2026, 7, 31))
    assert got[0]["desc"].startswith("Cypress TWO DAY SALE")
    assert "Read" not in got[0]["desc"]


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

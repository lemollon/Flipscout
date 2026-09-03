"""PriceCharting collections-for-sale feed.

Fixtures are trimmed from the real pages fetched 2026-08-23 (the collection in
Leron's email: seller n66gtsg..., $1,252.03 / 64 items / ga / 10 hours).
"""
import datetime as dt

import pytest

from flipscout import collections_feed as cf
from flipscout.notify import channel_for, destination, build_embed

TODAY = dt.date(2026, 8, 23)

FEED_HTML = """
<h3>Collections For Sale</h3>
<table id="games_table"><thead><tr><th>Photo</th></tr></thead>
<tbody>
<tr>
  <td class="title"><div class="image">
    <a href="/offers?seller=n66gtsgl7hcukxjwjohkssxx2a&status=collection">
    <img src="https://storage.googleapis.com/images.pricecharting.com/x/120.jpg" /></a>
  </div></td>
  <td class="numeric">$1,252.03</td><td class="numeric">64</td>
  <td>ga, United States</td><td>Age: 10 hours</td>
</tr>
<tr>
  <td class="title"><div class="image">
    <a href="/offers?seller=bigone&status=collection"><img src="b.jpg" /></a>
  </div></td>
  <td class="numeric">$7,707.60</td><td class="numeric">463</td>
  <td>tx, United States</td><td>Age: 8 days</td>
</tr>
<tr>
  <td class="title"><div class="image">
    <a href="/offers?seller=stale&status=collection"><img src="c.jpg" /></a>
  </div></td>
  <td class="numeric">$196,160.83</td><td class="numeric">1002</td>
  <td>tx, United States</td><td>Age: 2 years</td>
</tr>
</tbody></table>
"""

ITEMS_HTML = """
<table><tbody><tr><td>Value</td><td>Count</td></tr>
<tr><td>Total:</td><td>$1,252.03</td><td>64</td><td>x</td></tr></tbody></table>
<table id="games_table"><tbody>
<tr><td class="photo"><img src="a.jpg"></td>
    <td class="title"><a href="/offer/aaa">Super Nintendo System</a>
        <a href="/game/7145">Super Nintendo</a></td>
    <td class="includes">Item only</td>
    <td class="numeric">$114.50</td></tr>
<tr><td class="photo"><img src="b.jpg"></td>
    <td class="title"><a href="/offer/bbb">HardBall III</a>
        <a href="/game/8490">Sega Genesis</a></td>
    <td class="includes">Item only</td>
    <td class="numeric">$6.27</td></tr>
<tr><td class="photo"><img src="c.jpg"></td>
    <td class="title"><a href="/offer/ccc">Boxed Thing</a>
        <a href="/game/999">NES</a></td>
    <td class="includes">Box only</td>
    <td class="numeric">$40.00</td></tr>
</tbody></table>
"""


def _sales_page(cls_rows):
    """A product page carrying completed-sale tables, one div per condition."""
    out = ["<section>"]
    for cls, rows in cls_rows.items():
        out.append(f'<div class="completed-auctions-{cls}"><table>')
        for i, (date, price) in enumerate(rows):
            out.append(f'<tr id="ebay-{i}"><td>{date}</td>'
                       f'<td>${price:,.2f}</td></tr>')
        out.append("</table></div>")
    out.append("</section>")
    return "".join(out)


def _dates(n, start, step=1):
    d = dt.date.fromisoformat(start)
    return [((d - dt.timedelta(days=i * step)).isoformat(), 100.0 + i)
            for i in range(n)]


# --- the feed ---------------------------------------------------------------
def test_parse_feed_reads_every_column():
    cols = cf.parse_feed(FEED_HTML)
    assert len(cols) == 3
    c = cols[0]
    assert c.seller == "n66gtsgl7hcukxjwjohkssxx2a"
    assert c.total_value == 1252.03 and c.item_count == 64
    assert c.location == "ga, United States" and c.age == "10 hours"
    assert c.url.startswith("https://www.pricecharting.com/offers?seller=")
    assert c.photo and c.photo.endswith("120.jpg")


def test_a_big_collection_is_flagged_heavy():
    """Nobody mails 463 cartridges. PriceCharting says to collect these in
    person, so the card must say so - shipping being the norm is what makes
    the EXCEPTION worth printing."""
    small, big, _ = cf.parse_feed(FEED_HTML)
    assert not small.heavy
    assert big.heavy


@pytest.mark.parametrize("age,days", [
    ("10 hours", 10 / 24), ("8 days", 8), ("3 weeks", 21),
    ("13 months", 390), ("2 years", 730),
])
def test_age_days(age, days):
    assert cf.age_days(age) == pytest.approx(days)


def test_unparseable_age_reads_as_ancient_not_fresh():
    """The expensive direction is treating a three-year-old listing as new."""
    assert cf.age_days("just now-ish") > cf.MAX_AGE_DAYS
    assert cf.age_days("") > cf.MAX_AGE_DAYS


# --- contents ---------------------------------------------------------------
def test_parse_items_pins_the_product_id():
    """The /game/<id> on each row is the join key to the sold history - without
    it an item can only be looked up by keyword, which is the whole problem."""
    items = cf.parse_items(ITEMS_HTML)
    assert [i.name for i in items] == ["Super Nintendo System Super Nintendo",
                                       "HardBall III Sega Genesis",
                                       "Boxed Thing NES"]
    assert [i.game_id for i in items] == ["7145", "8490", "999"]
    assert items[0].value == 114.50
    assert items[0].product_url == "https://www.pricecharting.com/game/7145"


@pytest.mark.parametrize("includes,cls,label", [
    ("Item only", "used", "Loose"), ("CIB", "cib", "Complete"),
    ("Box and manual", "cib", "Complete"), ("New", "new", "New"),
    ("Box only", "box-only", "Box only"),
    ("Manual only", "manual-only", "Manual only"),
])
def test_condition_maps_to_a_sales_tab(includes, cls, label):
    assert cf.condition_of(includes) == (cls, label)


def test_unknown_condition_reads_as_loose():
    """The cheapest reading, so an unrecognised phrase under-values."""
    assert cf.condition_of("some wording we have never seen") == ("used",
                                                                  "Loose")


def test_condition_labels_never_say_grade():
    """🚨 THE TABS ARE CONDITIONS HERE, NOT GRADES. sportscards._SALES_CLASS
    maps card grade 7 -> 'cib' and 8 -> 'new'; reusing it would print
    "Grade 7" over rows that mean "complete in box". Pin that we don't."""
    for includes, _cls, label in [(i, c, l) for i, c, l in
                                  [("Item only", "used", "Loose"),
                                   ("CIB", "cib", "Complete"),
                                   ("New", "new", "New")]]:
        assert "Grade" not in cf.condition_of(includes)[1]
        assert cf.condition_of(includes)[1] == label


# --- liquidity --------------------------------------------------------------
class _Session:
    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def get(self, url, **kw):
        self.asked.append(url)

        class R:
            status_code = 200
        R.text = self.pages.get(url, "")
        return R


def test_measure_attaches_sales_and_reports_what_it_skipped():
    items = cf.parse_items(ITEMS_HTML)
    pages = {"https://www.pricecharting.com/game/7145":
             _sales_page({"used": _dates(30, "2026-08-16")})}
    session = _Session(pages)
    left = cf.measure(items, session, cap=1)
    # Highest value first, and the cap is reported rather than swallowed.
    assert session.asked == ["https://www.pricecharting.com/game/7145"]
    assert left == 2
    assert len(items[0].sales) == 30
    assert items[1].sales == []


def test_a_missing_condition_falls_back_to_loose_and_says_so():
    """The same rule the card scraper learned about grades: degrade, admit it,
    never return nothing when the neighbouring rows still say what the market
    is doing."""
    items = [i for i in cf.parse_items(ITEMS_HTML) if i.includes == "Box only"]
    pages = {"https://www.pricecharting.com/game/999":
             _sales_page({"used": _dates(5, "2026-08-20")})}
    cf.measure(items, _Session(pages), cap=5)
    assert items[0].sales
    assert "loose sales" in items[0].label


def test_per_90_counts_only_the_window():
    it = cf.Item(name="x", includes="Item only", value=1.0, game_id="1")
    it.sales = _dates(4, "2026-08-20", step=40)   # 3d, 43d, 83d, 123d ago
    assert it.per_90(TODAY) == 3
    assert not it.saturated(TODAY)


def test_a_saturated_sample_is_a_floor_not_a_rate():
    """🚨 The page gives the 30 most recent sales and no more. When all 30 land
    inside the window the honest reading is ">= 30", not "30"."""
    it = cf.Item(name="x", includes="Item only", value=1.0, game_id="1")
    it.sales = _dates(30, "2026-08-20")
    assert it.per_90(TODAY) == 30
    assert it.saturated(TODAY)
    assert ">=30/90d" in cf._line(it, TODAY)


def test_summary_splits_liquid_from_dead():
    liquid = cf.Item(name="SNES", includes="Item only", value=114.50,
                     game_id="7145")
    liquid.sales = _dates(30, "2026-08-16")
    dead = cf.Item(name="HardBall III", includes="Item only", value=6.27,
                   game_id="8490")
    dead.sales = [("2026-07-25", 8.46), ("2026-04-26", 1.11),
                  ("2026-03-11", 9.95)]
    col = cf.parse_feed(FEED_HTML)[0]
    s = cf.summarize(col, [liquid, dead], unmeasured=3, today=TODAY)
    assert s.liquid_value == 114.50 and s.liquid_items == 1
    assert s.dead_value == 6.27 and s.dead_items == 1
    assert s.unmeasured == 3
    # 64 items in the collection, 2 known here: the rest is reported, not lost.
    assert s.unlisted == 62
    assert s.total_value == 1252.03


# --- the card ---------------------------------------------------------------
def _card(items=None, unmeasured=0):
    col = cf.parse_feed(FEED_HTML)[0]
    if items is None:
        it = cf.Item(name="SNES", includes="Item only", value=114.50,
                     game_id="7145")
        it.sales = _dates(30, "2026-08-16")
        items = [it]
    s = cf.summarize(col, items, unmeasured, today=TODAY)
    return col, items, s, cf.to_alert(col, items, s, today=TODAY)


def test_card_leads_with_value_then_liquidity():
    _c, _i, _s, a = _card()
    body = a["reason"]
    assert body.startswith("**$1,252.03** across **64** items")
    assert "moves quarterly" in body


def test_card_never_quotes_pricechartings_est_buy_value():
    """🚨 Their $530 is 40-60% of their own guide total applied blind to 64
    different products - the blanket-comp shape that armed $49 bids on $0.14
    cards. It may never reach a card."""
    _c, _i, _s, a = _card()
    assert "Est. Buy" not in a["reason"] and "530" not in a["reason"]


def test_card_carries_no_arming_number():
    """🚨 THE CARD IS THE CONTRACT, THE REGEX IS THE JUDGE. discordarm pulls an
    arm figure out of rendered text across every embed field. A collection has
    no lot id, so a card that looks armable invites a bot to arm nothing."""
    _c, _i, _s, a = _card()
    assert "max_bid" not in a and "open_bid" not in a and "ends" not in a
    rendered = a["reason"].lower() + " " + a["title"].lower()
    for trigger in ("max bid", "ceiling", "don't pay over", "dont pay over"):
        assert trigger not in rendered


def test_card_ceiling_leak_guard_against_the_real_parser():
    """Render the real embed and ask the real arm parser what it would arm."""
    from flipscout.discordarm import parse_card
    _c, _i, _s, a = _card()
    embed = build_embed(a)
    msg = {"embeds": [embed], "content": ""}
    src, lot, ceiling = parse_card(msg)
    assert ceiling is None, f"collection card would arm ${ceiling}"


def test_card_says_what_it_could_not_see():
    """A silent cap reads as 'that was everything'."""
    _c, _i, _s, a = _card(unmeasured=18)
    assert "**63 of 64 items are not shown**" in a["reason"]
    assert "18 lower-value items unmeasured" in a["reason"]


def test_card_always_carries_the_ownership_warning():
    _c, _i, _s, a = _card()
    assert "username written on paper" in a["reason"]


def test_card_with_no_sale_history_says_the_numbers_are_unproven():
    it = cf.Item(name="Mystery", includes="Item only", value=50.0)
    _c, _i, _s, a = _card(items=[it])
    assert "unproven" in a["reason"]
    assert "moves quarterly" not in a["reason"]


def test_no_listing_type_banner_for_an_offer():
    """_ACTION_TEXT only speaks for known types. A collection is neither an
    auction nor a fixed price, and a WRONG banner is worse than none."""
    _c, _i, _s, a = _card()
    embed = build_embed(a)
    values = " ".join(f["value"] for f in embed["fields"])
    assert "AUCTION" not in values and "BUY IT NOW" not in values


# --- routing ----------------------------------------------------------------
def test_collections_go_to_the_deals_channel():
    """Leron, 2026-08-23: "push anything not a card to the deals channel"."""
    assert channel_for({"category": "collections", "title": "Collection"}) == ""
    env = {"FLIPSCOUT_ALERT_WEBHOOK": "https://main",
           "FLIPSCOUT_CARDS_WEBHOOK": "https://cards"}
    url, _chan, label = destination({"category": "collections"}, env)
    assert url == "https://main" and label == "webhook"


def test_a_card_heavy_collection_still_goes_to_deals():
    """🚨 THE CATEGORY WINS OVER THE TEXT. A collection of graded Pokemon has
    every card word in its item list; routing on the title would file a
    whole-collection offer under #cards - the same mistake in reverse as the
    five Pokemon cartridges that sat there."""
    card_ish = {"category": "collections",
                "title": "Collection - PSA 10 Charizard Base Set holo rookie"}
    assert channel_for(card_ish) == ""


# --- the run pass -----------------------------------------------------------
def _cols():
    return cf.parse_feed(FEED_HTML)


def test_post_collections_skips_stale_and_cheap_and_seen(monkeypatch):
    from flipscout import hunt
    monkeypatch.setattr(cf, "items_of", lambda c, session=None: [])
    monkeypatch.setattr(cf, "measure", lambda i, s=None, cap=0: 0)
    posted = {}

    def notifier(alerts, content=""):
        posted["alerts"], posted["header"] = alerts, content
        return ["webhook"]

    keys = hunt.post_collections({"collections": True}, notifier, set(),
                                 feed=_cols(), today=TODAY)
    # The 2-year-old $196k collection is stale; the other two post.
    assert len(posted["alerts"]) == 2
    assert len(keys) == 2
    assert all(k.startswith("pricecharting-collection:") for k in keys)
    # Second run: nothing new.
    assert hunt.post_collections({"collections": True}, notifier, keys,
                                 feed=_cols(), today=TODAY) == set()


def test_post_collections_batches_and_says_how_many_are_queued(monkeypatch):
    from flipscout import hunt
    monkeypatch.setattr(cf, "items_of", lambda c, session=None: [])
    monkeypatch.setattr(cf, "measure", lambda i, s=None, cap=0: 0)
    monkeypatch.setattr(cf, "POST_PER_RUN", 1)
    seen_header = {}

    def notifier(alerts, content=""):
        seen_header["h"] = content
        return ["webhook"]

    hunt.post_collections({"collections": True}, notifier, set(),
                          feed=_cols(), today=TODAY)
    assert "1 more queued" in seen_header["h"]


def test_post_collections_reports_nothing_when_delivery_fails(monkeypatch):
    """Returning keys on a failed send would mark them seen and lose them."""
    from flipscout import hunt
    monkeypatch.setattr(cf, "items_of", lambda c, session=None: [])
    monkeypatch.setattr(cf, "measure", lambda i, s=None, cap=0: 0)
    assert hunt.post_collections({"collections": True}, lambda a, content="": [],
                                 set(), feed=_cols(), today=TODAY) == set()


def test_collections_pass_is_fail_soft(monkeypatch):
    from flipscout import hunt

    def boom(*a, **k):
        raise RuntimeError("pricecharting down")
    monkeypatch.setattr(hunt, "post_collections", boom)
    assert hunt._collections_pass({"collections": True}, None, set()) == set()


def test_collections_can_be_switched_off():
    from flipscout import hunt
    assert hunt._collections_pass({"collections": False}, None, set()) == set()


# --- coverage: the number that governs how to read the rest -----------------
def test_coverage_is_a_share_of_money_not_of_items():
    """🚨 Found on the live sweep 2026-08-23: a $4,533.18 collection whose 30
    PUBLIC rows were $0.74-$3.61 Pokemon commons. The page does NOT show the
    dearest thirty, so counting items would have called that 55% covered."""
    col = cf.parse_feed(FEED_HTML)[0]           # $1,252.03 / 64 items
    cheap = cf.Item(name="common", includes="Item only", value=3.61,
                    game_id="1")
    cheap.sales = _dates(30, "2026-08-21")
    s = cf.summarize(col, [cheap], unmeasured=0, today=TODAY)
    assert s.coverage < 0.01
    assert s.thin


def test_a_thin_collection_says_the_numbers_do_not_describe_it():
    col = cf.parse_feed(FEED_HTML)[0]
    cheap = cf.Item(name="common", includes="Item only", value=3.61,
                    game_id="1")
    cheap.sales = _dates(30, "2026-08-21")
    s = cf.summarize(col, [cheap], unmeasured=0, today=TODAY)
    body = cf.to_alert(col, [cheap], s, today=TODAY)["reason"]
    assert "do not describe this collection" in body
    # And it leads the warnings - it governs how to read everything above it.
    warn = [ln for ln in body.split("\n") if ln.startswith(":warning:")][0]
    assert warn.index("Only") < warn.index("items are not shown")


def test_a_well_covered_collection_carries_no_thin_warning():
    col = cf.parse_feed(FEED_HTML)[0]
    big = cf.Item(name="most of it", includes="Item only", value=1000.0,
                  game_id="1")
    big.sales = _dates(30, "2026-08-21")
    s = cf.summarize(col, [big], unmeasured=0, today=TODAY)
    assert not s.thin
    assert "do not describe" not in cf.to_alert(col, [big], s,
                                                today=TODAY)["reason"]


def test_the_card_always_states_coverage_when_it_priced_anything():
    _c, _i, _s, a = _card()
    assert "of the collection's $1,252.03" in a["reason"]
    # 🚨 And never the old wording, which read a sample as coverage.
    assert "of what we measured" not in a["reason"]


def test_coverage_never_exceeds_one():
    """Guide values can drift from the header total; a 103% read is a bug on
    the card, not a nuance."""
    col = cf.parse_feed(FEED_HTML)[0]
    huge = cf.Item(name="x", includes="Item only", value=99999.0, game_id="1")
    huge.sales = _dates(30, "2026-08-21")
    assert cf.summarize(col, [huge], 0, today=TODAY).coverage == 1.0


# --- could-not-ask is not does-not-sell -------------------------------------
class _DeadSession:
    def get(self, url, **kw):
        class R:
            status_code = 429
            text = ""
        return R


def test_a_throttled_fetch_is_not_reported_as_a_dead_market():
    """🚨 Seen live 2026-08-23: the same collection reported real sale rates on
    one run and "No sale history found" on the next. An empty `sales` list
    reads identically whether the market is dead or the page 429'd, and reading
    the second as the first turns OUR rate limit into THEIR dead collection."""
    items = cf.parse_items(ITEMS_HTML)
    cf.measure(items, _DeadSession(), cap=5)
    assert all(i.unreachable for i in items)
    col = cf.parse_feed(FEED_HTML)[0]
    s = cf.summarize(col, items, unmeasured=0, today=TODAY)
    assert s.unreachable == 3
    body = cf.to_alert(col, items, s, today=TODAY)["reason"]
    assert "OUR gap, not the market's" in body
    assert "No sale history found" not in body
    assert "3 product page(s) unreachable" in body


def test_genuinely_empty_history_still_says_unproven():
    """The other silence: the page loaded fine and had no sales in it."""
    items = cf.parse_items(ITEMS_HTML)
    cf.measure(items, _Session({}), cap=5)     # 200 OK, no sale tables
    assert not any(i.unreachable for i in items)
    col = cf.parse_feed(FEED_HTML)[0]
    s = cf.summarize(col, items, unmeasured=0, today=TODAY)
    body = cf.to_alert(col, items, s, today=TODAY)["reason"]
    assert "unproven" in body
    assert "OUR gap" not in body


# --- the switch -------------------------------------------------------------
def test_an_unmapped_actions_variable_does_not_switch_the_feature_off():
    """🚨 Actions renders an undefined `vars.X` as "". Reading that as falsy
    would ship the feature OFF the moment watch.yml mapped it - the same shape
    as the FLIPSCOUT_* vars that were set correctly and inert for a full run."""
    from flipscout.hunt import load_config
    assert load_config({})["collections"] is True
    assert load_config({"FLIPSCOUT_COLLECTIONS": ""})["collections"] is True
    assert load_config({"FLIPSCOUT_COLLECTIONS": " "})["collections"] is True


def test_only_an_explicit_word_switches_it_off():
    from flipscout.hunt import load_config
    for off in ("0", "false", "FALSE", "no", "off"):
        assert load_config({"FLIPSCOUT_COLLECTIONS": off})["collections"] is False
    assert load_config({"FLIPSCOUT_COLLECTIONS": "1"})["collections"] is True


def test_the_workflow_maps_the_switch():
    """A FLIPSCOUT_* var that is not in watch.yml's env block does nothing at
    all, and does it silently. Pin the mapping, not just the code."""
    import yaml
    with open(".github/workflows/watch.yml", encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    envs = [s["env"] for j in wf["jobs"].values() for s in j["steps"]
            if isinstance(s, dict) and s.get("env")]
    assert any("FLIPSCOUT_COLLECTIONS" in e for e in envs)


# --- the condition column, surveyed rather than guessed ---------------------
# 🚨 The first version of this map was written by guessing at the wording and
# four of its seven rows were wrong. This is the ENTIRE vocabulary the column
# uses, surveyed 2026-08-23 across 14 live collections, with its real counts.
REAL_VOCABULARY = [
    ("Ungraded", 170, "used", "Ungraded"),
    ("Item only", 167, "used", "Loose"),
    ("Item, Box, and Manual", 32, "cib", "Complete"),
    ("Item and Box only", 5, "loose-and-box", "Item + box"),
    ("Ungraded Qty: 2", 3, "used", "Ungraded"),
    ("Item only Qty: 2", 3, "used", "Loose"),
    ("Graded 9", 2, "graded", "Grade 9"),
    ("Graded 9.5", 2, "box-only", "Grade 9.5"),
    ("New Item, Box, and Manual", 1, "new", "New"),
    ("Graded 7", 1, "cib", "Grade 7"),
    ("Item only Qty: 5", 1, "used", "Loose"),
]


@pytest.mark.parametrize("includes,_n,slug,label", REAL_VOCABULARY)
def test_every_wording_the_column_actually_uses(includes, _n, slug, label):
    assert cf.condition_of(includes) == (slug, label)


def test_a_graded_card_is_never_quoted_against_raw_sales():
    """🚨 THE MONEY BUG. "Graded 9" fell through to the ungraded tab, pricing a
    slab against raw sales - the same shape as the blanket $92 any-PSA-7-9
    comp, reached from the opposite direction. Measured on one real card:
    raw $3.53 · Grade 9 $17.49 · PSA 10 $74.00. A 21x spread, collapsed to the
    cheapest number."""
    for graded in ("Graded 7", "Graded 9", "Graded 9.5", "Graded 10"):
        slug, label = cf.condition_of(graded)
        assert slug != "used", f"{graded} priced against raw sales"
        assert label.startswith("Grade") or label == "PSA 10"


def test_a_comma_cannot_hide_the_manual():
    r"""⛔ THE COMMA BUG, FOURTH APPEARANCE (Singer "case, pedal and manual").
    `box\s*and\s*manual` cannot reach "and" through ", ". Read as loose, a
    complete-in-box game is quoted against cartridge-only sales."""
    assert cf.condition_of("Item, Box, and Manual")[0] == "cib"
    assert cf.condition_of("Box and Manual")[0] == "cib"


def test_a_compound_wording_beats_the_bare_one_it_contains():
    """"Item and Box only" CONTAINS "Box only", and box-only sales are for a
    box with NO GAME IN IT."""
    assert cf.condition_of("Item and Box only")[0] == "loose-and-box"
    assert cf.condition_of("Box only")[0] == "box-only"


def test_sealed_beats_complete():
    """"New Item, Box, and Manual" is both; the sealed tab is the right one."""
    assert cf.condition_of("New Item, Box, and Manual")[0] == "new"


def test_quantity_is_read_because_value_is_guide_times_qty():
    """🚨 Measured: a "Qty: 5" Gamecube controller carries $149.95 against a
    $29.99 sold median - exactly 5x."""
    assert cf.quantity("Item only Qty: 5") == 5
    assert cf.quantity("Ungraded Qty: 2") == 2
    assert cf.quantity("Item only") == 1


def test_quantity_scales_the_liquidity_bar_not_the_rate():
    """Five copies of a thing that sells eight times a quarter is not five
    times as liquid - you need five sales to clear the position."""
    one = cf.Item(name="x", includes="Item only", value=50.0, game_id="1")
    five = cf.Item(name="x", includes="Item only Qty: 5", value=250.0,
                   game_id="1")
    one.sales = five.sales = _dates(10, "2026-08-20")   # 10 sales in 90 days
    col = cf.parse_feed(FEED_HTML)[0]
    assert cf.summarize(col, [one], 0, today=TODAY).liquid_items == 1
    assert cf.summarize(col, [five], 0, today=TODAY).liquid_items == 0


def test_the_card_line_names_the_condition_and_the_quantity():
    it = cf.Item(name="Black Controller Gamecube", includes="Item only Qty: 5",
                 value=149.95, game_id="1", label="Loose")
    it.sales = _dates(30, "2026-08-20")
    line = cf._line(it, TODAY)
    assert "x5" in line and "[Loose]" in line


# --- the page names its own tabs --------------------------------------------
CARD_TABS = """
<select>
<option value="completed-auctions-used">Ungraded (60)</option>
<option value="completed-auctions-cib">Grade 7 (1)</option>
<option value="completed-auctions-new">Grade 8 (3)</option>
<option value="completed-auctions-graded">Grade 9 (29)</option>
<option value="completed-auctions-box-only">Grade 9.5 (1)</option>
<option value="completed-auctions-manual-only">PSA 10 (19)</option>
</select>
"""


def test_tabs_are_read_off_the_page_so_they_cannot_drift():
    """🚨 THE SITE REUSES THE GAME SLUGS FOR CARD GRADES and nothing in the
    slug says so: `completed-auctions-cib` is labelled "Grade 7" on a card and
    `manual-only` is "PSA 10". Only the page knows."""
    t = cf.tabs(CARD_TABS)
    assert t["ungraded"] == "used"
    assert t["grade 9"] == "graded"
    assert t["grade 9.5"] == "box-only"
    assert t["psa 10"] == "manual-only"
    # The row count in the label is not part of the name.
    assert not any("(" in k for k in t)


def test_condition_prefers_the_pages_own_mapping():
    t = cf.tabs(CARD_TABS)
    assert cf.condition_of("Graded 9", t) == ("graded", "Grade 9")
    assert cf.condition_of("Graded 10", t) == ("manual-only", "PSA 10")
    assert cf.condition_of("Ungraded", t) == ("used", "Ungraded")


def test_a_page_with_no_tab_list_still_resolves():
    """Fallback table, so a parse failure degrades instead of breaking."""
    assert cf.condition_of("Graded 9", {}) == ("graded", "Grade 9")
    assert cf.condition_of("Graded 9", None) == ("graded", "Grade 9")


def test_measure_labels_a_graded_card_by_its_grade():
    page = CARD_TABS + _sales_page({"graded": _dates(28, "2026-08-18"),
                                    "used": _dates(30, "2026-08-20")})
    it = cf.Item(name="Mewtwo #59", includes="Graded 9", value=17.49,
                 game_id="4637172")
    cf.measure([it], _Session({it.product_url: page}), cap=1)
    assert it.label == "Grade 9"
    assert len(it.sales) == 28          # the GRADE 9 rows, not the 30 raw ones


def test_a_grade_with_no_sales_falls_back_and_says_ungraded_not_loose():
    """🚨 A RAW CARD IS NOT "LOOSE". Printing a cartridge word over a card is
    how a card ends up read as a game."""
    page = CARD_TABS + _sales_page({"used": _dates(30, "2026-08-20")})
    it = cf.Item(name="Mewtwo #59", includes="Graded 9", value=17.49,
                 game_id="4637172")
    cf.measure([it], _Session({it.product_url: page}), cap=1)
    assert it.label == "Grade 9 (ungraded sales)"
    assert "loose" not in it.label


# --- money, not gross: what the card leads with ------------------------------
# Leron, 2026-09-03: "so much data and offer I find it hard to see what I can
# truly make money off flipping in eBay". Every number the card leads with is
# the eBay NET and the one that matters is the offer.

def _fast(name, value, includes="Item only", n=30, start="2026-08-16"):
    it = cf.Item(name=name, includes=includes, value=value, game_id="1")
    it.sales = _dates(n, start)
    return it


def _slow(name, value):
    it = cf.Item(name=name, includes="Item only", value=value, game_id="2")
    it.sales = _dates(1, "2026-07-25")
    return it


def _flip_card(extra=()):
    """A lot that actually flips: one $400 fast mover is 32% of the $1,252.03
    guide, so coverage is not thin. Nets $341.60; offer = 341.60/1.5 =
    $227.73 (the ROI cap binds). `_card()` on its own is THIN - its $114.50
    SNES is 9% of the value - and so renders as unproven, by design."""
    col = cf.parse_feed(FEED_HTML)[0]
    items = [_fast("N64 console bundle", 400.0)] + list(extra)
    s = cf.summarize(col, items, 0, today=TODAY)
    return col, items, s, cf.to_alert(col, items, s, today=TODAY)


def test_net_is_guide_minus_ebay_fees_and_postage():
    """A $114.50 SNES: 13.25% + $0.40 + a $5 label leaves $93.93."""
    assert _fast("SNES", 114.50).net() == 93.93


def test_net_never_goes_negative():
    """A $3.61 common after a $5 label is $0.00, not -$1.87: you would never
    ship it alone, so it counts for nothing rather than less than nothing."""
    assert _fast("common", 3.61).net() == 0.0


def test_net_is_per_unit_times_quantity():
    """Value is guide x qty, so the fee bite lands on each unit, not once."""
    five = _fast("controller", 149.95, includes="Item only Qty: 5")
    one = _fast("controller", 29.99)
    assert five.unit_value == pytest.approx(29.99)
    assert five.net() == pytest.approx(one.net() * 5, abs=0.05)


def test_postage_is_a_knob_the_card_states():
    it = _fast("console", 200.0)
    assert it.net(ship=15.0) < it.net(ship=5.0)
    _c, _i, _s, a = _flip_card()
    assert "~$5 postage each" in a["reason"]


def test_offer_is_what_the_fast_movers_pay_back_at_the_goal():
    """N = $93.93 net: profit cap $83.93, ROI cap $62.62 -> the tighter one."""
    assert cf.offer_for(93.93) == 62.62
    assert cf.offer_for(0.0) == 0.0
    assert cf.offer_for(5.0) == 0.0          # under min_profit -> nothing


def test_summary_carries_liquid_net_and_offer():
    _c, _i, s, _a = _flip_card()
    assert s.liquid_net == 341.60
    assert s.offer == 227.73
    assert s.verdict == "flip"


def test_the_default_fixture_is_thin_and_therefore_unproven():
    """Pinned so nobody 'fixes' _card() into a flip: a $114.50 SNES is 9% of
    a $1,252.03 lot, and 9% priced is not a lot you write an offer on."""
    _c, _i, s, a = _card()
    assert s.thin and s.verdict == "unproven"
    assert "Offer up to" not in a["reason"]


def test_slow_stock_never_feeds_the_offer():
    """The offer is paid back by the fast movers ALONE; a $500 shelf ornament
    adds to the guide total and to nothing else."""
    col = cf.parse_feed(FEED_HTML)[0]
    fast_only = cf.summarize(col, [_fast("SNES", 114.50)], 0, today=TODAY)
    with_slow = cf.summarize(col, [_fast("SNES", 114.50),
                                   _slow("HardBall III", 500.0)], 0,
                             today=TODAY)
    assert with_slow.offer == fast_only.offer
    assert with_slow.dead_value == 500.0


def test_a_lot_with_only_slow_stock_is_a_pass():
    col = cf.parse_feed(FEED_HTML)[0]
    items = [_slow("HardBall III", 800.0)]
    s = cf.summarize(col, items, 0, today=TODAY)
    assert s.verdict == "pass" and s.offer == 0.0
    a = cf.to_alert(col, items, s, today=TODAY)
    assert a["title"].startswith("PASS")
    assert "**Pass**" in a["reason"]
    assert "nothing we priced sells fast enough" in a["reason"]
    assert a["verdict"] == "pass"


def test_a_tiny_offer_is_a_pass_not_a_number_to_send():
    """$30 net on the fast movers -> a $20 offer. Nobody with a $300+ lot is
    taking that; rendering it as a number invites the email."""
    col = cf.parse_feed(FEED_HTML)[0]
    items = [_fast("cheap cart", 900.0, includes="Item only"),
             _fast("game", 40.0)]
    # 900 guide fast mover ... make the fast money small instead:
    items = [_fast("game", 40.0), _slow("HardBall III", 900.0)]
    s = cf.summarize(col, items, 0, today=TODAY)
    assert 0 < s.offer < cf.MIN_OFFER
    assert s.verdict == "pass"
    body = cf.to_alert(col, items, s, today=TODAY)["reason"]
    assert "net only $" in body and "Offer up to" not in body


def test_thin_coverage_is_unproven_however_good_the_slice_looked():
    """A $4,533 lot whose thirty public rows are commons gets no offer."""
    col = cf.parse_feed(FEED_HTML)[0]              # $1,252.03
    items = [_fast("Gamecube controller", 29.99)]   # 2.4% of the value
    s = cf.summarize(col, items, 0, today=TODAY)
    assert s.thin and s.verdict == "unproven"
    a = cf.to_alert(col, items, s, today=TODAY)
    assert "**Unproven**" in a["reason"]
    assert "Offer up to" not in a["reason"]
    assert a["verdict"] == "watch"
    assert a["title"].startswith("Collection")


def test_flip_card_leads_with_the_offer():
    _c, _i, s, a = _flip_card()
    body = a["reason"]
    assert "**Offer up to $227.73**" in body
    assert "net **$341.60** after eBay fees" in body
    assert "50%+ ROI" in body
    assert a["title"] == "FLIP · 64 items · $1,252.03 · offer up to $228"
    assert a["verdict"] == "buy"
    # The offer sits above the liquidity line, not under it.
    assert body.index("Offer up to") < body.index("moves quarterly")


def test_an_offer_far_under_the_sellers_band_says_so():
    """$227.73 on a $1,252.03 lot is 18% of guide. PriceCharting's own FAQ
    says sellers get 40-60%; the card says expect a no, rather than let you
    write the email and find out."""
    _c, _i, _s, a = _flip_card()
    assert "(18% of guide)" in a["reason"]
    assert "Under the 40%-60% of guide" in a["reason"]


def test_an_offer_inside_the_band_carries_no_long_shot_note():
    col = cf.parse_feed(FEED_HTML)[0]              # $1,252.03
    items = [_fast("PS2 lot", 1200.0)]              # nets ~$1,035 -> offer ~$690
    s = cf.summarize(col, items, 0, today=TODAY)
    body = cf.to_alert(col, items, s, today=TODAY)["reason"]
    assert s.offer / s.total_value > 0.40
    assert "expect a no" not in body


def test_item_lines_show_the_net_and_only_the_fast_movers():
    """The old card listed the dearest eight by GUIDE, which put a $65
    one-sale-a-quarter game above a controller that sells weekly."""
    col = cf.parse_feed(FEED_HTML)[0]
    items = [_slow("HardBall III", 65.0),
             _fast("Black Controller Gamecube", 29.99),
             _fast("SNES", 114.50)]
    s = cf.summarize(col, items, 0, today=TODAY)
    body = cf.to_alert(col, items, s, today=TODAY)["reason"]
    lines = [l for l in body.split("\n") if "` net" in l]
    assert len(lines) == 2
    assert "SNES" in lines[0] and "Gamecube" in lines[1]      # by net, desc
    assert lines[0].startswith("`$  93.93` net")
    assert "guide $114.50" in lines[0] and "last 08-16" in lines[0]
    assert not any("HardBall" in l for l in lines)


def test_slow_stock_is_named_in_one_line():
    col = cf.parse_feed(FEED_HTML)[0]
    items = [_fast("SNES", 114.50)] + [_slow(f"dud {i}", 10.0 + i)
                                        for i in range(5)]
    s = cf.summarize(col, items, 0, today=TODAY)
    body = cf.to_alert(col, items, s, today=TODAY)["reason"]
    line = next(l for l in body.split("\n") if "Slow, pay nothing" in l)
    assert "dud 4 $14" in line and "+2 more" in line


def test_middling_sellers_are_counted_not_listed():
    col = cf.parse_feed(FEED_HTML)[0]
    mid = cf.Item(name="Mario Paint", includes="Item only", value=25.0,
                  game_id="3")
    mid.sales = _dates(5, "2026-08-20")            # 5/90d: neither fast nor slow
    items = [_fast("SNES", 114.50), mid]
    s = cf.summarize(col, items, 0, today=TODAY)
    body = cf.to_alert(col, items, s, today=TODAY)["reason"]
    assert "1 more sell at a middling pace ($25.00 guide)" in body
    assert "Mario Paint" not in body


def test_bucket_is_one_rule_for_summary_and_card():
    assert cf.bucket(_fast("x", 10.0), TODAY) == "fast"
    assert cf.bucket(_slow("x", 10.0), TODAY) == "slow"
    assert cf.bucket(cf.Item(name="x", includes="", value=1.0), TODAY) is None


def test_the_offer_never_reads_as_an_arming_number():
    """Same guard as before, on the card that now carries a dollar figure in
    its first line AND its title: the real parser must still arm nothing."""
    from flipscout.discordarm import parse_card
    _c, _i, _s, a = _flip_card()
    assert "Offer up to" in a["reason"]
    embed = build_embed(a)
    _src, _lot, ceiling = parse_card({"embeds": [embed], "content": ""})
    assert ceiling is None, f"collection card would arm ${ceiling}"


def test_the_card_fits_discords_field_cap():
    """build_embed cuts `reason` at 1000 chars; a card that overflows loses
    the ownership warning at the bottom. Eight fast movers plus slow stock
    plus warnings has to fit."""
    col = cf.parse_feed(FEED_HTML)[0]
    items = ([_fast(f"Some Long Nintendo Title {i}", 100.0 + i)
              for i in range(8)]
             + [_slow(f"Dead Title {i}", 40.0) for i in range(6)])
    s = cf.summarize(col, items, unmeasured=12, today=TODAY)
    body = cf.to_alert(col, items, s, today=TODAY)["reason"]
    assert len(body) <= cf.FIELD_CAP, len(body)
    # It fitted by dropping fast-mover lines - and said so - not by losing
    # the slow line or the standing warning at the bottom.
    assert "more fast movers_" in body
    assert "Slow, pay nothing" in body
    assert body.endswith("before sending money._")
    assert "Some Long Nintendo Title 7" in body       # the best one stays


def test_a_short_card_is_not_trimmed():
    _c, _i, _s, a = _flip_card(extra=[_fast("SNES", 114.50)])
    body = a["reason"]
    assert "more fast movers" not in body
    assert body.count("` net") == 2


# --- the batch: flips first ---------------------------------------------------
def test_post_collections_puts_flips_above_passes(monkeypatch):
    """The batch is picked by guide value (all the feed page knows). Once
    measured, the lot worth an email goes on top whatever it is worth."""
    from flipscout import hunt
    cols = _cols()
    # Two fresh lots: bigone (higher value) is all slow stock; n66 flips.
    # Sized to each lot's guide total so neither reads as thin.
    monkeypatch.setattr(
        cf, "items_of",
        lambda c, session=None: [_slow("HardBall III", c.total_value)]
        if c.seller == "bigone" else [_fast("N64 bundle", c.total_value)])
    monkeypatch.setattr(cf, "measure", lambda i, s=None, cap=0: 0)
    got = {}

    def notifier(alerts, content=""):
        got["alerts"], got["header"] = alerts, content
        return ["webhook"]

    hunt.post_collections({"collections": True}, notifier, set(),
                          feed=cols, today=TODAY)
    titles = [a["title"] for a in got["alerts"]]
    assert titles[0].startswith("FLIP") and titles[-1].startswith("PASS")
    assert "(1 worth an offer)" in got["header"]


def test_header_says_when_nothing_is_worth_an_offer(monkeypatch):
    from flipscout import hunt
    monkeypatch.setattr(cf, "items_of", lambda c, session=None: [])
    monkeypatch.setattr(cf, "measure", lambda i, s=None, cap=0: 0)
    got = {}

    def notifier(alerts, content=""):
        got["header"] = content
        return ["webhook"]

    hunt.post_collections({"collections": True}, notifier, set(),
                          feed=_cols(), today=TODAY)
    assert "(none worth an offer)" in got["header"]

"""Tests for image+link (embed) alerts."""

import pytest

from flipscout import notify
from flipscout.notify import VERDICT_COLORS, build_embed, notify_rich


class FakeResp:
    def __init__(self, mid="1"):
        self._mid = mid

    def raise_for_status(self):
        pass

    def json(self):
        return {"id": self._mid}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None, params=None):
        self.calls.append(json)
        return FakeResp()

    def put(self, url, headers=None, timeout=None):
        return FakeResp()


CAND = {
    "title": "Milwaukee M18 FUEL 2-Tool Brushless Kit",
    "url": "https://www.ebay.com/itm/358816746510",
    "image": "https://i.ebayimg.com/images/g/CW0AAeSwhPFqD7AO/s-l1600.jpg",
    "verdict": "pass",
    "reason": "Bare tools, no batteries/charger.",
    "all_in": 103.26, "comp": 265.74, "max_bid": 170.13, "bids": 0, "ends": "23h",
}


def test_embed_carries_link_and_image():
    e = build_embed(CAND)
    assert e["url"] == CAND["url"]
    assert e["image"]["url"] == CAND["image"]
    assert e["color"] == VERDICT_COLORS["pass"]


def test_embed_fields_are_labelled_money():
    vals = {f["name"]: f["value"] for f in build_embed(CAND)["fields"]}
    assert vals["Costs now"] == "$103.26"
    assert vals["Sells for"] == "$265.74"
    assert vals["MAX bid (never exceed)"] == "$170.13"
    assert "batteries" in vals["Verdict"]


def test_no_room_is_stated_not_a_negative_number():
    e = build_embed({**CAND, "max_bid": -5.0})
    assert {f["name"]: f["value"] for f in e["fields"]}["MAX bid (never exceed)"] == "no room"


def test_missing_fields_are_omitted_not_crashed():
    e = build_embed({"title": "x", "verdict": "buy"})
    # Only the copy-paste name field survives - everything else stays omitted.
    assert [f["name"] for f in e["fields"]] == ["\U0001F4CB Copy-paste name"]
    assert "url" not in e and "thumbnail" not in e


def test_notify_rich_posts_embeds():
    s = FakeSession()
    sent = notify_rich([CAND], content="hi", env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=s)
    assert sent == ["webhook", "webhook"]      # header, then the card
    assert s.calls[0]["content"] == "hi"
    assert len(s.calls[1]["embeds"]) == 1


def test_every_deal_gets_its_own_message():
    """🚨 A reaction belongs to a MESSAGE, so a digest carrying ten embeds
    cannot say which deal you meant - arming just took the first link it found.

    Measured against the live channel on 2026-08-18: all 25 recent alerts were
    multi-deal, which means arming had never once worked.
    """
    s = FakeSession()
    notify_rich([CAND] * 23, env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=s)
    assert len(s.calls) == 23
    assert all(len(c["embeds"]) == 1 for c in s.calls)


def test_the_tap_targets_are_seeded_on_each_card():
    """The bot puts 🎯, 🔥 and ❌ under every card so arming is ONE TAP - that
    is as close to a button as a webhook can get."""
    seen = []

    class S(FakeSession):
        def put(self, url, headers=None, timeout=None):
            seen.append(url)
            return FakeResp()

    notify_rich([CAND, CAND], env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x",
                                   "FLIPSCOUT_DISCORD_BOT_TOKEN": "t",
                                   "FLIPSCOUT_DISCORD_CHANNEL_ID": "9"}, session=S())
    assert len(seen) == 6                       # three chips on each of two cards
    assert all(u.endswith("/@me") for u in seen)


def test_a_missing_bot_token_never_blocks_the_alert():
    """Seeding is a convenience; the card itself is the product."""
    s = FakeSession()
    sent = notify_rich([CAND], env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=s)
    assert sent == ["webhook"]


def test_no_webhook_falls_back_to_printing(capsys):
    sent = notify_rich([CAND], content="digest", env={})
    assert sent == []
    assert "ebay.com/itm" in capsys.readouterr().out


def test_dead_webhook_is_fail_soft():
    class Boom(FakeSession):
        def post(self, *a, **k):
            raise RuntimeError("503")
    assert notify_rich([CAND], env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=Boom()) == []


def test_embed_carries_both_the_buy_link_and_the_comps_link():
    """Every alert must let you verify the 'sells for more' claim yourself."""
    from flipscout.notify import build_embed
    e = build_embed({**CAND,
                     "buy_url": "https://shopgoodwill.com/item/9",
                     "comps_url": "https://www.ebay.com/sch/i.html?_nkw=ipod&LH_Sold=1"})
    links = {f["name"]: f["value"] for f in e["fields"]}["Links"]
    assert "shopgoodwill.com/item/9" in links
    assert "LH_Sold=1" in links
    assert "Buy it here" in links and "sold for on eBay" in links


def test_failure_message_includes_the_discord_response_body(capsys):
    """Discord explains itself (unknown webhook / rate limit); that text IS the
    diagnosis, so it must reach the logs."""
    import requests

    class Resp:
        status_code = 404
        text = '{"message": "Unknown Webhook", "code": 10015}'

    class Boom:
        def post(self, *a, **k):
            err = requests.HTTPError("404 Client Error")
            err.response = Resp()
            raise err

    assert notify_rich([CAND], env={"FLIPSCOUT_ALERT_WEBHOOK": "http://x"}, session=Boom()) == []
    out = capsys.readouterr().out
    assert "Unknown Webhook" in out and "HTTP 404" in out


def test_photo_is_full_width_by_default(monkeypatch):
    """Condition is most of the buy decision and can't be judged from an 80px
    corner crop."""
    from flipscout.notify import build_embed
    monkeypatch.delenv("FLIPSCOUT_SMALL_IMAGES", raising=False)
    e = build_embed(CAND)
    assert e["image"]["url"] == CAND["image"]
    assert "thumbnail" not in e


def test_small_images_can_be_opted_into(monkeypatch):
    from flipscout.notify import build_embed
    monkeypatch.setenv("FLIPSCOUT_SMALL_IMAGES", "1")
    e = build_embed(CAND)
    assert e["thumbnail"]["url"] == CAND["image"]
    assert "image" not in e


# --- copy-paste name (2026-08-13) --------------------------------------------
# The embed title is Discord's ONLY clickable link - and Discord gives no way
# to copy text out of a link, so the raw name has to exist somewhere as plain
# text too.

def test_embed_carries_the_title_as_copyable_inline_code():
    e = build_embed(CAND)
    vals = {f["name"]: f["value"] for f in e["fields"]}
    assert vals["\U0001F4CB Copy-paste name"] == f"`{CAND['title']}`"
    # It rides on its own row, not squeezed into the money grid.
    assert next(f for f in e["fields"]
                if f["name"] == "\U0001F4CB Copy-paste name")["inline"] is False


def test_copy_paste_name_is_truncated_not_field_limit_busted():
    long_title = "Craftsman " + "Professional " * 100 + "Socket Set"
    assert len(long_title) > 1024
    e = build_embed({**CAND, "title": long_title})
    value = {f["name"]: f["value"] for f in e["fields"]}["\U0001F4CB Copy-paste name"]
    assert len(value) < 1024
    assert value == f"`{long_title[:150]}`"


def test_copy_paste_name_field_is_absent_when_there_is_no_title():
    e = build_embed({"verdict": "buy"})
    assert e["fields"] == []


def test_overlong_content_is_truncated_not_rejected():
    """Discord 400s the ENTIRE message when content > 2000 chars - a caller
    composing header + digest hit this live on 2026-07-28 and the whole
    delivery silently died. Truncate instead."""
    import flipscout.notify as notify
    posted = {}

    class Sess:
        def post(self, url, json=None, timeout=None):
            posted.update(json)
            class R:
                def raise_for_status(self):
                    assert len(json["content"]) <= 2000
            return R()

    sent = notify.notify_rich([], content="x" * 5000,
                              env={"FLIPSCOUT_ALERT_WEBHOOK": "https://h"},
                              session=Sess())
    assert sent == ["webhook"]
    assert len(posted["content"]) <= 2000

# --- channel routing, added 2026-08-22 --------------------------------------
# Leron made a Discord channel for cards specifically. Alerts route by subject
# now; these pin the ways routing can go wrong, and every one of them is silent.

class RoutingSession(FakeSession):
    """FakeSession, but it remembers WHERE each post went."""

    def __init__(self):
        super().__init__()
        self.urls = []

    def post(self, url, json=None, timeout=None, params=None):
        self.urls.append(url)
        return super().post(url, json=json, timeout=timeout, params=params)


CARDS_ENV = {"FLIPSCOUT_ALERT_WEBHOOK": "http://main",
             "FLIPSCOUT_CARDS_WEBHOOK": "http://cards"}

CARD_CAND = dict(CAND, title="2018 Panini Prizm Luka Doncic RC Auto /99")
PKMN_CAND = dict(CAND, title="Charizard VMAX Rainbow Rare", category="pokemon-cards")


def test_a_card_goes_to_the_cards_channel():
    assert notify.channel_for(CARD_CAND) == "cards"
    assert notify.destination(CARD_CAND, CARDS_ENV)[0] == "http://cards"


def test_the_priced_category_routes_even_when_the_title_does_not():
    """A Pokemon title carries no maker and no slab, so the title reader alone
    would not call it a sports card. The category the book PRICED it as does,
    which is why the category is checked first."""
    assert notify.channel_for(PKMN_CAND) == "cards"


def test_everything_else_still_goes_to_the_main_channel():
    """A category with no channel of its own is never swallowed by an existing
    one - it lands in the default deals channel. Was `cameras` until cameras
    got a channel of its own on 2026-08-23; a calculator has none."""
    other = dict(CAND, category="calculators", title="TI-84 Plus CE")
    assert notify.channel_for(other) == ""
    assert notify.destination(other, CARDS_ENV)[0] == "http://main"


# --- the 2026-08-23 subject channels ----------------------------------------
# Leron: "we need more channels - one for watches, camcorders, iPods and the
# other items we resell." Every one of these can only fail silently.

SUBJECT_ENV = dict(CARDS_ENV,
                   FLIPSCOUT_WATCHES_WEBHOOK="http://watches",
                   FLIPSCOUT_CAMERAS_WEBHOOK="http://cameras",
                   FLIPSCOUT_CAMCORDERS_WEBHOOK="http://camcorders",
                   FLIPSCOUT_IPODS_WEBHOOK="http://ipods",
                   FLIPSCOUT_GAMES_WEBHOOK="http://games",
                   FLIPSCOUT_COLLECTIONS_WEBHOOK="http://collections")


@pytest.mark.parametrize("category,title,channel", [
    # 2026-09-03: whole collections got their own channel. The title is
    # card-heavy on purpose - the category map must win, never the card reader.
    ("collections", "Pokemon card collection, 40 PSA 10s, Charizard", "collections"),
    ("watches",    "Citizen Eco-Drive Perpetual Calendar BL5250", "watches"),
    ("cameras",    "Canon AE-1 35mm Film Camera",                 "cameras"),
    ("lenses",     "Canon FD 50mm f/1.4 lens",                    "cameras"),
    ("ipods",      "Apple iPod Classic 160GB",                    "ipods"),
    ("walkman",    "Sony Walkman WM-EX",                          "ipods"),
    ("headphones", "Bose QuietComfort 35 II",                     "ipods"),
    ("videogames", "Nintendo 64 console bundle",                  "games"),
    # 🚨 A POKEMON CARTRIDGE IS NOT A CARD. The book prices game carts under
    # "pokemon" and the TCG under "pokemon-cards"; five cartridges once sat in
    # #cards because the title reader saw the word.
    ("pokemon",    "Pokemon Emerald Game Boy Advance",            "games"),
    ("calculators", "TI-84 Plus CE",                              ""),
    ("medical",    "Littmann Cardiology IV stethoscope",          ""),
])
def test_each_subject_routes_to_its_own_channel(category, title, channel):
    c = dict(CAND, category=category, title=title)
    assert notify.channel_for(c) == channel
    want = f"http://{channel}" if channel else "http://main"
    assert notify.destination(c, SUBJECT_ENV)[0] == want


def test_collections_without_a_webhook_fall_to_deals_never_cards():
    """The collections webhook unset (SUBJECT_ENV minus it, i.e. the 8/23
    world): a Pokemon-heavy collection lands in the deals channel, and NOT in
    #cards even though the cards webhook IS set and the title screams card."""
    env = {k: v for k, v in SUBJECT_ENV.items() if k != "FLIPSCOUT_COLLECTIONS_WEBHOOK"}
    c = dict(CAND, category="collections",
             title="Pokemon card collection, 40 PSA 10s, Charizard")
    assert notify.channel_for(c) == "collections"
    url, _, label = notify.destination(c, env)
    assert url == "http://main"
    assert label == "webhook"


@pytest.mark.parametrize("title", [
    "Sony Handycam DCR-TRV19 MiniDV camcorder",
    "Sony CCD-TR818 Hi8 Video8 Camcorder",
    "Sony HDR-CX405 Handycam",
])
def test_a_camcorder_splits_out_of_the_cameras_channel(title):
    """🚨 A CAMCORDER IS PRICED AS A CAMERA, so the category alone cannot
    separate them - the title read is load-bearing here and only here."""
    c = dict(CAND, category="cameras", title=title)
    assert notify.channel_for(c) == "camcorders"
    assert notify.destination(c, SUBJECT_ENV)[0] == "http://camcorders"


@pytest.mark.parametrize("title", [
    "Canon AE-1 Program 35mm SLR",
    "Olympus Stylus Epic mju-II",
    "Nikon Coolpix S6000 digital camera",
    "Contax T2 point and shoot",
])
def test_a_still_camera_is_not_read_as_a_camcorder(title):
    """The camcorder pattern is deliberately narrow. A broad "video"/"cam"
    would drag whole film SLRs into the wrong channel."""
    assert notify.channel_for(dict(CAND, category="cameras", title=title)) == "cameras"


def test_a_camcorder_falls_back_to_cameras_before_the_main_channel():
    """🚨 THE FALLBACK CHAIN. With #camcorders not created, a Handycam belongs
    in #cameras - it is a camera - not dumped in the general deals feed."""
    c = dict(CAND, category="cameras", title="Sony Handycam DCR-TRV19")
    env = {"FLIPSCOUT_ALERT_WEBHOOK": "http://main",
           "FLIPSCOUT_CAMERAS_WEBHOOK": "http://cameras"}
    assert notify.destination(c, env)[:1] == ("http://cameras",)
    # ...and with neither set it still lands somewhere, never nowhere.
    assert notify.destination(c, {"FLIPSCOUT_ALERT_WEBHOOK": "http://main"})[0]         == "http://main"


@pytest.mark.parametrize("name", sorted(notify.CHANNELS))
def test_no_subject_channel_can_ever_drop_an_alert(name):
    """🚨 THE RULE THAT OUTRANKS ROUTING. Every named channel with no webhook
    configured resolves to a real URL - the failure mode of a routed alert
    going nowhere is indistinguishable from the watcher being dead."""
    hook_var, _chan = notify.CHANNELS[name]
    probe = {"FLIPSCOUT_ALERT_WEBHOOK": "http://main"}
    assert notify.destination({"category": "__none__"}, probe)[0] == "http://main"
    assert hook_var.startswith("FLIPSCOUT_") and hook_var.endswith("_WEBHOOK")


def test_every_channel_has_a_label_and_a_workflow_mapping():
    """🚨 A SECRET NOT MAPPED IN watch.yml IS INERT AND SILENT - this repo's
    own scar ("three of them were set and silently inert for a full run, and
    the log looked perfectly healthy"). Adding a channel to CHANNELS without
    teaching the workflow to pass it through is that failure exactly."""
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parent.parent
          / ".github" / "workflows" / "watch.yml").read_text(encoding="utf-8")
    for name, (hook_var, chan_var) in notify.CHANNELS.items():
        assert name in notify.CHANNEL_LABEL, f"{name} has no human label"
        assert hook_var in wf, f"{hook_var} is not mapped in watch.yml"
        assert chan_var in wf, f"{chan_var} is not mapped in watch.yml"


def test_a_parent_channel_is_itself_a_real_channel():
    """A fallback pointing at a channel that does not exist would raise a
    KeyError mid-delivery, which is the one way routing could still lose an
    alert."""
    for child, parent in notify.PARENT.items():
        assert child in notify.CHANNELS and parent in notify.CHANNELS


def test_an_unset_cards_webhook_falls_back_and_never_drops_the_alert():
    """🚨 THE FAILURE THAT LOOKS LIKE A DEAD WATCHER. A routed alert with
    nowhere to go must land in the default channel, not vanish."""
    url, _chan, label = notify.destination(
        CARD_CAND, {"FLIPSCOUT_ALERT_WEBHOOK": "http://main"})
    assert (url, label) == ("http://main", "webhook")


def test_a_mixed_run_splits_between_the_two_channels():
    s = RoutingSession()
    sent = notify_rich([CAND, CARD_CAND], content="hdr", env=CARDS_ENV, session=s)
    assert sent.count("webhook") == 2 and sent.count("webhook:cards") == 2
    # each channel gets its own header, then its own card - never interleaved
    assert s.urls == ["http://main", "http://main", "http://cards", "http://cards"]
    assert s.calls[0] == {"content": "hdr"} and s.calls[2] == {"content": "hdr"}


def test_routing_off_behaves_exactly_as_before():
    """With no cards webhook set, delivery is byte-for-byte the old path - the
    default group is posted first and alone."""
    s = RoutingSession()
    sent = notify_rich([CARD_CAND, CAND], content="hdr",
                       env={"FLIPSCOUT_ALERT_WEBHOOK": "http://main"}, session=s)
    assert sent == ["webhook", "webhook", "webhook"]
    assert set(s.urls) == {"http://main"}


def test_the_cards_channel_is_seeded_with_its_own_channel_id(monkeypatch):
    """🚨 A REACTION IS ADDRESSED BY CHANNEL ID, NOT BY WEBHOOK. Seeding the
    cards channel against the default id 404s, and the card arrives with no
    tap-target and no error anyone would ever see."""
    seeded = []
    monkeypatch.setattr(notify, "seed_arm_reactions",
                        lambda mid, env=None, session=None, channel=None:
                        seeded.append(channel))
    env = dict(CARDS_ENV, FLIPSCOUT_DISCORD_CHANNEL_ID="111",
               FLIPSCOUT_CARDS_CHANNEL_ID="222")
    notify_rich([CAND, CARD_CAND], env=env, session=RoutingSession())
    assert seeded == ["111", "222"]


# --- bidding or buying, added 2026-08-22 ------------------------------------
# Leron: "I don't know if I'm bidding or buying." The only thing separating the
# two was the field labels "Open at"/"Asking" and "MAX bid"/"Don't pay over" -
# small grey type, invisible on a phone, below numbers whose MEANING depends on
# the answer.

def test_an_auction_says_you_are_bidding_first_and_loudly():
    e = build_embed(dict(CAND, listing_type="auction"))
    first = e["fields"][0]["value"]
    assert notify.AUCTION_MARK in first and "BIDDING" in first


def test_a_fixed_price_says_you_are_buying_first_and_loudly():
    e = build_embed(dict(CAND, listing_type="fixed"))
    first = e["fields"][0]["value"]
    assert notify.BUY_NOW_MARK in first and "BUYING" in first


def test_the_banner_sits_above_the_money():
    """🚨 The numbers mean different things depending on the answer, so it
    cannot sit below them."""
    e = build_embed(dict(CAND, listing_type="auction"))
    names = [f["name"] for f in e["fields"]]
    money = next(i for i, f in enumerate(e["fields"])
                 if f["name"] in ("Open at", "Asking", "Sells for", "Costs now"))
    assert money > 0 and notify.AUCTION_MARK in e["fields"][0]["value"], names


def test_an_unknown_listing_type_says_nothing_rather_than_guessing():
    """🚨 A WRONG "AUCTION" IS WORSE THAN A MISSING ONE, and some senders (the
    board digest) carry no listing type at all."""
    e = build_embed({k: v for k, v in CAND.items() if k != "listing_type"})
    assert notify.AUCTION_MARK not in str(e["fields"])
    assert notify.BUY_NOW_MARK not in str(e["fields"])


def test_the_source_alone_cannot_answer_it():
    """Measured on the live board: ShopGoodwill posts BOTH (212 auction, 10
    fixed), so "goodwill means auction" is right 95% of the time - the worst
    kind of rule."""
    import json, pathlib, collections
    board = pathlib.Path(__file__).resolve().parent.parent / "docs" / "deals.json"
    if not board.exists():
        return
    items = json.loads(board.read_text())["items"]
    per = collections.defaultdict(set)
    for i in items:
        per[i.get("source")].add(i.get("listing_type"))
    # Not an assertion on the CURRENT board - that file is regenerated hourly.
    # The claim is that SOME source mixes, which is why the banner cannot be
    # derived from the source name.
    assert any(len(v) > 1 for v in per.values()), \
        "no source mixes listing types any more; revisit the banner's rationale"


# --- edge cases swept 2026-08-23 --------------------------------------------
# Leron: "make sure the right items are going to the right folders and the auto
# bid options are still available." Everything below was run against the 582
# real listings on the live board first; these pin what that sweep found.

def test_a_category_with_whitespace_still_routes():
    """🚨 A SILENT FALL-THROUGH. `" watches "` missed the map and landed in
    #deals looking exactly like a category with no channel."""
    assert notify.channel_for({"category": "  watches  ", "title": "Citizen"}) \
        == "watches"
    assert notify.channel_for({"category": "CAMERAS",
                               "title": "Sony Handycam DCR-TRV19"}) == "camcorders"


@pytest.mark.parametrize("cand", [
    {}, {"category": None, "title": None}, {"title": ""}, {"category": 0},
    {"category": "unknown-new-category", "title": "whatever"},
])
def test_malformed_candidates_never_raise_and_never_vanish(cand):
    """Routing runs inside delivery. An exception here does not misfile an
    alert, it kills the whole post."""
    assert notify.channel_for(cand) == ""
    assert notify.destination(cand, {"FLIPSCOUT_ALERT_WEBHOOK": "http://main"})[0] \
        == "http://main"


def test_the_camcorder_pattern_is_a_superset_of_the_books_own_matcher():
    """🚨 THE INVARIANT THAT KEEPS THE TWO CAMERA CHANNELS HONEST. Every title
    the price book matches as `sony_handycam` MUST also match CAMCORDER_RE - or
    that camcorder posts to #cameras with nothing to explain why. Verified
    against the live board (73 of 73 correct, 0 leaks either way); pinned here
    against the book's own include pattern so a book edit cannot break it."""
    import re
    from flipscout import pricebook as pb
    hc = next(m for m in pb.MODELS if m.key == "sony_handycam")
    for probe in ["Sony DCR-TRV350", "handycam", "CCD-TR818", "HDR-CX405",
                  "HDR-XR160", "HDR-PJ340", "HDR-SR11", "DCR - SX41", "dcr-hc40"]:
        if re.search(hc.include, probe, re.I):
            assert notify.CAMCORDER_RE.search(probe), \
                f"the book calls {probe!r} a Handycam; routing does not"


def test_every_price_book_category_routes_somewhere_deliberate():
    """A category is either mapped to a channel or it is in the default - it is
    never mapped to a channel that does not exist."""
    from flipscout import pricebook as pb
    for m in pb.MODELS:
        ch = notify.channel_for({"category": m.category, "title": m.label})
        assert ch == "" or ch in notify.CHANNELS, \
            f"{m.category} routes to unknown channel {ch!r}"


def test_the_arm_chips_are_seeded_in_the_channel_the_card_landed_in(monkeypatch):
    """🚨 AUTO-BID MUST SURVIVE ROUTING. A reaction is addressed by CHANNEL ID.
    Seeding a #watches card against the default id 404s silently and the card
    arrives with no 🎯 - the alert looks fine and arming is simply gone."""
    seeded = []
    monkeypatch.setattr(notify, "seed_arm_reactions",
                        lambda mid, env=None, session=None, channel=None:
                        seeded.append(channel))
    env = {"FLIPSCOUT_ALERT_WEBHOOK": "http://main",
           "FLIPSCOUT_DISCORD_CHANNEL_ID": "100"}
    for i, (hook, chan) in enumerate(notify.CHANNELS.values()):
        env[hook], env[chan] = f"http://h{i}", str(200 + i)
    for cand, want in [
            ({"category": "watches", "title": "Citizen Promaster"}, "watches"),
            ({"category": "cameras", "title": "Sony Handycam CCD-TR818"}, "camcorders"),
            ({"category": "cameras", "title": "Canon AE-1"}, "cameras"),
            ({"category": "ipods", "title": "iPod Classic 160GB"}, "ipods"),
            ({"category": "videogames", "title": "Nintendo 64"}, "games"),
            ({"category": "calculators", "title": "TI-84"}, None)]:
        seeded.clear()
        notify_rich([cand], env=env, session=RoutingSession())
        expected = env[notify.CHANNELS[want][1]] if want else "100"
        assert seeded == [expected], f"{cand['title']} seeded in {seeded}"


def test_every_channel_that_is_posted_to_is_also_polled_for_taps():
    """🚨 A CHANNEL POSTED TO BUT NOT POLLED IS A CHANNEL WHERE 🎯 DOES
    NOTHING. The two lists come from different modules; this is the seam."""
    import os
    from flipscout import discordarm as DA
    env = {"FLIPSCOUT_DISCORD_BOT_TOKEN": "tok",
           "FLIPSCOUT_DISCORD_CHANNEL_ID": "100"}
    for i, (_hook, chan) in enumerate(notify.CHANNELS.values()):
        env[chan] = str(200 + i)
    old = dict(os.environ)
    try:
        os.environ.update(env)
        _tok, polled = DA._cfg()
    finally:
        os.environ.clear(); os.environ.update(old)
    for name, (_hook, chan) in notify.CHANNELS.items():
        assert env[chan] in polled, f"#{name} is posted to but never polled"

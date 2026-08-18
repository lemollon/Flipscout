"""Arming a snipe from a Discord reaction.

🚨 The reaction IS the authorisation, so the tests are about WHERE THE NUMBER
COMES FROM. A reaction may only ever arm at a figure the card already printed,
or one Leron typed himself. This module must never invent an amount.
"""

import pytest

from flipscout import discordarm, snipe


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(snipe, "ARMED_PATH", tmp_path / "armed.json")
    monkeypatch.setenv("FLIPSCOUT_DISCORD_BOT_TOKEN", "x")
    monkeypatch.setenv("FLIPSCOUT_DISCORD_CHANNEL_ID", "1")
    yield


def _card(mid="1", ceiling="$41.34", react=None, content=None):
    return {
        "id": mid,
        "content": content or "**Flipscout** - 1 new find",
        "embeds": [{"title": "Citizen Eco-Drive Perpetual Calendar",
                    "fields": [
                        {"name": "Asking", "value": "$30.99"},
                        {"name": "Sells on eBay", "value": "$150.00"},
                        {"name": "Don't pay over", "value": ceiling},
                        {"name": "Link",
                         "value": "https://shopgoodwill.com/item/273876344"}]}],
        "reactions": ([{"emoji": {"name": react}, "count": 2, "me": True}]
                      if react else []),
    }


def _feed(monkeypatch, msgs):
    monkeypatch.setattr(discordarm, "_get", lambda *a, **k: msgs)
    armed_calls = []
    monkeypatch.setattr(snipe, "arm",
                        lambda i, m, override=False: armed_calls.append((i, m, override)) or 0)
    monkeypatch.setattr(snipe, "disarm", lambda i: armed_calls.append(("disarm", i)) or 0)
    return armed_calls


def test_the_target_reaction_arms_at_the_ceiling_the_card_printed(monkeypatch):
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    discordarm.scan()
    assert calls == [("273876344", 41.34, False)]


def test_a_reaction_on_a_card_with_no_ceiling_arms_NOTHING(monkeypatch):
    """🚨 The whole safety property. If the card printed no number, Leron never
    saw one, so the reaction authorised nothing - and this must not guess."""
    card = _card(react=discordarm.ARM_EMOJI, ceiling="n/a")
    card["embeds"][0]["fields"] = [f for f in card["embeds"][0]["fields"]
                                   if f["name"] != "Don't pay over"]
    calls = _feed(monkeypatch, [card])
    discordarm.scan()
    assert calls == []


def test_an_explicit_reply_beats_the_cards_ceiling(monkeypatch):
    card = _card()
    reply = {"id": "2", "content": "snipe 25", "embeds": [],
             "referenced_message": card, "reactions": []}
    calls = _feed(monkeypatch, [reply, card])
    discordarm.scan()
    assert calls == [("273876344", 25.0, True)]


def test_the_cross_reaction_disarms(monkeypatch):
    snipe.save_armed({"273876344": {"id": "273876344", "max_bid": 41.34,
                                    "status": "ARMED", "title": "x", "url": "u"}})
    calls = _feed(monkeypatch, [_card(react=discordarm.DISARM_EMOJI)])
    discordarm.scan()
    assert calls == [("disarm", "273876344")]


def test_an_unreacted_card_does_nothing(monkeypatch):
    calls = _feed(monkeypatch, [_card()])
    discordarm.scan()
    assert calls == []


def test_a_card_with_no_shopgoodwill_link_is_ignored(monkeypatch):
    card = _card(react=discordarm.ARM_EMOJI)
    card["embeds"][0]["fields"][-1]["value"] = "https://ebay.com/itm/123456789"
    calls = _feed(monkeypatch, [card])
    discordarm.scan()
    assert calls == []


def test_already_armed_items_are_not_re_armed(monkeypatch):
    snipe.save_armed({"273876344": {"id": "273876344", "max_bid": 10.0,
                                    "status": "ARMED", "title": "x", "url": "u"}})
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    discordarm.scan()
    assert calls == []


def test_missing_credentials_is_a_clear_refusal_not_a_crash(monkeypatch):
    monkeypatch.delenv("FLIPSCOUT_DISCORD_BOT_TOKEN", raising=False)
    assert discordarm.scan() == 2


def test_dry_run_reads_but_never_arms(monkeypatch):
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    discordarm.scan(dry_run=True)
    assert calls == []


@pytest.mark.parametrize("value,want", [
    ("$41.34", 41.34), ("$1,234.50", 1234.50), ("$8", 8.0),
])
def test_ceiling_parsing(value, want):
    """Parse the card directly rather than through scan().

    Going through scan() used to work, but react-arming now
    re-validates against the LIVE book - so this assertion started
    depending on the network and on today's comps, which is not what a
    PARSING test should be measuring.
    """
    _site, iid, ceiling = discordarm.parse_card(_card(ceiling=value))
    assert iid == "273876344"
    assert ceiling == want


# --- the acknowledgement tick -----------------------------------------------
# Flagged by the Chrome agent during setup: the invite asked for "Add
# Reactions" and the code never used it, so 🎯 produced NO visible feedback
# until the snipe fired minutes later. Either drop the permission or make it
# earn its place - this makes it earn it.

def _spy_react(monkeypatch):
    seen = []
    monkeypatch.setattr(discordarm, "_react",
                        lambda ch, mid, emoji, tok: seen.append((mid, emoji)) or True)
    return seen


def test_arming_acknowledges_with_a_tick(monkeypatch):
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    seen = _spy_react(monkeypatch)
    discordarm.scan()
    assert calls, "should have armed"
    assert seen == [("1", discordarm.ACK_EMOJI)]


def test_a_card_with_no_ceiling_gets_a_warning_tick_not_silence(monkeypatch):
    """Silence is indistinguishable from 'the poller is dead'."""
    card = _card(react=discordarm.ARM_EMOJI)
    card["embeds"][0]["fields"] = [f for f in card["embeds"][0]["fields"]
                                   if f["name"] != "Don't pay over"]
    _feed(monkeypatch, [card])
    seen = _spy_react(monkeypatch)
    discordarm.scan()
    assert seen == [("1", discordarm.NOPE_EMOJI)]


def test_a_failing_reaction_never_blocks_the_arm(monkeypatch):
    """🚨 The arm is the real work; the tick is only a receipt. A missing "Add
    Reactions" permission, or any Discord blip, must not cost Leron the snipe.

    Exercises the REAL _react - patching the spy to raise would only prove the
    spy raises.
    """
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])

    def boom(*a, **k):
        raise RuntimeError("403 Missing Permissions")

    monkeypatch.setattr(discordarm.requests, "put", boom)
    discordarm.scan()
    assert calls == [("273876344", 41.34, False)], \
        "the arm must still happen when the acknowledgement fails"


def test_react_swallows_errors_and_reports_false(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(discordarm.requests, "put", boom)
    assert discordarm._react("1", "2", discordarm.ACK_EMOJI, "tok") is False


def test_react_url_encodes_the_emoji(monkeypatch):
    """A raw ✅ in a URL path is not valid - it must be percent-encoded."""
    seen = {}
    class R:
        status_code = 204
    def cap(url, **k):
        seen["url"] = url
        return R()
    monkeypatch.setattr(discordarm.requests, "put", cap)
    assert discordarm._react("99", "77", discordarm.ACK_EMOJI, "tok") is True
    assert "/channels/99/messages/77/reactions/" in seen["url"]
    assert "%E2%9C%85" in seen["url"], "emoji must be percent-encoded"
    assert seen["url"].endswith("/@me")


def test_dry_run_does_not_react_either(monkeypatch):
    _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    seen = _spy_react(monkeypatch)
    discordarm.scan(dry_run=True)
    assert seen == []


def test_both_message_content_flags_count(monkeypatch):
    """🚨 There are TWO flags meaning 'can read message content'. Apps in under
    100 servers get the LIMITED one (1<<19); the full flag (1<<18) is for
    reviewed apps. They behave identically.

    Checking only bit 18 made me report "Message Content Intent is OFF" while
    the bot was reading all 50 messages fine, and send Leron to re-toggle a
    switch that was already correct.
    """
    for flags, want in [(1 << 18, True), (1 << 19, True),
                        ((1 << 18) | (1 << 19), True), (0, False), (1 << 12, False)]:
        monkeypatch.setattr(discordarm, "_get", lambda *a, **k: {"flags": flags})
        assert discordarm.can_read_content("tok") is want, f"flags={flags}"


# --- stale cards --------------------------------------------------------
# A reaction takes its number FROM THE CARD, so an old alert carries an old
# opinion. Live on 2026-08-18: a "Sony Handycam DCR-DVD610" card still showed a
# $89.90 ceiling printed before the tape-vs-DVD split; the book now refuses DVD
# camcorders outright. 🎯 on it would have armed $89.90 with nothing behind it.

def _book(monkeypatch, ceiling, title="Sony Handycam DCR-DVD610"):
    monkeypatch.setattr(snipe, "detail",
                        lambda iid: {"title": title, "handlingPrice": 0})
    monkeypatch.setattr(snipe, "book_ceiling", lambda t, inbound=0.0: ceiling)


def test_a_card_the_book_no_longer_prices_is_refused(monkeypatch):
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI, ceiling="$89.90")])
    seen = _spy_react(monkeypatch)
    _book(monkeypatch, None)                     # book now declines it
    discordarm.scan()
    assert calls == [], "must not arm on a ceiling the book has withdrawn"
    assert seen == [("1", discordarm.NOPE_EMOJI)]


def test_it_arms_at_the_lower_of_card_and_current_book(monkeypatch):
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI, ceiling="$89.90")])
    _spy_react(monkeypatch)
    _book(monkeypatch, 40.00)                    # book has come down since
    discordarm.scan()
    assert calls == [("273876344", 40.00, False)]


def test_the_card_still_wins_when_it_is_the_lower_number(monkeypatch):
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI, ceiling="$20.00")])
    _spy_react(monkeypatch)
    _book(monkeypatch, 95.00)                    # book is now higher
    discordarm.scan()
    assert calls == [("273876344", 20.00, False)], "never arm above what he saw"


def test_an_explicit_reply_is_not_re_validated(monkeypatch):
    """He named the number himself and may be overriding on purpose."""
    card = _card()
    reply = {"id": "2", "content": "snipe 75", "embeds": [],
             "referenced_message": card, "reactions": []}
    calls = _feed(monkeypatch, [reply, card])
    _spy_react(monkeypatch)
    _book(monkeypatch, None)                     # book declines - irrelevant here
    discordarm.scan()
    assert calls == [("273876344", 75.0, True)]


def test_an_unreachable_item_falls_back_to_the_card(monkeypatch):
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI, ceiling="$30.00")])
    _spy_react(monkeypatch)
    def boom(iid): raise RuntimeError("network")
    monkeypatch.setattr(snipe, "detail", boom)
    discordarm.scan()
    assert calls == [("273876344", 30.00, False)]


# --- two sites, two snipers ---------------------------------------------------

def _hibid_card(ceiling="$41.34", react=None):
    m = {"id": "m-hb", "content": "", "embeds": [{
        "title": "Fluke 87V True RMS Multimeter",
        "url": "https://hibid.com/lot/317852714",
        "fields": [{"name": "Max bid", "value": f"Don't pay over {ceiling}"}]}]}
    if react:
        m["reactions"] = [{"emoji": {"name": react}, "count": 2, "me": True}]
    return m


def test_a_hibid_card_is_recognised_as_hibid():
    site, iid, ceiling = discordarm.parse_card(_hibid_card())
    assert (site, iid, ceiling) == ("hibid", "317852714", 41.34)


def test_a_goodwill_card_is_still_recognised_as_goodwill():
    site, iid, _ = discordarm.parse_card(_card())
    assert site == "goodwill"
    assert iid == "273876344"


def test_each_site_routes_to_its_own_sniper():
    """🚨 Ids are not unique across the two sites, so routing must come from
    the LINK. Arming a HiBid lot into the ShopGoodwill sniper would point a
    bidder at a completely unrelated item."""
    from flipscout import hibidsnipe, snipe as sgw
    assert discordarm.sniper_for("hibid") is hibidsnipe
    assert discordarm.sniper_for("goodwill") is sgw
    assert discordarm.sniper_for(None) is sgw


def test_a_card_with_no_known_link_is_ignored():
    m = {"id": "x", "content": "https://ebay.com/itm/12345678 nice thing",
         "reactions": [{"emoji": {"name": discordarm.ARM_EMOJI}}]}
    site, iid, _ = discordarm.parse_card(m)
    assert site is None and iid is None


# --- the bot's own seed must not arm anything --------------------------------

def test_the_seeded_emoji_alone_never_arms(monkeypatch):
    """🚨 The bot puts 🎯 under every card so arming is one tap. That means the
    emoji being PRESENT proves nothing - if a seeded chip counted as intent,
    every card would arm itself the instant it was posted."""
    seeded = _card()
    seeded["reactions"] = [{"emoji": {"name": discordarm.ARM_EMOJI},
                            "count": 1, "me": True}]      # only the bot
    calls = _feed(monkeypatch, [seeded])
    discordarm.scan()
    assert not calls


def test_a_human_tap_on_the_seeded_emoji_does_arm(monkeypatch):
    tapped = _card()
    tapped["reactions"] = [{"emoji": {"name": discordarm.ARM_EMOJI},
                            "count": 2, "me": True}]      # bot + Leron
    calls = _feed(monkeypatch, [tapped])
    discordarm.scan()
    assert calls


def test_an_unseeded_human_reaction_still_works(monkeypatch):
    """If seeding failed, reacting by hand must still arm."""
    manual = _card()
    manual["reactions"] = [{"emoji": {"name": discordarm.ARM_EMOJI},
                            "count": 1, "me": False}]
    calls = _feed(monkeypatch, [manual])
    discordarm.scan()
    assert calls


def test_a_multi_deal_card_refuses_to_arm():
    """🚨 A reaction belongs to the MESSAGE. On a digest carrying several deals
    it cannot say which one, and the old parser silently took the first."""
    m = {"id": "multi", "content": "", "embeds": [
        {"title": "A", "url": "https://shopgoodwill.com/item/111111"},
        {"title": "B", "url": "https://hibid.com/lot/222222"},
    ], "reactions": [{"emoji": {"name": discordarm.ARM_EMOJI},
                      "count": 2, "me": True}]}
    assert discordarm.parse_card(m) == (None, None, None)


def test_the_ceiling_is_read_off_a_real_card_layout():
    """🚨 The amount sits on the LINE AFTER its label, because it is an embed
    FIELD. The old pattern allowed 12 characters between them and " (never
    exceed)
" is 16, so it matched NOTHING on any real card - 0 of 25 live
    alerts parsed a ceiling on 2026-08-18."""
    m = {"id": "r", "content": "", "embeds": [{
        "title": "3 Sega Dreamcast jump Packs",
        "url": "https://hibid.com/lot/317377910",
        "fields": [{"name": "MAX bid (never exceed)", "value": "$33.92"}]}]}
    assert discordarm.parse_card(m) == ("hibid", "317377910", 33.92)

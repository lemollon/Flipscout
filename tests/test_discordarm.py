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
        # The steady state on a real card: the poller has already seeded all
        # three chips (count 1, me=True). A human tap shows up as count 2.
        "reactions": [{"emoji": {"name": e}, "count": 2 if e == react else 1,
                       "me": True}
                      for e in (discordarm.ARM_EMOJI, discordarm.STRETCH_EMOJI,
                                discordarm.DISARM_EMOJI)],
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


# --- conflicting taps --------------------------------------------------------

def _reacted(*emoji):
    m = _card()
    m["reactions"] = [{"emoji": {"name": e}, "count": 2, "me": True} for e in emoji]
    return m


def test_fire_beats_target_when_both_are_tapped(monkeypatch):
    """Tapping both is a real thing on a phone. 🔥 is the more specific
    intent, so it wins - and it is the SAFER reading to resolve toward,
    because it is the one he had to reach past the default to press."""
    calls = _feed(monkeypatch, [_reacted(discordarm.ARM_EMOJI,
                                         discordarm.STRETCH_EMOJI)])
    discordarm.scan()
    assert calls, "must arm"


def test_disarm_wins_any_conflict(monkeypatch):
    """🚨 ❌ alongside an arm emoji must NOT bid. A conflicting instruction is
    the one case where doing nothing is unambiguously right - the lot can
    always be re-armed, but a bid cannot be recalled."""
    for other in (discordarm.ARM_EMOJI, discordarm.STRETCH_EMOJI):
        snipe.save_armed({"273876344": {"id": "273876344", "title": "x",
                                        "max_bid": 41.34, "status": "ARMED",
                                        "url": "u"}})
        calls = _feed(monkeypatch, [_reacted(other, discordarm.DISARM_EMOJI)])
        discordarm.scan()
        # _feed records arms AND disarms, so check which one happened.
        assert calls == [("disarm", "273876344")], (
            f"{other} alongside the cross must disarm, never arm - got {calls}")


# --- seeding from the poller -------------------------------------------------

def test_a_card_naming_its_lot_twice_is_still_one_deal():
    """🚨 Every card references its own lot twice - the embed url, and again in
    the "Buy it here" link. Counting raw matches made all 37 live cards look
    like two-deal digests and refused to arm ANY of them."""
    m = {"id": "x", "content": "", "embeds": [{
        "title": "Sony Handycam",
        "url": "https://shopgoodwill.com/item/273508745",
        "fields": [
            {"name": "MAX bid (never exceed)", "value": "$91.90"},
            {"name": "Links",
             "value": "[Buy it here](https://shopgoodwill.com/item/273508745)"},
        ]}]}
    assert discordarm.parse_card(m) == ("goodwill", "273508745", 91.90)


def test_two_different_lots_is_still_a_digest_and_refuses():
    m = {"id": "y", "content": "", "embeds": [
        {"title": "A", "url": "https://shopgoodwill.com/item/111111"},
        {"title": "B", "url": "https://shopgoodwill.com/item/222222"}]}
    assert discordarm.parse_card(m) == (None, None, None)


def test_the_poller_seeds_a_bare_card(monkeypatch):
    """🚨 The alerts Leron actually receives are posted by the GitHub Action,
    whose env has the webhook and NO bot token - so post-time seeding silently
    no-opped and every CI card arrived with no chips to tap."""
    bare = _card()
    bare["reactions"] = []
    put = []
    monkeypatch.setattr(discordarm, "_react",
                        lambda ch, mid, emoji, tok: put.append(emoji) or True)
    assert discordarm.seed_missing([bare], "chan", "tok") == 1
    assert put == [discordarm.ARM_EMOJI, discordarm.STRETCH_EMOJI,
                   discordarm.DISARM_EMOJI]


def test_an_already_seeded_card_is_not_re_seeded(monkeypatch):
    """Otherwise every poll spends its whole reaction budget re-doing work."""
    put = []
    monkeypatch.setattr(discordarm, "_react",
                        lambda *a, **k: put.append(a[2]) or True)
    assert discordarm.seed_missing([_card()], "chan", "tok") == 0
    assert put == []


def test_a_partly_seeded_card_gets_only_what_is_missing(monkeypatch):
    half = _card()
    half["reactions"] = [{"emoji": {"name": discordarm.ARM_EMOJI},
                          "count": 1, "me": True}]
    put = []
    monkeypatch.setattr(discordarm, "_react",
                        lambda *a, **k: put.append(a[2]) or True)
    discordarm.seed_missing([half], "chan", "tok")
    assert put == [discordarm.STRETCH_EMOJI, discordarm.DISARM_EMOJI]


def test_a_multi_deal_digest_is_left_bare(monkeypatch):
    """A chip on a message that cannot say which lot you meant is a lie."""
    m = {"id": "d", "content": "", "reactions": [], "embeds": [
        {"title": "A", "url": "https://shopgoodwill.com/item/111111"},
        {"title": "B", "url": "https://shopgoodwill.com/item/222222"}]}
    put = []
    monkeypatch.setattr(discordarm, "_react", lambda *a, **k: put.append(a) or True)
    assert discordarm.seed_missing([m], "chan", "tok") == 0
    assert put == []


def test_seeding_is_budgeted(monkeypatch):
    """Discord rate-limits reactions hard, and the poller has a bidding job to
    get to. Anything skipped is picked up next minute."""
    bare = []
    for i in range(40):
        c = _card()
        c["id"] = f"m{i}"
        c["reactions"] = []
        bare.append(c)
    monkeypatch.setattr(discordarm, "_react", lambda *a, **k: True)
    assert discordarm.seed_missing(bare, "chan", "tok") == discordarm.SEED_BUDGET


def test_a_dry_run_seeds_nothing(monkeypatch):
    bare = _card()
    bare["reactions"] = []
    put = []
    monkeypatch.setattr(discordarm, "_react", lambda *a, **k: put.append(a) or True)
    discordarm.seed_missing([bare], "chan", "tok", dry_run=True)
    assert put == []


# --- arming has to be visible ------------------------------------------------

def test_arming_sends_a_real_message_not_just_a_reaction(monkeypatch):
    """🚨 A REACTION IS NOT A NOTIFICATION. Arming used to leave only a ✅ chip,
    which produces no push and is invisible on a phone. Leron armed a lot on
    2026-08-18, saw nothing, and reasonably concluded it had failed - the tick
    was there, he just could not see it."""
    sent = []
    import flipscout.notify as N
    monkeypatch.setattr(N, "notify", lambda text, **k: sent.append(text))
    _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    discordarm.scan()
    assert sent, "arming must post a message"
    assert "Armed" in sent[0]


def test_the_confirmation_says_no_bid_has_been_placed(monkeypatch):
    """🚨 His words were "nothing confirming I made the bid" - and he had not
    made one. 🎯 arms; the bid happens three minutes before the close. A
    confirmation that does not draw that line invites the same confusion."""
    sent = []
    import flipscout.notify as N
    monkeypatch.setattr(N, "notify", lambda text, **k: sent.append(text))
    _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    discordarm.scan()
    assert "No bid has been placed yet" in sent[0]
    assert "41.34" in sent[0], "it must state the number it armed at"


def test_disarming_is_announced_too(monkeypatch):
    sent = []
    import flipscout.notify as N
    monkeypatch.setattr(N, "notify", lambda text, **k: sent.append(text))
    snipe.save_armed({"273876344": {"id": "273876344", "title": "x",
                                    "max_bid": 41.34, "status": "ARMED",
                                    "url": "u"}})
    _feed(monkeypatch, [_card(react=discordarm.DISARM_EMOJI)])
    discordarm.scan()
    assert sent and "Disarmed" in sent[0]


def test_a_failing_confirmation_never_blocks_the_arm(monkeypatch):
    """The arm is the real work; the message is a receipt."""
    import flipscout.notify as N
    monkeypatch.setattr(N, "notify",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    discordarm.scan()
    assert calls, "the arm must still happen"


def test_an_unreachable_detail_does_not_crash_the_confirmation(monkeypatch):
    """🚨 `d` was assigned inside the try, so a detail() failure - a network
    blip, a vanished lot - turned a recoverable fall-back into a crash."""
    sent = []
    import flipscout.notify as N
    monkeypatch.setattr(N, "notify", lambda text, **k: sent.append(text))
    monkeypatch.setattr(snipe, "detail",
                        lambda i: (_ for _ in ()).throw(RuntimeError("no net")))
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    discordarm.scan()
    assert calls, "it must fall back to the card's ceiling and still arm"


def test_a_broken_confirmation_never_costs_the_rest_of_the_poll(monkeypatch):
    """🚨 _confirm used to raise on a junk premium AFTER the arm had already
    succeeded, killing scan() - so every remaining card in that poll went
    unprocessed. The arm is the work; the message is a receipt."""
    def boom(*a, **k):
        raise ValueError("bad premium")
    monkeypatch.setattr(discordarm, "_confirm", boom)
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI)])
    discordarm.scan()                        # must not raise
    assert calls, "the arm must still have happened"


def test_confirm_tolerates_a_junk_premium():
    discordarm._confirm("hibid", "123456789", 41.34,
                        {"title": "x", "premium": "bad"}, 0.0, {})


# --- the ceiling-less refusal must be said ONCE ------------------------------

def _card_msg(mid, iid, chips):
    """A Flipscout card with `chips` = {emoji: (count, bot_reacted)}."""
    return {"id": mid, "content": "",
            "embeds": [{"title": "Some lot", "url": f"https://hibid.com/lot/{iid}",
                        "fields": [{"name": "Links",
                                    "value": f"[Buy it here](https://hibid.com/lot/{iid})"}]}],
            "reactions": [{"emoji": {"name": e}, "count": c, "me": me}
                          for e, (c, me) in chips.items()]}


def test_a_ceilingless_tap_is_refused_once_not_every_minute(monkeypatch, capsys):
    """🚨 THE REFUSAL HAD NO MEMORY.

    The 🎯 stays on the card forever, so a refusal that does not record itself
    fires again on every poll. Leron tapped 🎯 on four ceiling-less cards on
    2026-08-20 and got FOURTEEN identical "NOT armed" messages, once a minute,
    still climbing. The ⚠ the bot puts on the card is the record; reading it
    back is what stops the loop.
    """
    from flipscout import discordarm as DA
    monkeypatch.setattr(DA, "_cfg", lambda: ("tok", "chan"))
    # already refused: 🎯 tapped by a human, ⚠ already placed by the bot
    msg = _card_msg("m1", "999888777",
                    {DA.ARM_EMOJI: (2, True), DA.NOPE_EMOJI: (1, True)})
    monkeypatch.setattr(DA, "_get", lambda *a, **k: [msg])
    monkeypatch.setattr(DA, "seed_missing", lambda *a, **k: 0)
    monkeypatch.setattr(DA, "_react", lambda *a, **k: True)
    sent = []
    monkeypatch.setattr("flipscout.notify.notify",
                        lambda msg, **k: sent.append(msg))
    DA.scan()
    assert not sent, "the refusal repeated even though the bot had already ⚠'d it"


def test_a_ceilingless_tap_IS_refused_the_first_time(monkeypatch):
    from flipscout import discordarm as DA
    monkeypatch.setattr(DA, "_cfg", lambda: ("tok", "chan"))
    msg = _card_msg("m1", "999888777", {DA.ARM_EMOJI: (2, True)})   # no ⚠ yet
    monkeypatch.setattr(DA, "_get", lambda *a, **k: [msg])
    monkeypatch.setattr(DA, "seed_missing", lambda *a, **k: 0)
    monkeypatch.setattr(DA, "_react", lambda *a, **k: True)
    sent = []
    monkeypatch.setattr("flipscout.notify.notify",
                        lambda msg, **k: sent.append(msg))
    DA.scan()
    assert len(sent) == 1
    assert "Reply to that card" in sent[0]


# --- naming your own number ---------------------------------------------------

@pytest.mark.parametrize("text,want", [
    ("25", 25.0), ("$25", 25.0), ("snipe 25", 25.0), ("arm $25.50", 25.5),
    ("max 25", 25.0), ("bid 25", 25.0), ("25.00", 25.0), ("$1,250", 1250.0),
])
def test_a_bare_number_reply_is_a_valid_max(text, want):
    """🚨 IT ONLY EVER FIRES ON A REPLY TO A CARD, so the context is already
    unambiguous - demanding the word "snipe" on top bought nothing and cost a
    lot. On a phone, replying is three taps before you type anything; making
    him also remember a keyword is how a working escape hatch goes unused."""
    from flipscout import discordarm as DA
    m = DA._REPLY.search(text)
    assert m is not None, f"{text!r} should be accepted"
    assert DA._money(m.group(1)) == want


@pytest.mark.parametrize("text", [
    "looks like 25 of them are junk", "what about 25 or 30", "snipe it",
    "no", "", "maybe 25?",
])
def test_a_number_inside_a_sentence_is_not_an_authorisation(text):
    """Anchored end to end on purpose: an amount must be DELIBERATE."""
    from flipscout import discordarm as DA
    assert DA._REPLY.search(text) is None, f"{text!r} must not arm anything"

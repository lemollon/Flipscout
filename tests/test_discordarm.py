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
        "reactions": ([{"emoji": {"name": react}}] if react else []),
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
def test_ceiling_parsing(monkeypatch, value, want):
    calls = _feed(monkeypatch, [_card(react=discordarm.ARM_EMOJI, ceiling=value)])
    discordarm.scan()
    assert calls[0][1] == want

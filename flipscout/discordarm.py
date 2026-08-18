"""Arm snipes from Discord, by reacting to an alert.

WHY THIS IS SEPARATE FROM notify.py
-----------------------------------
The alert path is a WEBHOOK, which is write-only: it can post into the channel
and can never read anything back, so it cannot see a reaction. Reading requires
a real bot identity.

🚨 IT DOES NOT NEED A GATEWAY CONNECTION. The obvious build is a websocket bot
that stays online, which means a long-running process to supervise, reconnect
and monitor. Discord's REST API exposes reactions directly, so this POLLS on
the same one-minute scheduled task that already runs the sniper - no daemon, no
reconnect logic, nothing new to keep alive.

SETUP (once, by Leron - Claude must not create or paste the token)
------------------------------------------------------------------
  1. https://discord.com/developers/applications -> New Application -> Bot
  2. Enable "Message Content Intent" under Bot -> Privileged Gateway Intents
  3. Invite it to the server with the `bot` scope and these permissions:
     View Channels, Read Message History, Add Reactions
  4. Put the token in .env as FLIPSCOUT_DISCORD_BOT_TOKEN
     and the channel id as FLIPSCOUT_DISCORD_CHANNEL_ID

HOW ARMING WORKS
----------------
React to any Flipscout alert carrying a shopgoodwill item or hibid lot
link - the card's own link decides which sniper arms it:

    🎯  arm at the ceiling printed on that card
    🔥  arm, and go over that ceiling to win it - see below
    ❌  disarm

All three are already sitting under every card - this poller puts them there
within a minute of the card appearing - so arming is a single tap rather than a
long-press and an emoji hunt.

🚨 SEEDING HAPPENS HERE, NOT IN THE SENDER. notify.py also seeds at post time,
but that only works where the bot token exists, and the alerts Leron actually
receives are posted by the GitHub Action, which only has the webhook. Every
CI-posted card therefore arrived bare and could not be armed.
Discord will not give a webhook real buttons: interactive components need an
app listening for a pushed interaction, which means a daemon or a public
endpoint, and this design deliberately has neither.

The bot answers with ✅ once the item is armed, or ⚠ if the card carried no
ceiling to arm at. That tick is the ONLY thing this bot ever writes, and it is
why the invite needs "Add Reactions": without it you tap the target and see
nothing until the snipe fires, with no way to tell the poller noticed.

🚨 THE REACTION IS THE AUTHORISATION, so the amount must be one Leron already
SAW. 🎯 arms at the ceiling shown on the card - a number the alert printed
before he reacted - never at a figure this module invents. If the card has no
ceiling, it refuses and says so rather than guessing.

To use a different number, reply to the alert with `snipe 45`. An explicit
figure always beats the card's ceiling.

THE THREE LEVELS, IN ORDER OF HOW MUCH THEY COST YOU
----------------------------------------------------
  🎯  the disciplined ceiling - clears the full target profit
  🔥  that ceiling plus FLIPSCOUT_SNIPE_STRETCH (default $10), CLAMPED at
      break-even, so it can spend margin but never buy at a loss
  reply `snipe 60`  - your number, no clamp at all, because you named it

🚨 The stretch is profit you are spending, not headroom you found. And on a
proxy system it only ever activates when a rival is sitting between the two
numbers - which is precisely the narrow loss you wished you had won.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Optional

import requests

from . import snipe

API = "https://discord.com/api/v10"
ARM_EMOJI = "\N{DIRECT HIT}"        # 🎯
DISARM_EMOJI = "\N{CROSS MARK}"     # ❌
ACK_EMOJI = "\N{WHITE HEAVY CHECK MARK}"        # ✅ armed, poller saw it
NOPE_EMOJI = "\N{WARNING SIGN}"                 # ⚠ could not arm - no ceiling on the card
STRETCH_EMOJI = "\N{FIRE}"                     # 🔥 arm, and go over the book to win

# How far 🔥 reaches past the card's ceiling. It is a DOLLAR amount rather than
# a percentage on purpose: the thing being spent is profit, and profit is
# measured in dollars, not in a share of the hammer.
#
# 🚨 It is clamped at BREAK-EVEN by snipe.stretch_to, so however large this is
# set, 🔥 can never arm a bid that loses money. Going past break-even needs an
# explicit `snipe <amount>` reply, where Leron names the figure himself.
STRETCH_DEFAULT = float(os.environ.get("FLIPSCOUT_SNIPE_STRETCH", "10"))

# Both sniper-capable sites. The site decides which module arms it, so a
# card must never be matched by id alone - ids are not unique across them.
_ITEM = re.compile(
    r"(?:(shopgoodwill)\.com/item/|(hibid)\.com/lot/)(\d{6,})", re.I)
# "Don't pay over" is the field notify.build_embed prints on every card.
# 🚨 The amount sits on the LINE AFTER the label, because it is an embed FIELD:
#     MAX bid (never exceed)
#     $33.92
# The old \D{0,12} could not span " (never exceed)\n" (16 chars), so this
# matched nothing on any real card and 🎯 refused every one of them. Measured
# against the live channel on 2026-08-18: 0 of 25 cards parsed a ceiling.
_CEILING = re.compile(
    r"(?:don'?t pay over|max bid|ceiling)[^$\d]{0,40}\$\s*([\d,]+\.?\d*)", re.I)
_REPLY = re.compile(r"\bsnipe\s+\$?\s*([\d,]+\.?\d*)", re.I)


def _cfg() -> tuple:
    return (os.environ.get("FLIPSCOUT_DISCORD_BOT_TOKEN", "").strip(),
            os.environ.get("FLIPSCOUT_DISCORD_CHANNEL_ID", "").strip())


def _get(path: str, token: str, **params):
    r = requests.get(f"{API}{path}", timeout=25,
                     headers={"Authorization": f"Bot {token}"}, params=params)
    r.raise_for_status()
    return r.json()


def _react(channel: str, message_id: str, emoji: str, token: str) -> bool:
    """Put an acknowledgement reaction on a card. Fail-soft.

    🚨 This is the ONLY write this bot performs, and it is why the invite asks
    for "Add Reactions". Without it the loop is silent: you tap the target and
    nothing visible happens until the snipe fires minutes later, so you cannot
    tell the poller ever saw it. A tick appearing within the minute is the
    receipt. Never let this failing block an arm - the arm is the real work.
    """
    from urllib.parse import quote
    try:
        r = requests.put(
            f"{API}/channels/{channel}/messages/{message_id}/reactions/"
            f"{quote(emoji)}/@me",
            headers={"Authorization": f"Bot {token}"}, timeout=20)
        return r.status_code in (200, 204)
    except Exception:
        return False


# Discord application flags that mean "this bot can read message content".
# 🚨 THERE ARE TWO, and checking only the first is wrong. Apps in fewer than
# 100 servers are granted the LIMITED flag (1<<19); the full flag (1<<18) is
# for apps that have been through Discord's review. They behave identically.
# Diagnosed 2026-08-18: I checked only bit 18, reported "Message Content Intent
# is OFF" while the bot was in fact reading all 50 messages perfectly, and sent
# Leron off to re-toggle a switch that was already correct.
MSG_CONTENT_FLAGS = (1 << 18) | (1 << 19)


def can_read_content(token: str) -> bool:
    """True when this bot may see message content and embeds."""
    try:
        return bool(int(_get("/applications/@me", token).get("flags") or 0)
                    & MSG_CONTENT_FLAGS)
    except Exception:
        return False


def _money(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def card_text(msg: dict) -> str:
    """Everything a card says - content plus every embed field, flattened."""
    bits = [msg.get("content") or ""]
    for e in msg.get("embeds") or []:
        bits += [e.get("title") or "", e.get("description") or "", e.get("url") or ""]
        for f in e.get("fields") or []:
            bits += [f.get("name") or "", f.get("value") or ""]
    return "\n".join(bits)


def parse_card(msg: dict) -> tuple:
    """(site, item_id, ceiling) from one alert message. Any may be None.

    `site` is "goodwill" or "hibid" and selects the sniper - they keep separate
    armed files, separate browser profiles and separate rules (HiBid will not
    bid until you are registered for the auction).
    """
    txt = card_text(msg)
    # 🚨 DEDUPE. A single card names its own lot more than once - the embed
    # url, and again in the "Buy it here" link - so counting raw matches made
    # every card look like a two-deal digest and refused to arm ANY of them.
    # Two references to one lot is one deal; two different lots is a digest.
    hits = list(dict.fromkeys(_ITEM.findall(txt)))
    # 🚨 REFUSE AN AMBIGUOUS MESSAGE. Alerts used to pack up to ten deals into
    # one post, and a reaction belongs to the MESSAGE, not to an embed inside
    # it - so a tap could not say which deal was meant. This used to silently
    # take the FIRST link and the FIRST ceiling, which on a five-deal digest
    # meant arming one item and ignoring four.
    #
    # notify_rich now posts one card per message, but older multi-deal cards
    # are still sitting in the channel and must stay unarmable.
    if len(hits) != 1:
        return (None, None, None)
    gw, hb, iid = hits[0]
    c = _CEILING.search(txt)
    return ("goodwill" if gw else "hibid", iid,
            _money(c.group(1)) if c else None)


def sniper_for(site: str):
    """The module that arms this site. Both expose arm/disarm/load_armed."""
    from . import hibidsnipe
    return hibidsnipe if site == "hibid" else snipe


# Never spend more than this many reaction writes on seeding in one poll.
# Discord rate-limits reactions hard, and the poller has a bidding job to get
# to; anything missed is picked up on the next minute.
SEED_BUDGET = 12


def seed_missing(msgs: list, channel: str, token: str, dry_run: bool = False) -> int:
    """Put 🎯 🔥 ❌ under any Flipscout card that has none.

    🚨 THIS IS WHY IT LIVES IN THE POLLER AND NOT IN THE SENDER. notify.py
    seeds at post time, but that only works where the BOT TOKEN is present -
    and the alerts Leron actually gets are posted by the GitHub Action, whose
    env has the webhook and nothing else. So every CI-posted card arrived with
    no chips on it (seen 2026-08-18: the "ENDING SOON" digest had none, while a
    locally-posted card had all three) and could not be armed at all.

    Seeding here fixes every sender at once - hunt, ending-soon, the Facebook
    sweep, mybids, the heartbeat - including any added later, and it repairs
    cards that were posted before this existed. The cost is that chips can take
    up to a minute to appear.
    """
    done = 0
    for m in msgs:
        if done >= SEED_BUDGET:
            break
        # Only cards we could actually arm. A multi-deal digest returns None
        # and is deliberately left bare - a chip on it would be a lie.
        site, iid, _ = parse_card(m)
        if not iid:
            continue
        have = {(r.get("emoji") or {}).get("name") for r in (m.get("reactions") or [])
                if r.get("me")}
        want = {ARM_EMOJI, STRETCH_EMOJI, DISARM_EMOJI} - have
        if not want:
            continue
        for emoji in (ARM_EMOJI, STRETCH_EMOJI, DISARM_EMOJI):
            if emoji in want and not dry_run:
                _react(channel, m["id"], emoji, token)
        done += 1
    return done


def scan(limit: int = 50, dry_run: bool = False) -> int:
    """Poll the channel and act on 🎯 / ❌ / `snipe <n>` replies."""
    token, channel = _cfg()
    if not token or not channel:
        print("no FLIPSCOUT_DISCORD_BOT_TOKEN / FLIPSCOUT_DISCORD_CHANNEL_ID - "
              "see this module's docstring for the one-time setup")
        return 2
    try:
        msgs = _get(f"/channels/{channel}/messages", token, limit=limit)
    except Exception as e:
        print(f"discord read failed: {type(e).__name__}: {e}")
        return 1

    # Do this first: a card with no chips cannot be tapped, so seeding is the
    # prerequisite for everything below.
    seeded = seed_missing(msgs, channel, token, dry_run=dry_run)
    if seeded:
        print(f"seeded arm chips on {seeded} card(s)")

    by_id = {m["id"]: m for m in msgs}
    armed = snipe.load_armed()
    acted = 0

    # explicit replies first - a stated number always beats a card ceiling
    for m in msgs:
        rep = (m.get("referenced_message") or {})
        amt = _REPLY.search(m.get("content") or "")
        if not (rep and amt):
            continue
        site, iid, _ = parse_card(by_id.get(rep.get("id"), rep))
        if not iid:
            continue
        val = _money(amt.group(1))
        mod = sniper_for(site)
        if val is None or iid in mod.load_armed():
            continue
        print(f"reply-arm {site} {iid} at ${val:.2f}")
        if not dry_run:
            mod.arm(iid, val, override=True)     # he named the number himself
            _react(channel, rep.get("id") or m["id"], ACK_EMOJI, token)
        acted += 1

    for m in msgs:
        # 🚨 The bot SEEDS 🎯 and ❌ on every card so arming is one tap. That
        # means the emoji being present proves nothing - what counts is whether
        # a HUMAN also tapped it. Discord reports `me` (did this bot react) and
        # `count`, so a seeded chip needs count > 1 before it means anything.
        # Without this every card would arm itself the moment it was posted.
        emojis = set()
        for r in (m.get("reactions") or []):
            name = (r.get("emoji") or {}).get("name")
            if not name:
                continue
            human = int(r.get("count") or 0) - (1 if r.get("me") else 0)
            if human > 0:
                emojis.add(name)
        if not emojis & {ARM_EMOJI, DISARM_EMOJI, STRETCH_EMOJI}:
            continue
        site, iid, ceiling = parse_card(m)
        if not iid:
            continue
        mod = sniper_for(site)
        armed = mod.load_armed()
        if DISARM_EMOJI in emojis and iid in armed:
            print(f"disarm {site} {iid}")
            if not dry_run:
                mod.disarm(iid)
            acted += 1
            continue
        if (emojis & {ARM_EMOJI, STRETCH_EMOJI}) and iid not in armed:
            if ceiling is None:
                # 🚨 Never invent the number. If the card did not print a
                # ceiling, Leron never saw one, so reacting authorised nothing.
                print(f"{iid}: 🎯 but the card shows no ceiling - "
                      f"reply `snipe <amount>` instead")
                if not dry_run:
                    _react(channel, m["id"], NOPE_EMOJI, token)
                continue
            # 🚨 RE-VALIDATE THE CARD AGAINST TODAY'S BOOK.
            #
            # A reaction takes its number FROM THE CARD, not from Leron's head,
            # so an old alert carries an old opinion. Live example on
            # 2026-08-18: a card for "Sony Handycam DCR-DVD610" still showed a
            # $89.90 ceiling, printed before the tape-vs-DVD split. The book
            # now refuses DVD camcorders outright ($41 median, not $135), so
            # 🎯 on that card would have armed $89.90 with nothing behind it.
            #
            # An explicit `snipe 45` reply is different and is NOT re-checked -
            # there he named the number himself and may well be overriding on
            # purpose.
            try:
                d = mod.detail(iid)
                if site == "hibid":
                    # The HiBid ceiling is a HAMMER number, so it has to be
                    # re-derived against THIS auction's buyer's premium - the
                    # same lot under a 20% house is worth less to bid on.
                    fresh = mod.book_ceiling(d.get("title") or "",
                                             premium=float(d.get("premium") or 0))
                else:
                    fresh = mod.book_ceiling(
                        d.get("title") or "",
                        inbound=float(d.get("handlingPrice") or 0))
            except Exception:
                fresh = ceiling          # cannot check -> trust the card
            if fresh is None:
                print(f"{iid}: the book no longer prices this - card ceiling "
                      f"${ceiling:.2f} is stale. Reply `snipe <amount>` to override.")
                if not dry_run:
                    _react(channel, m["id"], NOPE_EMOJI, token)
                continue
            use = min(ceiling, fresh)
            if use < ceiling:
                print(f"{iid}: card said ${ceiling:.2f}, book now says "
                      f"${fresh:.2f} - arming at the LOWER figure")

            # 🔥 says "I would rather win this than keep the full margin".
            # The disciplined ceiling clears TARGET_PROFIT; each dollar past it
            # clears a dollar less, and stretch_to refuses to go beyond
            # break-even no matter how far STRETCH_DEFAULT reaches.
            stretch = STRETCH_DEFAULT if STRETCH_EMOJI in emojis else 0.0
            note = ""
            if stretch:
                try:
                    title = (d.get("title") or "") if isinstance(d, dict) else ""
                    if site == "hibid":
                        use, clears, be, clamped = mod.stretch_to(
                            title, use, stretch,
                            premium=float(d.get("premium") or 0))
                    else:
                        use, clears, be, clamped = mod.stretch_to(
                            title, use, stretch,
                            inbound=float(d.get("handlingPrice") or 0))
                    note = (f" (stretched; clears ${clears:.2f}, "
                            f"break-even ${be:.2f}"
                            + (", CLAMPED" if clamped else "") + ")")
                except Exception:
                    use = round(use + stretch, 2)
                    note = " (stretched; could not price break-even)"

            print(f"react-arm {site} {iid} at ${use:.2f}{note}")
            if not dry_run:
                mod.arm(iid, use, override=bool(stretch))
                _react(channel, m["id"], ACK_EMOJI, token)
            acted += 1
    if not acted:
        print("nothing to arm or disarm")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .mybids import load_env_file
    load_env_file()
    return scan(dry_run="--dry-run" in argv)


if __name__ == "__main__":
    raise SystemExit(main())

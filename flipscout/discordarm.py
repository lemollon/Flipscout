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
React to any Flipscout alert carrying a shopgoodwill item link:

    🎯  arm at the "Don't pay over" ceiling printed on that card
    ❌  disarm

🚨 THE REACTION IS THE AUTHORISATION, so the amount must be one Leron already
SAW. 🎯 arms at the ceiling shown on the card - a number the alert printed
before he reacted - never at a figure this module invents. If the card has no
ceiling, it refuses and says so rather than guessing.

To use a different number, reply to the alert with `snipe 45`. An explicit
figure always beats the card's ceiling.
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

_ITEM = re.compile(r"shopgoodwill\.com/item/(\d{6,})", re.I)
# "Don't pay over" is the field notify.build_embed prints on every card.
_CEILING = re.compile(r"(?:don'?t pay over|max bid|ceiling)\D{0,12}\$\s*([\d,]+\.?\d*)", re.I)
_REPLY = re.compile(r"\bsnipe\s+\$?\s*([\d,]+\.?\d*)", re.I)


def _cfg() -> tuple:
    return (os.environ.get("FLIPSCOUT_DISCORD_BOT_TOKEN", "").strip(),
            os.environ.get("FLIPSCOUT_DISCORD_CHANNEL_ID", "").strip())


def _get(path: str, token: str, **params):
    r = requests.get(f"{API}{path}", timeout=25,
                     headers={"Authorization": f"Bot {token}"}, params=params)
    r.raise_for_status()
    return r.json()


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
    """(item_id, ceiling) from one alert message. Either may be None."""
    txt = card_text(msg)
    m = _ITEM.search(txt)
    c = _CEILING.search(txt)
    return (m.group(1) if m else None,
            _money(c.group(1)) if c else None)


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

    by_id = {m["id"]: m for m in msgs}
    armed = snipe.load_armed()
    acted = 0

    # explicit replies first - a stated number always beats a card ceiling
    for m in msgs:
        rep = (m.get("referenced_message") or {})
        amt = _REPLY.search(m.get("content") or "")
        if not (rep and amt):
            continue
        iid, _ = parse_card(by_id.get(rep.get("id"), rep))
        if not iid:
            continue
        val = _money(amt.group(1))
        if val is None or iid in armed:
            continue
        print(f"reply-arm {iid} at ${val:.2f}")
        if not dry_run:
            snipe.arm(iid, val, override=True)   # he named the number himself
        acted += 1

    armed = snipe.load_armed()
    for m in msgs:
        emojis = {(r.get("emoji") or {}).get("name") for r in (m.get("reactions") or [])}
        if not emojis & {ARM_EMOJI, DISARM_EMOJI}:
            continue
        iid, ceiling = parse_card(m)
        if not iid:
            continue
        if DISARM_EMOJI in emojis and iid in armed:
            print(f"disarm {iid}")
            if not dry_run:
                snipe.disarm(iid)
            acted += 1
            continue
        if ARM_EMOJI in emojis and iid not in armed:
            if ceiling is None:
                # 🚨 Never invent the number. If the card did not print a
                # ceiling, Leron never saw one, so reacting authorised nothing.
                print(f"{iid}: 🎯 but the card shows no ceiling - "
                      f"reply `snipe <amount>` instead")
                continue
            print(f"react-arm {iid} at the card's ${ceiling:.2f} ceiling")
            if not dry_run:
                snipe.arm(iid, ceiling)
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

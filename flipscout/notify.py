"""Deliver deal alerts — the "comes to you" channel for the always-on watcher.

Two channels, both optional and fail-soft (a broken channel never crashes the
watch run):
  * Webhook (recommended, zero-setup): a Discord or Slack incoming-webhook URL in
    FLIPSCOUT_ALERT_WEBHOOK. We POST both {"content"} and {"text"} so either works.
  * Email: set FLIPSCOUT_SMTP_HOST/PORT/USER/PASS + FLIPSCOUT_ALERT_TO/FROM.

If neither is configured, alerts just print (they still show in the run's logs).
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Optional

import requests


def format_digest(hits, header: Optional[str] = None) -> str:
    """A compact, channel-friendly text digest of ranked deals."""
    n = len(hits)
    lines = [header or f"🏷️ Flipscout — {n} new deal{'s' if n != 1 else ''}", ""]
    for h in hits:
        vel = f" · ~{h.days_to_sell:.0f}d to sell" if getattr(h, "days_to_sell", None) is not None else ""
        lines.append(f"${h.per_hour:.0f}/hr · ${h.profit:.0f} profit ({h.roi:.0%} ROI){vel} [{h.source}]")
        # Backticks -> Discord inline code: renders as plain, selectable text
        # instead of markdown, so the exact title can be copied straight into
        # an eBay search box.
        lines.append(f"`{h.title[:70]}`")
        lines.append(f"buy ${h.buy_price:.0f} → sell ${h.sold_price:.0f}"
                     + (f"  ·  {h.url}" if h.url else ""))
        lines.append("")
    return "\n".join(lines).strip()


def _send_webhook(url: str, text: str, session=None) -> None:
    session = session or requests
    r = session.post(url, json={"content": text, "text": text}, timeout=15)
    r.raise_for_status()


# --- rich (image + link) alerts -------------------------------------------
# Discord renders "embeds": a clickable title, a thumbnail, and labelled fields.
# Slack ignores the key and still shows `content`, so this stays fail-soft.

VERDICT_COLORS = {
    "buy": 0x2ECC71,      # green  - clears your bar
    "watch": 0xF1C40F,    # yellow - close, needs a judgement call
    "pass": 0xE74C3C,     # red    - checked and rejected, with the reason
}


def build_embed(c: dict) -> dict:
    """One candidate -> a Discord embed.

    Expected keys: title, url, image, verdict ('buy'|'watch'|'pass'), reason,
    and any of: all_in, comp, max_bid, bids, ends.
    """
    fields = []
    # The embed title is the ONLY clickable link Discord shows - which means
    # it's the only place the name lives, and Discord gives no way to copy
    # text out of a link (mobile long-press just opens it). Inline code
    # (`backticks`) renders as plain, selectable text instead, so the exact
    # listing title can be pasted straight into an eBay search. Full-width
    # (inline=False) so it rides on its own row above the money-field grid
    # and never disturbs that grid's layout.
    title = (c.get("title") or "").strip()
    if title:
        fields.append({"name": "📋 Copy-paste name",
                       "value": f"`{title[:150]}`", "inline": False})
    if c.get("all_in") is not None:
        fields.append({"name": "Costs now", "value": f"${c['all_in']:,.2f}", "inline": True})
    if c.get("comp") is not None:
        fields.append({"name": "Sells for", "value": f"${c['comp']:,.2f}", "inline": True})
    # The two numbers that matter, always adjacent and on the same (bid) basis:
    # what to put in first, and the line you never cross when someone contests it.
    if c.get("open_bid") is not None:
        # Fixed-price listings have no bid to place - the number is the ask.
        label = "Asking" if c.get("listing_type") == "fixed" else "Open at"
        fields.append({"name": label, "value": f"${c['open_bid']:,.2f}", "inline": True})
    if c.get("max_bid") is not None:
        mb = c["max_bid"]
        max_label = ("Don't pay over" if c.get("listing_type") == "fixed"
                     else "MAX bid (never exceed)")
        fields.append({"name": max_label,
                       "value": f"${mb:,.2f}" if mb > 0 else "no room",
                       "inline": True})
    if c.get("bids") is not None:
        fields.append({"name": "Bids", "value": str(c["bids"]), "inline": True})
    if c.get("ends"):
        fields.append({"name": "Ends", "value": str(c["ends"]), "inline": True})
    if c.get("source"):
        fields.append({"name": "Source", "value": str(c["source"]), "inline": True})
    if c.get("reason"):
        fields.append({"name": "Verdict", "value": c["reason"][:1000], "inline": False})
    # Both sides of the trade, spelled out as links you can click to check the
    # claim rather than take it on trust.
    links = []
    if c.get("buy_url"):
        links.append(f"[Buy it here]({c['buy_url']})")
    if c.get("comps_url"):
        links.append(f"[See what it sold for on eBay]({c['comps_url']})")
    if links:
        fields.append({"name": "Links", "value": "  |  ".join(links)[:1000], "inline": False})

    embed = {
        "title": (c.get("title") or "untitled")[:250],
        "color": VERDICT_COLORS.get(c.get("verdict", "watch"), 0x95A5A6),
        "fields": fields,
    }
    if c.get("url"):
        embed["url"] = c["url"]
    if c.get("image"):
        # `image` renders full-width, `thumbnail` renders small in the corner.
        # Condition is most of the buy decision here (paint-spattered tools,
        # scratched screens, what's actually in a lot), and you can't judge that
        # from a 80px corner crop. Set FLIPSCOUT_SMALL_IMAGES=1 for the compact
        # layout when a batch of 10 gets too tall to scroll.
        key = "thumbnail" if os.environ.get("FLIPSCOUT_SMALL_IMAGES") else "image"
        embed[key] = {"url": c["image"]}
    return embed


def describe_webhook(url: str, session=None) -> str:
    """Which channel does this webhook actually post to?

    A 2xx on POST only proves Discord accepted the message - it says nothing
    about whether it landed anywhere you look. Logging the resolved channel makes
    the destination visible instead of assumed, which is the failure that made
    "it stopped sending me deals" take three rounds to diagnose.
    """
    if not url:
        return "no webhook configured"
    session = session or requests
    try:
        r = session.get(url, timeout=10)
        r.raise_for_status()
        d = r.json()
        return (f"webhook '{d.get('name')}' -> channel {d.get('channel_id')} "
                f"in guild {d.get('guild_id')}")
    except Exception as e:
        return f"could not resolve webhook: {e}"


def notify_rich(candidates: list, content: str = "", env=None, session=None) -> list[str]:
    """Post candidates to the webhook as embeds (image + clickable link).

    Discord caps a message at 10 embeds, so this chunks. Fail-soft like notify():
    a dead webhook prints instead of raising.
    """
    env = env if env is not None else os.environ
    url = env.get("FLIPSCOUT_ALERT_WEBHOOK")
    # Discord hard-rejects the WHOLE message when content exceeds 2000 chars -
    # a caller composing header + digest busted the cap on 2026-07-28 and the
    # delivery died with a 400. A truncated post beats a silently dropped one.
    content = (content or "")[:1990]
    embeds = [build_embed(c) for c in candidates]
    if not url:
        print(content or "")
        for c in candidates:
            print(f"- [{c.get('verdict','?')}] {c.get('title','')} {c.get('url','')}")
        return []

    session = session or requests
    sent: list[str] = []

    # 🚨 ONE CARD PER MESSAGE. This used to pack ten embeds into a single post,
    # which quietly made arming impossible: a reaction belongs to a MESSAGE, so
    # on a ten-deal digest it cannot say which deal you meant, and the parser
    # just took the first link it found. Leron hit this directly - "the arming
    # should be in the card".
    #
    # The cost is ten notifications instead of one. That is the price of every
    # card being individually actionable, and it is worth it.
    if content:
        try:
            r = session.post(url, json={"content": content}, timeout=15)
            r.raise_for_status()
            sent.append("webhook")
        except Exception as e:
            print(f"[notify] header failed: {e}")

    for emb in embeds:
        try:
            # ?wait=true makes Discord return the created message, which is the
            # only way to learn its id - and the id is what lets the bot seed
            # the tap-target reactions below.
            r = session.post(url, params={"wait": "true"},
                             json={"embeds": [emb]}, timeout=15)
            r.raise_for_status()
            sent.append("webhook")
            try:
                seed_arm_reactions((r.json() or {}).get("id"), env=env,
                                   session=session)
            except Exception:
                pass                       # a missing tap-target never blocks the alert
        except Exception as e:
            body = ""
            resp = getattr(e, "response", None)
            if resp is not None:
                body = f" | HTTP {resp.status_code}: {str(resp.text)[:200]}"
            print(f"[notify] rich webhook failed: {e}{body}")
    return sent


def seed_arm_reactions(message_id, env=None, session=None) -> bool:
    """Put 🎯 and ❌ on a card so arming is ONE TAP.

    🚨 THIS IS THE "BUTTON". Discord will not give a webhook real buttons -
    interactive components need an app listening on a pushed interaction, which
    would mean a daemon or a public endpoint, and this whole design runs off a
    one-minute scheduled task instead.

    A reaction the bot has already placed renders as a chip under the message
    that you tap once - on mobile that is the same gesture as a button, versus
    long-press-then-hunt-the-emoji-picker for an unseeded one.

    Needs the bot token; without it, cards still post and you can react
    manually.
    """
    env = env if env is not None else os.environ
    token = (env.get("FLIPSCOUT_DISCORD_BOT_TOKEN") or "").strip()
    channel = (env.get("FLIPSCOUT_DISCORD_CHANNEL_ID") or "").strip()
    if not (token and channel and message_id):
        return False
    from urllib.parse import quote
    session = session or requests
    ok = True
    for emoji in ("\N{DIRECT HIT}", "\N{CROSS MARK}"):
        try:
            r = session.put(
                f"https://discord.com/api/v10/channels/{channel}/messages/"
                f"{message_id}/reactions/{quote(emoji)}/@me",
                headers={"Authorization": f"Bot {token}"}, timeout=15)
            ok = ok and r.status_code in (200, 204)
        except Exception:
            ok = False
    return ok


def _send_email(text: str, subject: str, env) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = env.get("FLIPSCOUT_ALERT_FROM", env.get("FLIPSCOUT_SMTP_USER", ""))
    msg["To"] = env["FLIPSCOUT_ALERT_TO"]
    msg.set_content(text)
    host, port = env["FLIPSCOUT_SMTP_HOST"], int(env.get("FLIPSCOUT_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=20) as s:
        s.starttls()
        if env.get("FLIPSCOUT_SMTP_USER"):
            s.login(env["FLIPSCOUT_SMTP_USER"], env.get("FLIPSCOUT_SMTP_PASS", ""))
        s.send_message(msg)


def notify(text: str, subject: str = "Flipscout deals", env=None, session=None) -> list[str]:
    """Send `text` to every configured channel. Returns the channels used;
    exceptions are swallowed per-channel (fail-soft)."""
    env = env if env is not None else os.environ
    sent: list[str] = []
    url = env.get("FLIPSCOUT_ALERT_WEBHOOK")
    if url:
        try:
            _send_webhook(url, text, session=session)
            sent.append("webhook")
        except Exception as e:
            print(f"[notify] webhook failed: {e}")
    if env.get("FLIPSCOUT_SMTP_HOST") and env.get("FLIPSCOUT_ALERT_TO"):
        try:
            _send_email(text, subject, env)
            sent.append("email")
        except Exception as e:
            print(f"[notify] email failed: {e}")
    if not sent:
        print(text)  # nowhere to send -> at least land it in the logs
    return sent

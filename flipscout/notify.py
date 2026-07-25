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
        lines.append(f"{h.title[:70]}")
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
    if c.get("all_in") is not None:
        fields.append({"name": "Costs now", "value": f"${c['all_in']:,.2f}", "inline": True})
    if c.get("comp") is not None:
        fields.append({"name": "Sells for", "value": f"${c['comp']:,.2f}", "inline": True})
    # The two numbers that matter, always adjacent and on the same (bid) basis:
    # what to put in first, and the line you never cross when someone contests it.
    if c.get("open_bid") is not None:
        fields.append({"name": "Open at", "value": f"${c['open_bid']:,.2f}", "inline": True})
    if c.get("max_bid") is not None:
        mb = c["max_bid"]
        fields.append({"name": "MAX bid (never exceed)",
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
        embed["thumbnail"] = {"url": c["image"]}
    return embed


def notify_rich(candidates: list, content: str = "", env=None, session=None) -> list[str]:
    """Post candidates to the webhook as embeds (image + clickable link).

    Discord caps a message at 10 embeds, so this chunks. Fail-soft like notify():
    a dead webhook prints instead of raising.
    """
    env = env if env is not None else os.environ
    url = env.get("FLIPSCOUT_ALERT_WEBHOOK")
    embeds = [build_embed(c) for c in candidates]
    if not url:
        print(content or "")
        for c in candidates:
            print(f"- [{c.get('verdict','?')}] {c.get('title','')} {c.get('url','')}")
        return []

    session = session or requests
    sent: list[str] = []
    for i in range(0, max(len(embeds), 1), 10):
        chunk = embeds[i:i + 10]
        payload = {"content": content if i == 0 else "", "embeds": chunk}
        try:
            r = session.post(url, json=payload, timeout=15)
            r.raise_for_status()
            sent.append("webhook")
        except Exception as e:
            print(f"[notify] rich webhook failed: {e}")
    return sent


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

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
        lines.append(f"${h.per_hour:.0f}/hr · ${h.profit:.0f} profit ({h.roi:.0%} ROI) [{h.source}]")
        lines.append(f"{h.title[:70]}")
        lines.append(f"buy ${h.buy_price:.0f} → sell ${h.sold_price:.0f}"
                     + (f"  ·  {h.url}" if h.url else ""))
        lines.append("")
    return "\n".join(lines).strip()


def _send_webhook(url: str, text: str, session=None) -> None:
    session = session or requests
    r = session.post(url, json={"content": text, "text": text}, timeout=15)
    r.raise_for_status()


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

"""Your HiBid WATCH LIST -> one armable card per lot.

THE GAP THIS CLOSES
-------------------
Everything Flipscout knows about HiBid, it found itself: hunters.py sweeps the
catalog by keyword and the price book decides what is worth an alert. The lots
LERON hearts BY HAND - on his phone, in the HiBid app, at an auction he went
looking at himself - are invisible to all of that. They sit on his watch list,
and until now nothing in this repo ever read that page. He watched them because
he wants them, and the sniper never heard about a single one.

🚨 WHY THIS READS THE PAGE AND NOT THE EMAIL
--------------------------------------------
HiBid does mail "Your N watched lots are closing today", and that mail really
does carry the lot id, the lot name and the exact close time. It is a usable
feed, and it is what prompted this module. It is also a strictly worse one than
the page it is a copy of:

  * IT IS A DAY LATE. It fires the morning of the close. A lot hearted a week
    ago stays unknown to the sniper for that entire week - and the whole point
    of arming is to have the ceiling decided BEFORE the endgame.
  * IT IS LOSSY. Only lots closing TODAY appear in it. Nothing else on the
    watch list is ever mentioned, so an email-fed pipeline would be blind to
    most of the list most of the time.
  * IT IS A SECOND CREDENTIAL AND A SECOND FAILURE MODE. An app password, an
    IMAP session, and a parser aimed at marketing HTML that HiBid rewrites
    whenever it likes - and when that parser breaks it does not raise, it
    reports an empty inbox. A silent zero is the exact failure class that made
    a corrupt armed file look like a quiet day.
  * THE PAGE IS ALREADY READABLE. `.hibidprofile` is a signed-in browser
    profile this repo already drives for `registered_authed` and
    `outcome_authed`. The watch list is one more authenticated GET with it.

So: the email is the notification, this is the feed. Anything the mail could
tell us is a subset of what the page already says, a day later.

WHAT A CARD MEANS HERE, WHICH IS NOT WHAT A HUNT CARD MEANS
-----------------------------------------------------------
🚨 A HUNT CARD IS A SUGGESTION. A WATCH-LIST CARD IS A REMINDER OF HIS OWN
DECISION. hunt.evaluate() drops anything the book cannot price and anything
with no headroom, which is right for a keyword sweep of eleven hundred
strangers' lots. Applying that here would silently bin the lots he chose
himself - he would tap a heart on HiBid and Flipscout would answer with
nothing, which is indistinguishable from Flipscout being broken.

So EVERY open watched lot gets a card. What changes is whether the card carries
a ceiling:

  * The book prices it and there is room  -> ceiling printed, 🎯 arms it.
  * The book prices it but it is already  -> NO ceiling. The card says so, and
    at or over that ceiling                  `snipe <amount>` is the override.
  * The book has no comp for it at all    -> NO ceiling, and the card says the
                                             number has to come from him.

A ceiling-less card is deliberately unarmable by reaction: discordarm refuses
🎯 when the card printed no number, because a tap can only authorise a figure
he actually saw.

WHAT IT NEVER DOES
------------------
It never bids, never arms, never registers, never un-watches, and never writes
anything to HiBid. It navigates to one page and reads it.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
from typing import Optional

from .auctionfees import min_increment
from .bidding import advise
from .hibidsnipe import (PROFILE_DIR, book_ceiling, detail, load_armed,
                         signed_in)
from .notify import notify, notify_rich
from .pricebook import match

REPO = pathlib.Path(__file__).resolve().parent.parent
STATE_PATH = REPO / "hibid_watch_seen.json"
WATCH_URL = "https://hibid.com/account/watchlist"
LOT_URL = "https://hibid.com/lot/{}"

# A browser launch is expensive and a watch list changes at human speed, so this
# self-throttles instead of needing its own scheduled task: dropping it into the
# once-a-minute snipe job is safe, it simply returns early.
MIN_GAP_MIN = float(os.environ.get("FLIPSCOUT_HIBID_WATCH_GAP_MIN", "20"))

# 🚨 These must line up with what hibidsnipe.arm re-derives, or a ceiling this
# card printed gets rejected the moment he taps it. arm() calls book_ceiling
# with the defaults (inbound=9, target_profit=20). Passing sales tax here only
# ever LOWERS our number, and discordarm arms at min(card, fresh), so erring
# this way errs in the direction that does not cost money.
INBOUND = float(os.environ.get("FLIPSCOUT_INBOUND_SHIP", "9"))
TARGET_PROFIT = float(os.environ.get("FLIPSCOUT_TARGET_PROFIT", "20"))


_TOTAL = re.compile(r"Showing\s+[\d,]+\s*-\s*[\d,]+\s+of\s+([\d,]+)\s+lot", re.I)



def parse_total(body: str) -> Optional[int]:
    """How many lots HiBid's own paginator says are on the list."""
    m = _TOTAL.search(body or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def scrape(timeout_s: int = 60) -> dict:
    """Read the watch list. {'ok', 'ids', 'images', 'total', 'error'}.

    🚨 "NOT SIGNED IN" AND "NOTHING WATCHED" MUST NOT LOOK ALIKE. An expired
    session serves the same shell with an empty list, so counting ids alone
    would report a quiet day forever while the sniper heard about nothing.
    `ok` stays False unless the session was positively confirmed.
    """
    from playwright.sync_api import sync_playwright
    out = {"ok": False, "ids": [], "images": {}, "total": None, "error": None}
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            try:
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(WATCH_URL, timeout=timeout_s * 1000,
                        wait_until="domcontentloaded")
                pg.wait_for_timeout(6000)
                if not signed_in(ctx):
                    out["error"] = ("not signed in - run "
                                    "`python -m flipscout.hibidsnipe login`")
                    return out
                # The list paginates at 10 per page by DEFAULT. Ask for 100
                # before counting anything, or a long watch list quietly
                # becomes its first page.
                try:
                    sel = pg.query_selector(
                        'select[name="dataTables_paginate_ipp"]')
                    if sel:
                        sel.select_option("100")
                        pg.wait_for_timeout(3000)
                except Exception:
                    pass                   # shows up as a gap against `total`
                out["total"] = parse_total(pg.inner_text("body") or "")
                for a in pg.query_selector_all('a.lot-link[href*="/lot/"]'):
                    m = re.search(r"/lot/(\d+)", a.get_attribute("href") or "")
                    if not m:
                        continue
                    lid = m.group(1)
                    if lid not in out["ids"]:
                        out["ids"].append(lid)
                    if lid not in out["images"]:
                        img = a.query_selector("img")
                        src = img.get_attribute("src") if img else None
                        if src and src.startswith("http"):
                            out["images"][lid] = src
                out["ok"] = True
            finally:
                ctx.close()
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out



def ceiling_leak(c: dict) -> Optional[float]:
    """What discordarm would ARM this card at, if it disagrees with the card.

    🚨 THE CARD IS THE CONTRACT AND THE READER IS THE JUDGE. `max_bid` is what
    the card MEANS to offer; discordarm decides what a 🎯 actually arms, and it
    decides it by regexing the rendered text. Those two drifted apart the first
    time this module was written (see the `pass` branch above), and the failure
    was invisible: a card that said it could not be armed, armed.

    So ask the real reader, on the real embed, instead of trusting the wording.
    Returns None when they agree, otherwise the figure the reader would use.
    """
    from .discordarm import parse_card
    from .notify import build_embed
    try:
        _, _, seen = parse_card({"content": "", "embeds": [build_embed(c)]})
    except Exception:
        return None                        # cannot check -> do not block a card
    want = c.get("max_bid")
    if want is None:
        return seen
    return None if (seen is not None and abs(seen - want) < 0.005) else seen



# 🚨 run()/card()/main() MOVED TO watchlist.py ON 2026-08-20.
# Leron asked for ShopGoodwill's watch list too, plus a 30-minute call to arm.
# A second module that also read HiBid would have carded every lot twice, so
# the entrypoint lives there now and this file keeps only what it owns: the
# HiBid scrape, and ceiling_leak, which is the guard that a card's TEXT never
# authorises more than its `max_bid` does.

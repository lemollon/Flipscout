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


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def load_state() -> dict:
    """Which lots have already been carded, and when we last looked.

    🚨 A CORRUPT STATE FILE MUST NOT PASS FOR AN EMPTY ONE IN SILENCE. Starting
    clean is the right recovery - the cost is one duplicate card, not a lost
    arm - but it has to be said out loud, or a re-carded watch list reads as a
    bug in the alerting rather than a damaged file.
    """
    try:
        d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("seen"), dict):
            return d
        raise ValueError("state file is not the expected shape")
    except FileNotFoundError:
        return {"seen": {}, "last_run": None}
    except Exception as e:
        print(f"[hibidwatch] :warning: {STATE_PATH.name} is unreadable "
              f"({type(e).__name__}: {e}) - starting a fresh one. Lots already "
              f"carded may be carded once more.")
        return {"seen": {}, "last_run": None}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True),
                          encoding="utf-8")


def due(state: dict, now: Optional[_dt.datetime] = None) -> bool:
    last = state.get("last_run")
    if not last:
        return True
    try:
        prev = _dt.datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=_dt.timezone.utc)
    return ((now or _now()) - prev).total_seconds() >= MIN_GAP_MIN * 60


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


def card(lid: str, d: dict, image: str = "") -> dict:
    """One watched lot -> one Discord card. A ceiling only when it is real."""
    title = (d.get("title") or "").strip()
    prem = float(d.get("premium") or 0)
    tax = float(d.get("tax") or 0)
    price = d.get("high_bid")
    step = min_increment(price or 0, d.get("increments"))
    m = match(title)

    ends = None
    try:
        secs = float(d.get("left"))
        ends = (f"~{secs / 60:.0f} min" if secs < 5400
                else f"~{secs / 3600:.1f} h")
    except (TypeError, ValueError):
        pass

    c = {
        "title": title[:240],
        "url": LOT_URL.format(lid),
        "buy_url": LOT_URL.format(lid),
        "image": image or None,
        "source": "hibid (your watch list)",
        "bids": d.get("bids"),
        "ends": ends,
        "listing_type": "auction",
        "buyer_premium_rate": prem,
        "sales_tax_rate": tax,
        "verdict": "watch",
    }

    if not m:
        # 🚨 NOT A REJECTION - AN ADMISSION. The book covering nothing like this
        # says something about the book, not about the lot. He watched it, so
        # it is still worth telling him it is live; what Flipscout cannot do is
        # put a number on it.
        c["reason"] = (
            ":grey_question: **You watched this. The book has no comp for it,** "
            "so Flipscout will not name a ceiling - reply `snipe <amount>` to "
            "this card with the max hammer bid you want and it arms at exactly "
            "that."
            + (f"\n_Buyer's premium {prem * 100:.4g}% is charged on top of the "
               f"hammer._" if prem else ""))
        return c

    ceiling = book_ceiling(title, premium=prem, inbound=INBOUND,
                           target_profit=TARGET_PROFIT, tax=tax)
    adv = advise(m.model.comp, units=m.units, inbound_shipping=INBOUND,
                 outbound_shipping=m.model.outbound_shipping,
                 target_profit=TARGET_PROFIT, current_price=price,
                 min_bid=d.get("min_bid"), increment=float(step or 1.0),
                 bid_count=int(d.get("bids") or 0),
                 buyer_premium_rate=prem, sales_tax_rate=tax)
    c["comp"] = m.model.comp
    c["open_bid"] = adv.open_bid

    if ceiling is None or (adv.open_bid or 0) > ceiling:
        # 🚨 PRINT NO CEILING RATHER THAN ONE HE CANNOT USE. A ceiling below the
        # next valid bid is not a bid at all, and showing it invites a tap that
        # arms a number the site would reject.
        c["verdict"] = "pass"
        # 🚨 MIND THE WORDING, NOT JUST THE FIELD. discordarm reads a ceiling
        # out of the card's TEXT - "ceiling"/"max bid" followed by a dollar
        # figure - and it reads every embed field, not just the money ones. The
        # first draft of this branch printed no max_bid field and still said
        # "The book's ceiling is $10.42", which parsed as a live ceiling: a card
        # promising 🎯 would not arm it, that armed it. Say the number without
        # the trigger words, and let _ceiling_leak below catch any relapse.
        c["reason"] = (
            ":no_entry: **Already past what it is worth.** The next valid bid is "
            f"**${(adv.open_bid or 0):,.2f}**"
            + (f" ({d.get('bids')} bids)" if d.get("bids") else "")
            + (f", and the book stops at **${ceiling:,.2f}** hammer."
               if ceiling is not None
               else ", and the book will not price this at all.")
            + "\nNo bid line is printed on purpose, so 🎯 will not arm it. To "
              "take it anyway, reply `snipe <amount>`.")
        return c

    c["verdict"] = "buy"
    c["max_bid"] = ceiling
    c["reason"] = (
        f":eyes: **From your HiBid watch list.** Tap 🎯 to arm the snipe at "
        f"**${ceiling:,.2f}** hammer"
        + (f" (~${ceiling * (1 + prem):,.2f} all-in after the "
           f"{prem * 100:.4g}% premium)" if prem else "")
        + f".\nComp ${m.model.comp:,.2f}"
        + (f" · clears ~${adv.profit_at_open:,.2f} if it stops at "
           f"${(adv.open_bid or 0):,.2f}" if adv.profit_at_open else "")
        + "\n**Arming places no bid** - it fires about 3 minutes before the "
          "close."
        + ("\n:lock: You must be REGISTERED for this auction or the sniper "
           "refuses it." if d.get("registered") is not True else ""))
    return c


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


def run(dry_run: bool = False, force: bool = False, notifier=notify_rich) -> int:
    state = load_state()
    if not force and not due(state):
        print(f"[hibidwatch] last run under {MIN_GAP_MIN:.0f} min ago - skipping")
        return 0

    wl = scrape()
    if not wl["ok"]:
        # 🚨 LOUD. A read that FAILED and a watch list that is EMPTY produce the
        # same zero cards, so the difference has to be said out loud or the feed
        # dies without anybody noticing.
        msg = (f":warning: **Flipscout could not read your HiBid watch list** - "
               f"{wl['error']}. Nothing you watch is being carded until this is "
               f"fixed.")
        print(f"[hibidwatch] {msg}")
        if not dry_run:
            try:
                notify(msg, subject="Flipscout HiBid watch list")
            except Exception:
                pass
        return 1

    ids, total = wl["ids"], wl["total"]
    state["last_run"] = _now().isoformat()
    print(f"[hibidwatch] watch list: {len(ids)} lot(s) read"
          + (f", HiBid reports {total}" if total is not None else ""))
    # 🚨 NO SILENT CAPS. If the paginator says there are more than we picked up,
    # the shortfall is named rather than quietly dropped.
    if total is not None and total > len(ids):
        print(f"[hibidwatch] :warning: {total - len(ids)} watched lot(s) were "
              f"NOT read - the list runs past what one page returned. They are "
              f"not being carded.")

    armed = load_armed()
    seen = state["seen"]
    cards, carded = [], []
    for lid in ids:
        if lid in armed:
            continue                       # already decided, already armed
        if lid in seen:
            continue                       # carded once; silence means no
        try:
            d = detail(lid)
        except Exception as e:
            print(f"[hibidwatch] {lid}: lookup failed ({type(e).__name__}) - "
                  f"leaving it for the next run")
            continue
        if d.get("gone") or d.get("closed"):
            continue
        c = card(lid, d, wl["images"].get(lid, ""))
        leak = ceiling_leak(c)
        if leak is not None:
            # Never post a card whose text authorises a number its own author
            # did not intend. Dropping it costs one alert; sending it can cost
            # a bid.
            print(f"[hibidwatch] :warning: {lid}: card would arm at ${leak:,.2f} "
                  f"but its ceiling is {c.get('max_bid')} - NOT posting it. "
                  f"This is a wording bug in card(), not a HiBid problem.")
            continue
        cards.append(c)
        carded.append(lid)

    if not cards:
        print("[hibidwatch] nothing new on the watch list")
        if not dry_run:
            save_state(state)
        return 0

    header = (f"👀 **{len(cards)} lot(s) from your HiBid watch list** — you "
              f"picked these, so every one gets a card even where the book has "
              f"no opinion.")
    print(header)
    for c in cards:
        print(f"  - [{c['verdict']}] {c['title'][:60]} "
              + (f"ceiling ${c['max_bid']:,.2f}" if c.get("max_bid")
                 else "NO ceiling"))
    if dry_run:
        return 0

    notifier(cards, content=header)
    for lid in carded:
        seen[lid] = _now().isoformat()
    save_state(state)
    return 0


def main(argv=None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    from .mybids import load_env_file
    load_env_file()
    return run(dry_run="--dry-run" in argv or "--dry" in argv,
               force="--force" in argv)


if __name__ == "__main__":
    raise SystemExit(main())

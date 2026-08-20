"""Both watch lists, and the 30-minute call to arm.

Leron, 2026-08-20: "as I look through goodwill and hibid there will be items I
will put on a watchlist - capture all of those and let me know in discord when
the auction is 30 mins from close so I can arm the snipe."

So there are two jobs here, and they are NOT the same job:

  DISCOVERY  - you hearted something; here is what the book thinks of it.
               Fires once, whenever it first appears on a list.
  THE CALL   - that thing closes in half an hour. Arm it or lose it.
               Fires once, at T-30, whether or not you ever saw the first card.

The second is the one he asked for and the one that matters: a card sent the
moment you heart a lot is easy to scroll past three days before it closes.

WHY THIS REPLACED hibidwatch.py RATHER THAN SITTING BESIDE IT
-------------------------------------------------------------
hibidwatch already read the HiBid list and carded new lots. Adding a second
module that also read HiBid would have carded every lot twice, so this absorbs
it. The HiBid scraping is unchanged - see `hibid_ids`.

🚨 TWO CLOCKS, TWO COSTS. Reading a watch list needs a BROWSER (both sites put
it behind a login), which is far too expensive to run every minute. Reading a
single lot's close time is a plain HTTP call on both sites. So the list refresh
is throttled and the close-time check is not: ids are cached, and every run
re-checks the cached ids cheaply. That is what makes a T-30 alert land near 30
minutes instead of "somewhere in the last 20-minute bucket".

WHAT IT NEVER DOES
------------------
It never bids, never arms, never favourites or un-favourites anything. It reads
two pages and sends messages. Arming stays a tap on the card.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
from typing import Optional

# ceiling_leak is re-exported on purpose: it is the guard that a card's TEXT
# never authorises more than its `max_bid` does, and every card built here
# goes through it before it is sent.
from .hibidwatch import ceiling_leak
from .notify import notify, notify_rich

REPO = pathlib.Path(__file__).resolve().parent.parent
STATE_PATH = REPO / "watchlist_state.json"

GW_FAVORITES = "https://shopgoodwill.com/shopgoodwill/favorites"
GW_ITEM = "https://shopgoodwill.com/item/{}"
HB_LOT = "https://hibid.com/lot/{}"

# How close to the end the call goes out. Leron asked for 30 minutes.
CLOSING_MIN = float(os.environ.get("FLIPSCOUT_WATCH_CLOSING_MIN", "30"))
# 🚨 The alert must not fire so late that arming is pointless - the snipers
# themselves refuse to start a bid inside ~25s, and a tap needs longer than
# that. Below this, say so instead of pretending there is time.
TOO_LATE_MIN = float(os.environ.get("FLIPSCOUT_WATCH_TOO_LATE_MIN", "4"))
# The browser-backed list refresh. The cheap close-time check runs every time.
LIST_GAP_MIN = float(os.environ.get("FLIPSCOUT_WATCH_LIST_GAP_MIN", "20"))


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def load_state() -> dict:
    """🚨 A corrupt state file re-cards, it does not go quiet - and it says so."""
    try:
        d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("seen"), dict):
            d.setdefault("warned", {})
            d.setdefault("ids", {})
            return d
        raise ValueError("state file is not the expected shape")
    except FileNotFoundError:
        return {"seen": {}, "warned": {}, "ids": {}, "last_list": None}
    except Exception as e:
        print(f"[watchlist] :warning: {STATE_PATH.name} is unreadable "
              f"({type(e).__name__}: {e}) - starting fresh. Items already "
              f"carded may be carded once more.")
        return {"seen": {}, "warned": {}, "ids": {}, "last_list": None}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True),
                          encoding="utf-8")


def list_due(state: dict, now: Optional[_dt.datetime] = None) -> bool:
    last = state.get("last_list")
    if not last:
        return True
    try:
        prev = _dt.datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=_dt.timezone.utc)
    return ((now or _now()) - prev).total_seconds() >= LIST_GAP_MIN * 60


# --- reading the two lists ---------------------------------------------------

_GW_COUNT = re.compile(r"Open\s+Items\s*(\d+)", re.I)


def goodwill_ids(timeout_s: int = 60) -> dict:
    """ShopGoodwill Favorites. {'ok', 'ids', 'total', 'error'}.

    🚨 "NOT SIGNED IN" AND "NOTHING FAVOURITED" MUST NOT LOOK ALIKE, and on this
    site they very nearly do: the API answers an empty list with
    `status: false, "Records are not available."`, which reads exactly like a
    failure. So the session is confirmed positively off the page chrome, and an
    empty list is a normal, successful, zero-item answer.
    """
    from playwright.sync_api import sync_playwright
    from .snipe import PROFILE_DIR
    out = {"ok": False, "ids": [], "total": None, "error": None}
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            try:
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(GW_FAVORITES, timeout=timeout_s * 1000,
                        wait_until="domcontentloaded")
                pg.wait_for_timeout(7000)
                body = pg.inner_text("body") or ""
                low = body.lower()
                if "sign out" not in low and "my favorites" not in low:
                    out["error"] = ("not signed in - run "
                                    "`python -m flipscout.snipe login`")
                    return out
                m = _GW_COUNT.search(body.replace("\n", " "))
                out["total"] = int(m.group(1)) if m else None
                for href in pg.evaluate(
                        """() => [...document.querySelectorAll('a[href*="/item/"]')]
                                 .map(a => a.getAttribute('href') || '')"""):
                    g = re.search(r"/item/(\d+)", href)
                    if g and g.group(1) not in out["ids"]:
                        out["ids"].append(g.group(1))
                out["ok"] = True
            finally:
                ctx.close()
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def hibid_ids() -> dict:
    """HiBid watch list - the reader that was hibidwatch.scrape()."""
    from .hibidwatch import scrape
    return scrape()


# --- what one watched item is worth ------------------------------------------

def _gw_image(d: dict) -> str:
    """First photo of a ShopGoodwill item, assembled from its two fields."""
    server = (d.get("imageServer") or "").strip().rstrip("/")
    paths = (d.get("imageUrlString") or "").strip()
    if not server or not paths:
        return ""
    first = paths.split(";")[0].replace("\\", "/").lstrip("/")
    return f"{server}/{first}" if first else ""


def _goodwill_item(iid: str) -> Optional[dict]:
    from . import snipe
    d = snipe.detail(iid)
    if d.get("isItemEndTimeExpire"):
        return None
    title = (d.get("title") or "").strip()
    inbound = float(d.get("handlingPrice") or 0)
    return {
        "site": "goodwill", "id": iid, "title": title,
        "url": GW_ITEM.format(iid),
        "left": snipe.seconds_left(d),
        "price": d.get("currentPrice"),
        "next_bid": d.get("minimumBid"),
        "bids": snipe.bid_count(d),
        "ceiling": snipe.book_ceiling(title, inbound=inbound),
        "premium": 0.0,
        # 🚨 THIS LINE WAS `(d.get("imageServer") or "") and ""`, which is
        # always the empty string - so every ShopGoodwill card shipped without
        # a picture. The server and the path are two separate fields and the
        # path is a semicolon-separated list of BACKSLASHED relative paths.
        "image": _gw_image(d),
    }


def _hibid_item(lid: str) -> Optional[dict]:
    from . import hibidsnipe as H
    d = H.detail(lid)
    # 🚨 A page that belongs to a different lot tells us nothing about this one.
    if d.get("mismatch") or d.get("gone") or d.get("closed"):
        return None
    title = (d.get("title") or "").strip()
    prem = float(d.get("premium") or 0)
    return {
        "site": "hibid", "id": lid, "title": title,
        "url": HB_LOT.format(lid),
        "left": H.seconds_left(d),
        "price": d.get("high_bid"),
        "next_bid": d.get("min_bid"),
        "bids": d.get("bids"),
        "ceiling": H.book_ceiling(title, premium=prem,
                                 tax=float(d.get("tax") or 0)),
        "premium": prem,
        # Was hardcoded empty when this moved out of hibidwatch, which is why
        # the HiBid cards arrived with no images. See hibidsnipe._og_image.
        "image": d.get("image") or "",
    }


FETCH = {"goodwill": _goodwill_item, "hibid": _hibid_item}


def _mins(left: Optional[float]) -> str:
    if left is None:
        return "unknown"
    return f"{left/60:.0f} min" if left < 5400 else f"{left/3600:.1f} h"


def card(it: dict, closing: bool) -> dict:
    """One watched item -> one Discord card.

    A ceiling is printed ONLY when the book has a real one, because discordarm
    reads the arm figure out of the card and a number nobody chose is not
    authorisation. See hibidwatch.ceiling_leak for what that costs.
    """
    ceil = it.get("ceiling")
    site = "ShopGoodwill" if it["site"] == "goodwill" else "HiBid"
    c = {
        "title": (it["title"] or "")[:240],
        "url": it["url"], "buy_url": it["url"],
        "source": f"{site} watch list",
        "bids": it.get("bids"),
        "ends": _mins(it.get("left")),
        "listing_type": "auction",
        "open_bid": it.get("next_bid"),
        "verdict": "buy" if (closing and ceil) else "watch",
    }
    if it.get("image"):
        c["image"] = it["image"]

    head = (f":alarm_clock: **CLOSES IN {_mins(it.get('left'))}** - arm it now "
            f"or lose it." if closing else
            f":eyes: **From your {site} watch list.**")

    if ceil and (it.get("next_bid") or 0) <= ceil:
        c["max_bid"] = round(ceil, 2)
        allin = (f" (~${ceil * (1 + it['premium']):,.2f} all-in after the "
                 f"{it['premium']*100:.4g}% premium)" if it.get("premium") else "")
        c["reason"] = (
            f"{head}\nTap 🎯 to arm at **${ceil:,.2f}**{allin}.\n"
            f"**Arming places no bid** - it fires about 3 minutes before the "
            f"close.")
    else:
        # 🚨 No number printed, so 🎯 cannot arm it - deliberately. Say which
        # of the two reasons applies; they call for different actions.
        why = ("the book has no comp for this" if not ceil else
               f"it is already past the book's **${ceil:,.2f}**")
        c["reason"] = (f"{head}\nNo ceiling is printed because {why}, so 🎯 will "
                       f"not arm it. Reply `snipe <amount>` with your own max.")
    return c


# --- the run -----------------------------------------------------------------

def run(dry_run: bool = False, force: bool = False, notifier=notify_rich) -> int:
    state = load_state()
    ids = {k: list(v) for k, v in (state.get("ids") or {}).items()}
    failures = []

    # 1. refresh the two lists, on the slow clock
    if force or list_due(state):
        for site, reader in (("hibid", hibid_ids), ("goodwill", goodwill_ids)):
            r = reader()
            if not r["ok"]:
                failures.append(f"{site}: {r['error']}")
                continue
            ids[site] = r["ids"]
            gap = ((r["total"] - len(r["ids"]))
                   if r["total"] is not None else 0)
            print(f"[watchlist] {site}: {len(r['ids'])} watched"
                  + (f", site says {r['total']}" if r["total"] is not None else "")
                  + (f"  :warning: {gap} NOT read" if gap > 0 else ""))
        state["ids"] = ids
        state["last_list"] = _now().isoformat()

    if failures:
        # 🚨 A list that failed to load and a list with nothing on it both
        # produce zero cards. Only one of them means watched items are going
        # unwatched right now.
        msg = (":warning: **Could not read a watch list** - "
               + "; ".join(failures) + ". Items on it are NOT being watched.")
        print(f"[watchlist] {msg}")
        if not dry_run:
            try:
                notify(msg, subject="Flipscout watch list")
            except Exception:
                pass

    # 2. re-price every cached id on the fast clock
    seen, warned = state["seen"], state["warned"]
    cards, mark_seen, mark_warned = [], [], []
    for site, id_list in ids.items():
        for iid in id_list:
            key = f"{site}:{iid}"
            try:
                it = FETCH[site](iid)
            except Exception as e:
                print(f"[watchlist] {key}: lookup failed ({type(e).__name__})")
                continue
            if not it:
                continue                       # ended, or not this lot
            left = it.get("left")
            if left is None or left <= 0:
                continue
            closing = left <= CLOSING_MIN * 60
            if closing and key not in warned:
                if left <= TOO_LATE_MIN * 60:
                    print(f"[watchlist] {key}: only {_mins(left)} left - too "
                          f"late to arm, not sending a call to act")
                    mark_warned.append(key)
                    continue
                cards.append((key, card(it, closing=True), True))
            elif key not in seen and not closing:
                cards.append((key, card(it, closing=False), False))

    # 3. send, dropping any card whose text disagrees with its own ceiling
    out = []
    for key, c, is_call in cards:
        leak = ceiling_leak(c)
        if leak is not None:
            print(f"[watchlist] :warning: {key}: card would arm at ${leak:,.2f} "
                  f"but its ceiling is {c.get('max_bid')} - NOT posting it.")
            continue
        out.append(c)
        (mark_warned if is_call else mark_seen).append(key)

    if not out:
        print("[watchlist] nothing new and nothing closing")
        if not dry_run:
            for k in mark_warned:
                warned[k] = _now().isoformat()
            save_state(state)
        return 1 if failures else 0

    calls = sum(1 for _, c, is_call in cards if is_call)
    header = (f"⏰ **{calls} watched item(s) closing within {CLOSING_MIN:.0f} "
              f"minutes** - arm now." if calls else
              f"👀 **{len(out)} new item(s) on your watch lists.**")
    print(header)
    for c in out:
        print(f"  - [{c.get('verdict','?')}] {c['title'][:56]} "
              f"ends {c.get('ends')} "
              + (f"ceiling ${c['max_bid']:,.2f}" if c.get("max_bid") else "NO ceiling"))
    if dry_run:
        return 0

    notifier(out, content=header)
    now = _now().isoformat()
    for k in mark_seen:
        seen[k] = now
    for k in mark_warned:
        warned[k] = now
        seen.setdefault(k, now)
    save_state(state)
    return 1 if failures else 0


def main(argv=None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    from .mybids import load_env_file
    load_env_file()
    return run(dry_run="--dry-run" in argv or "--dry" in argv,
               force="--force" in argv)


if __name__ == "__main__":
    raise SystemExit(main())

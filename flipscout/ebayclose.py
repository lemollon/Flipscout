"""The last call. eBay auctions, ten minutes out.

Leron, 2026-08-20: "for flipscout you sent me pokemon cards that have 2hrs left
on ebay i dont want to sit and watch is there a way you can send them to me with
10 mins left? so i can snipe it. i dont mind the initial send but also i want to
snipe it."

WHAT WAS MISSING
----------------
Every other source already has an endgame. ShopGoodwill and HiBid have real
snipers (`snipe.py`, `hibidsnipe.py`) that fire ~3 minutes before the close, and
`watchlist.py` calls out anything he hearted at T-30 so it can be armed with one
tap. eBay had neither. The only thing eBay ever sent was hunt's ENDING SOON tier
at T-2h - which is the alert he is describing, and it is the WRONG END of the
auction to act on. On eBay the price two hours out is meaningless; the whole
auction happens in the final minute.

🚨 WHY THIS IS AN ALERT AND NOT A SNIPER
-----------------------------------------
It cannot place the bid. eBay bidding needs a signed-in session, and unlike
ShopGoodwill and HiBid there is no `.ebayprofile` in this repo and no consented
login to drive. A plain HTTP GET of an item page answers **403** from this box
(measured 2026-08-20), so even reading the live price without app keys is out.
So this does the one thing it can do honestly: put the lot in front of him with
enough time to bid himself, and never imply it did more.

That is also why these cards are posted with `seed=False`. `notify_rich` seeds
🎯 🔥 ❌ on every card it posts, and `discordarm.parse_card` only recognises
shopgoodwill.com/item and hibid.com/lot links - so a 🎯 chip on an eBay card is
a button that silently does nothing. A dead chip on a card that says LAST CALL
is worse than no chip: it reads as armed.

TWO CLOCKS, AND ONLY ONE OF THEM COSTS ANYTHING
------------------------------------------------
Same shape as `watchlist.py`, and for the same reason - but cheaper, because an
eBay end time is HARD. There is no soft close and no extension, so once we know
`itemEndDate` the countdown is pure arithmetic and needs no network at all:

  THE QUEUE   - who is closing, read off the deals board. Network. Throttled to
                FLIPSCOUT_EBAY_BOARD_GAP_MIN (default 5) because the board only
                regenerates every ~30 minutes anyway.
  THE CALL    - is anything inside T-10 right now. Free. Runs every minute,
                which is what makes the call land at ~10 minutes instead of
                "somewhere in the last half-hour bucket".

🚨 THE BOARD IS FETCHED OVER HTTP, NOT READ OFF DISK. The hunt runs on GitHub
Actions and commits `docs/deals.json`; the local copy only moves when
RepoAutoPull fires, and that task repeats **hourly**. Reading the file would
mean a queue up to an hour stale - long enough to miss the entire window for a
lot that was discovered with 40 minutes left. The local file stays as the
fallback for when the network is down, and it says so in the log rather than
pretending it was current.

WHAT IT NEVER DOES
------------------
It never bids, never signs in, never writes to eBay, and never seeds an arm
chip. It reads one JSON file and sends messages.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
from typing import Optional

import requests

from .hunt import hours_until
from .notify import notify, notify_rich

REPO = pathlib.Path(__file__).resolve().parent.parent
STATE_PATH = REPO / "ebay_closing_state.json"
LOCAL_BOARD = REPO / "docs" / "deals.json"
BOARD_URL = os.environ.get(
    "FLIPSCOUT_BOARD_URL",
    "https://raw.githubusercontent.com/lemollon/Flipscout/main/docs/deals.json")

# How close to the end the call goes out. Leron asked for 10 minutes.
CALL_MIN = float(os.environ.get("FLIPSCOUT_EBAY_CALL_MIN", "10"))
# 🚨 A call he cannot act on is worse than silence. Placing a bid means opening
# the app, signing in and typing a number; under this many minutes that is not
# happening, so say nothing rather than send a card that only causes panic.
TOO_LATE_MIN = float(os.environ.get("FLIPSCOUT_EBAY_TOO_LATE_MIN", "1.5"))
# The queue refresh. The free clock check runs every time.
BOARD_GAP_MIN = float(os.environ.get("FLIPSCOUT_EBAY_BOARD_GAP_MIN", "5"))
# Never fire more than this many last calls in one minute. A single burst of
# eBay lots all closing together would otherwise be twenty pings in one screen.
MAX_CARDS = int(os.environ.get("FLIPSCOUT_EBAY_CALL_MAX", "6"))
# 🚨 THE BACKSTOP, AND THE REASON THIS IS NOT A FEED. MEASURED 2026-08-20:
# the live board carried 184 eBay auctions, 77 of them closing inside twelve
# hours - a T-10 call on all of them is ~150 pings a day. An alert that fires
# 150 times a day is not a last call, it is a feed, and it gets muted. The
# real filter is `alerted` (below); this is the belt to its braces, and what
# it drops is always printed - never a silent truncation.
DAILY_MAX = int(os.environ.get("FLIPSCOUT_EBAY_CALL_DAILY_MAX", "12"))
# Queue the whole board instead of only what he was sent. Off by default,
# for exactly the volume reason above.
CALL_ALL = bool((os.environ.get("FLIPSCOUT_EBAY_CALL_ALL") or "").strip())
# Only used when the board predates the `alerted` flag - see `merge`.
FALLBACK_SHARE = float(os.environ.get("FLIPSCOUT_EBAY_CALL_SHARE", "0.5"))
FALLBACK_PROFIT = float(os.environ.get("FLIPSCOUT_EBAY_CALL_PROFIT", "60"))
# Drop dead entries this long after their close, so the queue cannot grow
# without bound and a re-run cannot re-card something already over.
PURGE_AFTER_MIN = 120.0

_ITEM_ID = re.compile(r"/itm/(?:[^/]+/)?(\d{9,})")
_TIMEOUT = 20


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def item_id(url: str) -> Optional[str]:
    """The numeric eBay item id out of a listing url.

    🚨 The board carries no id field for eBay - `board.write` keeps title/url/
    money and drops `row['id']` - so the url IS the identity here. Both shapes
    appear in the wild: /itm/123456789012 and /itm/some-slug/123456789012.
    """
    m = _ITEM_ID.search(url or "")
    return m.group(1) if m else None


# --- state -------------------------------------------------------------------

def load_state() -> dict:
    """🚨 A corrupt state file re-cards, it does not go quiet - and it says so.

    The alternative - returning an empty queue on a parse error - is the exact
    failure that made a corrupt `snipe_armed.json` erase every arm and look
    like a quiet day (2026-08-18). Losing the `called` marks costs a duplicate
    card. Losing the queue costs the lot.
    """
    try:
        d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("queue"), dict):
            d.setdefault("called", {})
            d.setdefault("last_board", None)
            return d
        raise ValueError("state file is not the expected shape")
    except FileNotFoundError:
        return {"queue": {}, "called": {}, "last_board": None}
    except Exception as e:
        print(f"[ebayclose] :warning: {STATE_PATH.name} is unreadable "
              f"({type(e).__name__}: {e}) - starting fresh. Lots already "
              f"called may be called once more.")
        return {"queue": {}, "called": {}, "last_board": None}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True),
                          encoding="utf-8")


def board_due(state: dict, now: Optional[_dt.datetime] = None) -> bool:
    last = state.get("last_board")
    if not last:
        return True
    try:
        prev = _dt.datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=_dt.timezone.utc)
    return ((now or _now()) - prev).total_seconds() >= BOARD_GAP_MIN * 60


# --- the queue ---------------------------------------------------------------

def fetch_board(session=None) -> dict:
    """The published deals board. {'ok', 'items', 'generated', 'via', 'error'}.

    🚨 "COULD NOT READ THE BOARD" AND "NOTHING IS CLOSING" MUST NOT LOOK ALIKE.
    Both produce zero cards, and only one of them means auctions are running out
    unwatched right now. `ok` is False unless something was actually read.
    """
    out = {"ok": False, "items": [], "generated": None, "via": None,
           "error": None}
    errs = []
    session = session or requests
    try:
        r = session.get(BOARD_URL, timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json() or {}
        out.update(ok=True, items=d.get("items") or [],
                   generated=d.get("generated"), via="github")
        return out
    except Exception as e:
        errs.append(f"http: {type(e).__name__}: {e}")
    try:
        d = json.loads(LOCAL_BOARD.read_text(encoding="utf-8"))
        out.update(ok=True, items=d.get("items") or [],
                   generated=d.get("generated"), via="local file")
        # Not an error, but never silent: the local copy only moves when
        # RepoAutoPull fires, which is hourly.
        print(f"[ebayclose] :warning: board came from the LOCAL file "
              f"({errs[0]}) - it may be up to an hour behind.")
        return out
    except Exception as e:
        errs.append(f"file: {type(e).__name__}: {e}")
    out["error"] = "; ".join(errs)
    return out


def _entry(row: dict, now: Optional[_dt.datetime] = None) -> Optional[dict]:
    """One board row -> one queue entry, or None if it is not our business."""
    if (row.get("source") or "") != "ebay":
        return None
    if (row.get("listing_type") or "") != "auction":
        return None                       # nothing "ends" on a buy-now ask
    iid = item_id(row.get("url") or "")
    if not iid:
        return None
    # 🚨 source="ebay" is load-bearing: itemEndDate is UTC, and without the
    # zone `hours_until` compares it to a NAIVE LOCAL clock. On this box
    # (America/Chicago) that reads five hours long, so a lot ending in ten
    # minutes would look like 5h10m and the call would never fire. See the
    # _ENDS_TZ entry added alongside this module.
    left = hours_until(row.get("ends"), now=now, source="ebay")
    if left is None or left <= 0:
        return None
    return {
        "id": iid,
        "title": (row.get("title") or "").strip(),
        "url": row.get("url") or "",
        "ends": row.get("ends") or "",
        "image": row.get("image") or "",
        "comps_url": row.get("comps_url") or "",
        "model": row.get("model") or "",
        "comp": row.get("comp"),
        "bids": row.get("bids"),
        "open_bid": row.get("open_bid"),
        "max_bid": row.get("max_bid"),
        "profit_at_open": row.get("profit_at_open"),
        "seen_at": (now or _now()).isoformat(),
    }


def _worth_calling(row: dict, has_flag: bool) -> bool:
    """Is this one of HIS lots, or just something on the board?"""
    if has_flag:
        return bool(row.get("alerted"))
    ceil = row.get("max_bid") or 0
    nxt = row.get("open_bid")
    if ceil <= 0 or nxt is None or nxt > FALLBACK_SHARE * ceil:
        return False
    return (row.get("profit_at_open") or 0) >= FALLBACK_PROFIT


def merge(state: dict, items: list, now: Optional[_dt.datetime] = None) -> int:
    """Fold the board's eBay auctions into the queue. Returns how many are new.

    🚨 THE QUEUE OUTLIVES THE BOARD. A board row is only what the last hunt
    happened to see, and the eBay auction pass is gated to the :17 run
    (`hunters.EbayBrowse.__init__`) - so a lot alerted at 15:17 with two hours
    left is simply ABSENT from the 15:47 board and every board after it. Keying
    our own queue is what makes the T-10 call survive that gap; re-reading the
    board each minute never would.
    """
    q = state["queue"]
    fresh = 0
    # 🚨 NEWS, NOT INVENTORY. `alerted` is set by board.item() off hunt's
    # seen-cache: True means Leron was actually sent a card for this lot. He
    # asked for a second look at the ones he got, and the board is ~150
    # closing auctions a day. A board written before the flag existed has the
    # key on NO row - which is indistinguishable from "nothing was alerted",
    # so detect the schema rather than the value and fall back to a tight
    # quality bar instead of going silent.
    has_flag = any("alerted" in (r or {}) for r in items or [])
    if not (has_flag or CALL_ALL):
        print("[ebayclose] :warning: this board predates the `alerted` flag - "
              f"falling back to open_bid <= {FALLBACK_SHARE:g} x ceiling and "
              f"${FALLBACK_PROFIT:,.0f}+ profit at the current price.")
    for row in items or []:
        e = _entry(row, now=now)
        if not e:
            continue
        if not CALL_ALL and not _worth_calling(row, has_flag):
            continue
        if e["id"] not in q:
            fresh += 1
            q[e["id"]] = e
        else:
            # Refresh the money and the bid count; keep the original sighting.
            q[e["id"]].update({k: e[k] for k in
                               ("open_bid", "max_bid", "bids", "comp", "ends",
                                "image", "comps_url", "title", "url")})
    return fresh


def purge(state: dict, now: Optional[_dt.datetime] = None) -> int:
    now = now or _now()
    gone = []
    for iid, e in list(state["queue"].items()):
        left = hours_until(e.get("ends"), now=now, source="ebay")
        if left is None or left * 60 < -PURGE_AFTER_MIN:
            gone.append(iid)
    for iid in gone:
        state["queue"].pop(iid, None)
        state["called"].pop(iid, None)
    return len(gone)


# --- the call ----------------------------------------------------------------

def _mins(left_h: Optional[float]) -> str:
    if left_h is None:
        return "unknown"
    m = left_h * 60
    return f"{m:.0f} min" if m < 90 else f"{left_h:.1f} h"


def due(state: dict, now: Optional[_dt.datetime] = None) -> list:
    """Queue entries inside the call window that are still worth bidding on."""
    now = now or _now()
    out = []
    for iid, e in state["queue"].items():
        if iid in state["called"]:
            continue
        left = hours_until(e.get("ends"), now=now, source="ebay")
        if left is None:
            continue
        mins = left * 60
        if mins > CALL_MIN or mins <= 0:
            continue
        if mins <= TOO_LATE_MIN:
            # Marked called so it never fires later at an even worse moment.
            print(f"[ebayclose] {iid}: only {mins:.1f} min left - too late to "
                  f"bid, not sending a call to act")
            state["called"][iid] = now.isoformat()
            continue
        ceil, nxt = e.get("max_bid"), e.get("open_bid")
        if ceil is None or ceil <= 0:
            print(f"[ebayclose] {iid}: no ceiling on the board row - skipped")
            state["called"][iid] = now.isoformat()
            continue
        if nxt is not None and nxt > ceil:
            # 🚨 Not news you can use. It already ran past the number that
            # clears the target profit, so bidding is losing money on purpose.
            print(f"[ebayclose] {iid}: at ${nxt:,.2f} vs ${ceil:,.2f} ceiling "
                  f"- ran past your max, no call sent")
            state["called"][iid] = now.isoformat()
            continue
        out.append((iid, e, left))
    out.sort(key=lambda t: t[2])          # soonest first
    return out


def card(e: dict, left_h: float, board_age_min: Optional[float]) -> dict:
    """One closing eBay auction -> one Discord card.

    No ceiling is ever printed that the board did not carry, and no arm chip is
    seeded on it - see the module docstring for why a 🎯 here would be a lie.
    """
    ceil = float(e["max_bid"])
    nxt = e.get("open_bid")
    stale = (f"_Price is from the board {board_age_min:.0f} min ago - the live "
             f"bid is on the page._" if board_age_min is not None else
             "_Check the live bid on the page._")
    c = {
        "title": (e.get("title") or "")[:240],
        "url": e["url"], "buy_url": e["url"],
        "comps_url": e.get("comps_url") or "",
        "source": "eBay - LAST CALL",
        "listing_type": "auction",
        "bids": e.get("bids"),
        "comp": e.get("comp"),
        "open_bid": nxt,
        "max_bid": round(ceil, 2),
        "ends": _mins(left_h),
        "verdict": "buy",
        "reason": (
            f":rotating_light: **CLOSES IN {_mins(left_h)}** - bid it now.\n"
            f"eBay ends HARD: there is no soft close and no extension, so the "
            f"winning bid is the one sitting there at the buzzer.\n"
            f"Put in **${ceil:,.2f}** as your maximum and let eBay's proxy "
            f"bidding do the rest - it only pays what it must to stay ahead. "
            f"**Never raise it.** Past ${ceil:,.2f} this stops making money.\n"
            f"{stale}\n"
            f"_Flipscout cannot place this one - eBay needs your own signed-in "
            f"session. This is the reminder, the tap is yours._"),
    }
    if e.get("image"):
        c["image"] = e["image"]
    return c


# --- the run -----------------------------------------------------------------

def run(dry_run: bool = False, force: bool = False, notifier=notify_rich,
        session=None, now: Optional[_dt.datetime] = None) -> int:
    now = now or _now()
    state = load_state()

    # 1. refresh the queue, on the slow clock
    board_age = None
    if force or board_due(state, now=now):
        b = fetch_board(session=session)
        if not b["ok"]:
            # 🚨 Say it out loud. A board we could not read produces exactly as
            # many cards as a board with nothing on it.
            msg = (":warning: **Flipscout could not read the deals board** - "
                   f"{b['error']}. eBay auctions closing right now are NOT "
                   f"being called.")
            print(f"[ebayclose] {msg}")
            if not dry_run:
                try:
                    notify(msg, subject="Flipscout eBay last call")
                except Exception:
                    pass
        else:
            fresh = merge(state, b["items"], now=now)
            state["last_board"] = now.isoformat()
            state["board_generated"] = b.get("generated")
            auctions = sum(1 for r in b["items"]
                           if (r.get("source") == "ebay"
                               and r.get("listing_type") == "auction"))
            print(f"[ebayclose] board via {b['via']}: {len(b['items'])} item(s), "
                  f"{auctions} eBay auction(s), {fresh} new to the queue "
                  f"({len(state['queue'])} queued)")

    gen = state.get("board_generated")
    if gen:
        try:
            g = _dt.datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
            if g.tzinfo is None:
                g = g.replace(tzinfo=_dt.timezone.utc)
            board_age = (now - g).total_seconds() / 60.0
        except (TypeError, ValueError):
            board_age = None

    # 2. the free clock check, every run
    calls = due(state, now=now)
    dropped = purge(state, now=now)
    if dropped:
        print(f"[ebayclose] purged {dropped} ended lot(s) from the queue")

    if not calls:
        print(f"[ebayclose] {len(state['queue'])} queued, nothing inside "
              f"T-{CALL_MIN:g} min")
        if not dry_run:
            save_state(state)
        return 0

    # 🚨 THE DAILY BACKSTOP. Deliberately BEFORE the per-run cap, and the
    # lots it drops are marked called - they are not deferred, they are gone
    # for good, because their auction ends in minutes. That is the trade: a
    # last call that stays rare enough to be read. Both numbers are printed.
    day = now.strftime("%Y-%m-%d")
    if state.get("day") != day:
        state["day"], state["sent_today"] = day, 0
    room = max(0, DAILY_MAX - int(state.get("sent_today") or 0))
    if len(calls) > room:
        dropped = calls[room:]
        print(f"[ebayclose] :warning: DAILY CAP - {state.get('sent_today')} "
              f"call(s) already sent today (max {DAILY_MAX}), so "
              f"{len(dropped)} lot(s) get NO last call:")
        for iid, e, left in dropped:
            print(f"    dropped: {e['title'][:56]} | {_mins(left)} | "
                  f"{e['url']}")
            if not dry_run:
                state["called"][iid] = now.isoformat()
        calls = calls[:room]
    if not calls:
        if not dry_run:
            save_state(state)
        return 0

    over = max(0, len(calls) - MAX_CARDS)
    if over:
        # Never a silent truncation - the ones cut are the ones with the most
        # time left, and they still get called on the next minute's run.
        print(f"[ebayclose] :warning: {len(calls)} due, sending {MAX_CARDS} "
              f"(soonest first); {over} deferred to the next run")
    calls = calls[:MAX_CARDS]

    cards = [card(e, left, board_age) for _, e, left in calls]
    header = (f"⏰ **LAST CALL - {len(cards)} eBay auction"
              f"{'' if len(cards) == 1 else 's'} closing within "
              f"{CALL_MIN:.0f} minutes.** Bid now or let it go.")
    print(header)
    for iid, e, left in calls:
        print(f"  - {e['title'][:56]} | {_mins(left)} | at "
              f"${(e.get('open_bid') or 0):,.2f} vs ${e['max_bid']:,.2f} max "
              f"| {e['url']}")
    if dry_run:
        return 0

    # 🚨 seed=False. discordarm cannot arm an eBay link, so a 🎯 chip under
    # this card would be a button that does nothing on the one card where
    # believing it worked costs the lot.
    sent = notifier(cards, content=header, seed=False)
    if sent:
        for iid, _, _ in calls:
            state["called"][iid] = now.isoformat()
        state["sent_today"] = int(state.get("sent_today") or 0) + len(calls)
        print(f"[ebayclose] DELIVERED {len(cards)} last call(s) "
              f"({state['sent_today']}/{DAILY_MAX} today).")
    else:
        # Not marked called: an undelivered call is not a call. It retries next
        # minute, which is the whole point of running every minute.
        print(f"[ebayclose] NOT DELIVERED - {len(cards)} last call(s) went "
              f"nowhere. Check FLIPSCOUT_ALERT_WEBHOOK.")
    save_state(state)
    return 0 if sent else 1


def main(argv=None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    from .mybids import load_env_file
    load_env_file()
    return run(dry_run="--dry-run" in argv or "--dry" in argv,
               force="--force" in argv)


if __name__ == "__main__":
    raise SystemExit(main())

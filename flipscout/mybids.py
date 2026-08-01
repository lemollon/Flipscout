"""Bid sentry: watch MY active ShopGoodwill bids and shout before I lose them.

The hunter finds deals; this watches the ones I actually bid on. The problem it
solves (2026-07-30): of 13 live bids, 6 were already outbid and 4 sat exactly at
their cap - and every loss happened silently, because ShopGoodwill only emails
you *after* the auction closes. The buy decision happens in the last 90 minutes;
that's when this gets loud.

Where the bid list comes from: the "Auctions in Progress" CSV export from
https://shopgoodwill.com/shopgoodwill/inprogress-auctions saved to Downloads.
Re-export it after placing new bids - the newest matching file wins, so just
downloading again is the whole update flow. (There is no way to read "my bids"
anonymously from the API; the CSV is the source of truth for MY max.)

Watch-only items (no bid yet - waiting to snipe) have no export at all, so
they come from Downloads\flipscout_watchlist.txt: paste one shopgoodwill item
link per line. They get the same countdown pings plus the book's snipe
ceiling; bidding on one later just moves it to the CSV side automatically.

Outbid detection is inferred from proxy-bid math, no login required:
    current > my max  -> OUTBID, definitively (proxy would have defended below it)
    current == my max -> AT CAP: either a tie I lost or my proxy pinned at its
                         limit - one more $1 bid kills me either way
    current < my max  -> my proxy is winning

Timing uses `serverTime` vs `endTime` from the SAME detail response (both
Pacific), so the local clock and timezones never enter into it - raw wall-clock
comparison across zones is exactly the class of bug that blinded FLASHPOINT.

Alert policy (state in FLIPSCOUT_MYBIDS_STATE, alerts via the same Discord
webhook as the hunter):
    * any time:   status flips to OUTBID/AT_CAP -> one alert per flip
    * <=90 min:   losing -> re-alert on EVERY price move (this is the window
                  the whole module exists for)
    * <=90 min:   winning -> one heads-up to watch the close
    * 60 and 30:  countdown ping for winners AND losers, price move or not -
                  one per threshold (Leron kept getting sniped after the
                  single early heads-up)
    * ended:      one closing note - likely won or lost, and at what price
"""

from __future__ import annotations

import csv
import datetime as _dt
import glob
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests

from .bidding import advise, next_valid_bid
from .hunters import _GW_DETAIL, _GW_HEADERS, _TIMEOUT
from .notify import notify_rich
from .pricebook import match

# The endgame window. The sentry runs every ~5 min so a snipe still leaves
# time to counter.
DEFAULT_WINDOW_MIN = 90.0

# Guaranteed countdown pings inside the window, whether or not the price has
# moved. Leron, 7/31: "I need to know when there is an hour and then 30 mins
# left ... you tell with hours left and I always get outbid" - the original
# "an hour and 30 mins left" was one 90-minute warning when he meant TWO
# checkpoints. One alert per threshold per item; jumping straight past both
# (sentry was off) fires once, not twice.
MILESTONES_MIN = (60.0, 30.0)

STATE_FILE_ENV = "FLIPSCOUT_MYBIDS_STATE"
DEFAULT_STATE_FILE = "flipscout_mybids_state.json"

# Where the CSV export lands. Newest file matching the pattern wins, so a fresh
# export (even "... (3).csv") supersedes older ones with zero bookkeeping.
CSV_GLOB = "Auctions in Progress*.csv"


@dataclass(frozen=True)
class Bid:
    item_id: str
    title: str
    my_max: float
    # True for items pulled automatically off the deals board rather than
    # named by Leron. They get exactly ONE alert (the 30-minute snipe call)
    # and no closed note - board turnover would otherwise bury the alerts
    # about auctions he actually chose to follow.
    auto: bool = False

    @property
    def watching(self) -> bool:
        """No bid placed - he's waiting to snipe (Leron, 7/31: "if i bid or
        not sometimes im just waiting til the last minute"). No proxy math
        applies; these get the countdown pings and the book's snipe number."""
        return self.my_max <= 0


def _money(s: str) -> float:
    try:
        return float(re.sub(r"[^0-9.]", "", s or "") or 0)
    except ValueError:
        return 0.0


def find_bids_csv(directory: Optional[str] = None) -> Optional[str]:
    """Newest 'Auctions in Progress*.csv' in Downloads (or FLIPSCOUT_BIDS_DIR)."""
    explicit = os.environ.get("FLIPSCOUT_BIDS_CSV")
    if explicit:
        return explicit if os.path.exists(explicit) else None
    directory = directory or os.environ.get(
        "FLIPSCOUT_BIDS_DIR", os.path.join(os.path.expanduser("~"), "Downloads"))
    paths = glob.glob(os.path.join(directory, CSV_GLOB))
    if not paths:
        return None
    return max(paths, key=os.path.getmtime)


# Watch-only items: one shopgoodwill link (or bare item id) per line, pasted
# into this file next to where the CSV lands. Unlike bids there is no export
# for "things I might snipe", so the file IS the list; lines starting with #
# are comments, and deleting a line stops the watching.
WATCHLIST_NAME = "flipscout_watchlist.txt"


def find_watchlist(directory: Optional[str] = None) -> Optional[str]:
    explicit = os.environ.get("FLIPSCOUT_WATCHLIST_FILE")
    if explicit:
        return explicit if os.path.exists(explicit) else None
    directory = directory or os.environ.get(
        "FLIPSCOUT_BIDS_DIR", os.path.join(os.path.expanduser("~"), "Downloads"))
    path = os.path.join(directory, WATCHLIST_NAME)
    return path if os.path.exists(path) else None


def load_watchlist(path: str) -> list[Bid]:
    """Item ids from pasted links or bare ids. my_max=0 marks them watch-only."""
    out, seen = [], set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.search(r"/item/(\d+)", line) or re.fullmatch(r"(\d+)", line)
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    out.append(Bid(item_id=m.group(1), title="", my_max=0.0))
    except OSError:
        return []
    return out


# Auto-watch: the hunter already publishes every qualifying deal to
# docs/deals.json hourly (the board), with the goodwill end times on the rows.
# Anything closing inside the sentry window is a snipe candidate nobody had to
# paste anywhere - Leron, 7/31, on the watchlist file: "that too much manual
# work". Top-N by profit keeps a busy board from turning into a siren.
BOARD_FILE_ENV = "FLIPSCOUT_BOARD_FILE"
DEFAULT_BOARD_FILE = os.path.join("docs", "deals.json")


def _now_pacific() -> Optional[_dt.datetime]:
    """Board `ends` stamps are naive Pacific (endTime[:16] from the same API
    the sentry calls). Compare them ONLY to a Pacific clock - raw wall-clock
    across zones is the FLASHPOINT class of bug."""
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("America/Los_Angeles")).replace(tzinfo=None)
    except Exception:
        return None


def load_autowatch(board_path: Optional[str] = None,
                   window_min: float = DEFAULT_WINDOW_MIN,
                   top: int = 10,
                   now_pt: Optional[_dt.datetime] = None) -> list[Bid]:
    """Goodwill board deals closing within the window, best profit first.

    This is only a cheap PRE-filter to bound API calls; the alert itself still
    times off serverTime-vs-endTime from the live detail call. [] on any
    failure - a broken board must not take down the bid watching."""
    board_path = board_path or os.environ.get(BOARD_FILE_ENV, DEFAULT_BOARD_FILE)
    now_pt = now_pt or _now_pacific()
    if now_pt is None:
        return []
    try:
        with open(board_path, encoding="utf-8") as f:
            items = (json.load(f) or {}).get("items") or []
    except Exception:
        return []
    keep = []
    for r in items:
        if r.get("source") != "goodwill" or not r.get("ends"):
            continue
        m = re.search(r"/item/(\d+)", r.get("url") or "")
        if not m:
            continue
        try:
            left = (_dt.datetime.fromisoformat(r["ends"]) - now_pt).total_seconds() / 60.0
        except ValueError:
            continue
        # Small negative slack: a board built an hour ago may be slightly
        # stale; the live call decides whether it's really over.
        if -10 <= left <= window_min:
            keep.append((float(r.get("profit_at_open") or 0), m.group(1),
                         (r.get("title") or "").strip()))
    keep.sort(key=lambda t: -t[0])
    return [Bid(item_id=iid, title=title, my_max=0.0, auto=True)
            for _, iid, title in keep[:top]]


def load_bids(path: str) -> list[Bid]:
    out: list[Bid] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            iid = (row.get("Item ID") or "").strip()
            if not iid.isdigit():
                continue
            # A non-empty Status ("Auction lost") means the closed-auctions
            # export was pointed at us by mistake - those need no watching.
            if (row.get("Status") or "").strip():
                continue
            out.append(Bid(item_id=iid,
                           title=(row.get("Item") or "").strip(),
                           my_max=_money(row.get("My Max Bid") or "")))
    return out


# --- live state of one auction ----------------------------------------------

def check_item(item_id: str, session=None) -> Optional[dict]:
    """One detail call -> everything the alert decision needs. None on failure
    (fail-soft: one dead call must not kill the sentry run)."""
    session = session or requests
    try:
        r = session.get(_GW_DETAIL.format(item_id), headers=_GW_HEADERS,
                        timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json() or {}
    except Exception as e:
        print(f"[mybids] detail fetch failed for {item_id}: {e}")
        return None
    try:
        end = _dt.datetime.fromisoformat(str(d.get("endTime"))[:19])
        now = _dt.datetime.fromisoformat(str(d.get("serverTime"))[:19])
        left_min = (end - now).total_seconds() / 60.0
    except (TypeError, ValueError):
        end, left_min = None, None
    base = d.get("imageServer", "")
    first_img = next((base + x.replace("\\", "/")
                      for x in (d.get("imageUrlString") or "").split(";")
                      if x.strip()), None)
    return {
        "id": item_id,
        "title": (d.get("title") or "").strip(),
        "url": f"https://shopgoodwill.com/item/{item_id}",
        "image": first_img,
        "current": float(d.get("currentPrice") or 0),
        "min_bid": float(d.get("minimumBid") or 0) or None,
        "increment": float(d.get("bidIncrement") or 1.0),
        "bids": int(d.get("numberOfBids") or 0),
        "handling": float(d.get("handlingPrice") or 0),
        "ends": str(d.get("endTime") or "")[:16],
        "left_min": left_min,
        "expired": bool(d.get("isItemEndTimeExpire")) or (
            left_min is not None and left_min <= 0),
    }


def classify(current: float, my_max: float) -> str:
    if current > my_max:
        return "OUTBID"
    if current == my_max:
        return "AT_CAP"
    return "WINNING"


# --- what the book says about raising ---------------------------------------

def book_advice(title: str, live: dict, inbound_shipping: float = 9.0):
    """(model, BidAdvice) when the title prices in the book, else (None, None).

    This is the raise/walk decision: an outbid alert without the book ceiling
    just invites chasing, which is the expensive direction to be wrong in."""
    m = match(title or "")
    if not m:
        return None, None
    try:
        adv = advise(
            m.model.comp, units=m.units,
            handling=float(live.get("handling") or 0),
            inbound_shipping=inbound_shipping,
            outbound_shipping=m.model.outbound_shipping,
            current_price=live.get("current"),
            min_bid=live.get("min_bid"),
            increment=float(live.get("increment") or 1.0),
            bid_count=int(live.get("bids") or 0),
        )
    except Exception:
        return m.model, None
    return m.model, adv


# --- alert decisions ---------------------------------------------------------

def _load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(path: str, state: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[mybids] couldn't save state: {e}")


def decide(bid: Bid, live: dict, st: dict,
           window_min: float = DEFAULT_WINDOW_MIN,
           book_max: Optional[float] = None) -> tuple[Optional[str], dict]:
    """(alert_kind, new_state) for one item. alert_kind None = stay quiet.

    Kinds: 'closed', 'endgame_losing', 'endgame_winning', 'endgame_watch',
    'status_flip', 'over_ceiling', 'raise_max'."""
    status = "WATCHING" if bid.watching else classify(live["current"], bid.my_max)
    left = live.get("left_min")
    new = dict(st)
    new["status"] = status
    new["last_price"] = live["current"]

    if live.get("expired"):
        if bid.auto or st.get("closed_notified"):
            return None, new
        new["closed_notified"] = True
        return "closed", new

    # Auto-watched board deals: exactly one alert, the 30-minute snipe call.
    # More would drown the auctions he chose himself under board churn.
    if bid.auto:
        if (left is not None and left <= MILESTONES_MIN[-1]
                and not st.get("auto_notified")):
            new["auto_notified"] = True
            return "endgame_watch", new
        return None, new

    # Watch-only: no proxy to defend and no cap to guard, so the ONLY news is
    # the countdown - entry heads-up plus the 60/30 pings, then the close.
    if bid.watching:
        in_window = left is not None and left <= window_min
        if not in_window:
            return None, new
        passed = [m for m in MILESTONES_MIN if left <= m]
        fresh = [m for m in passed if m not in st.get("milestones", [])]
        if fresh:
            new["milestones"] = sorted(set(st.get("milestones", [])) | set(passed))
            new["endgame_notified"] = True
            return "endgame_watch", new
        if not st.get("endgame_notified"):
            new["endgame_notified"] = True
            return "endgame_watch", new
        return None, new

    # WINNING can still be losing money: the live sweep caught a $91 lead on a
    # camera whose book ceiling was ~$28. Losing an auction costs nothing;
    # winning one above the ceiling costs real dollars. Once per item.
    if (status == "WINNING" and book_max is not None
            and live["current"] > book_max and not st.get("over_notified")):
        new["over_notified"] = True
        return "over_ceiling", new

    # The measured leak (2026-07-30 closed CSV): Featherweight lost by $1,
    # SX-70 by $0.12, both Mitutoyos at exact ties - every one had $20+ of
    # ceiling headroom above his max. A max below the ceiling donates the win
    # to whoever bids $1 more; proxy bidding means hardening it costs nothing
    # unless contested. Once per (item, current max) - a re-exported CSV with
    # a raised max re-arms it.
    if (status == "WINNING" and book_max is not None and bid.my_max < book_max
            and st.get("raise_max_at") != bid.my_max):
        new["raise_max_at"] = bid.my_max
        return "raise_max", new

    in_window = left is not None and left <= window_min
    if in_window:
        # Countdown checkpoints fire for winners AND losers: the once-only
        # entry alert meant a winner heard "closes in 1.5h", nothing more,
        # and then the snipe. These re-ping at 60 and 30 regardless of price.
        passed = [m for m in MILESTONES_MIN if left <= m]
        fresh = [m for m in passed if m not in st.get("milestones", [])]
        if fresh:
            new["milestones"] = sorted(set(st.get("milestones", [])) | set(passed))
            new["endgame_notified"] = True
            new["endgame_price"] = live["current"]
            return ("endgame_losing" if status in ("OUTBID", "AT_CAP")
                    else "endgame_winning"), new
        if status in ("OUTBID", "AT_CAP"):
            # Every price move inside the window is news you can still act on.
            if st.get("endgame_price") != live["current"]:
                new["endgame_price"] = live["current"]
                new["endgame_notified"] = True
                return "endgame_losing", new
            return None, new
        if not st.get("endgame_notified"):
            new["endgame_notified"] = True
            return "endgame_winning", new
        return None, new

    # Early phase: only status FLIPS are news; every $1 of a slow walk-up is not.
    if status in ("OUTBID", "AT_CAP") and st.get("status") != status:
        return "status_flip", new
    return None, new


def _fmt_left(left_min: Optional[float]) -> str:
    if left_min is None:
        return "unknown time"
    if left_min >= 90:
        return f"{left_min / 60:.1f}h"
    return f"{left_min:.0f}min"


def to_alert(kind: str, bid: Bid, live: dict, model=None, adv=None) -> dict:
    """One decision -> a Discord embed payload (see notify.build_embed)."""
    status = classify(live["current"], bid.my_max)
    left = _fmt_left(live.get("left_min"))
    nxt = next_valid_bid(live["current"], live["min_bid"],
                         live["increment"], live["bids"])

    bits = []
    if kind == "raise_max":
        ceiling = adv.max_bid if adv is not None else 0
        bits.append(f":shield: **Harden your max NOW.** You're winning at "
                    f"${live['current']:,.2f} but your ${bid.my_max:,.2f} max "
                    f"is below the **${ceiling:,.2f}** book ceiling - a sniper "
                    f"bidding ${bid.my_max + 1:,.0f} takes it. Raise your max "
                    f"TO the ceiling: proxy bidding means you still only pay "
                    f"one increment over the second bidder. You lost the "
                    f"Featherweight by $1 and the SX-70 by 12 cents this way.")
    elif kind == "over_ceiling":
        bits.append(f":money_with_wings: **You're WINNING at "
                    f"${live['current']:,.2f} - and that's already ABOVE the "
                    f"book ceiling.** Every further dollar comes out of the "
                    f"resale profit. Losing this one is free; winning it here "
                    f"is not. Consider letting the snipers have it.")
    elif kind == "closed":
        if bid.watching:
            bits.append(f"Auction ended at **${live['current']:,.2f}** - you "
                        f"were watching and never bid. Gone either way; "
                        f"removing it from the watchlist file stops this.")
        else:
            likely = "likely **WON**" if status == "WINNING" else "**LOST**"
            bits.append(f"Auction ended at **${live['current']:,.2f}** vs your "
                        f"${bid.my_max:,.2f} max - {likely}. Check My Account to confirm.")
    elif kind == "endgame_watch":
        bits.append(f":dart: **Closes in {left}** - you're WATCHING, no bid "
                    f"in. Price **${live['current']:,.2f}** ({live['bids']} "
                    f"bids).")
        if nxt is not None:
            bits.append(f"To take it, bid **${nxt:,.2f}**.")
    elif kind == "endgame_winning":
        bits.append(f":hourglass: **Closes in {left}** and you're WINNING at "
                    f"${live['current']:,.2f} (your max ${bid.my_max:,.2f}). "
                    f"Snipers land in the last minutes - watch this one close.")
    else:
        verb = ("You're **OUTBID**" if status == "OUTBID"
                else "You're **AT YOUR CAP** - one more $1 bid beats you")
        urgency = (f":rotating_light: **Closes in {left}.** "
                   if kind == "endgame_losing" else "")
        bits.append(f"{urgency}{verb}: price is **${live['current']:,.2f}** vs "
                    f"your ${bid.my_max:,.2f} max ({live['bids']} bids).")
        if nxt is not None:
            bits.append(f"To retake the lead bid **${nxt:,.2f}**.")

    # The raise/walk call, from the book. Without this an outbid alert is just
    # an invitation to chase. (raise_max IS the raise call - no tail needed.)
    if kind == "raise_max":
        if model is not None:
            bits.append(f"_{model.label} comps ${model.comp:,.2f}"
                        + (f" - {model.note}_" if model.note else "_"))
    elif kind == "endgame_watch" and model is not None and adv is not None:
        # The snipe number: what the book says the whole thing is worth.
        if nxt is not None and nxt <= adv.max_bid:
            bits.append(f":white_check_mark: {model.label} comps "
                        f"${model.comp:,.2f} -> snipe anywhere up to the "
                        f"**${adv.max_bid:,.2f}** ceiling.")
        else:
            bits.append(f":no_entry: Already past the book ceiling "
                        f"**${adv.max_bid:,.2f}** ({model.label} comps "
                        f"${model.comp:,.2f}). Watching is free; bidding "
                        f"here isn't.")
        if model.note:
            bits.append(f"_{model.note}_")
    elif model is not None and adv is not None:
        if bid.my_max >= adv.max_bid:
            bits.append(f":no_entry: **Book says WALK AWAY.** {model.label} "
                        f"comps ${model.comp:,.2f}; the ceiling that still "
                        f"clears $20 is **${adv.max_bid:,.2f}** and your max is "
                        f"already at/above it. Losing this one costs nothing.")
        elif nxt is not None and nxt <= adv.max_bid:
            bits.append(f":white_check_mark: **Room to raise.** {model.label} "
                        f"comps ${model.comp:,.2f} -> ceiling "
                        f"**${adv.max_bid:,.2f}**. The ${nxt:,.2f} it takes to "
                        f"lead is still under it.")
        else:
            bits.append(f":no_entry: **Retaking the lead busts the ceiling.** "
                        f"Next valid bid ${nxt:,.2f} > book max "
                        f"${adv.max_bid:,.2f} ({model.label} comps "
                        f"${model.comp:,.2f}). Let it go.")
        if model.note:
            bits.append(f"_{model.note}_")
    elif model is None:
        bits.append("_Not in the price book - no comp to judge a raise against._")

    if kind == "endgame_watch":
        verdict = ("buy" if adv is not None and nxt is not None
                   and nxt <= adv.max_bid else "watch")
    else:
        verdict = {"closed": "watch", "endgame_winning": "buy"}.get(
            kind, "pass" if kind == "endgame_losing" else "watch")
    out = {
        "title": (live.get("title") or bid.title)[:240],
        "url": live["url"],
        "buy_url": live["url"],
        "image": live.get("image"),
        "verdict": verdict,
        "bids": live.get("bids"),
        "ends": live.get("ends"),
        "source": "goodwill",
        "reason": "\n".join(bits),
    }
    if adv is not None:
        out["max_bid"] = adv.max_bid
    return out


# --- the run -----------------------------------------------------------------

def run(csv_path: Optional[str] = None, window_min: float = DEFAULT_WINDOW_MIN,
        notifier=notify_rich, session=None, state_file: Optional[str] = None,
        dry: bool = False) -> dict:
    csv_path = csv_path or find_bids_csv()
    watch_path = find_watchlist()
    if not csv_path:
        print("[mybids] no 'Auctions in Progress*.csv' found in Downloads - "
              "export it from shopgoodwill.com/shopgoodwill/inprogress-auctions"
              f" (watch-only items go in Downloads\\{WATCHLIST_NAME})")
    bids, age_h = [], 0.0
    if csv_path:
        age_h = max(0.0, (_now_ts() - os.path.getmtime(csv_path)) / 3600.0)
        bids = load_bids(csv_path)
        print(f"[mybids] {len(bids)} tracked bid(s) from "
              f"{os.path.basename(csv_path)} (exported {age_h:.1f}h ago)")
    if watch_path:
        # An item he then bids on shows up in both; the bid (with its real
        # max) wins, so a stale watchlist line can't mask proxy math.
        have = {b.item_id for b in bids}
        extra = [w for w in load_watchlist(watch_path) if w.item_id not in have]
        bids += extra
        print(f"[mybids] {len(extra)} watch-only item(s) from "
              f"{os.path.basename(watch_path)}")
    if os.environ.get("FLIPSCOUT_AUTOWATCH", "1").lower() not in ("0", "false"):
        have = {b.item_id for b in bids}
        auto = [a for a in load_autowatch(window_min=window_min,
                                          top=int(os.environ.get(
                                              "FLIPSCOUT_AUTOWATCH_TOP", "10")))
                if a.item_id not in have]
        bids += auto
        if auto:
            print(f"[mybids] {len(auto)} board deal(s) auto-watched into the "
                  f"closing window")

    state_file = state_file or os.environ.get(STATE_FILE_ENV, DEFAULT_STATE_FILE)
    state = _load_state(state_file)

    alerts, urgent = [], 0
    for bid in bids:
        live = check_item(bid.item_id, session=session)
        if live is None:
            continue
        # Book advice up front: decide() needs the ceiling to catch the
        # "winning yourself into a loss" case, not just the losing ones.
        model, adv = book_advice(bid.title or live.get("title", ""), live)
        kind, new_st = decide(bid, live, state.get(bid.item_id, {}), window_min,
                              book_max=adv.max_bid if adv is not None else None)
        state[bid.item_id] = new_st
        status = ("WATCHING" if bid.watching
                  else classify(live["current"], bid.my_max))
        print(f"[mybids] {bid.item_id} {status:8s} ${live['current']:>8,.2f} / "
              f"max ${bid.my_max:,.2f} | {_fmt_left(live.get('left_min'))} left"
              + (f" -> ALERT {kind}" if kind else ""))
        if kind:
            alerts.append(to_alert(kind, bid, live, model=model, adv=adv))
            urgent += kind == "endgame_losing"

    sent: list[str] = []
    if alerts:
        header = (f":rotating_light: **BID SENTRY - {urgent} closing auction(s) "
                  f"you're LOSING.** Act now or lose them.\n"
                  if urgent else
                  f"**Bid sentry** - {len(alerts)} update(s) on your "
                  f"ShopGoodwill bids.\n")
        if age_h > 24:
            header += (f"_Bid list is {age_h / 24:.1f} days old - re-export the "
                       f"Auctions in Progress CSV if you've placed new bids._\n")
        if dry:
            print(header)
            for a in alerts:
                print(f"- [{a['verdict']}] {a['title'][:70]}\n  {a['reason']}")
        else:
            sent = notifier(alerts, content=header)
            print(f"[mybids] {'DELIVERED' if sent else 'NOT DELIVERED'} "
                  f"{len(alerts)} alert(s)" + (f" via {', '.join(sent)}" if sent else
                  " - check FLIPSCOUT_ALERT_WEBHOOK"))
    if not dry:
        _save_state(state_file, state)
    return {"tracked": len(bids), "alerts": len(alerts), "sent": sent}


def _now_ts() -> float:
    return _dt.datetime.now().timestamp()


def load_env_file(path: str = ".env") -> None:
    """Fill os.environ from a KEY=VALUE .env without overriding real env.

    The sentry runs from a Scheduled Task, which gets no shell profile - the
    webhook would otherwise silently be unset and every alert would 'deliver'
    to stdout."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


if __name__ == "__main__":
    load_env_file()
    run()

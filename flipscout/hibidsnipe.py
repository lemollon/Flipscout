"""HiBid last-minute bidder ("sniper") for lots LERON has armed.

Same authorisation boundary as the ShopGoodwill sniper in snipe.py, and for
the same reasons - it spends real money, so read that file's header too:

  * It NEVER chooses a lot. You arm each one by hand.
  * It NEVER invents a price. You state a max per lot; it cannot exceed it.
  * It NEVER logs in for you (`hibidsnipe login` opens a visible window).
  * It bids ONCE per lot, ever.

🚨 AND ONE MORE, SPECIFIC TO HIBID: IT NEVER REGISTERS YOU FOR AN AUCTION.
Registering accepts that house's terms and puts your card on file, to be
charged automatically when the auction ends ("The card you register with on
HiBid will be charged after the auction ends" - HiBid's own FAQ). That is a
financial commitment to a counterparty, and it is not a bot's to make. If you
have not registered, this refuses the lot and sends you the link.

WHY SNIPING IS WORTH IT HERE, WHICH IS NOT THE OBVIOUS REASON
-------------------------------------------------------------
HiBid runs proxy bidding (`bidAmountType: MAX_BIDDING`), so as on ShopGoodwill
a snipe wins at the same PRICE an early max would - what it buys is
concealment, not a discount. But the real reason is the closing format:

  * Lots close STAGGERED, roughly 20-30 seconds apart. Measured 2026-08-18:
    three lots of one auction sat at 13s, 33s and 53s remaining.
  * A single estate catalog runs to hundreds of lots.

So a catalog you registered for once drips lots out over several hours. Nobody
sits through that. This does.

🚨 SOFT CLOSE IS REAL AND IT IS FREE TEXT. 13% of sampled auctions describe an
extending close ("bids in the last minute will extend the close by 2 minutes")
and they describe it in `biddingNotice`, a prose field, with no structured flag
anywhere. That is survivable rather than fatal: firing at T-180s is outside a
1-2 minute extension window, and if the clock does extend, the max we placed is
a standing proxy bid that defends itself. It degrades to ordinary proxy
bidding, which is what we would have done anyway. It does NOT mean we bid
again - one bid per lot, always.

TIMING
------
`timeLeftSeconds` is a COUNTDOWN read from the same response as everything
else, so no clock and no timezone is involved anywhere - the rule mybids.py
follows and the bug class that blinded FLASHPOINT.

🚨 Do NOT be tempted by `bidCloseDateTime`. It is the AUCTION's close, not the
lot's, it carries no timezone, and with a staggered close the lot you care
about may close hours after it. The three lots above all reported the same
`bidCloseDateTime` while sitting 20 seconds apart.

WHAT THE MAX MEANS
------------------
🚨 The armed number is a HAMMER bid, because that is what the bid box takes.
The buyer's premium (10-20%, see auctionfees.py) is charged ON TOP at
checkout. Ceilings that come off a Flipscout card are already net of it; a
number you type yourself is not, so `arm` shows you the all-in cost before it
saves anything.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
from typing import Optional

import requests

from .auctionfees import min_increment, parse_premium
from .bidding import advise
from .pricebook import match

REPO = pathlib.Path(__file__).resolve().parent.parent
PROFILE_DIR = REPO / ".hibidprofile"
ARMED_PATH = REPO / "hibid_armed.json"
# Shared with the ShopGoodwill sniper on purpose: one file stops every bot.
KILL_SWITCH = REPO / "SNIPE_DISABLED"

_LOT_URL = "https://hibid.com/lot/{}"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Fire inside this window before close. Wider than ShopGoodwill's 180s because
# HiBid lots close in a staggered stream and one poll has to catch several.
SNIPE_AT_S = int(os.environ.get("FLIPSCOUT_HIBID_SNIPE_SECONDS", "180"))
ABORT_UNDER_S = int(os.environ.get("FLIPSCOUT_HIBID_ABORT_UNDER", "25"))


def lot_id(s: str) -> str:
    m = re.search(r"/lot/(\d{6,})", str(s)) or re.search(r"(\d{6,})", str(s))
    if not m:
        raise ValueError(f"no HiBid lot id in {s!r}")
    return m.group(1)


def load_armed() -> dict:
    if not ARMED_PATH.exists():
        return {}
    try:
        return json.loads(ARMED_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_armed(d: dict) -> None:
    ARMED_PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _blob(text: str, key: str):
    """Pull one JSON value out of the page's embedded state."""
    m = re.search(rf'"{key}":\s*(\{{.*?\}}|\[.*?\]|"[^"]*"|true|false|null|-?[\d.]+)',
                  text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def detail(lid: str) -> dict:
    """Everything about one lot, from ONE response.

    Parsed out of the lot page rather than GraphQL: HiBid's schema blocks
    introspection and exposes no single-lot query (`lot` takes an ID but
    returns an opaque LotAccessType), while the page embeds the whole state
    object the site's own UI runs on.
    """
    r = requests.get(_LOT_URL.format(lid), headers={"User-Agent": _UA}, timeout=30)
    r.raise_for_status()
    t = r.text
    st = _blob(t, "lotState") or {}
    if not st:
        # Closed and archived auctions serve a stripped shell with no state.
        return {"lot_id": lid, "gone": True}
    return {
        "lot_id": lid,
        "gone": False,
        "title": (_blob(t, "lead") or "").strip(),
        "high_bid": float(st.get("highBid") or 0),
        "min_bid": float(st.get("minBid") or 0) or None,
        "bids": int(st.get("bidCount") or 0),
        "closed": bool(st.get("isClosed")),
        "left": st.get("timeLeftSeconds"),
        "extended": bool(st.get("biddingExtended")),
        # 🚨 The gate. False means we cannot bid at all, however well armed.
        "registered": bool(st.get("isRegistered")),
        "increments": _blob(t, "bidIncrements"),
        "premium": parse_premium(_blob(t, "buyerPremium")),
        "notice": (_blob(t, "biddingNotice") or "")[:400],
    }


def seconds_left(d: dict) -> Optional[float]:
    v = d.get("left")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def book_ceiling(title: str, premium: float = 0.0,
                 inbound: float = 9.0) -> Optional[float]:
    """What the price book says this lot is worth as a HAMMER bid."""
    m = match(title)
    if not m:
        return None
    a = advise(m.model.comp, units=m.units, inbound_shipping=inbound,
               outbound_shipping=m.model.outbound_shipping,
               target_profit=20.0, current_price=1, buyer_premium_rate=premium)
    return a.max_bid if a.max_bid > 0 else None


def arm(url_or_id: str, max_bid: float, override: bool = False) -> int:
    lid = lot_id(url_or_id)
    d = detail(lid)
    if d.get("gone"):
        print(f"{lid}: lot is closed or archived - nothing to arm")
        return 1
    cap = float(max_bid)
    prem = float(d.get("premium") or 0)

    book = book_ceiling(d["title"], premium=prem)
    if book is not None and cap > book and not override:
        print(f"{lid}: ${cap:.2f} is above the book's ${book:.2f} hammer ceiling.\n"
              f"       Re-run with --override if you mean it.")
        return 1

    # 🚨 Say the all-in cost. The bid box takes a hammer number but the invoice
    # does not, and a 20% premium is bigger than the target profit on most of
    # these lots.
    landed = cap * (1 + prem)
    print(f"ARMED {lid} - {d['title'][:60]}")
    print(f"  max hammer bid   ${cap:,.2f}")
    print(f"  buyer's premium  {prem * 100:.4g}%  -> you pay ${landed:,.2f} all-in")
    print(f"  current bid      ${d['high_bid']:,.2f} ({d['bids']} bids)")
    if not d["registered"]:
        print(f"  :warning: NOT REGISTERED for this auction - this will NOT bid "
              f"until you register at {_LOT_URL.format(lid)}")

    armed = load_armed()
    armed[lid] = {"lot_id": lid, "title": d["title"], "url": _LOT_URL.format(lid),
                  "max_bid": round(cap, 2), "premium": prem,
                  "landed_at_max": round(landed, 2), "status": "ARMED"}
    save_armed(armed)
    return 0


def disarm(url_or_id: str) -> int:
    lid = lot_id(url_or_id)
    armed = load_armed()
    if lid not in armed:
        print(f"{lid}: not armed")
        return 1
    armed.pop(lid)
    save_armed(armed)
    print(f"disarmed {lid}")
    return 0


def show() -> int:
    armed = load_armed()
    if not armed:
        print("nothing armed")
        return 0
    for lid, a in armed.items():
        print(f"  {a['status']:16s} ${a['max_bid']:>8.2f} hammer "
              f"(${a.get('landed_at_max', a['max_bid']):>8.2f} all-in)  "
              f"{a['title'][:46]}")
        if a.get("result"):
            print(f"                   {a['result']}")
    return 0


def place_bid(lid: str, amount: float, dry_run: bool) -> tuple:
    """Drive the real bid form. Returns (ok, message).

    UI-driven for the same reason as the ShopGoodwill sniper: it does exactly
    what a person does, and a dry run can stop one click short of committing
    money, which a raw POST cannot.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            pg.goto(_LOT_URL.format(lid), timeout=40000, wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            body = (pg.inner_text("body") or "")[:5000]
            if re.search(r"\bsign in\b|\blog in\b", body, re.I) and \
               not re.search(r"my hibid|sign out|log out", body, re.I):
                return False, "LOGGED OUT - run `hibidsnipe login`"
            if re.search(r"register to bid|you must register|not registered", body, re.I):
                return False, "NOT REGISTERED for this auction - register first"

            box = None
            for sel in ("input[name='bidAmount']", "input#bidAmount",
                        "input[formcontrolname='bidAmount']",
                        "input[placeholder*='bid' i]", "input[type='number']"):
                box = pg.query_selector(sel)
                if box:
                    break
            if not box:
                return False, "bid box not found (page changed?)"
            box.fill(f"{amount:.2f}")

            btn = None
            for b in pg.query_selector_all("button, input[type='submit']"):
                label = (b.inner_text() or b.get_attribute("value") or "")
                if re.search(r"place bid|submit bid|bid now|place my bid", label, re.I):
                    btn = b
                    break
            if not btn:
                return False, "'Place Bid' button not found"
            if dry_run:
                return True, f"DRY RUN - form filled with ${amount:.2f}, NOT clicked"

            btn.click()
            pg.wait_for_timeout(2500)
            # HiBid usually raises a confirm dialog on top of the form.
            for b in pg.query_selector_all("button"):
                if re.search(r"^\s*(confirm|yes|ok|place bid)\s*$",
                             (b.inner_text() or ""), re.I):
                    b.click()
                    break
            pg.wait_for_timeout(3000)
            after = (pg.inner_text("body") or "")[:5000]
            if re.search(r"you are the high bidder|winning|bid accepted|"
                         r"bid placed|your bid", after, re.I):
                return True, f"BID PLACED at ${amount:.2f}"
            if re.search(r"outbid|higher bid|bid too low|increase", after, re.I):
                return False, "bid rejected - already above your max"
            return True, f"bid submitted at ${amount:.2f} (no confirmation text seen)"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        finally:
            ctx.close()


def run(dry_run: bool = False) -> int:
    from .notify import notify
    if KILL_SWITCH.exists():
        print(f"kill switch present ({KILL_SWITCH.name}) - not bidding")
        return 0
    armed = load_armed()
    if not armed:
        return 0
    changed = False
    for lid, a in list(armed.items()):
        if a.get("status") != "ARMED":
            continue                      # ONE bid per lot, ever
        try:
            d = detail(lid)
        except Exception as e:
            print(f"{lid}: detail failed {type(e).__name__}")
            continue
        if d.get("gone") or d.get("closed"):
            a["status"] = "ENDED_UNBID"
            changed = True
            continue
        left = seconds_left(d)
        if left is None:
            print(f"{lid}: no countdown in the response - skipping")
            continue
        if left <= 0:
            a["status"] = "ENDED_UNBID"
            changed = True
            continue

        # 🚨 THE REGISTRATION GATE. Check it before the clock so the warning
        # arrives while there is still time to act on it, not at T-180s.
        if not d["registered"]:
            if a.get("warned_unregistered"):
                continue
            a["warned_unregistered"] = True
            changed = True
            msg = (f":lock: **Cannot snipe - you are not registered** for this "
                   f"auction.\n{a['title'][:60]}\nRegister on the lot page and "
                   f"it will bid as armed. Closes in {left / 60:.0f} min.\n{a['url']}")
            print(msg)
            if not dry_run:
                notify(msg, subject="Flipscout HiBid - register to bid")
            continue

        if left > SNIPE_AT_S:
            print(f"{lid}: {left / 60:.1f} min left - waiting")
            continue
        if left < ABORT_UNDER_S:
            a["status"] = "MISSED"
            changed = True
            msg = (f":warning: **Snipe MISSED** {a['title'][:60]} - only "
                   f"{left:.0f}s left when checked.\n{a['url']}")
            print(msg)
            if not dry_run:
                notify(msg, subject="Flipscout HiBid snipe missed")
            continue

        cap = float(a["max_bid"])
        need = d.get("min_bid") or (d["high_bid"] +
                                    min_increment(d["high_bid"], d.get("increments")))
        if need > cap:
            a["status"] = "PASSED_TOO_HIGH"
            changed = True
            msg = (f":no_entry: **Snipe passed** {a['title'][:60]} - the next "
                   f"required bid is ${need:.2f}, above your ${cap:.2f} max.\n{a['url']}")
            print(msg)
            if not dry_run:
                notify(msg, subject="Flipscout HiBid snipe passed")
            continue

        # Bid the MAX, not one increment over the current price. HiBid is a
        # proxy system, so the max is a ceiling and you pay one step over the
        # runner-up - see the long note in snipe.py, which cost real losses to
        # learn ("lost the Featherweight by $1").
        before = d["high_bid"]
        ok, detail_msg = place_bid(lid, cap, dry_run=dry_run)
        a["status"] = ("DRY_RUN" if dry_run else ("BID" if ok else "FAILED"))
        a["result"] = detail_msg
        a["price_before"] = before
        changed = True

        after = None
        if ok and not dry_run:
            try:
                time.sleep(2)
                after = detail(lid).get("high_bid")
            except Exception:
                pass
            if after is not None:
                a["price_after"] = after

        prem = float(a.get("premium") or d.get("premium") or 0)
        icon = ":dart:" if ok else ":x:"
        lines = [f"{icon} **HiBid snipe {'(dry run) ' if dry_run else ''}{a['status']}** "
                 f"on {a['title'][:60]}",
                 f"bid when it fired: **${before:.2f}**  (needed ${need:.2f})",
                 f"your armed max: **${cap:.2f}** hammer"]
        if after is not None:
            lines.append(
                f"price now: **${after:.2f}**"
                + (f"  - winning at ${after:.2f}, ${cap - after:.2f} under your max; "
                   f"**${after * (1 + prem):.2f} all-in** with the "
                   f"{prem * 100:.4g}% premium"
                   if after <= cap else "  - ABOVE your max, you were outbid"))
        if d.get("extended"):
            lines.append("_This auction soft-closed and extended. Your max is a "
                         "standing proxy bid and still stands - no second bid._")
        lines += [detail_msg, a["url"]]
        msg = "\n".join(lines)
        print(msg)
        if not dry_run:
            notify(msg, subject="Flipscout HiBid snipe")
    if changed:
        save_armed(armed)
    return 0


def login(timeout_s: int = 300) -> int:
    """Open the dedicated profile VISIBLY so Leron signs in himself.

    🚨 Claude must NEVER type these credentials.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.goto("https://hibid.com/login", timeout=60000)
        print("Sign in to HiBid in the window that just opened.")
        print("This window is yours - Claude does not type anything into it.")
        print(f"Waiting up to {timeout_s}s, then saving the session...")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                body = (pg.inner_text("body") or "")[:4000]
                if re.search(r"my hibid|sign out|log out", body, re.I):
                    print("Signed in - session saved.")
                    ctx.close()
                    return 0
            except Exception:
                pass
            time.sleep(3)
        print("Timed out. Session may still be saved; re-run to check.")
        ctx.close()
        return 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .mybids import load_env_file
    load_env_file()
    cmd = argv[0] if argv else "show"
    dry = "--dry-run" in argv
    if cmd == "login":
        return login()
    if cmd == "arm":
        if len(argv) < 3:
            print("usage: hibidsnipe arm <lot-url-or-id> <max-hammer-bid> [--override]")
            return 2
        return arm(argv[1], float(argv[2]), override="--override" in argv)
    if cmd == "disarm":
        return disarm(argv[1]) if len(argv) > 1 else 2
    if cmd == "run":
        return run(dry_run=dry)
    return show()


if __name__ == "__main__":
    raise SystemExit(main())

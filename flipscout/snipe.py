"""ShopGoodwill last-minute bidder ("sniper") for items LERON has armed.

WHAT THIS WILL AND WILL NOT DO
------------------------------
It places real bids with real money, so the authorisation boundary matters more
than the code:

  * It NEVER chooses an item. You arm each one by hand.
  * It NEVER invents a price. You state a max per item; it cannot exceed it.
  * It NEVER logs in for you. You sign in once, yourself, to a dedicated
    browser profile (`snipe login`). Claude must not type those credentials.
  * It bids ONCE per item, ever. It will not get into a bidding war.

Arming an item IS the authorisation: a specific item, a specific dollar cap,
stated in advance. That is the same commitment shape as ShopGoodwill's own
proxy bidding, just timed differently.

WHY SNIPING WORKS HERE (verified, not assumed)
----------------------------------------------
ShopGoodwill has a HARD CLOSE. Watched live on 2026-08-16: item 273876344 took
bids through its final 21 minutes and `endTime` never moved off 17:05. No
auto-extend, so a late bid cannot be answered.

🚨 BUT BE HONEST ABOUT THE EDGE. ShopGoodwill uses PROXY bidding - you enter a
max and the site bids the increment for you. So sniping does NOT get you a
better price than simply arming your max early; the winning price is identical.
What it buys is CONCEALMENT: nobody sees your interest and re-evaluates the
item, and no one gets days to talk themselves into outbidding you.

What it COSTS is execution risk. A proxy bid placed early always stands. A
snipe can miss - expired session, slow page, a laggy minute - and then you get
nothing. That trade is real and it is Leron's to make per item.

🚨 UNVERIFIED: I could not retrieve ShopGoodwill's Terms of Use (the SPA does
not serve them at any URL I tried). Many auction sites prohibit automated
bidding. This is Leron's account and his risk; he should read the terms before
relying on this.

TIMING
------
`serverTime` and `endTime` come from the SAME detail response and are both
Pacific, so the local clock and timezones never enter into it - the same rule
mybids.py follows, and the exact class of bug that blinded FLASHPOINT.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
from datetime import datetime
from typing import Optional

import requests

from .bidding import advise
from .pricebook import match

REPO = pathlib.Path(__file__).resolve().parent.parent
PROFILE_DIR = REPO / ".sgwprofile"
ARMED_PATH = REPO / "snipe_armed.json"
KILL_SWITCH = REPO / "SNIPE_DISABLED"

_DETAIL = "https://buyerapi.shopgoodwill.com/api/ItemDetail/GetItemDetailModelByItemId/{}"
_H = {"Content-Type": "application/json", "Origin": "https://shopgoodwill.com",
      "Referer": "https://shopgoodwill.com/", "User-Agent": "Mozilla/5.0"}

# Fire inside this window before close. 180s default: late enough that nobody
# can comfortably respond, early enough to absorb a slow page load.
SNIPE_AT_S = int(os.environ.get("FLIPSCOUT_SNIPE_SECONDS", "180"))
# Refuse to start a bid with less than this left - a half-placed bid at T-5s is
# worse than no bid, because you cannot tell whether it landed.
ABORT_UNDER_S = int(os.environ.get("FLIPSCOUT_SNIPE_ABORT_UNDER", "25"))


def item_id(s: str) -> str:
    m = re.search(r"(\d{6,})", str(s))
    if not m:
        raise ValueError(f"no item id in {s!r}")
    return m.group(1)


def load_armed() -> dict:
    try:
        return json.loads(ARMED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_armed(d: dict) -> None:
    ARMED_PATH.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")


def detail(iid: str) -> dict:
    r = requests.get(_DETAIL.format(iid), headers=_H, timeout=30)
    r.raise_for_status()
    return r.json() or {}


def seconds_left(d: dict) -> Optional[float]:
    """From serverTime vs endTime in the SAME response. Never the local clock."""
    try:
        srv = datetime.fromisoformat(str(d["serverTime"])[:26])
        end = datetime.fromisoformat(str(d["endTime"])[:26])
    except Exception:
        return None
    return (end - srv).total_seconds()


def book_ceiling(title: str, inbound: float = 0.0) -> Optional[float]:
    """What the price book would pay. Advisory - Leron's max still governs."""
    m = match(title or "")
    if not m:
        return None
    a = advise(m.model.comp, outbound_shipping=m.model.outbound_shipping,
               target_profit=20.0, inbound_shipping=inbound, current_price=1)
    return a.max_bid


# ----------------------------------------------------------------- commands --

def arm(url_or_id: str, max_bid: float, override: bool = False) -> int:
    iid = item_id(url_or_id)
    d = detail(iid)
    title = (d.get("title") or "").strip()
    if d.get("isItemEndTimeExpire"):
        print(f"that auction has already ended: {title[:60]}")
        return 1
    ceiling = book_ceiling(title, inbound=float(d.get("handlingPrice") or 0))
    print(f"item    : {iid}  {title[:64]}")
    print(f"current : ${float(d.get('currentPrice') or 0):.2f}   ends {d.get('endTime')}")
    print(f"your max: ${max_bid:.2f}")
    if ceiling is None:
        print("book    : does not price this - your max is the only limit")
    else:
        print(f"book    : ceiling ${ceiling:.2f}")
        if max_bid > ceiling and not override:
            # 🚨 Not a hard block - it is HIS money and the book is only a
            # model - but it must be a deliberate act, not a typo.
            print(f"\nREFUSED: ${max_bid:.2f} is above the book ceiling ${ceiling:.2f}.")
            print("If you mean it, re-run with --override.")
            return 2
    armed = load_armed()
    armed[iid] = {"id": iid, "title": title, "max_bid": round(float(max_bid), 2),
                  "end_time": d.get("endTime"), "armed_at": d.get("serverTime"),
                  "override": bool(override), "status": "ARMED",
                  "url": f"https://shopgoodwill.com/item/{iid}"}
    save_armed(armed)
    print(f"\nARMED. It will bid ${max_bid:.2f} at T-{SNIPE_AT_S}s and never more.")
    return 0


def disarm(url_or_id: str) -> int:
    iid = item_id(url_or_id)
    armed = load_armed()
    if iid not in armed:
        print(f"{iid} was not armed")
        return 1
    armed.pop(iid)
    save_armed(armed)
    print(f"disarmed {iid}")
    return 0


def show() -> int:
    armed = load_armed()
    if not armed:
        print("nothing armed")
        return 0
    print(f"{'item':>11s} {'max':>9s} {'status':>10s}  ends / title")
    for iid, a in sorted(armed.items(), key=lambda kv: kv[1].get("end_time") or ""):
        print(f"{iid:>11s} {a['max_bid']:9.2f} {a.get('status',''):>10s}  "
              f"{(a.get('end_time') or '')[:16]}  {a.get('title','')[:44]}")
    return 0


def place_bid(iid: str, amount: float, dry_run: bool) -> tuple:
    """Drive the real bid form. Returns (ok, message).

    UI-driven on purpose rather than reverse-engineering the bid API: what it
    does is exactly what a person does, and a dry run can stop one click short
    of committing money, which a raw POST cannot.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            pg.goto(f"https://shopgoodwill.com/item/{iid}", timeout=40000,
                    wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            body = (pg.inner_text("body") or "")[:4000]
            if re.search(r"\bsign in\b|\blog in\b", body, re.I) and \
               not re.search(r"my shopgoodwill", body, re.I):
                return False, "LOGGED OUT - run `snipe login`"
            box = pg.query_selector("#currentBid")
            if not box:
                return False, "bid box #currentBid not found (page changed?)"
            box.fill(f"{amount:.2f}")
            btn = None
            for b in pg.query_selector_all("button"):
                if re.search(r"place my bid", (b.inner_text() or ""), re.I):
                    btn = b
                    break
            if not btn:
                return False, "'Place My Bid' button not found"
            if dry_run:
                return True, (f"DRY RUN - form filled with ${amount:.2f}, "
                              f"button found, NOT clicked")
            btn.click()
            pg.wait_for_timeout(3500)
            after = (pg.inner_text("body") or "")[:4000]
            if re.search(r"you are the high bidder|your bid has been placed|"
                         r"bid confirmation", after, re.I):
                return True, f"BID PLACED at ${amount:.2f}"
            if re.search(r"outbid|higher bid|increase your bid", after, re.I):
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
    for iid, a in list(armed.items()):
        if a.get("status") != "ARMED":
            continue                      # ONE bid per item, ever
        try:
            d = detail(iid)
        except Exception as e:
            print(f"{iid}: detail failed {type(e).__name__}")
            continue
        left = seconds_left(d)
        if left is None:
            continue
        if d.get("isItemEndTimeExpire") or left <= 0:
            a["status"] = "ENDED_UNBID"
            changed = True
            continue
        if left > SNIPE_AT_S:
            print(f"{iid}: {left/60:.1f} min left - waiting")
            continue
        if left < ABORT_UNDER_S:
            a["status"] = "MISSED"
            changed = True
            msg = (f":warning: **Snipe MISSED** {a['title'][:60]} - only "
                   f"{left:.0f}s left when checked, too late to bid safely.\n{a['url']}")
            print(msg)
            if not dry_run:
                notify(msg, subject="Flipscout snipe missed")
            continue

        need = float(d.get("minimumBid") or 0)
        cap = float(a["max_bid"])
        if need > cap:
            a["status"] = "PASSED_TOO_HIGH"
            changed = True
            msg = (f":no_entry: **Snipe passed** {a['title'][:60]} - the next "
                   f"required bid is ${need:.2f}, above your ${cap:.2f} max.\n{a['url']}")
            print(msg)
            if not dry_run:
                notify(msg, subject="Flipscout snipe passed")
            continue

        # 🚨 BID THE MAX. This looks like "paying the max" and it is not.
        #
        # ShopGoodwill runs PROXY bidding: you submit a ceiling and the site
        # bids the $1 increment on your behalf, so you pay ONE INCREMENT OVER
        # THE RUNNER-UP, not your number. Submitting max $50 against a rival
        # whose hidden max is $30 wins the item at $31.
        #
        # Bidding "just over the current price" instead is strictly worse, and
        # it is how auctions are actually lost here. The displayed price is not
        # the rival's limit - it is the second-highest max plus an increment,
        # with any standing proxy hidden behind it. Bid current+$1 and a rival
        # proxy answers instantly, in the last seconds, with no time to react.
        # mybids.py records the receipts: "You lost the Featherweight by $1 and
        # the SX-70 by 12 cents this way."
        #
        # Same price when you would have won either way; you win the cases the
        # low bid loses. The only thing the max costs you is the scenario where
        # a rival sits just under it - which is the price you already said yes
        # to when you armed it.
        before = float(d.get("currentPrice") or 0)
        ok, detail_msg = place_bid(iid, cap, dry_run=dry_run)
        a["status"] = ("DRY_RUN" if dry_run else ("BID" if ok else "FAILED"))
        a["result"] = detail_msg
        a["price_before"] = before
        changed = True

        # Re-read so the alert reports what it actually COST, not what was
        # authorised - the whole point of proxy bidding is that those differ.
        after = None
        if ok and not dry_run:
            try:
                time.sleep(2)
                after = float(detail(iid).get("currentPrice") or 0)
                a["price_after"] = after
            except Exception:
                pass

        icon = ":dart:" if ok else ":x:"
        lines = [f"{icon} **Snipe {'(dry run) ' if dry_run else ''}{a['status']}** "
                 f"on {a['title'][:60]}",
                 f"current bid when it fired: **${before:.2f}**  (needed ${need:.2f})",
                 f"your armed max: **${cap:.2f}**"]
        if after is not None:
            lines.append(f"price now: **${after:.2f}**"
                         + (f"  - you are winning at ${after:.2f}, "
                            f"${cap - after:.2f} under your max"
                            if after <= cap else "  - ABOVE your max, you were outbid"))
        lines += [detail_msg, a["url"]]
        msg = "\n".join(lines)
        print(msg)
        if not dry_run:
            notify(msg, subject="Flipscout snipe")
    if changed:
        save_armed(armed)
    return 0


def login(timeout_s: int = 300) -> int:
    """Open the dedicated profile VISIBLY so Leron signs in himself.

    🚨 Claude must NEVER type these credentials.
    """
    from playwright.sync_api import sync_playwright
    print("Opening a Chrome window on the Flipscout ShopGoodwill profile.")
    print("Sign in there. It closes itself once you are in.")
    print(f"Profile: {PROFILE_DIR}\n")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.goto("https://shopgoodwill.com/signin", timeout=60000)
        ok, waited = False, 0
        while waited < timeout_s:
            if pg.is_closed():
                break
            try:
                if re.search(r"my shopgoodwill", pg.inner_text("body") or "", re.I) \
                        and not re.search(r"\bsign in\b", pg.inner_text("body") or "", re.I):
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(3)
            waited += 3
        try:
            pg.wait_for_timeout(1500)
        except Exception:
            pass
        ctx.close()
    print("\nSigned in - session saved." if ok else
          "\nDid not detect a signed-in session.")
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .mybids import load_env_file
    load_env_file()
    cmd = argv[0] if argv else "run"
    rest = [a for a in argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in argv
    if cmd == "login":
        return login()
    if cmd == "arm":
        if len(rest) < 2:
            print("usage: snipe arm <item-url-or-id> <max_bid> [--override]")
            return 1
        return arm(rest[0], float(rest[1]), override="--override" in argv)
    if cmd == "disarm":
        return disarm(rest[0]) if rest else 1
    if cmd in ("list", "show"):
        return show()
    if cmd == "run":
        return run(dry_run=dry)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

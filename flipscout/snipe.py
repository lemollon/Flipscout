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
# What `snipe verify` learned about what happens after "Place My Bid".
BIDFORM_PATH = REPO / "sgw_bidform.json"

# Prefix on the place_bid message when a rival proxy outbid us on the spot.
# 🚨 This is a NORMAL outcome, not a fault - somebody simply valued the thing
# more than we did. It has to be distinguishable from a real failure (a moved
# selector, a dead session), because one needs no action and the other needs
# fixing, and burying both under "FAILED" hides the broken one.
OUTBID = "OUTBID"

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


class ArmedFileCorrupt(Exception):
    """The armed-items file could not be read.

    🚨 THIS MUST NEVER BE SWALLOWED. load_armed() used to answer an unreadable
    file with {} - so a half-written file made every armed item vanish, `show`
    printed "nothing armed", and the sniper sat silent through the close. That
    is indistinguishable from a quiet day, which is the worst possible failure
    for something whose whole job is to act at a deadline.

    🚨 A MISSING file is NOT corrupt - that is just the normal empty state, and
    conflating the two would cry wolf on every fresh install.
    """


def load_armed() -> dict:
    if not ARMED_PATH.exists():
        return {}
    try:
        return json.loads(ARMED_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        kept = ARMED_PATH.with_suffix(".corrupt")
        try:
            kept.write_bytes(ARMED_PATH.read_bytes())
        except Exception:
            kept = None
        raise ArmedFileCorrupt(
            f"{ARMED_PATH.name} is unreadable ({type(e).__name__})"
            + (f"; a copy is at {kept.name}" if kept else "")) from e


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


def book_ceiling(title: str, inbound: float = 0.0,
                 target_profit: float = 20.0) -> Optional[float]:
    """What the price book would pay. Advisory - Leron's max still governs.

    Pass target_profit=0 for the BREAK-EVEN price, which is where winning stops
    being worth anything - the clamp a stretch is measured against.
    """
    m = match(title or "")
    if not m:
        return None
    a = advise(m.model.comp, outbound_shipping=m.model.outbound_shipping,
               target_profit=target_profit, inbound_shipping=inbound,
               current_price=1)
    return a.max_bid


def stretch_to(title: str, base: float, extra: float,
               inbound: float = 0.0) -> tuple:
    """Raise a ceiling by `extra`, but never past break-even.

    🚨 THE STRETCH IS PROFIT YOU ARE SPENDING, not headroom you found. The base
    is priced to clear TARGET_PROFIT; each dollar over clears a dollar less.

    On a proxy system it costs nothing unless a rival sits between the old
    ceiling and the new one - exactly the narrow loss you wanted to win.

    Returns (ceiling, clears, breakeven, clamped).
    """
    want = round(base + max(0.0, float(extra)), 2)
    be = book_ceiling(title, inbound=inbound, target_profit=0.0)
    clamped = False
    if be is not None and want > be:
        want, clamped = round(be, 2), True
    clears = round(be - want, 2) if be is not None else None
    return want, clears, (round(be, 2) if be is not None else None), clamped


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
    try:
        armed = load_armed()
    except ArmedFileCorrupt as e:
        print(f"CANNOT READ THE ARMED LIST: {e}")
        return 1
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
            # Ground truth for "did it land", read BEFORE we touch anything.
            try:
                bids_before = bid_count(detail(iid))
            except Exception:
                bids_before = None
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
            pg.wait_for_timeout(2500)

            # ShopGoodwill puts a CONFIRMATION step behind "Place My Bid".
            # Clicking once only opens it; the bid is not placed until the
            # second control is pressed. Missing this is why a snipe reported
            # success on 2026-08-18 while the lot still read "Number of
            # Bids: 0".
            # 🚨 USE WHAT VERIFY OBSERVED, NOT A GUESS LIST.
            #
            # Leron ran `snipe verify` on item 274020144 on 2026-08-20 and the
            # dialog is real: heading "Confirm Bid", body "Click Place Bid to
            # confirm your bid of $7.99", buttons [Close] [Place Bid]. So the
            # confirm control is "Place Bid" while the TRIGGER is "Place My
            # Bid" - and both matched the old pattern.
            #
            # That is the whole failure. query_selector_all returns DOM order,
            # the trigger comes first, so the "confirmation" click landed back
            # on the button already pressed. The real confirm was never touched
            # and the bid never went in - three lots, none landed, one of them
            # a camcorder that closed at $10.00 against a $72.70 max.
            #
            # Skipping `btn` fixes the ordering; preferring the RECORDED label
            # means a future wording change is a one-line re-verify rather than
            # another silent miss.
            want = (bidform().get("confirm_text") or "").strip()
            pattern = (re.escape(want) if want else
                       r"confirm|confirm bid|yes|ok|place bid|submit")
            for b in pg.query_selector_all("button, input[type='submit']"):
                if b == btn:
                    continue                # never re-click the trigger
                label = (b.inner_text() or b.get_attribute("value") or "").strip()
                if re.fullmatch(pattern, label, re.I):
                    try:
                        b.click()
                    except Exception:
                        pass
                    break
            pg.wait_for_timeout(3000)

            after = (pg.inner_text("body") or "")[:4000]
            if re.search(r"outbid|higher bid|increase your bid", after, re.I):
                # 🚨 Say WHOSE max. "already above your max" reads as though
                # our own bid was the problem; what actually happened is a
                # rival proxy is standing higher than our ceiling.
                return False, (f"{OUTBID} - a standing bid is above your "
                               f"${amount:,.2f} max, so this cannot be won "
                               f"at your number")

            # 🚨 PROVE IT AGAINST THE SITE, NOT THE PAGE TEXT.
            #
            # This used to end with `return True, "(no confirmation text
            # seen)"` - claiming success with no evidence whatsoever. On
            # 2026-08-18 that reported "you are winning" on a lot that read
            # "Number of Bids: 0", and Leron only found out because he asked.
            #
            # A money action must FAIL CLOSED: unknown is a failure, because a
            # false win is worse than a missed lot. You can re-bid a lot you
            # know you lost; you cannot re-bid one you were told you had won.
            landed = None
            try:
                fresh = detail(iid)
                after_bids = bid_count(fresh)
                if after_bids is not None and bids_before is not None:
                    landed = int(after_bids) > int(bids_before)
                elif after_bids is not None:
                    landed = int(after_bids) > 0
            except Exception:
                landed = None

            if landed:
                return True, f"BID PLACED at ${amount:.2f}"
            if re.search(r"you are the high bidder|your bid has been placed|"
                         r"bid confirmation", after, re.I):
                return True, f"BID PLACED at ${amount:.2f} (confirmed on the page)"
            if landed is False:
                return False, (f"NOT PLACED - the bid count did not move after "
                               f"submitting ${amount:.2f}. The lot is untouched.")
            return False, (f"UNVERIFIED - submitted ${amount:.2f} but could not "
                           f"confirm it landed. Check the lot yourself.")
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        finally:
            ctx.close()


def run(dry_run: bool = False) -> int:
    from .notify import notify
    if KILL_SWITCH.exists():
        print(f"kill switch present ({KILL_SWITCH.name}) - not bidding")
        return 0
    try:
        armed = load_armed()
    except ArmedFileCorrupt as e:
        # 🚨 Loud on purpose. An empty armed list and an unreadable one look
        # identical from the outside - silence - and only one of them means
        # snipes are being missed right now.
        msg = (f":rotating_light: **ShopGoodwill snipe list is UNREADABLE** - {e}\n"
               f"Nothing can be sniped until it is fixed. Re-arm the items you "
               f"still want.")
        print(msg)
        if not dry_run:
            try:
                notify(msg, subject="Flipscout snipe list corrupt")
            except Exception:
                pass
        return 1
    if not armed:
        return 0
    # 🚨 THE GATE HIBID HAS HAD ALL ALONG, AND THIS FILE DID NOT.
    #
    # `bidform()` was written and then never called, so ShopGoodwill has been
    # bidding BLIND: place_bid clicks "Place My Bid" and then guesses at the
    # confirmation control from a fixed list of labels. Measured 2026-08-18/19,
    # three armed lots all failed the same way - "submitted but could not
    # confirm it landed", and every one closed with Bids: 0. A $72.70 max lost
    # a camcorder that ended at $10.00 with nobody bidding at all.
    #
    # Refusing is strictly better than failing silently: a refusal names the
    # one command that fixes it, where a blind click just loses the lot and
    # reports a fault after the fact.
    if not dry_run and not bidform():
        msg = (":lock: **ShopGoodwill sniping is OFF until the bid form is "
               "proven.** `sgw_bidform.json` does not exist, so nothing has "
               "ever recorded what happens after 'Place My Bid' - and the last "
               "three snipes all submitted without landing.\n"
               "Run once, in a visible window:  "
               "`python -m flipscout.snipe verify <item-url>`")
        print(msg)
        try:
            notify(msg, subject="Flipscout - ShopGoodwill bid form unproven")
        except Exception:
            pass
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
            msg = (f":warning: **Snipe MISSED** {(a.get('title') or '(untitled)')[:60]} - only "
                   f"{left:.0f}s left when checked, too late to bid safely.\n{a.get('url', '')}")
            print(msg)
            if not dry_run:
                notify(msg, subject="Flipscout snipe missed")
            continue

        need = float(d.get("minimumBid") or 0)
        cap = float(a["max_bid"])
        if need > cap:
            a["status"] = "PASSED_TOO_HIGH"
            changed = True
            msg = (f":no_entry: **Snipe passed** {(a.get('title') or '(untitled)')[:60]} - the next "
                   f"required bid is ${need:.2f}, above your ${cap:.2f} max.\n{a.get('url', '')}")
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
        outbid = (not ok) and str(detail_msg).startswith(OUTBID)
        a["status"] = ("DRY_RUN" if dry_run else
                       "BID" if ok else "OUTBID" if outbid else "FAILED")
        a["result"] = detail_msg
        a["price_before"] = before
        changed = True

        # Re-read so the alert reports what it actually COST, not what was
        # authorised - the whole point of proxy bidding is that those differ.
        # 🚨 Re-read the price on a LOSS too, not just a win. Being outbid
        # without a number is useless: "you lost" and "you lost by $1" call for
        # completely different responses, and the gap is the only evidence that
        # the book's ceiling is set too low.
        after = None
        if not dry_run:
            try:
                time.sleep(2)
                after = float(detail(iid).get("currentPrice") or 0)
                a["price_after"] = after
            except Exception:
                pass

        icon = ":dart:" if ok else (":raised_hand:" if outbid else ":x:")
        lines = [f"{icon} **Snipe {'(dry run) ' if dry_run else ''}{a['status']}** "
                 f"on {(a.get('title') or '(untitled)')[:60]}",
                 f"current bid when it fired: **${before:.2f}**  (needed ${need:.2f})",
                 f"your armed max: **${cap:.2f}**"]
        if after is not None:
            if outbid:
                lines.append(f"it is at **${after:.2f}** now, against your "
                             f"${cap:.2f} max - beaten by ${max(0, after - cap):.2f}. "
                             f"Nothing was spent.")
            elif not ok:
                # 🚨 See hibidsnipe: a bid that did not land is not a win.
                lines.append(f"price now: **${after:.2f}**. Your bid did NOT go "
                             f"through, so you are not in this one.")
            elif after <= cap:
                lines.append(f"price now: **${after:.2f}** - you are winning, "
                             f"${cap - after:.2f} under your max")
            else:
                lines.append(f"price now: **${after:.2f}** - ABOVE your max, "
                             f"you were outbid")
        lines += [detail_msg, a.get("url", "")]
        msg = "\n".join(lines)
        print(msg)
        if not dry_run:
            notify(msg, subject="Flipscout snipe")
    if changed:
        save_armed(armed)
    # Close the loop on anything that has since finished.
    try:
        report_outcomes(dry_run=dry_run)
    except Exception as e:
        print(f"outcome pass failed (non-fatal): {type(e).__name__}")
    return 0


def bid_count(d: dict):
    """How many bids the site says this item has, or None.

    🚨 THE FIELD IS `numberOfBids`, AND THIS MODULE ASKED FOR `numBids`.

    Measured 2026-08-20 on item 274020144: the detail response carries 88
    fields, `numberOfBids` is 1, and `numBids` is not among them. mybids.py has
    read `numberOfBids` correctly all along; snipe.py asked for the wrong name
    in five places, and every one of them is on the VERIFICATION path.

    The consequence was silent and total: place_bid proves a bid landed by
    watching this number move, so it was comparing None to None on every single
    snipe and falling through to "UNVERIFIED - submitted but could not confirm
    it landed". The bid may or may not have gone in; the code could never tell,
    and run() then booked it as FAILED either way. verify() had the same hole,
    which is why it could not report whether a click had worked.

    Tolerant of both spellings so a future rename cannot re-open this.
    """
    for key in ("numberOfBids", "numBids"):
        v = d.get(key)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
    return None


def bidform() -> dict:
    if not BIDFORM_PATH.exists():
        return {}
    try:
        return json.loads(BIDFORM_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def outcome_of(iid: str, my_max: float, d: dict) -> tuple:
    """(result, final_price, reason) once a ShopGoodwill lot is over.

    result is "won", "lost" or None when it genuinely cannot be told.

    🚨 SHOPGOODWILL DOES NOT SAY WHO WON. Checked on a real ended lot with a
    signed-in session: the page reads only "Auction Ended | Bids: 0 | Current
    Price: $8.99" - no "you won", no "you were outbid", nothing about the
    viewer at all. So two things are certain and the rest is not:

      * nobody bid          -> certainly not ours
      * it closed ABOVE our max -> certainly not ours

    Anything else is reported as UNCLEAR with a link, rather than guessed at.
    Claiming a win we cannot see is the mistake that cost lot 273688511.
    """
    final = None
    try:
        final = float(d.get("currentPrice") or 0)
    except (TypeError, ValueError):
        pass
    bids = bid_count(d)
    try:
        bids = int(bids) if bids is not None else None
    except (TypeError, ValueError):
        bids = None

    if bids == 0:
        return "lost", final, "nobid"      # nobody bid at all, us included
    if final is not None and my_max and final > my_max + 1e-9:
        return "lost", final, "outbid"     # it went past our ceiling

    # Ask the page anyway - if ShopGoodwill ever starts saying it, use it.
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            try:
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(f"https://shopgoodwill.com/item/{iid}", timeout=40000,
                        wait_until="domcontentloaded")
                pg.wait_for_timeout(4000)
                body = pg.inner_text("body") or ""
                if re.search(r"you won|congratulations|you are the winning bidder",
                             body, re.I):
                    return "won", final, "page"
                if re.search(r"you (?:were |have been )?outbid|you did not win",
                             body, re.I):
                    return "lost", final, "page"
                # 🚨 The PAGE carries the bid count even when the public API
                # answers null for it - "Auction Ended|Bids:0". That zero is
                # the difference between "someone outbid you" and "our bidder
                # is broken", so it is worth the extra read.
                m = re.search(r"Bids:\s*(\d+)", body, re.I)
                if m and int(m.group(1)) == 0:
                    return "lost", final, "nobid"
            finally:
                ctx.close()
    except Exception:
        pass
    return None, final, "unclear"


def _num(v, default: float = 0.0) -> float:
    """A float from whatever the armed file happens to hold.

    🚨 Entries are written by several code paths and have gained fields all
    session; a string or None where a number was expected used to raise and
    take the whole outcome pass down with it.
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def report_outcomes(dry_run: bool = False) -> int:
    """Tell Leron whether he WON or LOST, once the auction is over.

    🚨 THIS IS THE ONLY PART THAT CLOSES THE LOOP. Everything else reports at
    BID time - a snapshot taken minutes before the close that says nothing
    about how it ended.

    It works off what the sniper itself bid on, so unlike the Bid sentry it
    needs no "Auctions in Progress" CSV and cannot go stale - the export Leron
    was relying on was 19.6 days old.
    """
    from .notify import notify
    try:
        armed = load_armed()
    except ArmedFileCorrupt:
        return 0                           # run() already shouted
    changed = acted = 0
    for iid, a in list(armed.items()):
        if a.get("status") not in ("BID", "OUTBID", "FAILED"):
            continue
        try:
            d = detail(iid)
        except Exception:
            continue
        left = seconds_left(d)
        if not (d.get("isItemEndTimeExpire") or (left is not None and left <= 0)):
            continue

        cap = _num(a.get("max_bid"))
        res, final, why_code = outcome_of(iid, cap, d)
        a["status"] = {"won": "WON", "lost": "LOST"}.get(res, "ENDED_UNKNOWN")
        a["final_price"] = final
        changed += 1

        price = f"${final:,.2f}" if final is not None else "an unknown price"
        if res == "won":
            lines = [f":trophy: **WON** - {(a.get('title') or '(untitled)')[:60]}",
                     f"Yours at **{price}**, against your ${cap:,.2f} max.",
                     "Pay the invoice, then log it with `flipscout bought`.",
                     a.get("url", "")]
        elif res == "lost":
            # 🚨 Distinguish "outbid" from "our bid never landed". They call
            # for completely different responses, and conflating them is how a
            # broken bidder hides behind bad luck.
            # 🚨 "Outbid" and "our bid never landed" call for completely
            # different responses, and conflating them is how a broken bidder
            # hides behind bad luck.
            why = ""
            if why_code == "nobid":
                why = (" :rotating_light: **Nobody bid at all - including us.** "
                       "Our bid never landed, so this was lost to a fault, not "
                       "to competition.")
            elif final is not None and final > cap:
                why = f" It went ${final - cap:,.2f} past your max."
            lines = [f":x: **Lost** - {(a.get('title') or '(untitled)')[:60]}",
                     f"Closed at **{price}**; your max was ${cap:,.2f}.{why}",
                     "Nothing was spent.", a.get("url", "")]
        else:
            lines = [f":grey_question: **Ended - outcome unclear** - "
                     f"{(a.get('title') or '(untitled)')[:60]}",
                     f"Closed at **{price}**, at or under your ${cap:,.2f} max, "
                     f"so you may have taken it.",
                     "ShopGoodwill does not say who won on the item page - "
                     "check your account.", a.get("url", "")]
        msg = "\n".join(x for x in lines if x)
        print(msg)
        if not dry_run:
            try:
                notify(msg, subject="Flipscout auction result")
            except Exception:
                pass
        acted += 1
    if changed:
        save_armed(armed)
    return acted


def verify(url_or_id: str, timeout_s: int = 240) -> int:
    """Learn what "Place My Bid" actually does. ONE TIME.

    🚨 WHY THIS EXISTS. On 2026-08-18 a snipe filled the box, clicked "Place My
    Bid", found no error text, and reported "you are winning" - while the lot
    read "Number of Bids: 0" and then closed unsold. The selectors were right;
    something AFTER the click swallowed the bid, and the code had no way to
    tell because it treated "no error" as success.

    Rather than guess at a confirmation step, this watches Leron do it once and
    writes down what appears. Same approach that settled HiBid, for the same
    reason: a money path must be observed, not assumed.

    It opens the lot in the sniper's OWN visible window, waits for him to fill
    a bid and press the button, and records every dialog, button and message
    that follows. It never types an amount and never presses anything.
    """
    from playwright.sync_api import sync_playwright
    iid = item_id(url_or_id)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.goto(f"https://shopgoodwill.com/item/{iid}", timeout=60000,
                wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
        pg.evaluate("""() => {
            const d = document.createElement('div');
            d.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;'
              + 'background:#c2410c;color:#fff;font:700 15px system-ui;padding:10px;'
              + 'text-align:center';
            d.textContent = 'Type a bid and press Place My Bid. Flipscout is '
              + 'watching what happens next. Do NOT confirm anything after that.';
            document.body.appendChild(d);
        }""")
        try:
            before = bid_count(detail(iid))
        except Exception:
            before = None
        print("A window just opened on the lot.")
        print("  1. Type a bid you are happy to actually place.")
        print("  2. Press 'Place My Bid' ONCE.")
        print("  3. STOP - do not press anything that appears after it.")
        print(f"Watching for up to {timeout_s}s...")

        deadline = time.time() + timeout_s
        seen = None
        while time.time() < deadline:
            try:
                seen = pg.evaluate(r"""() => {
                    const q = s => [...document.querySelectorAll(s)];
                    const modal = q('.modal,[role=dialog],.modal-content,'
                                    + 'ngb-modal-window,.mat-dialog-container')[0];
                    const btns = (modal || document).querySelectorAll('button');
                    const txt = (modal || document.body).innerText || '';
                    if (!modal && !/confirm|are you sure|review your bid/i.test(txt))
                        return null;
                    return {
                        isModal: !!modal,
                        text: txt.replace(/\s+/g,' ').slice(0, 400),
                        buttons: [...btns].map(b => (b.innerText||'').trim())
                                          .filter(Boolean).slice(0, 12)
                    };
                }""")
            except Exception:
                seen = None
            if seen:
                break
            time.sleep(2)

        after = None
        try:
            after = bid_count(detail(iid))
        except Exception:
            pass
        ctx.close()

        rec = {"observed": bool(seen), "bids_before": before, "bids_after": after}
        if seen:
            rec.update({"confirm_dialog": seen["isModal"],
                        "dialog_text": seen["text"][:200],
                        "buttons": seen["buttons"]})
            print("\nSomething appeared after the click:")
            print(f"  modal   : {seen['isModal']}")
            print(f"  buttons : {seen['buttons']}")
            print(f"  text    : {seen['text'][:180]}")
            print("\nDo NOT press anything else. Recorded.")
        elif before is not None and after is not None and after > before:
            rec["confirm_dialog"] = False
            print("\nThe bid went straight through - no confirmation step.")
            print(f"  bids {before} -> {after}")
            print("So the earlier failure was NOT a missing confirm click.")
        else:
            print("\nNothing observed and the bid count did not move.")
            print(f"  bids {before} -> {after}")
            print("Either the click never landed, or the site rejected it "
                  "silently. Run it again and watch the window.")
        BIDFORM_PATH.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        return 0 if seen or (after or 0) > (before or 0) else 1


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
    if cmd == "verify":
        if len(argv) < 2:
            print("usage: snipe verify <item-url-or-id>")
            return 2
        return verify(argv[1])
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

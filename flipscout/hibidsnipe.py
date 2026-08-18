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

🚨 THE BID FORM MUST BE PROVEN BEFORE THIS CAN BID
--------------------------------------------------
A HiBid lot page has NO bid input. The only control is one button reading
"Bid 5.00 USD" - the next increment, not a max. Whether clicking it opens a
confirmation dialog or places the bid instantly is a PER-ACCOUNT PREFERENCE,
and nothing in the DOM reveals which. On the auction inspected 2026-08-18 the
auctioneer's terms read "BIDS CANNOT BE CANCELED - ALL BIDS ARE FINAL!".

So a blind click is a coin flip on an irreversible contract, and this module
refuses to make it. `run()` will not place a real bid until `hibidsnipe verify`
has recorded what that button actually does, in BIDFORM_PATH.

    hibidsnipe verify https://hibid.com/lot/<id>

That opens the lot in the sniper's OWN visible window, asks Leron to click Bid
once and stop, watches the DOM for the dialog, writes down the max-bid input
and confirm button it finds, and never confirms anything itself. One time, for
the account - not per lot.

🚨 Claude must never click the bid button to "find out". The verify step
exists precisely so that guessing is unnecessary.

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
# What `verify` learned about this account's bid button. Absent = never proven,
# and an unproven button is never clicked with real money behind it.
BIDFORM_PATH = REPO / "hibid_bidform.json"
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


def bidform() -> dict:
    """What `verify` recorded, or {} if it was never run."""
    if not BIDFORM_PATH.exists():
        return {}
    try:
        return json.loads(BIDFORM_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def bidform_ok() -> bool:
    """True only when the bid button is known to be confirm-gated.

    🚨 This is the gate that stops a coin-flip click on an irreversible
    bid. If verify found NO dialog, the button commits instantly - which means
    it would bid the site's increment, never Leron's max, and could not be
    stopped. That is not a snipe, so it stays refused.
    """
    f = bidform()
    return bool(f.get("confirm_dialog")) and bool(f.get("max_input"))


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


def signed_in(ctx) -> bool:
    """True when this browser context holds a live HiBid session.

    🚨 READ THE COOKIE, NOT THE PAGE. HiBid renders its header through
    JavaScript, so "My HiBid" / "Sign Out" never appear in `inner_text` on a
    freshly loaded page even when you ARE signed in - measured 2026-08-18,
    where a working session showed none of those markers and `login` reported
    a timeout on an account that had signed in fine.

    That mattered for more than the login message: place_bid used the same
    text test and would have aborted every real bid with "LOGGED OUT",
    silently, at T-180s, with no way to tell it from a genuine expiry.

    `HBIsLoggedIn` is HiBid's own flag and it sits alongside a JWT `sessionId`
    and a `hibid-refresh-token`; require the JWT too, so a stale flag left
    behind by a logout cannot read as a session.
    """
    try:
        ck = {c["name"]: (c.get("value") or "") for c in ctx.cookies()}
    except Exception:
        return False
    return ck.get("HBIsLoggedIn") == "1" and len(ck.get("sessionId") or "") > 40


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
            if not signed_in(ctx):
                return False, "LOGGED OUT - run `hibidsnipe login`"
            body = (pg.inner_text("body") or "")[:5000]
            if re.search(r"register to bid|you must register|not registered", body, re.I):
                return False, "NOT REGISTERED for this auction - register first"

            form = bidform()
            if not (dry_run or bidform_ok()):
                return False, "bid form not verified - run `hibidsnipe verify`"

            # Open the dialog. 🚨 This click is only safe because verify
            # proved it opens a dialog rather than committing - see the header.
            opener = pg.query_selector(form.get("open_button")
                                       or "button.lot-bid-button-bid-amount")
            if not opener:
                return False, "the 'Bid <amount>' button is not on the page"
            opener.click()
            pg.wait_for_timeout(2000)

            box = pg.query_selector(form.get("max_input") or "")
            if not box:
                return False, ("the bid dialog did not appear as recorded - "
                               "re-run `hibidsnipe verify`. NOT bidding.")
            box.fill(f"{amount:.2f}")

            confirm_sel = form.get("confirm_button")
            btn = pg.query_selector(confirm_sel) if confirm_sel else None
            if not btn:
                return False, "the confirm button moved - re-run `hibidsnipe verify`"
            if dry_run:
                return True, (f"DRY RUN - dialog open, max ${amount:.2f} entered, "
                              f"confirm NOT clicked")

            btn.click()
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
    # 🚨 Never click an unproven bid button with real money behind it.
    # A dry run is still allowed - that is how you rehearse before verifying.
    if not dry_run and not bidform_ok():
        print("bid form not verified - run `hibidsnipe verify <lot-url>` once.\n"
              "Refusing to bid: on this site a bid cannot be cancelled, and it "
              "is an account preference whether that button confirms first.")
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


def verify(url_or_id: str, timeout_s: int = 240) -> int:
    """Learn what this account's bid button actually does. ONE TIME.

    Opens the lot in the sniper's OWN visible window - which removes the "which
    tab am I looking at" problem entirely - and waits for Leron to click the
    Bid button once. It watches the DOM, and:

      * if a dialog appears, records the max-bid input and the confirm button,
        so place_bid can drive the real thing instead of guessed selectors;
      * if the page commits instead, records that and the sniper stays refused,
        because a button that bids instantly can only ever bid the site's
        increment - never Leron's max.

    🚨 This function never clicks the bid button and never confirms
    anything. Leron clicks; it only reads.
    """
    from playwright.sync_api import sync_playwright
    lid = lot_id(url_or_id)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.goto(_LOT_URL.format(lid), timeout=60000, wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        if not signed_in(ctx):
            print("not signed in - run `hibidsnipe login` first")
            ctx.close()
            return 1

        pg.evaluate("""() => {
            const d = document.createElement('div');
            d.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;'
              + 'background:#c2410c;color:#fff;font:700 15px system-ui;padding:10px;'
              + 'text-align:center';
            d.textContent = 'Click the blue Bid button ONCE, then STOP. '
              + 'Do not confirm. Flipscout is reading the dialog.';
            document.body.appendChild(d);
        }""")
        print("A window just opened on the lot.")
        print("  1. Click the blue 'Bid <amount>' button ONCE.")
        print("  2. STOP. Do not confirm, do not press anything else.")
        print(f"Watching for up to {timeout_s}s...")

        deadline = time.time() + timeout_s
        found = None
        while time.time() < deadline:
            try:
                found = pg.evaluate(r"""() => {
                    const q = s => [...document.querySelectorAll(s)];
                    const m = q('ngb-modal-window,.modal,[role=dialog],.modal-content')[0];
                    if (!m) return null;
                    const inp = [...m.querySelectorAll('input')]
                        .filter(i => i.type !== 'hidden')
                        .map(i => ({type: i.type, name: i.name, id: i.id,
                                    fc: i.getAttribute('formcontrolname'),
                                    ph: i.placeholder, cls: i.className}));
                    const btn = [...m.querySelectorAll('button')]
                        .map(b => ({t: (b.innerText||'').trim(), cls: b.className}))
                        .filter(b => b.t);
                    return {text: (m.innerText||'').replace(/\s+/g,' ').slice(0,400),
                            inputs: inp, buttons: btn};
                }""")
            except Exception:
                found = None
            if found:
                break
            time.sleep(2)

        if not found:
            print("\nNo dialog appeared.")
            print("If you clicked and the bid went straight through, this account "
                  "has bid confirmation OFF - the sniper stays disabled, because "
                  "that button can only bid the site's increment, never your max.")
            BIDFORM_PATH.write_text(json.dumps(
                {"confirm_dialog": False, "note": "no dialog observed"},
                indent=2), encoding="utf-8")
            ctx.close()
            return 1

        def _sel(d):
            if d.get("id"):
                return f"#{d['id']}"
            if d.get("fc"):
                return f"[formcontrolname='{d['fc']}']"
            if d.get("name"):
                return f"input[name='{d['name']}']"
            cls = (d.get("cls") or "").split()
            return "." + ".".join(cls[:2]) if cls else "input"

        amount_like = [i for i in found["inputs"]
                       if re.search(r"bid|amount|max", str(i), re.I)]
        chosen = (amount_like or found["inputs"] or [None])[0]
        confirm = next((b for b in found["buttons"]
                        if re.search(r"place|confirm|submit|bid|yes|ok", b["t"], re.I)),
                       None)
        rec = {
            "confirm_dialog": True,
            "max_input": _sel(chosen) if chosen else None,
            "confirm_button": (f"button.{confirm['cls'].split()[0]}"
                               if confirm and confirm.get("cls") else None),
            "confirm_text": confirm["t"] if confirm else None,
            "open_button": "button.lot-bid-button-bid-amount",
            "dialog_text": found["text"][:200],
            "verified_on_lot": lid,
        }
        BIDFORM_PATH.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        print("\nDialog found and recorded:")
        for k, v in rec.items():
            print(f"  {k:16s} {v}")
        print("\nClose the dialog WITHOUT confirming. Nothing was bid.")
        print("The sniper is now allowed to bid on armed lots.")
        ctx.close()
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
            if signed_in(ctx):
                print("Signed in - session saved.")
                ctx.close()
                return 0
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
    if cmd == "verify":
        if len(argv) < 2:
            print("usage: hibidsnipe verify <lot-url-or-id>")
            return 2
        return verify(argv[1])
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

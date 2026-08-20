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

from .auctionfees import min_increment, parse_premium, parse_tax
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

# Prefix on the place_bid message when a rival proxy outbid us on the spot.
# 🚨 This is a NORMAL outcome, not a fault - somebody simply valued the thing
# more than we did. It has to be distinguishable from a real failure (a moved
# selector, a dead session), because one needs no action and the other needs
# fixing, and burying both under "FAILED" hides the broken one.
OUTBID = "OUTBID"

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


class ArmedFileCorrupt(Exception):
    """The armed-lots file could not be read.

    🚨 THIS MUST NEVER BE SWALLOWED. load_armed() used to answer an unreadable
    file with {} - so a half-written file made every armed lot vanish, `show`
    printed "nothing armed", and the sniper sat silent through the close. That
    is indistinguishable from a quiet day, which is the worst possible failure
    for something whose whole job is to act at a deadline.
    """


def load_armed() -> dict:
    if not ARMED_PATH.exists():
        return {}
    try:
        return json.loads(ARMED_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        # Keep the evidence - the armed maxes may be recoverable by hand.
        kept = ARMED_PATH.with_suffix(".corrupt")
        try:
            kept.write_bytes(ARMED_PATH.read_bytes())
        except Exception:
            kept = None
        raise ArmedFileCorrupt(
            f"{ARMED_PATH.name} is unreadable ({type(e).__name__})"
            + (f"; a copy is at {kept.name}" if kept else "")) from e


def save_armed(d: dict) -> None:
    # 🚨 ATOMIC. A plain write_text leaves a truncated file if the process dies
    # mid-write, and the scheduled task is killed at a four-minute limit. Write
    # beside it and rename: os.replace is atomic within a volume, so a reader
    # sees either the whole old file or the whole new one, never a half.
    tmp = ARMED_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, ARMED_PATH)


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
    return bool(f.get("confirm_dialog")) and bool(f.get("confirm_text"))


def _blob(text: str, key: str):
    """Pull one JSON value out of the page's embedded state.

    🚨 THE STRING BRANCH MUST ALLOW ESCAPED QUOTES. `"[^"]*"` stops at the
    first quote character, and an INCH MARK inside a title is exactly that:

        "lead":"LOT (4) MITUTOYO 0-1\" DIGITAL MICROMETERS"

    That captured a truncated fragment, json.loads rejected it, and the title
    came back empty - so the book could not price the lot and arming was
    refused as "stale". Measured 2026-08-19 on a lot Leron tapped. Inch marks
    are everywhere in tool listings, so this silently blocked a whole category.
    """
    m = re.search(
        rf'"{key}":\s*(\{{.*?\}}|\[.*?\]|"(?:[^"\\]|\\.)*"|true|false|null|-?[\d.]+)',
        text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _og_image(text: str) -> str:
    """The lot's own featured image, from the page's og:image meta tag."""
    m = re.search(r'<meta property="og:image" content="([^"]{4,400})"', text)
    if not m:
        return ""
    url = m.group(1).replace("&amp;", "&").strip()
    return url if url.startswith("http") else ""


def detail(lid: str) -> dict:
    """Everything about one lot, from ONE response.

    Parsed out of the lot page rather than GraphQL: HiBid's schema blocks
    introspection and exposes no single-lot query (`lot` takes an ID but
    returns an opaque LotAccessType), while the page embeds the whole state
    object the site's own UI runs on.

    🚨 THE PAGE IS NOT ALWAYS THE LOT YOU ASKED FOR. Measured 2026-08-20:
    hibid.com/lot/318429788 returned a body whose `lead` was "1988 Pillsberry
    Company cookie jar" while the page's own <title> said "Complete Gameboy
    Advance Pokemon Sapphire Version", and /lot/313948695 came back once as
    "Back to the Future 3 Movie Script" with isClosed=true, then correctly as
    the Mitutoyo micrometers with six days left on the next poll. Same URL,
    same second, different lot.

    Reading that blindly is how all four armed lots died: run() believes
    `closed` immediately, so one contaminated response retires a live snipe
    permanently and silently, and arm() priced two ceilings off the WRONG
    item's title ("STOP Sign" for what is really a Casio G-Shock).

    So every field is now read from the requested lot's OWN record, anchored on
    `"id":<lid>,` - `lead` sits 31 bytes after it and `lotState` 153. If that
    anchor is absent the response is about some other lot and is reported as
    `gone` (a transient bad fetch, which the caller already handles with a
    three-strike rule) plus `mismatch`, so it can never be mistaken for the
    site saying this lot has ended.
    """
    r = requests.get(_LOT_URL.format(lid), headers={"User-Agent": _UA}, timeout=30)
    r.raise_for_status()
    t = r.text
    anchor = t.find(f'"id":{lid},')
    if anchor == -1:
        return {"lot_id": lid, "gone": True, "mismatch": True}
    # Lot-scoped: everything that describes THIS lot lives in its own record.
    scope = t[anchor:anchor + 20000]
    st = _blob(scope, "lotState") or {}
    if not st:
        # Closed and archived auctions serve a stripped shell with no state.
        return {"lot_id": lid, "gone": True}
    return {
        "lot_id": lid,
        "gone": False,
        # 🚨 SCOPED, like lotState. This is the field that priced the wrong
        # item: read from the whole document it returned another lot's name.
        "title": (_blob(scope, "lead") or "").strip(),
        # 🚨 UNKNOWN, NOT ZERO. `or 0` collapsed "the page did not say" into
        # "the lot is at $0.00", and run() then priced the next bid off that
        # phantom and reported "$0.00 when it fired" - a number that was never
        # true. A genuine no-bids lot really is 0, so the two must stay apart.
        "high_bid": (float(st["highBid"])
                     if st.get("highBid") is not None else None),
        "min_bid": float(st.get("minBid") or 0) or None,
        "bids": int(st.get("bidCount") or 0),
        "closed": bool(st.get("isClosed")),
        "left": st.get("timeLeftSeconds"),
        "extended": bool(st.get("biddingExtended")),
        # 🚨 UNKNOWN, NOT FALSE. This request carries no cookies, so HiBid
        # answers as an anonymous visitor and `isRegistered` is ALWAYS false -
        # it describes nobody. Reading it as "Leron is not registered" made the
        # registration gate reject every lot forever, which would have looked
        # exactly like a sniper that quietly never fires.
        #
        # Only an authenticated read can answer this; see registered_authed().
        "registered": True if st.get("isRegistered") else None,
        # 🚨 og:image, NOT featuredPicture. The page carries 212 copies of
        # `fullSizeLocation` and every one before the lot's own record belongs
        # to a DIFFERENT lot in the same catalog - the same trap that made this
        # function read the wrong lot in the first place. og:image is the one
        # image the page states about ITSELF, and by the time we reach here the
        # `"id":<lid>` anchor has already proved the page is this lot.
        "image": _og_image(t),
        "increments": _blob(t, "bidIncrements"),
        "premium": parse_premium(_blob(t, "buyerPremium")),
        "tax": parse_tax(_blob(t, "paymentInfo"), _blob(t, "state")),
        "notice": (_blob(t, "biddingNotice") or "")[:400],
    }


def registered_authed(lid: str) -> Optional[bool]:
    """Whether LERON is registered for this lot's auction. Authenticated.

    Costs a browser launch, so `run()` only asks once a lot is close enough to
    matter - never on every poll of every armed lot.
    """
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            try:
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(_LOT_URL.format(lid), timeout=40000,
                        wait_until="domcontentloaded")
                pg.wait_for_timeout(3500)
                if not signed_in(ctx):
                    return None
                body = (pg.inner_text("body") or "")
                if re.search(r"register to bid|you must register|not registered",
                             body, re.I):
                    return False
                # A live "Bid <amount>" control only renders for a bidder who
                # may actually use it.
                return bool(pg.query_selector("button.lot-bid-button-bid-amount"))
            finally:
                ctx.close()
    except Exception:
        return None



# Terms that change whether a lot is worth winning, not just what it costs.
#
# 🚨 LERON SAID "ASSUME I ACCEPT THE TERMS WHEN I TAP THE TARGET". Taken
# literally that means auto-registering, and this is why the answer is to show
# him the terms instead:
#
#   * The terms are not uniform. Lot 313948695 (DeCosmo Industrial, 2026-08-19)
#     is CASH OR BANK WIRE ONLY - no card, so no chargeback and an irreversible
#     payment - with ALL LOTS MUST BE REMOVED BY SEPTEMBER 4TH (NO EXCEPTIONS)
#     and daily storage charges after. Those are not consumer terms, and no
#     standing "yes" should cover them sight unseen.
#   * Registration is often not a click. That same house reserves bidder
#     approval "at their sole discretion" and "may require a registration
#     deposit", so a bot cannot complete it anyway.
#
# So the automation is: read the terms that bite, put them in front of him, and
# let one informed tap do the rest.
_TERM_PATTERNS = (
    ("payment", re.compile(r"Payment\s+Types?:\s*([^\r\n|]{0,60})", re.I)),
    ("removal", re.compile(r"(ALL LOTS MUST BE REMOVED[^.\r\n]{0,60}"
                           r"|FINAL DATE FOR REMOVAL[^.\r\n]{0,60})", re.I)),
    ("approval", re.compile(r"(reserves the right to deny bidding[^.]{0,70}"
                            r"|require a registration deposit[^.]{0,40})", re.I)),
)

# Payment methods with no buyer protection. A wire cannot be reversed.
_HARD_PAYMENT = re.compile(r"wire\s*transfer|cash\s*only|cashier'?s?\s*check", re.I)


def terms_flags(lid: str) -> dict:
    """The handful of auction terms that decide whether to bid at all."""
    out = {}
    try:
        t = requests.get(_LOT_URL.format(lid), headers={"User-Agent": _UA},
                         timeout=30).text
    except Exception:
        return out
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
    for key, pat in _TERM_PATTERNS:
        m = pat.search(flat)
        if m:
            out[key] = (m.group(1) if m.groups() else m.group(0)).strip()[:70]
    pay = out.get("payment", "")
    out["no_card"] = bool(_HARD_PAYMENT.search(pay)) and not re.search(
        r"credit|visa|master|card", pay, re.I)
    return out


def outcome_authed(lid: str) -> Optional[str]:
    """Did Leron WIN this lot? "won" / "lost" / None if it cannot be told.

    🚨 MUST BE AUTHENTICATED. `buyerBidStatus` describes whoever is asking, and
    the cheap anonymous poll is nobody - it reads NO_BID on every lot,
    including ones he is winning. Same trap as isRegistered.
    """
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            try:
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(_LOT_URL.format(lid), timeout=40000,
                        wait_until="domcontentloaded")
                pg.wait_for_timeout(4000)
                if not signed_in(ctx):
                    return None
                html = pg.content() or ""
                m = re.search(r'"buyerBidStatus":"([A-Z_]+)"', html)
                status = m.group(1) if m else ""
                if status in ("WON", "HIGH_BID", "WINNING"):
                    return "won"
                if status in ("LOST", "OUTBID"):
                    return "lost"
                body = (pg.inner_text("body") or "")
                if re.search(r"you won|congratulations", body, re.I):
                    return "won"
                if re.search(r"you (?:were |have been )?outbid|you lost", body, re.I):
                    return "lost"
                if status == "NO_BID":
                    return "lost"          # ended with no bid of ours standing
                return None
            finally:
                ctx.close()
    except Exception:
        return None


def seconds_left(d: dict) -> Optional[float]:
    v = d.get("left")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def book_ceiling(title: str, premium: float = 0.0, inbound: float = 9.0,
                 target_profit: float = 20.0, tax: float = 0.0) -> Optional[float]:
    """What the price book says this lot is worth as a HAMMER bid.

    `target_profit` is what you insist on clearing. The default 20 is the
    disciplined number; pass 0 to get the BREAK-EVEN hammer, which is the point
    where winning stops being worth anything.
    """
    m = match(title)
    if not m:
        return None
    a = advise(m.model.comp, units=m.units, inbound_shipping=inbound,
               outbound_shipping=m.model.outbound_shipping,
               target_profit=target_profit, current_price=1,
               buyer_premium_rate=premium, sales_tax_rate=tax)
    return a.max_bid if a.max_bid > 0 else None


def stretch_to(title: str, base: float, extra: float, premium: float = 0.0,
               inbound: float = 9.0) -> tuple:
    """Raise a ceiling by `extra`, but never past break-even.

    🚨 THE STRETCH IS NOT FREE MONEY - IT IS PROFIT YOU ARE SPENDING. The base
    ceiling is priced to clear TARGET_PROFIT; every dollar above it clears a
    dollar less, and past break-even you are paying to own the thing.

    On a proxy system a stretch costs nothing unless a rival is sitting between
    the old ceiling and the new one - which is exactly the case where you lost
    narrowly and wished you had gone higher. That is the whole point of it.

    Returns (ceiling, clears, breakeven, clamped).
    """
    want = round(base + max(0.0, float(extra)), 2)
    be = book_ceiling(title, premium=premium, inbound=inbound, target_profit=0.0)
    clamped = False
    if be is not None and want > be:
        want, clamped = round(be, 2), True
    clears = None
    if be is not None:
        # Every dollar of hammer above break-even costs (1 + premium) all-in.
        clears = round((be - want) * (1.0 + float(premium)), 2)
    return want, clears, (round(be, 2) if be is not None else None), clamped


def arm(url_or_id: str, max_bid: float, override: bool = False,
        stretch: float = 0.0) -> int:
    lid = lot_id(url_or_id)
    d = detail(lid)
    # 🚨 NEVER PRICE A CEILING OFF A PAGE THAT IS NOT THIS LOT. This already
    # happened twice: lot 317709253 was armed at $11.09 as "STOP Sign" and is
    # really a Casio G-Shock, and 318429788 was armed at $22.77 as a "1992
    # Topps Baseball Card Set" and is really a Pokemon Sapphire cart. Both
    # ceilings came from the book pricing somebody else's item. Retry rather
    # than arm - the contamination is intermittent, so the next read is usually
    # clean.
    for _ in range(4):
        if not d.get("mismatch"):
            break
        time.sleep(1.5)
        d = detail(lid)
    if d.get("mismatch"):
        print(f"{lid}: HiBid kept returning a DIFFERENT lot's page for this "
              f"URL (5 tries). Not arming - a ceiling priced off the wrong "
              f"item is worse than no ceiling. Try again in a minute.")
        return 1
    if d.get("gone"):
        print(f"{lid}: lot is closed or archived - nothing to arm")
        return 1
    cap = float(max_bid)
    prem = float(d.get("premium") or 0)

    book = book_ceiling(d["title"], premium=prem)

    # A stretch is an explicit "I will give up profit to win this one".
    stretch = max(0.0, float(stretch or 0.0))
    clears = breakeven = None
    clamped = False
    if stretch:
        cap, clears, breakeven, clamped = stretch_to(
            d["title"], cap, stretch, premium=prem)
        override = True                    # he asked for it, by name and amount

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
    if stretch:
        if clamped:
            print(f"  :warning: STRETCH CLAMPED at break-even ${breakeven:,.2f} - "
                  f"anything above that is buying at a loss")
        elif clears is not None:
            print(f"  stretched ${stretch:,.2f} over the book - you clear "
                  f"${clears:,.2f} instead of ${20.0:,.2f} "
                  f"(break-even ${breakeven:,.2f})")
    hb = d.get("high_bid")
    print(f"  current bid      "
          + (f"${hb:,.2f} ({d.get('bids', 0)} bids)" if hb is not None
             else "not stated on the page"))
    if d["registered"] is None:
        print(f"  note: registration not checked here (the quick lookup is "
              f"anonymous). The sniper resolves it before it bids.")
    elif not d["registered"]:
        print(f"  :warning: NOT REGISTERED for this auction - this will NOT bid "
              f"until you register at {_LOT_URL.format(lid)}")

    armed = load_armed()
    armed[lid] = {"lot_id": lid, "title": d["title"], "url": _LOT_URL.format(lid),
                  "max_bid": round(cap, 2), "premium": prem,
                  "landed_at_max": round(landed, 2), "status": "ARMED",
                  "stretch": round(stretch, 2) or None,
                  "breakeven": breakeven}
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
    try:
        armed = load_armed()
    except ArmedFileCorrupt as e:
        print(f"CANNOT READ THE ARMED LIST: {e}")
        return 1
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

            # Everything from here on is scoped INSIDE the dialog, so a stray
            # match on the page behind it is impossible.
            modal = None
            for sel in ("ngb-modal-window", ".modal-content", "[role=dialog]",
                        ".modal"):
                modal = pg.query_selector(sel)
                if modal:
                    break
            if not modal:
                return False, ("the bid dialog did not appear - NOT bidding. "
                               "Re-run `hibidsnipe verify`.")

            # 🚨 Search AMONG THE INPUTS, never by a bare class. ".text-lg" was
            # recorded from the real dialog and matches a label there, not the
            # field - fill() then failed with "Element is not an <input>".
            fields = [i for i in modal.query_selector_all("input")
                      if (i.get_attribute("type") or "text")
                      not in ("hidden", "checkbox", "radio", "submit", "button")]
            box = None
            want = form.get("max_input")
            if want:
                for i in fields:
                    try:
                        if i.evaluate("(e,s) => e.matches(s)", want):
                            box = i
                            break
                    except Exception:
                        pass
            if not box and len(fields) == 1:
                box = fields[0]             # a one-field dialog is unambiguous
            if not box:
                return False, (f"could not identify the max-bid field among "
                               f"{len(fields)} inputs - NOT bidding. "
                               f"Re-run `hibidsnipe verify`.")
            box.fill(f"{amount:.2f}")

            # 🚨 Match the confirm button by its EXACT recorded text. Its class
            # is "btn", which is every button on the page.
            want = (form.get("confirm_text") or "").strip().lower()
            btn = None
            for b in modal.query_selector_all("button"):
                if (b.inner_text() or "").strip().lower() == want:
                    btn = b
                    break
            if not btn:
                return False, (f"the '{form.get('confirm_text')}' button is not "
                               f"in the dialog - NOT bidding.")
            if dry_run:
                shown = box.input_value() if hasattr(box, "input_value") else ""
                return True, (f"DRY RUN - dialog open, max bid field set to "
                              f"'{shown or amount}', '{form.get('confirm_text')}' "
                              f"located but NOT clicked")

            btn.click()
            pg.wait_for_timeout(3000)
            after = (pg.inner_text("body") or "")[:5000]
            if re.search(r"you are the high bidder|winning|bid accepted|"
                         r"bid placed|your bid", after, re.I):
                return True, f"BID PLACED at ${amount:.2f}"
            if re.search(r"outbid|higher bid|bid too low|increase", after, re.I):
                # 🚨 Say WHOSE max. "already above your max" reads as
                # though our own bid was the problem; what actually
                # happened is a rival proxy is standing higher than
                # our ceiling, so this lot cannot be won at our price.
                return False, (f"{OUTBID} - a standing bid is above your "
                               f"${amount:,.2f} max, so this cannot be won "
                               f"at your number")
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
    try:
        armed = load_armed()
    except ArmedFileCorrupt as e:
        # 🚨 Loud on purpose. An empty armed list and an unreadable one look
        # identical from the outside - silence - and only one of them means
        # snipes are being missed right now.
        msg = (f":rotating_light: **HiBid snipe list is UNREADABLE** - {e}\n"
               f"Nothing can be sniped until it is fixed. Re-arm the lots you "
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
        # 🚨 A MISSING PAGE IS NOT A CLOSED AUCTION. `gone` just means this
        # response carried no lotState, which HiBid also does on a transient
        # bad fetch. Retiring on the first one permanently killed a live lot
        # with 218 hours left on it (observed 2026-08-18) - the snipe was gone
        # and nothing said why.
        #
        # `closed` is different: that is the site stating the lot is finished,
        # so it is believed immediately.
        # 🚨 A MISMATCHED PAGE IS NOT THIS LOT, SO IT CANNOT END THIS LOT.
        # detail() sets `mismatch` when the body it got back belongs to a
        # different lot (see its docstring - HiBid does this intermittently).
        # Believing `closed` off one of those is what silently retired all four
        # armed lots on 2026-08-19 while every one of them was still live with
        # days to run. Fall through to the three-strike `gone` path instead.
        if d.get("closed") and not d.get("mismatch"):
            a["status"] = "ENDED_UNBID"
            changed = True
            continue
        if d.get("gone"):
            a["gone_strikes"] = int(a.get("gone_strikes") or 0) + 1
            changed = True
            if a["gone_strikes"] >= 3:
                a["status"] = "ENDED_UNBID"
                print(f"{lid}: no lot data three polls running - retiring")
            else:
                print(f"{lid}: no lot data (strike {a['gone_strikes']}/3) - "
                      f"probably a bad response, keeping it armed")
            continue
        if a.get("gone_strikes"):
            a["gone_strikes"] = 0          # it came back
            changed = True
        left = seconds_left(d)
        if left is None:
            print(f"{lid}: no countdown in the response - skipping")
            continue
        if left < 0:
            # 🚨 Not "it ended" - HiBid's own search endpoint has been seen
            # returning a negative countdown for lots with days left on them
            # (sign-flipped, magnitude correct). Retiring on that would kill a
            # live snipe. A real ending arrives as `closed`, handled above.
            print(f"{lid}: countdown came back negative ({left:.0f}s) - "
                  f"ignoring this poll")
            continue
        if left == 0:
            a["status"] = "ENDED_UNBID"
            changed = True
            continue

        # 🚨 THE REGISTRATION GATE. Check it before the clock so the warning
        # arrives while there is still time to act on it, not at T-180s.
        #
        # `d["registered"]` is None from the cheap anonymous poll, which means
        # UNKNOWN. Resolve it for real only once the lot is within a few hours
        # of closing - an authenticated check costs a browser launch, and there
        # is no point paying that on a lot nine days out.
        reg = d["registered"]
        if reg is None and left <= 4 * 3600:
            reg = registered_authed(lid)
        if reg is False:
            if a.get("warned_unregistered"):
                continue
            a["warned_unregistered"] = True
            changed = True
            msg = (f":lock: **Cannot snipe - you are not registered** for this "
                   f"auction.\n{(a.get('title') or '(untitled)')[:60]}\nRegister on the lot page and "
                   f"it will bid as armed. Closes in {left / 60:.0f} min.\n{a.get('url', '')}")
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
            msg = (f":warning: **Snipe MISSED** {(a.get('title') or '(untitled)')[:60]} - only "
                   f"{left:.0f}s left when checked.\n{a.get('url', '')}")
            print(msg)
            if not dry_run:
                notify(msg, subject="Flipscout HiBid snipe missed")
            continue

        cap = float(a["max_bid"])
        # 🚨 If neither the current price nor the minimum came through, we have
        # no idea what this lot stands at. Bidding anyway is bounded by the max
        # so it cannot overpay, but it burns the ONE bid this lot ever gets on
        # a guess, and reports a price that was never real. Wait for a good
        # read instead - the poller comes back in a minute.
        if d.get("min_bid") is None and d.get("high_bid") is None:
            print(f"{lid}: the page gave no price - skipping this poll")
            continue
        base = d["high_bid"] if d.get("high_bid") is not None else 0.0
        need = d.get("min_bid") or (base +
                                    min_increment(base, d.get("increments")))
        if need > cap:
            a["status"] = "PASSED_TOO_HIGH"
            changed = True
            msg = (f":no_entry: **Snipe passed** {(a.get('title') or '(untitled)')[:60]} - the next "
                   f"required bid is ${need:.2f}, above your ${cap:.2f} max.\n{a.get('url', '')}")
            print(msg)
            if not dry_run:
                notify(msg, subject="Flipscout HiBid snipe passed")
            continue

        # Bid the MAX, not one increment over the current price. HiBid is a
        # proxy system, so the max is a ceiling and you pay one step over the
        # runner-up - see the long note in snipe.py, which cost real losses to
        # learn ("lost the Featherweight by $1").
        before = d["high_bid"] if d.get("high_bid") is not None else base
        ok, detail_msg = place_bid(lid, cap, dry_run=dry_run)
        outbid = (not ok) and str(detail_msg).startswith(OUTBID)
        a["status"] = ("DRY_RUN" if dry_run else
                       "BID" if ok else "OUTBID" if outbid else "FAILED")
        a["result"] = detail_msg
        a["price_before"] = before
        changed = True

        # 🚨 Re-read the price on a LOSS too, not just a win. Being outbid
        # without a number is useless: "you lost" and "you lost by $1" call for
        # completely different responses, and the gap is the only evidence that
        # the book's ceiling is set too low.
        after = None
        if not dry_run:
            try:
                time.sleep(2)
                after = detail(lid).get("high_bid")   # may be None - guarded below
            except Exception:
                pass
            if after is not None:
                a["price_after"] = after

        prem = float(a.get("premium") or d.get("premium") or 0)
        icon = ":dart:" if ok else (":raised_hand:" if outbid else ":x:")
        lines = [f"{icon} **HiBid snipe {'(dry run) ' if dry_run else ''}{a['status']}** "
                 f"on {(a.get('title') or '(untitled)')[:60]}",
                 f"bid when it fired: **${before:.2f}**  (needed ${need:.2f})",
                 f"your armed max: **${cap:.2f}** hammer"]
        if after is not None:
            if outbid:
                lines.append(f"it is at **${after:.2f}** now, against your "
                             f"${cap:.2f} max - beaten by "
                             f"${max(0, after - cap):.2f}. Nothing was spent.")
            elif not ok:
                # 🚨 The bid did not land, so we hold nothing. Saying "you are
                # winning" here - which it did - is the worst kind of wrong: it
                # reads as a success on a lot nobody is defending.
                lines.append(f"price now: **${after:.2f}**. Your bid did NOT go "
                             f"through, so you are not in this one.")
            elif after <= cap:
                lines.append(f"price now: **${after:.2f}** - winning, "
                             f"${cap - after:.2f} under your max; "
                             f"**${after * (1 + prem):.2f} all-in** with the "
                             f"{prem * 100:.4g}% premium")
            else:
                lines.append(f"price now: **${after:.2f}** - ABOVE your max, "
                             f"you were outbid")
        if d.get("extended"):
            lines.append("_This auction soft-closed and extended. Your max is a "
                         "standing proxy bid and still stands - no second bid._")
        lines += [detail_msg, a.get("url", "")]
        msg = "\n".join(lines)
        print(msg)
        if not dry_run:
            notify(msg, subject="Flipscout HiBid snipe")
    if changed:
        save_armed(armed)
    # Close the loop on anything that has since finished.
    try:
        report_outcomes(dry_run=dry_run)
    except Exception as e:
        print(f"outcome pass failed (non-fatal): {type(e).__name__}")
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
        # 🚨 Snapshot the bid state. Without this, "no dialog appeared" is
        # ambiguous between "he never clicked" and "it bid instantly" - and on
        # 2026-08-18 the first run recorded the second when the truth was the
        # first, which would have disabled the sniper on a false premise.
        try:
            before = detail(lid)
            b_bids, b_high = before.get("bids"), before.get("high_bid")
        except Exception:
            b_bids = b_high = None

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
            # Did the bid state move? That distinguishes the two cases.
            committed = False
            try:
                after = detail(lid)
                committed = (b_bids is not None
                             and (after.get("bids") or 0) > b_bids)
                # b_high may be None if the page did not state a price; the
                # bid COUNT is the reliable signal either way.
            except Exception:
                pass
            ctx.close()
            if committed:
                print("\n:warning: THE BID WENT STRAIGHT THROUGH - no dialog.")
                print("This account has bid confirmation OFF. The sniper stays "
                      "DISABLED: that button can only bid the site's increment, "
                      "never your max, and it cannot be stopped once clicked.")
                print("Turn bid confirmation ON in your HiBid account, then "
                      "re-run verify.")
                BIDFORM_PATH.write_text(json.dumps(
                    {"confirm_dialog": False,
                     "note": "bid committed instantly - confirmation is OFF"},
                    indent=2), encoding="utf-8")
                return 1
            print("\nNo dialog, and no bid was placed either - so the click "
                  "never landed.")
            print(f"  bids still {b_bids}, high bid still "
                  + (f"${b_high:.2f}" if b_high is not None else "unstated"))
            print("The window may have opened behind your other windows. Nothing "
                  "was recorded and nothing was spent - just run verify again and "
                  "look for the window with the ORANGE BAR across the top.")
            return 2                       # inconclusive, NOT a finding

        # 🚨 Angular stamps STATE onto className - ng-untouched becomes
        # ng-touched the moment anything types into the field, ng-pristine
        # becomes ng-dirty, ng-valid flips to ng-invalid. A selector built from
        # those matches on a fresh dialog and misses on a used one. The first
        # verify run produced exactly that: ".text-lg.ng-untouched".
        _NG = re.compile(r"^ng-")

        def _sel(d):
            if d.get("id"):
                return f"#{d['id']}"
            if d.get("fc"):
                return f"[formcontrolname='{d['fc']}']"
            if d.get("name"):
                return f"input[name='{d['name']}']"
            cls = [c for c in (d.get("cls") or "").split() if not _NG.match(c)]
            return "." + ".".join(cls[:2]) if cls else None

        # Only real, editable inputs are candidates - a label is not a field.
        editable = [i for i in found["inputs"]
                    if (i.get("type") or "text")
                    not in ("hidden", "checkbox", "radio", "submit", "button")]
        amount_like = [i for i in editable
                       if re.search(r"bid|amount|max", str(i), re.I)]
        chosen = (amount_like or editable or [None])[0]
        confirm = next((b for b in found["buttons"]
                        if re.search(r"place|confirm|submit|bid|yes|ok", b["t"], re.I)),
                       None)
        rec = {
            "confirm_dialog": True,
            "max_input": _sel(chosen) if chosen else None,
            # Matched by TEXT, not class: "button.btn" is every button on the
            # page, and clicking the wrong one in a bid dialog is unforgivable.
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
    """Tell Leron whether he WON or LOST, once each auction is over.

    🚨 THIS IS THE ONLY PART THAT CLOSES THE LOOP. Everything else reports at
    BID time - "you are winning, $6.01 under your max" - which is a snapshot
    taken minutes before the close and says nothing about how it ended. Without
    this the last thing he ever hears about a lot is a guess.

    It works off what the sniper itself bid on, so unlike the Bid sentry it
    needs no CSV export and cannot go stale.
    """
    from .notify import notify
    try:
        armed = load_armed()
    except ArmedFileCorrupt:
        return 0                           # run() already shouted about this
    changed = acted = 0
    for lid, a in list(armed.items()):
        if a.get("status") not in ("BID", "OUTBID"):
            continue
        try:
            d = detail(lid)
        except Exception:
            continue
        # Only once it is genuinely over.
        if not (d.get("gone") or d.get("closed")
                or (seconds_left(d) or 1) <= 0):
            continue

        final = d.get("high_bid")
        res = outcome_authed(lid)
        a["status"] = {"won": "WON", "lost": "LOST"}.get(res, "ENDED_UNKNOWN")
        a["final_price"] = final
        changed += 1

        prem = _num(a.get("premium"))
        tax = _num(a.get("tax"))
        cap = _num(a.get("max_bid"))
        if res == "won":
            allin = (final or 0) * (1 + prem) * (1 + tax)
            lines = [f":trophy: **WON** - {(a.get('title') or '(untitled)')[:60]}",
                     f"Hammer **${final:,.2f}** against your ${cap:,.2f} max."
                     if final is not None else f"Your max was ${cap:,.2f}.",
                     f"**${allin:,.2f} all-in** with premium and tax."
                     if final is not None and (prem or tax) else "",
                     "Pay the invoice, then log it with `flipscout bought`.",
                     a.get("url", "")]
        elif res == "lost":
            lines = [f":x: **Lost** - {(a.get('title') or '(untitled)')[:60]}",
                     (f"It went for **${final:,.2f}**; your max was "
                      f"${cap:,.2f} - beaten by ${max(0, (final or 0) - cap):,.2f}."
                      if final is not None else
                      f"Your max was ${cap:,.2f}."),
                     "Nothing was spent.", a.get("url", "")]
        else:
            lines = [f":grey_question: **Ended, outcome unclear** - "
                     f"{(a.get('title') or '(untitled)')[:60]}",
                     f"It closed at ${final:,.2f}. " if final is not None else "",
                     "Check the lot yourself - the site did not say whether "
                     "you took it.", a.get("url", "")]
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
            print("usage: hibidsnipe arm <lot-url-or-id> <max-hammer-bid> "
                  "[--stretch N] [--override]")
            return 2
        stretch = 0.0
        for i, a in enumerate(argv):
            if a == "--stretch" and i + 1 < len(argv):
                stretch = float(argv[i + 1])
            elif a.startswith("--stretch="):
                stretch = float(a.split("=", 1)[1])
        return arm(argv[1], float(argv[2]), override="--override" in argv,
                   stretch=stretch)
    if cmd == "disarm":
        return disarm(argv[1]) if len(argv) > 1 else 2
    if cmd == "run":
        return run(dry_run=dry)
    return show()


if __name__ == "__main__":
    raise SystemExit(main())

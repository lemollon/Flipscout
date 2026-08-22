"""Facebook Marketplace sweep that does NOT need a Claude session.

WHY THIS FILE EXISTS
--------------------
FB Marketplace is login-walled, so it is the one Flipscout source that cannot
run in the hourly GitHub Action alongside the other eight. Until 2026-08-17 it
ran as an "assisted sweep": Claude drove Leron's logged-in Chrome on a
CronCreate schedule. That schedule is session-only - it dies when the Claude
session exits and auto-expires after 7 days.

It died. `fb_sweep_seen.json` was last written 2026-08-15 14:00 and nobody
noticed for two days, because a dead sweep and a quiet sweep look identical:
both post nothing to Discord. That failure mode is the thing this file fixes,
not just the scheduling.

HOW IT AVOIDS BOTH PROBLEMS
---------------------------
* **No Claude.** A plain Python entry point, driven by a Windows scheduled task
  exactly like FlipscoutBidSentry. Survives reboots and closed sessions.
* **No tabs.** Playwright drives its OWN Chrome profile at PROFILE_DIR,
  headless by default. It never touches the browser Leron is using, never
  steals focus, and opens nothing on screen.

🚨 THE ONE MANUAL STEP: the dedicated profile has to be logged in once, by
Leron, by hand:

    python -m flipscout.fbsweep login

That opens a visible window for him to sign in. Claude must never type those
credentials. After that the session cookie persists in PROFILE_DIR and the
scheduled task runs unattended until FB expires it.

🚨 AND WHEN IT EXPIRES, SAY SO LOUDLY. `sweep()` returns a `logged_out` flag
and the runner posts a Discord warning, because the whole reason this was dark
for two days is that silence was ambiguous. A sweep that finds nothing and a
sweep that is broken MUST look different.

Automation note: this reads listings on Leron's own account that he can
already see in a browser. It is his account and his risk - Facebook does throttle
and challenge automated sessions, so keep the cadence low (3x/day) and expect
to re-login periodically.
"""

from __future__ import annotations

import json
import os
import pathlib
import random
import re
import sys
import time
from typing import Optional

from .bidding import advise
from .hunt import scam_shaped as _scam_shaped
from .pricebook import BY_KEY, match

REPO = pathlib.Path(__file__).resolve().parent.parent
PROFILE_DIR = REPO / ".fbprofile"
SEEN_PATH = REPO / "fb_sweep_seen.json"

SEARCH = "https://www.facebook.com/marketplace/{city}/search?query={q}"

# Local pickup, so inbound shipping is ZERO - that is the whole reason FB
# beats the shipped sources on thin margins.
INBOUND = 0.0


# 🚨 READ THESE LAZILY, NEVER AT IMPORT TIME. A Scheduled Task gets no shell
# profile, so the config lives in .env and is not present until
# `load_env_file()` runs inside main(). Module-level `os.environ.get(...)`
# constants are evaluated at import - i.e. BEFORE that - so they would freeze
# the defaults and silently ignore .env. Caught 2026-08-17 on the first real
# task run.
def _city() -> str:
    return os.environ.get("FLIPSCOUT_FB_CITY", "houston")


def _target_profit() -> float:
    try:
        return float(os.environ.get("FLIPSCOUT_TARGET_PROFIT", "20"))
    except ValueError:
        return 20.0

# Skip rules learned on the 7/30-7/31 sweeps, corrected against real cards
# 2026-08-17.
# 🚨 `\brent\b`, not `\brental\b`. Houston's G7X market is mostly RENTALS -
# of the first 8 results for "canon g7x", FOUR were rental listings phrased
# "FOR RENT", "(Rental)" and "Rent Only". The last one slipped a $80 rental
# through as a $910 "profit" on the first live run.
_SKIP = re.compile(
    r"\brent(al|als|ing)?\b|"
    r"\bwanted\b|\bin search of\b|\biso\b",
    re.I)
# 🚨 "Ships to you" is NOT in the title - it appears where the LOCATION goes.
# Checking only the title missed it entirely, and a shipped item breaks the
# inbound=0 assumption that the whole FB edge rests on.
# "Partner listing" is FB's label for dealer/retail inventory rather than a
# neighbour selling a thing - it is priced at retail and generally shipped, so
# the local-pickup maths does not apply.
_NOT_LOCAL = re.compile(r"ships? to you|partner listing", re.I)
# How many times to scroll each search. 8 x ~20 cards is ~160/term, well past
# what Houston carries for these niches; the loop exits early the moment a
# scroll adds nothing, so a thin term costs one extra second, not eight.
SCROLL_PASSES = int(os.environ.get("FLIPSCOUT_FB_SCROLLS", "8"))

# $1 asks are "message me" placeholder bait, not prices.
_MIN_REAL_PRICE = 5.0
# Arc'teryx local search is flooded with "UA Factory Direct" fakes at ~$70.
_FAKE_MAGNET = re.compile(r"arc'?teryx", re.I)


def _terms() -> list:
    """Book terms worth searching locally.

    Deliberately NOT pricebook.search_terms() wholesale: that list is tuned for
    keyword APIs and includes deliberate misspellings that FB's fuzzy search
    turns into noise. These are the phrases a human would type.
    """
    return [
        # cameras
        "canon g7x", "canon ae-1", "olympus stylus", "contax t2",
        "canon powershot", "nikon coolpix", "sony cybershot", "sony handycam",
        # calculators + ipods
        "ti-84 plus ce", "ti-nspire", "ipod classic", "ipod nano",
        # 🚨 test gear / tools REMOVED 2026-08-22 (muted categories) - a
        # Facebook sweep for a category he will not buy is pure cost.
        "littmann",
        # sewing
        "singer featherweight",
        # watches
        "citizen eco-drive", "seiko automatic", "casio g-shock",
        # consoles - the 8/16 platform pack
        "playstation 5", "playstation 4", "xbox series x", "nintendo switch",
        "steam deck", "nintendo 2ds xl", "game boy advance",
    ]


def load_seen() -> set:
    try:
        return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")).get("seen") or [])
    except Exception:
        return set()


def save_seen(seen: set) -> None:
    SEEN_PATH.write_text(json.dumps({"seen": sorted(seen)}), encoding="utf-8")


def _price_of(text: str) -> Optional[float]:
    m = re.search(r"\$\s?([\d,]+)(?:\.(\d{2}))?", text or "")
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 else None


def parse_card(lines: list) -> tuple:
    """(price, title, location) from one marketplace card's text lines.

    Real shapes, captured 2026-08-17:
        ["$350",         "Canon EOS M50 Great condition", "Houston, TX"]
        ["$140", "$220", "Nintendo switch",               "Channelview, TX"]
        ["$150", "$175", "Nintendo Switch OLED",          "Ships to you"]

    🚨 LOCATION IS THE LAST LINE, not part of the title. The first version did
    `" ".join(lines[1:3])` and produced titles like "Rent Only Canon G7X Mark II
    Houston, TX".
    🚨 A DISCOUNTED card carries TWO prices - current first, original
    (strikethrough) second. Take the first and drop the rest, or the "$220"
    ends up in the title and the original price gets treated as the ask.
    """
    lines = [x.strip() for x in (lines or []) if x and x.strip()]
    if not lines:
        return None, "", ""
    location = lines[-1] if len(lines) > 1 else ""
    body = lines[:-1] if len(lines) > 1 else lines
    price_lines = [x for x in body if x.lstrip().startswith("$")]
    title_lines = [x for x in body if not x.lstrip().startswith("$")]
    price = _price_of(price_lines[0]) if price_lines else None
    return price, " ".join(title_lines).strip(), location


def evaluate(title: str, price: float, location: str = "") -> Optional[dict]:
    """Price one listing against the book. None = not a deal (or not in book).

    Local pickup means inbound=0, which is worth more than it sounds: a flat $9
    to ship something to you was quietly killing every thin margin in the book.
    """
    if not title or price is None:
        return None
    if _NOT_LOCAL.search(location) or _NOT_LOCAL.search(title):
        return None
    if _SKIP.search(title) or _FAKE_MAGNET.search(title):
        return None
    if price < _MIN_REAL_PRICE:
        return None
    m = match(title)
    if not m:
        return None
    model = m.model
    a = advise(model.comp, outbound_shipping=model.outbound_shipping,
               target_profit=_target_profit(), inbound_shipping=INBOUND,
               current_price=price, units=m.units)
    if a.max_bid <= 0 or price > a.max_bid:
        return None
    return {
        "title": title, "price": price, "model": model.label,
        "model_key": model.key, "comp": model.comp,
        "comp_sample": model.sample, "comp_measured": model.measured,
        "max_bid": a.max_bid, "net_resale": a.net_resale,
        "profit_at_open": a.profit_at_open, "open_bid": price,
        "units": m.units, "source": "facebook", "listing_type": "fixed",
        "local": True, "nearby": True, "bids": None,
        "warnings": [w for w in (m.dead_also_present or [])],
        # Reuse hunt's shared bait check rather than inventing a second
        # threshold here - board.py and the Discord copy both read this key,
        # and the whole point of it living in one place is that the flag can
        # never diverge between the board's ranking and the alert text.
        "scam_shaped": _scam_shaped(
            {"listing_type": "fixed", "local": True}, a),
    }


def sweep(headless: bool = True, limit_terms: Optional[int] = None,
          verbose: bool = True) -> dict:
    """Search every term, return {'finds': [...], 'logged_out': bool, ...}."""
    from playwright.sync_api import sync_playwright

    terms = _terms()[:limit_terms] if limit_terms else _terms()
    finds, scanned, logged_out = [], 0, False

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for i, term in enumerate(terms):
                url = SEARCH.format(city=_city(), q=term.replace(" ", "%20"))
                try:
                    page.goto(url, timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(2500 + random.randint(0, 1500))
                except Exception as e:
                    if verbose:
                        print(f"  [{term}] nav failed: {type(e).__name__}")
                    continue

                body = (page.inner_text("body") or "")[:4000]
                if re.search(r"log in to facebook|create new account|"
                             r"you must log in", body, re.I):
                    logged_out = True
                    break

                # 🚨 COLLECT ON EVERY SCROLL PASS, never once at the end.
                #
                # Two things are going on and only the first is obvious:
                #   1. The page is infinite-scroll, so without scrolling you get
                #      one screen - a suspiciously uniform ~18 cards for EVERY
                #      term (min 8, max 24 over 30 terms). That is the viewport,
                #      not Houston's inventory.
                #   2. Worse, the list is VIRTUALISED: FB recycles DOM nodes as
                #      you scroll, so cards you have passed are REMOVED.
                #      Measured 2026-08-17 on "nintendo switch", counting after
                #      each scroll: 39, 42, 21, 42, 21, 42 - it oscillates.
                #      Reading once at the end therefore caps you at whatever
                #      happens to be rendered, and can catch a trough where half
                #      the results are gone.
                # So union the ids across passes and keep the best text seen for
                # each. Stop when a full pass adds nothing new.
                seen_cards: dict = {}
                stale = 0
                for _ in range(SCROLL_PASSES):
                    try:
                        cards = page.eval_on_selector_all(
                            'a[href*="/marketplace/item/"]',
                            """els => els.map(e => ({
                                href: e.getAttribute('href') || '',
                                text: (e.innerText || '').trim()
                            }))""")
                    except Exception:
                        break
                    added = 0
                    for c in cards:
                        mid = re.search(r"/marketplace/item/(\d+)", c["href"] or "")
                        if not mid:
                            continue
                        key = mid.group(1)
                        # keep the richest render of a card we have seen
                        if key not in seen_cards or len(c["text"] or "") > len(seen_cards[key]["text"] or ""):
                            if key not in seen_cards:
                                added += 1
                            seen_cards[key] = c
                    stale = stale + 1 if added == 0 else 0
                    if stale >= 2:
                        break              # two dry passes = end of results
                    try:
                        page.mouse.wheel(0, 6000)
                        page.wait_for_timeout(1200 + random.randint(0, 600))
                    except Exception:
                        break

                for c in seen_cards.values():
                    mid = re.search(r"/marketplace/item/(\d+)", c["href"] or "")
                    if not mid:
                        continue
                    price, title, location = parse_card(
                        (c["text"] or "").split("\n"))
                    scanned += 1
                    hit = evaluate(title, price, location)
                    if hit:
                        hit["id"] = mid.group(1)
                        hit["url"] = f"https://www.facebook.com/marketplace/item/{mid.group(1)}/"
                        finds.append(hit)
                if verbose:
                    print(f"  [{i+1}/{len(terms)}] {term:22s} scanned={scanned} finds={len(finds)}")
                time.sleep(1.0 + random.random())
        finally:
            ctx.close()

    # dedupe within the run, best profit wins
    best = {}
    for f in finds:
        cur = best.get(f["id"])
        if not cur or (f["profit_at_open"] or 0) > (cur["profit_at_open"] or 0):
            best[f["id"]] = f
    return {"finds": list(best.values()), "logged_out": logged_out,
            "scanned": scanned, "terms": len(terms)}


def run(headless: bool = True, dry_run: bool = False) -> int:
    from .notify import notify, notify_rich

    res = sweep(headless=headless)

    # 🚨 A broken sweep and a quiet sweep must NOT look the same. This is the
    # exact failure that hid a two-day outage.
    if res["logged_out"]:
        msg = ("**Flipscout FB sweep is LOGGED OUT** - the dedicated Chrome "
               "profile lost its Facebook session and swept nothing. Fix: run "
               "`python -m flipscout.fbsweep login` and sign in once.")
        print(msg)
        if not dry_run:
            notify(msg, subject="Flipscout FB sweep logged out")
        return 2

    seen = load_seen()
    fresh = [f for f in res["finds"] if f["id"] not in seen]
    fresh.sort(key=lambda f: -(f.get("profit_at_open") or 0))

    print(f"[fbsweep] {res['terms']} terms | {res['scanned']} listings scanned | "
          f"{len(res['finds'])} priced | {len(fresh)} new")
    for f in fresh:
        print(f"   ${f['price']:>8.2f} -> ${f['profit_at_open']:>7.2f}  "
              f"[{f['model_key']}] {f['title'][:52]}")

    if dry_run:
        return 0
    if fresh:
        notify_rich(fresh, content=f"**Flipscout - Facebook Marketplace ({_city()})** "
                                   f"- {len(fresh)} new local find(s)")
        seen.update(f["id"] for f in fresh)
        save_seen(seen)
    return 0


def _looks_logged_in(page) -> bool:
    try:
        body = (page.inner_text("body") or "")[:4000]
    except Exception:
        return False
    if re.search(r"log in to facebook|create new account|you must log in", body, re.I):
        return False
    # Marketplace only renders these once authenticated.
    return bool(re.search(r"marketplace|today'?s picks|sell", body, re.I))


def login(timeout_s: int = 300) -> int:
    """Open the dedicated profile VISIBLY so Leron can sign in once.

    🚨 Claude must NEVER type these credentials. This only opens the window;
    the human signs in.

    It POLLS for the logged-in state and closes itself rather than waiting on
    the window-close event. The first version blocked forever on
    `page.wait_for_event("close", timeout=0)`, which hangs any wrapper with a
    timeout (including running this via `!` inside Claude Code) and leaves you
    unsure whether the profile was saved.
    """
    from playwright.sync_api import sync_playwright
    print("Opening a Chrome window on the Flipscout FB profile.")
    print("Sign in to Facebook there. This window is SEPARATE from your normal")
    print("browser and nothing else uses it.")
    print(f"Profile: {PROFILE_DIR}")
    print(f"Waiting up to {timeout_s//60} minutes; it closes itself once you are in.\n")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://www.facebook.com/marketplace/", timeout=60000)
        except Exception as e:
            print(f"could not open Facebook: {type(e).__name__}")
            ctx.close()
            return 1
        ok, waited = False, 0
        while waited < timeout_s:
            if page.is_closed():
                break                      # signed in and closed it manually
            if _looks_logged_in(page):
                ok = True
                break
            time.sleep(3)
            waited += 3
        # let cookies flush to the profile before tearing the context down
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass
        ctx.close()
    if ok:
        print("\nSigned in - session saved.")
    else:
        print("\nDid not detect a signed-in session (timed out or closed early).")
    print("Verify with:  python -m flipscout.fbsweep sweep --dry-run")
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 🚨 FIRST, always. A Scheduled Task gets no shell profile, so without
    # this the webhook is unset and every alert "delivers" to stdout - which
    # is exactly how a LOGGED OUT warning ended up in a log file nobody
    # reads instead of Discord, on the first real task run.
    from .mybids import load_env_file
    load_env_file()
    cmd = argv[0] if argv else "sweep"
    if cmd == "login":
        return login()
    if cmd in ("sweep", "run"):
        return run(headless="--headed" not in argv, dry_run="--dry-run" in argv)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

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

from .analyzer import Thresholds
from .bidding import advise
from .pricebook import BY_KEY, match

REPO = pathlib.Path(__file__).resolve().parent.parent
PROFILE_DIR = REPO / ".fbprofile"
SEEN_PATH = REPO / "fb_sweep_seen.json"

CITY = os.environ.get("FLIPSCOUT_FB_CITY", "houston")
SEARCH = "https://www.facebook.com/marketplace/{city}/search?query={q}"

# Local pickup, so inbound shipping is ZERO - that is the whole reason FB
# beats the shipped sources on thin margins.
INBOUND = 0.0
TARGET_PROFIT = float(os.environ.get("FLIPSCOUT_TARGET_PROFIT", "20"))

# Skip rules learned on the 7/30-7/31 sweeps.
_SKIP = re.compile(
    r"ships? to you|"          # not local, so inbound is no longer 0
    r"\brental\b|\bfor rent\b|"
    r"\bwanted\b|\bisO\b|\bin search of\b",
    re.I)
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
        # test gear / tools
        "fluke multimeter", "mitutoyo", "starrett", "dial indicator",
        "littmann", "milwaukee m18", "dewalt 20v",
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


def evaluate(title: str, price: float) -> Optional[dict]:
    """Price one listing against the book. None = not a deal (or not in book).

    Local pickup means inbound=0, which is worth more than it sounds: a flat $9
    to ship something to you was quietly killing every thin margin in the book.
    """
    if not title or price is None:
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
               target_profit=TARGET_PROFIT, inbound_shipping=INBOUND,
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
                url = SEARCH.format(city=CITY, q=term.replace(" ", "%20"))
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

                # Each result is an anchor to /marketplace/item/<id>/ whose text
                # carries the price and title on separate lines.
                cards = page.eval_on_selector_all(
                    'a[href*="/marketplace/item/"]',
                    """els => els.map(e => ({
                        href: e.getAttribute('href') || '',
                        text: (e.innerText || '').trim()
                    }))""")
                for c in cards:
                    mid = re.search(r"/marketplace/item/(\d+)", c["href"] or "")
                    if not mid:
                        continue
                    lines = [x.strip() for x in (c["text"] or "").split("\n") if x.strip()]
                    if not lines:
                        continue
                    price = _price_of(lines[0])
                    title = " ".join(lines[1:3]) if len(lines) > 1 else ""
                    if price is None:
                        for ln in lines:
                            price = price or _price_of(ln)
                    scanned += 1
                    hit = evaluate(title, price)
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
        notify_rich(fresh, content=f"**Flipscout - Facebook Marketplace ({CITY})** "
                                   f"- {len(fresh)} new local find(s)")
        seen.update(f["id"] for f in fresh)
        save_seen(seen)
    return 0


def login() -> int:
    """Open the dedicated profile VISIBLY so Leron can sign in once.

    Claude must never type these credentials; this only opens the window.
    """
    from playwright.sync_api import sync_playwright
    print("Opening the Flipscout Facebook profile.")
    print("Sign in, then CLOSE the window. The session is saved to")
    print(f"  {PROFILE_DIR}")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.facebook.com/marketplace/", timeout=60000)
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        ctx.close()
    print("Saved. Now test with:  python -m flipscout.fbsweep sweep --dry-run")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "sweep"
    if cmd == "login":
        return login()
    if cmd in ("sweep", "run"):
        return run(headless="--headed" not in argv, dry_run="--dry-run" in argv)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""The constant watcher: sweep every source, price what it finds, alert with numbers.

Pipeline, per run:
    every hunter x every search term
      -> match the title against the price book (model IS the trade)
      -> enrich only the ones that matched (detail calls cost requests)
      -> compute open bid + max bid
      -> drop anything already alerted, and anything with no room
      -> post to Discord with photo, link, and both bid numbers

Config (env):
    FLIPSCOUT_ALERT_WEBHOOK   Discord webhook
    FLIPSCOUT_SOURCES         goodwill,hibid,craigslist (default all three)
    FLIPSCOUT_CL_CITIES       your craigslist metro(s) (default houston)
    FLIPSCOUT_ZIP             your zip - adds HiBid's local pass  (e.g. 77441)
    FLIPSCOUT_RADIUS_MILES    how far you'd drive               (e.g. 150)
    FLIPSCOUT_NELLIS_LOCATION nellis warehouse city             (default houston)
    FLIPSCOUT_ESTATE_AREA     estate-sale digest area  (e.g. TX/Fulshear/77441)
    FLIPSCOUT_GSALR_CITIES    gsalr.com city slugs for the garage digest
                              (default fulshear-tx,katy-tx)
    FLIPSCOUT_TARGET_PROFIT   dollars per flip        (default 20)
    FLIPSCOUT_INBOUND_SHIP    est. shipping to you    (default 9)
    FLIPSCOUT_TOP             max alerts per run      (default 10)
    FLIPSCOUT_MAX_PER_MODEL   max picks per model/run (default 3)
    FLIPSCOUT_STATE_FILE      seen-cache              (default flipscout_seen.json)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional

from .bidding import advise
from .cards import comp_query as cards_comp_query
from .cards import comp_url as cards_comp_url
from .cards import one_liner as card_line, read as read_card
from .hunters import build_hunters
from .auctionfees import REQUIRE_CARD
from .notify import describe_webhook, destination, notify_rich
from .ebay_ui import sold_url
from .pricebook import comp_search, match, search_terms


def load_config(env=None) -> dict:
    env = env if env is not None else os.environ
    return {
        # Default kept IN SYNC with the live FLIPSCOUT_SOURCES repo variable.
        # It had drifted: the default still listed poshmark, outandback and
        # geartrade months after all three were dropped from production on
        # 2026-08-15 (every term they carry is apparel, and apparel is benched
        # in the price book, so they sweep listings that can never price).
        # The drift was not harmless - a local run swept 3 dead sources that
        # production does not, so local and prod disagreed about what the
        # pipeline even does, which is exactly the kind of gap that makes a
        # local reproduction lie to you.
        "sources": [s.strip() for s in env.get(
            "FLIPSCOUT_SOURCES",
            "goodwill,hibid,craigslist,propertyroom,nellis,unclaimedbaggage,gsa,ebay"
        ).split(",") if s.strip()],
        # Where you live. Drives HiBid's local pass and labels alerts as drivable.
        "zip": (env.get("FLIPSCOUT_ZIP") or "").strip(),
        "radius_miles": (env.get("FLIPSCOUT_RADIUS_MILES") or "").strip(),
        # Estate sales are a digest, not an alert - they carry no item prices.
        "estate_area": (env.get("FLIPSCOUT_ESTATE_AREA") or "").strip(),
        # gsalr.com has no zip endpoint, only per-city pages; nearby city slugs
        # for the garage digest. Empty string turns the gsalr feeds off.
        "gsalr_cities": (env.get("FLIPSCOUT_GSALR_CITIES", "fulshear-tx,katy-tx")).strip(),
        "target_profit": float(env.get("FLIPSCOUT_TARGET_PROFIT", "20")),
        "inbound_shipping": float(env.get("FLIPSCOUT_INBOUND_SHIP", "9")),
        "top": int(env.get("FLIPSCOUT_TOP", "10")),
        # A digest of top-N by profit_at_open let a handful of high-volume
        # models (Starrett, Mitutoyo, film cameras, camcorders) monopolize
        # every run while whole categories with measured live supply never
        # surfaced. Cap how many picks any one model label gets per run;
        # the rest stay unseen and queue for the next run.
        "max_per_model": int(env.get("FLIPSCOUT_MAX_PER_MODEL", "3")),
        # 🚨 RANKING ON profit_at_open IS BIASED TOWARDS LOTS NOBODY HAS BID ON.
        # Measured on 505 live lots (2026-08-19): median profit at open is
        # $30.57 for lots closing inside 6h and $69.79 for lots more than 3 days
        # out - not because the distant ones are better, but because their price
        # has not moved yet. So the top 20 by profit contained ZERO lots closing
        # within 6 hours, median 46.9 hours left.
        #
        # That is exactly backwards for a sniper. A lot with days to run will be
        # bid up before it ends and its "profit at open" is fiction; a lot
        # closing in two hours is priced almost final and is the one the bot can
        # actually win. So part of every run is reserved for the closing lane.
        # TWO WINDOWS, because they are two different jobs (Leron, 2026-08-19):
        #   URGENT  - closing within the hour. Arm it NOW or lose it. Rare, so
        #             the slots are few, and unused ones fall through.
        #   CLOSING - closing today. Get it armed while there is still slack.
        # Whatever is left goes to the ordinary profit ranking.
        # 🚨 BUY-IT-NOW PROFIT IS REAL; AUCTION "PROFIT AT OPEN" IS NOT.
        # A fixed price is the price - no bidding war, no proxy sniping you at
        # the buzzer, and no waiting days to find out. An auction's profit at
        # open is measured against a price that has not moved yet and will,
        # which is why lots days out look richest (median $69.79 at >3d vs
        # $30.57 inside 6h). Ranking the two together on the same number
        # flatters the auction every time, so BIN gets its own slots.
        "bin_slots": int(env.get("FLIPSCOUT_BIN_SLOTS", "0")),          # 0 = top/4
        "urgent_hours": float(env.get("FLIPSCOUT_URGENT_HOURS", "1")),
        "urgent_slots": int(env.get("FLIPSCOUT_URGENT_SLOTS", "0")),    # 0 = top/4
        "closing_hours": float(env.get("FLIPSCOUT_CLOSING_HOURS", "12")),
        "closing_slots": int(env.get("FLIPSCOUT_CLOSING_SLOTS", "0")),  # 0 = top/4
        # Optional hard freshness filter, OFF by default on purpose: for auctions
        # "listed recently" is the wrong signal - a lot posted days ago that ends
        # in 30 minutes with no bids is the better buy, because its price is
        # nearly final. Only Goodwill exposes a listing date at all.
        "max_age_hours": (float(env["FLIPSCOUT_MAX_AGE_HOURS"])
                          if env.get("FLIPSCOUT_MAX_AGE_HOURS") else None),
        # Deep-discount gate: only surface items costing under this fraction of
        # their net resale (0.6 = "pay at most 60 cents on the resale dollar").
        # None = off; the live value comes from the repo variable, never a
        # code default (knob fail-safe rule).
        "max_ask_ratio": (float(env["FLIPSCOUT_MAX_ASK_RATIO"])
                          if env.get("FLIPSCOUT_MAX_ASK_RATIO") else None),
        # For-parts/not-working listings are priced against a HAIRCUT comp, not
        # the working-item comp - measured on cameras, untested sells at roughly
        # half of working (SX-70: $40-85 vs $99.99). Ratio of working comp.
        "parts_comp_ratio": float(env.get("FLIPSCOUT_PARTS_COMP_RATIO") or "0.5"),
        # Best Offer listings routinely clear 10-20% under ask, so the
        # deep-discount gate judges them a shade looser: cap x (1 + bonus).
        "best_offer_bonus": float(env.get("FLIPSCOUT_BEST_OFFER_BONUS") or "0.15"),
        # Second alert tier: re-alert once when a qualifying lot ends inside
        # this window with its price still at/under half the ceiling. The buy
        # decision happens in the last hour, not when the lot is first seen.
        "ending_soon_hours": float(env.get("FLIPSCOUT_ENDING_SOON_HOURS", "2")),
        # Quiet is the NORMAL state - most runs find nothing new - but silence
        # reads as breakage. Once a day, say so out loud even with zero finds.
        "heartbeat_file": env.get("FLIPSCOUT_HEARTBEAT_FILE", "flipscout_heartbeat.json"),
        "state_file": env.get("FLIPSCOUT_STATE_FILE", "flipscout_seen.json"),
        # Every qualifying item, published for the web app's deals board.
        "board_file": env.get("FLIPSCOUT_BOARD_FILE", "docs/deals.json"),
    }


# A committed floor for the sent-list. The Actions cache is the primary store,
# but it is not durable: changing the workflow's cache `path` invalidated it once
# and the watcher happily re-sent 50 already-delivered deals. Anything in this
# baseline is never alerted again, whatever the cache does.
BASELINE_SEEN = "flipscout_seen_baseline.json"


def _load_seen(path: str, baseline: str = BASELINE_SEEN) -> set:
    seen: set = set()
    for candidate in (baseline, path):
        try:
            with open(candidate, encoding="utf-8") as f:
                seen |= set(json.load(f))
        except Exception:
            pass
    return seen


def _save_seen(path: str, seen: set) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(seen)[-5000:], f)
    except Exception as e:
        print(f"[hunt] couldn't save seen-cache: {e}")


def _due_for_heartbeat(path: str, today: Optional[str] = None,
                       key: str = "last") -> bool:
    """True at most once per calendar day, per key."""
    today = today or _dt.date.today().isoformat()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get(key) != today
    except Exception:
        return True


def _mark_heartbeat(path: str, today: Optional[str] = None,
                    key: str = "last") -> None:
    state = {}
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f) or {}
    except Exception:
        pass          # first run, or a corrupt file we're about to replace
    state[key] = today or _dt.date.today().isoformat()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[hunt] couldn't save heartbeat: {e}")


def post_estate_digest(config: dict, notifier, feed=None) -> bool:
    """Once a day, list the estate sales and online estate auctions near you.

    Kept separate from the deal alerts because these carry NO item prices (see
    estates.py) - it's a "go look at these" list, not a max bid. Returns True
    when something was posted."""
    area = config.get("estate_area")
    if not area or not _due_for_heartbeat(config["heartbeat_file"], key="estates"):
        return False
    from .estates import EstateSalesNet, digest
    feed = feed if feed is not None else EstateSalesNet(area=area)
    sales = feed.sales()
    if not sales:
        return False
    body = digest(sales, area_label=area.split("/")[1] if "/" in area else area)
    notifier([], content=body)
    _mark_heartbeat(config["heartbeat_file"], key="estates")
    print(f"[hunt] estate digest: {len(sales)} sale(s) near {area}.")
    return True


def post_garage_digest(config: dict, notifier, feed=None) -> bool:
    """Once a day: the garage/yard sales worth a drive. Like the estate digest,
    these carry no item prices, so it's a go-look list, never a max bid.
    Keyed off FLIPSCOUT_ZIP - no zip, no digest."""
    zip_code = config.get("zip")
    if not zip_code or not _due_for_heartbeat(config["heartbeat_file"], key="garage"):
        return False
    from .garagesales import (YardSaleSearch, Gsalr, GarageSaleFinder,
                              merged_sales, digest, expand_desc,
                              split_for_discord)
    if feed is not None:
        feeds = [feed]
        expand = None
    else:
        feeds = [YardSaleSearch(zip_code), GarageSaleFinder(zip_code)]
        feeds += [Gsalr(c.strip()) for c in
                  (config.get("gsalr_cities") or "").split(",") if c.strip()]
        expand = expand_desc
    sales = merged_sales(feeds)
    if not sales:
        return False
    # Leron, 7/31: a list of titles+links is useless - the digest must show
    # WHAT each sale has. Descriptions push past Discord's 2000-char cap, so
    # the digest goes out in sale-boundary parts instead of being truncated.
    for part in split_for_discord(digest(sales, zip_code, expand=expand)):
        notifier([], content=part)
    _mark_heartbeat(config["heartbeat_file"], key="garage")
    print(f"[hunt] garage digest: {len(sales)} sale(s) near {zip_code} "
          f"from {len(feeds)} feed(s).")
    return True


def sweep(config: dict, hunters=None) -> list[dict]:
    """Every source x every term -> deduped raw listings."""
    hunters = hunters if hunters is not None else build_hunters(config["sources"])
    found: dict[str, dict] = {}
    all_terms = search_terms()
    for h in hunters:
        # A hunter may only be able to serve some of the watchlist (Poshmark has
        # no test equipment, and its pages are 5MB each).
        terms = h.relevant_terms(all_terms) if hasattr(h, "relevant_terms") else all_terms
        for term in terms:
            for row in h.search(term):
                key = f"{row['source']}:{row['id']}"
                if key not in found:
                    found[key] = row
    return list(found.values())


# A fixed-price ask this far under resale on a LOCAL source is bait, not a
# deal: real Craigslist underprices vanish in minutes, and the ones that sit
# are scams. The board's #1 "find" - a $50 G7X Mark II vs a $1,149 comp - was
# one (Leron confirmed, 2026-07-28). Auction opens near $0 are normal; fixed
# asks near $0 are not.
SCAM_ASK_SHARE = 0.15


def scam_shaped(row: dict, adv) -> bool:
    """True when this fixed-price LOCAL ask is bait, not a deal.

    Computed once here, in `evaluate()`, and carried on the candidate dict as
    `c["scam_shaped"]` - so the flag can never diverge between the board's
    ranking and the Discord alert copy the way it used to. The bug: this used
    to be recomputed from scratch inside `to_alert()` only, which is called
    for Discord alerts alone. `evaluate()`'s output feeds `board.write()`
    directly with no idea a row was scam-shaped, so a $50 Craigslist "find"
    against a $1,149 comp ranked #1 on the board by raw profit_at_open even
    though the SAME candidate would have shown a SCAM-SHAPED banner had it
    ever reached a Discord alert.
    """
    return bool(row.get("listing_type") == "fixed" and row.get("local")
                and (adv.open_bid or 0) > 0
                and adv.open_bid < SCAM_ASK_SHARE * adv.net_resale)


def scam_warning(adv) -> str:
    """The loud banner text - shared verbatim between the Discord alert and
    the board's per-item warnings, so the wording can't drift between them."""
    return (f":triangular_flag_on_post: **SCAM-SHAPED PRICE** - a fixed ask of "
            f"${adv.open_bid:,.2f} on an item that nets ${adv.net_resale:,.2f} "
            f"is bait more often than a deal (the $50 G7X lesson). Real "
            f"underprices sell in minutes; the ones that SIT are the trap. "
            f"Verify in person before believing it.")


def evaluate(rows: list, config: dict, hunters=None) -> list[dict]:
    """Price each listing that matches a model in the book."""
    by_name = {h.name: h for h in (hunters if hunters is not None
                                   else build_hunters(config["sources"]))}
    out = []
    dropped_no_card: list[str] = []
    for row in rows:
        m = match(row.get("title", ""))
        if not m:
            continue

        # Enrich for real handling/photos before quoting a number.
        h = by_name.get(row["source"])
        if h is not None and hasattr(h, "enrich") and row.get("handling") is None:
            try:
                row = h.enrich(row)
            except Exception:
                pass

        # Local pickup means you collect it yourself, so there is no inbound
        # shipping to subtract. Worth stating plainly: that flat ~$9 is what makes
        # thin-margin categories unprofitable, so the same item is worth ~$9 more
        # to you on Craigslist than in a shipped auction.
        inbound = 0.0 if row.get("local") else config["inbound_shipping"]

        # 🚨 A lot he cannot pay for is not a deal. Cash/wire-only houses are
        # dropped outright rather than alerted with a warning: a warning on an
        # unbuyable lot is just noise, and a wire carries no chargeback if the
        # goods are wrong. Counted and reported below - never silently.
        if REQUIRE_CARD and row.get("card_ok") is False:
            dropped_no_card.append(row.get("title", "")[:50])
            continue

        # A for-parts listing must never be bid at working-item comps - haircut
        # the comp before advising, so max_bid/net_resale all scale with it.
        comp = m.model.comp
        cond = (row.get("condition") or "").lower()
        is_parts = "parts" in cond or "not working" in cond
        if is_parts:
            comp *= config.get("parts_comp_ratio", 0.5)

        adv = advise(
            comp,
            units=m.units,
            handling=float(row.get("handling") or 0),
            inbound_shipping=inbound,
            outbound_shipping=m.model.outbound_shipping,
            target_profit=config["target_profit"],
            current_price=row.get("price"),
            min_bid=row.get("min_bid"),
            increment=float(row.get("increment") or 1.0),
            bid_count=int(row.get("bids") or 0),
            # Auction houses charge a premium ON TOP of the hammer. Sources
            # that have none (ShopGoodwill, fixed-price listings) leave this
            # absent and it stays 0.
            buyer_premium_rate=float(row.get("buyer_premium_rate") or 0.0),
            # Charged on hammer + premium at checkout. Zero where a source has
            # none, and zero throughout if FLIPSCOUT_RESALE_EXEMPT is set.
            sales_tax_rate=float(row.get("sales_tax_rate") or 0.0),
        )
        if not adv.has_room:
            continue

        # DEEP-DISCOUNT GATE (Leron, 2026-07-29): only surface items whose
        # entry price is well under what they net on resale. has_room alone
        # lets a $500 open on a $560-net item through with a technical $20 of
        # headroom - retail-priced, not underpriced. Ratio of open cost to net
        # resale must stay below the knob (repo var FLIPSCOUT_MAX_ASK_RATIO,
        # e.g. 0.6 = "only alert when it costs under 60% of what it nets").
        # None/unset = gate off, so an unconfigured install behaves as before.
        ratio_cap = config.get("max_ask_ratio")
        if ratio_cap is not None and adv.net_resale:
            entry = (adv.open_bid or 0)
            # Best Offer: the ask isn't the floor - offers clear 10-20% under it,
            # so near-misses on the ratio gate are still buyable via an offer.
            cap = (ratio_cap * (1 + config.get("best_offer_bonus", 0.15))
                   if row.get("best_offer") else ratio_cap)
            if entry > 0 and entry / adv.net_resale > cap:
                continue

        max_age = config.get("max_age_hours")
        if max_age is not None:
            age = age_hours(row.get("listed"))
            # Unknown age is NOT treated as stale - Craigslist and HiBid never
            # report one, and silently dropping two of three sources would look
            # like the watcher had died.
            if age is not None and age > max_age:
                continue

        is_scam = scam_shaped(row, adv)
        out.append({"row": row, "model": m.model, "match": m, "advice": adv,
                    "scam_shaped": is_scam,
                    "scam_warning": scam_warning(adv) if is_scam else None})

    # Best headroom first: what you'd clear if you won at the current minimum.
    # SCAM-SHAPED asks sort AFTER every legit candidate regardless of their
    # (often huge, and fake) profit number - `not c["scam_shaped"]` puts the
    # legit ones (True) ahead of the bait (False) under reverse=True, then
    # each group is still ranked best-profit-first within itself.
    out.sort(key=lambda c: (not c["scam_shaped"], c["advice"].profit_at_open or 0),
              reverse=True)
    # 🚨 NO SILENT CAPS. A lot dropped for being unpayable has to be counted out
    # loud, or "no deals today" and "three deals you cannot pay for" look
    # identical from the outside.
    if dropped_no_card:
        print(f"[hunt] dropped {len(dropped_no_card)} lot(s) from cash/wire-only "
              f"houses (you pay by card): "
              + "; ".join(dropped_no_card[:3])
              + (" ..." if len(dropped_no_card) > 3 else ""))
    return out


# --- the card scout ---------------------------------------------------------
# 🚨 THE ONE PLACE THIS REPO ALERTS WITHOUT A COMP, AND WHY IT IS ALLOWED.
#
# Every other alert in Flipscout carries two numbers behind a MEASURED comp,
# and `evaluate()` drops anything that matches no model precisely so an
# unpriced guess can never reach a bid. That rule is not being relaxed here:
# a scout card carries NO comp, NO ceiling and NO max bid, and it is posted to
# a different channel from the priced alerts so the two can never be confused.
#
# What justifies it is that cards are the one category where the title CANNOT
# be priced and yet CAN be triaged. The book measured this directly (see
# DEAD_MODELS): an unsorted card lot ran p25 $10.72 / median $25.18 / max
# $1,061 on n=65 - a hundred-fold spread no comp can straddle. But the shop's
# five rules read straight off the same title and say, correctly, which of
# those thousand listings is the $1,061 one.
#
# So the scout answers a different question from the rest of the file. Not
# "what do I bid" - "which of these do I open". Leron, 2026-08-22: "I'm
# expecting more cards to come out in the channel don't be shy."
#
# 🚨 IT MUST NEVER SAY A NUMBER. Not a comp, not a ceiling, not an estimate.
# The value assessment it DOES carry is the eBay SOLD search for that exact
# card (cards.comp_url) - the real prices, one tap away, aimed by the identity
# the title states. That is the same promise the priced alerts make, minus the
# claim we have not earned.
# Categories he does not want alerted, however well they price. Leron,
# 2026-08-22: "let's remove tools i dont want to snipe those".
#
# 🚨 MUTED, NOT BENCHED. Benching them in `pricebook` (active=False) was the
# first attempt and it took 51 tests down with it - `match()` returning None
# means every measured include/exclude for those models stops being exercised.
# The comps are real and cost a browser session each; the preference is a
# preference. So the book still prices them and `flipscout item` still works -
# they simply never become an alert, a board row or a scout card.
#
# Chosen as all three categories rather than the one literally called "tools":
# metrology (Mitutoyo, Starrett, dial indicators) was 96 of 526 board items to
# tools' 18, and is what his armed HiBid lots were actually full of.
MUTED_CATEGORIES = {c.strip().lower() for c in
                    (os.environ.get("FLIPSCOUT_MUTE_CATEGORIES")
                     or "tools,metrology,test-gear").split(",") if c.strip()}


def _muted(c: dict) -> bool:
    """Is this candidate in a category he asked not to see?"""
    return (getattr(c.get("model"), "category", "") or "").lower() in MUTED_CATEGORIES


SCOUT_VERDICTS = ("CHASE", "LOOK")


def scout_cards(rows: list, config: dict, seen: Optional[set] = None,
                limit: int = 12) -> list[dict]:
    """Cards worth OPENING, from listings no model could price.

    Deliberately runs on the rows `evaluate()` threw away. A listing that DID
    price is already alerting with real numbers and must not be posted twice.
    """
    seen = seen or set()
    out = []
    for row in rows:
        title = row.get("title") or ""
        if match(title):                      # priced elsewhere, with numbers
            continue
        key = f"{row.get('source')}:{row.get('id')}"
        if key in seen:
            continue
        r = read_card(title)
        if r.verdict not in SCOUT_VERDICTS:
            continue
        out.append({"row": row, "read": r, "key": key})
    # Best first, so a cap cuts the weakest rather than an arbitrary tail.
    out.sort(key=lambda c: c["read"].score, reverse=True)
    if len(out) > limit:
        # 🚨 SAY WHAT WAS DROPPED. A silent cap reads as "that was everything",
        # which is how a quiet channel gets mistaken for a quiet market.
        print(f"[scout] {len(out)} card finds, posting top {limit}")
    return out[:limit]


# --- live market numbers for a card the book cannot price -------------------
# 🚨 THE SCOUT SHIPPED WITH A LINK AND NO NUMBER, AND THAT WAS HALF AN ANSWER.
# Leron, 2026-08-22: "You are missing the comps on the cards - you need to find
# them." He is right. A verdict plus a search URL asks him to do the lookup
# himself on every card, which is exactly the work the tool exists to remove.
#
# eBay's Browse API is APPROVED and LIVE on this app and the keys are already in
# the Actions secrets, so the numbers CAN be fetched - just not the ones the
# book normally uses:
#
#   Marketplace Insights (SOLD prices)  - a Limited Release API, closed to new
#       users. _insights() is implemented and returns (None, []) on 403/404, so
#       the day access is granted this starts reporting real solds with nothing
#       to change here.
#   Browse (ACTIVE asks)                - live today.
#
# 🚨 AN ASK IS NOT A SALE, AND THIS MUST NEVER PRETEND OTHERWISE. Asks skew
# high (everything unsold is still listed at its optimistic price) and a card
# nobody buys can carry a hundred asks. So this NEVER produces a comp, never a
# ceiling, never a max bid - `source` is printed on the card so the difference
# is on the alert rather than in a docstring, and the sold-search link stays
# there for him to check in one tap.
#
# QUOTA: bounded by the scout cap, so at most 12 lookups per run - ~288/day
# against a budget where the sixteen card TERMS were costing ~1,150. That is
# the trade this makes: stop spending quota searching eBay for cards, spend a
# fraction of it pricing the ones the auction sources already found.
def price_scout_finds(finds: list, comps=None) -> None:
    """Attach live eBay market numbers to each scout find, in place. Fail-soft."""
    if not finds:
        return

    # 🚨 POKEMON DOES NOT GO THROUGH eBAY, AND MUST NOT WAIT ON ITS KEYS.
    # Browse returns ASKING prices for whatever words a title happens to share
    # with other listings, which is where "the range is nice but its wide for
    # every card" came from. `pokemontcg` returns TCGplayer's market price for
    # THE card - one number per printing, or an explicit range when the title
    # did not pin which card it is.
    #
    # 🚨 PRICED FIRST, BEFORE THE eBAY PROVIDER IS EVEN BUILT. The first cut of
    # this put the Pokemon branch inside the loop below, which sits behind an
    # early `return` when no eBay credentials are configured - so on a machine
    # without eBay keys (every local run) every Pokemon card shipped saying
    # "no market price came back" while the source it actually uses was fine.
    tcg = [c for c in finds if (c["read"].family or "") == "tcg"]
    rest = [c for c in finds if c not in tcg]
    poke_ok = 0
    for c in tcg:
        try:
            from .pokemontcg import read as read_poke
            pv = read_poke(c["row"].get("title") or "")
            c["poke"] = pv
            poke_ok += bool(pv.comp is not None)
        except Exception as e:
            print(f"[scout] pokemon price lookup failed ({type(e).__name__}) "
                  f"- card ships with its link only.")
    if tcg:
        print(f"[scout] priced {poke_ok}/{len(tcg)} pokemon card(s) against "
              f"TCGplayer market")

    # 🚨 BEFORE ANY EARLY RETURN. A Pokemon-only batch leaves `rest` empty, and
    # the first cut called this after that check - so the sold history never
    # ran for exactly the cards whose grade it validates best.
    if not rest:
        _attach_recent_sales(finds)
        return

    # 🚨 SPORTS CARDS ARE PRICED BY THE CARD TOO, FOR THE SAME REASON.
    # Leron: "we need a source outside of ebay for the sports cards too". eBay
    # Browse answers a keyword with a population of ASKING prices;
    # SportsCardsPro is addressed by player + set + parallel + grade. It cannot
    # always pin the card - a HiBid title rarely states the year or the card
    # number - but naming the candidate set and its price spread beats a keyword
    # range, and when the spread is decisive it needs no identity at all
    # (sportscards.verdict).
    #
    # 🚨 GATED ON THE TOKEN, AND DELIBERATELY SO. Without one the source can
    # still NAME the card but not value it, and a line that says "this is one
    # of 4 Josh Donaldson refractors, price unknown" changes no decision while
    # costing a network call per card on every run. No token -> this whole
    # branch is inert and the eBay fallback behaves exactly as it did before,
    # which is also what keeps `price_scout_finds` offline under test.
    from .sportscards import token as _sc_token
    if _sc_token():
        sports_ok = 0
        for c in rest:
            try:
                from .sportscards import read as read_sports
                row = c["row"]
                sv = read_sports(row.get("title") or "", ask=row.get("price"))
                if sv.comp is not None and sv.comp.candidates:
                    c["sports"] = sv
                    sports_ok += 1
            except Exception as e:
                print(f"[scout] sports lookup failed ({type(e).__name__}) - "
                      f"cards ship with their link only.")
                break                      # one outage, not one line per card
        print(f"[scout] priced {sports_ok}/{len(rest)} sports card(s) against "
              f"SportsCardsPro")
        # eBay is now only the fallback for cards the card source could not
        # name at all.
        rest = [c for c in rest if "sports" not in c]
    _attach_recent_sales(finds)
    if not rest:
        return
    if comps is None:
        try:
            from .ebay_api import EbayApiComps
            comps = EbayApiComps()
        except Exception as e:            # no keys configured - say so once
            print(f"[scout] no eBay fallback ({type(e).__name__}); unnamed "
                  f"cards ship with their sold-search link only.")
            return
    ok = 0
    for c in rest:
        q = cards_comp_query(c["read"])
        if not q:
            continue
        try:
            c["market"] = comps.lookup(q)
            ok += 1
        except Exception as e:
            # 🚨 A RATE LIMIT IS NOT A PER-CARD FAILURE, SO STOP ASKING.
            # Observed 2026-08-22: with the Browse allowance already spent,
            # all twelve lookups 429'd and printed twelve near-identical
            # lines, which reads like twelve unlucky cards instead of one
            # exhausted budget. Each retry also spends a call that the next
            # run could have used. One line, then stop.
            if _is_rate_limited(e):
                print(f"[scout] eBay rate-limited ({_status_of(e)}) - the daily "
                      f"Browse allowance is spent, so {len(finds) - ok} card(s) "
                      f"ship with their sold-search link and no numbers. Not a "
                      f"per-card failure; see hunters.EbayBrowse on the quota.")
                break
            # Anything else IS per-card - a weird title, a transient blip -
            # and must not cost the other eleven their numbers.
            print(f"[scout] lookup failed for {q[:40]!r}: {type(e).__name__}")
    if ok:
        print(f"[scout] priced {ok}/{len(finds)} find(s) against live eBay data")


def _status_of(exc) -> str:
    """The HTTP status behind an exception, or its type name."""
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return f"HTTP {code}" if code else type(exc).__name__


def _is_rate_limited(exc) -> bool:
    """Is this the shared quota being exhausted rather than one bad query?

    429 is the documented signal; 403 is included because eBay also returns it
    once an application is over its allowance, and both mean the same thing to
    a caller: stop asking, the answer will not change this run.
    """
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None) in (429, 403)


def _market_line(m) -> str:
    """One line of real numbers, honest about which kind they are."""
    if m is None:
        return ""
    sold, asks = m.sold_price, m.active_count
    if sold is not None:
        return (f":moneybag: **SOLD median ${sold:,.2f}**"
                + (f" across {m.sold_count} sale(s)" if m.sold_count else "")
                + (f", range ${m.low:,.2f}-${m.high:,.2f}" if m.low and m.high else "")
                + " - measured from eBay's sold data.")
    if asks:
        rng = (f" (${m.low:,.2f}-${m.high:,.2f})" if m.low and m.high else "")
        return (f":chart_with_upwards_trend: **{asks} listed on eBay right now**"
                f"{rng}. 🚨 Those are ASKING prices, not sales - unsold cards "
                f"stay listed at optimistic numbers, so treat this as the "
                f"ceiling of opinion, not the floor of value. **Open the sold "
                f"search below before you bid.**")
    return (":grey_question: Nothing comparable listed on eBay right now - "
            "either it is genuinely scarce or the title does not match how "
            "sellers write it. Check the sold search.")



def _attach_recent_sales(finds: list) -> None:
    """What each card ACTUALLY last sold for, at its own grade.

    🚨 ONLY FOR CARDS PINNED TO ONE PRODUCT, and only for the dozen actually
    posted - it is one page fetch each. An ambiguous match has no single
    product page, so there is nothing honest to fetch.
    """
    from .sportscards import recent_sales, token as _tok
    if not _tok():
        return
    got = 0
    for c in finds:
        # 🚨 POKEMON HAS A PRICECHARTING PRODUCT TOO, and the first cut skipped
        # every one of them: `_comp_source_url` hands back a TCGplayer SEARCH
        # for Pokemon (that is the right link to show), which is not a page
        # with sold history on it. The graded lookup already resolved the
        # actual product - use that.
        pv = c.get("poke")
        g = getattr(pv, "graded", None)
        url = getattr(g, "url", None) or _comp_source_url(c)
        if not url or ("sportscardspro" not in url and "pricecharting" not in url):
            continue
        grade = (getattr(c.get("sports"), "comp", None) or
                 getattr(pv, "ident", None))
        grade = getattr(grade, "grade", None)
        try:
            sales = recent_sales(url, grade, limit=3)
        except Exception:
            sales = []
        if sales:
            c["sales"] = sales
            got += 1
    if got:
        print(f"[scout] pulled real sold history for {got} card(s)")


def _comp_source_url(c: dict) -> str:
    """The page behind the number this card quotes, or "".

    🚨 ONLY WHEN THE CARD IS PINNED TO ONE PRODUCT. On an ambiguous match the
    card quotes a RANGE across several products, and linking one of them would
    put a single price behind a sentence that deliberately refused to name one.
    """
    sv = c.get("sports")
    comp = getattr(sv, "comp", None)
    if comp is not None and comp.priced and comp.pinned and comp.candidates:
        return comp.candidates[0].url
    pv = c.get("poke")
    pc = getattr(pv, "comp", None)
    if pc is not None and not pc.ambiguous:
        return pc.url
    return ""


def _money_line(c: dict, ask) -> str:
    """The one line that decides the card: what it is, what it is worth."""
    sv, pv = c.get("sports"), c.get("poke")
    scomp = getattr(sv, "comp", None)
    pcomp = getattr(pv, "comp", None)
    bid = f" · bid ${float(ask):,.2f}" if ask is not None else ""

    if scomp is not None and scomp.priced and scomp.low is not None:
        if scomp.pinned:
            c0 = scomp.candidates[0]
            # 🚨 NAME THE SET TOO. "Hobby Box — $65.00" says nothing; the set
            # is what tells you which hobby box it is.
            who = f"{c0.name} · {c0.set_name}"
            worth = f"${scomp.low:,.2f}"
        else:
            who = f"{scomp.player_and_set} · 1 of {scomp.n}"
            worth = scomp.range_text
        grade = f" {scomp.grade}" if scomp.grade else ""
        return f"**{who}**{grade} — **{worth}**{bid}"

    # 🚨 THE GRADED PRICE WINS OVER THE RAW ONE. It is the comp for the object
    # actually being sold: a PSA 7 Alakazam is $84.13, not the $69.45 the loose
    # card fetches. The first cut of this line showed the raw figure on a slab.
    g = getattr(pv, "graded", None)
    if g is not None and getattr(g, "price", None):
        grade = f" {pv.ident.grade}" if getattr(pv, "ident", None) and pv.ident.grade else ""
        return f"**{g.name} · {g.set_name}**{grade} — **${g.price:,.2f}**{bid}"

    if pcomp is not None:
        who = f"{pcomp.name} #{pcomp.number} · {pcomp.set_name} {pcomp.released[:4]}"
        worth = (pcomp.range_text if pcomp.ambiguous
                 else f"${pcomp.market:,.2f}")
        return f"**{who}** — **{worth}**{bid}"

    if scomp is not None and scomp.candidates:
        return f"**{scomp.player_and_set}** · 1 of {scomp.n}{bid}"

    # 🚨 LAST RESORT, AND LABELLED AS ASKS. Neither card source could price it,
    # so all that is left is what eBay has LISTED - and asks skew high because
    # everything unsold is still sitting there at its optimistic number. It is
    # one line and it says which kind of number it is; reporting an ask as a
    # sale is the one thing this line may never do.
    m = c.get("market")
    if m is not None:
        if getattr(m, "sold_price", None):
            n = f" ({m.sold_count} sold)" if getattr(m, "sold_count", None) else ""
            return f"**SOLD median ${m.sold_price:,.2f}**{n}{bid}"
        if getattr(m, "active_count", None):
            rng = (f" ${m.low:,.0f}-${m.high:,.0f}"
                   if getattr(m, "low", None) and getattr(m, "high", None) else "")
            return f"_{m.active_count} listed on eBay{rng} — asks, not sales_{bid}"
    return f"_no comp_{bid}" if bid else ""


def _worth_of(c: dict):
    """The single figure the card is quoting, for the ratio. None if it quotes
    a range - a multiple off one end of a range is not a fact."""
    sv, pv = c.get("sports"), c.get("poke")
    sc = getattr(sv, "comp", None)
    if sc is not None and sc.priced and sc.pinned and sc.low is not None:
        return sc.low
    g = getattr(pv, "graded", None)
    if g is not None and getattr(g, "price", None):
        return g.price
    pc = getattr(pv, "comp", None)
    if pc is not None and not pc.ambiguous:
        return pc.market
    return None


def to_scout_alert(c: dict) -> dict:
    """One scout find -> the Discord embed payload.

    🚨 FOUR LINES, NOT TWELVE. Leron, 2026-08-22: "the cards need to be concise
    i dont want to read all the on each card". He is right, and at twelve cards
    a run it is not a style question: a card nobody finishes reading is a card
    that does not get acted on, and the caveats were being skipped along with
    everything else.

    What survives is what changes a decision:
        1. the verdict, and how much it clears by
        2. WHICH card this is and what it is worth
        3. anything that would stop the buy - no photo, pickup only, thin market
        4. the links

    What went: the card-shop signal bullets (they justify the verdict, they do
    not change it), the "asking vs current bid" sentence (the AUCTION / BUY IT
    NOW banner above already says it), and every caveat that printed on all
    twelve cards.
    """
    row, r = c["row"], c["read"]
    comps = cards_comp_url(r)
    pv0 = c.get("poke") or c.get("sports")
    verdict = getattr(pv0, "verdict", None) or r.verdict
    if verdict == "UNKNOWN":
        verdict = r.verdict

    ask = row.get("price")
    bits = []
    money = _money_line(c, ask)
    if money:
        bits.append(money)

    # The verdict, and the ONE clause that earns it. `why` is a full paragraph
    # from the price source; the first sentence is the finding and the rest is
    # its reasoning, which belongs in the source not on the card.
    why = (getattr(pv0, "why", "") or "").strip()
    head = {"CHASE": ":dart: **CHASE**", "PASS": ":no_entry: **PASS**",
            "LOOK": ":eyes: **LOOK**"}.get(verdict, f"**{verdict}**")
    clause = ""
    for marker in ("Even the cheapest", "Even the dearest", "The grade is",
                   "The grade decides", "A common", "Code card", "A lot or"):
        if marker in why:
            frag = why[why.index(marker):]
            clause = frag.split(". ")[0].rstrip(".")
            # 🚨 CUTTING A SENTENCE OUT OF MARKDOWN CAN TAKE HALF A **BOLD**
            # WITH IT, and Discord then renders the stray asterisks literally.
            # Seen on the very first concise card: "...the $12.00 bid**,".
            if clause.count("**") % 2:
                clause = clause.replace("**", "")
            break
    # 🚨 NO CLAUSE? SAY THE MULTIPLE. "That is a comp for the slab you are
    # actually bidding on" is filler - the two numbers are already on the line
    # above. What is NOT on that line is the ratio between them, which is the
    # whole decision.
    if not clause:
        worth = _worth_of(c)
        if worth and ask:
            try:
                mult = worth / float(ask)
                clause = (f"{mult:.1f}x the bid" if mult >= 1
                          else f"only {mult:.2f}x the bid")
            except (TypeError, ZeroDivisionError):
                pass
    bits.append(head + (f" — {clause}." if clause else "."))

    # 🚨 WHAT IT ACTUALLY SOLD FOR BEATS WHAT IT IS "WORTH". A guide price is
    # a model; a completed sale is a fact, and it carries a date - which is the
    # only thing that shows a thin market for what it is.
    sales = c.get("sales") or []
    if sales:
        s0 = sales[0]
        others = (" · " + ", ".join(f"${x.price:,.2f}" for x in sales[1:])
                  if len(sales) > 1 else "")
        tag = f" ({s0.grade})" if s0.exact else f" ({s0.grade} — nearest grade)"
        bits.append(f"last sold **${s0.price:,.2f}** {s0.date}{tag}{others}")

    # Only what would stop the buy.
    if not row.get("image"):
        bits.append(":warning: **No photo.**")
    if row.get("pickup_risk"):
        bits.append(":warning: **Pickup only.**")
    if "Thin" in why:
        bits.append(":warning: **Thin market.**")
    where = ", ".join(x for x in (row.get("city"), row.get("state")) if x)
    if row.get("house"):
        bits.append(f"_{row['house']}" + (f" · {where}_" if where else "_"))

    return {
        "title": (row.get("title") or "")[:240],
        "url": row.get("url"),
        "image": row.get("image"),
        "verdict": "watch",
        "source": row.get("source"),
        "buy_url": row.get("url"),
        "comps_url": comps,
        # Carried so build_embed can print the bidding/buying banner. A scout
        # card has no ceiling, which makes the question MORE pressing here, not
        # less: there is no second number to reveal what kind of listing it is.
        "listing_type": row.get("listing_type", "auction"),
        # The page the quoted price came from - SportsCardsPro's product page
        # or TCGplayer's, whichever priced it. "" when nothing did.
        "comp_source_url": _comp_source_url(c),
        # Routes to the cards channel either way (notify.CARD_CATEGORIES holds
        # both); named honestly so the log's category mix does not report every
        # Pokemon card as a sports card.
        "category": ("pokemon-cards" if (c.get("read").family or "") == "tcg"
                     else "sports-cards"),
        "reason": "\n".join(bits),
    }


def age_hours(listed: Optional[str], now: Optional[_dt.datetime] = None) -> Optional[float]:
    """Hours since the listing went up, or None when the source doesn't say."""
    if not listed:
        return None
    try:
        t = _dt.datetime.fromisoformat(str(listed)[:19])
    except ValueError:
        return None
    now = now or _dt.datetime.now()
    return max(0.0, (now - t).total_seconds() / 3600.0)


def to_alert(c: dict) -> dict:
    """One evaluated candidate -> the Discord embed payload."""
    row, model, adv, m = c["row"], c["model"], c["advice"], c["match"]
    units = f" x{adv.units}" if adv.units > 1 else ""
    # Read the flag `evaluate()` already computed - never recompute it here,
    # that duplication is exactly how the board and the alert copy drifted.
    is_scam = c.get("scam_shaped", False)

    # Both links, every time: where to BUY it, and the eBay solds that back the
    # "sells for more" claim. The comp link reproduces the exact search that
    # produced the number, so the claim is checkable in one click.
    comps_link = sold_url(comp_search(model), used_only=model.comp_used_only)

    bits = [f"**{model.label}{units}** - [comps ${model.comp:,.2f} on eBay]({comps_link})"]
    if model.sample:
        bits[-1] += f" (n={model.sample}, measured {model.measured})"
    else:
        bits[-1] += "  :warning: *estimate, not measured*"
    bits.append(f"Sale side nets **${adv.net_resale:,.2f}** after fees + postage.")
    # The model's own caveat (conservative floor, seasonal hold window, thin
    # sample). Written into the book once, surfaced on every alert.
    if model.note:
        bits.append(f"_{model.note}_")

    if row.get("listing_type") == "fixed":
        # No auction to win: it's asking price vs your ceiling, and the price is
        # negotiable, so the ceiling is really a walk-away number in person.
        if adv.profit_at_open is not None:
            bits.append(f"Asking **${adv.open_bid:,.2f}** -> buy it and clear "
                        f"**${adv.profit_at_open:,.2f}**.")
        bits.append(f"Don't pay over **${adv.max_bid:,.2f}** (that's where the "
                    f"${adv.profit_at_max:,.0f} disappears). Price is negotiable.")
        if row.get("local"):
            bits.append("_Local pickup - no inbound shipping, so this is worth "
                        "~$9 more to you than the same thing in a shipped auction._")
        if row.get("source") == "craigslist":
            # Standing rule (Leron, 2026-07-30): local sources are PICKUP ONLY.
            # A seller who offers to "just ship it" is running the other classic
            # CL scam - you pay, nothing arrives.
            bits.append(":handshake: **Meet in person, test it, pay on pickup. "
                        "NEVER ship, never deposit, never pay ahead.**")
    else:
        if adv.profit_at_open is not None:
            bits.append(f"Win at the opening bid -> **${adv.profit_at_open:,.2f}** profit.")
        bits.append(f"At your max (${adv.max_bid:,.2f}) it lands at "
                    f"${adv.landed_at_max:,.2f} and still clears ${adv.profit_at_max:,.0f}.")
        # Say the premium out loud. It is charged at checkout, not in the bid,
        # so a bidder who only ever sees the hammer discovers it after winning -
        # and on these houses it is 10-20%, bigger than the target profit.
        if adv.buyer_premium_rate or adv.sales_tax_rate:
            parts = []
            if adv.buyer_premium_rate:
                parts.append(f"a **{adv.buyer_premium_rate * 100:.4g}% buyer's "
                             f"premium** (${adv.buyer_premium_at_max:,.2f})")
            if adv.sales_tax_rate:
                parts.append(f"**{adv.sales_tax_rate * 100:.4g}% sales tax** "
                             f"(${adv.sales_tax_at_max:,.2f})")
            guessed = [w for w, g in
                       (("premium", row.get("buyer_premium_guessed")),
                        ("tax", row.get("sales_tax_guessed")))
                       if g and getattr(adv, f"{'buyer_premium' if w == 'premium' else 'sales_tax'}_rate")]
            tail = ""
            if guessed:
                tail = (f" The {' and '.join(guessed)} rate"
                        f"{'s were' if len(guessed) > 1 else ' was'} not stated by "
                        f"the house - these are the going rates. Check its terms "
                        f"before bidding big.")
            bits.append(":receipt: At your max it also costs " +
                        " and ".join(parts) + "." + tail)
    age = age_hours(row.get("listed"))
    if age is not None:
        bits.append(f"_Listed {age:.0f}h ago._" if age >= 1 else "_Just listed._")
    if m.dead_also_present:
        bits.append(":warning: also contains: " + "; ".join(m.dead_also_present))

    # The card-shop read, on the listings that are cards. Costs nothing on
    # everything else - `cards.one_liner` returns "" unless the title proved it
    # is a sports card, and hands TCG straight back to the measured pokemon
    # tiers rather than second-guessing them.
    #
    # 🚨 IT NEVER TOUCHES THE CEILING. The numbers on this card come from a
    # MEASURED comp; the read is a note about what the title said, and letting
    # a triage score move a bid would put an unmeasured guess behind money.
    cl = card_line(read_card(row.get("title") or ""))
    if cl:
        bits.append(f":card_index: {cl}")
        # 🚨 A CARD ALERT WITH NO PHOTO IS NOT AN ALERT. On every other
        # category the picture is a nice-to-have and the title carries the
        # trade; on a raw card the title cannot state condition and condition
        # IS most of the value - which is why the vintage-chase tier's own note
        # says "buy the picture, not the words". 520 of the 521 listings on the
        # board carry an image, so this is rare - and precisely because it is
        # rare it would otherwise arrive as a silently worse alert that looks
        # exactly like every other one.
        if not row.get("image"):
            bits.append(":warning: **No photo on this listing.** On a card that "
                        "is disqualifying, not cosmetic - condition is most of "
                        "the value and the title never states it. Open the "
                        "listing and look before treating this as priced.")

    # WHO is selling it and WHERE. On the local auction sources this is the
    # difference between a 30-minute drive and an unknown, so say it plainly.
    where = ", ".join(x for x in (row.get("city"), row.get("state")) if x)
    if row.get("house"):
        bits.append(f"_{row['house']}" + (f" - {where}_" if where else "_"))
    elif where:
        bits.append(f"_Located in {where}._")

    if row.get("nearby") and row.get("source") != "craigslist":
        bits.append(":round_pushpin: **Within driving range** - collect it "
                    "yourself, so no inbound shipping (that's ~$9 of margin "
                    "the shipped auctions lose).")
    # Only warn when the auctioneer actually offers no shipping. This used to
    # fire on EVERY HiBid lot, which is how a warning becomes wallpaper.
    if row.get("pickup_risk"):
        if row.get("source") == "nellis":
            bits.append(":warning: Nellis is **pickup only** - you must collect "
                        "it from the warehouse, or you've bought nothing.")
        else:
            bits.append(":warning: Auctioneer offers **no shipping** - local "
                        "pickup only; check the lot terms.")
    if row.get("source") == "ebay" and row.get("listing_type") == "auction":
        bits.append(":dart: **eBay auction - snipe, don't chase.** Place ONE "
                    "bid at your max in the closing minutes; if it's outbid, "
                    "walk away.")
    cond = (row.get("condition") or "").lower()
    if "parts" in cond or "not working" in cond:
        bits.append(":wrench: **FOR PARTS / NOT WORKING** - the numbers here are "
                    "priced at the parts-grade haircut, NOT working-item comps. "
                    "Buy for repair/resale-as-is only.")
    if row.get("best_offer"):
        bits.append(":handshake: **Best Offer accepted** - don't pay the ask; "
                    "open 15-20% under it.")
    if adv.note:
        bits.append(f"_{adv.note}_")

    if is_scam:
        bits.insert(0, c.get("scam_warning") or scam_warning(adv))

    return {
        "title": (row.get("title") or "")[:240],
        "url": row.get("url"),
        "image": row.get("image"),
        "verdict": ("watch" if is_scam else
                    "buy" if (adv.profit_at_open or 0) >= adv.profit_at_max
                    else "watch"),
        "all_in": None,
        "comp": model.comp,
        "max_bid": adv.max_bid,
        "buyer_premium_rate": adv.buyer_premium_rate,
        "sales_tax_rate": adv.sales_tax_rate,
        "bids": row.get("bids"),
        "ends": row.get("ends") or None,
        "open_bid": adv.open_bid,
        "listing_type": row.get("listing_type", "auction"),
        "source": row.get("source"),
        # What the book PRICED it as. notify routes on this before falling back
        # to reading the title, because the category is what the money was
        # actually computed from.
        "category": model.category,
        "buy_url": row.get("url"),        # where to buy it
        "comps_url": comps_link,          # the eBay solds backing the claim
        "reason": "\n".join(bits),
    }


# Each source's naive `ends` stamp lives in a KNOWN zone: ShopGoodwill sends
# Pacific, Nellis sends UTC (the trailing Z is stripped to [:16] at parse).
# The old code compared them to runner-local time - "fuzzy by a few hours" per
# its own docstring - and the GitHub runner is UTC, so every goodwill lot
# looked 7 hours closer to ending than it was. Leron, 8/1: an "ENDS in ~0.1h"
# alert for a lot the site showed with 6 hours left. Same clock-bug family as
# FLASHPOINT: never raw-compare timestamps across zones.
_ENDS_TZ = {"goodwill": "America/Los_Angeles", "nellis": "UTC"}


def hours_until(ends: Optional[str], now: Optional[_dt.datetime] = None,
                source: Optional[str] = None) -> Optional[float]:
    """Hours until an `ends` timestamp, or None when unparseable/absent.

    When the source's zone is known, both sides are compared timezone-aware;
    unknown sources (HiBid sends nothing) keep the legacy runner-local guess
    and the generous window absorbs the fuzz."""
    if not ends:
        return None
    try:
        end = _dt.datetime.fromisoformat(str(ends)[:16])
    except ValueError:
        return None
    tzname = _ENDS_TZ.get(source or "")
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            end = end.replace(tzinfo=ZoneInfo(tzname))
            if now is None:
                now = _dt.datetime.now(_dt.timezone.utc)
            elif now.tzinfo is None:
                # Tests hand in a naive `now` meant as same-zone wall clock.
                now = now.replace(tzinfo=ZoneInfo(tzname))
        except Exception:
            end = end.replace(tzinfo=None)
            now = (now.replace(tzinfo=None) if now is not None
                   else _dt.datetime.now())
    else:
        now = now or _dt.datetime.now()
    return (end - now).total_seconds() / 3600.0


# Re-alert only while the price still sits at or under this share of the
# ceiling - an ending lot already bid to 90% of max is not news you can use.
ENDING_SOON_PRICE_SHARE = 0.5


def ending_soon_alerts(cands: list, config: dict, seen: set,
                       now: Optional[_dt.datetime] = None) -> list:
    """(seen_key, alert) pairs for qualifying lots in their final window."""
    window = config.get("ending_soon_hours")
    if not window:
        return []
    out = []
    for c in cands:
        row, adv = c["row"], c["advice"]
        if row.get("listing_type") == "fixed":
            continue                      # nothing "ends" on a buy-now ask
        left = hours_until(row.get("ends"), now=now, source=row.get("source"))
        if left is None or left < 0 or left > window:
            continue
        if (adv.open_bid or 0) > ENDING_SOON_PRICE_SHARE * adv.max_bid:
            continue
        key = f"endsoon:{row['source']}:{row['id']}"
        if key in seen:
            continue
        a = to_alert(c)
        a["reason"] = (f":alarm_clock: ENDS in ~{left:.1f}h and still at "
                       f"${(adv.open_bid or 0):,.2f} vs your ${adv.max_bid:,.2f} "
                       f"ceiling. " + (a.get("reason") or ""))
        out.append((key, a))
    return out


def estate_catalog_rows(config: dict, hunters=None, feed=None) -> list[dict]:
    """Every lot of every nearby ONLINE estate auction that turns out to be a
    HiBid catalog. Returns [] quietly when estates are off or nothing resolves."""
    area = config.get("estate_area")
    if not area:
        return []
    hib = next((h for h in (hunters or []) if getattr(h, "name", "") == "hibid"), None)
    if hib is None or not hasattr(hib, "catalog_lots"):
        return []
    from .estates import EstateSalesNet
    feed = feed if feed is not None else EstateSalesNet(area=area)
    sales = feed.sales()
    ids = feed.hibid_catalog_ids(sales)
    rows: list[dict] = []
    for aid in ids[:6]:                      # politeness cap per run
        rows += hib.catalog_lots(aid)
    if ids:
        print(f"[hunt] estate catalogs: {len(ids)} resolved to HiBid, "
              f"{len(rows)} lots swept in full.")
    return rows


def _post_scout(rows: list, config: dict, seen: set, notifier) -> tuple:
    """Post the card scout's finds. Returns (keys posted, channels used).

    🚨 RUNS ON A QUIET DAY TOO. It is called on BOTH of run()'s exits, because
    the day nothing clears the priced bar is exactly the day a card table is
    worth walking - and a cards channel that only speaks when the tool
    hardware is buying is a cards channel that never speaks.
    """
    try:
        finds = scout_cards(rows, config, seen=seen)
    except Exception as e:                    # never let the scout kill a run
        print(f"[hunt] card scout failed (non-fatal): {e}")
        return set(), []
    if not finds:
        return set(), []
    price_scout_finds(finds)
    alerts = [to_scout_alert(c) for c in finds]
    chase = sum(1 for c in finds if c["read"].verdict == "CHASE")
    # 🚨 THE HEADER SAID "no measured comps on these" TOO, and since the card
    # sources went in most of them DO have one. Count, don't assume.
    priced = sum(1 for c in finds
                 if getattr(c.get("sports"), "comp", None) is not None
                 and c["sports"].comp.priced and c["sports"].comp.low is not None
                 or getattr(c.get("poke"), "comp", None) is not None)
    header = (f":card_index: **Card scout** - {len(alerts)} worth opening"
              + (f" ({chase} CHASE)" if chase else "")
              + (f", {priced} priced" if priced else ""))

    # 🚨 SAY IT WHERE HE IS ACTUALLY LOOKING. When FLIPSCOUT_CARDS_WEBHOOK is
    # unset these cards fall back to the MAIN channel - deliberately, so a
    # routing rule can never make an alert vanish - and the only symptom is
    # that they turn up in the wrong place. `[hunt] card destination: NOT SET`
    # already says so, but that is a CI log line, and the person wondering why
    # his cards are in the wrong channel is looking at Discord, not at Actions.
    # Leron reported the same symptom twice while that line was printing
    # correctly on every run. A diagnostic nobody reads is not a diagnostic.
    _url, _chan, label = destination({"category": "sports-cards"}, os.environ)
    if label == "webhook":
        header += ("\n:warning: **These belong in your cards channel, not here.** "
                   "`FLIPSCOUT_CARDS_WEBHOOK` is not set, so they fall back to "
                   "this one. Add it under repo Settings -> Secrets and "
                   "variables -> Actions (and `FLIPSCOUT_CARDS_CHANNEL_ID` as a "
                   "*Variable*, or the cards arrive with no tap-to-arm chips).")
    sent = notifier(alerts, content=header)
    if sent:
        print(f"[hunt] SCOUT: {len(alerts)} card find(s) via {', '.join(sent)}.")
        return {c["key"] for c in finds}, sent
    print(f"[hunt] SCOUT NOT DELIVERED - {len(alerts)} card find(s) went nowhere.")
    return set(), []


def run(config: Optional[dict] = None, hunters=None, notifier=notify_rich) -> dict:
    config = config or load_config()
    # Print the DESTINATION every run. Delivery success has repeatedly meant
    # "Discord accepted it" while the alerts landed in a channel nobody watches.
    print(f"[hunt] alert destination: {describe_webhook(os.environ.get('FLIPSCOUT_ALERT_WEBHOOK'))}")
    # 🚨 SAY WHERE CARDS GO, TOO. The cards webhook falls back to the main
    # channel when unset - deliberately, so a routing rule can never make an
    # alert vanish - and the cost of that safety is that a MISSING secret looks
    # exactly like a working setup: cards quietly pile into #flips and the
    # cards channel reads as broken. Same failure this line already exists to
    # prevent for the main webhook, one channel over.
    _cards_hook = os.environ.get("FLIPSCOUT_CARDS_WEBHOOK")
    if _cards_hook:
        print(f"[hunt] card destination:  {describe_webhook(_cards_hook)}")
        if not (os.environ.get("FLIPSCOUT_CARDS_CHANNEL_ID") or "").strip():
            print("[hunt] card destination:  no FLIPSCOUT_CARDS_CHANNEL_ID - "
                  "cards will post but arrive with no tap-to-arm chips.")
    else:
        print("[hunt] card destination:  NOT SET - card alerts will fall back "
              "to the main channel. Set FLIPSCOUT_CARDS_WEBHOOK to split them "
              "out.")
    # Say the local config out loud. These come from repo VARIABLES, which the
    # workflow has to map into env one by one - three of them were set and
    # silently inert for a full run, and the log looked perfectly healthy.
    print("[hunt] local config: "
          + (f"zip {config['zip']} +{config['radius_miles']}mi"
             if config.get("zip") and config.get("radius_miles")
             else "NO ZIP SET - HiBid is searching nationally only")
          + " | estates: " + (config.get("estate_area") or "OFF"))
    hunters = hunters if hunters is not None else build_hunters(config["sources"])
    # The eBay hunter fails soft per-search, so say up front whether its keys
    # actually authenticate — a wrong secret otherwise looks like a quiet market.
    for h in hunters:
        if hasattr(h, "auth_probe"):
            print(f"[hunt] {h.auth_probe()}")
    rows = sweep(config, hunters=hunters)
    # Per-source counts: a blocked/broken source shows up as =0 here instead of
    # hiding inside the grand total.
    counts: dict[str, int] = {getattr(h, "name", "?"): 0 for h in hunters}
    for r in rows:
        counts[r.get("source", "?")] = counts.get(r.get("source", "?"), 0) + 1
    print("[hunt] per-source: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    for h in hunters:
        if hasattr(h, "error_summary"):
            s = h.error_summary()
            if s:
                print(f"[hunt] {s}")

    # Estate catalogs, swept IN FULL. The digest used to hand over links and
    # leave the reading to Leron ("its you job to go to the links and find me
    # deals", 2026-07-29). Online estate sales that resolve to HiBid catalogs
    # now feed every lot through the same book + gate as everything else.
    try:
        extra = estate_catalog_rows(config, hunters=hunters)
        known = {f"{r['source']}:{r['id']}" for r in rows}
        rows += [r for r in extra if f"{r['source']}:{r['id']}" not in known]
    except Exception as e:
        print(f"[hunt] estate catalog sweep failed (non-fatal): {e}")

    cands = evaluate(rows, config, hunters=hunters)
    # 🚨 FILTERED ONCE, HERE, SO EVERYTHING DOWNSTREAM INHERITS IT. `cands`
    # feeds the alerts, the board, the ending-soon re-alert and the heartbeat
    # digest - muting at any one of them would have left the category showing
    # up in the other three.
    if MUTED_CATEGORIES:
        keep = [c for c in cands if not _muted(c)]
        if len(keep) != len(cands):
            import collections as _co
            gone = _co.Counter((getattr(c.get("model"), "category", "") or "?")
                               for c in cands if _muted(c))
            print("[hunt] muted " + ", ".join(f"{k} {v}" for k, v in
                                              sorted(gone.items()))
                  + f" ({len(cands) - len(keep)} of {len(cands)}) - "
                  + "FLIPSCOUT_MUTE_CATEGORIES")
        cands = keep

    seen = _load_seen(config["state_file"])
    all_keys = {f"{c['row']['source']}:{c['row']['id']}" for c in cands}
    unseen = all_keys - seen
    # PER-MODEL CAP: `cands` is already sorted best-profit-first, and a
    # straight top-N slice let a handful of high-volume models (Starrett,
    # Mitutoyo, film cameras, camcorders) monopolize every digest while whole
    # categories with measured live supply never got a look-in. Walk the
    # sorted list and skip any candidate whose model already has its
    # FLIPSCOUT_MAX_PER_MODEL picks THIS RUN, still stopping at `top` total.
    # Capped-out candidates are deliberately left OUT of `fresh` (never
    # touched by `seen`) so they queue for the next run instead of being
    # silently lost - only what's actually released/sent gets marked seen.
    max_per_model = config.get("max_per_model", 3)
    model_counts: dict[str, int] = {}
    fresh: list[dict] = []
    capped = 0

    # 🚨 CLAMP, DO NOT TRUST. Every one of these is an env var, and the
    # failure mode is silence: a negative TOP or a negative slot count made
    # `_take` return immediately and the run alerted NOTHING, which looks
    # exactly like a quiet market. Measured during the 2026-08-19 audit.
    top = max(0, int(config["top"]))

    # 🚨 `or 1` turns a deliberate 0 INTO 1. Setting FLIPSCOUT_URGENT_HOURS=0
    # to switch the lane off silently switched it on at one hour instead. A
    # non-positive window now means the lane is off, which is what was asked.
    urgent_h = float(config.get("urgent_hours", 1) if
                     config.get("urgent_hours") is not None else 1)
    closing_h = float(config.get("closing_hours", 12) if
                      config.get("closing_hours") is not None else 12)
    quarter = max(1, top // 4)
    bin_slots = max(0, int(config.get("bin_slots") or 0) or quarter)
    urgent_slots = max(0, int(config.get("urgent_slots") or 0) or quarter)
    reserved = max(0, int(config.get("closing_slots") or 0) or quarter)

    eligible = [c for c in cands
                if f"{c['row']['source']}:{c['row']['id']}" not in seen
                and not c.get("scam_shaped")]

    taken: set = set()
    capped_keys: set = set()

    def _key(c):
        return f"{c['row']['source']}:{c['row']['id']}"

    def _take(pool, limit):
        """Fill up to `limit` slots from `pool`, honouring the per-model cap.

        🚨 IDENTITY IS THE KEY, NOT THE DICT. This used to test `c in fresh`,
        which is a DEEP EQUALITY walk over every candidate already chosen -
        including its advice dataclass and model - once per candidate per lane.
        Quadratic, and wrong in principle: two distinct lots that happened to
        compare equal would silently lose one.
        """
        nonlocal capped
        if limit <= 0:
            return 0
        n = 0
        for c in pool:
            if n >= limit or len(fresh) >= top:
                break
            k = _key(c)
            if k in taken:
                continue
            label = c["model"].label
            if model_counts.get(label, 0) >= max_per_model:
                # 🚨 Count each lot ONCE. Lanes re-walk the same pool, so a lot
                # blocked by the model cap was counted again in every lane and
                # the reported figure ran far above the real one.
                if k not in capped_keys:
                    capped_keys.add(k)
                    capped += 1
                continue
            model_counts[label] = model_counts.get(label, 0) + 1
            taken.add(k)
            fresh.append(c)
            n += 1
        return n

    # THE CLOSING LANE FIRST. Soonest-closing wins inside it, not richest -
    # within a few hours of the end the clock is the scarce thing, and the
    # sniper needs to be armed before it runs out.
    # 🚨 hours_until, not a new helper - it already knows each source's clock
    # convention, and a home-rolled parse here would be the FLASHPOINT bug all
    # over again. Fixed-price asks are skipped: nothing "ends" on a buy-now.
    def _left(c):
        if c["row"].get("listing_type") == "fixed":
            return None
        return hours_until(c["row"].get("ends"), source=c["row"].get("source"))

    # BUY IT NOW. Ranked by PROFIT rather than by clock - unlike the closing
    # lanes, nothing is running out, and the number is trustworthy because the
    # price is final. Leron can act on these immediately: no registration, no
    # sniper, no bidding war.
    binned = [c for c in eligible if c["row"].get("listing_type") == "fixed"]
    took_b = _take(binned, bin_slots)

    # 🚨 URGENT FIRST. A lot closing inside the hour cannot wait for the next
    # run, because the next run may be after it has closed. Unused urgent slots
    # are not wasted - each _take is also bounded by how many slots remain free
    # overall, so the later lanes simply pick them up.
    urgent = [] if urgent_h <= 0 else [
        c for c in eligible
        if _left(c) is not None and 0 <= _left(c) <= urgent_h]
    urgent.sort(key=lambda c: _left(c))
    took_u = _take(urgent, urgent_slots)

    # Then today's closers, EXCLUDING the urgent ones already taken.
    closing = [] if closing_h <= 0 else [
        c for c in eligible
        if _left(c) is not None and max(urgent_h, 0) < _left(c) <= closing_h]
    closing.sort(key=lambda c: _left(c))
    took_c = _take(closing, reserved)

    # Everything else on the usual profit ranking.
    _take(eligible, top - len(fresh))
    if binned or urgent or closing:
        print(f"[hunt] buy-it-now lane: {len(binned)} candidate(s), {took_b} "
              f"alerted | urgent (<={urgent_h:g}h): {len(urgent)}, {took_u} "
              f"alerted | closing (<={closing_h:g}h): {len(closing)}, "
              f"{took_c} alerted")
    # Is the queue genuinely refilling, or are we just draining a backlog? This is
    # the difference between "new opportunities daily" and "one pool, dripped out".
    print(f"[hunt] already-alerted: {len(seen)} | qualifying now: {len(all_keys)} | "
          f"never-alerted: {len(unseen)} | releasing: {min(len(unseen), config['top'])} | "
          f"deferred by per-model cap ({max_per_model}/model): {capped}")
    # 🚨 KEPT after the per-category cap was removed (Leron, 2026-08-19).
    # Alerts are ranked purely on profit again, so a single category CAN take a
    # whole run - that is now a deliberate choice rather than a bug, and this
    # line is the only way to see it happening. If the "all cameras, then all
    # watches, then dry" swing comes back, it will show up here first.
    if fresh:
        mix = {}
        for c in fresh:
            k = getattr(c["model"], "category", "") or "?"
            mix[k] = mix.get(k, 0) + 1
        print("[hunt] category mix this run: "
              + ", ".join(f"{k} {v}" for k, v in sorted(mix.items())))

    print(f"[hunt] {len(rows)} listings across {len(config['sources'])} source(s); "
          f"{len(cands)} priced; {len(fresh)} new.")

    # Hunters fail soft, so a WAF block looks identical to "nothing for sale".
    # That silence is the dangerous failure: the watcher would look healthy
    # forever while seeing nothing. Say so loudly instead.
    if not rows:
        msg = ("[hunt] NO listings from ANY source. That is almost certainly a block "
               "or an API change, not an empty market - check the hunters.")
        print(msg)
        return {"scanned": 0, "priced": 0, "new": 0, "sent": [], "blocked": True}

    # Publish EVERY qualifying item, not just the new ones. An alert is news;
    # the board is inventory, and something you were told about an hour ago is
    # still buyable. Deliberately after the blocked-check above, so a WAF block
    # leaves the last good board in place instead of blanking it.
    from . import board as _board
    board_file = config.get("board_file")
    if board_file and _board.write(cands, board_file):
        print(f"[hunt] deals board: {len(cands)} item(s) -> {board_file}")

    # Independent of whether any deal qualified: the estate-sale calendar is
    # its own thing, and a quiet deal-day is exactly when it's most useful.
    try:
        post_estate_digest(config, notifier)
    except Exception as e:
        print(f"[hunt] estate digest failed (non-fatal): {e}")
    try:
        post_garage_digest(config, notifier)
    except Exception as e:
        print(f"[hunt] garage digest failed (non-fatal): {e}")

    # SECOND ALERT TIER: the buy decision happens in the last hour, not when
    # a lot is first seen days out - a $5 find can quietly become $80 without
    # another word. Re-alert ONCE (its own seen-namespace) when a qualifying
    # lot ends inside the window and its price still sits at or under half
    # the ceiling. Runs on the quiet path too - ending lots don't wait for a
    # fresh find to show up.
    try:
        endsoon = ending_soon_alerts(cands, config, seen)
        if endsoon:
            es_sent = notifier(
                [a for _, a in endsoon],
                content=(f":alarm_clock: **ENDING SOON** - {len(endsoon)} "
                         f"lot(s) close within "
                         f"{config['ending_soon_hours']:g}h still at or under "
                         f"HALF your max bid. Last call."))
            if es_sent:
                seen |= {k for k, _ in endsoon}
                _save_seen(config["state_file"], seen)
                print(f"[hunt] ENDING SOON: {len(endsoon)} re-alert(s) delivered.")
    except Exception as e:
        print(f"[hunt] ending-soon pass failed (non-fatal): {e}")

    if not fresh:
        if _due_for_heartbeat(config["heartbeat_file"]):
            # Post what's ON the board, not just "nothing changed". Saying
            # "nothing new right now" while 164 items sit there buyable is how
            # a working watcher reads as a dead one.
            board_data = _board.build(cands)
            body = _board.digest(board_data)
            # Top rows ride along as embeds with OUR image urls - Discord's
            # unfurler was producing blank cards for Craigslist search links.
            notifier(_board.top_items(board_data) if body else [], content=(body or (
                f"**Flipscout daily check-in** - still running, nothing new right now.\n"
                f"Swept **{len(rows):,}** listings across {len(config['sources'])} "
                f"sources; nothing currently clears your "
                f"${config['target_profit']:.0f} bar.\n"
                f"_Quiet is normal._")))
            _mark_heartbeat(config["heartbeat_file"])
        scouted, scout_sent = _post_scout(rows, config, seen, notifier)
        if scouted:
            _save_seen(config["state_file"], seen | scouted)
        return {"scanned": len(rows), "priced": len(cands), "new": 0,
                "sent": scout_sent, "scouted": len(scouted)}

    alerts = [to_alert(c) for c in fresh]
    header = (f"**Flipscout** - {len(alerts)} new "
              f"{'find' if len(alerts) == 1 else 'finds'} "
              f"across {', '.join(sorted({a['source'] for a in alerts}))}\n"
              f"_Open at the first number, never bid past the second._")
    sent = notifier(alerts, content=header)

    # Say out loud whether Discord actually took it. notify_rich is fail-soft, so
    # a dead or wrong webhook produces silence that looks exactly like success -
    # which is how "it stopped sending me deals" becomes undiagnosable from logs.
    if sent:
        print(f"[hunt] DELIVERED {len(alerts)} alert(s) via {', '.join(sent)}.")
    else:
        print(f"[hunt] NOT DELIVERED - {len(alerts)} alert(s) went nowhere. "
              f"Check FLIPSCOUT_ALERT_WEBHOOK is set and still valid.")

    fresh_keys = {f"{c['row']['source']}:{c['row']['id']}" for c in fresh}
    scouted, scout_sent = _post_scout(rows, config, seen | fresh_keys, notifier)
    _save_seen(config["state_file"], seen | fresh_keys | scouted)
    return {"scanned": len(rows), "priced": len(cands), "new": len(fresh),
            "sent": sent + [x for x in scout_sent if x not in sent],
            "scouted": len(scouted)}


def main(argv=None) -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

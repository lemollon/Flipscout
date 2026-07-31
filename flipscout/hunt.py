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
    FLIPSCOUT_STATE_FILE      seen-cache              (default flipscout_seen.json)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional

from .bidding import advise
from .hunters import build_hunters
from .notify import describe_webhook, notify_rich
from .ebay_ui import sold_url
from .pricebook import comp_search, match, search_terms


def load_config(env=None) -> dict:
    env = env if env is not None else os.environ
    return {
        "sources": [s.strip() for s in env.get("FLIPSCOUT_SOURCES", "goodwill,hibid,craigslist,poshmark,propertyroom,nellis,outandback,geartrade").split(",") if s.strip()],
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
                              merged_sales, digest)
    if feed is not None:
        feeds = [feed]
    else:
        feeds = [YardSaleSearch(zip_code), GarageSaleFinder(zip_code)]
        feeds += [Gsalr(c.strip()) for c in
                  (config.get("gsalr_cities") or "").split(",") if c.strip()]
    sales = merged_sales(feeds)
    if not sales:
        return False
    notifier([], content=digest(sales, zip_code))
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


def evaluate(rows: list, config: dict, hunters=None) -> list[dict]:
    """Price each listing that matches a model in the book."""
    by_name = {h.name: h for h in (hunters if hunters is not None
                                   else build_hunters(config["sources"]))}
    out = []
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

        out.append({"row": row, "model": m.model, "match": m, "advice": adv})

    # Best headroom first: what you'd clear if you won at the current minimum.
    out.sort(key=lambda c: (c["advice"].profit_at_open or 0), reverse=True)
    return out


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


# A fixed-price ask this far under resale on a LOCAL source is bait, not a
# deal: real Craigslist underprices vanish in minutes, and the ones that sit
# are scams. The board's #1 "find" - a $50 G7X Mark II vs a $1,149 comp - was
# one (Leron confirmed, 2026-07-28). Auction opens near $0 are normal; fixed
# asks near $0 are not.
SCAM_ASK_SHARE = 0.15


def to_alert(c: dict) -> dict:
    """One evaluated candidate -> the Discord embed payload."""
    row, model, adv, m = c["row"], c["model"], c["advice"], c["match"]
    units = f" x{adv.units}" if adv.units > 1 else ""
    scam_shaped = (row.get("listing_type") == "fixed" and row.get("local")
                   and (adv.open_bid or 0) > 0
                   and adv.open_bid < SCAM_ASK_SHARE * adv.net_resale)

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
    age = age_hours(row.get("listed"))
    if age is not None:
        bits.append(f"_Listed {age:.0f}h ago._" if age >= 1 else "_Just listed._")
    if m.dead_also_present:
        bits.append(":warning: also contains: " + "; ".join(m.dead_also_present))

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

    if scam_shaped:
        bits.insert(0, (f":triangular_flag_on_post: **SCAM-SHAPED PRICE** - a "
                        f"fixed ask of ${adv.open_bid:,.2f} on an item that nets "
                        f"${adv.net_resale:,.2f} is bait more often than a deal "
                        f"(the $50 G7X lesson). Real underprices sell in minutes; "
                        f"the ones that SIT are the trap. Verify in person before "
                        f"believing it."))

    return {
        "title": (row.get("title") or "")[:240],
        "url": row.get("url"),
        "image": row.get("image"),
        "verdict": ("watch" if scam_shaped else
                    "buy" if (adv.profit_at_open or 0) >= adv.profit_at_max
                    else "watch"),
        "all_in": None,
        "comp": model.comp,
        "max_bid": adv.max_bid,
        "bids": row.get("bids"),
        "ends": row.get("ends") or None,
        "open_bid": adv.open_bid,
        "listing_type": row.get("listing_type", "auction"),
        "source": row.get("source"),
        "buy_url": row.get("url"),        # where to buy it
        "comps_url": comps_link,          # the eBay solds backing the claim
        "reason": "\n".join(bits),
    }


def hours_until(ends: Optional[str], now: Optional[_dt.datetime] = None) -> Optional[float]:
    """Hours until an `ends` timestamp, or None when unparseable/absent.

    Source timestamps are naive local-ish strings ("2026-07-29T18:30") with no
    timezone - HiBid sends none at all. Treated as runner-local time, which is
    fuzzy by a few hours; the window is deliberately generous to absorb that."""
    if not ends:
        return None
    try:
        end = _dt.datetime.fromisoformat(str(ends)[:16])
    except ValueError:
        return None
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
        left = hours_until(row.get("ends"), now=now)
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


def run(config: Optional[dict] = None, hunters=None, notifier=notify_rich) -> dict:
    config = config or load_config()
    # Print the DESTINATION every run. Delivery success has repeatedly meant
    # "Discord accepted it" while the alerts landed in a channel nobody watches.
    print(f"[hunt] alert destination: {describe_webhook(os.environ.get('FLIPSCOUT_ALERT_WEBHOOK'))}")
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

    seen = _load_seen(config["state_file"])
    all_keys = {f"{c['row']['source']}:{c['row']['id']}" for c in cands}
    unseen = all_keys - seen
    fresh = [c for c in cands
             if f"{c['row']['source']}:{c['row']['id']}" not in seen][: config["top"]]
    # Is the queue genuinely refilling, or are we just draining a backlog? This is
    # the difference between "new opportunities daily" and "one pool, dripped out".
    print(f"[hunt] already-alerted: {len(seen)} | qualifying now: {len(all_keys)} | "
          f"never-alerted: {len(unseen)} | releasing: {min(len(unseen), config['top'])}")

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
        return {"scanned": len(rows), "priced": len(cands), "new": 0, "sent": []}

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

    _save_seen(config["state_file"],
               seen | {f"{c['row']['source']}:{c['row']['id']}" for c in fresh})
    return {"scanned": len(rows), "priced": len(cands), "new": len(fresh), "sent": sent}


def main(argv=None) -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

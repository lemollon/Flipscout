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
    FLIPSCOUT_CL_CITIES       your craigslist metro(s) (default sfbay)
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
        "sources": [s.strip() for s in env.get("FLIPSCOUT_SOURCES", "goodwill,hibid,craigslist").split(",") if s.strip()],
        "target_profit": float(env.get("FLIPSCOUT_TARGET_PROFIT", "20")),
        "inbound_shipping": float(env.get("FLIPSCOUT_INBOUND_SHIP", "9")),
        "top": int(env.get("FLIPSCOUT_TOP", "10")),
        # Optional hard freshness filter, OFF by default on purpose: for auctions
        # "listed recently" is the wrong signal - a lot posted days ago that ends
        # in 30 minutes with no bids is the better buy, because its price is
        # nearly final. Only Goodwill exposes a listing date at all.
        "max_age_hours": (float(env["FLIPSCOUT_MAX_AGE_HOURS"])
                          if env.get("FLIPSCOUT_MAX_AGE_HOURS") else None),
        "state_file": env.get("FLIPSCOUT_STATE_FILE", "flipscout_seen.json"),
    }


def _load_seen(path: str) -> set:
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_seen(path: str, seen: set) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(seen)[-5000:], f)
    except Exception as e:
        print(f"[hunt] couldn't save seen-cache: {e}")


def sweep(config: dict, hunters=None) -> list[dict]:
    """Every source x every term -> deduped raw listings."""
    hunters = hunters if hunters is not None else build_hunters(config["sources"])
    found: dict[str, dict] = {}
    for h in hunters:
        for term in search_terms():
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

        adv = advise(
            m.model.comp,
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


def to_alert(c: dict) -> dict:
    """One evaluated candidate -> the Discord embed payload."""
    row, model, adv, m = c["row"], c["model"], c["advice"], c["match"]
    units = f" x{adv.units}" if adv.units > 1 else ""

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
    if row.get("pickup_risk"):
        bits.append(":warning: HiBid lot - may be **local pickup only**; check the terms.")
    if adv.note:
        bits.append(f"_{adv.note}_")

    return {
        "title": (row.get("title") or "")[:240],
        "url": row.get("url"),
        "image": row.get("image"),
        "verdict": "buy" if (adv.profit_at_open or 0) >= adv.profit_at_max else "watch",
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


def run(config: Optional[dict] = None, hunters=None, notifier=notify_rich) -> dict:
    config = config or load_config()
    # Print the DESTINATION every run. Delivery success has repeatedly meant
    # "Discord accepted it" while the alerts landed in a channel nobody watches.
    print(f"[hunt] alert destination: {describe_webhook(os.environ.get('FLIPSCOUT_ALERT_WEBHOOK'))}")
    rows = sweep(config, hunters=hunters)
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

    if not fresh:
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

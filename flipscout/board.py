"""The deals board: the actual items that currently clear your bar.

The hourly watcher already prices every listing it sweeps and knows exactly
which ones qualify - it just used to throw that away after posting the few that
were new to Discord. This publishes the whole qualifying set as JSON so the web
app can show REAL ITEMS with photos, prices and both links, instead of asking
you to type a search into a scanner that needs eBay credentials we never got.

Two deliberate differences from the Discord alerts:

  * The board carries EVERY qualifying item, not just the never-alerted ones.
    An alert is news ("this is new"); the board is inventory ("here's what's
    buyable right now"), and something you were told about an hour ago is still
    buyable.
  * It is regenerated from scratch each run, so a sold or expired lot simply
    stops appearing. There is no stale-item cleanup to get wrong.

Written to docs/deals.json and committed by the workflow, which makes the board
a plain static file: no server, no API keys, and it still works when the free
Render instance is asleep.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional

from .ebay_ui import sold_url
from .pricebook import comp_search


def item(c: dict) -> dict:
    """One evaluated candidate -> one board row."""
    row, model, adv, m = c["row"], c["model"], c["advice"], c["match"]
    where = ", ".join(x for x in (row.get("city"), row.get("state")) if x)
    return {
        "title": (row.get("title") or "")[:200],
        "source": row.get("source"),
        "url": row.get("url"),
        "image": row.get("image") or "",
        # What it IS, and the evidence for the number.
        "model": model.label,
        "units": adv.units,
        "comp": round(model.comp, 2),
        "comp_sample": model.sample,
        "comp_measured": model.measured,
        "comps_url": sold_url(comp_search(model), used_only=model.comp_used_only),
        "net_resale": round(adv.net_resale, 2),
        # What to DO. open_bid is what it costs right now; max_bid is the line.
        "listing_type": row.get("listing_type", "auction"),
        "price": row.get("price"),
        "open_bid": round(adv.open_bid, 2) if adv.open_bid is not None else None,
        "max_bid": round(adv.max_bid, 2),
        "profit_at_open": (round(adv.profit_at_open, 2)
                           if adv.profit_at_open is not None else None),
        "profit_at_max": round(adv.profit_at_max, 2),
        # Where it is and what could bite.
        "house": row.get("house") or "",
        "where": where,
        "nearby": bool(row.get("nearby")),
        "pickup_only": bool(row.get("pickup_risk")),
        "bids": row.get("bids"),
        "ends": row.get("ends") or "",
        "warnings": list(m.dead_also_present or []),
    }


def build(cands: list, now: Optional[_dt.datetime] = None) -> dict:
    items = [item(c) for c in cands]
    return {
        "generated": (now or _dt.datetime.now(_dt.timezone.utc)).isoformat(timespec="seconds"),
        "count": len(items),
        "nearby_count": sum(1 for i in items if i["nearby"]),
        "sources": sorted({i["source"] for i in items if i["source"]}),
        "items": items,
    }


def write(cands: list, path: str, now: Optional[_dt.datetime] = None) -> Optional[str]:
    """Write the board, creating the directory if needed. Returns the path, or
    None on failure - publishing must never take the watcher down."""
    board = build(cands, now=now)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(board, f, indent=1)
        return path
    except Exception as e:
        print(f"[hunt] couldn't write the deals board: {e}")
        return None


def digest(board_data: dict, top: int = 5) -> str:
    """The board as one Discord message.

    The daily check-in used to say "nothing new right now - you've already been
    sent all of them", which is actively misleading when 164 items are sitting
    there buyable. Discord should say what's ON the board, not just what changed
    since yesterday.
    """
    items = board_data.get("items") or []
    if not items:
        return ""
    best = sorted(items, key=lambda i: (i.get("profit_at_open") or -1e9),
                  reverse=True)[:top]
    near = board_data.get("nearby_count") or 0
    lines = [f"**Flipscout board** - {len(items)} item(s) buyable right now"
             + (f", {near} drivable" if near else "")
             + f" across {', '.join(board_data.get('sources') or []) or '-'}",
             "_Nothing NEW since the last alert - this is everything currently "
             "clearing your bar._", ""]
    for i in best:
        fixed = i.get("listing_type") == "fixed"
        now_label = "Asking" if fixed else "Open"
        cap_label = "don't pay over" if fixed else "max bid"
        tags = []
        if i.get("nearby"):
            tags.append(":round_pushpin: drivable")
        if i.get("pickup_only"):
            tags.append("pickup only")
        where = " - ".join(x for x in (i.get("house"), i.get("where")) if x)
        lines.append(
            f"**[{(i.get('title') or '')[:70]}]({i.get('url')})** "
            f"({i.get('source')}{', ' + ', '.join(tags) if tags else ''})")
        profit = i.get("profit_at_open")
        lines.append(
            f"   {i.get('model')} - {now_label} ${i.get('open_bid'):,.2f}"
            + (f" -> clear **${profit:,.2f}**" if profit is not None else "")
            + f", {cap_label} **${i.get('max_bid'):,.2f}**"
            + (f"  _{where}_" if where else ""))
    if len(items) > top:
        lines.append(f"\n_...and {len(items) - top} more on the board._")
    return "\n".join(lines)[:1900]


def load(path: str) -> dict:
    """Read a published board. Returns an empty board when there isn't one."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
        data.setdefault("items", [])
        return data
    except Exception:
        return {"generated": None, "count": 0, "nearby_count": 0,
                "sources": [], "items": []}

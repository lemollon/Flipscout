"""The whole price book as one JSON file: every model, its comp, and the
most you may pay for it on Facebook.

WHY THIS FILE EXISTS
--------------------
The ceilings a negotiation runs against have to be the CURRENT ones.
Before this export, the only ways to read the book were opening a
3,000-line Python file or copying numbers out of a one-off inspection -
and a copied ceiling goes stale the moment a comp is re-measured, without
anything looking wrong. hunt.py already republishes docs/deals.json every
run; this snapshot rides the same workflow commit, so it is never older
than the board next to it.

The max bid here is on the FACEBOOK basis - local pickup, so inbound
shipping is ZERO, target profit from the run's config - computed by the
same bidding.advise() call fbsweep.evaluate() makes. One computation, two
readers; the numbers cannot drift apart.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional

from .bidding import advise
from .pricebook import MODELS

# Local pickup: the whole FB edge. Must stay in step with fbsweep.INBOUND.
INBOUND = 0.0


def build(target_profit: float = 20.0,
          now: Optional[_dt.datetime] = None) -> dict:
    """Every model in the book with its comp and its FB never-exceed number.

    Benched (inactive) models are exported too, flagged `active: false` -
    the book still prices them when checked by hand, and a reader that only
    saw the active set could not tell "benched" from "never measured".
    """
    rows = []
    for m in MODELS:
        a = advise(m.comp, outbound_shipping=m.outbound_shipping,
                   target_profit=target_profit, inbound_shipping=INBOUND)
        rows.append({
            "key": m.key,
            "label": m.label,
            "category": m.category,
            "active": m.active,
            "comp": round(m.comp, 2),
            "comp_measured": m.measured,
            "comp_sample": m.sample,
            "fb_max_bid": round(a.max_bid, 2),
            "net_resale": round(a.net_resale, 2),
        })
    return {
        "generated": (now or _dt.datetime.now(_dt.timezone.utc)
                      ).isoformat(timespec="seconds"),
        "count": len(rows),
        "target_profit": target_profit,
        "inbound_shipping": INBOUND,
        "models": rows,
    }


def write(path: str, target_profit: float = 20.0,
          now: Optional[_dt.datetime] = None) -> Optional[str]:
    """Write the snapshot. Returns the path, or None on failure - publishing
    must never take the watcher down (same contract as board.write)."""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(build(target_profit=target_profit, now=now), f, indent=1)
        return path
    except Exception as e:
        print(f"[hunt] couldn't write the price book snapshot: {e}")
        return None

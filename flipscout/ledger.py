"""The purchase ledger: what was actually bought, what it actually sold for.

Why this exists (2026-07-29): every number in the price book is a MEASURED
sold comp, but none of them had ever been checked against Leron's own realized
prices. That's the same gap as backtest-vs-live in a trading system, and it is
where models quietly rot: a comp measured in July can be a hype peak by
October. The ledger records buys and sales, computes realized profit with the
same fee model the alerts use, and reports comp-vs-realized drift per model so
a decaying comp announces itself instead of costing money silently.

Storage is one JSON file in the repo root - committed if you want it synced,
local otherwise. Deliberately no server, no database, no cleverness.

    flipscout bought "Gunne Sax dress" --paid 21.50 --source goodwill
    flipscout sold 3 --gross 122.00 --shipping 6.40
    flipscout pnl
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional

from .fees import FeeModel, net_proceeds
from .pricebook import match

def _default_path() -> str:
    return os.environ.get("FLIPSCOUT_LEDGER_FILE", "flipscout_ledger.json")


def _load(path=None) -> list[dict]:
    path = path or _default_path()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or []
    except Exception:
        return []


def _save(entries, path=None) -> None:
    path = path or _default_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=1)


def entries(path=None) -> list:
    """Every ledger row, oldest first. The public read side — `flipscout.velocity`
    measures realized hold times off these, so it needs a supported way in."""
    return _load(path)


def record_buy(title: str, paid: float, source: str = "", url: str = "",
               note: str = "", path=None,
               today: Optional[str] = None) -> dict:
    """One purchase. The book match is pinned AT BUY TIME so later re-measures
    don't rewrite history - drift is only visible if we keep the old number."""
    entries = _load(path)
    m = match(title)
    entry = {
        "id": (max((e["id"] for e in entries), default=0) + 1),
        "date": today or _dt.date.today().isoformat(),
        "title": title.strip(),
        "paid": round(float(paid), 2),
        "source": source, "url": url, "note": note,
        "model": m.model.key if m else None,
        "model_label": m.model.label if m else "(not in book)",
        "comp_at_buy": m.model.comp if m else None,
        "status": "open",
    }
    entries.append(entry)
    _save(entries, path)
    return entry


def record_sale(entry_id: int, gross: float, shipping: float = 0.0,
                fees: Optional[FeeModel] = None, path=None,
                today: Optional[str] = None) -> Optional[dict]:
    """Close a purchase with what it ACTUALLY sold for (all-in gross).

    Net uses the same fee model as the alerts, so realized profit and promised
    profit are computed on identical assumptions - if they diverge, it's the
    comp or the market, never the arithmetic."""
    entries = _load(path)
    for e in entries:
        if e["id"] == int(entry_id):
            net = net_proceeds(float(gross), fees=fees or FeeModel(),
                               shipping_cost=float(shipping)).net
            e.update({
                "sold_date": today or _dt.date.today().isoformat(),
                "gross": round(float(gross), 2),
                "sale_shipping": round(float(shipping), 2),
                "net": round(net, 2),
                "profit": round(net - e["paid"], 2),
                "status": "sold",
            })
            _save(entries, path)
            return e
    return None


def pnl(path=None) -> str:
    """Realized P&L per model, with comp-vs-realized drift."""
    entries = _load(path)
    if not entries:
        return ("ledger is empty - record purchases with:\n"
                '  flipscout bought "<title>" --paid <amount> --source <src>')
    sold = [e for e in entries if e["status"] == "sold"]
    open_ = [e for e in entries if e["status"] != "sold"]
    L = [f"Flipscout ledger - {len(entries)} purchase(s), {len(sold)} sold, "
         f"{len(open_)} open"]
    if sold:
        total_paid = sum(e["paid"] for e in sold)
        total_profit = sum(e.get("profit") or 0 for e in sold)
        L.append(f"REALIZED: paid ${total_paid:,.2f} -> profit "
                 f"${total_profit:,.2f} across {len(sold)} flip(s)")
        by_model: dict[str, list] = {}
        for e in sold:
            by_model.setdefault(e.get("model_label") or "?", []).append(e)
        L.append("")
        L.append(f"{'model':<38} {'n':>2} {'paid':>9} {'profit':>9}  comp vs realized")
        for label, es in sorted(by_model.items(), key=lambda kv: -sum(x.get('profit') or 0 for x in kv[1])):
            paid = sum(e["paid"] for e in es)
            prof = sum(e.get("profit") or 0 for e in es)
            comps = [e for e in es if e.get("comp_at_buy") and e.get("gross")]
            drift = ""
            if comps:
                avg = sum(e["gross"] / e["comp_at_buy"] for e in comps) / len(comps)
                drift = f"{avg:5.0%}"
                # The line this whole module exists for.
                if avg < 0.85:
                    drift += "  <- realizing UNDER comp: re-measure this model"
            L.append(f"{label[:38]:<38} {len(es):>2} ${paid:>8,.2f} ${prof:>8,.2f}  {drift}")
    if open_:
        L.append("")
        L.append("OPEN (bought, not yet sold):")
        for e in open_:
            L.append(f"  #{e['id']:<3} {e['date']} ${e['paid']:>8,.2f} "
                     f"{e.get('model_label') or '?':<32} {e['title'][:40]}")
    return "\n".join(L)

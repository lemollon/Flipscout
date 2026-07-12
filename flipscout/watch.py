"""Always-on deal watcher — money shows up without you looking.

Runs your watchlist across every buying source, keeps only deals you haven't been
alerted about, and pushes the best ones (by $/hour) to your alert channel. Meant to
run on a schedule (a free GitHub Actions cron; see .github/workflows/watch.yml).

Config is all env vars, so it drops into any scheduler:
  FLIPSCOUT_WATCHLIST   searches, newline- or comma-separated (required)
  FLIPSCOUT_SOURCES     e.g. "ebay,goodwill"        (default "ebay")
  FLIPSCOUT_MIN_PROFIT  dollars                       (default 20)
  FLIPSCOUT_MIN_ROI     fraction                      (default 0.6)
  FLIPSCOUT_LOCAL       "1" for local-pickup only     (default off)
  FLIPSCOUT_ZIP         your ZIP (with local)
  FLIPSCOUT_MINUTES     handling minutes for $/hr
  FLIPSCOUT_TOP         max deals per run             (default 10)
  FLIPSCOUT_STATE_FILE  seen-cache path for dedup     (default flipscout_seen.json)
  + eBay + alert-channel vars (see ebay_api / notify).
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from .analyzer import Thresholds
from .notify import format_digest, notify


def load_config(env=None) -> dict:
    env = env if env is not None else os.environ
    raw = env.get("FLIPSCOUT_WATCHLIST", "")
    queries = [q.strip() for q in re.split(r"[\n,]", raw) if q.strip()]
    return {
        "queries": queries,
        "sources": [s.strip() for s in env.get("FLIPSCOUT_SOURCES", "ebay").split(",") if s.strip()],
        "min_profit": float(env.get("FLIPSCOUT_MIN_PROFIT", "20")),
        "min_roi": float(env.get("FLIPSCOUT_MIN_ROI", "0.6")),
        "local": env.get("FLIPSCOUT_LOCAL", "") in ("1", "true", "True"),
        "zip": env.get("FLIPSCOUT_ZIP") or None,
        "minutes": int(env["FLIPSCOUT_MINUTES"]) if env.get("FLIPSCOUT_MINUTES") else None,
        "top": int(env.get("FLIPSCOUT_TOP", "10")),
        "state_file": env.get("FLIPSCOUT_STATE_FILE", "flipscout_seen.json"),
    }


def _hit_key(h) -> str:
    return f"{h.source}:{h.url or h.title}"


def _load_seen(path: str) -> set:
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_seen(path: str, seen: set) -> None:
    try:
        # Cap the cache so it can't grow forever across many runs.
        keep = list(seen)[-5000:]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(keep, f)
    except Exception as e:
        print(f"[watch] couldn't save seen-cache: {e}")


def run_watch(config: dict, ebay=None, notifier=notify) -> dict:
    """Scan the watchlist, alert on new deals only. Returns a small result dict.
    `ebay`/`notifier` are injectable for tests."""
    if not config["queries"]:
        print("[watch] FLIPSCOUT_WATCHLIST is empty — nothing to scan.")
        return {"new": 0, "sent": [], "scanned": 0}

    from .ebay_api import EbayApiComps
    from .scanner import scan
    from .sources import build_sources

    ebay = ebay or EbayApiComps()
    sources = build_sources(config["sources"], ebay)
    hits = scan(
        config["queries"], sources, comp_source=ebay,
        thresholds=Thresholds(min_profit=config["min_profit"], min_roi=config["min_roi"]),
        local=config["local"], zip_code=config["zip"], effort_minutes=config["minutes"],
        limit_per_query=config["top"],
    )

    seen = _load_seen(config["state_file"])
    fresh = [h for h in hits if _hit_key(h) not in seen][: config["top"]]
    if not fresh:
        print(f"[watch] scanned {len(hits)} deal(s); none new.")
        return {"new": 0, "sent": [], "scanned": len(hits)}

    text = format_digest(fresh)
    sent = notifier(text)
    _save_seen(config["state_file"], seen | {_hit_key(h) for h in fresh})
    print(f"[watch] {len(fresh)} new deal(s); alerted via {sent or 'logs'}.")
    return {"new": len(fresh), "sent": sent, "scanned": len(hits)}


def main(argv: Optional[list] = None) -> int:
    run_watch(load_config())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

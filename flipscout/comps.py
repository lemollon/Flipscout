"""Comparable-sales ("comps") — what does this thing actually sell for on eBay?

The whole game of reselling is knowing the *realized* price (what items SOLD for,
not what hopeful listings ASK). eBay exposes this two ways:

  1. Free, no code: search eBay, filter to "Sold items", eyeball the median. This
     is what you do while sourcing. You type that median into the analyzer.

  2. Official API (when you get free developer keys):
       * Browse API           — active listings (asking prices, supply).
       * Marketplace Insights  — actual sold prices over the last 90 days
                                 (this is the gold; it needs an application grant).

This module defines a small provider interface so the analyzer doesn't care which
one you used. `EstimateComps` is the offline default: it trusts the sold price you
observed. `EbayApiComps` is a stub showing exactly where live calls slot in, so
adding credentials later is a drop-in, not a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Optional, Protocol, Sequence


@dataclass(frozen=True)
class Comp:
    """A verdict on what an item resells for and how liquid it is.

    sold_price     — representative realized price (median of recent solds).
    sold_count     — how many sold in the lookback window (demand).
    active_count   — how many are listed right now (supply/competition).
    source         — where the numbers came from ("manual", "ebay_insights", ...).
    low/high       — optional realistic range, for showing you the spread.
    """

    query: str
    sold_price: Optional[float]
    sold_count: Optional[int] = None
    active_count: Optional[int] = None
    source: str = "manual"
    low: Optional[float] = None
    high: Optional[float] = None

    @property
    def sell_through(self) -> Optional[float]:
        """sold / (sold + active): a rough liquidity score in [0, 1]. Higher means
        it sells fast relative to how many are competing. Needs both counts."""
        if self.sold_count is None or self.active_count is None:
            return None
        denom = self.sold_count + self.active_count
        if denom == 0:
            return None
        return self.sold_count / denom

    @property
    def has_price(self) -> bool:
        return self.sold_price is not None and self.sold_price > 0


class CompsProvider(Protocol):
    """Anything that can turn a search query into a Comp."""

    def lookup(self, query: str, observed_price: Optional[float] = None) -> Comp: ...


@dataclass
class EstimateComps:
    """Offline provider: uses the sold price you observed on eBay's free sold-search.

    This is not magic — if you don't give it a price it can't invent one, and it
    says so (has_price == False). That honesty is the point: the analyzer will flag
    the item as "needs a comp" rather than pretend to value it.

    You can also preload a table of known comps (e.g. a CSV you keep of things you
    resell often) so repeat items auto-fill.
    """

    known: dict[str, Comp] = field(default_factory=dict)

    def add(self, comp: Comp) -> None:
        self.known[comp.query.strip().lower()] = comp

    def lookup(self, query: str, observed_price: Optional[float] = None) -> Comp:
        if observed_price is not None:
            return Comp(query=query, sold_price=observed_price, source="manual")
        key = query.strip().lower()
        if key in self.known:
            return self.known[key]
        # No observed price and nothing on file — return a "priceless" comp so the
        # caller knows to go look one up rather than trust a fabricated number.
        return Comp(query=query, sold_price=None, source="unknown")


# The live eBay-API provider lives in flipscout.ebay_api.EbayApiComps (it needs the
# `requests` dependency, so it's kept out of this core module). Import it directly:
#     from flipscout.ebay_api import EbayApiComps, EbayConfig


def median_sold(prices: Sequence[float]) -> Optional[float]:
    """Median of a set of realized sold prices, ignoring non-positive junk. Handy
    when you jotted down several sold comps and want one number."""
    clean = [p for p in prices if p and p > 0]
    return median(clean) if clean else None


# ---------------------------------------------------------------------------
# Comps memory — the same models recur constantly. Research a price once, save
# it, and every future sighting is instant. This is where a lot of your time
# savings come from: your personal price book grows as you source.
# ---------------------------------------------------------------------------

import json
import os


def load_memory(path: str) -> EstimateComps:
    """Load a saved price book into an EstimateComps provider. Missing/empty file
    -> empty provider (first run just starts building it)."""
    prov = EstimateComps()
    if not path or not os.path.exists(path):
        return prov
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    for query, rec in raw.items():
        prov.add(Comp(
            query=query,
            sold_price=rec.get("sold_price"),
            sold_count=rec.get("sold_count"),
            active_count=rec.get("active_count"),
            source=rec.get("source", "memory"),
            low=rec.get("low"),
            high=rec.get("high"),
        ))
    return prov


def save_comp(path: str, comp: Comp) -> None:
    """Add/overwrite one comp in the price book on disk, keyed by lowercased title."""
    book: dict = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            book = json.load(f)
    book[comp.query.strip().lower()] = {
        "sold_price": comp.sold_price,
        "sold_count": comp.sold_count,
        "active_count": comp.active_count,
        "source": comp.source,
        "low": comp.low,
        "high": comp.high,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(book, f, indent=2, sort_keys=True)

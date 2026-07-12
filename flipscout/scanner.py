"""Arbitrage scanner — find underpriced listings and rank them by profit.

The hands-off "deals come to me" engine. For each search you give it, it pulls the
current active listings from a source, compares each to what the item *sells* for
(eBay sold median), and flags the ones priced low enough to flip after fees — ranked
best-profit-first. No browsing, no typing.

Source-agnostic by design. Today it scans **eBay itself** (buy an underpriced /
mistitled / ending-soon listing, resell at the going rate) because eBay's own API
makes that 100% allowed and reliable — the same provider that powers live comps
(`ebay_api.EbayApiComps`) also lists active items. The pipeline (candidate listings
→ comp → rank) is the same for a Craigslist-RSS or liquidation-feed source later:
implement `active_listings(query)` + `lookup(query)` and it drops right in.

This needs live data (sold prices + active listings), so it runs against the eBay
API, not estimate mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from .fees import FeeModel, net_proceeds
from .analyzer import Thresholds


class ListingSource(Protocol):
    """A source that can list active items and price them (EbayApiComps fits)."""
    def active_listings(self, query: str, limit: int = 50) -> list[dict]: ...
    def lookup(self, query: str, observed_price=None): ...


@dataclass(frozen=True)
class ScanHit:
    query: str
    title: str
    buy_price: float        # what the underpriced listing is going for
    sold_price: float       # what it resells for (comp median)
    profit: float           # net after fees, buying at buy_price and reselling at sold_price
    roi: float
    url: str
    source: str

    def summary(self) -> str:
        return (f"${self.profit:>7.2f}  ROI {self.roi:>4.0%}  "
                f"buy ${self.buy_price:>7.2f} → sell ${self.sold_price:>7.2f}  "
                f"{self.title[:44]}")


def scan_query(
    query: str,
    source: ListingSource,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
    buy_shipping: float = 0.0,
    resell_shipping: float = 0.0,
) -> list[ScanHit]:
    """Scan one search: comp it once, then flag every active listing cheap enough
    to flip. Returns hits ranked by profit (highest first)."""
    comp = source.lookup(query)
    sold = getattr(comp, "sold_price", None)
    if not sold or sold <= 0:
        return []  # no sold data -> nothing to arbitrage against

    np_ = net_proceeds(sold, fees=fees, shipping_cost=resell_shipping)
    hits: list[ScanHit] = []
    for it in source.active_listings(query):
        buy = it["price"] + buy_shipping
        profit = np_.net - buy
        roi = profit / buy if buy > 0 else float("inf")
        if profit >= thresholds.min_profit and roi >= thresholds.min_roi:
            hits.append(ScanHit(
                query=query, title=it.get("title", ""), buy_price=it["price"],
                sold_price=sold, profit=profit, roi=roi,
                url=it.get("url", ""), source=getattr(comp, "source", "ebay"),
            ))
    hits.sort(key=lambda h: h.profit, reverse=True)
    return hits


def scan(
    queries: list[str],
    source: ListingSource,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
    buy_shipping: float = 0.0,
    resell_shipping: float = 0.0,
    limit_per_query: Optional[int] = None,
) -> list[ScanHit]:
    """Scan several searches and return one merged, profit-ranked deal list."""
    all_hits: list[ScanHit] = []
    for q in queries:
        hits = scan_query(q, source, fees, thresholds, buy_shipping, resell_shipping)
        if limit_per_query is not None:
            hits = hits[:limit_per_query]
        all_hits.extend(hits)
    all_hits.sort(key=lambda h: h.profit, reverse=True)
    return all_hits

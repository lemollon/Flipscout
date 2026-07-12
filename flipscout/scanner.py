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


# Rough handling-time per flip, in minutes — buy, receive/inspect, list, pack, ship.
# Local pickup adds a drive + meetup. These are estimates; tune to your own pace.
EFFORT_SHIPPED = 20
EFFORT_LOCAL = 45


class ListingSource(Protocol):
    """A source that can list active items and price them (EbayApiComps fits)."""
    def active_listings(self, query: str, limit: int = 50, **kw) -> list[dict]: ...
    def lookup(self, query: str, observed_price=None): ...


@dataclass(frozen=True)
class ScanHit:
    query: str
    title: str
    buy_price: float        # what the underpriced listing is going for
    sold_price: float       # what it resells for (comp median)
    profit: float           # net after fees, buying at buy_price and reselling at sold_price
    roi: float
    per_hour: float         # profit ÷ your handling time — the money/labor number (#4)
    url: str
    source: str

    def summary(self) -> str:
        return (f"${self.per_hour:>6.0f}/hr  ${self.profit:>7.2f}  ROI {self.roi:>4.0%}  "
                f"buy ${self.buy_price:>7.2f} → sell ${self.sold_price:>7.2f}  "
                f"{self.title[:40]}")


def scan_query(
    query: str,
    listing_source: ListingSource,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
    buy_shipping: float = 0.0,
    resell_shipping: float = 0.0,
    local: bool = False,
    zip_code: Optional[str] = None,
    effort_minutes: Optional[int] = None,
    comp_source: Optional[object] = None,
) -> list[ScanHit]:
    """Scan one search on `listing_source` (where you BUY), comp it against
    `comp_source` (where you SELL — eBay; defaults to listing_source for the
    eBay→eBay case), and flag every listing cheap enough to flip. Ranked by
    profit-per-hour (money for the least labor)."""
    comp_source = comp_source or listing_source
    comp = comp_source.lookup(query)
    sold = getattr(comp, "sold_price", None)
    if not sold or sold <= 0:
        return []  # no sold data -> nothing to arbitrage against

    minutes = effort_minutes if effort_minutes is not None else (EFFORT_LOCAL if local else EFFORT_SHIPPED)
    where = getattr(listing_source, "name", getattr(comp, "source", "ebay"))
    np_ = net_proceeds(sold, fees=fees, shipping_cost=resell_shipping)
    hits: list[ScanHit] = []
    for it in listing_source.active_listings(query, local=local, zip_code=zip_code):
        buy = it["price"] + buy_shipping
        profit = np_.net - buy
        roi = profit / buy if buy > 0 else float("inf")
        if profit >= thresholds.min_profit and roi >= thresholds.min_roi:
            hits.append(ScanHit(
                query=query, title=it.get("title", ""), buy_price=it["price"],
                sold_price=sold, profit=profit, roi=roi,
                per_hour=profit * 60.0 / minutes,
                url=it.get("url", ""), source=where,
            ))
    hits.sort(key=lambda h: h.per_hour, reverse=True)
    return hits


def scan(
    queries: list[str],
    sources,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
    buy_shipping: float = 0.0,
    resell_shipping: float = 0.0,
    local: bool = False,
    zip_code: Optional[str] = None,
    effort_minutes: Optional[int] = None,
    limit_per_query: Optional[int] = None,
    comp_source: Optional[object] = None,
) -> list[ScanHit]:
    """Scan several searches across one or more buying `sources` → a single merged
    inventory of deals, each tagged with where to buy, ranked by profit-per-hour.
    `comp_source` (eBay) prices resale for every source; defaults to the first
    source that can price itself."""
    src_list = list(sources) if isinstance(sources, (list, tuple)) else [sources]
    if comp_source is None:
        comp_source = next((s for s in src_list if hasattr(s, "lookup")), src_list[0])
    all_hits: list[ScanHit] = []
    for s in src_list:
        for q in queries:
            hits = scan_query(q, s, fees, thresholds, buy_shipping, resell_shipping,
                              local=local, zip_code=zip_code, effort_minutes=effort_minutes,
                              comp_source=comp_source)
            if limit_per_query is not None:
                hits = hits[:limit_per_query]
            all_hits.extend(hits)
    all_hits.sort(key=lambda h: h.per_hour, reverse=True)
    return all_hits

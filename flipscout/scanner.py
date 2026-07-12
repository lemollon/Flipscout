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
    source: ListingSource,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
    buy_shipping: float = 0.0,
    resell_shipping: float = 0.0,
    local: bool = False,
    zip_code: Optional[str] = None,
    effort_minutes: Optional[int] = None,
) -> list[ScanHit]:
    """Scan one search: comp it once, then flag every active listing cheap enough
    to flip. Ranked by profit-per-hour (money for the least labor). `local=True`
    asks the source for local-pickup listings near `zip_code`."""
    comp = source.lookup(query)
    sold = getattr(comp, "sold_price", None)
    if not sold or sold <= 0:
        return []  # no sold data -> nothing to arbitrage against

    minutes = effort_minutes if effort_minutes is not None else (EFFORT_LOCAL if local else EFFORT_SHIPPED)
    np_ = net_proceeds(sold, fees=fees, shipping_cost=resell_shipping)
    hits: list[ScanHit] = []
    for it in source.active_listings(query, local=local, zip_code=zip_code):
        buy = it["price"] + buy_shipping
        profit = np_.net - buy
        roi = profit / buy if buy > 0 else float("inf")
        if profit >= thresholds.min_profit and roi >= thresholds.min_roi:
            hits.append(ScanHit(
                query=query, title=it.get("title", ""), buy_price=it["price"],
                sold_price=sold, profit=profit, roi=roi,
                per_hour=profit * 60.0 / minutes,
                url=it.get("url", ""), source=getattr(comp, "source", "ebay"),
            ))
    hits.sort(key=lambda h: h.per_hour, reverse=True)
    return hits


def scan(
    queries: list[str],
    source: ListingSource,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
    buy_shipping: float = 0.0,
    resell_shipping: float = 0.0,
    local: bool = False,
    zip_code: Optional[str] = None,
    effort_minutes: Optional[int] = None,
    limit_per_query: Optional[int] = None,
) -> list[ScanHit]:
    """Scan several searches → one merged inventory of buyable deals, ranked by
    profit-per-hour (best money-for-least-labor first)."""
    all_hits: list[ScanHit] = []
    for q in queries:
        hits = scan_query(q, source, fees, thresholds, buy_shipping, resell_shipping,
                          local=local, zip_code=zip_code, effort_minutes=effort_minutes)
        if limit_per_query is not None:
            hits = hits[:limit_per_query]
        all_hits.extend(hits)
    all_hits.sort(key=lambda h: h.per_hour, reverse=True)
    return all_hits

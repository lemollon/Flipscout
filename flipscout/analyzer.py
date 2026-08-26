"""The decision layer — turn "I found this for $X" into "buy it / skip it / go comp it".

Given what you'd pay locally and what it sells for on eBay, this computes:
    net profit   = eBay net proceeds - what you paid - other costs
    ROI          = net profit / total cash you tie up
    margin       = net profit / sale price
and, when you have the counts, sell-through (how fast it moves). Then it renders a
plain-English verdict against thresholds you can tune to your own risk appetite.

The thresholds default to conservative beginner-friendly numbers: don't tie up cash
in something that only clears a few bucks or barely beats break-even, and prefer
items that actually sell.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from .comps import Comp, CompsProvider, EstimateComps
from .fees import FeeModel, net_proceeds


class Verdict(str, Enum):
    BUY = "BUY"                 # clears the profit + ROI bar, sells well enough
    MAYBE = "MAYBE"            # profitable but thin, slow, or borderline
    SKIP = "SKIP"             # loses money or too little upside for the cash/effort
    NEEDS_COMP = "NEEDS_COMP"  # no sold price yet — go look one up before deciding


def est_days_to_sell(sold_count, active_count, window_days: int = 90):
    """Rough days for a fresh listing to sell: the queue of active listings divided
    by the recent sales rate (sold in `window_days`). High supply + low sales = slow.
    An estimate (ignores your price/quality), but directionally honest. None if the
    counts aren't both known or nothing has sold."""
    if not sold_count or active_count is None:
        return None
    per_day = sold_count / window_days
    if per_day <= 0:
        return None
    return active_count / per_day


@dataclass(frozen=True)
class Thresholds:
    """What separates a BUY from a MAYBE from a SKIP. All tunable."""

    min_profit: float = 10.0        # dollars of net profit to bother at all
    min_roi: float = 0.50           # 50% return on the cash you tie up
    good_profit: float = 20.0       # comfortably worth the effort
    good_roi: float = 1.00          # doubles your money
    min_sell_through: float = 0.30  # if known, want it to actually move


@dataclass
class Candidate:
    """One thing you're thinking about buying to flip.

    title           — what it is (also the eBay search query).
    source_price    — what you'd pay locally (the asking price you'd haggle from).
    observed_price  — median SOLD price you saw on eBay (estimate mode). Optional.
    shipping_cost   — postage YOU'll pay to ship it. Estimate if unsure.
    shipping_charged— postage you'll charge the buyer (0 = free shipping, common).
    extra_cost      — supplies, gas, refurb, cleaning — anything else per item.
    sold_count/active_count — from eBay's sold vs active filters, for sell-through.
    days_to_sell    — YOUR estimate of how long it takes to sell, in days. Beats
                      the sold/active estimate when you know the item (e.g. the
                      last three sold in a week). Feeds flipscout.velocity.
    """

    title: str
    source_price: float
    observed_price: Optional[float] = None
    shipping_cost: float = 0.0
    shipping_charged: float = 0.0
    extra_cost: float = 0.0
    sold_count: Optional[int] = None
    active_count: Optional[int] = None
    days_to_sell: Optional[float] = None


@dataclass(frozen=True)
class DealAnalysis:
    candidate: Candidate
    comp: Comp
    sale_price: Optional[float]
    total_cost: Optional[float]     # source + shipping you pay + extras
    total_fees: Optional[float]
    net_profit: Optional[float]
    roi: Optional[float]            # net_profit / total_cost
    margin: Optional[float]         # net_profit / sale_price
    sell_through: Optional[float]
    verdict: Verdict
    days_to_sell: Optional[float] = None   # rough estimate; None if counts unknown
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        c = self.candidate
        if self.verdict is Verdict.NEEDS_COMP:
            return (f"{self.verdict.value:>10}  {c.title[:40]:40}  "
                    f"paid ${c.source_price:>7.2f}  -> look up a sold comp on eBay")
        st = f"{self.sell_through:>4.0%}" if self.sell_through is not None else "  - "
        days = f"~{self.days_to_sell:>3.0f}d" if self.days_to_sell is not None else "   - "
        return (f"{self.verdict.value:>10}  {c.title[:40]:40}  "
                f"buy ${c.source_price:>7.2f}  sell ${self.sale_price:>7.2f}  "
                f"profit ${self.net_profit:>7.2f}  ROI {self.roi:>5.0%}  ST {st}  {days}")


def analyze(
    candidate: Candidate,
    provider: Optional[CompsProvider] = None,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
) -> DealAnalysis:
    """Score a single candidate. Uses the observed price if given, else asks the
    provider (which, in estimate mode, may have nothing — that's a NEEDS_COMP)."""
    provider = provider or EstimateComps()
    comp = provider.lookup(candidate.title, observed_price=candidate.observed_price)

    # Carry counts from the candidate into the comp if the provider didn't supply them.
    if comp.sold_count is None and candidate.sold_count is not None:
        comp = Comp(query=comp.query, sold_price=comp.sold_price, source=comp.source,
                    sold_count=candidate.sold_count, active_count=candidate.active_count,
                    low=comp.low, high=comp.high)

    notes: list[str] = []

    if not comp.has_price:
        notes.append("No sold price yet. Search eBay, filter to 'Sold items', "
                     "take the median, and pass it as observed_price.")
        return DealAnalysis(
            candidate=candidate, comp=comp, sale_price=None, total_cost=None,
            total_fees=None, net_profit=None, roi=None, margin=None,
            sell_through=comp.sell_through, verdict=Verdict.NEEDS_COMP, notes=notes,
        )

    sale_price = comp.sold_price
    np_ = net_proceeds(
        sale_price=sale_price,
        fees=fees,
        shipping_cost=candidate.shipping_cost,
        shipping_charged=candidate.shipping_charged,
    )

    total_cost = candidate.source_price + candidate.shipping_cost + candidate.extra_cost
    # net_proceeds already subtracts shipping_cost, so profit = its net minus what
    # you paid for the item and any extras (shipping isn't double-counted).
    net_profit = np_.net - candidate.source_price - candidate.extra_cost
    roi = net_profit / total_cost if total_cost > 0 else float("inf")
    margin = net_profit / sale_price if sale_price > 0 else 0.0
    sell_through = comp.sell_through
    # Your own read on how fast it moves beats the supply/demand estimate: the
    # counts estimate ignores price and condition, you don't.
    days = candidate.days_to_sell
    if days is None:
        days = est_days_to_sell(comp.sold_count, comp.active_count)

    verdict = _decide(net_profit, roi, sell_through, thresholds, notes)
    if days is not None and days > 60 and verdict is Verdict.BUY:
        verdict = Verdict.MAYBE
        notes.append(f"Estimated ~{days:.0f} days to sell — you'd sit on it a while.")

    return DealAnalysis(
        candidate=candidate, comp=comp, sale_price=sale_price, total_cost=total_cost,
        total_fees=np_.total_fees, net_profit=net_profit, roi=roi, margin=margin,
        sell_through=sell_through, verdict=verdict, days_to_sell=days, notes=notes,
    )


def _decide(net_profit, roi, sell_through, t: Thresholds, notes: list[str]) -> Verdict:
    if net_profit <= 0:
        notes.append("Loses money after fees and shipping.")
        return Verdict.SKIP
    if net_profit < t.min_profit or roi < t.min_roi:
        notes.append(f"Below the bar (want >= ${t.min_profit:.0f} profit and "
                     f">= {t.min_roi:.0%} ROI).")
        return Verdict.SKIP

    slow = sell_through is not None and sell_through < t.min_sell_through
    if slow:
        notes.append(f"Sells slowly (sell-through {sell_through:.0%} < "
                     f"{t.min_sell_through:.0%}). Cash may sit a while.")

    if net_profit >= t.good_profit and roi >= t.good_roi and not slow:
        return Verdict.BUY
    if slow:
        return Verdict.MAYBE
    return Verdict.BUY if (net_profit >= t.min_profit and roi >= t.min_roi) else Verdict.MAYBE


# ---------------------------------------------------------------------------
# Walk-away price: the fast question. "It sells for $X — what's the MOST I can
# pay and still hit my profit + ROI goal?" Answer this at a glance and you never
# have to run a full analysis in the aisle; you just compare the sticker to your
# ceiling and haggle toward it.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaxPay:
    sale_price: float
    max_price: float          # highest source price that still clears both bars
    binding: str              # which constraint set the ceiling: "profit" | "roi" | "none"
    at_max_profit: float      # net profit if you pay exactly max_price
    at_max_roi: float         # ROI if you pay exactly max_price

    def summary(self) -> str:
        if self.max_price <= 0:
            return (f"WALK AWAY  sells ${self.sale_price:.2f}  -> can't hit the goal at "
                    f"any price above $0 (fees/shipping eat it)")
        return (f"PAY <= ${self.max_price:>7.2f}  to flip a ${self.sale_price:.2f} item "
                f"(then ~${self.at_max_profit:.2f} profit, {self.at_max_roi:.0%} ROI; "
                f"limited by {self.binding})")


def max_pay(
    sale_price: float,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
    shipping_cost: float = 0.0,
    shipping_charged: float = 0.0,
    extra_cost: float = 0.0,
) -> MaxPay:
    """Highest price to pay for an item so it still meets BOTH min_profit and
    min_roi. Solve each constraint for source_price, take the tighter one.

    Let N = net proceeds of the sale (already nets out YOUR shipping). With
    profit = N - source - extra and total_cost = source + shipping_cost + extra:
        profit >= min_profit          ->  source <= N - extra - min_profit
        profit >= min_roi*total_cost  ->  source <= (N - extra - r*(ship+extra))/(1+r)
    """
    np_ = net_proceeds(sale_price, fees=fees,
                       shipping_cost=shipping_cost, shipping_charged=shipping_charged)
    n = np_.net
    r = thresholds.min_roi

    cap_profit = n - extra_cost - thresholds.min_profit
    cap_roi = (n - extra_cost - r * (shipping_cost + extra_cost)) / (1 + r)

    if cap_profit <= cap_roi:
        binding, max_price = "profit", cap_profit
    else:
        binding, max_price = "roi", cap_roi
    max_price = max(0.0, max_price)
    if max_price <= 0:
        binding = "none"

    # Profit/ROI you'd actually realize paying exactly the ceiling.
    total = max_price + shipping_cost + extra_cost
    profit = n - max_price - extra_cost
    roi = profit / total if total > 0 else 0.0
    return MaxPay(sale_price=sale_price, max_price=max_price, binding=binding,
                  at_max_profit=profit, at_max_roi=roi)


# ---------------------------------------------------------------------------
# CSV workflow: keep a spreadsheet of candidates while you source, score it all.
# ---------------------------------------------------------------------------

_CSV_FIELDS = {
    "title", "source_price", "observed_price", "shipping_cost",
    "shipping_charged", "extra_cost", "sold_count", "active_count",
    "days_to_sell",
}


def _num(row: dict, key: str, cast=float) -> Optional[float]:
    v = (row.get(key) or "").strip()
    if v == "":
        return None
    return cast(v)


def candidates_from_csv(path: str) -> list[Candidate]:
    """Read candidates from a CSV. Required column: title, source_price. Optional:
    observed_price, shipping_cost, shipping_charged, extra_cost, sold_count,
    active_count, days_to_sell. Blank cells are treated as unknown."""
    out: list[Candidate] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not (row.get("title") or "").strip():
                continue
            out.append(Candidate(
                title=row["title"].strip(),
                source_price=_num(row, "source_price") or 0.0,
                observed_price=_num(row, "observed_price"),
                shipping_cost=_num(row, "shipping_cost") or 0.0,
                shipping_charged=_num(row, "shipping_charged") or 0.0,
                extra_cost=_num(row, "extra_cost") or 0.0,
                sold_count=_num(row, "sold_count", int),
                active_count=_num(row, "active_count", int),
                days_to_sell=_num(row, "days_to_sell"),
            ))
    return out


def analyze_csv(
    path: str,
    provider: Optional[CompsProvider] = None,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
) -> list[DealAnalysis]:
    """Score every candidate in a CSV, best deals first (BUY > MAYBE > SKIP >
    NEEDS_COMP, then by net profit)."""
    results = [analyze(c, provider, fees, thresholds) for c in candidates_from_csv(path)]
    rank = {Verdict.BUY: 0, Verdict.MAYBE: 1, Verdict.SKIP: 2, Verdict.NEEDS_COMP: 3}
    results.sort(key=lambda r: (rank[r.verdict], -(r.net_profit or -1e9)))
    return results

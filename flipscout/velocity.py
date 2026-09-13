"""flipscout.velocity — the high-frequency layer: profit per dollar per DAY.

Everything else in this package answers "is this item profitable?". That is the
wrong question once you have more deals than money, which happens roughly a week
into sourcing seriously. The right question is the one a trading desk asks:

    how much does each dollar of my capital earn, per day it is tied up?

A $200 profit on a $400 buy that takes eight months to sell is a WORSE use of
$400 than a $25 profit on a $40 buy that clears in three weeks — the second one
recycles that $40 seventeen times a year. Margin flatters slow inventory; only
velocity exposes it. This module makes velocity the unit of comparison:

    velocity = net_profit / total_cost / hold_days        ($ per $ per day)

reported as **$ per $100 per day** because that is a number a human can hold in
their head ($1.00/$100/day is roughly a 365% simple annual return on that slot).

Three things follow from taking that seriously, and all three live here:

  1. HOLD DAYS ARE NOT DAYS-TO-SELL. Your cash is dead from the moment you hand
     over the money to the moment eBay pays out — prep + listing time + transit +
     the payout hold. `CycleModel` carries those, and they routinely add two
     weeks to a "sells in 9 days" item.

  2. CAPITAL IS NOT THE ONLY BUDGET. Every flip costs the same ~25 minutes of
     handling whether it clears $8 or $80, so a tiny fast flip can have a superb
     velocity and still be a bad trade. `VelocityThresholds.min_hourly` is the
     labor floor that kills those, and `allocate()` treats hours as a hard budget
     alongside the bankroll.

  3. THE BINDING CONSTRAINT IS THE WHOLE ANSWER. If you run out of money first,
     find cheaper/faster flips. If you run out of hours first, buy fewer, bigger
     ones. If you run out of DEALS first, more of both is wasted — go source.
     `allocate()` names which one bound you.

Nothing here re-derives the fee math: it composes `analyzer.analyze`, so a flip's
profit is computed exactly once, in one place, with one fee model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List, Optional, Sequence, Tuple

from .analyzer import Candidate, DealAnalysis, Thresholds, Verdict, analyze
from .comps import CompsProvider
from .fees import FeeModel, net_proceeds

DAYS_PER_YEAR = 365.0


def _fmt_v(x: Optional[float], width: int = 6) -> str:
    """Velocity, or "∞" for the $0-at-risk case. (The web app renders an infinite
    ROI the same way, and `flipscout/__init__` already forces a UTF-8 console, so
    the symbol is safe on Windows too.)"""
    if x is None:
        return "-".rjust(width)
    if x == float("inf"):
        return "∞".rjust(width)
    return f"{x:>{width},.2f}"


# ---------------------------------------------------------------------------
# The operating model — how long a dollar is actually dead, and what a flip
# costs you in labor. These are the assumptions everything else is measured
# against, so they are one editable object rather than magic numbers inline.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CycleModel:
    """The dead-cash timeline around a sale, plus the per-flip labor cost.

    prep_days     — buy -> listed: clean, test, photograph, measure, write it up.
                    2 days is an honest weekday-evening pace for one item.
    ship_days     — sold -> delivered. eBay's clock, not yours.
    payout_days   — delivered -> money spendable. eBay releases funds after
                    delivery confirmation; new sellers see longer holds.
    default_days_to_sell — used when nothing (counts, ledger, your own guess)
                    tells us how fast it moves. Deliberately pessimistic: an
                    unknown item is a slow item until proven otherwise.
    handle_minutes— your hands-on time per flip: prep + packing + the post
                    office. Nearly constant across items, which is exactly why
                    cheap flips lose on a per-hour basis.
    """

    prep_days: float = 2.0
    ship_days: float = 3.0
    payout_days: float = 2.0
    default_days_to_sell: float = 45.0
    handle_minutes: float = 25.0

    @property
    def overhead_days(self) -> float:
        """Dead days that happen no matter how fast the thing sells."""
        return self.prep_days + self.ship_days + self.payout_days

    def hold_days(self, days_to_sell: Optional[float]) -> float:
        """Total days your cash is in the item. `None` -> the pessimistic default."""
        dts = self.default_days_to_sell if days_to_sell is None else max(0.0, days_to_sell)
        return self.overhead_days + dts


# A same-day-listing, everything-goes-right timeline. Useful as the optimistic
# bookend: if a deal is not HOT even under FAST, it is not a fast flip.
FAST = CycleModel(prep_days=0.5, ship_days=2.0, payout_days=1.0,
                  default_days_to_sell=21.0, handle_minutes=20.0)


class Tier(str, Enum):
    """Velocity verdicts. Deliberately NOT the same words as `Verdict` — an item
    can be a perfectly good BUY and still be a DEAD slot for your cash."""

    HOT = "HOT"                # top-shelf use of a dollar; buy it before it's gone
    GOOD = "GOOD"              # solidly beats parking the cash
    SLOW = "SLOW"              # real profit, but this dollar could work harder
    DEAD = "DEAD"              # loses money, or ties up cash for a rounding error
    NEEDS_COMP = "NEEDS_COMP"  # no sold price -> nothing to divide by


# Tier order for sorting/reporting: best first.
_TIER_RANK = {Tier.HOT: 0, Tier.GOOD: 1, Tier.SLOW: 2, Tier.DEAD: 3, Tier.NEEDS_COMP: 4}


@dataclass(frozen=True)
class VelocityThresholds:
    """Where the tier lines fall, in $ per $100 per day.

    The anchors: a reseller who turns inventory 4x a year at 100% ROI is doing
    well, and that is ~$1.10/$100/day. So `good` sits just under a "doing well"
    pace, `hot` at roughly double it, and `slow` marks the point where the flip
    is barely beating the effort of owning it.

    The two floors are the labor reality check. `min_profit` kills the $3 flip
    that technically turns in a week; `min_hourly` kills anything that pays less
    than a normal side job for the same 25 minutes of packing.
    """

    hot: float = 2.00        # $/$100/day  (~730% simple annual on the slot)
    good: float = 0.75       #             (~274%)
    slow: float = 0.25       #             (~91%) — below this it is DEAD
    min_profit: float = 8.0  # dollars; fewer isn't worth the handling
    min_hourly: float = 20.0 # dollars per hour of YOUR time


@dataclass(frozen=True)
class VelocityAnalysis:
    """One deal, scored on capital velocity rather than margin."""

    deal: DealAnalysis
    hold_days: float
    days_to_sell: Optional[float]
    days_assumed: bool             # True when default_days_to_sell had to be used
    velocity: Optional[float]      # net profit per DOLLAR of cost per day
    per_100_per_day: Optional[float]
    turns_per_year: Optional[float]
    annual_return: Optional[float] # simple: roi x turns (no compounding claimed)
    handle_minutes: float
    hourly: Optional[float]        # net profit per hour of hands-on work
    profit_per_day: Optional[float]# $/day this slot generates while held
    tier: Tier
    notes: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.deal.candidate.title

    @property
    def cost(self) -> Optional[float]:
        return self.deal.total_cost

    def summary(self) -> str:
        c = self.deal.candidate
        if self.tier is Tier.NEEDS_COMP:
            return (f"{self.tier.value:>10}  {self.title[:38]:38}  "
                    f"buy ${c.source_price:>7.2f}  -> look up a sold comp first")
        star = "*" if self.days_assumed else " "
        return (f"{self.tier.value:>10}  {self.title[:38]:38}  "
                f"buy ${c.source_price:>7.2f}  profit ${self.deal.net_profit:>7.2f}  "
                f"hold {self.hold_days:>4.0f}d{star} "
                f"${_fmt_v(self.per_100_per_day)}/$100/day  "
                f"{self.turns_per_year:>4.1f} turns/yr  "
                f"${self.hourly:>6.2f}/hr")

    def detail(self) -> str:
        """The full readout for a single item."""
        c = self.deal.candidate
        L = [f"{self.title}", f"  verdict      {self.deal.verdict.value} / velocity {self.tier.value}"]
        if self.tier is Tier.NEEDS_COMP:
            L.append("  no sold comp — search eBay, filter to 'Sold items', take the median")
            return "\n".join(L)
        L += [
            f"  buy          ${c.source_price:,.2f}"
            + (f"  (+${c.shipping_cost + c.extra_cost:,.2f} ship/extras)"
               if (c.shipping_cost + c.extra_cost) else ""),
            f"  sells for    ${self.deal.sale_price:,.2f}   fees ${self.deal.total_fees:,.2f}",
            f"  net profit   ${self.deal.net_profit:,.2f}   ROI {self.deal.roi:.0%}",
            f"  hold         {self.hold_days:.0f} days  "
            f"({self._sell_note} to sell + prep/ship/payout)",
            f"  velocity     ${_fmt_v(self.per_100_per_day, 0)} per $100 per day"
            f"   ({self.turns_per_year:.1f} turns/yr, "
            f"{_fmt_v(self.annual_return, 0) if self.annual_return == float('inf') else '%.0f%%' % (self.annual_return * 100)}"
            f"/yr simple)",
            f"  your time    {self.handle_minutes:.0f} min -> ${self.hourly:,.2f}/hour",
        ]
        for n in self.notes:
            L.append(f"  - {n}")
        return "\n".join(L)

    @property
    def _sell_note(self) -> str:
        if self.days_to_sell is None:
            return "?"
        return f"~{self.days_to_sell:.0f}d" + (" ASSUMED" if self.days_assumed else "")


def _tier(velocity_100: Optional[float], net_profit: Optional[float],
          hourly: Optional[float], t: VelocityThresholds,
          notes: List[str]) -> Tier:
    """Tier from velocity, then capped by the labor floors.

    Order matters: velocity decides the ceiling, the floors can only pull it
    down. That way a fast $4 flip cannot be dressed up as HOT by arithmetic —
    dividing a tiny profit by a tiny hold time makes a big ratio and a bad day.
    """
    if velocity_100 is None or net_profit is None:
        return Tier.NEEDS_COMP
    if net_profit <= 0:
        notes.append("Loses money after fees and shipping — velocity is moot.")
        return Tier.DEAD
    if velocity_100 < t.slow:
        notes.append(f"Only ${velocity_100:.2f} per $100 per day — this dollar is "
                     f"asleep. Cheaper, faster inventory beats it.")
        return Tier.DEAD

    if velocity_100 >= t.hot:
        tier = Tier.HOT
    elif velocity_100 >= t.good:
        tier = Tier.GOOD
    else:
        tier = Tier.SLOW

    if net_profit < t.min_profit:
        notes.append(f"Turns fast but only clears ${net_profit:.2f} — under the "
                     f"${t.min_profit:.0f} floor, the handling eats it.")
        tier = Tier.SLOW
    if hourly is not None and hourly < t.min_hourly:
        notes.append(f"${hourly:.2f}/hour of your time (floor ${t.min_hourly:.0f}). "
                     f"Same 25 minutes as a bigger flip.")
        tier = Tier.SLOW
    return tier


def score(
    deal: DealAnalysis,
    days_to_sell: Optional[float] = None,
    cycle: CycleModel = CycleModel(),
    thresholds: VelocityThresholds = VelocityThresholds(),
    handle_minutes: Optional[float] = None,
) -> VelocityAnalysis:
    """Add the velocity view to an already-analyzed deal.

    days_to_sell — your own estimate, in days. Omitted, it falls back to the
    deal's own estimate (from the eBay sold/active counts) and then to the
    pessimistic `cycle.default_days_to_sell`.
    """
    dts = days_to_sell if days_to_sell is not None else deal.days_to_sell
    assumed = dts is None
    hold = cycle.hold_days(dts)
    minutes = cycle.handle_minutes if handle_minutes is None else handle_minutes
    notes: List[str] = []

    if deal.verdict is Verdict.NEEDS_COMP or deal.net_profit is None:
        return VelocityAnalysis(
            deal=deal, hold_days=hold, days_to_sell=dts, days_assumed=assumed,
            velocity=None, per_100_per_day=None, turns_per_year=None,
            annual_return=None, handle_minutes=minutes, hourly=None,
            profit_per_day=None, tier=Tier.NEEDS_COMP, notes=notes,
        )

    # hold is always > 0: overhead_days is positive for any sane CycleModel, but
    # a caller can zero it out, and dividing by zero here would be a crash in the
    # aisle. Clamp to a fraction of a day instead.
    hold_safe = max(hold, 0.25)
    if deal.total_cost and deal.total_cost > 0:
        velocity = deal.net_profit / deal.total_cost / hold_safe
    else:
        # A gifted or curb-found item: no capital at risk at all, so return ON
        # capital is unbounded and the only real question is whether it is worth
        # the evening. `inf` is the honest answer, and the labor floor below is
        # what actually decides the tier.
        velocity = float("inf") if deal.net_profit > 0 else 0.0
        notes.append("You have $0 at risk — this is bounded by your time, not "
                     "your money.")
    per_100 = velocity * 100.0
    turns = DAYS_PER_YEAR / hold_safe
    annual = (deal.roi or 0.0) * turns
    hourly = deal.net_profit / (minutes / 60.0) if minutes > 0 else None
    per_day = deal.net_profit / hold_safe

    if assumed:
        notes.append(f"No sell-speed data — assumed {cycle.default_days_to_sell:.0f} "
                     f"days to sell. Pass --days-to-sell or the sold/active counts "
                     f"to replace the guess.")

    tier = _tier(per_100, deal.net_profit, hourly, thresholds, notes)

    return VelocityAnalysis(
        deal=deal, hold_days=hold, days_to_sell=dts, days_assumed=assumed,
        velocity=velocity, per_100_per_day=per_100, turns_per_year=turns,
        annual_return=annual, handle_minutes=minutes, hourly=hourly,
        profit_per_day=per_day, tier=tier, notes=notes,
    )


def score_candidate(
    candidate: Candidate,
    provider: Optional[CompsProvider] = None,
    fees: FeeModel = FeeModel(),
    thresholds: Thresholds = Thresholds(),
    cycle: CycleModel = CycleModel(),
    velocity_thresholds: VelocityThresholds = VelocityThresholds(),
    days_to_sell: Optional[float] = None,
    handle_minutes: Optional[float] = None,
) -> VelocityAnalysis:
    """analyze() + score() in one call — the usual entry point."""
    deal = analyze(candidate, provider=provider, fees=fees, thresholds=thresholds)
    if days_to_sell is None:
        days_to_sell = candidate.days_to_sell
    return score(deal, days_to_sell=days_to_sell, cycle=cycle,
                 thresholds=velocity_thresholds, handle_minutes=handle_minutes)


def rank(analyses: Iterable[VelocityAnalysis]) -> List[VelocityAnalysis]:
    """Best use of the next dollar first: tier, then raw velocity."""
    return sorted(analyses,
                  key=lambda a: (_TIER_RANK[a.tier], -(a.velocity or -1e9)))


# ---------------------------------------------------------------------------
# The in-the-aisle question, restated for velocity: "it sells for $X and takes
# about N days — what's the most I can pay and still have this dollar working?"
#
# The identity worth internalizing: requiring a velocity of v over d hold days
# is EXACTLY requiring an ROI of (v * d). Velocity isn't a new kind of math, it
# is ROI with the clock finally attached to it.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaxPayVelocity:
    sale_price: float
    hold_days: float
    target_per_100_per_day: float
    required_roi: float        # the ROI that target implies over this hold
    max_price: float
    binding: str               # "velocity" | "profit" | "none"
    at_max_profit: float
    at_max_roi: float

    def summary(self) -> str:
        if self.max_price <= 0:
            return (f"WALK AWAY  a ${self.sale_price:,.2f} item held "
                    f"{self.hold_days:.0f}d can't earn "
                    f"${self.target_per_100_per_day:.2f}/$100/day at any price")
        return (f"PAY <= ${self.max_price:>7.2f}  for a ${self.sale_price:,.2f} item "
                f"you'll hold ~{self.hold_days:.0f}d at "
                f"${self.target_per_100_per_day:.2f}/$100/day "
                f"(needs {self.required_roi:.0%} ROI; then ~${self.at_max_profit:.2f} "
                f"profit; limited by {self.binding})")


def max_pay_for_velocity(
    sale_price: float,
    days_to_sell: Optional[float] = None,
    target_per_100_per_day: float = VelocityThresholds().good,
    fees: FeeModel = FeeModel(),
    cycle: CycleModel = CycleModel(),
    thresholds: VelocityThresholds = VelocityThresholds(),
    shipping_cost: float = 0.0,
    shipping_charged: float = 0.0,
    extra_cost: float = 0.0,
) -> MaxPayVelocity:
    """Highest source price that still hits a velocity target over the real hold.

    With N = net proceeds (already net of YOUR postage), s = source price,
    e = extras, k = shipping you pay, cost = s + k + e and required ROI r = v*d:

        profit = N - s - e >= r * (s + k + e)
        =>  s <= (N - e - r*(k + e)) / (1 + r)

    The dollar floor (`thresholds.min_profit`) is applied as a second ceiling and
    the tighter of the two wins — same shape as `analyzer.max_pay`, which is the
    point: this is that function with time in it.
    """
    hold = max(cycle.hold_days(days_to_sell), 0.25)
    v = target_per_100_per_day / 100.0
    r = v * hold

    np_ = net_proceeds(sale_price, fees=fees,
                       shipping_cost=shipping_cost, shipping_charged=shipping_charged)
    n = np_.net

    cap_velocity = (n - extra_cost - r * (shipping_cost + extra_cost)) / (1 + r)
    cap_profit = n - extra_cost - thresholds.min_profit

    if cap_profit <= cap_velocity:
        binding, max_price = "profit", cap_profit
    else:
        binding, max_price = "velocity", cap_velocity
    max_price = max(0.0, max_price)
    if max_price <= 0:
        binding = "none"

    total = max_price + shipping_cost + extra_cost
    profit = n - max_price - extra_cost
    roi = profit / total if total > 0 else 0.0
    return MaxPayVelocity(
        sale_price=sale_price, hold_days=hold,
        target_per_100_per_day=target_per_100_per_day, required_roi=r,
        max_price=max_price, binding=binding, at_max_profit=profit, at_max_roi=roi,
    )


# ---------------------------------------------------------------------------
# Allocation — the actual high-frequency question. You have a bankroll and a
# finite number of evenings. Which of tonight's candidates do you actually buy?
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Portfolio:
    """A concrete shopping plan for one bankroll and one week's labor."""

    bought: List[VelocityAnalysis]
    skipped: List[Tuple[VelocityAnalysis, str]]
    bankroll: float
    capital_used: float
    hours_available: float
    hours_used: float
    profit_total: float        # profit once every position closes
    profit_per_week: float     # run-rate while the positions are held
    binding: str               # "capital" | "labor" | "deal flow"

    @property
    def capital_free(self) -> float:
        return round(self.bankroll - self.capital_used, 2)

    @property
    def blended_per_100_per_day(self) -> Optional[float]:
        """Velocity of the plan as a whole: profit over total capital-DAYS. The
        right way to average velocities — a big slow position drags more than a
        small one, which a mean of the per-item numbers would hide."""
        capital_days = sum((a.cost or 0.0) * a.hold_days for a in self.bought)
        if capital_days <= 0:
            return None
        return self.profit_total / capital_days * 100.0

    def summary(self) -> str:
        L = [f"PLAN  {len(self.bought)} buy(s), ${self.capital_used:,.2f} of "
             f"${self.bankroll:,.2f} deployed, {self.hours_used:.1f} of "
             f"{self.hours_available:.1f} hours"]
        for a in self.bought:
            L.append("  " + a.summary())
        blended = self.blended_per_100_per_day
        L.append(f"  -> ${self.profit_total:,.2f} profit when it all clears; "
                 f"${self.profit_per_week:,.2f}/week run-rate"
                 + (f"; ${blended:,.2f}/$100/day blended" if blended else ""))
        L.append(f"  -> BINDING CONSTRAINT: {self.binding}. {_BINDING_ADVICE[self.binding]}")
        if self.skipped:
            L.append(f"  skipped {len(self.skipped)}:")
            for a, why in self.skipped[:10]:
                L.append(f"    {a.title[:40]:40} {why}")
            if len(self.skipped) > 10:
                L.append(f"    ... and {len(self.skipped) - 10} more")
        return "\n".join(L)


_BINDING_ADVICE = {
    "capital": "You ran out of money before deals. Favour cheaper, faster flips "
               "(or sell what's sitting) — more sourcing won't help today.",
    "labor": "You ran out of hours before money. Buy fewer, bigger-ticket items: "
             "same 25 minutes each, more dollars per minute.",
    "deal flow": "You bought everything that qualified and still have money and "
                 "hours left. The bottleneck is FINDING deals, not funding them.",
}


def allocate(
    analyses: Sequence[VelocityAnalysis],
    bankroll: float,
    hours: float = 8.0,
    min_tier: Tier = Tier.GOOD,
) -> Portfolio:
    """Greedy fill of a bankroll, best velocity first, under a labor budget.

    Greedy-by-velocity is the right rule here and not just a convenience: with a
    fixed capital budget, taking the highest profit-per-dollar-per-day first is
    optimal for the fractional problem and near-optimal for whole items, because
    sourcing candidates are small relative to a bankroll. Where it can lose a few
    dollars — one big item crowding out two better small ones — the answer is to
    re-run with a different bankroll, not to pretend a knapsack solver would make
    you a better buyer.

    A skipped item is skipped with a REASON, because "why didn't it buy this"
    is the question you will actually have.
    """
    bought: List[VelocityAnalysis] = []
    skipped: List[Tuple[VelocityAnalysis, str]] = []
    cap_left = float(bankroll)
    hours_left = float(hours)
    blocked_by_capital = False
    blocked_by_labor = False

    for a in rank(analyses):
        if a.tier is Tier.NEEDS_COMP:
            skipped.append((a, "no sold comp yet — look one up before it counts"))
            continue
        if _TIER_RANK[a.tier] > _TIER_RANK[min_tier]:
            skipped.append((a, f"{a.tier.value} at ${_fmt_v(a.per_100_per_day, 0)}/$100/day "
                               f"— below the {min_tier.value} bar"))
            continue
        cost = a.cost or 0.0
        need_hours = a.handle_minutes / 60.0
        if cost > cap_left:
            blocked_by_capital = True
            skipped.append((a, f"needs ${cost:,.2f}, only ${cap_left:,.2f} left"))
            continue
        if need_hours > hours_left:
            blocked_by_labor = True
            skipped.append((a, f"needs {need_hours:.1f}h, only {hours_left:.1f}h left"))
            continue
        bought.append(a)
        cap_left -= cost
        hours_left -= need_hours

    profit_total = sum(a.deal.net_profit or 0.0 for a in bought)
    profit_per_week = sum((a.profit_per_day or 0.0) for a in bought) * 7.0

    # Which budget actually stopped you? Capital first: running out of money is
    # the harder stop (you cannot work more hours to fix it).
    if blocked_by_capital:
        binding = "capital"
    elif blocked_by_labor:
        binding = "labor"
    else:
        binding = "deal flow"

    return Portfolio(
        bought=bought, skipped=skipped, bankroll=float(bankroll),
        capital_used=round(float(bankroll) - cap_left, 2),
        hours_available=float(hours), hours_used=round(float(hours) - hours_left, 2),
        profit_total=round(profit_total, 2), profit_per_week=round(profit_per_week, 2),
        binding=binding,
    )


# ---------------------------------------------------------------------------
# Realized velocity — the only numbers that aren't a forecast.
#
# `analyze` promises a profit; `ledger` records what actually happened. Hold
# time is where the two diverge most violently, because "sells in 2 weeks" is
# the assumption every reseller is most confident about and most wrong about.
# This reads the ledger and computes velocity the way a fund would: total
# profit over total CAPITAL-DAYS, so a big slow position drags the number down
# proportionally instead of hiding inside an average of per-item ratios.
# ---------------------------------------------------------------------------

import datetime as _dt


def _days_between(start: str, end: str) -> Optional[int]:
    try:
        a = _dt.date.fromisoformat(start)
        b = _dt.date.fromisoformat(end)
    except Exception:
        return None
    return (b - a).days


@dataclass(frozen=True)
class RealizedFlip:
    id: int
    title: str
    paid: float
    profit: float
    hold_days: int
    per_100_per_day: float


@dataclass(frozen=True)
class OpenPosition:
    id: int
    title: str
    paid: float
    age_days: int


@dataclass(frozen=True)
class RealizedVelocity:
    """What your capital actually earned, per dollar per day."""

    flips: List[RealizedFlip]
    open_positions: List[OpenPosition]
    profit: float
    capital_days: float          # sum(paid x hold_days) — the real denominator
    per_100_per_day: Optional[float]
    avg_hold_days: Optional[float]
    turns_per_year: Optional[float]
    parked_capital: float        # money currently sitting in unsold items
    stale: List[OpenPosition]    # positions older than the stale cutoff
    stale_days: int

    def tier(self, t: VelocityThresholds = VelocityThresholds()) -> Tier:
        if self.per_100_per_day is None:
            return Tier.NEEDS_COMP
        return _tier(self.per_100_per_day, self.profit, None, t, [])

    def report(self) -> str:
        if not self.flips and not self.open_positions:
            return ("ledger is empty — record purchases with:\n"
                    '  flipscout bought "<title>" --paid <amount> --source <src>')
        L = []
        if self.flips:
            L.append(f"REALIZED VELOCITY  {len(self.flips)} closed flip(s)")
            L.append(f"  profit         ${self.profit:,.2f} over "
                     f"${self.capital_days:,.0f} capital-days")
            L.append(f"  velocity       ${self.per_100_per_day:,.2f} per $100 per day "
                     f"({self.tier().value})")
            L.append(f"  avg hold       {self.avg_hold_days:.0f} days -> "
                     f"{self.turns_per_year:.1f} turns/yr on that slot")
            L.append("")
            L.append(f"  {'#':>3} {'held':>5} {'paid':>9} {'profit':>9} "
                     f"{'$/100/day':>10}  item")
            for f_ in sorted(self.flips, key=lambda x: -x.per_100_per_day):
                L.append(f"  {f_.id:>3} {f_.hold_days:>4}d ${f_.paid:>8,.2f} "
                         f"${f_.profit:>8,.2f} {f_.per_100_per_day:>10,.2f}  "
                         f"{f_.title[:40]}")
        else:
            L.append("REALIZED VELOCITY  nothing closed yet — sell something and "
                     "record it with `flipscout sold`")
        if self.open_positions:
            L.append("")
            L.append(f"  ${self.parked_capital:,.2f} parked in "
                     f"{len(self.open_positions)} unsold item(s)")
            if self.stale:
                stuck = sum(p.paid for p in self.stale)
                L.append(f"  🚨 ${stuck:,.2f} has been sitting >{self.stale_days} days "
                         f"in {len(self.stale)} item(s) — that capital is earning "
                         f"$0.00/day. Reprice or dump:")
                for p in sorted(self.stale, key=lambda x: -x.age_days):
                    L.append(f"     #{p.id:<3} {p.age_days:>4}d  ${p.paid:>8,.2f}  "
                             f"{p.title[:40]}")
        return "\n".join(L)


def realized_velocity(
    path: Optional[str] = None,
    today: Optional[str] = None,
    stale_days: int = 60,
    ledger_entries: Optional[Sequence[dict]] = None,
) -> RealizedVelocity:
    """Measure velocity off the purchase ledger.

    `ledger_entries` is an injection point for tests and for anyone keeping the
    ledger somewhere else; by default it reads `flipscout.ledger`.
    """
    if ledger_entries is None:
        from .ledger import entries as _ledger_entries  # lazy: pulls in pricebook
        rows = _ledger_entries(path)
    else:
        rows = list(ledger_entries)

    now = today or _dt.date.today().isoformat()
    flips: List[RealizedFlip] = []
    open_positions: List[OpenPosition] = []

    for e in rows:
        paid = float(e.get("paid") or 0.0)
        title = e.get("title") or "?"
        eid = int(e.get("id") or 0)
        if e.get("status") == "sold" and e.get("sold_date"):
            held = _days_between(e.get("date", ""), e["sold_date"])
            if held is None:
                continue
            # Same-day flips still tie up money for a day; 0 would divide away.
            held = max(1, held)
            profit = float(e.get("profit") or 0.0)
            per100 = (profit / paid / held * 100.0) if paid > 0 else 0.0
            flips.append(RealizedFlip(id=eid, title=title, paid=paid, profit=profit,
                                      hold_days=held, per_100_per_day=per100))
        else:
            age = _days_between(e.get("date", ""), now)
            if age is None:
                continue
            open_positions.append(OpenPosition(id=eid, title=title, paid=paid,
                                               age_days=max(0, age)))

    profit = sum(f_.profit for f_ in flips)
    capital_days = sum(f_.paid * f_.hold_days for f_ in flips)
    per100 = (profit / capital_days * 100.0) if capital_days > 0 else None
    avg_hold = (sum(f_.hold_days for f_ in flips) / len(flips)) if flips else None
    turns = (DAYS_PER_YEAR / avg_hold) if avg_hold else None
    stale = [p for p in open_positions if p.age_days > stale_days]

    return RealizedVelocity(
        flips=flips, open_positions=open_positions, profit=round(profit, 2),
        capital_days=round(capital_days, 2), per_100_per_day=per100,
        avg_hold_days=avg_hold, turns_per_year=turns,
        parked_capital=round(sum(p.paid for p in open_positions), 2),
        stale=stale, stale_days=stale_days,
    )

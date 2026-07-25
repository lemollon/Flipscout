"""What to bid: the opening number, and the number you never go past.

Two different jobs, and conflating them is how people lose money at auction:

  * **max bid**  - the walk-away ceiling, derived from what the thing actually
    resells for minus every cost. It does not move because someone outbid you.
  * **open bid** - what you put in first. On a proxy-bid site (ShopGoodwill and
    HiBid both are) bidding early only advertises interest and walks the price up,
    so the opening number is just the minimum needed to hold position; the real
    decision is the ceiling.

Every number here is on the SAME basis - the bid itself, with handling and inbound
shipping accounted for separately - because mixing "all-in" and "bid only" is
exactly how an alert misleads you.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .fees import FeeModel, net_proceeds

# A bid you can't win isn't worth alerting on. If the minimum to enter already
# exceeds the ceiling, the listing is dead on arrival.
DEFAULT_TARGET_PROFIT = 20.0


@dataclass(frozen=True)
class BidAdvice:
    """Everything the alert needs to state a price with no ambiguity."""

    open_bid: Optional[float]      # what to bid now (bid basis)
    max_bid: float                 # never exceed (bid basis)
    landed_at_max: float           # what max_bid actually costs you, all-in
    net_resale: float              # what you clear on the sale side
    profit_at_open: Optional[float]
    profit_at_max: float           # == target_profit by construction
    units: int = 1
    has_room: bool = True
    note: str = ""

    def summary(self) -> str:
        if not self.has_room:
            return f"NO ROOM - minimum bid already exceeds the ${self.max_bid:.2f} ceiling"
        o = f"${self.open_bid:.2f}" if self.open_bid is not None else "-"
        return (f"open {o} -> max ${self.max_bid:.2f} "
                f"(lands ${self.landed_at_max:.2f}, clears ${self.profit_at_max:.2f})")


def next_valid_bid(current_price: Optional[float], min_bid: Optional[float],
                   increment: float = 1.0, bid_count: int = 0) -> Optional[float]:
    """The smallest bid the site will accept right now.

    Sources disagree on what they expose, so prefer an explicit `min_bid` and fall
    back to current + increment. With no bids yet the starting price IS the minimum
    (you don't add an increment to open).
    """
    if min_bid is not None and min_bid > 0:
        return round(float(min_bid), 2)
    if current_price is None:
        return None
    cur = float(current_price)
    return round(cur if not bid_count else cur + increment, 2)


def advise(
    comp: float,
    *,
    units: int = 1,
    handling: float = 0.0,
    inbound_shipping: float = 0.0,
    outbound_shipping: float = 0.0,
    fees: Optional[FeeModel] = None,
    target_profit: float = DEFAULT_TARGET_PROFIT,
    current_price: Optional[float] = None,
    min_bid: Optional[float] = None,
    increment: float = 1.0,
    bid_count: int = 0,
) -> BidAdvice:
    """Turn a resale comp into a bidding plan.

    `comp` is the per-unit resale price (what one sells for, including the shipping
    the buyer pays). `units` > 1 for a lot containing several sellable items -
    each unit costs you its own outbound shipping and its own eBay fees, which is
    why the lot math is not simply comp x units.
    """
    fees = fees or FeeModel()
    units = max(1, int(units))

    # Sale side: every unit is its own order, so fees + postage apply per unit.
    net_resale = sum(
        net_proceeds(comp, fees=fees, shipping_cost=outbound_shipping).net
        for _ in range(units)
    )

    # Buy side: handling + inbound shipping are paid once for the whole lot.
    fixed_buy = float(handling) + float(inbound_shipping)
    max_bid = round(net_resale - target_profit - fixed_buy, 2)

    open_bid = next_valid_bid(current_price, min_bid, increment, bid_count)
    has_room = max_bid > 0 and (open_bid is None or open_bid <= max_bid)

    profit_at_open = None
    if open_bid is not None:
        profit_at_open = round(net_resale - (open_bid + fixed_buy), 2)

    note = ""
    if not has_room:
        note = ("the minimum bid is already above the ceiling"
                if max_bid > 0 else "fees exceed the resale value")
    elif bid_count == 0:
        note = "no bids yet - it will get bid up; set the max and walk away"

    return BidAdvice(
        open_bid=open_bid,
        max_bid=max(max_bid, 0.0),
        landed_at_max=round(max(max_bid, 0.0) + fixed_buy, 2),
        net_resale=round(net_resale, 2),
        profit_at_open=profit_at_open,
        profit_at_max=float(target_profit),
        units=units,
        has_room=has_room,
        note=note,
    )

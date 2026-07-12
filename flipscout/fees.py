"""The eBay cost model — what a sale actually nets you after everyone takes a cut.

eBay runs "managed payments": the final value fee (FVF) already includes payment
processing, and it is charged on the *total* the buyer pays — item price PLUS the
shipping you charge them PLUS sales tax eBay collects. New sellers pay roughly the
same category FVF as everyone else; a store subscription lowers it a little. There
is also a small fixed per-order fee.

Defaults below are the common US "most categories" numbers as of 2025-2026. They
move, and they vary by category, so every rate is a knob you can override. Treat
the output as "close enough to decide whether to buy", not accounting.

Fee references (verify against your own eBay fee page — rates change):
  * Final value fee: ~13.25% of the order total for most categories.
  * Per-order fee: $0.40 (eBay charges $0.30 on orders of $10 or less).
  * Optional international fee: ~1.65% when the buyer is outside your country.
  * Promoted Listings: an ad rate you set (e.g. 2%-12%) charged on the sale price
    only when the promoted click leads to the sale. Off by default.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeModel:
    """All the rates that eat into a sale. Money values in dollars, rates as
    fractions (0.1325 == 13.25%)."""

    final_value_pct: float = 0.1325       # eBay FVF incl. payment processing
    per_order_fee: float = 0.40           # fixed fee per order
    per_order_fee_small: float = 0.30     # applies when order total <= small_order_threshold
    small_order_threshold: float = 10.0
    international_pct: float = 0.0         # set ~0.0165 if you sell abroad
    promoted_pct: float = 0.0             # ad rate on sale price, if promoting
    tax_collected_pct: float = 0.0        # sales tax eBay collects & is FVF'd on (buyer-side)

    def order_fixed_fee(self, order_total: float) -> float:
        thr = self.small_order_threshold
        return self.per_order_fee_small if order_total <= thr else self.per_order_fee


# A conservative "assume nothing goes your way" model: higher effective take rate.
# Useful as a stress case when deciding whether a thin-margin flip is worth it.
CONSERVATIVE = FeeModel(final_value_pct=0.1355, per_order_fee=0.40, international_pct=0.0165)


@dataclass(frozen=True)
class NetProceeds:
    """Breakdown of a single sale."""

    sale_price: float          # what the item lists/sells for
    shipping_charged: float    # what the buyer pays you for shipping (0 if free shipping)
    order_total: float         # item + shipping (+ tax) the FVF is computed on
    final_value_fee: float
    fixed_fee: float
    international_fee: float
    promoted_fee: float
    shipping_cost: float       # what YOU pay the carrier
    total_fees: float          # everything eBay/ads take
    net: float                 # cash in your pocket from the sale, before item cost


def net_proceeds(
    sale_price: float,
    fees: FeeModel = FeeModel(),
    shipping_cost: float = 0.0,
    shipping_charged: float = 0.0,
) -> NetProceeds:
    """What you keep from a sale, before subtracting what you paid for the item.

    sale_price       — the item's sale/list price.
    shipping_cost    — postage YOU pay the carrier (a real cost to you).
    shipping_charged — postage the BUYER pays you. With "free shipping" this is 0
                       and you eat shipping_cost; otherwise it offsets it. Note
                       eBay charges FVF on shipping_charged too.
    """
    if sale_price < 0 or shipping_cost < 0 or shipping_charged < 0:
        raise ValueError("money values must be non-negative")

    tax = sale_price * fees.tax_collected_pct
    order_total = sale_price + shipping_charged + tax

    fvf = order_total * fees.final_value_pct
    fixed = fees.order_fixed_fee(order_total)
    intl = order_total * fees.international_pct
    promoted = sale_price * fees.promoted_pct
    total_fees = fvf + fixed + intl + promoted

    # Cash in = item price + shipping the buyer paid; cash out = fees + your postage.
    net = sale_price + shipping_charged - total_fees - shipping_cost

    return NetProceeds(
        sale_price=sale_price,
        shipping_charged=shipping_charged,
        order_total=order_total,
        final_value_fee=fvf,
        fixed_fee=fixed,
        international_fee=intl,
        promoted_fee=promoted,
        shipping_cost=shipping_cost,
        total_fees=total_fees,
        net=net,
    )

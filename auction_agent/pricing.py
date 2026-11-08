"""Max-bid (price cap) math. Kept deterministic on purpose: the LLM estimates
retail value and repair cost; the arithmetic is done here so it's auditable."""
from __future__ import annotations

from .models import BuyBox, LotEvaluation

# Approximate buyer-fee tiers (sale price ceiling -> fee). Auction fee tables
# change and differ by member type, title type and payment method: replace
# these with the current table from your Copart / IAA account.
DEFAULT_FEE_TIERS: list[tuple[float, float]] = [
    (500, 200), (1000, 325), (1500, 425), (2000, 500), (3000, 600),
    (4000, 700), (5000, 800), (7500, 950), (10000, 1100), (15000, 1300),
]
DEFAULT_FEE_PCT_ABOVE = 0.10      # above the last tier
GATE_AND_ONLINE_FEES = 160.0      # gate fee + virtual-bid fee, approx.


def buyer_fee(price: float, tiers=DEFAULT_FEE_TIERS) -> float:
    for ceiling, fee in tiers:
        if price <= ceiling:
            return fee + GATE_AND_ONLINE_FEES
    return price * DEFAULT_FEE_PCT_ABOVE + GATE_AND_ONLINE_FEES


def max_bid(ev: LotEvaluation, box: BuyBox) -> tuple[float, dict[str, float]]:
    """Highest hammer price that still leaves `target_profit` after costs,
    using the HIGH repair estimate (be conservative)."""
    costs = ev.repair_high + box.transport_cost + box.misc_costs + box.target_profit
    ceiling = ev.est_retail_value - costs
    # Solve bid + fee(bid) <= ceiling by stepping down in $25 increments.
    bid = max(0.0, ceiling)
    while bid > 0 and bid + buyer_fee(bid) > ceiling:
        bid -= 25
    bid = max(0.0, bid)
    if box.max_budget is not None:
        bid = min(bid, box.max_budget)
    bid = float(int(bid // 25) * 25)
    breakdown = {
        "est_retail_value": ev.est_retail_value,
        "repair_high": ev.repair_high,
        "transport": box.transport_cost,
        "misc": box.misc_costs,
        "target_profit": box.target_profit,
        "buyer_fees_at_cap": buyer_fee(bid) if bid else 0.0,
        "max_bid": bid,
    }
    return bid, breakdown

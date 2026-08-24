"""Shared risk-based position sizing for live trading and backtests."""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Optional


DEFAULT_TARGET_RISK_USD = 3.0
DEFAULT_NOTIONAL_CAP_USD = 250.0
MAX_ROUND_TRIP_COST_RISK_FRACTION = 0.20


@dataclass(frozen=True)
class PositionSizeResult:
    size: Optional[float]
    raw_size: float
    planned_risk: Optional[float]
    notional: Optional[float]
    reason: Optional[str] = None

    @property
    def skipped(self) -> bool:
        return self.size is None


def calculate_round_trip_cost_fraction(
    *, round_trip_cost: float, stop_distance: float,
) -> float:
    """Express execution cost in units of the planned price risk."""
    if stop_distance <= 0:
        return math.inf
    return max(round_trip_cost, 0.0) / stop_distance


def calculate_position_size(
    *,
    entry_price: float,
    stop_loss: float,
    target_risk: float = DEFAULT_TARGET_RISK_USD,
    notional_cap: float = DEFAULT_NOTIONAL_CAP_USD,
    min_size: float = 0.0,
    size_increment: float = 0.0,
    max_size: Optional[float] = None,
) -> PositionSizeResult:
    """Keep both risk and notional at or below their hard limits."""
    values = (entry_price, stop_loss, target_risk, notional_cap)
    if not all(math.isfinite(value) for value in values):
        return PositionSizeResult(None, 0.0, None, None, "non-finite sizing input")
    stop_distance = abs(entry_price - stop_loss)
    if entry_price <= 0 or stop_distance <= 0:
        return PositionSizeResult(None, 0.0, None, None, "invalid entry or stop distance")
    if target_risk <= 0 or notional_cap <= 0:
        return PositionSizeResult(None, 0.0, None, None, "risk and notional caps must be positive")

    risk_size = target_risk / stop_distance
    notional_size = notional_cap / entry_price
    capped_sizes = [risk_size, notional_size]
    if max_size is not None and math.isfinite(max_size) and max_size > 0:
        capped_sizes.append(max_size)
    raw_size = min(capped_sizes)

    if min_size > 0 and raw_size < min_size:
        reason = (
            f"calculated size {raw_size:.10g} is below broker minimum size "
            f"{min_size:.10g}; increasing it would exceed a hard cap"
        )
        return PositionSizeResult(None, raw_size, None, None, reason)

    size = raw_size
    if size_increment > 0:
        value = Decimal(str(raw_size))
        increment = Decimal(str(size_increment))
        size = float(
            (value / increment).to_integral_value(rounding=ROUND_FLOOR)
            * increment
        )
    if size <= 0:
        reason = (
            f"calculated size {raw_size:.10g} rounds below one broker step "
            f"{size_increment:.10g}"
        )
        return PositionSizeResult(None, raw_size, None, None, reason)
    if min_size > 0 and size < min_size:
        reason = (
            f"rounded size {size:.10g} is below broker minimum size "
            f"{min_size:.10g}; increasing it would exceed a hard cap"
        )
        return PositionSizeResult(None, raw_size, None, None, reason)

    planned_risk = size * stop_distance
    notional = size * entry_price
    tolerance = 1e-9
    if planned_risk > target_risk + tolerance or notional > notional_cap + tolerance:
        return PositionSizeResult(
            None,
            raw_size,
            None,
            None,
            "broker rounding would exceed the risk or notional cap",
        )
    return PositionSizeResult(size, raw_size, planned_risk, notional)

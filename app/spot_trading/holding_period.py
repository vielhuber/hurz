"""Shared stale-exit holding-period rules."""
from __future__ import annotations

import os
from typing import Optional


_DEFAULT_MAX_HOLD_BARS = 24
_BAR_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}
# The journal has no resolution column, so 4h strategy names carry it.
_STRATEGY_BAR_SECONDS = {
    "donchian_breakout_4h": 14400,
    "momentum_4h": 14400,
    "turtle_breakout_4h": 14400,
}
# The trailing strategy needs a multi-day leash to realize its edge.
_STRATEGY_MAX_HOLD_BARS = {
    "donchian_trail": 240,
}


def _configured_max_hold_bars() -> int:
    configured_bars = os.getenv("HURZ_MAX_HOLD_BARS")
    try:
        return (
            int(configured_bars)
            if configured_bars
            else _DEFAULT_MAX_HOLD_BARS
        )
    except ValueError:
        return _DEFAULT_MAX_HOLD_BARS


def stale_exits_enabled() -> bool:
    """Return whether the global stale-exit leash is active."""
    return _configured_max_hold_bars() > 0


def stale_exit_after_seconds(
    strategy_name: str,
    resolution: str = "1h",
) -> Optional[int]:
    """Return the effective stale-exit deadline or no limit when disabled."""
    default_hold_bars = _configured_max_hold_bars()
    if default_hold_bars <= 0:
        return None
    hold_bars = _STRATEGY_MAX_HOLD_BARS.get(
        strategy_name,
        default_hold_bars,
    )
    bar_seconds = _STRATEGY_BAR_SECONDS.get(
        strategy_name,
        _BAR_SECONDS.get(resolution, 3600),
    )
    return hold_bars * bar_seconds

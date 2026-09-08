"""Shared execution parameters for live trading and backtests."""
from __future__ import annotations


DEFAULT_RISK_REWARD = 1.5

# Stop distance in ATR(14) multiples. Widened from 1.0 on 2026-09-07: the
# round-trip spread is charged on price but measured against the stop, so
# a wider stop lowers the cost per unit of risk arithmetically, and the
# gross expectancy did not get worse on either sample (EDGE_FINDINGS 60).
# Risk per trade is unchanged; sizing shrinks the position instead.
DEFAULT_STOP_ATR = 2.0

# Smallest stop the live loop and the backtests will place, as a fraction
# of price: the venue's 1 % minimum plus its 5 % buffer, which every
# instrument but GOLD (venue minimum 0.1 %) already enforces. Widening
# GOLD to it instead of refusing it under the 1 % floor measured +0.130 R
# and +0.073 R on the two walk-forward samples (EDGE_FINDINGS 104).
VENUE_MIN_STOP_FRACTION = 0.0105

# The donchian aliases share their entry logic but deliberately use fixed,
# wider targets. The v3 value originated from a 2026-07-08 BTCUSD backtest
# and is not evidence of an advantage on other instruments.
_STRATEGY_RISK_REWARD = {
    "donchian_breakout_v2": 2.5,
    "donchian_breakout_v3": 3.5,
    # Far backstop only; the live loop normally exits via its ATR trail.
    "donchian_trail": 5.0,
}


def risk_reward_for(strategy_name: str, default: float = DEFAULT_RISK_REWARD) -> float:
    """Keep strategy-specific exit targets identical in every execution path."""
    return _STRATEGY_RISK_REWARD.get(strategy_name, default)

"""Shared execution parameters for live trading and backtests."""
from __future__ import annotations


DEFAULT_RISK_REWARD = 1.5

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

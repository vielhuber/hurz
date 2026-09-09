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
# of price. Despite the name this is the project's own floor, not the
# venue's: the dealing rules' minStopOrProfitDistance is 0.01 in plain
# percent (0.01 % of price, GOLD 0.001 %; the same field's maximum reads
# 100), and the broker has honoured live stops at 0.18 %. Read as a
# fraction it became 1.05 %, and that accident measures better than the
# designed 2-ATR stop: at the venue's true floor the 2-ATR book reads
# -0.065 R against -0.033 R with the cost per R doubled (EDGE_FINDINGS
# 135), so the floor stays as a deliberate wide-stop setting. Widening
# GOLD to it measured +0.130 R and +0.073 R (EDGE_FINDINGS 104).
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

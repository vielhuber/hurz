"""Strategy library for spot trading.

A `Strategy` takes a DataFrame of OHLC bars and emits a list of
`Signal` objects — entry index, direction, and the strategy's
own confidence (only used by ensemble voters; standalone strategies
emit 1.0). Strategies are stateless and side-effect-free; the
backtest harness handles trade simulation, the autotrader handles
live execution.

Adding a strategy:
  1. Write a function that conforms to `StrategyFn` in `base.py`
  2. Register it in `_REGISTRY` in this module
  3. Optionally expose hyperparameters via the `params` dict that
     the backtest CLI passes through
"""
from app.strategies.base import (
    Strategy,
    StrategyFn,
    Signal,
    add_indicators,
)
from app.strategies.bollinger_rev import bollinger_rev
from app.strategies.momentum import momentum
from app.strategies.rsi_mr import rsi_mr
from app.strategies.multi_consensus import multi_consensus
from app.strategies.stochastic_mr import stochastic_mr
from app.strategies.donchian_breakout import donchian_breakout


_REGISTRY = {
    "bollinger_rev":     bollinger_rev,
    "momentum":          momentum,
    "rsi_mr":            rsi_mr,
    "multi_consensus":   multi_consensus,
    "stochastic_mr":     stochastic_mr,
    "donchian_breakout": donchian_breakout,
}


def get_strategy(name: str) -> Strategy:
    """Look up a strategy by name. Raises if unknown."""
    if name not in _REGISTRY:
        raise ValueError(
            f"unknown strategy '{name}'. Registered: {list(_REGISTRY)}"
        )
    return _REGISTRY[name]


def available_strategies() -> list:
    return list(_REGISTRY.keys())


__all__ = [
    "Strategy",
    "StrategyFn",
    "Signal",
    "add_indicators",
    "get_strategy",
    "available_strategies",
]

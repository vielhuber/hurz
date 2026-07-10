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
from app.strategies.turtle_breakout import turtle_breakout
from app.strategies.donchian_atr import donchian_atr
from app.strategies.keltner_breakout import keltner_breakout


_REGISTRY = {
    "bollinger_rev":     bollinger_rev,
    "momentum":          momentum,
    "rsi_mr":            rsi_mr,
    "multi_consensus":   multi_consensus,
    "stochastic_mr":     stochastic_mr,
    "donchian_breakout": donchian_breakout,
    "turtle_breakout":   turtle_breakout,
    "donchian_atr":      donchian_atr,
    # Parallel forward-test clones of donchian_breakout: identical entry
    # logic, but the live loop exits them at a wider risk:reward (see
    # autotrade._STRATEGY_RR). donchian_breakout itself is untouched.
    "donchian_breakout_v2": donchian_breakout,
    "donchian_breakout_v3": donchian_breakout,
    # 4h-timeframe book: same entry logic, evaluated on 4h bars (the combo's
    # `resolution` field drives the fetch). Distinct names keep journal
    # stats, dedup keys and per-strategy overrides separate from the 1h book
    # — the journal has no resolution column, so the name carries it.
    "donchian_breakout_4h": donchian_breakout,
    "momentum_4h":          momentum,
    "turtle_breakout_4h":   turtle_breakout,
    # Volatility-adaptive channel — structurally distinct from donchian's
    # fixed price-extreme channel (see keltner_breakout.py).
    "keltner_breakout":     keltner_breakout,
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

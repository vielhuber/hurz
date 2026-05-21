"""Strategy interface + shared indicator computation.

Every strategy is a callable:
    fn(df: DataFrame, params: dict) -> List[Signal]

It receives a DataFrame with at minimum these columns:
    timestamp (datetime, UTC)
    open, high, low, close, volume (float)
    plus any indicator columns added by `add_indicators()`
        bb_pos       — Bollinger %position [-1.0, +1.0]
        rsi_14       — RSI(14) in [0, 100]
        atr_14       — Average True Range (price units)
        roc_10       — Rate-of-change (10-bar % return)
        ema_fast     — EMA(10)
        ema_slow     — EMA(30)

It returns Signal objects describing intent:
    Signal(index, direction, confidence)
    direction: +1 long, -1 short
    confidence: [0, 1] — only meaningful for ensemble voters

Strategies are pure functions of past data — they may not look at
future bars (no leakage) and may not depend on previous state
(idempotent across reloads).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Signal:
    index: int
    direction: int           # +1 or -1
    confidence: float = 1.0  # used by ensembles to weigh sub-strategies


# Type alias for strategy callables.
StrategyFn = Callable[[pd.DataFrame, Dict], List[Signal]]
Strategy = StrategyFn  # alias kept for clarity in code that imports it


# ---------------- shared indicators ----------------
# Computed once per backtest, reused by every strategy. Adding a new
# indicator column here makes it available to all strategies; remove
# only when no strategy references it.

_BB_PERIOD = 20
_BB_K = 2.0
_RSI_PERIOD = 14
_ATR_PERIOD = 14
_ROC_PERIOD = 10
_EMA_FAST = 10
_EMA_SLOW = 30


def _compute_bb_pos(close: pd.Series) -> pd.Series:
    sma = close.rolling(_BB_PERIOD).mean()
    sd = close.rolling(_BB_PERIOD).std()
    half_band = _BB_K * sd
    # Avoid div-by-zero on flat windows.
    return (close - sma) / half_band.replace(0, np.nan)


def _compute_rsi(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(_RSI_PERIOD).mean()
    loss = (-delta.clip(upper=0)).rolling(_RSI_PERIOD).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_atr(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(_ATR_PERIOD).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Attach all shared indicators to a copy of `df`. Idempotent —
    safe to call multiple times. Returns a new DataFrame."""
    out = df.copy()
    out["bb_pos"] = _compute_bb_pos(out["close"])
    out["rsi_14"] = _compute_rsi(out["close"])
    out["atr_14"] = _compute_atr(out)
    out["roc_10"] = out["close"].pct_change(_ROC_PERIOD) * 100.0
    out["ema_fast"] = out["close"].ewm(span=_EMA_FAST, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=_EMA_SLOW, adjust=False).mean()
    return out


def warmup_bars() -> int:
    """Number of leading bars to skip — every indicator needs its
    rolling window. Strategies may depend on any of them, so we use
    the worst-case warmup."""
    return max(_BB_PERIOD, _RSI_PERIOD, _ATR_PERIOD, _ROC_PERIOD, _EMA_SLOW) + 1

"""Stochastic-oscillator mean-reversion.

Uses the classic %K (close vs N-bar high/low range) as a faster
companion to RSI. Fires CALL when %K crosses up out of oversold
(<20), PUT when %K crosses down out of overbought (>80).

Hyperparameters via `params`:
    period      : int (default 14) — lookback for high/low range
    oversold    : float (default 20)
    overbought  : float (default 80)
    confidence  : float (default 1.0)
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from app.strategies.base import Signal, warmup_bars


def _stoch_k(df: pd.DataFrame, period: int) -> pd.Series:
    low_min = df["low"].rolling(period).min()
    high_max = df["high"].rolling(period).max()
    rng = (high_max - low_min).replace(0, np.nan)
    return 100.0 * (df["close"] - low_min) / rng


def stochastic_mr(df: pd.DataFrame, params: Dict | None = None) -> List[Signal]:
    p = params or {}
    period = int(p.get("period", 14))
    oversold = float(p.get("oversold", 20))
    overbought = float(p.get("overbought", 80))
    conf = float(p.get("confidence", 1.0))

    k = _stoch_k(df, period).values
    signals: List[Signal] = []
    start = max(warmup_bars(), period + 1)

    # Edge-trigger on cross-OUT of extreme zones — classic interpretation.
    for i in range(start, len(df)):
        if not (np.isfinite(k[i]) and np.isfinite(k[i - 1])):
            continue
        # Cross UP out of oversold → bullish
        if k[i - 1] <= oversold < k[i]:
            signals.append(Signal(index=i, direction=+1, confidence=conf))
        # Cross DOWN out of overbought → bearish
        elif k[i - 1] >= overbought > k[i]:
            signals.append(Signal(index=i, direction=-1, confidence=conf))
    return signals

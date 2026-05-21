"""RSI mean-reversion.

Hypothesis: when RSI(14) is < 30 (oversold) the price tends to
bounce up; when > 70 (overbought) it tends to fall. Classic
indicator, often-noisy on minute bars but robust on longer
intervals.

Hyperparameters via `params`:
    oversold     : float (default 30) — RSI threshold for long signal
    overbought   : float (default 70) — RSI threshold for short signal
    confidence   : float (default 1.0)
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from app.strategies.base import Signal, warmup_bars


def rsi_mr(df: pd.DataFrame, params: Dict | None = None) -> List[Signal]:
    p = params or {}
    oversold = float(p.get("oversold", 30))
    overbought = float(p.get("overbought", 70))
    conf = float(p.get("confidence", 1.0))

    signals: List[Signal] = []
    rsi = df["rsi_14"].values
    start = warmup_bars()

    # Edge-trigger only — we fire when RSI ENTERS the extreme zone,
    # not for every bar it stays there. Saves over-trading on flat
    # extreme regimes.
    prev_in_zone = 0  # -1 oversold, +1 overbought, 0 neither
    for i in range(start, len(df)):
        v = rsi[i]
        if not np.isfinite(v):
            prev_in_zone = 0
            continue
        if v <= oversold:
            cur = -1
        elif v >= overbought:
            cur = +1
        else:
            cur = 0
        if cur == -1 and prev_in_zone != -1:
            signals.append(Signal(index=i, direction=+1, confidence=conf))
        elif cur == +1 and prev_in_zone != +1:
            signals.append(Signal(index=i, direction=-1, confidence=conf))
        prev_in_zone = cur
    return signals

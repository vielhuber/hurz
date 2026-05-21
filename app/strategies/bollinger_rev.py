"""Bollinger-band mean-reversion.

Hypothesis: when price reaches an extreme of its 20-period
Bollinger Band (|bb_pos| > trigger), mean-reversion is more likely
than continuation. Long the lower band, short the upper.

Hyperparameters via `params`:
    trigger        : float (default 0.9) — fire when |bb_pos| > trigger
    confidence     : float (default 1.0) — fixed signal confidence
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from app.strategies.base import Signal, warmup_bars


def bollinger_rev(df: pd.DataFrame, params: Dict | None = None) -> List[Signal]:
    p = params or {}
    trigger = float(p.get("trigger", 0.9))
    conf = float(p.get("confidence", 1.0))

    signals: List[Signal] = []
    bb = df["bb_pos"].values
    start = warmup_bars()
    for i in range(start, len(df)):
        v = bb[i]
        if not np.isfinite(v):
            continue
        if v > trigger:
            # price at upper extreme → bet on reversion DOWN
            signals.append(Signal(index=i, direction=-1, confidence=conf))
        elif v < -trigger:
            # price at lower extreme → bet on reversion UP
            signals.append(Signal(index=i, direction=+1, confidence=conf))
    return signals

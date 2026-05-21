"""Momentum / trend-continuation.

Hypothesis: a sustained move in one direction over the last N bars
tends to continue at least one more bar — particularly on crypto
which has well-documented trend persistence.

Implementation: fast EMA crossing slow EMA in the direction of the
recent ROC. Optional gate on minimum ROC magnitude to avoid trading
flat regimes.

Hyperparameters via `params`:
    min_roc_pct : float (default 0.5) — minimum ROC% to fire
    confidence  : float (default 1.0)
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from app.strategies.base import Signal, warmup_bars


def momentum(df: pd.DataFrame, params: Dict | None = None) -> List[Signal]:
    p = params or {}
    min_roc = float(p.get("min_roc_pct", 0.5))
    conf = float(p.get("confidence", 1.0))

    signals: List[Signal] = []
    ema_fast = df["ema_fast"].values
    ema_slow = df["ema_slow"].values
    roc = df["roc_10"].values
    start = warmup_bars()

    # Detect EMA-cross + same-sign ROC. We fire ONLY on the first bar
    # of a new cross — sustained crosses don't re-fire.
    prev_diff = 0.0
    for i in range(start, len(df)):
        ef = ema_fast[i]; es = ema_slow[i]; r = roc[i]
        if not (np.isfinite(ef) and np.isfinite(es) and np.isfinite(r)):
            prev_diff = 0.0
            continue
        diff = ef - es
        crossed_up = diff > 0 and prev_diff <= 0
        crossed_dn = diff < 0 and prev_diff >= 0
        if crossed_up and r >= min_roc:
            signals.append(Signal(index=i, direction=+1, confidence=conf))
        elif crossed_dn and r <= -min_roc:
            signals.append(Signal(index=i, direction=-1, confidence=conf))
        prev_diff = diff
    return signals

"""Turtle System-2 breakout — the SLOW sibling of donchian_breakout.

Fires CALL when the close pushes above the highest high of the last
55 bars (excluding the current bar), PUT on the symmetric break of
the lowest low. Structurally identical to donchian_breakout but with
a 55-bar channel instead of 20: it ignores the short-term noise the
fast channel trades and only fires on multi-week moves, so the two
capture different trend phases and rarely trigger on the same bar.

Hyperparameters via `params`:
    period      : int (default 55) — channel lookback
    confidence  : float (default 1.0)

Notes:
  - Excludes the current bar from the high/low so a touch isn't a
    self-fulfilling breakout (prevents same-bar leakage).
  - Each new high resets the trigger — only the FIRST bar of a new
    breakout fires; sustained highs don't keep firing.
  - Live lookback is 240 bars (autotrade default), which comfortably
    covers the 55-bar channel plus indicator warmup.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from app.strategies.base import Signal, warmup_bars


def turtle_breakout(df: pd.DataFrame, params: Dict | None = None) -> List[Signal]:
    p = params or {}
    period = int(p.get("period", 55))
    conf = float(p.get("confidence", 1.0))

    # Channel = max of past N bars EXCLUDING current. Shift first.
    high_ch = df["high"].shift(1).rolling(period).max().values
    low_ch = df["low"].shift(1).rolling(period).min().values
    close = df["close"].values
    signals: List[Signal] = []
    start = max(warmup_bars(), period + 1)
    prev_state = 0  # +1 above channel, -1 below, 0 inside
    for i in range(start, len(df)):
        h = high_ch[i]; l = low_ch[i]; c = close[i]
        if not (np.isfinite(h) and np.isfinite(l) and np.isfinite(c)):
            prev_state = 0
            continue
        cur = 0
        if c > h:
            cur = +1
        elif c < l:
            cur = -1
        # Edge-trigger on entering new state.
        if cur == +1 and prev_state != +1:
            signals.append(Signal(index=i, direction=+1, confidence=conf))
        elif cur == -1 and prev_state != -1:
            signals.append(Signal(index=i, direction=-1, confidence=conf))
        prev_state = cur
    return signals

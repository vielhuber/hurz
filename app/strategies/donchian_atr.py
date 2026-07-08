"""Donchian breakout with an ATR confirmation buffer.

Identical to `donchian_breakout` except the close must clear the channel
by at least `atr_buffer * ATR` before a breakout counts. Vanilla Donchian
fires the instant the close ticks one point past the channel — many of
those are marginal breaks that immediately reverse and hit the stop
(live: donchian_breakout's loss column is dominated by such false breaks).
Requiring the close to push a fraction of an ATR *beyond* the channel
filters the marginal breaks while still catching genuine range expansions.

This is a SEPARATE strategy — `donchian_breakout` is untouched. Both can
run side by side; the pair-selector / pins decide which trades live.

Hyperparameters via `params`:
    period      : int   (default 20)  — channel lookback
    atr_buffer  : float (default 0.5) — breakout must clear the channel by
                                        this multiple of ATR(14)
    confidence  : float (default 1.0)

Notes:
  - Excludes the current bar from the high/low (shift) so a touch isn't a
    self-fulfilling breakout.
  - Edge-triggered: only the FIRST bar that clears channel+buffer fires;
    sustained breakouts don't re-fire.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from app.strategies.base import Signal, warmup_bars


def donchian_atr(df: pd.DataFrame, params: Dict | None = None) -> List[Signal]:
    p = params or {}
    period = int(p.get("period", 20))
    atr_buffer = float(p.get("atr_buffer", 0.5))
    conf = float(p.get("confidence", 1.0))

    high_ch = df["high"].shift(1).rolling(period).max().values
    low_ch = df["low"].shift(1).rolling(period).min().values
    close = df["close"].values
    atr = df["atr_14"].values
    signals: List[Signal] = []
    start = max(warmup_bars(), period + 1)
    prev_state = 0  # +1 above channel+buffer, -1 below, 0 inside
    for i in range(start, len(df)):
        h = high_ch[i]; l = low_ch[i]; c = close[i]; a = atr[i]
        if not (np.isfinite(h) and np.isfinite(l)
                and np.isfinite(c) and np.isfinite(a)):
            prev_state = 0
            continue
        buf = atr_buffer * a
        cur = 0
        if c > h + buf:
            cur = +1
        elif c < l - buf:
            cur = -1
        if cur == +1 and prev_state != +1:
            signals.append(Signal(index=i, direction=+1, confidence=conf))
        elif cur == -1 and prev_state != -1:
            signals.append(Signal(index=i, direction=-1, confidence=conf))
        prev_state = cur
    return signals

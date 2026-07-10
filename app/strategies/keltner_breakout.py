"""Keltner-channel breakout — volatility-adaptive trend entry.

Fires CALL when the close pushes above EMA(center) + k×ATR, PUT below
EMA(center) − k×ATR. Complements donchian_breakout structurally: donchian
breaks FIXED N-bar price extremes, Keltner breaks a VOLATILITY-SCALED band
around a moving center — in quiet markets the band tightens (earlier
entries), in wild markets it widens (fewer whipsaws). The two fire on
different bars, which is the point (breadth, not duplication).

Not to be confused with the rejected `donchian_atr` (2026-07-08): that was
the donchian price-extreme channel PLUS an ATR buffer — a stricter donchian.
This is a different channel construction entirely.

Hyperparameters via `params`:
    center_span : int   (default 20)  — EMA span of the channel center
    atr_mult    : float (default 2.0) — band width in ATR(14) multiples
    confidence  : float (default 1.0)

Notes:
  - Band is computed on the PREVIOUS bar (shift) so the current close is
    compared against a band that doesn't already contain it (no same-bar
    leakage — same discipline as donchian's shifted channel).
  - Edge-triggered: only the FIRST bar that closes outside the band fires;
    a sustained ride outside doesn't re-fire.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from app.strategies.base import Signal, warmup_bars


def keltner_breakout(df: pd.DataFrame, params: Dict | None = None) -> List[Signal]:
    p = params or {}
    center_span = int(p.get("center_span", 20))
    atr_mult = float(p.get("atr_mult", 2.0))
    conf = float(p.get("confidence", 1.0))

    center = df["close"].ewm(span=center_span, adjust=False).mean()
    upper = (center + atr_mult * df["atr_14"]).shift(1).values
    lower = (center - atr_mult * df["atr_14"]).shift(1).values
    close = df["close"].values
    signals: List[Signal] = []
    start = max(warmup_bars(), center_span + 1)
    prev_state = 0  # +1 above band, -1 below, 0 inside
    for i in range(start, len(df)):
        u = upper[i]; l = lower[i]; c = close[i]
        if not (np.isfinite(u) and np.isfinite(l) and np.isfinite(c)):
            prev_state = 0
            continue
        cur = 0
        if c > u:
            cur = +1
        elif c < l:
            cur = -1
        if cur == +1 and prev_state != +1:
            signals.append(Signal(index=i, direction=+1, confidence=conf))
        elif cur == -1 and prev_state != -1:
            signals.append(Signal(index=i, direction=-1, confidence=conf))
        prev_state = cur
    return signals

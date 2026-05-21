"""Multi-strategy consensus voter.

Runs three orthogonal sub-strategies on the same bars and emits a
signal only when at least `min_agree` of them agree on direction
WITHIN the same `agree_window` bars. This filters single-strategy
false positives while keeping the diversification benefit.

Hyperparameters via `params`:
    min_agree     : int (default 2) — minimum agreeing voters
    agree_window  : int (default 1) — bar window inside which votes
                    count as concurrent (default = same bar only)
    confidence    : float (default 1.0)

Sub-strategies share their own indicator dependencies — make sure
`add_indicators()` was called on the DataFrame upstream.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import pandas as pd

from app.strategies.base import Signal
from app.strategies.bollinger_rev import bollinger_rev
from app.strategies.momentum import momentum
from app.strategies.rsi_mr import rsi_mr


def multi_consensus(df: pd.DataFrame, params: Dict | None = None) -> List[Signal]:
    p = params or {}
    min_agree = int(p.get("min_agree", 2))
    window = max(1, int(p.get("agree_window", 1)))
    conf = float(p.get("confidence", 1.0))

    # Run each sub-strategy with its defaults. The voter does not
    # forward params to subs — that would be confusing; tune subs in
    # their own files if needed.
    sub_signals = {
        "bollinger_rev": bollinger_rev(df),
        "momentum":      momentum(df),
        "rsi_mr":        rsi_mr(df),
    }

    # Index votes by bar index and direction. Each voter contributes
    # at most one direction per bar.
    votes_by_bar = defaultdict(lambda: defaultdict(int))  # bar → direction → count
    for sig_list in sub_signals.values():
        for s in sig_list:
            votes_by_bar[s.index][s.direction] += 1

    out: List[Signal] = []
    issued: set = set()
    for i in sorted(votes_by_bar.keys()):
        # Aggregate votes across the agree_window.
        long_votes = 0
        short_votes = 0
        for j in range(i - window + 1, i + 1):
            d_map = votes_by_bar.get(j, {})
            long_votes += d_map.get(+1, 0)
            short_votes += d_map.get(-1, 0)
        if long_votes >= min_agree and long_votes > short_votes and i not in issued:
            out.append(Signal(index=i, direction=+1, confidence=conf))
            issued.add(i)
        elif short_votes >= min_agree and short_votes > long_votes and i not in issued:
            out.append(Signal(index=i, direction=-1, confidence=conf))
            issued.add(i)
    return out

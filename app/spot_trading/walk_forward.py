"""Walk-forward segment-stability analysis.

PROBLEM
-------
A strategy that posts strong POOLED backtest stats can still be a
regime-overfit trap: it may have won everything during one favourable
phase (say, a 30-day Bitcoin down-trend) and lose elsewhere. Pooled
expectancy / profit factor hide this — the per-trade arithmetic just
averages over all bars.

SOLUTION
--------
Split the bar history into N consecutive segments and evaluate the
strategy independently in each. If fewer than ~2/3 of the segments
post a positive per-trade expectancy (E[R] > 0), the "edge" is most
likely the consequence of one regime that won't repeat — not a
structural property of the rules.

This module is the LIBRARY used by:
  - scripts/spot_backtest.py to compute and persist a stability
    score alongside each per-pair result row.
  - app/spot_trading/pair_selector.py to filter out combos that
    fail the stability bar before they ever go live.

`scripts/walk_forward.py` is a CLI sibling that visualises the same
computation for manual inspection — kept separate so its formatting
code doesn't bleed into the importable path.

DESIGN NOTES
------------
- No fee/spread modelling. The stability check answers a binary
  question ("does the edge generalise?"); fee precision is the job
  of `_simulate_trades` in spot_backtest.py.
- The R-unit accounting (rr on win, -1 on loss, partial on max-hold
  fall-through) mirrors `scripts/walk_forward.py._simulate` so the
  numbers in `data/spot_backtest_results.json` and CLI output match.
- Segments must hold at least `min_segment_bars` bars (default 60),
  otherwise stability cannot be meaningfully computed and the
  function returns None — callers should treat None as "unknown"
  rather than "failed".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StabilityResult:
    """Outcome of a walk-forward segment evaluation.

    Attributes
    ----------
    n_segments
        Number of segments that produced at least one trade. May be
        smaller than the requested `segments` argument when some
        segments had no signal at all.
    positive_segments
        Count of segments whose mean per-trade E[R] was strictly > 0.
    mean_expectancy_R
        Average of per-segment mean E[R] values. Useful as a
        diagnostic — large mean_expectancy_R combined with a low
        stability_ratio means one segment dominated.
    """
    n_segments: int
    positive_segments: int
    mean_expectancy_R: float

    @property
    def stability_ratio(self) -> float:
        """Fraction of segments with positive expectancy, in [0, 1]."""
        if self.n_segments == 0:
            return 0.0
        return self.positive_segments / self.n_segments

    def as_dict(self) -> dict:
        """JSON-serialisable form for persistence in
        `data/spot_backtest_results.json`."""
        return {
            "n_segments": self.n_segments,
            "positive_segments": self.positive_segments,
            "mean_expectancy_R": self.mean_expectancy_R,
            "ratio": self.stability_ratio,
        }


def _simulate_segment_expectancy(
    df: pd.DataFrame, signals,
    *, rr: float, stop_atr: float, max_hold: int,
) -> Optional[float]:
    """Run the signal list against `df` and return the per-trade mean
    expectancy in R-units, or None if no trade triggered.

    Trade accounting (R-units):
      - SL hit first  → -1.0
      - TP hit first  → +rr
      - neither hit within max_hold bars → close at the max_hold bar
        and book (close - entry) * direction / |entry - sl|

    Mirrors `scripts/walk_forward.py._simulate`; intentionally
    leaves fees out because this function asks "is the rules edge
    regime-stable?", not "what's the net P&L?".
    """
    rs = []
    in_until = -1
    for sig in signals:
        i = sig.index
        if i <= in_until or i >= len(df):
            continue
        atr = df.iloc[i].get("atr_14")
        if atr is None or not np.isfinite(atr) or atr <= 0:
            continue
        entry = float(df.iloc[i]["close"])
        stop_d = stop_atr * atr
        tp_d = rr * stop_d
        sl = entry - stop_d if sig.direction == 1 else entry + stop_d
        tp = entry + tp_d if sig.direction == 1 else entry - tp_d
        outcome = None
        for j in range(1, max_hold + 1):
            if i + j >= len(df):
                break
            h = df.iloc[i + j]["high"]
            l = df.iloc[i + j]["low"]
            if sig.direction == 1:
                if l <= sl:
                    outcome = "loss"; in_until = i + j; break
                if h >= tp:
                    outcome = "win"; in_until = i + j; break
            else:
                if h >= sl:
                    outcome = "loss"; in_until = i + j; break
                if l <= tp:
                    outcome = "win"; in_until = i + j; break
        if outcome == "win":
            rs.append(rr)
        elif outcome == "loss":
            rs.append(-1.0)
        elif i + max_hold < len(df):
            cl = float(df.iloc[i + max_hold]["close"])
            pnl = (cl - entry) * sig.direction
            risk = abs(entry - sl)
            if risk > 0:
                rs.append(pnl / risk)
                in_until = i + max_hold
    if not rs:
        return None
    return float(np.mean(rs))


def compute_segment_stability(
    df: pd.DataFrame, strategy_fn: Callable,
    *, segments: int = 3, rr: float = 1.5, stop_atr: float = 1.0,
    max_hold: int = 24, min_segment_bars: int = 60,
) -> Optional[StabilityResult]:
    """Run `strategy_fn` independently on N consecutive slices of `df`
    and report how many produced a positive per-trade expectancy.

    Parameters
    ----------
    df
        Bars with indicators already attached (the strategy needs them).
    strategy_fn
        Callable signature `strategy_fn(df, params_dict) -> List[Signal]`.
        Re-invoked per segment so each segment sees only its own slice.
    segments
        How many consecutive slices to cut. Default 3 — for a 30-day
        1h history that's ~10 days per segment, enough to typically
        produce 5-15 trades for a moderately active strategy.
    rr, stop_atr, max_hold
        Same simulation knobs the live trader uses; pass them through
        so the stability check evaluates the EXACT rules the autotrader
        will deploy.
    min_segment_bars
        Floor below which segmentation is meaningless. Returns None
        when the history is too short to satisfy
        `len(df) // segments >= min_segment_bars`.

    Returns
    -------
    StabilityResult, or None when:
      - df has too few bars to support `segments` × `min_segment_bars`
      - no segment produced any trade

    None means "stability unknown" — callers should NOT treat it as
    failure. A combo with too-short history should be filtered on its
    own merits (e.g. via `min_trades` in pair_selector), not silently
    rejected here.
    """
    n = len(df)
    seg_size = n // segments
    if seg_size < min_segment_bars:
        return None
    seg_E = []
    for s in range(segments):
        lo = s * seg_size
        hi = (s + 1) * seg_size if s < segments - 1 else n
        seg_df = df.iloc[lo:hi].reset_index(drop=True)
        signals = strategy_fn(seg_df, {})
        E = _simulate_segment_expectancy(
            seg_df, signals,
            rr=rr, stop_atr=stop_atr, max_hold=max_hold,
        )
        if E is not None:
            seg_E.append(E)
    if not seg_E:
        return None
    positive = sum(1 for e in seg_E if e > 0)
    return StabilityResult(
        n_segments=len(seg_E),
        positive_segments=positive,
        mean_expectancy_R=float(np.mean(seg_E)),
    )

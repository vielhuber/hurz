"""Market-regime filter — keep each strategy style in the regime where
it actually has an edge.

WHY
---
Between 2026-06-15 and 06-18 the book gave back ~$175, almost entirely
ETHUSD: the mean-reversion strategies (bollinger_rev, rsi_mr) faded a
directional ETH move and were stopped out over and over. Mean-reversion
has no edge in a trending regime; trend-following has none in a flat
range. This module classifies the current regime per pair via ADX and
blocks a signal whose strategy style is structurally wrong for it.

DEAD-BAND
---------
We only veto when the regime is *clearly* mismatched, to avoid
over-filtering in transitional conditions:

    ADX >= adx_trend   -> trending   (block mean-reversion)
    ADX <= adx_range   -> ranging    (block trend-following)
    adx_range < ADX < adx_trend -> ambiguous (allow both)

Strategies whose style is "neutral" (e.g. multi_consensus) and any
unknown strategy are never blocked. When ADX is unavailable (warmup,
NaN) we fail OPEN — never block on missing data.

CONSISTENCY
-----------
The exact same decision is applied in two places so live trading and
the backtest/pair-selector never diverge:
  - app/spot_trading/autotrade.py  (live: vetoes a signal pre-order)
  - scripts/spot_backtest.py        (sim: skips the signal so persisted
                                      edge-stats reflect the filter)

TOGGLE / TUNE via env (read per call; set before bot start):
    HURZ_REGIME_FILTER    = 1|0   (default 1 = on)
    HURZ_REGIME_ADX_TREND = float (default 25)
    HURZ_REGIME_ADX_RANGE = float (default 20)
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd


# Strategy style classification. Mean-reversion fades extremes (wants a
# range); trend-following rides breakouts (wants a trend). Everything
# else is neutral and never filtered.
_MEAN_REVERSION = {"bollinger_rev", "rsi_mr", "stochastic_mr"}
_TREND = {"donchian_breakout", "momentum"}

_DEFAULT_ADX_TREND = 25.0
_DEFAULT_ADX_RANGE = 20.0


@dataclass(frozen=True)
class RegimeDecision:
    blocked: bool
    regime: str            # "trend" | "range" | "ambiguous" | "unknown" | "n/a"
    adx: Optional[float]
    reason: str


def style_of(strategy_name: str) -> str:
    if strategy_name in _MEAN_REVERSION:
        return "mean_reversion"
    if strategy_name in _TREND:
        return "trend"
    return "neutral"


def _config() -> tuple:
    enabled = os.getenv("HURZ_REGIME_FILTER", "1").strip().lower() \
        not in ("0", "false", "no", "off")
    try:
        adx_trend = float(os.getenv("HURZ_REGIME_ADX_TREND", "") or _DEFAULT_ADX_TREND)
    except ValueError:
        adx_trend = _DEFAULT_ADX_TREND
    try:
        adx_range = float(os.getenv("HURZ_REGIME_ADX_RANGE", "") or _DEFAULT_ADX_RANGE)
    except ValueError:
        adx_range = _DEFAULT_ADX_RANGE
    return enabled, adx_trend, adx_range


def decide(strategy_name: str, adx_value: Optional[float]) -> RegimeDecision:
    """Core policy: given a strategy and the current ADX, decide whether
    to block the signal. Fails open on missing data / disabled / neutral."""
    enabled, adx_trend, adx_range = _config()
    style = style_of(strategy_name)
    if not enabled:
        return RegimeDecision(False, "n/a", adx_value, "regime filter disabled")
    if style == "neutral":
        return RegimeDecision(False, "n/a", adx_value, "neutral strategy")
    if adx_value is None or not math.isfinite(adx_value):
        return RegimeDecision(False, "unknown", None, "ADX unavailable — allow")
    if adx_value >= adx_trend:
        regime = "trend"
    elif adx_value <= adx_range:
        regime = "range"
    else:
        regime = "ambiguous"
    if style == "mean_reversion" and regime == "trend":
        return RegimeDecision(
            True, regime, adx_value,
            f"mean-reversion blocked in trend (ADX={adx_value:.1f} "
            f">= {adx_trend:.0f})")
    if style == "trend" and regime == "range":
        return RegimeDecision(
            True, regime, adx_value,
            f"trend-following blocked in range (ADX={adx_value:.1f} "
            f"<= {adx_range:.0f})")
    return RegimeDecision(False, regime, adx_value, "regime ok")


def adx_at(df: pd.DataFrame, index: int) -> Optional[float]:
    """Read the precomputed adx_14 at a bar index. Returns None when the
    column is missing or the value is NaN (warmup)."""
    if "adx_14" not in df.columns:
        return None
    try:
        v = df.iloc[index].get("adx_14")
    except (IndexError, KeyError):
        return None
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def gate(strategy_name: str, df: pd.DataFrame, index: int) -> RegimeDecision:
    """Convenience: read ADX at `index` and apply the policy in one call."""
    return decide(strategy_name, adx_at(df, index))


def summary() -> str:
    """One-line config summary for the startup log."""
    enabled, adx_trend, adx_range = _config()
    if not enabled:
        return "off"
    return f"on (trend>={adx_trend:.0f}, range<={adx_range:.0f})"

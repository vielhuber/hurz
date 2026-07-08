"""Regime ROUTER — run each strategy style only in the regime where it
has an edge, and stand aside in the transitional zone where neither does.

WHY
---
Two separate drawdowns taught us the full picture:
  - 2026-06-15..18 (−$234 cumulative across the run): mean-reversion
    (bollinger_rev, rsi_mr, stochastic_mr) faded directional moves and
    was stopped out repeatedly. ADX at those entries: 36–56 (strong trend).
  - 2026-06-26..28 (−$67): trend-following (donchian_breakout) entered
    breakouts that immediately reversed (whipsaw). ADX at those entries:
    ~20–28 (weak / transitional trend).

So there is a "death zone" around ADX ~20–30 where breakouts whipsaw AND
ranges aren't clean — BOTH styles lose. The router encodes this:

    ADX >= adx_trend (30)  -> STRONG TREND  -> trend-following only
    ADX <= adx_range (20)  -> RANGE         -> mean-reversion only
    adx_range < ADX < adx_trend -> NO-TRADE ZONE -> stand aside (block all)

This is a true router, not a soft filter: trend-following is blocked
*below* the trend threshold (not just in clear ranges), and mean-reversion
is blocked *above* the range threshold. The gap between the two
thresholds is the deliberate stand-aside band.

Strategies whose style is "neutral" (e.g. multi_consensus) and unknown
strategies are never blocked. When ADX is unavailable (warmup, NaN) we
fail OPEN — never block on missing data.

CONSISTENCY
-----------
The exact same decision is applied in two places so live trading and
the backtest/pair-selector never diverge:
  - app/spot_trading/autotrade.py  (live: vetoes a signal pre-order)
  - scripts/spot_backtest.py        (sim: skips the signal so persisted
                                      edge-stats reflect the router)

TOGGLE / TUNE via env (read per call; set before bot start):
    HURZ_REGIME_FILTER    = 1|0   (default 1 = on)
    HURZ_REGIME_ADX_TREND = float (default 30 — trend-following floor)
    HURZ_REGIME_ADX_RANGE = float (default 20 — mean-reversion ceiling)
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
_TREND = {"donchian_breakout", "momentum", "turtle_breakout", "donchian_atr",
          "donchian_breakout_v2", "donchian_breakout_v3"}

_DEFAULT_ADX_TREND = 30.0
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
    """Router policy: given a strategy and the current ADX, decide whether
    the signal may trade. Trend-following needs ADX >= adx_trend; mean-
    reversion needs ADX <= adx_range; the gap between is a no-trade zone
    where both styles are blocked. Fails open on missing data / disabled
    / neutral."""
    enabled, adx_trend, adx_range = _config()
    style = style_of(strategy_name)
    if not enabled:
        return RegimeDecision(False, "n/a", adx_value, "regime router disabled")
    if style == "neutral":
        return RegimeDecision(False, "n/a", adx_value, "neutral strategy")
    if adx_value is None or not math.isfinite(adx_value):
        return RegimeDecision(False, "unknown", None, "ADX unavailable — allow")
    if style == "trend":
        if adx_value >= adx_trend:
            return RegimeDecision(False, "strong-trend", adx_value,
                                  f"trend-following in trend (ADX={adx_value:.1f})")
        return RegimeDecision(
            True, "no-trade-zone" if adx_value > adx_range else "range", adx_value,
            f"trend-following needs ADX>={adx_trend:.0f}, got {adx_value:.1f}")
    if style == "mean_reversion":
        if adx_value <= adx_range:
            return RegimeDecision(False, "range", adx_value,
                                  f"mean-reversion in range (ADX={adx_value:.1f})")
        return RegimeDecision(
            True, "no-trade-zone" if adx_value < adx_trend else "trend", adx_value,
            f"mean-reversion needs ADX<={adx_range:.0f}, got {adx_value:.1f}")
    return RegimeDecision(False, "n/a", adx_value, "unclassified strategy")


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


def trend_threshold() -> float:
    """The ADX floor at/above which the market counts as a strong trend
    (trend-following allowed, mean-reversion blocked)."""
    return _config()[1]


def flip_exit_enabled() -> bool:
    """Whether open mean-reversion positions should be force-closed once
    ADX rises into the trend regime. Independent of the entry router:
    toggle via HURZ_REGIME_FLIP_EXIT (default on)."""
    return os.getenv("HURZ_REGIME_FLIP_EXIT", "1").strip().lower() \
        not in ("0", "false", "no", "off")


def summary() -> str:
    """One-line config summary for the startup log."""
    enabled, adx_trend, adx_range = _config()
    flip = "on" if flip_exit_enabled() else "off"
    if not enabled:
        return f"off (flip-exit {flip})"
    return (f"router on (trend-follow ADX>={adx_trend:.0f}, "
            f"mean-rev ADX<={adx_range:.0f}, else stand aside; "
            f"flip-exit {flip})")

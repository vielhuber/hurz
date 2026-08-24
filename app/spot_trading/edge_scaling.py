"""Risk budget that follows proven forward edge.

The daily-return target cannot be reached at 3 USD of risk per trade —
it needs roughly ten times that. But raising the budget while expectancy
is negative just scales the losses, so the size has to be earned rather
than assumed.

This module derives the budget from realised forward results: the risk
grows only once a sufficiently large out-of-sample sample shows an
expectancy whose lower confidence bound is still positive, and it grows
in bounded steps. If the edge decays, the budget shrinks again on the
next evaluation. With no edge, nothing changes.

Trades before `cutoff` are excluded on purpose — the entry filters were
calibrated on them, so their expectancy is in-sample and worthless as
evidence for sizing.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

from app.spot_trading.position_sizing import DEFAULT_TARGET_RISK_USD

# Out-of-sample window. Everything before this date fed the calibration
# of the cost and veto filters.
DEFAULT_EDGE_CUTOFF = "2026-08-24"
# Below this many closed trades no expectancy is believable, however
# good it looks.
MIN_SAMPLE = 40
# How far the budget may run ahead of the base risk.
MAX_RISK_MULTIPLE = 10.0
# Hard ceiling as a share of account equity, applied on top of the
# multiple. Without it the 10x cap is meaningless on a small account:
# 30 USD per trade against the 474 EUR balance measured on 2026-08-24
# would put roughly 43% of the account at risk across eight concurrent
# positions. Scaling has to answer to the balance, not just to the edge.
MAX_RISK_ACCOUNT_FRACTION = 0.01
# Confidence for the lower bound on expectancy, in standard errors.
CONFIDENCE_SIGMAS = 2.0


@dataclass(frozen=True)
class EdgeAssessment:
    trades: int
    mean_r: Optional[float]
    lower_bound_r: Optional[float]
    risk_usd: float
    reason: str


def _cutoff() -> str:
    return os.environ.get("HURZ_EDGE_CUTOFF") or DEFAULT_EDGE_CUTOFF


_MIN_RISK_FRACTION = 0.10


def _target_risk() -> float:
    """The per-trade risk budget outlier positions are measured against."""
    raw = os.getenv("HURZ_RISK_PER_TRADE")
    if raw:
        try:
            configured = float(raw)
            if configured > 0:
                return configured
        except ValueError:
            pass
    return DEFAULT_TARGET_RISK_USD


def assess_edge(
    base_risk: float = DEFAULT_TARGET_RISK_USD,
    account_equity: Optional[float] = None,
) -> EdgeAssessment:
    """Risk budget justified by out-of-sample results so far.

    Returns `base_risk` unchanged whenever the evidence does not carry a
    larger size — too few trades, an unreadable journal, or a lower
    confidence bound at or below zero.

    `account_equity`, when known, caps the result at
    `MAX_RISK_ACCOUNT_FRACTION` of the balance. A proven edge justifies a
    bigger bet only up to what the account can absorb; it never justifies
    betting the account."""
    try:
        from app.utils.singletons import database
        rows = database.select(
            """
            SELECT CASE WHEN exit_price IS NOT NULL AND fill_price IS NOT NULL
                        THEN (exit_price - fill_price) * direction * size
                        ELSE realized_pnl END AS pnl_fill,
                   size, COALESCE(fill_price, entry_price) AS px, stop_loss
            FROM spot_trades
            WHERE accepted = 1 AND paper_mode = 0 AND platform = 'capital_com'
              AND exit_time IS NOT NULL AND realized_pnl IS NOT NULL
              AND size > 0 AND created_at >= %s
              AND ABS(COALESCE(fill_price, entry_price) - stop_loss) > 0
              AND COALESCE(outcome, '') <> 'abandoned'
            """,
            (_cutoff(),),
        )
    except Exception:
        return EdgeAssessment(0, None, None, base_risk,
                              "journal unavailable")
    # Booked against the fill, and ignoring positions sized far below the
    # budget: their R carries a near-zero denominator, which both shifts
    # the mean and inflates the variance the confidence bound is built
    # from. Scaling risk up on that would be the worst place to get it
    # wrong.
    floor = _MIN_RISK_FRACTION * _target_risk()
    values = []
    for row in rows or []:
        risk = abs(float(row["px"]) - float(row["stop_loss"])) * float(row["size"])
        if risk >= floor:
            values.append(float(row["pnl_fill"]) / risk)
    if len(values) < MIN_SAMPLE:
        return EdgeAssessment(len(values), None, None, base_risk,
                              f"only {len(values)} of {MIN_SAMPLE} "
                              f"out-of-sample trades")
    mean_r = sum(values) / len(values)
    variance = sum((v - mean_r) ** 2 for v in values) / (len(values) - 1)
    standard_error = math.sqrt(variance / len(values))
    lower = mean_r - CONFIDENCE_SIGMAS * standard_error
    if lower <= 0:
        return EdgeAssessment(len(values), mean_r, lower, base_risk,
                              "no edge proven at this confidence")
    # Scale with the proven lower bound, not the point estimate: the
    # size is only as trustworthy as the weakest defensible edge.
    multiple = min(1.0 + lower / 0.10, MAX_RISK_MULTIPLE)
    risk = base_risk * multiple
    reason = f"edge proven, scaling {multiple:.2f}x"
    if account_equity and account_equity > 0:
        ceiling = account_equity * MAX_RISK_ACCOUNT_FRACTION
        if ceiling < risk:
            # Never scale below the base risk on account grounds alone —
            # that is a separate decision from "is there an edge".
            risk = max(base_risk, ceiling)
            reason = (f"edge proven, capped at "
                      f"{MAX_RISK_ACCOUNT_FRACTION:.0%} of "
                      f"{account_equity:.2f} equity")
    return EdgeAssessment(len(values), mean_r, lower, risk, reason)

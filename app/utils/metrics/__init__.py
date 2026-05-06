"""Diagnostic metrics package.

Metrics are observers — they read the trade history / fulltest results
and produce per-asset numbers (Brier score, Wilson CI, drift). They
never veto a trade. The drift-tracker is shared with the
`drift_automute` gate.
"""
from app.utils.metrics.brier_score import compute_brier_score
from app.utils.metrics.wilson_ci import wilson_ci, format_wilson_ci
from app.utils.metrics.drift_tracker import get_drift_for_asset

__all__ = [
    "compute_brier_score",
    "wilson_ci",
    "format_wilson_ci",
    "get_drift_for_asset",
]

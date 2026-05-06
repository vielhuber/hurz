"""Brier score — calibration metric for binary classifiers.

Brier(p, y) = (p - y)^2 averaged over samples. Range [0, 1]:
    0      = perfect calibration AND perfect resolution
    0.25   = always predicting 0.5 (random)
    > 0.25 = worse than random

We also report the *reliability* component (how far miscalibrated the
average prob is from the empirical event rate), which is the part the
conformal calibrator should reduce.

Computed on the last `window_days` of CLOSED demo trades — relies on
having the original `trade_confidence` column logged with each trade
(the live system does, since this drives the order path).
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


def compute_brier_score(
    asset: str, window_days: int = 30
) -> Optional[Dict[str, Any]]:
    """Return a dict with brier and resolution OR None if insufficient data."""
    from app.utils.singletons import database, store

    cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = database.select(
        "SELECT trade_confidence, direction, success FROM trades "
        "WHERE asset_name = %s AND status = 'closed' "
        "AND open_timestamp >= %s "
        "AND is_demo = %s",
        (asset, cutoff, getattr(store, "demo", 1)),
    )
    rows = [r for r in (rows or []) if r.get("success") in (0, 1)]
    if len(rows) < 5:
        return None

    # We don't store the raw probability per trade; we only have the
    # confidence threshold (e.g. 55) and the direction taken. Reconstruct
    # an effective prob: if the trade was a CALL we know prob > conf/100,
    # if PUT we know prob < (1 - conf/100). We use the threshold itself
    # as the LOWER BOUND on the model's confidence — this is conservative
    # (real prob was equal-or-higher). Brier is computed on this lower
    # bound so a poorly-calibrated bias still shows up.
    n = 0
    sse = 0.0
    avg_prob = 0.0
    avg_label = 0.0
    for r in rows:
        conf = int(r["trade_confidence"]) / 100.0
        direction = int(r["direction"])
        success = int(r["success"])
        if direction == 1:
            # CALL: predicted prob ≥ conf, actual outcome is 1 if success
            p = conf
            y = success
        else:
            # PUT: predicted prob ≤ 1 - conf, actual outcome is 0 if success
            p = 1.0 - conf
            y = 1 - success  # success on a PUT = price went down = label 0
        sse += (p - y) ** 2
        avg_prob += p
        avg_label += y
        n += 1
    brier = sse / n
    avg_prob /= n
    avg_label /= n
    reliability = abs(avg_prob - avg_label)

    return {
        "n": n,
        "brier": brier,
        "reliability": reliability,
        "avg_prob": avg_prob,
        "avg_label": avg_label,
        "window_days": window_days,
    }

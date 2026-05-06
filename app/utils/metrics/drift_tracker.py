"""Per-asset drift tracker — Live-WR vs. Fulltest-Success.

Computes the drift in percentage points between
    - "live": rolling N-day live closed-trade success rate
    - "fulltest": last_fulltest_quote_success column on the assets table
This is the data source for the drift_automute gate AND the diagnostic
dashboard. Cheap (single SELECT per asset).
"""
from datetime import timedelta
from typing import Any, Dict, Optional

from app.utils.feature_flags import FeatureFlags


def get_drift_for_asset(
    asset: str, lookback_days: int = 5, min_trades: int = 5
) -> Optional[Dict[str, Any]]:
    """Return drift info for `asset` over the last `lookback_days`.

    Returns None when no live trades exist (drift undefined) or when the
    asset has no fulltest record. The caller decides what "no drift info"
    means (allow vs. veto).

    Output dict:
        {
          "live_wr_pct":    float,
          "fulltest_pct":   float,
          "drift_pp":       float,   # negative = live worse than backtest
          "n_live_trades":  int,
          "live_wins":      int,
        }
    """
    from app.utils.singletons import database, store

    cutoff = None
    try:
        from datetime import datetime
        cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    rows = database.select(
        "SELECT success FROM trades "
        "WHERE asset_name = %s AND status = 'closed' "
        "AND open_timestamp >= %s "
        "AND is_demo = %s",
        (asset, cutoff, getattr(store, "demo", 1)),
    )
    if not rows:
        return None
    closed = [r for r in rows if r.get("success") in (0, 1)]
    if len(closed) < min_trades:
        return None
    wins = sum(1 for r in closed if r["success"] == 1)
    live_wr = wins / len(closed) * 100.0

    fulltest_rows = database.select(
        "SELECT last_fulltest_quote_success FROM assets "
        "WHERE platform = %s AND model = %s AND asset = %s",
        (store.trade_platform, store.active_model, asset),
    )
    if not fulltest_rows:
        return None
    raw = fulltest_rows[0].get("last_fulltest_quote_success")
    if raw is None:
        return None
    try:
        fulltest = float(raw)
    except (TypeError, ValueError):
        return None

    return {
        "live_wr_pct": live_wr,
        "fulltest_pct": fulltest,
        "drift_pp": live_wr - fulltest,
        "n_live_trades": len(closed),
        "live_wins": wins,
    }


def is_drift_tracker_enabled() -> bool:
    return FeatureFlags.is_enabled("metrics", "drift_tracker")

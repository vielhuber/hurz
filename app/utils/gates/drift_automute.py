"""Drift-auto-mute gate.

If an asset's live success rate has drifted more than `max_drift_pp`
percentage points BELOW its last fulltest quote_success over the last
`lookback_days`, refuse further trades on it. The model claims X% but
delivers Y% — until a re-fulltest closes the gap, sit out.

Sample-size guard: requires at least `min_trades` closed trades in the
window before any mute is triggered (1 loss in a 1-trade window is
noise, not drift).
"""
from typing import Any, Dict

from app.utils.gates.base import Gate, GateDecision
from app.utils.metrics.drift_tracker import get_drift_for_asset


class DriftAutoMuteGate(Gate):

    name = "drift_automute"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.lookback_days = int(config.get("lookback_days", 5))
        self.min_trades = int(config.get("min_trades", 5))
        self.max_drift_pp = float(config.get("max_drift_pp", 8.0))

    def check(self, asset: str, context: Dict[str, Any]) -> GateDecision:
        info = get_drift_for_asset(asset, self.lookback_days, self.min_trades)
        if info is None:
            return GateDecision(
                True,
                f"drift_automute: insufficient data (<{self.min_trades} trades in {self.lookback_days}d)",
            )
        drift = info["drift_pp"]
        # We only mute on negative drift (live worse than backtest).
        if drift < -self.max_drift_pp:
            return GateDecision(
                False,
                f"drift_automute: {asset} drift {drift:+.1f}pp "
                f"(live {info['live_wr_pct']:.1f}% vs fulltest {info['fulltest_pct']:.1f}%, "
                f"{info['n_live_trades']} trades)",
                info,
            )
        return GateDecision(
            True,
            f"drift_automute: {asset} drift {drift:+.1f}pp within tolerance",
            info,
        )

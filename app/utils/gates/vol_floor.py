"""Volatility-floor gate.

Counterpart to vol_regime: vol_regime blocks the high end (HIGH/EXTREME);
vol_floor blocks the dead-flat low end where 1-minute moves are too small
for any directional prediction to clear the spread + payout-cut. In flat
markets even a perfectly-correct direction call can land 1 pip off the
target after a 1-min hold and lose the trade.

Two complementary thresholds:
  - `min_atr_pct`: absolute floor on ATR%. If the current ATR% is below
    this, the gate vetoes regardless of percentile rank. Useful as a
    sanity floor (e.g. 0.005% over 14 minutes = ~0.5 pip avg move per
    minute on EUR pairs — clearly too flat to trade).
  - `min_pct_rank`: relative floor as a percentile of the last
    `lookback_days` of data. Skip if the current ATR% is below the
    P-th percentile of recent history.

Either threshold can disable itself (set to 0 or null). When both are
set, the gate vetoes when EITHER condition is met (logical OR — be
conservative, skip if ANY indicator says the market is too flat).
"""
from datetime import timedelta
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from app.utils.gates.base import Gate, GateDecision


class VolFloorGate(Gate):

    name = "vol_floor"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.atr_period = int(config.get("atr_period", 14))
        self.lookback_days = int(config.get("lookback_days", 30))
        self.min_atr_pct: Optional[float] = (
            float(config["min_atr_pct"]) if config.get("min_atr_pct") else None
        )
        self.min_pct_rank: Optional[float] = (
            float(config["min_pct_rank"]) if config.get("min_pct_rank") else None
        )

    @staticmethod
    def _atr_pct_series(prices: np.ndarray, period: int) -> np.ndarray:
        # Same TR-proxy as vol_regime: rolling mean of |Δprice| / price.
        if len(prices) < period + 1:
            return np.array([])
        diffs = np.abs(np.diff(prices))
        kernel = np.ones(period) / period
        atr = np.convolve(diffs, kernel, mode="valid")
        aligned_prices = prices[period:]
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(
                aligned_prices != 0,
                atr / aligned_prices * 100.0,
                np.nan,
            )

    def check(self, asset: str, context: Dict[str, Any]) -> GateDecision:
        # local import — keep base/registry importable in tests
        from app.utils.singletons import database, store

        now = context.get("now")
        try:
            cutoff = (now - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            cutoff = None
        rows = database.select(
            "SELECT timestamp, price FROM trading_data "
            "WHERE trade_asset = %s AND trade_platform = %s "
            + ("AND timestamp >= %s " if cutoff else "")
            + "ORDER BY timestamp",
            (asset, store.trade_platform, cutoff) if cutoff else (asset, store.trade_platform),
        )
        if not rows or len(rows) < self.atr_period + 50:
            return GateDecision(
                True,
                f"vol_floor: insufficient data ({len(rows) if rows else 0} rows)",
                {"atr_pct": None},
            )
        df = pd.DataFrame(rows)
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df = df.dropna(subset=["price"])
        prices = df["price"].astype(float).values
        atr_pct = self._atr_pct_series(prices, self.atr_period)
        if len(atr_pct) == 0 or not np.isfinite(atr_pct[-1]):
            return GateDecision(True, "vol_floor: ATR series empty", {"atr_pct": None})

        current = float(atr_pct[-1])

        # Absolute floor check.
        if self.min_atr_pct is not None and current < self.min_atr_pct:
            return GateDecision(
                False,
                f"vol_floor: ATR%={current:.4f} below absolute floor {self.min_atr_pct}",
                {"atr_pct": current, "min_atr_pct": self.min_atr_pct},
            )

        # Percentile-rank floor check.
        if self.min_pct_rank is not None:
            finite = atr_pct[np.isfinite(atr_pct)]
            if len(finite) >= 50:
                threshold = float(np.percentile(finite, self.min_pct_rank))
                if current < threshold:
                    return GateDecision(
                        False,
                        (
                            f"vol_floor: ATR%={current:.4f} below "
                            f"P{self.min_pct_rank:.0f}={threshold:.4f}"
                        ),
                        {"atr_pct": current, "pct_threshold": threshold, "rank": self.min_pct_rank},
                    )

        return GateDecision(
            True,
            f"vol_floor: ATR%={current:.4f} above floor",
            {"atr_pct": current},
        )

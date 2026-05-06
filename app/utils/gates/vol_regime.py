"""Volatility-regime gate.

Classifies the current ATR% into one of LOW / NORMAL / HIGH / EXTREME
buckets by percentile rank against the last `lookback_days` of data,
and refuses trades whose regime is not in `allowed_regimes`.

ATR% = ATR(period) / close * 100. We use the per-minute trading_data
table directly so the indicator is consistent with what the live
prediction sees. The percentile lookup is cheap (one SELECT + one
np.percentile), so we recompute on every call rather than caching.
"""
from datetime import timedelta
from typing import Any, Dict

import numpy as np
import pandas as pd

from app.utils.gates.base import Gate, GateDecision


class VolRegimeGate(Gate):

    name = "vol_regime"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.atr_period = int(config.get("atr_period", 14))
        self.lookback_days = int(config.get("lookback_days", 30))
        self.low_pct = float(config.get("low_pct", 20))
        self.high_pct = float(config.get("high_pct", 80))
        self.extreme_pct = float(config.get("extreme_pct", 95))
        self.allowed = set(
            (r or "").upper() for r in (config.get("allowed_regimes") or ["LOW", "NORMAL"])
        )

    @staticmethod
    def _atr_pct_series(prices: np.ndarray, period: int) -> np.ndarray:
        # 1-min binary-options data only has price (close) — no OHLC. We
        # approximate ATR with the rolling mean of |Δprice| as a TR proxy,
        # then divide by current price to get ATR%. Equivalent to mean
        # absolute return * 100 — same shape, same regime ordering.
        if len(prices) < period + 1:
            return np.array([])
        diffs = np.abs(np.diff(prices))
        # rolling mean of |diff|, length = len(diffs) - period + 1
        kernel = np.ones(period) / period
        atr = np.convolve(diffs, kernel, mode="valid")
        # align atr to the price index of the LAST sample in each window
        aligned_prices = prices[period:]
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(
                aligned_prices != 0,
                atr / aligned_prices * 100.0,
                np.nan,
            )

    def _classify(self, atr_pct_series: np.ndarray, current: float) -> str:
        finite = atr_pct_series[np.isfinite(atr_pct_series)]
        if len(finite) < 50:
            return "UNKNOWN"
        p_low = np.percentile(finite, self.low_pct)
        p_high = np.percentile(finite, self.high_pct)
        p_extreme = np.percentile(finite, self.extreme_pct)
        if current >= p_extreme:
            return "EXTREME"
        if current >= p_high:
            return "HIGH"
        if current <= p_low:
            return "LOW"
        return "NORMAL"

    def check(self, asset: str, context: Dict[str, Any]) -> GateDecision:
        # local import — keep base/registry importable in tests
        from app.utils.singletons import database, store
        now = context.get("now")
        # Pull just the lookback window — saves DB time on 80-asset rotations.
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
                f"vol_regime: insufficient data ({len(rows) if rows else 0} rows)",
                {"regime": "UNKNOWN"},
            )
        df = pd.DataFrame(rows)
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df = df.dropna(subset=["price"])
        prices = df["price"].astype(float).values
        atr_pct = self._atr_pct_series(prices, self.atr_period)
        if len(atr_pct) == 0 or not np.isfinite(atr_pct[-1]):
            return GateDecision(True, "vol_regime: ATR series empty", {"regime": "UNKNOWN"})

        current = float(atr_pct[-1])
        regime = self._classify(atr_pct, current)

        if regime in self.allowed:
            return GateDecision(
                True,
                f"vol_regime: {regime} (ATR%={current:.4f})",
                {"regime": regime, "atr_pct": current},
            )
        return GateDecision(
            False,
            f"vol_regime: {regime} (ATR%={current:.4f}) not in allowed {sorted(self.allowed)}",
            {"regime": regime, "atr_pct": current, "allowed": sorted(self.allowed)},
        )

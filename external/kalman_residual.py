"""E8 — Kalman-residual rule.

Hypothesis: when the observed price is an extreme outlier relative to a
Kalman-smoothed estimate of the "true" price path, the next bar tends
to mean-revert back toward the smoother. We measure this as a z-score
of the final residual against the window's residual standard deviation.

Smoothing: local-level Kalman filter (random-walk latent state plus
Gaussian observation noise). For fixed Q/R the steady-state gain
K* = alpha, so the filter is equivalent to an EWMA with smoothing
factor `alpha`. We implement the EWMA form directly for speed — the
observation-prediction residual at step t is identical to the Kalman
innovation.

Signal:
    z = (p_last - smoothed_last) / std(residuals)
    z >  +Z_THRESHOLD  →  PUT  (reversion expected)
    z <  -Z_THRESHOLD  →  CALL (reversion expected)
    otherwise         →  0.5  (no trade)

Two modes, one file:
    kalman_residual      — trade AGAINST the residual (reversion)
    kalman_residual_cont — trade WITH the residual (continuation)

Feature-vector layout (shared with every other model):
    [train_window normalized prices]         indices 0..N-1
    [8 indicators]                           indices N..N+7
    [4 cyclical time features]               indices N+8..N+11
"""

import json
from typing import List, Union

import numpy as np
import pandas as pd

from app.utils.singletons import store


_ALPHA = 0.10           # EWMA/Kalman smoothing factor (low → smoother)
_Z_THRESHOLD = 2.0      # residual z-score cutoff to fire a signal
_SIGNAL_PROB = 0.99


def _extract_price_window(feature_row: np.ndarray) -> np.ndarray:
    tail = len(store.indicator_columns) + 4
    return np.asarray(feature_row[:-tail], dtype=float)


def _kalman_smoother(prices: np.ndarray, alpha: float) -> np.ndarray:
    """Steady-state local-level Kalman = EWMA with gain=alpha."""
    n = prices.shape[0]
    s = np.empty(n, dtype=np.float64)
    s[0] = prices[0]
    # Vectorization doesn't gain much here — pure Python loop over n<=240.
    for i in range(1, n):
        s[i] = s[i - 1] + alpha * (prices[i] - s[i - 1])
    return s


def _residual_zscore(prices: np.ndarray) -> float:
    if prices.size < 16 or not np.all(np.isfinite(prices)):
        return 0.0
    smoothed = _kalman_smoother(prices, _ALPHA)
    residuals = prices - smoothed
    # Drop the warm-up portion where the filter is still catching up.
    tail = residuals[max(0, residuals.size // 4):]
    sigma = float(tail.std(ddof=0))
    if sigma <= 0.0 or not np.isfinite(sigma):
        return 0.0
    return float(residuals[-1] / sigma)


def _prob_from_z(z: float, mode: str) -> float:
    if not np.isfinite(z):
        return 0.5
    if mode == "reversion":
        if z > _Z_THRESHOLD:
            return 1.0 - _SIGNAL_PROB   # PUT: price too high → expect drop
        if z < -_Z_THRESHOLD:
            return _SIGNAL_PROB          # CALL: price too low → expect rise
    elif mode == "continuation":
        if z > _Z_THRESHOLD:
            return _SIGNAL_PROB          # CALL: ride the breakout up
        if z < -_Z_THRESHOLD:
            return 1.0 - _SIGNAL_PROB    # PUT: ride the breakout down
    return 0.5


def _decide(prob: float, trade_confidence: int) -> float:
    upper = trade_confidence / 100.0
    lower = 1.0 - upper
    if prob > upper:
        return 1
    if prob < lower:
        return 0
    return 0.5


def _predict_all(X_test, mode: str) -> List[float]:
    out: List[float] = []
    for row in X_test:
        arr = np.asarray(row, dtype=float)
        prices = _extract_price_window(arr)
        z = _residual_zscore(prices)
        out.append(_prob_from_z(z, mode))
    return out


class KalmanResidualModel:
    """E8a — bet AGAINST large residuals (reversion)."""

    name = "kalman_residual"
    MODE = "reversion"

    @staticmethod
    def model_train_model(trade_asset, trade_platform, filename_model,
                          train_window, train_horizon) -> None:
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "mode": KalmanResidualModel.MODE,
                    "alpha": _ALPHA,
                    "z_threshold": _Z_THRESHOLD,
                    "signal_prob": _SIGNAL_PROB,
                },
                f,
                indent=2,
            )

    @staticmethod
    def model_buy_sell_order(X_df: pd.DataFrame, filename_model: str,
                             trade_confidence: int) -> float:
        row = np.asarray(X_df.iloc[0].values, dtype=float)
        prices = _extract_price_window(row)
        prob = _prob_from_z(_residual_zscore(prices), KalmanResidualModel.MODE)
        return _decide(prob, trade_confidence)

    @staticmethod
    def model_run_fulltest(filename_model: str,
                           X_test: Union[List[List[float]], np.ndarray],
                           trade_confidence: int) -> List[float]:
        probs = _predict_all(X_test, KalmanResidualModel.MODE)
        return [_decide(p, trade_confidence) for p in probs]

    @staticmethod
    def model_predict_probabilities(
        filename_model: str, X_test: Union[List[List[float]], np.ndarray]
    ) -> np.ndarray:
        return np.asarray(
            _predict_all(X_test, KalmanResidualModel.MODE), dtype=np.float64
        )


class KalmanResidualContinuationModel:
    """E8b — bet WITH large residuals (breakout continuation)."""

    name = "kalman_residual_cont"
    MODE = "continuation"

    @staticmethod
    def model_train_model(trade_asset, trade_platform, filename_model,
                          train_window, train_horizon) -> None:
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "mode": KalmanResidualContinuationModel.MODE,
                    "alpha": _ALPHA,
                    "z_threshold": _Z_THRESHOLD,
                    "signal_prob": _SIGNAL_PROB,
                },
                f,
                indent=2,
            )

    @staticmethod
    def model_buy_sell_order(X_df: pd.DataFrame, filename_model: str,
                             trade_confidence: int) -> float:
        row = np.asarray(X_df.iloc[0].values, dtype=float)
        prices = _extract_price_window(row)
        prob = _prob_from_z(
            _residual_zscore(prices), KalmanResidualContinuationModel.MODE
        )
        return _decide(prob, trade_confidence)

    @staticmethod
    def model_run_fulltest(filename_model: str,
                           X_test: Union[List[List[float]], np.ndarray],
                           trade_confidence: int) -> List[float]:
        probs = _predict_all(X_test, KalmanResidualContinuationModel.MODE)
        return [_decide(p, trade_confidence) for p in probs]

    @staticmethod
    def model_predict_probabilities(
        filename_model: str, X_test: Union[List[List[float]], np.ndarray]
    ) -> np.ndarray:
        return np.asarray(
            _predict_all(X_test, KalmanResidualContinuationModel.MODE),
            dtype=np.float64,
        )

"""E6 — RSI mean-reversion rule.

Hypothesis: on 1-minute OTC binaries, when RSI(14) prints an extreme
(> 75 or < 25) the next bar often retraces. If that retracement beats
the payout floor more than 5 pp of the time, rule > model on this
feature layout — which is exactly the conclusion the ML experiments
(E1–E4) pushed us toward.

Signal:
    rsi_14 > 75  → PUT  (prob 0.01)
    rsi_14 < 25  → CALL (prob 0.99)
    otherwise    → 0.5  (no trade)

Feature-vector layout (shared with every other model):
    [train_window normalized prices]         indices 0..N-1
    [8 indicators]                           indices N..N+7
      [0] rsi_14  [1] macd  [2] macd_signal  [3] macd_hist
      [4] bb_pos  [5] atr_14 [6] roc_10      [7] vol_30
    [4 cyclical time features]               indices N+8..N+11
"""

import json
from typing import List, Union

import numpy as np
import pandas as pd

from app.utils.singletons import store


_RSI_IDX_FROM_INDICATORS = 0   # position within the 8-indicator block
_OVERBOUGHT = 75.0
_OVERSOLD = 25.0
_SIGNAL_PROB = 0.99


def _extract_rsi(feature_row: np.ndarray) -> float:
    tail = len(store.indicator_columns) + 4
    indicator_block_start = len(feature_row) - tail
    return float(feature_row[indicator_block_start + _RSI_IDX_FROM_INDICATORS])


def _prob_from_rsi(rsi: float) -> float:
    if not np.isfinite(rsi):
        return 0.5
    if rsi > _OVERBOUGHT:
        return 1.0 - _SIGNAL_PROB
    if rsi < _OVERSOLD:
        return _SIGNAL_PROB
    return 0.5


class RsiMeanReversionModel:
    """E6 — bet against RSI extremes."""

    name = "rsi_mr"

    @staticmethod
    def model_train_model(trade_asset, trade_platform, filename_model,
                          train_window, train_horizon) -> None:
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "overbought": _OVERBOUGHT,
                    "oversold": _OVERSOLD,
                    "signal_prob": _SIGNAL_PROB,
                },
                f,
                indent=2,
            )

    @staticmethod
    def model_buy_sell_order(X_df: pd.DataFrame, filename_model: str,
                             trade_confidence: int) -> float:
        row = np.asarray(X_df.iloc[0].values, dtype=float)
        prob = _prob_from_rsi(_extract_rsi(row))
        upper = trade_confidence / 100.0
        lower = 1.0 - upper
        if prob > upper:
            return 1
        if prob < lower:
            return 0
        return 0.5

    @staticmethod
    def model_run_fulltest(filename_model: str,
                           X_test: Union[List[List[float]], np.ndarray],
                           trade_confidence: int) -> List[float]:
        upper = trade_confidence / 100.0
        lower = 1.0 - upper
        out: List[float] = []
        for row in X_test:
            prob = _prob_from_rsi(_extract_rsi(np.asarray(row, dtype=float)))
            if prob > upper:
                out.append(1)
            elif prob < lower:
                out.append(0)
            else:
                out.append(0.5)
        return out

    @staticmethod
    def model_predict_probabilities(
        filename_model: str, X_test: Union[List[List[float]], np.ndarray]
    ) -> np.ndarray:
        probs = np.empty(len(X_test), dtype=np.float64)
        for i, row in enumerate(X_test):
            probs[i] = _prob_from_rsi(_extract_rsi(np.asarray(row, dtype=float)))
        return probs

"""E5 — Bollinger breakout rule.

Hypothesis: when the current price pushes above the upper Bollinger
band (bb_pos > 0.9) the move often continues for at least one more
minute; symmetric below the lower band. This is a pure continuation
bet that ignores every ML consideration — we're asking whether
short-term momentum at the band edges beats the payout floor.

Alternative framing (reversion) is tested via MODE = 'reversion'
below, which flips the signal direction. A single file, two modes —
we sweep both in the same loop by instantiating two classes.

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


_BB_POS_IDX_FROM_INDICATORS = 4   # position within the 8-indicator block
_THRESHOLD = 0.9
_SIGNAL_PROB = 0.99


def _extract_bb_pos(feature_row: np.ndarray) -> float:
    tail = len(store.indicator_columns) + 4
    indicator_block_start = len(feature_row) - tail
    return float(feature_row[indicator_block_start + _BB_POS_IDX_FROM_INDICATORS])


def _prob_from_bb(bb_pos: float, mode: str) -> float:
    if not np.isfinite(bb_pos):
        return 0.5
    if mode == "continuation":
        if bb_pos > _THRESHOLD:
            return _SIGNAL_PROB
        if bb_pos < -_THRESHOLD:
            return 1.0 - _SIGNAL_PROB
    elif mode == "reversion":
        if bb_pos > _THRESHOLD:
            return 1.0 - _SIGNAL_PROB
        if bb_pos < -_THRESHOLD:
            return _SIGNAL_PROB
    return 0.5


class BollingerBreakoutModel:
    """E5a — continuation when price leaves the band."""

    name = "bollinger"
    MODE = "continuation"

    @staticmethod
    def model_train_model(trade_asset, trade_platform, filename_model,
                          train_window, train_horizon) -> None:
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(
                {"mode": BollingerBreakoutModel.MODE, "threshold": _THRESHOLD,
                 "signal_prob": _SIGNAL_PROB},
                f,
                indent=2,
            )

    @staticmethod
    def model_buy_sell_order(X_df: pd.DataFrame, filename_model: str,
                             trade_confidence: int) -> float:
        row = np.asarray(X_df.iloc[0].values, dtype=float)
        prob = _prob_from_bb(_extract_bb_pos(row), BollingerBreakoutModel.MODE)
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
            prob = _prob_from_bb(
                _extract_bb_pos(np.asarray(row, dtype=float)),
                BollingerBreakoutModel.MODE,
            )
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
            probs[i] = _prob_from_bb(
                _extract_bb_pos(np.asarray(row, dtype=float)),
                BollingerBreakoutModel.MODE,
            )
        return probs


class BollingerReversionModel:
    """E5b — mean-reversion when price leaves the band."""

    name = "bollinger_rev"
    MODE = "reversion"

    @staticmethod
    def model_train_model(trade_asset, trade_platform, filename_model,
                          train_window, train_horizon) -> None:
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(
                {"mode": BollingerReversionModel.MODE, "threshold": _THRESHOLD,
                 "signal_prob": _SIGNAL_PROB},
                f,
                indent=2,
            )

    @staticmethod
    def model_buy_sell_order(X_df, filename_model, trade_confidence) -> float:
        row = np.asarray(X_df.iloc[0].values, dtype=float)
        prob = _prob_from_bb(_extract_bb_pos(row), BollingerReversionModel.MODE)
        upper = trade_confidence / 100.0
        lower = 1.0 - upper
        if prob > upper:
            return 1
        if prob < lower:
            return 0
        return 0.5

    @staticmethod
    def model_run_fulltest(filename_model, X_test, trade_confidence) -> List[float]:
        upper = trade_confidence / 100.0
        lower = 1.0 - upper
        out: List[float] = []
        for row in X_test:
            prob = _prob_from_bb(
                _extract_bb_pos(np.asarray(row, dtype=float)),
                BollingerReversionModel.MODE,
            )
            if prob > upper:
                out.append(1)
            elif prob < lower:
                out.append(0)
            else:
                out.append(0.5)
        return out

    @staticmethod
    def model_predict_probabilities(filename_model, X_test) -> np.ndarray:
        probs = np.empty(len(X_test), dtype=np.float64)
        for i, row in enumerate(X_test):
            probs[i] = _prob_from_bb(
                _extract_bb_pos(np.asarray(row, dtype=float)),
                BollingerReversionModel.MODE,
            )
        return probs

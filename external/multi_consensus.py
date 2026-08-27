"""E7 — Multi-indicator consensus rule.

Hypothesis: when at least MIN_AGREE of four directional indicators point
the same way, the consensus is a stronger signal than any one indicator
alone. Each of E5 (Bollinger) and E6 (RSI) individually failed at random
baseline on 1-minute OTC pairs — this experiment asks whether requiring
simultaneous agreement from multiple orthogonal indicators lifts the
edge out of the noise floor.

Directional votes (each produces CALL / PUT / neutral):
    rsi_14      > 50      → CALL      < 50      → PUT
    macd_hist   > 0       → CALL      < 0       → PUT
    bb_pos      > 0       → CALL      < 0       → PUT
    roc_10      > 0       → CALL      < 0       → PUT

Consensus fires when CALL votes >= MIN_AGREE or PUT votes >= MIN_AGREE.
A neutral (== 0, or non-finite) vote is not counted for either side.

Two modes, one file:
    multi_consensus      — trade WITH the majority (continuation)
    multi_consensus_rev  — trade AGAINST the majority (reversion)

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


# Indices inside the 8-indicator block.
_RSI_IDX = 0
_MACD_HIST_IDX = 3
_BB_POS_IDX = 4
_ROC_IDX = 6

_MIN_AGREE = 3          # at least 3 of 4 indicators must agree
_RSI_NEUTRAL = 50.0     # rsi > 50 is bullish, < 50 bearish
_SIGNAL_PROB = 0.99


def _indicator_block_start(feature_row: np.ndarray) -> int:
    tail = len(store.indicator_columns) + 4
    return len(feature_row) - tail


def _vote_from_row(feature_row: np.ndarray) -> int:
    """Return +1 if >= MIN_AGREE indicators are bullish, -1 if bearish, 0 else."""
    base = _indicator_block_start(feature_row)
    rsi = float(feature_row[base + _RSI_IDX])
    macd_hist = float(feature_row[base + _MACD_HIST_IDX])
    bb_pos = float(feature_row[base + _BB_POS_IDX])
    roc = float(feature_row[base + _ROC_IDX])

    bull = 0
    bear = 0
    for val, ref in (
        (rsi, _RSI_NEUTRAL),
        (macd_hist, 0.0),
        (bb_pos, 0.0),
        (roc, 0.0),
    ):
        if not np.isfinite(val):
            continue
        if val > ref:
            bull += 1
        elif val < ref:
            bear += 1

    if bull >= _MIN_AGREE:
        return 1
    if bear >= _MIN_AGREE:
        return -1
    return 0


def _prob_from_vote(vote: int, mode: str) -> float:
    if vote == 0:
        return 0.5
    if mode == "continuation":
        return _SIGNAL_PROB if vote > 0 else 1.0 - _SIGNAL_PROB
    # reversion
    return 1.0 - _SIGNAL_PROB if vote > 0 else _SIGNAL_PROB


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
        vote = _vote_from_row(arr)
        out.append(_prob_from_vote(vote, mode))
    return out


class MultiConsensusModel:
    """E7a — trade WITH the 3-of-4 consensus direction."""

    name = "multi_consensus"
    MODE = "continuation"

    @staticmethod
    def model_train_model(trade_asset, trade_platform, filename_model,
                          train_window, train_horizon) -> None:
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "mode": MultiConsensusModel.MODE,
                    "min_agree": _MIN_AGREE,
                    "signal_prob": _SIGNAL_PROB,
                },
                f,
                indent=2,
            )

    @staticmethod
    def model_buy_sell_order(X_df: pd.DataFrame, filename_model: str,
                             trade_confidence: int) -> float:
        row = np.asarray(X_df.iloc[0].values, dtype=float)
        prob = _prob_from_vote(_vote_from_row(row), MultiConsensusModel.MODE)
        return _decide(prob, trade_confidence)

    @staticmethod
    def model_run_fulltest(filename_model: str,
                           X_test: Union[List[List[float]], np.ndarray],
                           trade_confidence: int) -> List[float]:
        probs = _predict_all(X_test, MultiConsensusModel.MODE)
        return [_decide(p, trade_confidence) for p in probs]

    @staticmethod
    def model_predict_probabilities(
        filename_model: str, X_test: Union[List[List[float]], np.ndarray]
    ) -> np.ndarray:
        return np.asarray(
            _predict_all(X_test, MultiConsensusModel.MODE), dtype=np.float64
        )


class MultiConsensusReversionModel:
    """E7b — trade AGAINST the 3-of-4 consensus direction."""

    name = "multi_consensus_rev"
    MODE = "reversion"

    @staticmethod
    def model_train_model(trade_asset, trade_platform, filename_model,
                          train_window, train_horizon) -> None:
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "mode": MultiConsensusReversionModel.MODE,
                    "min_agree": _MIN_AGREE,
                    "signal_prob": _SIGNAL_PROB,
                },
                f,
                indent=2,
            )

    @staticmethod
    def model_buy_sell_order(X_df: pd.DataFrame, filename_model: str,
                             trade_confidence: int) -> float:
        row = np.asarray(X_df.iloc[0].values, dtype=float)
        prob = _prob_from_vote(
            _vote_from_row(row), MultiConsensusReversionModel.MODE
        )
        return _decide(prob, trade_confidence)

    @staticmethod
    def model_run_fulltest(filename_model: str,
                           X_test: Union[List[List[float]], np.ndarray],
                           trade_confidence: int) -> List[float]:
        probs = _predict_all(X_test, MultiConsensusReversionModel.MODE)
        return [_decide(p, trade_confidence) for p in probs]

    @staticmethod
    def model_predict_probabilities(
        filename_model: str, X_test: Union[List[List[float]], np.ndarray]
    ) -> np.ndarray:
        return np.asarray(
            _predict_all(X_test, MultiConsensusReversionModel.MODE),
            dtype=np.float64,
        )

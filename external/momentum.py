import json
import numpy as np
import pandas as pd
from typing import List, Union

from app.utils.singletons import store


class MomentumModel:
    """
    Rule-based model for short-term momentum trading on 1-minute bars.

    Idea: when the last N 1-minute returns all go in the same direction and
    the cumulative move exceeds a threshold, take a position — either
    continuing the trend (continuation) or fading it (reversion).

    No machine learning — the "training" step just persists the current
    hyperparameters into the model file so the fulltest / predict path can
    read them back. To experiment, edit the constants below (or the saved
    JSON) and retrain.
    """

    name = "momentum"

    # How many recent 1-minute bars form the signal window.
    LOOKBACK_MINUTES = 5

    # Minimum cumulative move over LOOKBACK_MINUTES, in percent. 0.30 = 0.30%.
    # Stricter than the first sweep (0.15% / 3 min) to surface only clear
    # micro-trends — trade count will drop, but signal-to-noise should
    # improve if a real edge exists.
    MIN_MOVE_PCT = 0.30

    # If True, every 1-minute bar in the lookback window must go the same
    # direction. If False, only the cumulative sign matters.
    REQUIRE_MONOTONE = True

    # "continuation" = bet in the direction of the move (trend-follow)
    # "reversion"    = bet against the move (mean-revert)
    # The first sweep (0.15% / 3 min) showed continuation winning on every
    # positive-edge candidate (USDIDR, USDPKR, EURGBP) — default to that.
    DIRECTION = "continuation"

    # Probability emitted on a valid signal. 0.99 guarantees the confidence
    # filter triggers for any trade_confidence setting <= 99. Symmetric for
    # the opposite direction.
    SIGNAL_PROB = 0.99

    @staticmethod
    def _load_params(filename_model: str) -> dict:
        try:
            with open(filename_model, "r", encoding="utf-8") as f:
                params = json.load(f)
            if isinstance(params, dict) and "lookback_minutes" in params:
                return params
        except Exception:
            pass
        # Fallback to class defaults if the file is missing or malformed.
        return MomentumModel._default_params()

    @staticmethod
    def _default_params() -> dict:
        return {
            "lookback_minutes": MomentumModel.LOOKBACK_MINUTES,
            "min_move_pct": MomentumModel.MIN_MOVE_PCT,
            "require_monotone": MomentumModel.REQUIRE_MONOTONE,
            "direction": MomentumModel.DIRECTION,
            "signal_prob": MomentumModel.SIGNAL_PROB,
        }

    @staticmethod
    def _prob_for_window(window_norm: np.ndarray, params: dict) -> float:
        """Compute BUY probability for a single normalized price window.

        window_norm[i] = P_i / P_0 - 1, so (window_norm[i] + 1) = P_i / P_0.
        The 1-minute return r_i = P_i/P_{i-1} - 1 = (x_i+1)/(x_{i-1}+1) - 1.
        """
        lookback = int(params["lookback_minutes"])
        min_move = float(params["min_move_pct"]) / 100.0
        require_monotone = bool(params["require_monotone"])
        direction = params["direction"]
        signal = float(params["signal_prob"])

        if len(window_norm) < lookback + 1:
            return 0.5

        last = window_norm[-(lookback + 1):]
        if not np.all(np.isfinite(last)):
            return 0.5

        # Cumulative return across the full lookback window.
        cum_return = (last[-1] + 1.0) / (last[0] + 1.0) - 1.0

        if require_monotone:
            ratios = (last[1:] + 1.0) / (last[:-1] + 1.0)
            per_bar_returns = ratios - 1.0
            all_up = bool(np.all(per_bar_returns > 0))
            all_down = bool(np.all(per_bar_returns < 0))
        else:
            all_up = cum_return > 0
            all_down = cum_return < 0

        if all_up and cum_return >= min_move:
            return signal if direction == "continuation" else 1.0 - signal
        if all_down and cum_return <= -min_move:
            return 1.0 - signal if direction == "continuation" else signal

        return 0.5

    @staticmethod
    def _extract_price_window(feature_row: np.ndarray) -> np.ndarray:
        """Strip off indicator + time-feature tail to get just the normalized
        price window. Layout (see order.py / fulltest.py):
            [0 : train_window]            normalized prices
            [train_window : +8]           indicator snapshot
            [train_window+8 : +4]         cyclical time features (sin/cos × 2)
        """
        tail = len(store.indicator_columns) + 4
        return np.asarray(feature_row[:-tail], dtype=float)

    @staticmethod
    def model_train_model(
        trade_asset: str,
        trade_platform: str,
        filename_model: str,
        train_window: int,
        train_horizon: int,
    ) -> None:
        # No actual training — persist the current hyperparameters so predict
        # and fulltest can read them back from a stable file.
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(MomentumModel._default_params(), f, indent=2)

    @staticmethod
    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        params = MomentumModel._load_params(filename_model)
        row = X_df.iloc[0].values
        prices = MomentumModel._extract_price_window(row)
        prob = MomentumModel._prob_for_window(prices, params)

        upper = trade_confidence / 100.0
        lower = 1.0 - upper
        if prob > upper:
            return 1
        if prob < lower:
            return 0
        return 0.5

    @staticmethod
    def model_run_fulltest(
        filename_model: str,
        X_test: Union[List[List[float]], np.ndarray],
        trade_confidence: int,
    ) -> List[float]:
        params = MomentumModel._load_params(filename_model)
        upper = trade_confidence / 100.0
        lower = 1.0 - upper

        predictions: List[float] = []
        for row in X_test:
            prices = MomentumModel._extract_price_window(np.asarray(row))
            prob = MomentumModel._prob_for_window(prices, params)
            if prob > upper:
                predictions.append(1)
            elif prob < lower:
                predictions.append(0)
            else:
                predictions.append(0.5)
        return predictions

    @staticmethod
    def model_predict_probabilities(
        filename_model: str, X_test: Union[List[List[float]], np.ndarray]
    ) -> List[float]:
        """Return raw BUY probabilities [0..1] for each sample."""
        params = MomentumModel._load_params(filename_model)
        probs: List[float] = []
        for row in X_test:
            prices = MomentumModel._extract_price_window(np.asarray(row))
            probs.append(MomentumModel._prob_for_window(prices, params))
        return probs

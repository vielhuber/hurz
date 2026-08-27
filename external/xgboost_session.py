"""XGBoost gated on session hours.

Hypothesis: binary-option edges concentrate in the overlapping US/EU
trading sessions. By only taking trades when the UTC hour is in the
overlap bands (08..11 and 14..17), the success-rate edge per trade
should improve even if total trade count drops.

Design:
  - Training delegates to the vanilla XGBoost ensemble (5-seed,
    temperature-scaled) — same features, same split.
  - At predict time we recover UTC hour from the cyclical sin/cos
    features at the end of the feature vector and mask out predictions
    outside the session window by returning 0.5 (no trade).
"""

import importlib.util
import math
import os
import numpy as np
import pandas as pd
from typing import List, Union

from app.utils.singletons import store, utils


def _import_xgboost_module():
    """Load the sibling xgboost.py module at runtime."""
    existing = store.model_classes.get("XGBoost")
    if existing is not None:
        import sys
        mod = sys.modules.get(existing.__module__)
        if mod is not None and all(hasattr(mod, n) for n in (
            "_load_ensemble", "_ensemble_predict_probs"
        )):
            return mod
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "xgboost.py"),
        os.path.join(here, "..", "external", "xgboost.py"),
    ]
    for cand in candidates:
        cand = os.path.abspath(cand)
        if os.path.exists(cand):
            spec = importlib.util.spec_from_file_location("hurz_xgboost_base", cand)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise ImportError("Could not locate xgboost.py next to xgboost_session.py")


_xgb_mod = _import_xgboost_module()
XGBoostModel = _xgb_mod.XGBoostModel
_load_ensemble = _xgb_mod._load_ensemble
_ensemble_predict_probs = _xgb_mod._ensemble_predict_probs


# Feature layout (see fulltest.py):
#   [train_window normalized prices] + [8 indicators] + [4 time features]
# Time features in order: [sin(2πh/24), cos(2πh/24), sin(2πd/5), cos(2πd/5)].
# Hour sin is 4 from the end, hour cos is 3 from the end.
HOUR_SIN_OFFSET = -4
HOUR_COS_OFFSET = -3

# UTC session bands where the London/NY liquidity is typically active.
# Closed-open on the right: [8,11) covers 08:00-10:59, [14,17) covers
# 14:00-16:59. This excludes thin Asian hours and the post-NY drift.
SESSION_BANDS_UTC = ((8, 11), (14, 17))


def _recover_hours_utc(X_np: np.ndarray) -> np.ndarray:
    """Return the UTC hour (float in [0, 24)) for each sample.

    Inverts the cyclical encoding h_sin = sin(2πh/24), h_cos = cos(2πh/24)
    via atan2. Values outside the input range are clipped.
    """
    h_sin = X_np[:, HOUR_SIN_OFFSET].astype(np.float64)
    h_cos = X_np[:, HOUR_COS_OFFSET].astype(np.float64)
    angle = np.arctan2(h_sin, h_cos)
    # arctan2 returns [-π, π]; convert to [0, 24)
    hours = angle * 24.0 / (2.0 * math.pi)
    hours = np.where(hours < 0, hours + 24.0, hours)
    return hours


def _session_mask(X_np: np.ndarray) -> np.ndarray:
    hours = _recover_hours_utc(X_np)
    mask = np.zeros(len(hours), dtype=bool)
    for lo, hi in SESSION_BANDS_UTC:
        mask |= (hours >= lo) & (hours < hi)
    return mask


class XGBoostSessionModel:

    name = "xgboost_session"

    @staticmethod
    def model_train_model(
        trade_asset: str,
        trade_platform: str,
        filename_model: str,
        train_window: int,
        train_horizon: int,
    ) -> None:
        # Delegate fully to XGBoost so the .ensemble.pkl sidecar lands
        # next to this wrapper's model filename.
        return XGBoostModel.model_train_model(
            trade_asset, trade_platform, filename_model, train_window, train_horizon
        )

    @staticmethod
    def _gated_probs(filename_model: str, X_np: np.ndarray) -> np.ndarray:
        """Return XGBoost probabilities with non-session samples forced to 0.5."""
        members = _load_ensemble(filename_model)
        if members is None:
            return np.full(len(X_np), 0.5, dtype=np.float64)
        try:
            probs = _ensemble_predict_probs(members, X_np)
        finally:
            for member, _ in members:
                del member
            del members

        if len(X_np) == 0:
            return probs

        in_session = _session_mask(X_np)
        probs = np.where(in_session, probs, 0.5)
        return probs

    @staticmethod
    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        X_np = np.asarray(X_df, dtype=np.float32)
        probs = XGBoostSessionModel._gated_probs(filename_model, X_np)
        prediction = float(probs[0])
        utils.print(
            f"ℹ️ Session Prediction: {prediction:.4f} "
            f"(in_session={bool(_session_mask(X_np)[0])})",
            1,
        )

        upper = trade_confidence / 100
        lower = 1 - upper
        if prediction > upper:
            return 1
        if prediction < lower:
            return 0
        return 0.5

    @staticmethod
    def model_run_fulltest(
        filename_model: str,
        X_test: Union[List[List[float]], np.ndarray],
        trade_confidence: int,
    ) -> List[float]:
        X_np = np.asarray(X_test, dtype=np.float32)
        probs = XGBoostSessionModel._gated_probs(filename_model, X_np)
        upper = trade_confidence / 100
        lower = 1 - upper
        out: List[float] = []
        for p in probs:
            if p > upper:
                out.append(1)
            elif p < lower:
                out.append(0)
            else:
                out.append(0.5)
        return out

    @staticmethod
    def model_predict_probabilities(
        filename_model: str,
        X_test: Union[List[List[float]], np.ndarray],
    ) -> np.ndarray:
        X_np = np.asarray(X_test, dtype=np.float32)
        return XGBoostSessionModel._gated_probs(filename_model, X_np)

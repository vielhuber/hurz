"""XGBoost with a volatility regime filter.

Hypothesis: the XGBoost ensemble has real signal during high-volatility
regimes and is pure noise during low-volatility regimes. By only taking
trades when `indicator_vol_30` is above a learned threshold, the
success-rate edge per trade should improve (at the cost of trade count).

Design:
  - Training delegates entirely to XGBoostModel — same 5-seed ensemble,
    same calibration. A fresh ensemble is trained under this wrapper's
    filename so the fulltest paths do not collide with vanilla xgboost.
  - After training we compute an asset-specific threshold from the training
    data's vol_30 distribution (default: 33rd percentile) and persist it
    in a JSON sidecar next to the model.
  - At predict time we load the ensemble, pull vol_30 from the feature row
    (fixed position `-7`: 8 indicators precede 4 time features, vol_30 is
    the 6th indicator), and return XGBoost's calibrated probability if
    vol_30 >= threshold, else 0.5 (no trade).
"""

import importlib.util
import json
import os
import numpy as np
import pandas as pd
from typing import List, Union

from app.utils.singletons import store, utils, database


def _import_xgboost_module():
    """Load the sibling xgboost.py model module at runtime.

    settings.load_externals() loads each file in external/ as a standalone
    module (no package __init__.py), so `from external.xgboost import ...`
    would be fragile. Resolve the path explicitly instead.
    """
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
    raise ImportError("Could not locate xgboost.py next to xgboost_volgated.py")


_xgb_mod = _import_xgboost_module()
XGBoostModel = _xgb_mod.XGBoostModel
_load_ensemble = _xgb_mod._load_ensemble
_ensemble_predict_probs = _xgb_mod._ensemble_predict_probs


# Index of indicator_vol_30 from the END of the feature vector.
# Layout (see fulltest.py):
#   [train_window normalized prices] + [8 indicators] + [4 time features]
# indicator_columns ordering (store.py): rsi_14, macd, macd_signal,
# macd_hist, bb_pos, atr_14, roc_10, vol_30  → vol_30 is the 6th indicator
# (0-indexed: 5). From the end that is: -(4 + (8 - 5)) = -7.
VOL_30_OFFSET_FROM_END = -7

# Percentile of the training distribution below which we refuse to trade.
# 33 means "skip the calmest third of bars"; keep ~2/3 of samples for
# prediction. A higher number is stricter (fewer trades, hopefully higher
# precision); lower is looser.
VOL_PERCENTILE_CUT = 33.0


def _sidecar_path(filename_model: str) -> str:
    base, _ = os.path.splitext(filename_model)
    return base + ".volgate.json"


def _compute_vol30_threshold(trade_asset: str, trade_platform: str) -> float:
    """Compute the threshold from the training half of the data.

    Mirrors the training slice used by XGBoostModel (first 50% of rows).
    Returns the VOL_PERCENTILE_CUT percentile of finite vol_30 values, or
    0.0 if no data is available (degenerates to "never gate").
    """
    rows = database.select(
        "SELECT indicator_vol_30 FROM trading_data "
        "WHERE trade_asset = %s AND trade_platform = %s "
        "ORDER BY timestamp",
        (trade_asset, trade_platform),
    )
    if not rows:
        return 0.0
    vals = np.array(
        [r["indicator_vol_30"] for r in rows if r.get("indicator_vol_30") is not None],
        dtype=float,
    )
    if vals.size == 0:
        return 0.0
    # Match the training slice: first half only.
    half = len(vals) // 2
    vals = vals[:half] if half > 0 else vals
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return 0.0
    return float(np.percentile(vals, VOL_PERCENTILE_CUT))


class XGBoostVolGatedModel:

    name = "xgboost_volgated"

    @staticmethod
    def model_train_model(
        trade_asset: str,
        trade_platform: str,
        filename_model: str,
        train_window: int,
        train_horizon: int,
    ) -> None:
        # Delegate to vanilla XGBoost training under OUR filename so the
        # .ensemble.pkl sidecar lands next to this wrapper's .json model.
        result = XGBoostModel.model_train_model(
            trade_asset, trade_platform, filename_model, train_window, train_horizon
        )

        # Persist the asset-specific volatility threshold alongside the model.
        threshold = _compute_vol30_threshold(trade_asset, trade_platform)
        sidecar = {
            "vol30_threshold": threshold,
            "percentile_cut": VOL_PERCENTILE_CUT,
            "offset_from_end": VOL_30_OFFSET_FROM_END,
        }
        try:
            with open(_sidecar_path(filename_model), "w", encoding="utf-8") as f:
                json.dump(sidecar, f, indent=2)
            utils.print(
                f"ℹ️ {trade_asset}: vol30 gate threshold "
                f"{threshold:.6f} (p{VOL_PERCENTILE_CUT:.0f})",
                0,
            )
        except Exception as e:
            utils.print(f"⛔ Failed to write volgate sidecar: {e}", 0)

        return result

    @staticmethod
    def _load_threshold(filename_model: str) -> float:
        path = _sidecar_path(filename_model)
        if not os.path.exists(path):
            return 0.0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return float(data.get("vol30_threshold", 0.0))
        except Exception:
            return 0.0

    @staticmethod
    def _gated_probs(filename_model: str, X_np: np.ndarray) -> np.ndarray:
        """Return XGBoost probabilities with low-vol samples forced to 0.5.

        Single ensemble load + single GPU inference pass for the whole
        batch; then a mask replaces low-vol predictions with 0.5 so the
        fulltest sweep treats them as skipped.
        """
        members = _load_ensemble(filename_model)
        if members is None:
            return np.full(len(X_np), 0.5, dtype=np.float64)
        try:
            probs = _ensemble_predict_probs(members, X_np)
        finally:
            for member, _ in members:
                del member
            del members

        threshold = XGBoostVolGatedModel._load_threshold(filename_model)
        if threshold <= 0.0 or len(X_np) == 0:
            return probs

        vol30 = X_np[:, VOL_30_OFFSET_FROM_END].astype(np.float64)
        low_vol = ~np.isfinite(vol30) | (vol30 < threshold)
        probs = np.where(low_vol, 0.5, probs)
        return probs

    @staticmethod
    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        X_np = np.asarray(X_df, dtype=np.float32)
        probs = XGBoostVolGatedModel._gated_probs(filename_model, X_np)
        prediction = float(probs[0])
        utils.print(
            f"ℹ️ VolGated Prediction: {prediction:.4f} "
            f"(threshold={XGBoostVolGatedModel._load_threshold(filename_model):.6f})",
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
        probs = XGBoostVolGatedModel._gated_probs(filename_model, X_np)
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
        return XGBoostVolGatedModel._gated_probs(filename_model, X_np)

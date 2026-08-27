"""XGBoost variant with deeper trees and a higher learning rate.

The canonical ensemble's `max_depth=3` +
`learning_rate=0.02` is under-powered for CHFJPY @ 3600 s. A slightly
deeper model with a faster learner may capture non-linear interactions
between the price window and the 8 indicators + 4 time features.

Design:
  - Training delegates entirely to XGBoostModel (same 5-seed ensemble,
    same feature split, same calibration) but monkey-patches
    xgb.XGBClassifier.__init__ so each seed is instantiated with
    max_depth=5, learning_rate=0.05. The original __init__ is restored
    in a finally block, so other models trained later in the same hurz
    process are not affected.
  - Inference delegates 1:1 — the ensemble bundle on disk already
    contains fully-trained boosters, no hyperparams needed at predict
    time.

"""

import importlib.util
import os
import numpy as np
import pandas as pd
import xgboost as xgb
from typing import List, Union

from app.utils.singletons import store


def _import_xgboost_module():
    """Load the sibling xgboost.py model module at runtime."""
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
    raise ImportError("Could not locate xgboost.py next to xgboost_deep.py")


_xgb_mod = _import_xgboost_module()
XGBoostModel = _xgb_mod.XGBoostModel
_load_ensemble = _xgb_mod._load_ensemble
_ensemble_predict_probs = _xgb_mod._ensemble_predict_probs


# Hyperparameter overrides injected into every XGBClassifier instantiated
# during this variant's training pass. Keep the list tight: each override
# is an explicit hypothesis we want to test against the baseline.
HYPERPARAM_OVERRIDES = {
    "max_depth": 5,
    "learning_rate": 0.05,
}


class XGBoostDeepModel:

    name = "xgboost_deep"

    @staticmethod
    def model_train_model(
        trade_asset: str,
        trade_platform: str,
        filename_model: str,
        train_window: int,
        train_horizon: int,
    ) -> None:
        # Monkey-patch xgb.XGBClassifier so every ensemble seed picks up
        # the deeper / faster-learning hyperparams without touching the
        # canonical xgboost.py. Restore the original init in finally so
        # a later `--auto-train` run of vanilla XGBoost is unaffected.
        original_init = xgb.XGBClassifier.__init__

        def patched_init(self, **kwargs):
            for key, value in HYPERPARAM_OVERRIDES.items():
                kwargs[key] = value
            return original_init(self, **kwargs)

        xgb.XGBClassifier.__init__ = patched_init
        try:
            return XGBoostModel.model_train_model(
                trade_asset, trade_platform, filename_model, train_window, train_horizon
            )
        finally:
            xgb.XGBClassifier.__init__ = original_init

    @staticmethod
    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        # Inference is identical to vanilla XGBoost — the ensemble bundle
        # on disk is a fully-trained set of boosters + calibrators.
        return XGBoostModel.model_buy_sell_order(X_df, filename_model, trade_confidence)

    @staticmethod
    def model_run_fulltest(
        filename_model: str,
        X_test: Union[List[List[float]], np.ndarray],
        trade_confidence: int,
    ) -> List[float]:
        # The canonical XGBoostModel does not expose model_run_fulltest
        # directly (fulltest.py probes for it). Replicate the minimal
        # logic: load ensemble, predict, threshold per-sample.
        X_np = np.asarray(X_test, dtype=np.float32)
        members = _load_ensemble(filename_model)
        if members is None:
            return [0.5] * len(X_np)
        try:
            probs = _ensemble_predict_probs(members, X_np)
        finally:
            for member, _ in members:
                del member
            del members

        upper = trade_confidence / 100
        lower = 1 - upper
        out: List[float] = []
        for p in probs:
            if p > upper:
                out.append(1.0)
            elif p < lower:
                out.append(0.0)
            else:
                out.append(0.5)
        return out

    @staticmethod
    def model_predict_probabilities(
        filename_model: str,
        X_test: Union[List[List[float]], np.ndarray],
    ) -> np.ndarray:
        return XGBoostModel.model_predict_probabilities(filename_model, X_test)

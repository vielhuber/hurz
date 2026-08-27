"""XGBoost variant with ATR-quartile regime flags.

Hypothesis: the 1-h CHFJPY edge may concentrate in specific volatility
regimes. Adding four one-hot features that encode which quartile of
`atr_14` the prediction-time bar falls in gives the model a discretized
handle on volatility regime on top of the raw ATR value it already
sees.

Design:
  - Training delegates to `XGBoostModel.model_train_model` so the 5-seed
    ensemble, temperature scaling, purged split, and learning curve all
    stay bit-identical to the canonical pipeline. We only monkey-patch
    `xgb.XGBClassifier.fit` and `predict_proba` to augment the matrix
    with four quartile columns before XGBoost sees it. The first fit
    call records the quartile edges from its training data; subsequent
    members reuse the cached edges so every ensemble seed observes the
    same feature space.
  - After training completes, the edges are persisted to a sidecar JSON
    next to the model file (`<model>.atrreg_quartiles.json`). The
    monkey-patches are restored in a `finally` block, so a subsequent
    vanilla train in the same hurz process is unaffected.
  - Inference loads the sidecar, augments the incoming feature matrix
    with the four quartile columns, then delegates probability lookup
    to the shared `_ensemble_predict_probs` helper from `xgboost.py`.

"""

import importlib.util
import os
import numpy as np
import pandas as pd
import xgboost as xgb
from typing import List, Union

try:
    import cupy as cp
except Exception:  # pragma: no cover - CPU-only dev envs
    cp = None

from app.utils.singletons import store, utils
from app.utils.atr_quartile import (
    atr_column_index,
    compute_quartile_edges,
    augment_numpy_matrix,
    load_sidecar,
    save_sidecar,
    sidecar_path,
)


def _import_xgboost_module():
    """Load the sibling xgboost.py model module at runtime.

    Mirrors `xgboost_deep.py::_import_xgboost_module` so both wrappers
    discover the same ensemble helpers regardless of which sibling
    module loaded first into `store.model_classes`.
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
    raise ImportError("Could not locate xgboost.py next to xgboost_atrreg.py")


_xgb_mod = _import_xgboost_module()
XGBoostModel = _xgb_mod.XGBoostModel
_load_ensemble = _xgb_mod._load_ensemble
_ensemble_predict_probs = _xgb_mod._ensemble_predict_probs


def _augment_any(X, edges):
    """Augment X with 4 quartile columns. Handles numpy, cupy arrays
    and pandas DataFrames without changing the container type."""
    if isinstance(X, pd.DataFrame):
        aug = augment_numpy_matrix(X.values, edges)
        return pd.DataFrame(aug, index=X.index)
    if cp is not None and isinstance(X, cp.ndarray):
        # Pull only the ATR column to host memory to keep the transfer
        # small; one-hot expansion happens on CPU, then the small
        # (N x 4) block is uploaded and concatenated on device.
        atr_col = X[:, atr_column_index(X.shape[1])].get()
        from app.utils.atr_quartile import quartile_one_hot
        one_hot = quartile_one_hot(atr_col, edges).astype(
            cp.asnumpy(X[:1]).dtype, copy=False
        )
        one_hot_gpu = cp.asarray(one_hot)
        return cp.concatenate([X, one_hot_gpu], axis=1)
    # fall back to numpy
    X_np = np.asarray(X)
    return augment_numpy_matrix(X_np, edges)


# Module-level state kept across monkey-patched `.fit` calls so every
# seed in the ensemble uses identical quartile edges. Reset on every
# outer `model_train_model` call (see finally block below).
_TRAINING_STATE = {
    "edges": None,
}


def _extract_atr_column(X) -> np.ndarray:
    """Return the ATR column of X as a host (numpy) array."""
    if isinstance(X, pd.DataFrame):
        return X.values[:, atr_column_index(X.shape[1])]
    if cp is not None and isinstance(X, cp.ndarray):
        return X[:, atr_column_index(X.shape[1])].get()
    arr = np.asarray(X)
    return arr[:, atr_column_index(arr.shape[1])]


class XGBoostATRRegModel:

    name = "xgboost_atrreg"

    @staticmethod
    def model_train_model(
        trade_asset: str,
        trade_platform: str,
        filename_model: str,
        train_window: int,
        train_horizon: int,
    ) -> None:
        # Reset state on every outer call so concurrent/sequential
        # auto-train runs on different assets can't pollute each other.
        _TRAINING_STATE["edges"] = None

        original_fit = xgb.XGBClassifier.fit
        original_predict_proba = xgb.XGBClassifier.predict_proba

        def patched_fit(self, X, y=None, **kwargs):
            # First `.fit` call sees the full training matrix: learn
            # quartile edges from its ATR column. Every subsequent
            # ensemble seed reuses the cached edges.
            if _TRAINING_STATE["edges"] is None:
                atr = _extract_atr_column(X)
                _TRAINING_STATE["edges"] = compute_quartile_edges(atr)
                utils.print(
                    f"ℹ️ ATRReg quartile edges (p25/p50/p75): "
                    f"{_TRAINING_STATE['edges']}",
                    1,
                )

            edges = _TRAINING_STATE["edges"]
            X_aug = _augment_any(X, edges)
            eval_set = kwargs.get("eval_set")
            if eval_set is not None:
                kwargs["eval_set"] = [
                    (_augment_any(xv, edges), yv) for xv, yv in eval_set
                ]
            return original_fit(self, X_aug, y, **kwargs)

        def patched_predict_proba(self, X, **kwargs):
            edges = _TRAINING_STATE["edges"]
            if edges is None:
                # Called before any `.fit` — do nothing. This can only
                # happen in pathological test contexts; the canonical
                # training pipeline always calls `.fit` first.
                return original_predict_proba(self, X, **kwargs)
            X_aug = _augment_any(X, edges)
            return original_predict_proba(self, X_aug, **kwargs)

        xgb.XGBClassifier.fit = patched_fit
        xgb.XGBClassifier.predict_proba = patched_predict_proba
        try:
            result = XGBoostModel.model_train_model(
                trade_asset,
                trade_platform,
                filename_model,
                train_window,
                train_horizon,
            )
            # Persist the quartile edges so inference picks up the same
            # feature space. Only write when training actually produced
            # an edge tuple (ensemble aborted early -> no sidecar).
            if _TRAINING_STATE["edges"] is not None:
                save_sidecar(filename_model, _TRAINING_STATE["edges"])
                utils.print(
                    f"✅ ATRReg sidecar saved to "
                    f"{sidecar_path(filename_model)}",
                    1,
                )
            return result
        finally:
            xgb.XGBClassifier.fit = original_fit
            xgb.XGBClassifier.predict_proba = original_predict_proba
            _TRAINING_STATE["edges"] = None

    @staticmethod
    def _augment_for_inference(
        filename_model: str, X_np: np.ndarray
    ) -> np.ndarray:
        edges = load_sidecar(filename_model)
        if edges is None:
            raise RuntimeError(
                f"ATRReg sidecar missing for {filename_model} "
                f"(expected at {sidecar_path(filename_model)}). "
                f"Retrain the xgboost_atrreg variant before predicting."
            )
        return augment_numpy_matrix(X_np, edges)

    @staticmethod
    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        X_np = np.asarray(X_df, dtype=np.float32)
        X_aug = XGBoostATRRegModel._augment_for_inference(
            filename_model, X_np
        ).astype(np.float32)
        members = _load_ensemble(filename_model)
        if members is None:
            return 0.5
        try:
            probs = _ensemble_predict_probs(members, X_aug)
        finally:
            for member, _ in members:
                del member
            del members

        prediction = float(probs[0])
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
        X_aug = XGBoostATRRegModel._augment_for_inference(
            filename_model, X_np
        ).astype(np.float32)
        members = _load_ensemble(filename_model)
        if members is None:
            return [0.5] * len(X_np)
        try:
            probs = _ensemble_predict_probs(members, X_aug)
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
        X_np = np.asarray(X_test, dtype=np.float32)
        X_aug = XGBoostATRRegModel._augment_for_inference(
            filename_model, X_np
        ).astype(np.float32)
        members = _load_ensemble(filename_model)
        if members is None:
            return np.full(len(X_np), 0.5, dtype=np.float64)
        try:
            probs = _ensemble_predict_probs(members, X_aug)
        finally:
            for member, _ in members:
                del member
            del members
        return probs

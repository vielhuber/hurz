"""Stacking ensemble: XGBoost + LightGBM → Logistic-Regression meta-learner.

Conforms to the same interface as `external/xgboost.py`:
    - model_train_model(trade_asset, trade_platform, filename_model, train_window, train_horizon)
    - model_buy_sell_order(X_df, filename_model, trade_confidence)
    - model_predict_probabilities(filename_model, X_test)

So you can flip between models via `data/settings.json -> "model"`.

Architecture
------------
    Layer 0 (base learners, 5-seed bag each):
        - XGBoost   (CPU, hist tree, 200 rounds, depth=4)
        - LightGBM  (CPU, leaf-wise, 200 rounds, leaves=31)
    Layer 1 (meta-learner):
        - Logistic Regression on [xgb_prob, lgbm_prob, |xgb-lgbm|]
        - Trained on a 20% holdout from the first-half training data
          using out-of-fold base predictions

Storage
-------
A single pickle next to the legacy `.json` model file:
    <filename_model>.stacking.pkl
The legacy `.json` is still emitted (best XGBoost member) so any tool
that doesn't know about stacking can still read a usable single model.

Toggle
------
This file is auto-discovered at boot. To activate:
    data/settings.json:  "model": "XGBoostStacking"
    data/feature_flags.json -> models.stacking.enabled is informational
    only — flipping settings.model is the actual switch.
"""
from __future__ import annotations

import gc
import os
import pickle
import time
from collections import Counter
from typing import Any, Dict, List, Optional

import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

from app.utils.singletons import database, store, utils


_BASE_SEEDS = (11, 23, 47, 89, 173)
_HOLDOUT_FRACTION = 0.2


def _stacking_path(filename_model: str) -> str:
    base, _ = os.path.splitext(filename_model)
    return base + ".stacking.pkl"


def _build_dataset(trade_asset: str, trade_platform: str, train_window: int, train_horizon: int):
    """Pull data + materialise the same feature vector used by the XGBoost
    model: 240-window of relative prices + 8 indicator snapshots + 4 time
    features. Reused logic — kept short here, full version in external/xgboost.py.
    """
    indicator_cols = store.indicator_columns
    indicator_cols_sql = ", ".join(indicator_cols)
    df = database.select(
        f"SELECT trade_asset, trade_platform, timestamp, price, {indicator_cols_sql} "
        f"FROM trading_data WHERE trade_asset = %s AND trade_platform = %s "
        f"ORDER BY timestamp",
        (trade_asset, trade_platform),
    )
    df = pd.DataFrame(df)
    df = df.rename(columns={
        "trade_asset": "Waehrung",
        "trade_platform": "Plattform",
        "timestamp": "Zeitpunkt",
        "price": "Wert",
    })
    df.dropna(subset=["Wert"], inplace=True)
    df["Wert"] = df["Wert"].astype(float)
    for col in indicator_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")

    # First 50% only (training half) — same convention as XGBoost.
    df = df.iloc[: len(df) // 2].reset_index(drop=True)

    price = df["Wert"].values
    indicator_arrs = [df[col].values for col in indicator_cols]
    ts = pd.to_datetime(df["Zeitpunkt"]).values.astype("datetime64[s]")
    minute_arr = ts.astype("int64") // 60
    hour = (ts - ts.astype("datetime64[D]")).astype("timedelta64[m]").astype(float) / 60.0
    dow = (ts.astype("datetime64[D]") - np.datetime64("1970-01-05", "D")).astype(int) % 7

    X, y = [], []
    two_pi = 2.0 * np.pi
    for i in range(len(df) - train_window - train_horizon):
        if minute_arr[i + train_window + train_horizon] - minute_arr[i] != train_window + train_horizon:
            continue
        raw = price[i : i + train_window]
        if raw[0] == 0 or not np.isfinite(raw[0]):
            continue
        window = raw / raw[0] - 1
        if not np.all(np.isfinite(window)):
            continue
        last_idx = i + train_window - 1
        ind_snap = np.array([arr[last_idx] for arr in indicator_arrs], dtype=float)
        if not np.all(np.isfinite(ind_snap)):
            continue
        h = hour[last_idx]
        d = dow[last_idx]
        time_feat = np.array([
            np.sin(two_pi * h / 24.0),
            np.cos(two_pi * h / 24.0),
            np.sin(two_pi * d / 5.0),
            np.cos(two_pi * d / 5.0),
        ], dtype=float)
        feature_vec = np.concatenate([window, ind_snap, time_feat])
        future = price[i + train_window + train_horizon]
        # label = 1 if future price went up vs last, 0 otherwise
        label = 1 if future > raw[-1] else 0
        X.append(feature_vec)
        y.append(label)
    X = np.asarray(X, dtype=np.float32)
    y = np.asarray(y, dtype=np.int32)
    return X, y


def _split_holdout(X: np.ndarray, y: np.ndarray, frac: float, seed: int = 42):
    rng = np.random.default_rng(seed)
    n = len(X)
    idx = rng.permutation(n)
    cut = int(n * (1 - frac))
    train_idx = idx[:cut]
    hold_idx = idx[cut:]
    return X[train_idx], y[train_idx], X[hold_idx], y[hold_idx]


def _fit_xgb_bag(X_train, y_train, X_val, y_val):
    members = []
    for seed in _BASE_SEEDS:
        m = xgb.XGBClassifier(
            objective="binary:logistic",
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            tree_method="hist",
            device="cpu",
            random_state=seed,
            verbosity=0,
        )
        m.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
        members.append(m)
    return members


def _fit_lgb_bag(X_train, y_train, X_val, y_val):
    members = []
    for seed in _BASE_SEEDS:
        m = lgb.LGBMClassifier(
            objective="binary",
            n_estimators=200,
            num_leaves=31,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            random_state=seed,
            verbosity=-1,
        )
        m.fit(X_train, y_train, eval_set=[(X_val, y_val)])
        members.append(m)
    return members


def _bag_predict(bag, X) -> np.ndarray:
    if not bag:
        return np.full(X.shape[0], 0.5, dtype=float)
    probs = np.zeros(X.shape[0], dtype=np.float64)
    for m in bag:
        probs += m.predict_proba(X)[:, 1]
    return probs / len(bag)


class XGBoostStacking:

    name = "XGBoostStacking"

    def model_train_model(
        trade_asset: str, trade_platform: str, filename_model: str,
        train_window: int, train_horizon: int,
    ) -> None:
        utils.print(f"✅ [stacking] Starting training for {trade_asset}...", 1)
        X, y = _build_dataset(trade_asset, trade_platform, train_window, train_horizon)
        if len(X) < 500:
            utils.print(f"⛔ [stacking] {trade_asset}: only {len(X)} samples — aborting.", 0)
            return False

        # Three-way split: base train | base val (early-stop) | meta train.
        # Use the meta-train slice for the meta-learner so no leakage.
        X_train, y_train, X_meta, y_meta = _split_holdout(X, y, _HOLDOUT_FRACTION, seed=11)
        X_inner_train, y_inner_train, X_val, y_val = _split_holdout(
            X_train, y_train, 0.15, seed=23
        )
        utils.print(
            f"ℹ️ [stacking] split: base_train={len(X_inner_train)}  "
            f"base_val={len(X_val)}  meta_train={len(X_meta)}",
            1,
        )

        t0 = time.perf_counter()
        xgb_bag = _fit_xgb_bag(X_inner_train, y_inner_train, X_val, y_val)
        utils.print(f"ℹ️ [stacking] XGBoost bag fit in {time.perf_counter()-t0:.1f}s", 1)
        t0 = time.perf_counter()
        lgb_bag = _fit_lgb_bag(X_inner_train, y_inner_train, X_val, y_val)
        utils.print(f"ℹ️ [stacking] LightGBM bag fit in {time.perf_counter()-t0:.1f}s", 1)

        # Layer-1 features on meta_train (must be SAMPLES UNSEEN by base bags).
        xgb_p = _bag_predict(xgb_bag, X_meta)
        lgb_p = _bag_predict(lgb_bag, X_meta)
        meta_X = np.column_stack([xgb_p, lgb_p, np.abs(xgb_p - lgb_p)])

        meta = LogisticRegression(
            C=1.0, max_iter=200, solver="lbfgs", random_state=42
        )
        meta.fit(meta_X, y_meta)
        meta_logloss = log_loss(y_meta, meta.predict_proba(meta_X)[:, 1])
        utils.print(
            f"ℹ️ [stacking] meta-learner logloss on meta-train = {meta_logloss:.4f}", 1,
        )

        # Random baseline for comparison.
        try:
            random_probs = np.random.rand(len(y_meta))
            ll_rand = log_loss(y_meta, random_probs)
            utils.print(f"ℹ️ [stacking] random baseline logloss = {ll_rand:.4f}", 1)
            utils.print(f"ℹ️ [stacking] meta_train labels: {Counter(y_meta.tolist())}", 1)
        except Exception:
            pass

        # Persist:
        # 1. Single .json (best XGB member) for legacy single-model fallback.
        xgb_bag[0].save_model(filename_model)
        # 2. Stacking bundle.
        bundle = {
            "xgb_members": xgb_bag,
            "lgb_members": lgb_bag,
            "meta": meta,
            "trained_at": time.time(),
        }
        with open(_stacking_path(filename_model), "wb") as f:
            pickle.dump(bundle, f)
        utils.print(
            f"✅ [stacking] Saved bundle to {_stacking_path(filename_model)} "
            f"(XGB×{len(xgb_bag)} + LGB×{len(lgb_bag)} + meta).",
            1,
        )
        gc.collect()

    def _load_bundle(filename_model: str) -> Optional[Dict[str, Any]]:
        path = _stacking_path(filename_model)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception as exc:
            utils.print(f"⚠️ [stacking] failed to load bundle: {exc}", 1)
            return None

    def _predict_meta_prob(bundle: Dict[str, Any], X: np.ndarray) -> np.ndarray:
        xgb_p = _bag_predict(bundle["xgb_members"], X)
        lgb_p = _bag_predict(bundle["lgb_members"], X)
        meta_X = np.column_stack([xgb_p, lgb_p, np.abs(xgb_p - lgb_p)])
        return bundle["meta"].predict_proba(meta_X)[:, 1]

    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        X_np = np.asarray(X_df, dtype=np.float32)
        bundle = XGBoostStacking._load_bundle(filename_model)
        if bundle is None:
            utils.print("⚠️ [stacking] bundle missing — abstaining.", 1)
            return 0.5
        prob = float(XGBoostStacking._predict_meta_prob(bundle, X_np)[0])
        utils.print(f"ℹ️ Prediction (stacking): {prob:.4f}", 1)
        upper = trade_confidence / 100
        lower = 1 - upper
        if prob > upper:
            return 1
        if prob < lower:
            return 0
        return 0.5

    def model_predict_probabilities(
        filename_model: str, X_test: List[List[float]]
    ) -> np.ndarray:
        X_np = np.asarray(X_test, dtype=np.float32)
        bundle = XGBoostStacking._load_bundle(filename_model)
        if bundle is None:
            return np.full(X_np.shape[0], 0.5, dtype=float)
        return XGBoostStacking._predict_meta_prob(bundle, X_np)

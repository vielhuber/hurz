"""LightGBM baseline — same features + same calibrated ensemble as XGBoost.

Hypothesis: LightGBM's histogram-based leaf-wise growth finds more
decisive splits on noisy tabular data, so its calibrated probability
distribution is wider — meaning a larger fraction of samples clear the
[51, 55] confidence sweep. That would lift trade count above the 500
gate on the same assets where XGBoost currently only produces ~150
trades in the fulltest window, without necessarily hurting per-trade
success.

Design:
  - Same feature layout as XGBoost: [train_window normalized prices]
    + [8 indicators] + [4 cyclical time features]
  - Same 5-seed ensemble with temperature scaling
  - CPU only (lightgbm has GPU support, but it's finicky and the gains
    are marginal for 60k-sample fits)
  - Saves an .ensemble.pkl sidecar with the same format XGBoost uses so
    the bundle is pickleable across Python runs without a GPU context.
"""

import gc
import os
import pickle
import time
from collections import Counter
from typing import List, Union

import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.utils.singletons import utils, database, store
from app.utils.temperature_scaler import TemperatureScaler


ENSEMBLE_SEEDS = [42, 7, 123, 999, 1]


def _ensemble_path(filename_model: str) -> str:
    base, _ = os.path.splitext(filename_model)
    return base + ".ensemble.pkl"


def _load_ensemble(filename_model: str):
    ens_path = _ensemble_path(filename_model)
    if not os.path.exists(ens_path):
        return None
    with open(ens_path, "rb") as f:
        bundle = pickle.load(f)
    return bundle["members"]


def _ensemble_predict_probs(members, X_np: np.ndarray) -> np.ndarray:
    """Average calibrated BUY probabilities across all ensemble members."""
    n = len(X_np)
    accum = np.zeros(n, dtype=np.float64)
    for booster, calibrator in members:
        raw = booster.predict(X_np)
        accum += calibrator.transform(np.asarray(raw, dtype=np.float64))
    return accum / len(members)


class LGBMModel:

    name = "lgbm"

    @staticmethod
    def model_train_model(
        trade_asset: str,
        trade_platform: str,
        filename_model: str,
        train_window: int,
        train_horizon: int,
    ) -> None:
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

        # use the first 50% of data for training; second half is held out for fulltest
        df = df.iloc[: len(df) // 2].reset_index(drop=True)

        window_size = train_window
        forecast_horizon = train_horizon

        price_arr = df["Wert"].values
        indicator_arrs = [df[col].values for col in indicator_cols]
        ts_dt64 = pd.to_datetime(df["Zeitpunkt"]).values.astype("datetime64[s]")
        minute_arr = ts_dt64.astype("int64") // 60
        hour_float_arr = (
            (ts_dt64 - ts_dt64.astype("datetime64[D]"))
            .astype("timedelta64[m]")
            .astype(float)
            / 60.0
        )
        dow_arr = (
            ts_dt64.astype("datetime64[D]") - np.datetime64("1970-01-05", "D")
        ).astype(int) % 7

        num_candidates = len(df) - window_size - forecast_horizon
        if num_candidates <= 0:
            utils.print(
                f"⛔ {trade_asset}: not enough rows ({len(df)}) for window "
                f"{window_size} + horizon {forecast_horizon}.",
                0,
            )
            return False

        _candidate_last_idx = np.arange(num_candidates) + window_size - 1
        _candidate_future_idx = _candidate_last_idx + 1 + forecast_horizon
        _last_prices = price_arr[_candidate_last_idx]
        _future_prices = price_arr[_candidate_future_idx]
        with np.errstate(divide="ignore", invalid="ignore"):
            _deltas_all = np.where(
                (_last_prices != 0)
                & np.isfinite(_last_prices)
                & np.isfinite(_future_prices),
                (_future_prices - _last_prices) / _last_prices,
                np.nan,
            )
        _finite_abs = np.abs(_deltas_all[np.isfinite(_deltas_all)])
        sideways_pct = float(store.train_label_sideways_percentile)
        if len(_finite_abs) == 0 or sideways_pct <= 0:
            label_threshold = 0.0
        else:
            label_threshold = float(
                np.percentile(_finite_abs, min(max(sideways_pct, 0.0), 100.0))
            )
        _sigma = (
            float(np.std(_deltas_all[np.isfinite(_deltas_all)]))
            if len(_finite_abs)
            else 0.0
        )
        utils.print(
            f"ℹ️ {trade_asset}: adaptive sideways threshold "
            f"{label_threshold*100:.4f}% "
            f"(percentile {sideways_pct:.0f} of |delta|, σ={_sigma*100:.4f}%, "
            f"{num_candidates} candidate windows)",
            0,
        )

        X, y = [], []
        dropped_sideways = 0
        dropped_gap = 0
        TWO_PI = 2.0 * np.pi
        for i in range(len(df) - window_size - forecast_horizon):
            expected_minutes = window_size + forecast_horizon
            if (
                minute_arr[i + window_size + forecast_horizon] - minute_arr[i]
                != expected_minutes
            ):
                dropped_gap += 1
                continue

            raw = price_arr[i : i + window_size]
            if raw[0] == 0 or not np.isfinite(raw[0]):
                continue
            window = raw / raw[0] - 1
            if not np.all(np.isfinite(window)):
                continue

            last_idx = i + window_size - 1
            indicator_snapshot = np.array(
                [arr[last_idx] for arr in indicator_arrs], dtype=float
            )
            if not np.all(np.isfinite(indicator_snapshot)):
                continue

            future = price_arr[i + window_size + forecast_horizon]
            last = raw[-1]
            if last == 0:
                continue
            delta = (future - last) / last
            if abs(delta) < label_threshold:
                dropped_sideways += 1
                continue

            h = hour_float_arr[last_idx]
            d = dow_arr[last_idx]
            time_features = np.array(
                [
                    np.sin(TWO_PI * h / 24.0),
                    np.cos(TWO_PI * h / 24.0),
                    np.sin(TWO_PI * d / 5.0),
                    np.cos(TWO_PI * d / 5.0),
                ],
                dtype=float,
            )

            label = 1 if delta > 0 else 0
            feature_vec = np.concatenate([window, indicator_snapshot, time_features])
            X.append(feature_vec)
            y.append(label)

        utils.print(
            f"ℹ️ {trade_asset}: {len(X)} training samples kept, "
            f"{dropped_sideways} sideways dropped (threshold {label_threshold*100:.3f}%), "
            f"{dropped_gap} gap-crossing dropped",
            0,
        )

        if len(X) < 100:
            utils.print(
                f"⛔ {trade_asset}: only {len(X)} usable samples. Aborting.", 0
            )
            return False

        X = np.asarray(X, dtype=np.float32)
        y = np.asarray(y, dtype=np.int32)

        # chronological purged split (same logic as XGBoost)
        val_split = int(len(X) * 0.8)
        purge_gap = min(window_size + forecast_horizon, val_split // 2)
        train_end = val_split - purge_gap
        X_train = X[:train_end]
        y_train = y[:train_end]
        X_val = X[val_split:]
        y_val = y[val_split:]
        utils.print(
            f"ℹ️ Purged split: train={len(X_train)} val={len(X_val)} purge_gap={purge_gap}",
            1,
        )

        pos_ratio = float(y.mean())
        scale_pos_weight = (
            float((1.0 - pos_ratio) / pos_ratio) if pos_ratio > 0 else 1.0
        )
        utils.print(
            f"ℹ️ train pos_ratio = {pos_ratio:.4f}  scale_pos_weight = {scale_pos_weight:.4f}",
            1,
        )

        ensemble_members = []
        logloss_train_curves = []
        logloss_val_curves = []

        # Test whether underfitting is a capacity problem
        # (leaves/depth/learning rate) rather than a calibration problem.
        base_params = dict(
            objective="binary",
            metric="binary_logloss",
            num_leaves=63,
            max_depth=-1,
            learning_rate=0.05,
            feature_fraction=0.8,
            bagging_fraction=0.8,
            bagging_freq=5,
            reg_alpha=1.0,
            reg_lambda=1.0,
            scale_pos_weight=scale_pos_weight,
            verbosity=-1,
        )

        for member_idx, seed in enumerate(ENSEMBLE_SEEDS):
            params = dict(base_params)
            params["seed"] = seed
            params["bagging_seed"] = seed
            params["feature_fraction_seed"] = seed

            dtrain = lgb.Dataset(X_train, label=y_train)
            dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

            eval_result = {}
            t0 = time.perf_counter()
            booster = lgb.train(
                params,
                dtrain,
                num_boost_round=500,
                valid_sets=[dtrain, dval],
                valid_names=["train", "val"],
                callbacks=[
                    lgb.early_stopping(stopping_rounds=20, verbose=False),
                    lgb.record_evaluation(eval_result),
                ],
            )
            fit_s = time.perf_counter() - t0
            best_iter = booster.best_iteration
            best_val = (
                eval_result["val"]["binary_logloss"][best_iter - 1]
                if best_iter > 0
                else float("nan")
            )
            utils.print(
                f"ℹ️ Ensemble member {member_idx+1}/{len(ENSEMBLE_SEEDS)} "
                f"(seed={seed}): best_iter={best_iter} "
                f"val_logloss={best_val:.4f} ({fit_s:.1f}s)",
                1,
            )

            logloss_train_curves.append(eval_result["train"]["binary_logloss"])
            logloss_val_curves.append(eval_result["val"]["binary_logloss"])

            val_probs = booster.predict(X_val)
            calibrator = TemperatureScaler()
            calibrator.fit(val_probs, y_val)
            utils.print(
                f"ℹ️ Ensemble member {member_idx+1}: temperature T={calibrator.T:.4f}",
                1,
            )

            ensemble_members.append((booster, calibrator))

        # combined learning-curve plot (mean over members)
        max_len = max(len(c) for c in logloss_train_curves)

        def _pad(c):
            arr = np.full(max_len, np.nan)
            arr[: len(c)] = c
            return arr

        train_mean = np.nanmean(np.stack([_pad(c) for c in logloss_train_curves]), axis=0)
        val_mean = np.nanmean(np.stack([_pad(c) for c in logloss_val_curves]), axis=0)
        plt.figure(figsize=(8, 4))
        plt.plot(train_mean, label="Train Logloss (mean)")
        plt.plot(val_mean, label="Validation Logloss (mean)")
        plt.title("LightGBM learning curve — ensemble mean")
        plt.xlabel("Boosting-Round")
        plt.ylabel("Logloss")
        plt.grid(True)
        plt.legend()
        plt.tight_layout()
        plt.savefig("tmp/lernkurve.png", dpi=150, bbox_inches="tight")
        plt.close()

        try:
            random_probs = np.random.rand(len(y))
            logloss_random = log_loss(y, random_probs)
            utils.print(f"ℹ️ Logloss random baseline: {logloss_random:.4f}", 1)
            utils.print(f"ℹ️ Counter(y_true): {Counter(y.tolist())}", 1)
        except ValueError as e:
            utils.print(f"⛔ Logloss error: {e}", 1)

        # accuracy on training data (ensemble-averaged)
        ensemble_probs_train = np.zeros(len(X), dtype=np.float64)
        for booster, calibrator in ensemble_members:
            raw = booster.predict(X)
            ensemble_probs_train += calibrator.transform(np.asarray(raw, dtype=np.float64))
        ensemble_probs_train /= len(ensemble_members)
        ensemble_preds_train = (ensemble_probs_train >= 0.5).astype(int)
        accuracy = float((ensemble_preds_train == y).mean())
        utils.print(f"ℹ️ Ensemble accuracy on training data: {accuracy:.4f}", 1)

        # save a plain booster at the .txt path (matches the legacy "single-model" pattern)
        ensemble_members[0][0].save_model(filename_model, num_iteration=ensemble_members[0][0].best_iteration)

        ens_path = _ensemble_path(filename_model)
        with open(ens_path, "wb") as f:
            pickle.dump(
                {
                    "members": ensemble_members,
                    "seeds": ENSEMBLE_SEEDS,
                    "scale_pos_weight": scale_pos_weight,
                    "pos_ratio": pos_ratio,
                },
                f,
            )
        utils.print(
            f"✅ Ensemble ({len(ENSEMBLE_SEEDS)} LightGBM members + temperature calibrators) "
            f"saved to {ens_path}",
            1,
        )

        del ensemble_members
        gc.collect()

    @staticmethod
    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        X_np = np.asarray(X_df, dtype=np.float32)
        members = _load_ensemble(filename_model)
        if members is None:
            utils.print("⚠️ No LightGBM ensemble found — skipping trade.", 1)
            return 0.5
        probs = _ensemble_predict_probs(members, X_np)
        prediction = float(probs[0])
        utils.print(f"ℹ️ LGBM Prediction: {prediction:.4f} (ensemble of {len(members)})", 1)

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
        members = _load_ensemble(filename_model)
        if members is None:
            return [0.5] * len(X_np)
        probs = _ensemble_predict_probs(members, X_np)
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
        members = _load_ensemble(filename_model)
        if members is None:
            return np.full(len(X_np), 0.5, dtype=np.float64)
        return _ensemble_predict_probs(members, X_np)

import matplotlib.pyplot as plt
import numpy as np
import os
import pandas as pd
import pickle
import gc
import time
import xgboost as xgb
from collections import Counter
from sklearn.metrics import log_loss
from typing import List

from app.utils.singletons import utils, database, store
from app.utils.temperature_scaler import TemperatureScaler


try:
    import cupy as cp

    CUDA_AVAILABLE = cp.cuda.runtime.getDeviceCount() > 0
except Exception:
    cp = None
    CUDA_AVAILABLE = False

XGBOOST_DEVICE = "cuda" if CUDA_AVAILABLE else "cpu"


def _accelerator_array(values):
    if CUDA_AVAILABLE:
        return cp.asarray(values)
    return np.asarray(values)


def _numpy_array(values) -> np.ndarray:
    if hasattr(values, "get"):
        return values.get()
    return np.asarray(values)


def _free_accelerator_memory() -> None:
    if not CUDA_AVAILABLE:
        return
    try:
        cp.get_default_memory_pool().free_all_blocks()
    except Exception:
        pass


# ------------------------------------------------------------------
# Ensemble constants
# ------------------------------------------------------------------
# Seed-ensemble with per-seed temperature scaling.
# Experiment results (tmp/train_experiment.py) show:
#   - single-seed EV ranges from −0.9 to +33 → dominated by variance
#   - 5-seed ensemble + calibration → smooth, monotonic confidence curve
#     and a wide positive-EV zone (conf 73..93) with ~363 trades
#     at ~58% success rate on EURUSD 900s out-of-sample.
ENSEMBLE_SEEDS = [42, 7, 123, 999, 1]


def _ensemble_path(filename_model: str) -> str:
    """Companion .pkl file that stores ensemble members + calibrators."""
    base, _ = os.path.splitext(filename_model)
    return base + ".ensemble.pkl"


def _load_ensemble(filename_model: str):
    """Load an ensemble bundle saved by model_train_model.

    Returns list of (xgb_model, calibrator) tuples, or None if no
    ensemble file exists (caller should fall back to single-model mode).
    """
    ens_path = _ensemble_path(filename_model)
    if not os.path.exists(ens_path):
        return None
    with open(ens_path, "rb") as f:
        bundle = pickle.load(f)
    return bundle["members"]


def _ensemble_predict_probs(members, X_np: np.ndarray) -> np.ndarray:
    """Average calibrated BUY probabilities across all ensemble members.

    Each member is a (XGBClassifier, TemperatureScaler) pair. We run
    inference on the available accelerator, then apply the
    temperature-scaling calibration on the CPU and average.

    After each call we free the CuPy memory pool. Without this, running
    the fulltest sequentially over 76 assets × 5-member ensemble = 380
    booster-predict calls causes CuPy's pool to grow until CUDA forces
    a brutal cleanup → multi-second GPU stalls that block the asyncio
    event loop enough to miss ping/pong deadlines and drop the
    PocketOption websocket (observed ~16 s stall → 1005 disconnect).
    """
    n = len(X_np)
    accum = np.zeros(n, dtype=np.float64)
    X_accelerated = _accelerator_array(X_np)
    try:
        for member, calibrator in members:
            member.get_booster().set_param({"device": XGBOOST_DEVICE})
            raw = member.get_booster().inplace_predict(X_accelerated)
            raw = _numpy_array(raw)
            accum += calibrator.transform(raw)
    finally:
        del X_accelerated
        _free_accelerator_memory()
    return accum / len(members)


class XGBoostModel:

    name = "XGBoost"

    def model_train_model(
        trade_asset: str, trade_platform: str, filename_model: str, train_window: int, train_horizon: int
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
        df = df.rename(columns={'trade_asset': 'Waehrung', 'trade_platform': 'Plattform', 'timestamp': 'Zeitpunkt', 'price': 'Wert'})
        df.dropna(subset=["Wert"], inplace=True)
        df['Wert'] = df['Wert'].astype(float)

        # indicator columns: to float, so numpy ops work
        for col in indicator_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

        # convert time column to datetime
        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")

        # ✂️ only use the first 50% of the data for training,
        # the second half is reserved for out-of-sample testing (fulltest)
        df = df.iloc[: len(df) // 2].reset_index(drop=True)

        # 🔁 sliding window configuration
        window_size = train_window  # e.g., 300 data points -> input length
        forecast_horizon = (
            train_horizon  # e.g., 60 data points -> forecast time in the future
        )

        X, y = [], []  # empty lists for features and labels

        # Cache indicator columns as numpy arrays for speed
        price_arr = df["Wert"].values
        indicator_arrs = [df[col].values for col in indicator_cols]
        # Cumulative minute positions for gap detection (weekend gaps on non-OTC).
        # A contiguous window of N rows must span exactly (N-1) minutes end-to-end.
        ts_dt64 = pd.to_datetime(df["Zeitpunkt"]).values.astype("datetime64[s]")
        minute_arr = ts_dt64.astype("int64") // 60
        # UTC hour (float, 0.0-23.98) and day-of-week (0=Mon..4=Fri) for time features
        hour_float_arr = (
            (ts_dt64 - ts_dt64.astype("datetime64[D]")).astype("timedelta64[m]").astype(float) / 60.0
        )
        dow_arr = (ts_dt64.astype("datetime64[D]") - np.datetime64("1970-01-05", "D")).astype(int) % 7

        # 📦 generate training data using a sliding window over the time series
        # feature vector per sample = [240 normalized prices] + [8 indicator values at last minute]
        # price window is normalized: (window / window[0] - 1)
        # indicators are used as-is (each already comparable across assets / time).
        # samples with |relative move| < label_threshold are dropped to
        # filter out sideways/noise movements and sharpen the signal.
        # samples whose window/target span crosses a weekend gap are also skipped.
        #
        # --- Adaptive sideways threshold ---
        # We size the threshold to this asset's actual volatility by taking a
        # percentile of |delta| over the forecast horizon. That way the filter
        # drops roughly `train_label_sideways_percentile` percent of samples as
        # "no clear direction" regardless of whether the asset is EUR/USD
        # (tiny 5-min moves) or AUDCHF_otc (synthetic high-vol).
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
        _sigma = float(np.std(_deltas_all[np.isfinite(_deltas_all)])) if len(_finite_abs) else 0.0
        utils.print(
            f"ℹ️ {trade_asset}: adaptive sideways threshold "
            f"{label_threshold*100:.4f}% "
            f"(percentile {sideways_pct:.0f} of |delta|, σ={_sigma*100:.4f}%, "
            f"{num_candidates} candidate windows)",
            0,
        )
        dropped_sideways = 0
        dropped_gap = 0
        TWO_PI = 2.0 * np.pi
        for i in range(len(df) - window_size - forecast_horizon):
            # skip if window + horizon crosses a gap (e.g., weekend)
            expected_minutes = window_size + forecast_horizon
            if minute_arr[i + window_size + forecast_horizon] - minute_arr[i] != expected_minutes:
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

            # cyclical time features at the prediction moment
            h = hour_float_arr[last_idx]
            d = dow_arr[last_idx]
            time_features = np.array([
                np.sin(TWO_PI * h / 24.0),
                np.cos(TWO_PI * h / 24.0),
                np.sin(TWO_PI * d / 5.0),
                np.cos(TWO_PI * d / 5.0),
            ], dtype=float)

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

        # abort if no usable samples remain after filtering
        min_samples_required = 100
        if len(X) < min_samples_required:
            utils.print(
                f"⛔ {trade_asset}: only {len(X)} usable training samples "
                f"(need at least {min_samples_required}). Aborting training.",
                0,
            )
            return False

        # 🧾 convert to dataframe and series
        X = pd.DataFrame(X)
        y = pd.Series(y)

        # ✂️ chronological train/validation split for early stopping.
        # Last 20% is held out. A PURGE GAP is inserted between train and
        # val to prevent label/feature leakage: two sliding windows that
        # start `i` minutes apart share `window_size - i` minutes of price
        # data, and a sample's label depends on price `forecast_horizon`
        # minutes after its window ends. So train samples near the val
        # boundary can contain features AND labels that are computed from
        # price points the val samples also use. Without the purge the
        # val-logloss is optimistic and early stopping triggers too late.
        # We drop `window_size + forecast_horizon` train samples at the
        # right edge, which is the minimum gap that breaks both kinds of
        # overlap.
        val_split = int(len(X) * 0.8)
        purge_gap = min(window_size + forecast_horizon, val_split // 2)
        train_end = val_split - purge_gap
        X_train_df = X.iloc[:train_end]
        y_train_ser = y.iloc[:train_end]
        X_val_df = X.iloc[val_split:]
        y_val_ser = y.iloc[val_split:]
        utils.print(
            f"ℹ️ Purged split: train={len(X_train_df)} val={len(X_val_df)} "
            f"purge_gap={purge_gap}",
            1,
        )

        # ⚙️ use CUDA when available and NumPy on CPU-only systems
        X_train_accelerated = _accelerator_array(X_train_df.values)
        y_train_accelerated = _accelerator_array(y_train_ser.values)
        X_val_accelerated = _accelerator_array(X_val_df.values)
        y_val_accelerated = _accelerator_array(y_val_ser.values)
        # full set (for final score reporting)
        X_accelerated = _accelerator_array(X.values)
        y_accelerated = _accelerator_array(y.values)
        # cpu copies used for post-training inference (ensemble uses CPU-side
        # predictions so temperature scaling can chain cleanly)
        X_cpu = X.values
        y_cpu = y.values

        # 🧠 Seed-ensemble of XGBoost classifiers with per-seed temperature
        # calibration. Motivation:
        #   - Single-seed EV is dominated by subsample/colsample randomness
        #     (seed 42 → +9 EV, seed 7 → +34 EV, seed 999 → +27 EV etc.)
        #   - Averaging the calibrated probabilities across 5 seeds yields
        #     a smooth, monotonic confidence curve and a wide positive-EV
        #     zone (on EURUSD 900s: conf 73..93 all positive, best ≈ +27 EV
        #     at conf 88 vs. baseline mean ≈ +13 EV with a single seed).
        #   - scale_pos_weight corrects for the mild UP/DOWN class imbalance
        #     in the training slice (usually ~0.97-1.03 for FX majors).
        pos_ratio = float(y_cpu.mean())
        scale_pos_weight = float((1.0 - pos_ratio) / pos_ratio) if pos_ratio > 0 else 1.0
        utils.print(
            f"ℹ️ train pos_ratio = {pos_ratio:.4f}  "
            f"scale_pos_weight = {scale_pos_weight:.4f}",
            1,
        )

        ensemble_members = []  # list of (xgb_model, TemperatureScaler)

        # shared base hyper-params (from previous single-model baseline, known
        # to give a reasonable fit without overfitting on 60-100k samples)
        base_params = dict(
            objective="binary:logistic",
            base_score=0.5,
            eval_metric="logloss",
            n_estimators=500,
            max_depth=3,
            learning_rate=0.02,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=1.0,
            reg_lambda=1.0,
            early_stopping_rounds=20,
            tree_method="hist",
            device=XGBOOST_DEVICE,
            verbosity=0,
            scale_pos_weight=scale_pos_weight,
        )

        logloss_train_curves = []
        logloss_val_curves = []
        for member_idx, seed in enumerate(ENSEMBLE_SEEDS):
            params = dict(base_params)
            params["random_state"] = seed
            member = xgb.XGBClassifier(**params)

            t0 = time.perf_counter()
            member.fit(
                X_train_accelerated,
                y_train_accelerated,
                eval_set=[
                    (X_train_accelerated, y_train_accelerated),
                    (X_val_accelerated, y_val_accelerated),
                ],
                verbose=False,
            )
            fit_s = time.perf_counter() - t0
            utils.print(
                f"ℹ️ Ensemble member {member_idx+1}/{len(ENSEMBLE_SEEDS)} "
                f"(seed={seed}): best_iter={member.best_iteration} "
                f"val_logloss={member.best_score:.4f} ({fit_s:.1f}s)",
                1,
            )

            # per-member learning curves (for the combined plot below)
            ergebnisse = member.evals_result()
            logloss_train_curves.append(ergebnisse["validation_0"]["logloss"])
            logloss_val_curves.append(ergebnisse["validation_1"]["logloss"])

            # fit temperature-scaling calibration on val-set probabilities
            member.set_params(device="cpu")
            val_probs = member.predict_proba(X_val_df)[:, 1]
            calibrator = TemperatureScaler()
            calibrator.fit(val_probs, y_val_ser.values)
            member.set_params(device=XGBOOST_DEVICE)
            utils.print(
                f"ℹ️ Ensemble member {member_idx+1}: temperature T={calibrator.T:.4f}",
                1,
            )

            ensemble_members.append((member, calibrator))

        # 📊 combined learning curve (mean over ensemble members)
        max_len = max(len(c) for c in logloss_train_curves)

        def _pad(c):
            arr = np.full(max_len, np.nan)
            arr[: len(c)] = c
            return arr

        train_mean = np.nanmean(
            np.stack([_pad(c) for c in logloss_train_curves]), axis=0
        )
        val_mean = np.nanmean(
            np.stack([_pad(c) for c in logloss_val_curves]), axis=0
        )
        plt.figure(figsize=(8, 4))
        plt.plot(train_mean, label="Train Logloss (mean)")
        plt.plot(val_mean, label="Validation Logloss (mean)")
        plt.title("Learning curve — ensemble mean")
        plt.xlabel("Boosting-Round")
        plt.ylabel("Logloss")
        plt.grid(True)
        plt.legend()
        plt.tight_layout()
        plt.savefig("tmp/lernkurve.png", dpi=150, bbox_inches="tight")
        plt.close()

        # compare with random
        try:
            y_true = _numpy_array(y_accelerated)
            assert set(np.unique(y_true)) <= {0, 1}, "Labels contain values other than 0/1"
            random_probs = np.random.rand(len(y_true))
            logloss_random = log_loss(y_true, random_probs)
            utils.print(f"ℹ️ Logloss random baseline: {logloss_random:.4f}", 1)
            utils.print(f"ℹ️ Counter(y_true): {Counter(y_true)}", 1)
        except ValueError as e:
            utils.print(f"⛔ An error appeared on calculation of logloss: {e}", 1)

        # 🔄 accuracy on training data (ensemble-averaged). X_cpu/y_cpu were
        # already pulled earlier before training; reuse them here.
        ensemble_probs_train = np.zeros(len(X_cpu), dtype=np.float64)
        for member, calibrator in ensemble_members:
            member.set_params(device="cpu")
            raw = member.predict_proba(X_cpu)[:, 1]
            ensemble_probs_train += calibrator.transform(raw)
        ensemble_probs_train /= len(ensemble_members)
        ensemble_preds_train = (ensemble_probs_train >= 0.5).astype(int)
        accuracy = float((ensemble_preds_train == y_cpu).mean())
        utils.print(f"ℹ️ Ensemble accuracy on training data: {accuracy:.4f}", 1)

        # 💾 save ensemble (pickle: list of (xgb model, temperature scaler))
        # Also save the first member as a plain .json so downstream tools
        # that still assume a single-model path keep working for legacy
        # (non-ensemble) callers.
        ensemble_members[0][0].save_model(filename_model)
        ens_path = _ensemble_path(filename_model)
        # Put XGB members on CPU before pickling so loading doesn't require
        # an active GPU context.
        serializable = []
        for member, calibrator in ensemble_members:
            member.set_params(device="cpu")
            serializable.append((member, calibrator))
        with open(ens_path, "wb") as f:
            pickle.dump({
                "members": serializable,
                "seeds": ENSEMBLE_SEEDS,
                "scale_pos_weight": scale_pos_weight,
                "pos_ratio": pos_ratio,
            }, f)
        utils.print(
            f"✅ Ensemble ({len(ENSEMBLE_SEEDS)} members + temperature scalers) "
            f"saved to {ens_path}",
            1,
        )

        # clear GPU memory
        for member, _ in ensemble_members:
            del member
        del ensemble_members
        del X_accelerated, y_accelerated
        del X_train_accelerated, y_train_accelerated
        del X_val_accelerated, y_val_accelerated
        gc.collect()
        _free_accelerator_memory()

    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        X_np = np.asarray(X_df, dtype=np.float32)
        members = _load_ensemble(filename_model)
        if members is not None:
            probs = _ensemble_predict_probs(members, X_np)
            prediction = float(probs[0])
            utils.print(
                f"ℹ️ Prediction: {prediction:.4f} (ensemble of {len(members)})", 1
            )
            for member, _ in members:
                del member
            del members
        else:
            # legacy single-model fallback (no ensemble file on disk)
            X_accelerated = _accelerator_array(X_np)
            model = xgb.XGBClassifier(tree_method="hist", device=XGBOOST_DEVICE)
            model.load_model(filename_model)
            model.get_booster().set_param({"device": XGBOOST_DEVICE})
            predictions = model.get_booster().inplace_predict(X_accelerated)
            prediction = float(predictions[0])
            utils.print(f"ℹ️ Prediction: {prediction:.4f} (single-model)", 1)
            del model, X_accelerated

        gc.collect()
        _free_accelerator_memory()

        # apply confidence threshold — the ensemble output is calibrated, so
        # a higher confidence (e.g. 66–90) means the probability must exceed
        # that fraction or fall below (1 − fraction) to trigger a trade.
        upper = trade_confidence / 100
        lower = 1 - upper
        if prediction > upper:
            return 1
        if prediction < lower:
            return 0
        return 0.5

    def model_predict_probabilities(
        filename_model: str, X_test: List[List[float]]
    ) -> np.ndarray:
        """Return calibrated BUY probabilities [0..1] for each sample.

        Uses the ensemble bundle if present (averaged per-seed temperature
        calibration), falls back to single-model raw predictions otherwise.
        This is the function called by the confidence-sweep in
        determine_confidence_based_on_fulltests, so it MUST return
        calibrated probabilities so the sweep finds a valid optimum.
        """
        X_np = np.asarray(X_test, dtype=np.float32)
        members = _load_ensemble(filename_model)
        if members is not None:
            probs_np = _ensemble_predict_probs(members, X_np)
            for member, _ in members:
                del member
            del members
        else:
            model = xgb.XGBClassifier(tree_method="hist", device=XGBOOST_DEVICE)
            model.load_model(filename_model)
            model.get_booster().set_param({"device": XGBOOST_DEVICE})
            X_accelerated = _accelerator_array(X_np)
            probs_np = _numpy_array(
                model.get_booster().inplace_predict(X_accelerated)
            )
            del model, X_accelerated

        gc.collect()
        _free_accelerator_memory()
        return probs_np

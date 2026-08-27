"""E9 — Small LSTM on the 240-minute price sequence.

Hypothesis: tree-based learners (XGBoost, LightGBM) cannot exploit the
*ordering* of the 240 normalized prices — they see each bar as an
independent feature. A sequence-aware LSTM can, in principle, extract
temporal structure (autocorrelation, momentum exhaustion, local
reversal patterns) that trees miss. If the val-logloss still floors at
~0.693 like the tree-based models, that's strong evidence there's no
predictable signal at the 1-minute horizon on this feature layout.

Architecture (intentionally tiny to avoid overfit on ~60 k samples):
  - LSTM(input=1, hidden=16, layers=1)
  - Concat last hidden state (16) with aux features (8 indicators +
    4 time features = 12) → 28-dim vector
  - FC(28 → 16) → ReLU → Dropout(0.3) → FC(16 → 1) → sigmoid

Training: Adam lr=1e-3, batch 256, BCE loss, early stop on val_logloss
patience=3, max 30 epochs, CPU only.
"""

from __future__ import annotations

import gc
import json
import os
import time
from typing import List, Union

import numpy as np
import pandas as pd

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    _TORCH_OK = True
except ImportError:  # pragma: no cover
    _TORCH_OK = False

from app.utils.singletons import utils, database, store


_HIDDEN = 16
_AUX_DIM = 12      # 8 indicators + 4 time features
_SEQ_LEN_DEFAULT = 240
_DROPOUT = 0.3
_BATCH_SIZE = 256
_MAX_EPOCHS = 30
_PATIENCE = 3
_LEARNING_RATE = 1e-3


def _weights_path(filename_model: str) -> str:
    base, _ = os.path.splitext(filename_model)
    return base + ".lstm.pt"


class _LSTMClassifier(nn.Module):
    def __init__(self, hidden: int = _HIDDEN, aux_dim: int = _AUX_DIM,
                 dropout: float = _DROPOUT) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=1, hidden_size=hidden, num_layers=1, batch_first=True,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc1 = nn.Linear(hidden + aux_dim, hidden)
        self.fc2 = nn.Linear(hidden, 1)
        self.relu = nn.ReLU()

    def forward(self, seq: torch.Tensor, aux: torch.Tensor) -> torch.Tensor:
        # seq: (B, T, 1); aux: (B, aux_dim)
        _, (h_n, _) = self.lstm(seq)
        h = h_n.squeeze(0)                        # (B, hidden)
        x = torch.cat([h, aux], dim=1)            # (B, hidden+aux)
        x = self.relu(self.fc1(x))
        x = self.dropout(x)
        return torch.sigmoid(self.fc2(x)).squeeze(-1)   # (B,)


def _split_features(X: np.ndarray, seq_len: int):
    """Split (N, seq_len + aux) → (seq, aux) for the model."""
    seq = X[:, :seq_len].astype(np.float32)
    aux = X[:, seq_len:].astype(np.float32)
    return seq[..., None], aux                   # seq: (N, T, 1)


def _build_training_set(trade_asset: str, trade_platform: str,
                        train_window: int, train_horizon: int):
    """Replicate XGBoost's data-loading pipeline with the same feature
    layout. Returns (X, y, train_end, val_start) for the purged split.
    """
    indicator_cols = store.indicator_columns
    indicator_cols_sql = ", ".join(indicator_cols)
    df = database.select(
        f"SELECT trade_asset, trade_platform, timestamp, price, "
        f"{indicator_cols_sql} FROM trading_data "
        f"WHERE trade_asset = %s AND trade_platform = %s ORDER BY timestamp",
        (trade_asset, trade_platform),
    )
    df = pd.DataFrame(df).rename(columns={
        "trade_asset": "Waehrung", "trade_platform": "Plattform",
        "timestamp": "Zeitpunkt", "price": "Wert",
    })
    df.dropna(subset=["Wert"], inplace=True)
    df["Wert"] = df["Wert"].astype(float)
    for col in indicator_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")

    # training half only — second half reserved for fulltest
    df = df.iloc[: len(df) // 2].reset_index(drop=True)

    price_arr = df["Wert"].values
    indicator_arrs = [df[col].values for col in indicator_cols]
    ts_dt64 = pd.to_datetime(df["Zeitpunkt"]).values.astype("datetime64[s]")
    minute_arr = ts_dt64.astype("int64") // 60
    hour_float_arr = (
        (ts_dt64 - ts_dt64.astype("datetime64[D]"))
        .astype("timedelta64[m]").astype(float) / 60.0
    )
    dow_arr = (
        (ts_dt64.astype("datetime64[D]") - np.datetime64("1970-01-05", "D"))
        .astype(int) % 7
    )

    num_candidates = len(df) - train_window - train_horizon
    if num_candidates <= 0:
        return None

    # adaptive sideways threshold matching XGBoost
    last_idx = np.arange(num_candidates) + train_window - 1
    future_idx = last_idx + 1 + train_horizon
    last_prices = price_arr[last_idx]
    future_prices = price_arr[future_idx]
    with np.errstate(divide="ignore", invalid="ignore"):
        deltas_all = np.where(
            (last_prices != 0) & np.isfinite(last_prices) & np.isfinite(future_prices),
            (future_prices - last_prices) / last_prices, np.nan,
        )
    finite_abs = np.abs(deltas_all[np.isfinite(deltas_all)])
    sideways_pct = float(store.train_label_sideways_percentile)
    if len(finite_abs) == 0 or sideways_pct <= 0:
        label_threshold = 0.0
    else:
        label_threshold = float(
            np.percentile(finite_abs, min(max(sideways_pct, 0.0), 100.0))
        )

    X_list, y_list = [], []
    TWO_PI = 2.0 * np.pi
    for i in range(num_candidates):
        expected_minutes = train_window + train_horizon
        if minute_arr[i + train_window + train_horizon] - minute_arr[i] != expected_minutes:
            continue
        raw = price_arr[i : i + train_window]
        if raw[0] == 0 or not np.isfinite(raw[0]):
            continue
        window = raw / raw[0] - 1
        if not np.all(np.isfinite(window)):
            continue
        li = i + train_window - 1
        indicator_snapshot = np.array(
            [arr[li] for arr in indicator_arrs], dtype=float
        )
        if not np.all(np.isfinite(indicator_snapshot)):
            continue
        future = price_arr[i + train_window + train_horizon]
        last = raw[-1]
        if last == 0:
            continue
        delta = (future - last) / last
        if abs(delta) < label_threshold:
            continue
        h = hour_float_arr[li]
        d = dow_arr[li]
        time_feats = np.array([
            np.sin(TWO_PI * h / 24.0), np.cos(TWO_PI * h / 24.0),
            np.sin(TWO_PI * d / 5.0),  np.cos(TWO_PI * d / 5.0),
        ], dtype=float)
        X_list.append(np.concatenate([window, indicator_snapshot, time_feats]))
        y_list.append(1 if delta > 0 else 0)

    if len(X_list) < 100:
        return None

    X = np.asarray(X_list, dtype=np.float32)
    y = np.asarray(y_list, dtype=np.float32)

    val_split = int(len(X) * 0.8)
    purge_gap = min(train_window + train_horizon, val_split // 2)
    return X, y, val_split - purge_gap, val_split


def _train_loop(model, loader_tr, loader_val, seq_len: int):
    device = torch.device("cpu")
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=_LEARNING_RATE)
    loss_fn = nn.BCELoss()

    best_val = float("inf")
    best_state = None
    stale = 0
    history = {"train": [], "val": []}

    for epoch in range(_MAX_EPOCHS):
        model.train()
        tr_losses = []
        for seq_batch, aux_batch, y_batch in loader_tr:
            optimizer.zero_grad()
            probs = model(seq_batch, aux_batch)
            loss = loss_fn(probs, y_batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            tr_losses.append(float(loss.item()))

        model.eval()
        val_losses = []
        with torch.no_grad():
            for seq_batch, aux_batch, y_batch in loader_val:
                probs = model(seq_batch, aux_batch)
                val_losses.append(float(loss_fn(probs, y_batch).item()))
        tr_mean = float(np.mean(tr_losses)) if tr_losses else float("nan")
        val_mean = float(np.mean(val_losses)) if val_losses else float("nan")
        history["train"].append(tr_mean)
        history["val"].append(val_mean)
        utils.print(
            f"ℹ️ LSTM epoch {epoch+1:02d}/{_MAX_EPOCHS}: "
            f"train_loss={tr_mean:.4f} val_loss={val_mean:.4f}",
            1,
        )
        if val_mean < best_val - 1e-4:
            best_val = val_mean
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= _PATIENCE:
                utils.print(f"ℹ️ early stop at epoch {epoch+1}", 1)
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history, best_val


class LSTMSeqModel:
    """E9 — 1-layer LSTM(hidden=16) + 2-layer FC head."""

    name = "lstm_seq"

    @staticmethod
    def model_train_model(trade_asset, trade_platform, filename_model,
                          train_window, train_horizon) -> None:
        if not _TORCH_OK:
            utils.print("⛔ lstm_seq requires torch; install torch first.", 0)
            return False

        t0 = time.perf_counter()
        built = _build_training_set(
            trade_asset, trade_platform, train_window, train_horizon
        )
        if built is None:
            utils.print(f"⛔ {trade_asset}: insufficient training data", 0)
            return False
        X, y, train_end, val_start = built
        seq_len = int(train_window)
        utils.print(
            f"ℹ️ lstm_seq {trade_asset}: total={len(X)} "
            f"train={train_end} val={len(X) - val_start} seq_len={seq_len}",
            0,
        )

        seq_all, aux_all = _split_features(X, seq_len)
        seq_tr = torch.from_numpy(seq_all[:train_end])
        aux_tr = torch.from_numpy(aux_all[:train_end])
        y_tr = torch.from_numpy(y[:train_end])
        seq_val = torch.from_numpy(seq_all[val_start:])
        aux_val = torch.from_numpy(aux_all[val_start:])
        y_val = torch.from_numpy(y[val_start:])

        loader_tr = DataLoader(
            TensorDataset(seq_tr, aux_tr, y_tr),
            batch_size=_BATCH_SIZE, shuffle=True, drop_last=False,
        )
        loader_val = DataLoader(
            TensorDataset(seq_val, aux_val, y_val),
            batch_size=_BATCH_SIZE, shuffle=False, drop_last=False,
        )

        torch.manual_seed(42)
        model = _LSTMClassifier()
        model, history, best_val = _train_loop(model, loader_tr, loader_val, seq_len)

        weights_path = _weights_path(filename_model)
        torch.save({
            "state_dict": model.state_dict(),
            "seq_len": seq_len,
            "hidden": _HIDDEN,
            "aux_dim": _AUX_DIM,
            "best_val_logloss": best_val,
            "history": history,
        }, weights_path)

        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump({
                "type": "lstm_seq",
                "seq_len": seq_len,
                "hidden": _HIDDEN,
                "aux_dim": _AUX_DIM,
                "best_val_logloss": best_val,
                "weights": os.path.basename(weights_path),
            }, f, indent=2)

        utils.print(
            f"✅ lstm_seq trained in {time.perf_counter()-t0:.1f}s "
            f"best_val_logloss={best_val:.4f}",
            0,
        )
        del model, seq_tr, aux_tr, y_tr, seq_val, aux_val, y_val
        gc.collect()

    @staticmethod
    def _load_model(filename_model: str):
        weights_path = _weights_path(filename_model)
        if not os.path.exists(weights_path):
            return None, None
        bundle = torch.load(weights_path, map_location="cpu", weights_only=False)
        model = _LSTMClassifier(
            hidden=int(bundle.get("hidden", _HIDDEN)),
            aux_dim=int(bundle.get("aux_dim", _AUX_DIM)),
        )
        model.load_state_dict(bundle["state_dict"])
        model.eval()
        return model, int(bundle.get("seq_len", _SEQ_LEN_DEFAULT))

    @staticmethod
    def _predict(filename_model: str, X_np: np.ndarray) -> np.ndarray:
        model, seq_len = LSTMSeqModel._load_model(filename_model)
        if model is None or seq_len is None:
            return np.full(len(X_np), 0.5, dtype=np.float64)
        seq, aux = _split_features(X_np, seq_len)
        seq_t = torch.from_numpy(seq)
        aux_t = torch.from_numpy(aux)
        outs = []
        with torch.no_grad():
            for start in range(0, len(seq_t), _BATCH_SIZE):
                end = start + _BATCH_SIZE
                probs = model(seq_t[start:end], aux_t[start:end])
                outs.append(probs.numpy())
        return np.concatenate(outs).astype(np.float64)

    @staticmethod
    def model_buy_sell_order(X_df: pd.DataFrame, filename_model: str,
                             trade_confidence: int) -> float:
        X_np = np.asarray(X_df, dtype=np.float32)
        probs = LSTMSeqModel._predict(filename_model, X_np)
        prob = float(probs[0])
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
        X_np = np.asarray(X_test, dtype=np.float32)
        probs = LSTMSeqModel._predict(filename_model, X_np)
        upper = trade_confidence / 100.0
        lower = 1.0 - upper
        out = []
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
        filename_model: str, X_test: Union[List[List[float]], np.ndarray]
    ) -> np.ndarray:
        X_np = np.asarray(X_test, dtype=np.float32)
        return LSTMSeqModel._predict(filename_model, X_np)

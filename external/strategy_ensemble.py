"""StrategyEnsemble — meta-model that blends orthogonal sub-strategies into
a single probability via weighted averaging.

Hypothesis: a single XGBoost model on RSI/MACD/BB features captures one
hypothesis about the market. When the regime shifts, the model's edge
disappears. Running three orthogonal strategies in parallel and blending
their probabilities should:
  - lower the variance of the signal (rule-based subs add stability)
  - diversify the edge (xgboost works in ML-friendly regimes; bollinger
    works in mean-reverting regimes; momentum works in trending regimes)
  - make the system robust against any single sub-strategy collapsing

Sub-strategies (configurable via the ensemble's JSON config):
  - XGBoost: machine-learning baseline (continuous prob in [0,1])
  - bollinger: BB-band breakout / reversion rule (prob ∈ {0.01, 0.5, 0.99})
  - momentum: N-bar streak rule (prob ∈ {0.01, 0.5, 0.99})

The meta-model implements the same interface as any other model under
external/, so it slots into the existing model_classes registry. Sub-
model filenames are derived from the ensemble's filename:
    models/model_<plat>_strategyensemble_<asset>_<time>s.json
becomes
    models/model_<plat>_strategyensemble_<asset>_<time>s.xgboost.json
    models/model_<plat>_strategyensemble_<asset>_<time>s.bollinger.json
    ...
so each sub-model has its own bundle and the ensemble doesn't trample
on any single-strategy bundle that may already exist.

Aggregation (changed 2026-05-07 — the original ≥k-of-N voting rule was
too restrictive: rule-based subs only fire on rare extreme readings,
making consensus practically impossible on 1-min bars and producing
flat 0.5 outputs that the fulltest sweep rejected as "too uncertain".)

Now: weighted mean of sub-probabilities, with per-sub weights configurable
via the bundle's JSON (defaults below). The XGBoost sub gets the highest
weight because its output is continuous and informative on every bar;
rule-based subs get smaller weights so their {0.01, 0.5, 0.99} spikes
nudge the ensemble probability without dominating it. The aggregated
prob is then thresholded against the order path's trade_confidence.
"""
import json
import os
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from app.utils.singletons import store, utils


# Default sub-strategies. Each entry must match a `name` registered by
# another file in external/ (auto-discovered by settings.load_externals).
_DEFAULT_SUB_STRATEGIES: List[str] = ["XGBoost", "bollinger", "momentum"]

# Default weights for weighted averaging. XGBoost dominates because it's
# continuous and informative; rule-based subs nudge the result when they
# fire. Weights are normalised at aggregation time, so absolute scale
# doesn't matter.
_DEFAULT_WEIGHTS: Dict[str, float] = {
    "XGBoost":   2.0,
    "bollinger": 1.0,
    "momentum":  1.0,
}


def _sub_filename(meta_filename: str, sub_name: str) -> str:
    """Derive a sub-model's filename from the ensemble's. The ensemble
    file ends in `.json`; we replace the suffix with `.<sub>.json` so
    each sub-model gets its own bundle slot."""
    base, _ = os.path.splitext(meta_filename)
    return f"{base}.{sub_name.lower()}.json"


def _load_meta_config(filename: str) -> Dict[str, Any]:
    """Return the saved ensemble config or a default. Robust to the
    file being missing (first call before training)."""
    if not os.path.exists(filename):
        return {
            "subs": list(_DEFAULT_SUB_STRATEGIES),
            "weights": dict(_DEFAULT_WEIGHTS),
        }
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        subs = data.get("subs") or list(_DEFAULT_SUB_STRATEGIES)
        weights = data.get("weights") or dict(_DEFAULT_WEIGHTS)
        return {"subs": subs, "weights": weights}
    except Exception:
        return {
            "subs": list(_DEFAULT_SUB_STRATEGIES),
            "weights": dict(_DEFAULT_WEIGHTS),
        }


def _resolve_sub(name: str):
    """Look up a sub-strategy in the registered model_classes. Returns
    None if not found — caller should treat as missing voter."""
    return store.model_classes.get(name)


def _aggregate(probs: List[float], names: List[str], weights: Dict[str, float]) -> float:
    """Weighted mean of sub-probabilities. Sub-strategies with no
    configured weight fall back to weight 1.0. Returns 0.5 (abstain)
    if no sub contributed a usable probability."""
    if not probs:
        return 0.5
    total = 0.0
    weight_sum = 0.0
    for p, name in zip(probs, names):
        if not np.isfinite(p):
            continue
        w = float(weights.get(name, 1.0))
        if w <= 0:
            continue
        total += p * w
        weight_sum += w
    if weight_sum <= 0:
        return 0.5
    return total / weight_sum


def _collect_sub_outputs(
    cfg: Dict[str, Any],
    filename_model: str,
    fn_kind: str,
    *args,
):
    """Iterate over configured subs, call their `model_buy_sell_order`
    or `model_predict_probabilities`, and collect (name, output) pairs.
    Subs whose bundle is missing or which raise are skipped."""
    out: List = []
    for sub_name in cfg["subs"]:
        sub_cls = _resolve_sub(sub_name)
        if sub_cls is None:
            continue
        sub_path = _sub_filename(filename_model, sub_name)
        # XGBoost saves both `<base>` and `<base>.ensemble.pkl`. Other
        # subs save just the JSON. Either presence is enough to call.
        pkl_path = sub_path.replace(".json", ".ensemble.pkl")
        if not os.path.exists(sub_path) and not os.path.exists(pkl_path):
            continue
        try:
            if fn_kind == "buy_sell":
                out.append((sub_name, sub_cls.model_buy_sell_order(*args, sub_path, *args[1:] if False else args[-1:])))
            elif fn_kind == "predict_probs":
                out.append((sub_name, sub_cls.model_predict_probabilities(sub_path, *args)))
            else:
                raise ValueError(f"unknown fn_kind: {fn_kind}")
        except Exception as exc:
            utils.print(
                f"⚠️ [StrategyEnsemble] sub '{sub_name}' {fn_kind} failed: {exc}", 1,
            )
    return out


class StrategyEnsemble:
    """Meta-model that blends multiple sub-strategies via weighted mean."""

    name = "StrategyEnsemble"

    @staticmethod
    def model_train_model(
        trade_asset: str, trade_platform: str, filename_model: str,
        train_window: int, train_horizon: int,
    ) -> None:
        """Train every sub-strategy on its own filename, then write the
        ensemble's index file referring to the sub-bundle paths."""
        cfg = _load_meta_config(filename_model)
        subs = cfg["subs"]
        for sub_name in subs:
            sub_cls = _resolve_sub(sub_name)
            if sub_cls is None:
                utils.print(
                    f"⚠️ [StrategyEnsemble] sub-strategy '{sub_name}' not registered, "
                    f"skipping its training step.",
                    1,
                )
                continue
            sub_path = _sub_filename(filename_model, sub_name)
            try:
                sub_cls.model_train_model(
                    trade_asset, trade_platform, sub_path, train_window, train_horizon,
                )
            except Exception as exc:
                utils.print(
                    f"⚠️ [StrategyEnsemble] sub '{sub_name}' train failed: {exc}", 0
                )

        # Persist the ensemble index file last so a half-trained run
        # does not look complete.
        os.makedirs(os.path.dirname(filename_model) or ".", exist_ok=True)
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "subs": subs,
                    "weights": cfg["weights"],
                    "train_window": int(train_window),
                    "train_horizon": int(train_horizon),
                },
                f,
                indent=2,
            )

    @staticmethod
    def _aggregate_one(
        cfg: Dict[str, Any], filename_model: str, X_df_or_test, mode: str,
        trade_confidence: int = 55,
    ) -> float:
        """Run each sub once for a single feature row and return the
        weighted-mean probability. `mode` is "buy_sell" or "predict_probs"."""
        names: List[str] = []
        probs: List[float] = []
        for sub_name in cfg["subs"]:
            sub_cls = _resolve_sub(sub_name)
            if sub_cls is None:
                continue
            sub_path = _sub_filename(filename_model, sub_name)
            pkl_path = sub_path.replace(".json", ".ensemble.pkl")
            if not os.path.exists(sub_path) and not os.path.exists(pkl_path):
                continue
            try:
                if mode == "buy_sell":
                    # buy_sell_order returns 0/0.5/1 — treat as confident
                    # probability {0.01, 0.5, 0.99} when the sub fires.
                    decision = float(sub_cls.model_buy_sell_order(
                        X_df_or_test, sub_path, trade_confidence
                    ))
                    if decision == 1:
                        p = 0.99
                    elif decision == 0:
                        p = 0.01
                    else:
                        p = 0.5
                elif mode == "predict_probs":
                    arr = sub_cls.model_predict_probabilities(sub_path, X_df_or_test)
                    p = float(np.asarray(arr).ravel()[0])
                else:
                    raise ValueError(f"unknown mode: {mode}")
                if not np.isfinite(p):
                    continue
                names.append(sub_name)
                probs.append(p)
            except Exception as exc:
                utils.print(
                    f"⚠️ [StrategyEnsemble] sub '{sub_name}' {mode} failed: {exc}", 1,
                )
        return _aggregate(probs, names, cfg["weights"])

    @staticmethod
    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int,
    ) -> float:
        """Live-trade decision: weighted-mean of sub probabilities, then
        threshold against the order path's trade_confidence."""
        cfg = _load_meta_config(filename_model)
        ensemble_prob = StrategyEnsemble._aggregate_one(
            cfg, filename_model, X_df, mode="buy_sell",
            trade_confidence=trade_confidence,
        )
        upper = trade_confidence / 100.0
        lower = 1.0 - upper
        if ensemble_prob > upper:
            return 1
        if ensemble_prob < lower:
            return 0
        return 0.5

    @staticmethod
    def model_predict_probabilities(
        filename_model: str, X_test,
    ) -> np.ndarray:
        """Used by fulltest sweep + by the conformal calibrator. Returns
        one weighted-mean probability per row of X_test."""
        cfg = _load_meta_config(filename_model)

        # Collect per-sub probability arrays first.
        sub_probs_per_strat: List[np.ndarray] = []
        sub_names_kept: List[str] = []
        for sub_name in cfg["subs"]:
            sub_cls = _resolve_sub(sub_name)
            if sub_cls is None:
                continue
            sub_path = _sub_filename(filename_model, sub_name)
            pkl_path = sub_path.replace(".json", ".ensemble.pkl")
            if not os.path.exists(sub_path) and not os.path.exists(pkl_path):
                continue
            try:
                probs = sub_cls.model_predict_probabilities(sub_path, X_test)
                arr = np.asarray(probs, dtype=float).ravel()
                sub_probs_per_strat.append(arr)
                sub_names_kept.append(sub_name)
            except Exception as exc:
                utils.print(
                    f"⚠️ [StrategyEnsemble] sub '{sub_name}' predict_probabilities "
                    f"failed: {exc}", 1,
                )

        if not sub_probs_per_strat:
            n = len(X_test) if hasattr(X_test, "__len__") else 1
            return np.full(n, 0.5, dtype=float)

        # All sub-arrays should have the same length; trim to the min if
        # one is short for any reason.
        n = min(len(a) for a in sub_probs_per_strat)
        weights = cfg["weights"]
        # Resolve weights into the same order as sub_names_kept.
        w_vec = np.array(
            [float(weights.get(name, 1.0)) for name in sub_names_kept],
            dtype=float,
        )
        # Treat negative weights as 0 (caller-error guard).
        w_vec = np.where(w_vec > 0, w_vec, 0.0)
        if w_vec.sum() <= 0:
            return np.full(n, 0.5, dtype=float)
        stacked = np.stack([a[:n] for a in sub_probs_per_strat], axis=0)
        # Mask non-finite values to 0.5 so they neither push nor pull.
        finite_mask = np.isfinite(stacked)
        stacked = np.where(finite_mask, stacked, 0.5)
        # Weighted average across the sub axis (axis=0).
        weighted = stacked * w_vec[:, None]
        out = weighted.sum(axis=0) / w_vec.sum()
        return out

"""Hybrid consensus — trade only when XGBoost AND Momentum agree.

Hypothesis: XGBoost's baseline on noisy OTC pairs trades all day and
clears the trade-count gate but barely beats the break-even line
(~+2 pp edge). Momentum only fires on strong, monotone micro-trends
and — measured in isolation — produced no positive-edge asset. Taken
together, they may reinforce each other: require Momentum's directional
gate (monotone N-bar move above a threshold) AND XGBoost's calibrated
probability to agree on the same direction. The expectation is a
dramatic trade-count reduction with a proportionally larger success
lift, surviving where either model alone fails.

Design:
  - `model_train_model` delegates to XGBoostModel.model_train_model and
    MomentumModel.model_train_model. Filenames are derived from the
    hybrid filename_model by substituting the model slug, so the
    underlying models live at `..._xgboost_...` and `..._momentum_...`
    paths and are reusable by the non-hybrid entries.
  - `model_predict_probabilities` returns a collapsed probability:
      * 0.99 when XGBoost > 0.5 AND Momentum = 0.99 (both CALL)
      * 0.01 when XGBoost < 0.5 AND Momentum = 0.01 (both PUT)
      * 0.5  otherwise (either model abstains or they disagree)
    Momentum is the gate (sparsity); XGBoost is the filter (direction
    cross-check).
"""

import json
import os
from typing import List, Union

import numpy as np
import pandas as pd

from app.utils.singletons import utils


def _component_filename(hybrid_filename: str, component_slug: str) -> str:
    """Replace the `_hybrid_` segment with e.g. `_xgboost_` so components
    share their filenames with standalone runs of the same model/asset."""
    base = os.path.basename(hybrid_filename)
    if "_hybrid_" not in base:
        raise ValueError(
            f"Hybrid filename must contain '_hybrid_' — got '{base}'"
        )
    return os.path.join(
        os.path.dirname(hybrid_filename),
        base.replace("_hybrid_", f"_{component_slug}_", 1),
    )


def _get_xgboost_class():
    from external import xgboost as xgb_ext  # noqa: WPS433 — late import
    return xgb_ext.XGBoostModel


def _get_momentum_class():
    from external import momentum as mom_ext  # noqa: WPS433 — late import
    return mom_ext.MomentumModel


class HybridModel:

    name = "hybrid"

    @staticmethod
    def model_train_model(
        trade_asset: str,
        trade_platform: str,
        filename_model: str,
        train_window: int,
        train_horizon: int,
    ) -> None:
        xgb_filename = _component_filename(filename_model, "xgboost")
        mom_filename = _component_filename(filename_model, "momentum")

        utils.print(
            f"ℹ️ Hybrid: training Momentum component → {os.path.basename(mom_filename)}",
            1,
        )
        _get_momentum_class().model_train_model(
            trade_asset=trade_asset,
            trade_platform=trade_platform,
            filename_model=mom_filename,
            train_window=train_window,
            train_horizon=train_horizon,
        )

        # only retrain XGBoost if its ensemble bundle isn't already there —
        # a fresh XGBoost train takes ~5–10 min on GPU and dominates the
        # loop budget. Callers can force a rebuild by deleting the .pkl.
        xgb_ensemble = os.path.splitext(xgb_filename)[0] + ".ensemble.pkl"
        if os.path.exists(xgb_ensemble):
            utils.print(
                f"ℹ️ Hybrid: reusing existing XGBoost ensemble at "
                f"{os.path.basename(xgb_ensemble)} — delete to force retrain.",
                1,
            )
        else:
            utils.print(
                f"ℹ️ Hybrid: training XGBoost component → {os.path.basename(xgb_filename)}",
                1,
            )
            _get_xgboost_class().model_train_model(
                trade_asset=trade_asset,
                trade_platform=trade_platform,
                filename_model=xgb_filename,
                train_window=train_window,
                train_horizon=train_horizon,
            )

        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "hybrid": True,
                    "components": {
                        "xgboost": os.path.basename(xgb_filename),
                        "momentum": os.path.basename(mom_filename),
                    },
                },
                f,
                indent=2,
            )
        utils.print(
            f"✅ Hybrid manifest saved to {os.path.basename(filename_model)}",
            1,
        )

    @staticmethod
    def _agreement_probs(
        xgb_probs: np.ndarray, mom_probs: np.ndarray
    ) -> np.ndarray:
        """Collapse two probability vectors into 0.99 / 0.01 / 0.5 based on
        directional agreement. Momentum encodes intent explicitly as
        0.99 (CALL) / 0.01 (PUT) / 0.5 (no signal); XGBoost is continuous
        and is thresholded at 0.5 for direction."""
        xgb = np.asarray(xgb_probs, dtype=np.float64)
        mom = np.asarray(mom_probs, dtype=np.float64)
        out = np.full_like(xgb, 0.5, dtype=np.float64)
        call_mask = (mom > 0.5) & (xgb > 0.5)
        put_mask = (mom < 0.5) & (xgb < 0.5)
        out[call_mask] = 0.99
        out[put_mask] = 0.01
        return out

    @staticmethod
    def _load_component_probs(
        filename_model: str, X_test: Union[List[List[float]], np.ndarray]
    ):
        xgb_filename = _component_filename(filename_model, "xgboost")
        mom_filename = _component_filename(filename_model, "momentum")
        xgb_probs = _get_xgboost_class().model_predict_probabilities(
            xgb_filename, X_test
        )
        mom_probs = _get_momentum_class().model_predict_probabilities(
            mom_filename, X_test
        )
        return np.asarray(xgb_probs, dtype=np.float64), np.asarray(
            mom_probs, dtype=np.float64
        )

    @staticmethod
    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        X_np = np.asarray(X_df, dtype=np.float32)
        xgb_probs, mom_probs = HybridModel._load_component_probs(
            filename_model, X_np
        )
        agreement = HybridModel._agreement_probs(xgb_probs, mom_probs)
        prob = float(agreement[0])
        utils.print(
            f"ℹ️ Hybrid: XGB={xgb_probs[0]:.4f}  MOM={mom_probs[0]:.4f}  "
            f"→ agreement={prob:.2f}",
            1,
        )
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
        xgb_probs, mom_probs = HybridModel._load_component_probs(
            filename_model, X_test
        )
        agreement = HybridModel._agreement_probs(xgb_probs, mom_probs)
        upper = trade_confidence / 100.0
        lower = 1.0 - upper
        out: List[float] = []
        for p in agreement:
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
        xgb_probs, mom_probs = HybridModel._load_component_probs(
            filename_model, X_test
        )
        return HybridModel._agreement_probs(xgb_probs, mom_probs)

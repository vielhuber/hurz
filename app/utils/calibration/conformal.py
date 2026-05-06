"""Conformal calibration for binary classification.

Uses Inductive Conformal Prediction (ICP):
    1. Hold out `calibration_fraction` of the (probs, labels) pairs.
    2. Compute nonconformity scores: s_i = 1 - p(y_i | x_i)
       (i.e. how surprised the model was by the true label).
    3. Use the `(1 - alpha)`-quantile of those scores as a threshold.
    4. At inference, a class is included in the prediction set iff
       its predicted probability is at least `1 - quantile`.

For binary trading we map the conformal set to a calibrated decision:
    - prediction set {1}      → calibrated prob = upper bound
    - prediction set {0}      → calibrated prob = lower bound
    - prediction set {0, 1}   → abstain (no confident class)
    - prediction set {}       → abstain (model is unsure for both)

The output of `.transform()` is the asymmetric probability bound that
maps cleanly back into the existing >upper / <lower threshold logic
in `order.py`. Effectively: a raw-prob of 0.55 may transform to 0.51
on a poorly-calibrated asset (= abstain) but stay at 0.62 on a
well-calibrated one.

This implementation does NOT depend on `mapie` — it is a faithful 60-
line ICP for the binary case, easier to debug than the library route.
"""
from typing import Any, Dict, Optional

import numpy as np

from app.utils.calibration.base import Calibrator


class ConformalCalibrator(Calibrator):

    name = "conformal"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.alpha = float(config.get("alpha", 0.1))
        self.calibration_fraction = float(config.get("calibration_fraction", 0.2))
        self.min_calibration_samples = int(config.get("min_calibration_samples", 200))
        self._quantile_p1: Optional[float] = None  # quantile for class-1 nonconformity
        self._quantile_p0: Optional[float] = None  # quantile for class-0 nonconformity
        self._fitted = False

    def fit(self, probs: np.ndarray, labels: np.ndarray) -> None:
        probs = np.asarray(probs, dtype=float)
        labels = np.asarray(labels, dtype=int)
        if probs.shape[0] != labels.shape[0] or probs.shape[0] < self.min_calibration_samples:
            self._fitted = False
            return

        # Class-conditional nonconformity scores. We split because asset
        # imbalance in 1h-direction is rarely 50/50 — a global quantile
        # gets dominated by the majority class.
        ones_mask = labels == 1
        zeros_mask = labels == 0
        # For class 1: the nonconformity is "how unsure was the model that this is class 1"
        s_ones = 1.0 - probs[ones_mask]
        # For class 0: model output is p(class 1), so "unsure for 0" = p itself
        s_zeros = probs[zeros_mask]

        if s_ones.size >= self.min_calibration_samples // 2:
            n = s_ones.size
            # ICP correction: ceil((n+1) * (1-alpha)) / n
            q_idx = min(n - 1, int(np.ceil((n + 1) * (1 - self.alpha))) - 1)
            self._quantile_p1 = float(np.sort(s_ones)[q_idx])
        if s_zeros.size >= self.min_calibration_samples // 2:
            n = s_zeros.size
            q_idx = min(n - 1, int(np.ceil((n + 1) * (1 - self.alpha))) - 1)
            self._quantile_p0 = float(np.sort(s_zeros)[q_idx])

        self._fitted = (
            self._quantile_p1 is not None and self._quantile_p0 is not None
        )

    def transform(self, probs: np.ndarray) -> np.ndarray:
        probs = np.asarray(probs, dtype=float)
        if not self._fitted:
            return probs

        # Determine prediction-set membership per sample:
        #   class 1 in set iff (1 - p)  <= q_p1  →  p >= 1 - q_p1
        #   class 0 in set iff       p  <= q_p0  →  p <= q_p0
        thresh_for_one = 1.0 - (self._quantile_p1 or 1.0)
        thresh_for_zero = self._quantile_p0 or 0.0

        out = np.full_like(probs, 0.5)
        only_one = (probs >= thresh_for_one) & (probs > thresh_for_zero)
        only_zero = (probs <= thresh_for_zero) & (probs < thresh_for_one)
        # both / neither stay at 0.5 → abstain in the order-path threshold logic
        out[only_one] = np.maximum(probs[only_one], thresh_for_one)
        out[only_zero] = np.minimum(probs[only_zero], thresh_for_zero)
        return out

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def state(self) -> Dict[str, Any]:
        return {
            "fitted": self._fitted,
            "alpha": self.alpha,
            "quantile_p1": self._quantile_p1,
            "quantile_p0": self._quantile_p0,
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        q1 = state.get("quantile_p1")
        q0 = state.get("quantile_p0")
        self._quantile_p1 = float(q1) if q1 is not None else None
        self._quantile_p0 = float(q0) if q0 is not None else None
        if "alpha" in state and state["alpha"] is not None:
            self.alpha = float(state["alpha"])
        self._fitted = (
            bool(state.get("fitted", False))
            and self._quantile_p1 is not None
            and self._quantile_p0 is not None
        )

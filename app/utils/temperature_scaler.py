import numpy as np
from scipy.optimize import minimize_scalar


class TemperatureScaler:
    """Single-parameter temperature scaling: p_cal = sigmoid(logit(p) / T).

    Drop-in replacement for sklearn.isotonic.IsotonicRegression in the
    seed-ensemble pipeline: exposes .fit(probs, labels) and .transform(probs).

    Why temperature scaling instead of isotonic:
      - Isotonic is non-parametric and collapses the output distribution
        around 0.5 when the val set's raw probs are concentrated there.
      - Temperature scaling has a single scalar parameter, so it can only
        rescale the logit axis — it never flattens variance. That
        preserves a wide live-prediction distribution.
      - T < 1 sharpens (spreads probs away from 0.5), T > 1 softens.
        Learned from the validation labels via log-loss minimization.

    Lives in app/utils/ (not external/xgboost.py) because external modules
    are loaded via importlib.spec_from_file_location with name "xgboost",
    which would set __module__ to "xgboost" and break pickling — pickle
    would then look for the class inside the installed xgboost package.
    """

    def __init__(self) -> None:
        self.T: float = 1.0

    def fit(self, probs: np.ndarray, labels: np.ndarray) -> "TemperatureScaler":
        probs = np.clip(np.asarray(probs, dtype=np.float64), 1e-7, 1 - 1e-7)
        labels = np.asarray(labels, dtype=np.float64)
        logits = np.log(probs / (1 - probs))

        def nll(T: float) -> float:
            if T <= 0:
                return 1e10
            scaled = 1.0 / (1.0 + np.exp(-logits / T))
            scaled = np.clip(scaled, 1e-7, 1 - 1e-7)
            return -float(
                np.mean(labels * np.log(scaled) + (1 - labels) * np.log(1 - scaled))
            )

        result = minimize_scalar(nll, bounds=(0.05, 20.0), method="bounded")
        self.T = float(result.x)
        return self

    def transform(self, probs: np.ndarray) -> np.ndarray:
        probs = np.clip(np.asarray(probs, dtype=np.float64), 1e-7, 1 - 1e-7)
        logits = np.log(probs / (1 - probs))
        return 1.0 / (1.0 + np.exp(-logits / self.T))

"""Default no-op calibrator — preserves current system behaviour."""
from typing import Any, Dict

import numpy as np

from app.utils.calibration.base import Calibrator


class PassthroughCalibrator(Calibrator):

    name = "passthrough"

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        super().__init__(config or {})

    def fit(self, probs: np.ndarray, labels: np.ndarray) -> None:
        return

    def transform(self, probs: np.ndarray) -> np.ndarray:
        return np.asarray(probs, dtype=float)

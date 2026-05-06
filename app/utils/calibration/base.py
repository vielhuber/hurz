"""Base class for probability calibrators."""
import json
import os
from typing import Any, Dict, Optional

import numpy as np


class Calibrator:

    name: str = "abstract"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = dict(config or {})

    def fit(self, probs: np.ndarray, labels: np.ndarray) -> None:
        """Calibrate on a held-out set. `labels` is binary (0 = down, 1 = up).

        For passthrough this is a no-op. For conformal etc., this caches
        the nonconformity quantile / mapping. Must be safe to call once
        per asset per fulltest run.
        """
        raise NotImplementedError

    def transform(self, probs: np.ndarray) -> np.ndarray:
        """Return calibrated probabilities (same shape as input)."""
        raise NotImplementedError

    def transform_decision(
        self, prob: float, threshold: float
    ) -> Optional[bool]:
        """High-level: return True (CALL), False (PUT) or None (abstain).

        Default behaviour: derive from `transform([prob])[0]` against
        `threshold` and `1-threshold`. Subclasses with set-valued output
        (e.g. conformal sets) override this.
        """
        cal = float(self.transform(np.asarray([prob], dtype=float))[0])
        upper = threshold
        lower = 1.0 - threshold
        if cal > upper:
            return True
        if cal < lower:
            return False
        return None

    def state(self) -> Dict[str, Any]:
        """Serialisable snapshot of fitted state. Override in subclasses."""
        return {}

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore fitted state from a snapshot. Override in subclasses."""
        return

    def save(self, path: str) -> None:
        """Persist fitted state to disk as JSON."""
        snapshot = self.state()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)

    def load(self, path: str) -> bool:
        """Restore fitted state from disk. Returns True if a usable snapshot
        was loaded, False if the file is missing or unreadable."""
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
            self.load_state(snapshot)
            return bool(getattr(self, "is_fitted", False))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return False

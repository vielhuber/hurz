"""Probability post-processors.

A `Calibrator` takes raw model probabilities and returns a calibrated
version. The active calibrator is selected by `feature_flags.json ->
calibration.active`. The default `passthrough` returns probs unchanged
so the system keeps its current behaviour when the feature isn't
explicitly switched on.
"""
from app.utils.calibration.base import Calibrator
from app.utils.calibration.passthrough import PassthroughCalibrator
from app.utils.calibration.conformal import ConformalCalibrator
from app.utils.calibration.factory import get_active_calibrator, save_calibrator

__all__ = [
    "Calibrator",
    "PassthroughCalibrator",
    "ConformalCalibrator",
    "get_active_calibrator",
    "save_calibrator",
]

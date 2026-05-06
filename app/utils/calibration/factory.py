"""Picks the active calibrator from feature_flags.json -> calibration.active.

Caches per-asset calibrator instances so the conformal quantiles fitted
on the fulltest holdout can be reused across many predict calls. Call
`reset_cache()` from the fulltest pipeline so a fresh fulltest re-fits
the calibrator from updated quantiles.

Calibrator state is also persisted to disk under `models/` so a fitted
calibrator survives a bot restart. Without disk persistence, conformal
silently falls back to passthrough until the next fulltest run.
"""
import os
from typing import Dict, Optional

from app.utils.feature_flags import FeatureFlags
from app.utils.calibration.base import Calibrator
from app.utils.calibration.passthrough import PassthroughCalibrator
from app.utils.calibration.conformal import ConformalCalibrator


_CALIBRATOR_CLASSES: Dict[str, type] = {
    "passthrough": PassthroughCalibrator,
    "conformal":   ConformalCalibrator,
}

_INSTANCE_CACHE: Dict[str, Calibrator] = {}

CALIBRATOR_DIR = "models"


def _calibrator_path(active_name: str, asset: str) -> str:
    safe_asset = asset.replace("/", "_").replace(" ", "_")
    return os.path.join(
        CALIBRATOR_DIR, f"calibrator_{active_name}_{safe_asset}.json"
    )


def get_active_calibrator(asset: str) -> Calibrator:
    """Return the calibrator instance for `asset` according to current flags.

    Multiple calls with the same asset return the same instance (so a
    `fit()` made during the fulltest persists for live `transform()`s).
    On a cache miss the calibrator also tries to load fitted state from
    disk so a bot restart does not silently fall back to passthrough.
    """
    cfg = FeatureFlags.section("calibration")
    active_name = (cfg.get("active") or "passthrough").lower()
    cls = _CALIBRATOR_CLASSES.get(active_name, PassthroughCalibrator)

    cache_key = f"{active_name}::{asset}"
    inst = _INSTANCE_CACHE.get(cache_key)
    if inst is None:
        sub = cfg.get(active_name, {}) if active_name != "passthrough" else {}
        inst = cls(sub)
        if active_name != "passthrough":
            inst.load(_calibrator_path(active_name, asset))
        _INSTANCE_CACHE[cache_key] = inst
    return inst


def save_calibrator(asset: str, calibrator: Calibrator) -> None:
    """Persist the calibrator's fitted state to disk so it survives
    bot restarts. No-op for passthrough."""
    active_name = getattr(calibrator, "name", "passthrough")
    if active_name == "passthrough":
        return
    calibrator.save(_calibrator_path(active_name, asset))


def reset_cache(asset: Optional[str] = None) -> None:
    """Drop cached calibrator(s). Pass an asset to reset just that one,
    None to wipe everything (e.g. on flag reload)."""
    global _INSTANCE_CACHE
    if asset is None:
        _INSTANCE_CACHE = {}
    else:
        for key in list(_INSTANCE_CACHE.keys()):
            if key.endswith(f"::{asset}"):
                del _INSTANCE_CACHE[key]

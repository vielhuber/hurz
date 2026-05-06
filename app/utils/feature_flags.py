"""Centralised feature-flag loader.

Reads `data/feature_flags.json` once on first access and caches it.
Every gate / calibrator / metric / scheduler asks for its own sub-config
through `FeatureFlags.section(...)`. Disabled features short-circuit
without instantiating.

Reload semantics: call `FeatureFlags.reload()` from the menu / a SIGHUP
handler if you need to flip a flag without restarting the process.
"""
import json
import os
import threading
from typing import Any, Dict, Optional


DEFAULT_PATH = "data/feature_flags.json"


class FeatureFlags:

    _lock = threading.Lock()
    _cache: Optional[Dict[str, Any]] = None
    _path: str = DEFAULT_PATH

    @classmethod
    def load(cls, path: Optional[str] = None) -> Dict[str, Any]:
        with cls._lock:
            if path is not None:
                cls._path = path
            if cls._cache is None:
                cls._cache = cls._read_file(cls._path)
            return cls._cache

    @classmethod
    def reload(cls, path: Optional[str] = None) -> Dict[str, Any]:
        with cls._lock:
            if path is not None:
                cls._path = path
            cls._cache = cls._read_file(cls._path)
            return cls._cache

    @classmethod
    def section(cls, *keys: str) -> Dict[str, Any]:
        node: Any = cls.load()
        for k in keys:
            if not isinstance(node, dict):
                return {}
            node = node.get(k, {})
        return node if isinstance(node, dict) else {}

    @classmethod
    def is_enabled(cls, *keys: str) -> bool:
        return bool(cls.section(*keys).get("enabled", False))

    @staticmethod
    def _read_file(path: str) -> Dict[str, Any]:
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

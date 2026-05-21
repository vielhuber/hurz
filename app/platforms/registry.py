"""Platform registry — pick the active broker by name.

Reads credentials from environment variables (.env) so secrets stay
out of the repo. The `data/settings.json -> trade_platform` field
determines which platform `get_platform()` returns.

Available registry keys:
    "kraken"       — KrakenPlatform (spot crypto)
    "capital_com"  — CapitalComPlatform (CFD/spot multi-asset)
    "pocketoption" — legacy binary-options broker, NOT in this registry;
                     code paths still go through the old singleton
                     (`app.singletons.websocket`) for backwards-compat.

Calling `get_platform()` with `pocketoption` raises so callers get a
clear hint that they should use the legacy code path instead.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from app.platforms.base import Platform
from app.platforms.kraken import KrakenPlatform
from app.platforms.kraken_futures import KrakenFuturesPlatform
from app.platforms.capital_com import CapitalComPlatform


_PLATFORM_CLASSES: Dict[str, type] = {
    "kraken": KrakenPlatform,
    "kraken_futures": KrakenFuturesPlatform,
    "capital_com": CapitalComPlatform,
}

# Module-level cache so repeated calls return the same instance — this
# matters because the platform holds an aiohttp session and cached
# tokens that should not be re-created per call.
_INSTANCE_CACHE: Dict[str, Platform] = {}


def available_platforms() -> List[str]:
    """Names of platforms registered in this build."""
    return list(_PLATFORM_CLASSES.keys())


def _read_credentials(name: str) -> Dict[str, str]:
    """Map env vars to the per-platform credential dict that the
    platform's `__init__` expects. Missing vars become empty strings —
    the platform decides whether that's enough to run public-only
    or whether `has_credentials` flips False."""
    if name == "kraken":
        return {
            "api_key":    os.getenv("KRAKEN_API_KEY", "").strip(),
            "api_secret": os.getenv("KRAKEN_API_SECRET", "").strip(),
        }
    if name == "kraken_futures":
        return {
            "api_key":    os.getenv("KRAKEN_FUTURES_API_KEY", "").strip(),
            "api_secret": os.getenv("KRAKEN_FUTURES_API_SECRET", "").strip(),
        }
    if name == "capital_com":
        return {
            "api_key":    os.getenv("CAPITAL_COM_API_KEY", "").strip(),
            "identifier": os.getenv("CAPITAL_COM_IDENTIFIER", "").strip(),
            "password":   os.getenv("CAPITAL_COM_PASSWORD", "").strip(),
        }
    return {}


def _read_demo_flag(name: str) -> bool:
    """Per-platform demo-vs-live toggle from env. Defaults to True so
    a fresh checkout never accidentally trades real money."""
    if name == "capital_com":
        flag = os.getenv("CAPITAL_COM_DEMO", "1").strip().lower()
        return flag not in ("0", "false", "no", "off")
    if name == "kraken":
        # Kraken has no separate demo endpoint — `demo=True` here
        # means "treat private endpoints as locked unless creds say
        # otherwise". The actual env switch is whether you put an API
        # key in or not.
        return True
    if name == "kraken_futures":
        flag = os.getenv("KRAKEN_FUTURES_DEMO", "1").strip().lower()
        return flag not in ("0", "false", "no", "off")
    return True


def _read_paper_trade_flag() -> bool:
    """Global paper-trade safety flag. Defaults to True (= refuse to
    place real orders). Operator must explicitly set
    PAPER_TRADE_ONLY=0 in .env to enable order placement.

    This is independent of the per-platform demo flag — it applies
    BOTH on demo endpoints (extra safety while developing) AND on
    live endpoints. Flipping it off should always be a deliberate,
    documented step."""
    flag = os.getenv("PAPER_TRADE_ONLY", "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def get_platform(name: Optional[str] = None) -> Platform:
    """Return a configured platform adapter by registry name.

    If `name` is not given we look it up from `store.trade_platform`
    so the rest of the code can do `get_platform()` without knowing
    the active selection.

    Cached: subsequent calls return the same instance. Use
    `clear_cache()` to force re-creation (e.g. on credential rotation).
    """
    if name is None:
        # Lazy import — registry must stay importable in tests that
        # don't bring up the singleton stack.
        from app.utils.singletons import store
        name = getattr(store, "trade_platform", None)
    if not name:
        raise ValueError("get_platform: no platform name and no store.trade_platform")
    name = name.lower()
    if name == "pocketoption":
        raise ValueError(
            "get_platform: pocketoption is not in the new platforms "
            "registry — use the legacy app.singletons.websocket path."
        )
    if name not in _PLATFORM_CLASSES:
        raise ValueError(
            f"get_platform: unknown platform '{name}'. "
            f"Registered: {available_platforms()}"
        )
    if name in _INSTANCE_CACHE:
        return _INSTANCE_CACHE[name]
    cls = _PLATFORM_CLASSES[name]
    creds = _read_credentials(name)
    demo = _read_demo_flag(name)
    paper = _read_paper_trade_flag()
    inst = cls(credentials=creds, demo=demo, paper_trade_only=paper)
    _INSTANCE_CACHE[name] = inst
    return inst


def clear_cache() -> None:
    """Drop cached platform instances. Pending sessions are NOT closed
    by this call — callers should `await platform.disconnect()` first
    if a clean shutdown is required."""
    global _INSTANCE_CACHE
    _INSTANCE_CACHE = {}

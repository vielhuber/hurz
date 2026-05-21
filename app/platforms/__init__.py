"""Trading-platform adapters.

Each platform exposes a uniform `Platform` interface (see `base.py`) so
the rest of the system (autotrade, fulltest, gates, models) does not
need to know which broker is connected. Platforms self-describe via
the registry — pick one by setting `data/settings.json -> trade_platform`.

Currently shipped:
  - kraken      — Kraken Spot Exchange (real exchange, BaFin-licensed
                  German entity for crypto). Cleanest for crypto-only.
  - capital_com — Capital.com (CySEC, EU-passported). CFD/Spot-FX +
                  crypto + indices + stocks. Closest match for the
                  legacy Pocket Option workflow.
  - pocketoption — legacy binary-options broker. Kept for backwards
                  compatibility but the trading semantics (binary
                  payouts) differ fundamentally; new development
                  should target the spot/CFD platforms.
"""
from app.platforms.base import (
    Platform,
    PlatformError,
    PlatformAuthError,
    PlatformAPIError,
    PaperTradeOnlyError,
    Bar,
    Instrument,
    Position,
    OrderResult,
)
from app.platforms.registry import get_platform, available_platforms

__all__ = [
    "Platform",
    "PlatformError",
    "PlatformAuthError",
    "PlatformAPIError",
    "PaperTradeOnlyError",
    "Bar",
    "Instrument",
    "Position",
    "OrderResult",
    "get_platform",
    "available_platforms",
]

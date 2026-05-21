"""Spot-trading subsystem.

Replaces the binary-options auto-trade flow (`app/singletons/autotrade.py`)
when `data/settings.json -> trade_platform` is `kraken` or `capital_com`.
The legacy PocketOption path is unaffected — we dispatch in `main.py`.

Modules:
  pair_selector — ranks instruments by backtested edge, persists the
                  active list to data/active_pairs.json
  sweep         — fulltest-equivalent for spot strategies (R-multiple
                  search over stop/target combinations per pair)
  autotrade     — live trade loop driven by platform price-stream +
                  active strategy + active-pair list
"""
from app.spot_trading.pair_selector import (
    PairScore,
    rank_pairs,
    persist_active_pairs,
    load_active_pairs,
)

__all__ = [
    "PairScore",
    "rank_pairs",
    "persist_active_pairs",
    "load_active_pairs",
]

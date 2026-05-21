"""Pull live bid/offer for every active Capital.com pair and persist
per-side spread estimates to data/capital_spreads.json.

The backtest fee-resolver reads this file to use ASSET-specific fees
instead of a coarse FX/Crypto category default. That matters: the
overnight live-demo session showed that ADAUSD's real round-trip
spread is ~0.42% (entry 0.27026 → fill 0.27082, ~0.21% per side),
five times what the 0.05% Crypto-default in the resolver assumed.
Backtests using the wrong number flatter the active-pair list.

Usage:
    python3 scripts/capital_spread_audit.py            # default pairs
    python3 scripts/capital_spread_audit.py --pairs EURUSD GBPUSD ADAUSD
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PAPER_TRADE_ONLY"] = "1"

from app.utils.singletons import settings  # noqa: E402
settings.load_env()
os.environ["PAPER_TRADE_ONLY"] = "1"

from app.platforms.registry import get_platform, clear_cache  # noqa: E402


_OUTPUT_PATH = "data/capital_spreads.json"

# Default audit list: union of pairs in the default backtest basket
# and pairs currently sitting in active_pairs.json. New pairs are
# easy to add — pass --pairs.
_DEFAULT_PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF",
    "BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "DOGEUSD", "XRPUSD",
]


async def audit(pairs: List[str]) -> dict:
    clear_cache()
    p = get_platform("capital_com")
    await p.connect()

    out: dict = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "GET /api/v1/markets/{epic} bid+offer",
        "pairs": {},
    }

    print(f"{'pair':<10} {'bid':>14} {'offer':>14} {'spread':>10} {'%/side':>10}")
    print("-" * 64)
    for pair in pairs:
        try:
            data = await p._raw_request(
                "GET", f"/api/v1/markets/{pair}", auth=True,
            )
        except Exception as exc:
            print(f"{pair:<10} ⛔ {str(exc)[:40]}")
            continue
        snap = data.get("snapshot") or {}
        bid = snap.get("bid")
        offer = snap.get("offer")
        if bid is None or offer is None:
            print(f"{pair:<10} ⛔ missing bid/offer in snapshot")
            continue
        bid = float(bid); offer = float(offer)
        mid = (bid + offer) / 2.0
        # Per-side spread = half-spread / mid. This is the cost the
        # round-trip takes 2× — matching how the backtest applies fees
        # at entry on `entry` and at exit on `out_p`.
        per_side = (offer - bid) / 2.0 / mid if mid > 0 else 0.0
        out["pairs"][pair] = {
            "bid": bid,
            "offer": offer,
            "mid": mid,
            "spread_abs": offer - bid,
            "fee_per_side": per_side,
        }
        print(f"{pair:<10} {bid:>14.5f} {offer:>14.5f} "
              f"{offer-bid:>10.5f} {per_side*100:>+9.4f}%")

    os.makedirs(os.path.dirname(_OUTPUT_PATH), exist_ok=True)
    with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print()
    print(f"✓ wrote {len(out['pairs'])} pairs to {_OUTPUT_PATH}")
    return out


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--pairs", nargs="+", default=_DEFAULT_PAIRS)
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(audit(_parse_args().pairs)) and 0 or 0)

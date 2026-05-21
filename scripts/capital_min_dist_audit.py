"""Pull venue min-stop-distance + dealing rules for every active
Capital.com pair and persist to data/capital_min_distances.json.

The pair-selector's pre-filter reads this file to drop combos whose
backtested ATR-stops are chronically below the venue minimum (which
would cause the autotrader's skip-tight-stops guard to fire on
nearly every signal — i.e. the combo is structurally untradeable
on this venue, regardless of edge).

Output schema per pair:
    {
      "min_dist_value":  float,      # raw value from dealing rules
      "min_dist_unit":   str,        # "PERCENTAGE" / "POINTS" / "PIPS"
      "ref_price":       float,      # bid+offer midpoint at audit time
      "min_dist_price":  float,      # converted to price-units around mid
    }

Usage:
    python3 scripts/capital_min_dist_audit.py
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


_OUTPUT_PATH = "data/capital_min_distances.json"

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
        "source": "GET /api/v1/markets/{epic} dealingRules.minStopOrProfitDistance",
        "buffer_factor": 1.05,  # mirrors the autotrader's clamp buffer
        "pairs": {},
    }

    print(f"{'pair':<10} {'unit':<12} {'value':>10} {'ref_price':>14} {'min_price':>14}")
    print("-" * 64)
    for pair in pairs:
        try:
            data = await p._raw_request(
                "GET", f"/api/v1/markets/{pair}", auth=True,
            )
        except Exception as exc:
            print(f"{pair:<10} ⛔ {str(exc)[:40]}")
            continue
        rules = data.get("dealingRules") or {}
        snap = data.get("snapshot") or {}
        mso = rules.get("minStopOrProfitDistance") or {}
        unit = mso.get("unit")
        value = mso.get("value")
        bid = snap.get("bid")
        offer = snap.get("offer")
        if bid is None or offer is None or value is None:
            print(f"{pair:<10} ⛔ missing fields (unit={unit} value={value} bid={bid} offer={offer})")
            continue
        mid = (float(bid) + float(offer)) / 2.0
        # Convert to a price-units distance around `mid`. Same conversion
        # as in CapitalComPlatform.place_order auto-clamp / min_stop_distance.
        if unit == "PERCENTAGE":
            min_price = mid * float(value) * 1.05
        elif unit in ("POINTS", "PIPS"):
            # 1 point = 1 unit in the smallest decimal step; we don't
            # have an easy "pip_size" without per-pair metadata.
            # Conservative fallback: leave as raw value (price units).
            min_price = float(value)
        else:
            min_price = 0.0
        out["pairs"][pair] = {
            "min_dist_value": float(value),
            "min_dist_unit": unit,
            "ref_price": mid,
            "min_dist_price": min_price,
        }
        print(f"{pair:<10} {str(unit):<12} {float(value):>10.5f} {mid:>14.5f} {min_price:>14.5f}")

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

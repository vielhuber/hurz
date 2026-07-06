"""CLI for the pair-selector — rank backtest results, persist top-N.

Usage:
    python3 scripts/select_pairs.py
        # rank all combinations, print sorted, persist top-10

    python3 scripts/select_pairs.py --strategy multi_consensus --top 5
        # only multi_consensus, take top-5

    python3 scripts/select_pairs.py --platform kraken --resolution 1h
        # filter to a specific (platform, resolution)
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.spot_trading.pair_selector import (
    rank_pairs, persist_active_pairs,
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--platform", default=None,
                   help="filter to a single platform (kraken / capital_com)")
    p.add_argument("--strategy", default=None,
                   help="filter to a single strategy")
    p.add_argument("--resolution", default=None,
                   help="filter to a single resolution (1h, 15m, ...)")
    p.add_argument("--min-trades", type=int, default=30)
    p.add_argument("--min-pf", type=float, default=1.0)
    p.add_argument("--min-er", type=float, default=0.0)
    # Walk-forward stability gate. Default 0.66 ≈ "edge must hold in
    # at least 2 of 3 segments". Set to 0.0 to disable.
    p.add_argument("--min-stability", type=float, default=0.66,
                   help="min walk-forward stability ratio (default 0.66)")
    p.add_argument("--top", type=int, default=10,
                   help="how many top-ranked pairs to persist (default 10)")
    # Strategy allow-list (comma-separated). Only these strategies are
    # eligible for selection — used to keep proven-losing strategy
    # classes (mean-reversion) out of the live basket entirely.
    p.add_argument("--strategies", default=None,
                   help="comma-separated allow-list of strategies")
    p.add_argument("--no-persist", dest="persist",
                   action="store_false", default=True)
    args = p.parse_args()

    allowed = None
    if args.strategies:
        allowed = {s.strip() for s in args.strategies.split(",") if s.strip()}

    scores = rank_pairs(
        platform=args.platform, strategy=args.strategy,
        resolution=args.resolution,
        min_trades=args.min_trades, min_pf=args.min_pf,
        min_expectancy_R=args.min_er,
        min_stability_ratio=args.min_stability,
        allowed_strategies=allowed,
    )
    if not scores:
        print("No backtest results matching the filters. "
              "Run scripts/spot_backtest.py first.")
        return
    print(f"{'rank':<5} {'platform':<11} {'strategy':<16} {'res':<5} "
          f"{'pair':<14} {'n':>4} {'win%':>6} {'PF':>6} "
          f"{'E[R]':>7} {'score':>7}")
    print("-" * 88)
    for i, s in enumerate(scores[:30], 1):
        pf_str = f"{s.profit_factor:.2f}"
        print(f"{i:<5} {s.platform:<11} {s.strategy:<16} {s.resolution:<5} "
              f"{s.pair:<14} {s.n:>4} {s.win_rate*100:>5.1f}% {pf_str:>6} "
              f"{s.expectancy_R:>+7.3f} {s.score:>+7.3f}")

    if args.persist:
        # Per-platform file when --platform is set, so concurrent bots
        # don't overwrite each other. Single-platform setups (no filter)
        # keep writing the legacy default path.
        out_path = (
            f"data/active_pairs.{args.platform}.json"
            if args.platform
            else "data/active_pairs.json"
        )
        payload = persist_active_pairs(scores, top_n=args.top,
                                       out_path=out_path,
                                       platform=args.platform)
        pinned_n = sum(1 for p in payload["pairs"] if p.get("pinned"))
        print()
        print(f"✓ persisted top-{args.top} to {out_path} "
              f"({len(payload['pairs'])} pairs, {pinned_n} pinned)")


if __name__ == "__main__":
    main()

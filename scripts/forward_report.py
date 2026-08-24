"""Forward-performance report against the daily target.

The filters that went live on 2026-08-24 were calibrated on the trades
that preceded them, so their in-sample expectancy proves nothing. This
report deliberately measures only trades opened AFTER a cutoff, so the
number it prints is genuinely out-of-sample.

Usage:
    python3 scripts/forward_report.py
    python3 scripts/forward_report.py --since 2026-08-24 --target-eur 50
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Filters went live here; trades before this are the calibration sample.
DEFAULT_CUTOFF = "2026-08-24"
DEFAULT_TARGET_EUR = 50.0
EUR_USD = 1.1699


def _rows(cutoff: str) -> list:
    from app.utils.singletons import database
    return database.select(
        """
        SELECT created_at, exit_time, realized_pnl, size, strategy, pair,
               COALESCE(fill_price, entry_price) AS px, stop_loss
        FROM spot_trades
        WHERE accepted = 1 AND paper_mode = 0 AND platform = 'capital_com'
          AND exit_time IS NOT NULL AND realized_pnl IS NOT NULL
          AND size > 0 AND created_at >= %s
          AND ABS(COALESCE(fill_price, entry_price) - stop_loss) > 0
        ORDER BY exit_time
        """,
        (cutoff,),
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--since", default=DEFAULT_CUTOFF,
                   help=f"only trades opened on/after this date "
                        f"(default {DEFAULT_CUTOFF})")
    p.add_argument("--target-eur", type=float, default=DEFAULT_TARGET_EUR)
    args = p.parse_args()

    rows = _rows(args.since)
    target_usd = args.target_eur * EUR_USD
    print(f"Forward window: trades opened on/after {args.since}")
    print(f"Target: {args.target_eur:.2f} EUR/day = {target_usd:.2f} USD/day\n")
    if not rows:
        print("No closed trades in the forward window yet — nothing to judge.")
        print("The filters cut trade frequency sharply, so allow several")
        print("days before reading anything into an empty or tiny sample.")
        return

    days = len({r["exit_time"].date() for r in rows})
    pnl = sum(float(r["realized_pnl"]) for r in rows)
    r_values = [
        float(r["realized_pnl"])
        / (abs(float(r["px"]) - float(r["stop_loss"])) * float(r["size"]))
        for r in rows
    ]
    mean_r = sum(r_values) / len(r_values)
    wins = sum(1 for v in r_values if v > 0)

    print(f"{'closed trades':<24}{len(rows)}")
    print(f"{'days with a close':<24}{days}")
    print(f"{'win rate':<24}{100 * wins / len(rows):.1f}%")
    print(f"{'expectancy':<24}{mean_r:+.4f} R")
    print(f"{'realised':<24}{pnl:+.2f} USD "
          f"({pnl / EUR_USD:+.2f} EUR)")
    if days:
        per_day = pnl / days
        print(f"{'per day':<24}{per_day:+.2f} USD "
              f"({per_day / EUR_USD:+.2f} EUR)")
        print(f"{'share of target':<24}{100 * per_day / target_usd:.1f}%")

    # The sample size that would make a positive mean meaningful rather
    # than a run of luck, at the observed spread of outcomes.
    if len(r_values) > 1 and mean_r > 0:
        var = sum((v - mean_r) ** 2 for v in r_values) / (len(r_values) - 1)
        sd = var ** 0.5
        if sd > 0:
            needed = int((2.0 * sd / mean_r) ** 2) + 1
            print(f"\n{'trades for 2-sigma':<24}{needed}")
            print("(how many closes it takes before this expectancy is "
                  "distinguishable from zero)")


if __name__ == "__main__":
    main()

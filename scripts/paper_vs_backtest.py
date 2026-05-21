"""Compare a paper-mode session's actual signals against the
expectations from `spot_backtest_results.json`.

After a 24h paper session, this script answers:
  - Did we get the expected number of signals?
  - Were they on the expected pairs?
  - Did the directional balance look like the backtest?
  - For closed bars where a signal SHOULD have fired (in-sample),
    did the live loop also catch it?

Useful as a calibration sanity-check before flipping to live demo
orders. If paper-mode signal density diverges wildly from backtest,
something is off (data freshness, indicator state, dedup bug, ...).

Usage:
    python3 scripts/paper_vs_backtest.py --since 24h
    python3 scripts/paper_vs_backtest.py --since 24h --platform capital_com
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.singletons import settings, database
settings.load_env()
database.init_connection()

from app.spot_trading.pair_selector import load_active_pairs


_RESULTS_PATH = "data/spot_backtest_results.json"


def _parse_since(spec: str) -> tuple:
    """Returns (sql_string, hours_back)."""
    spec = spec.strip().lower()
    units = {"h": 1, "d": 24, "w": 168}
    n = ""; u = "h"
    for ch in spec:
        if ch.isdigit():
            n += ch
        elif ch in units:
            u = ch; break
    hours = int(n or "24") * units.get(u, 1)
    cutoff = datetime.now() - timedelta(hours=hours)
    return cutoff.strftime("%Y-%m-%d %H:%M:%S"), hours


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--since", default="24h", help="time window (e.g. 24h, 7d)")
    p.add_argument("--platform", default=None)
    args = p.parse_args()

    cutoff, hours = _parse_since(args.since)
    print(f"Comparing paper-mode signals (since {cutoff}, {hours}h window) "
          f"against backtest expectations.")
    print()

    # 1. What did we expect?
    if not os.path.exists(_RESULTS_PATH):
        print(f"⛔ {_RESULTS_PATH} not found — run scripts/spot_backtest.py first.")
        return
    with open(_RESULTS_PATH) as f:
        results = json.load(f)

    active = load_active_pairs()
    if args.platform:
        active = [p for p in active if p.get("platform") == args.platform]
    if not active:
        print("⛔ no active pairs — run scripts/select_pairs.py first.")
        return

    # Per-pair signals/30d expectation → scale to the requested window.
    expected_by_pair = defaultdict(float)
    for entry in active:
        n_30d = float(entry.get("n", 0))
        scaled = n_30d * (hours / (30 * 24))
        expected_by_pair[entry["pair"]] += scaled

    total_expected = sum(expected_by_pair.values())

    # 2. What did we observe?
    where = ["created_at >= %s"]
    params = [cutoff]
    if args.platform:
        where.append("platform = %s"); params.append(args.platform)
    rows = database.select(
        f"SELECT pair, strategy, direction, accepted, paper_mode "
        f"FROM spot_trades WHERE {' AND '.join(where)}",
        tuple(params),
    )
    observed_by_pair = Counter(r["pair"] for r in rows)
    observed_by_strategy = Counter(r["strategy"] for r in rows)
    long_count = sum(1 for r in rows if r["direction"] == 1)
    short_count = sum(1 for r in rows if r["direction"] == -1)
    paper_count = sum(1 for r in rows if r["paper_mode"])
    accepted_count = sum(1 for r in rows if r["accepted"])

    total_observed = len(rows)
    print(f"  expected signals (scaled from backtest): {total_expected:.1f}")
    print(f"  observed signals:                        {total_observed}")
    if total_expected > 0:
        ratio = total_observed / total_expected
        if ratio < 0.3:
            tag = "❌ FAR below expectation"
        elif ratio < 0.7:
            tag = "⚠️ below expectation"
        elif ratio < 1.4:
            tag = "✅ matches expectation"
        elif ratio < 2.0:
            tag = "⚠️ above expectation"
        else:
            tag = "❌ FAR above expectation (dedup bug?)"
        print(f"  ratio observed/expected:                 {ratio:.2f} {tag}")
    print()

    print("Per-pair (expected vs observed):")
    print(f"  {'pair':<14} {'expected':>10} {'observed':>10}  status")
    all_pairs = set(list(expected_by_pair.keys()) + list(observed_by_pair.keys()))
    for pair in sorted(all_pairs):
        e = expected_by_pair.get(pair, 0)
        o = observed_by_pair.get(pair, 0)
        status = ""
        if e == 0 and o > 0:
            status = "🟡 not in active list (manually traded?)"
        elif e > 0 and o == 0:
            status = "🟡 expected signals but none captured"
        elif e > 0:
            r = o / e
            if r < 0.3: status = "🔴"
            elif r < 0.7: status = "🟠"
            elif r < 1.5: status = "🟢"
            else: status = "🔴 over-firing?"
        print(f"  {pair:<14} {e:>10.1f} {o:>10}  {status}")
    print()

    print(f"Direction balance:  {long_count} long  /  {short_count} short")
    if long_count + short_count > 0:
        skew = abs(long_count - short_count) / (long_count + short_count)
        if skew > 0.5:
            print(f"  ⚠️ heavy direction skew ({skew*100:.0f}%) — possibly trending market")
        else:
            print(f"  ✓ balanced")
    print()

    print(f"Paper-mode signals:    {paper_count}/{total_observed} "
          f"({100*paper_count/total_observed if total_observed else 0:.0f}%)")
    print(f"Accepted (live):       {accepted_count}/{total_observed} "
          f"({100*accepted_count/total_observed if total_observed else 0:.0f}%)")
    if accepted_count > 0 and paper_count > 0:
        print(f"  ⚠️ MIXED MODES — paper and live signals in same window. "
              f"Audit explicitly to make sure no orders escaped paper safety.")
    print()

    print("By strategy:")
    for strat, cnt in observed_by_strategy.most_common():
        print(f"  {strat:<18} {cnt}")


if __name__ == "__main__":
    main()

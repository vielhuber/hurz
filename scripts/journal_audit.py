"""Audit the spot_trades journal — paper-mode session inspector.

Reads the spot_trades table and reports:
  - Total signal count, broken down by paper-mode vs live
  - Per-pair / per-strategy distribution
  - Direction balance (long vs short)
  - Acceptance rate
  - Recent signals chronologically

Usage:
    python3 scripts/journal_audit.py
    python3 scripts/journal_audit.py --since "2026-05-08 00:00:00"
    python3 scripts/journal_audit.py --since 24h
    python3 scripts/journal_audit.py --pair EURUSD
    python3 scripts/journal_audit.py --tail 20

`--since` accepts ISO timestamps OR a relative spec like `24h`, `7d`,
`90m`. Default is "all of today".
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.singletons import settings, database
settings.load_env()
database.init_connection()


_REL = re.compile(r"^(\d+)([smhdw])$")
_REL_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def _parse_since(spec: str) -> str:
    if not spec:
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    m = _REL.match(spec.strip().lower())
    if m:
        n = int(m.group(1))
        unit = _REL_UNIT[m.group(2)]
        delta = timedelta(**{unit: n})
        return (datetime.now() - delta).strftime("%Y-%m-%d %H:%M:%S")
    # Otherwise treat as ISO timestamp.
    return spec


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--since", default="",
                   help="ISO timestamp or relative (24h, 7d, ...)")
    p.add_argument("--pair", default=None)
    p.add_argument("--strategy", default=None)
    p.add_argument("--platform", default=None)
    p.add_argument("--tail", type=int, default=10,
                   help="show the last N signals chronologically (default 10)")
    args = p.parse_args()

    since = _parse_since(args.since)
    where = ["created_at >= %s"]
    params = [since]
    if args.pair:
        where.append("pair = %s"); params.append(args.pair)
    if args.strategy:
        where.append("strategy = %s"); params.append(args.strategy)
    if args.platform:
        where.append("platform = %s"); params.append(args.platform)
    where_clause = " AND ".join(where)

    # Aggregate stats
    total = database.select(
        f"SELECT COUNT(*) AS c FROM spot_trades WHERE {where_clause}",
        tuple(params),
    )[0]["c"]
    if total == 0:
        print(f"No journal entries since {since} matching the filters.")
        return

    paper = database.select(
        f"SELECT COUNT(*) AS c FROM spot_trades WHERE {where_clause} AND paper_mode=1",
        tuple(params),
    )[0]["c"]
    accepted = database.select(
        f"SELECT COUNT(*) AS c FROM spot_trades WHERE {where_clause} AND accepted=1",
        tuple(params),
    )[0]["c"]
    longs = database.select(
        f"SELECT COUNT(*) AS c FROM spot_trades WHERE {where_clause} AND direction=1",
        tuple(params),
    )[0]["c"]
    shorts = database.select(
        f"SELECT COUNT(*) AS c FROM spot_trades WHERE {where_clause} AND direction=-1",
        tuple(params),
    )[0]["c"]

    print(f"Journal audit since {since}")
    print(f"  filters: pair={args.pair} strategy={args.strategy} platform={args.platform}")
    print()
    print(f"  total signals:     {total}")
    print(f"  paper-mode:        {paper}  ({100*paper/total:.0f}%)")
    print(f"  live (accepted):   {accepted}  ({100*accepted/total:.0f}%)")
    print(f"  rejected:          {total - accepted}  ({100*(total-accepted)/total:.0f}%)")
    print(f"  direction split:   {longs} long  /  {shorts} short")
    print()

    by_pair = database.select(
        f"SELECT pair, COUNT(*) AS c FROM spot_trades WHERE {where_clause} "
        f"GROUP BY pair ORDER BY c DESC",
        tuple(params),
    )
    print(f"by pair ({len(by_pair)}):")
    for r in by_pair:
        print(f"  {r['pair']:<14} {r['c']}")
    print()

    by_strat = database.select(
        f"SELECT strategy, COUNT(*) AS c FROM spot_trades WHERE {where_clause} "
        f"GROUP BY strategy ORDER BY c DESC",
        tuple(params),
    )
    print(f"by strategy ({len(by_strat)}):")
    for r in by_strat:
        print(f"  {r['strategy']:<18} {r['c']}")
    print()

    if args.tail and args.tail > 0:
        recent = database.select(
            f"SELECT created_at, platform, pair, strategy, direction, "
            f"entry_price, stop_loss, take_profit, accepted, error "
            f"FROM spot_trades WHERE {where_clause} "
            f"ORDER BY id DESC LIMIT {int(args.tail)}",
            tuple(params),
        )
        print(f"last {len(recent)} signals (most recent first):")
        print(f"  {'time':<20} {'pair':<12} {'strat':<18} {'dir':>3} "
              f"{'entry':>10} {'sl':>10} {'tp':>10} {'status':<10}")
        for r in recent:
            status = "ACCEPTED" if r["accepted"] else "rejected"
            print(f"  {str(r['created_at']):<20} {r['pair']:<12} {r['strategy']:<18} "
                  f"{r['direction']:>+3} "
                  f"{float(r['entry_price']):>10.5f} {float(r['stop_loss']):>10.5f} "
                  f"{float(r['take_profit']):>10.5f} {status:<10}")


if __name__ == "__main__":
    main()

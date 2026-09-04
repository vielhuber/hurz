"""Forward counterfactual for the ADX regime router.

Section 46 of docs/EDGE_FINDINGS.md asks for a forward comparison the
backtests could not supply, and the standing rule is to keep the router
until an independent test reaches t < -2.0. This supplies it from live
data: every regime-vetoed signal is journaled with its full
entry/stop/target, so the bars that followed decide what it would have
returned. Compares that suppressed population against the signals the
router let through, both net of the round-trip spread.

Usage:
    python3 scripts/regime_counterfactual.py
    python3 scripts/regime_counterfactual.py --since 2026-08-24
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from statistics import mean, stdev
import math

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
os.environ["PAPER_TRADE_ONLY"] = "1"

from app.utils.singletons import settings  # noqa: E402
settings.load_env()
os.environ["PAPER_TRADE_ONLY"] = "1"

from app.platforms.registry import get_platform, clear_cache  # noqa: E402
from scripts.spot_backtest import _fee_for  # noqa: E402

MAX_HOLD = 24

# entry_adx has only been written since this date; earlier rows cannot
# say which side of the router they fell on.
_DEFAULT_SINCE = "2026-08-24"


def rows(since):
    c = sqlite3.connect("data/hurz.sqlite")
    c.row_factory = sqlite3.Row
    return list(c.execute("""
        select pair, strategy, direction, entry_price, stop_loss, take_profit,
               bar_time, accepted, entry_adx, error
        from spot_trades
        where platform='capital_com' and entry_adx is not null
          and created_at >= ?
        order by bar_time
    """, (since,)))


def resolve(bars, r):
    """Return the R-multiple the signal would have produced, net of cost."""
    start = datetime.strptime(r["bar_time"], "%Y-%m-%d %H:%M:%S")
    after = [b for b in bars if b.timestamp.replace(tzinfo=None) > start][:MAX_HOLD]
    if len(after) < 2:
        return None
    entry = float(r["entry_price"])
    sl = float(r["stop_loss"])
    tp = float(r["take_profit"])
    d = int(r["direction"])
    stop_d = abs(entry - sl)
    if stop_d <= 0:
        return None
    cost_r = 2.0 * _fee_for("capital_com", r["pair"]) * entry / stop_d
    for b in after:
        if d == 1:
            if b.low <= sl:
                return -1.0 - cost_r
            if b.high >= tp:
                return (tp - entry) / stop_d - cost_r
        else:
            if b.high >= sl:
                return -1.0 - cost_r
            if b.low <= tp:
                return (entry - tp) / stop_d - cost_r
    return (after[-1].close - entry) * d / stop_d - cost_r


def report(name, vals):
    if not vals:
        print(f"  {name:22} no resolvable signals")
        return None
    n = len(vals)
    mu = mean(vals)
    if n < 2:
        print(f"  {name:22} n={n:>4}  E[R]={mu:+.3f}")
        return mu
    sd = stdev(vals)
    t = mu / (sd / math.sqrt(n)) if sd > 0 else 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    wr = 100.0 * sum(1 for v in vals if v > 0) / n
    print(f"  {name:22} n={n:>4}  E[R]={mu:+.3f}  win={wr:>4.1f}%  "
          f"t={t:+.2f}  p={p:.3f}")
    return mu


async def main(args):
    data = rows(args.since)
    pairs = sorted({r["pair"] for r in data})
    print(f"journaled signals with ADX: {len(data)} over {len(pairs)} pairs")
    clear_cache()
    p = get_platform("capital_com")
    await p.connect()
    taken, vetoed = [], []
    veto_by_adx = {}
    try:
        for pair in pairs:
            try:
                bars = await p.fetch_history(
                    pair,
                    from_ts=datetime.now(timezone.utc) - timedelta(days=20),
                    to_ts=datetime.now(timezone.utc),
                    resolution="1h",
                )
            except Exception as exc:
                print(f"  {pair}: history failed: {str(exc)[:50]}")
                continue
            for r in [x for x in data if x["pair"] == pair]:
                got = resolve(bars, r)
                if got is None:
                    continue
                if r["accepted"]:
                    taken.append(got)
                elif "regime filter" in (r["error"] or ""):
                    vetoed.append(got)
                    bucket = "30+" if r["entry_adx"] >= 30 else (
                        "25-30" if r["entry_adx"] >= 25 else (
                            "20-25" if r["entry_adx"] >= 20 else "<20"))
                    veto_by_adx.setdefault(bucket, []).append(got)
            await asyncio.sleep(0.4)
    finally:
        await p.disconnect()

    print()
    print("=== router decision, replayed on actual bars (R net of spread) ===")
    report("taken (ADX>=30)", taken)
    report("vetoed (ADX<30)", vetoed)
    print()
    print("=== vetoed, split by ADX bucket ===")
    for b in ("<20", "20-25", "25-30", "30+"):
        if b in veto_by_adx:
            report(b, veto_by_adx[b])
    if taken and vetoed:
        print()
        d = mean(taken) - mean(vetoed)
        sp = math.sqrt(
            (stdev(taken) ** 2 / len(taken)) + (stdev(vetoed) ** 2 / len(vetoed))
        ) if len(taken) > 1 and len(vetoed) > 1 else 0
        print(f"difference taken - vetoed = {d:+.3f} R"
              + (f"   t = {d/sp:+.2f}" if sp > 0 else ""))


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--since", default=_DEFAULT_SINCE)
    return p.parse_args()


asyncio.run(main(_parse()))

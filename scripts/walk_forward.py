"""Walk-forward validation: protect against overfit / cherry-picked
strategies by checking that the edge persists OUT OF SAMPLE.

Splits each pair's data into N rolling segments and runs the
strategy on each. If the IS edge (in-sample expectancy) doesn't
hold OOS (out-of-sample) within tolerance, the strategy is
suspect — even if pooled stats look great.

Usage:
    python3 scripts/walk_forward.py --platform kraken --strategy rsi_mr
    python3 scripts/walk_forward.py --strategy multi_consensus --segments 4

Output: per-segment win-rate / E[R] / PF, plus a stability score
showing how consistent the edge is across segments.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform, Bar
from app.platforms.registry import clear_cache
from app.strategies import get_strategy, available_strategies, add_indicators


_DEFAULT_KRAKEN = ["XBTEUR", "ETHEUR", "ADAUSD", "LINKEUR", "DOTEUR"]
_DEFAULT_CAPITAL = ["BTCUSD", "ETHUSD", "ADAUSD", "EURUSD", "AUDUSD"]


def _bars_to_df(bars: List[Bar]) -> pd.DataFrame:
    return pd.DataFrame([{
        "timestamp": b.timestamp,
        "open": b.open, "high": b.high, "low": b.low, "close": b.close,
    } for b in bars])


def _simulate(df, signals, *, rr, stop_atr, max_hold):
    rs = []
    in_until = -1
    for sig in signals:
        i = sig.index
        if i <= in_until or i >= len(df):
            continue
        atr = df.iloc[i].get("atr_14")
        if atr is None or not np.isfinite(atr) or atr <= 0:
            continue
        entry = float(df.iloc[i]["close"])
        stop_d = stop_atr * atr; tp_d = rr * stop_d
        sl = entry - stop_d if sig.direction == 1 else entry + stop_d
        tp = entry + tp_d if sig.direction == 1 else entry - tp_d
        outcome = None
        for j in range(1, max_hold + 1):
            if i + j >= len(df):
                break
            h = df.iloc[i + j]["high"]; l = df.iloc[i + j]["low"]
            if sig.direction == 1:
                if l <= sl: outcome = "loss"; in_until = i + j; break
                if h >= tp: outcome = "win"; in_until = i + j; break
            else:
                if h >= sl: outcome = "loss"; in_until = i + j; break
                if l <= tp: outcome = "win"; in_until = i + j; break
        if outcome == "win":
            rs.append(rr)
        elif outcome == "loss":
            rs.append(-1.0)
        elif i + max_hold < len(df):
            cl = float(df.iloc[i + max_hold]["close"])
            pnl = (cl - entry) * sig.direction
            risk = abs(entry - sl)
            if risk > 0:
                rs.append(pnl / risk)
            in_until = i + max_hold
    if not rs:
        return None
    arr = np.asarray(rs)
    pos = arr[arr > 0].sum(); neg = -arr[arr < 0].sum()
    pf = (pos / neg) if neg > 0 else float("inf")
    return {
        "n": len(arr), "win_rate": float((arr > 0).mean()),
        "pf": pf, "E[R]": float(arr.mean()),
    }


async def main(args):
    clear_cache()
    p = get_platform(args.platform)
    await p.connect()
    pairs = args.pairs or (
        _DEFAULT_KRAKEN if args.platform == "kraken" else _DEFAULT_CAPITAL
    )
    strategy = get_strategy(args.strategy)
    print(f"Walk-forward: {p.name} × {args.strategy} × {len(pairs)} pairs × "
          f"{args.segments} segments × {args.days} days")
    print()

    try:
        for pair in pairs:
            try:
                bars = await p.fetch_history(
                    pair,
                    from_ts=datetime.now(timezone.utc) - timedelta(days=args.days),
                    to_ts=datetime.now(timezone.utc),
                    resolution=args.resolution,
                )
            except Exception as exc:
                print(f"{pair:<12} ⛔ {str(exc)[:50]}")
                await asyncio.sleep(0.6)
                continue
            if len(bars) < 200:
                print(f"{pair:<12} ⛔ too few bars ({len(bars)})")
                await asyncio.sleep(0.6)
                continue
            df = _bars_to_df(bars)
            df = add_indicators(df)
            n = len(df)
            seg_size = n // args.segments
            if seg_size < 60:
                print(f"{pair:<12} ⛔ segments too short")
                continue
            print(f"{pair:<12} (n={n})")
            seg_E = []
            for s in range(args.segments):
                lo = s * seg_size
                hi = (s + 1) * seg_size if s < args.segments - 1 else n
                seg_df = df.iloc[lo:hi].reset_index(drop=True)
                signals = strategy(seg_df, {})
                stats = _simulate(seg_df, signals,
                                  rr=args.rr, stop_atr=args.stop_atr,
                                  max_hold=args.max_hold)
                if stats is None:
                    print(f"  segment {s+1}: no trades")
                    continue
                pf_str = f"{stats['pf']:.2f}" if stats['pf'] != float("inf") else "∞"
                print(f"  segment {s+1}: n={stats['n']:>3} win={stats['win_rate']*100:>4.0f}% "
                      f"PF={pf_str:>5} E[R]={stats['E[R]']:>+5.2f}")
                seg_E.append(stats["E[R]"])
            if seg_E:
                pos_segs = sum(1 for e in seg_E if e > 0)
                stability = pos_segs / len(seg_E)
                tag = "✅" if stability >= 0.66 else ("⚠️" if stability >= 0.5 else "❌")
                print(f"  {tag} stability: {pos_segs}/{len(seg_E)} segments positive "
                      f"(mean E[R]={np.mean(seg_E):+.3f})")
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--platform", default="kraken",
                   choices=["kraken", "capital_com"])
    p.add_argument("--strategy", default="rsi_mr",
                   choices=available_strategies())
    p.add_argument("--pairs", nargs="+", default=None)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--resolution", default="1h")
    p.add_argument("--segments", type=int, default=3)
    p.add_argument("--rr", type=float, default=1.5)
    p.add_argument("--stop-atr", dest="stop_atr", type=float, default=1.0)
    p.add_argument("--max-hold", dest="max_hold", type=int, default=24)
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse()))

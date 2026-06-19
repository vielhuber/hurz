"""Spot-trading backtest harness — strategy × platform × resolution.

Pulls live OHLC from the configured platform (kraken / capital_com),
runs a strategy from `app.strategies.*` over it, simulates trades
with ATR-based stop-loss + take-profit, and reports:
  - per-pair stats (win%, profit-factor, expectancy in R-multiples)
  - aggregate stats
  - persistent JSON output for the pair-selector

Usage:
    python3 scripts/spot_backtest.py --platform kraken --strategy bollinger_rev
    python3 scripts/spot_backtest.py --platform capital_com --strategy momentum --pairs BTCUSD ETHUSD
    python3 scripts/spot_backtest.py --strategy multi_consensus --rr 1.5

The `--persist` flag writes results to data/spot_backtest_results.json
where the pair-selector picks them up. Default is on.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import numpy as np
import pandas as pd

# Project root on sys.path so `import app.*` works.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform, Bar
from app.platforms.registry import clear_cache
from app.strategies import get_strategy, available_strategies, add_indicators
from app.spot_trading.regime import decide as _regime_decide, adx_at as _regime_adx


_DEFAULT_KRAKEN_PAIRS = [
    "XBTEUR", "ETHEUR", "SOLEUR", "ADAEUR", "XDGEUR",
    "LTCEUR", "DOTEUR", "LINKEUR",
    "XBTUSD", "ETHUSD", "SOLUSD", "ADAUSD",
]
_DEFAULT_KRAKEN_FUTURES_PAIRS = [
    # PF_-perpetuals only — Kraken Futures has no EUR contracts.
    "XBTUSD", "ETHUSD", "SOLUSD", "ADAUSD", "DOGEUSD",
    "XRPUSD", "LTCUSD", "LINKUSD", "DOTUSD", "AVAXUSD",
    "ATOMUSD", "MATICUSD",
]
_DEFAULT_CAPITAL_PAIRS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "DOGEUSD", "XRPUSD",
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
]
_DEFAULT_RESOLUTION = "1h"
_DEFAULT_DAYS = 30
_DEFAULT_RR = 1.5
_DEFAULT_STOP_ATR = 1.0
_DEFAULT_MAX_HOLD_BARS = 24
_INTER_CALL_SLEEP_SEC = 0.6
_RESULTS_PATH = "data/spot_backtest_results.json"

# Asset-specific fee rates (per side). The naive per-platform fee
# overstates FX-major spreads (Capital EURUSD ≈ 0.6 pip ≈ 0.005%)
# and understates Capital crypto spreads (DOGE ≈ 0.1%). Use the
# resolver below to get a per-(platform, pair) rate.
#
# Sources:
#   Kraken Spot:    Maker 0.16-0.26% / Taker 0.26-0.40%. Pessimistic
#                   0.26% for the maker case (best a small account
#                   can realistically expect with limit orders).
#   Capital.com:    CFD spreads vary by instrument. Approximate
#                   half-spreads (per side) used here:
#                     FX majors:  0.005% (≈ 0.6 pip on EURUSD)
#                     FX minors:  0.010% (≈ 0.9 pip on GBPJPY)
#                     Crypto:     0.050% (≈ wider on alts like DOGE)
_CRYPTO_PREFIXES = {
    "BTC", "ETH", "SOL", "ADA", "DOGE", "XRP", "LTC", "MATIC", "AVAX",
    "LINK", "DOT", "UNI", "ATOM", "BCH", "TRX", "BNB",
    "XBT", "XDG",  # Kraken legacy codes (BTC, DOGE)
}
_FX_MAJORS = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
}


# Cache for the live-spread JSON written by capital_spread_audit.py.
# Loaded once per process. None means "tried, not found / unreadable".
_SPREADS_PATH = "data/capital_spreads.json"
_SPREADS_CACHE: Optional[dict] = None

# Same shape for the min-stop-distance audit file.
_MIN_DIST_PATH = "data/capital_min_distances.json"
_MIN_DIST_CACHE: Optional[dict] = None


def _load_min_dist_cache() -> dict:
    global _MIN_DIST_CACHE
    if _MIN_DIST_CACHE is not None:
        return _MIN_DIST_CACHE
    if not os.path.exists(_MIN_DIST_PATH):
        _MIN_DIST_CACHE = {}
        return _MIN_DIST_CACHE
    try:
        with open(_MIN_DIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _MIN_DIST_CACHE = data.get("pairs") or {}
    except (OSError, json.JSONDecodeError):
        _MIN_DIST_CACHE = {}
    return _MIN_DIST_CACHE


def _venue_min_distance(platform: str, pair: str, ref_price: float) -> float:
    """Compute the venue's minimum stop-distance for `pair` at `ref_price`.

    For Capital.com the rule is a PERCENTAGE of mid-price (typically 1%);
    we apply a 5% buffer matching the autotrader's runtime clamp. Returns
    0 when no rule is known — caller treats that as "no expansion needed".
    """
    if platform != "capital_com":
        return 0.0
    rules = _load_min_dist_cache()
    entry = rules.get(pair)
    if not entry:
        return 0.0
    unit = entry.get("min_dist_unit")
    value = entry.get("min_dist_value", 0)
    if unit != "PERCENTAGE" or not value:
        return 0.0
    return ref_price * float(value) * 1.05


def _load_spreads_cache() -> dict:
    global _SPREADS_CACHE
    if _SPREADS_CACHE is not None:
        return _SPREADS_CACHE
    if not os.path.exists(_SPREADS_PATH):
        _SPREADS_CACHE = {}
        return _SPREADS_CACHE
    try:
        with open(_SPREADS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _SPREADS_CACHE = data.get("pairs") or {}
    except (OSError, json.JSONDecodeError):
        _SPREADS_CACHE = {}
    return _SPREADS_CACHE


def _fee_for(platform: str, pair: str) -> float:
    """Resolve the per-side fee for a given platform / pair.

    Resolution order:
      1. Live-spread file from `capital_spread_audit.py` (most accurate)
      2. Asset-class default (FX major / FX minor / crypto)
      3. Per-platform default

    The live file is preferred because hardcoded defaults systematically
    understated Capital crypto spreads (defaulted to 0.05%, real value
    on ADAUSD was ~0.21%/side per overnight live-demo data)."""
    if platform == "capital_com":
        spreads = _load_spreads_cache()
        if pair in spreads:
            return float(spreads[pair].get("fee_per_side", 0.0))
        is_crypto = any(pair.startswith(prefix) for prefix in _CRYPTO_PREFIXES)
        if is_crypto:
            return 0.0005
        if pair in _FX_MAJORS:
            return 0.00005
        return 0.0001  # FX minors / commodities default
    if platform == "kraken":
        # Kraken is crypto-only at spot; same fee tier for everything.
        return 0.0026
    if platform == "kraken_futures":
        # Tier-0 taker on Kraken Futures (perpetuals). Maker is 0.02%
        # but our market-order flow always pays taker.
        return 0.0005
    return 0.0


# Used by argparse default-display only; the resolver above is
# authoritative for the actual run.
_FEE_DEFAULTS = {
    "kraken":         "0.260% (flat — crypto only)",
    "kraken_futures": "0.050% (Tier-0 taker — perpetuals)",
    "capital_com":    "0.005% FX major / 0.050% crypto / 0.010% other",
}


@dataclass
class TradeOutcome:
    asset: str
    direction: int
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    outcome: str
    bars_held: int
    r_multiple: float


def _bars_to_df(bars: List[Bar]) -> pd.DataFrame:
    return pd.DataFrame([{
        "timestamp": b.timestamp,
        "open": b.open, "high": b.high, "low": b.low, "close": b.close,
    } for b in bars])


def _simulate_trades(asset: str, df: pd.DataFrame, signals, *,
                     rr: float, stop_atr_mult: float,
                     max_hold_bars: int,
                     fee_rate: float = 0.0,
                     platform: str = "",
                     strategy_name: str = "") -> List[TradeOutcome]:
    """Walk forward from each signal until SL or TP hits (or timeout).
    Conservative: when a single bar's range covers both SL and TP,
    assume SL hit first.

    `fee_rate` is per-side (e.g. Kraken Maker 0.0026). Round-trip cost
    of `2 * fee_rate * entry` is subtracted from raw PnL before the
    R-multiple is computed — this matches the real net result the
    operator would see on the venue.

    `platform` enables the adaptive stop-expansion: when the venue's
    minimum-stop-distance rule (Capital.com's 1% PERCENTAGE) exceeds
    the ATR-derived stop, expand to the venue minimum and stretch TP
    proportionally. Keeps R:R constant. Matches what the autotrader
    does live, so backtest stays an honest model of fills."""
    outcomes: List[TradeOutcome] = []
    in_trade_until = -1
    for sig in signals:
        i = sig.index
        if i <= in_trade_until or i >= len(df):
            continue
        # Regime filter — same policy as the live trader, so persisted
        # edge-stats reflect what would actually be traded.
        if strategy_name:
            if _regime_decide(strategy_name, _regime_adx(df, i)).blocked:
                continue
        row = df.iloc[i]
        atr = row.get("atr_14")
        if atr is None or not np.isfinite(atr) or atr <= 0:
            continue
        entry = float(row["close"])
        stop_dist = stop_atr_mult * atr
        # Adaptive stop expansion. The venue may refuse SL/TP closer
        # than X% — in that case the autotrader expands stop_dist to
        # the venue minimum; the backtest must mirror that to remain
        # a credible model of live fills.
        venue_min = _venue_min_distance(platform, asset, entry)
        if venue_min > 0 and stop_dist < venue_min:
            stop_dist = venue_min
        target_dist = rr * stop_dist
        if sig.direction == +1:
            sl = entry - stop_dist; tp = entry + target_dist
        else:
            sl = entry + stop_dist; tp = entry - target_dist
        out_t: Optional[datetime] = None
        out_p: Optional[float] = None
        outcome_type = "open"
        bars_held = 0
        for j in range(1, max_hold_bars + 1):
            if i + j >= len(df):
                break
            f = df.iloc[i + j]
            high = f["high"]; low = f["low"]
            if sig.direction == +1:
                hit_sl = low <= sl
                hit_tp = high >= tp
            else:
                hit_sl = high >= sl
                hit_tp = low <= tp
            if hit_sl and hit_tp:
                out_t = f["timestamp"]; out_p = sl
                outcome_type = "loss"; bars_held = j; break
            if hit_sl:
                out_t = f["timestamp"]; out_p = sl
                outcome_type = "loss"; bars_held = j; break
            if hit_tp:
                out_t = f["timestamp"]; out_p = tp
                outcome_type = "win"; bars_held = j; break
        else:
            j = max_hold_bars
            if i + j < len(df):
                f = df.iloc[i + j]
                out_t = f["timestamp"]; out_p = float(f["close"])
                outcome_type = "timeout"; bars_held = j
        if outcome_type == "open" or out_p is None or out_t is None:
            continue
        risk = abs(entry - sl)
        if risk == 0:
            continue
        gross_pnl = (out_p - entry) * sig.direction
        # Round-trip fee: paid at entry on `entry` and at exit on `out_p`.
        fee_cost = fee_rate * (entry + out_p) if fee_rate > 0 else 0.0
        net_pnl = gross_pnl - fee_cost
        r = net_pnl / risk
        outcomes.append(TradeOutcome(
            asset=asset, direction=sig.direction,
            entry_time=row["timestamp"], entry_price=entry,
            stop_loss=sl, take_profit=tp,
            exit_time=out_t, exit_price=out_p,
            outcome=outcome_type, bars_held=bars_held,
            r_multiple=float(r),
        ))
        in_trade_until = i + bars_held
    return outcomes


def _summarise(outcomes: List[TradeOutcome]) -> dict:
    if not outcomes:
        return {"n": 0, "wins": 0, "losses": 0, "timeouts": 0,
                "win_rate": 0.0, "profit_factor": 0.0,
                "expectancy_R": 0.0, "sharpe": 0.0,
                "best_R": 0.0, "worst_R": 0.0,
                "median_stop_distance": 0.0,
                "median_entry_price": 0.0}
    rs = np.array([o.r_multiple for o in outcomes], dtype=float)
    # Stop distance + entry price stats — used by the pair-selector's
    # pre-filter to drop combos whose ATR-derived stops are typically
    # tighter than the venue's minimum-stop-distance rule.
    stop_dists = np.array([abs(o.entry_price - o.stop_loss) for o in outcomes], dtype=float)
    entries = np.array([o.entry_price for o in outcomes], dtype=float)
    wins = sum(1 for o in outcomes if o.outcome == "win")
    losses = sum(1 for o in outcomes if o.outcome == "loss")
    timeouts = sum(1 for o in outcomes if o.outcome == "timeout")
    pos = rs[rs > 0].sum(); neg = -rs[rs < 0].sum()
    pf = (pos / neg) if neg > 0 else float("inf")
    return {
        "n": len(rs), "wins": wins, "losses": losses, "timeouts": timeouts,
        "win_rate": wins / len(rs),
        "profit_factor": pf,
        "expectancy_R": float(rs.mean()),
        "sharpe": float(rs.mean() / rs.std()) if rs.std() > 0 else 0.0,
        "best_R": float(rs.max()), "worst_R": float(rs.min()),
        "median_stop_distance": float(np.median(stop_dists)),
        "median_entry_price": float(np.median(entries)),
    }


async def _fetch(platform, pair, resolution, days):
    to_ts = datetime.now(timezone.utc)
    from_ts = to_ts - timedelta(days=days)
    return await platform.fetch_history(
        pair, from_ts=from_ts, to_ts=to_ts, resolution=resolution,
    )


def _load_persisted_results() -> dict:
    if not os.path.exists(_RESULTS_PATH):
        return {}
    try:
        with open(_RESULTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _persist_result(platform_name: str, strategy_name: str, resolution: str,
                    days: int, rr: float, stop_atr: float, max_hold: int,
                    fee_rate,
                    per_pair: list, overall: dict) -> None:
    """Append/update results in data/spot_backtest_results.json. Schema:
        {
          "<platform>::<strategy>::<resolution>": {
            "params": {...},
            "generated_at": "...",
            "overall": {...},
            "pairs": {pair: stats}
          },
          ...
        }
    """
    os.makedirs(os.path.dirname(_RESULTS_PATH), exist_ok=True)
    data = _load_persisted_results()
    key = f"{platform_name}::{strategy_name}::{resolution}"
    data[key] = {
        "platform": platform_name,
        "strategy": strategy_name,
        "resolution": resolution,
        "params": {
            "days": days, "rr": rr, "stop_atr": stop_atr,
            "max_hold": max_hold, "fee_rate": fee_rate,
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall": overall,
        "pairs": {p["pair"]: {k: v for k, v in p.items() if k != "pair"} for p in per_pair},
    }
    with open(_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


async def main(args) -> None:
    clear_cache()
    platform = get_platform(args.platform)
    strategy = get_strategy(args.strategy)
    await platform.connect()
    pairs = args.pairs or (
        _DEFAULT_KRAKEN_PAIRS if args.platform == "kraken"
        else _DEFAULT_KRAKEN_FUTURES_PAIRS if args.platform == "kraken_futures"
        else _DEFAULT_CAPITAL_PAIRS
    )
    fee_override = args.fee_rate  # if set, applies uniformly to all pairs

    print(f"{platform.name} × {args.strategy} — backtest on {len(pairs)} pairs")
    if fee_override is not None:
        fee_label = f"{fee_override*100:.3f}%/side (override)"
    else:
        fee_label = _FEE_DEFAULTS.get(args.platform, "(none)")
    print(f"  resolution={args.resolution}  days={args.days}  "
          f"stop={args.stop_atr}×ATR  rr=1:{args.rr}  max_hold={args.max_hold} bars  "
          f"fee={fee_label}")
    print()
    print(f"{'pair':<14} {'bars':>5} {'trades':>7} {'win%':>6} {'PF':>6} "
          f"{'E[R]':>7} {'Sharpe':>7} {'best':>7} {'worst':>7}")
    print("-" * 82)

    per_pair_summary = []
    all_outcomes: List[TradeOutcome] = []
    try:
        for pair in pairs:
            try:
                bars = await _fetch(platform, pair, args.resolution, args.days)
            except Exception as exc:
                print(f"{pair:<14} ⛔ fetch failed: {str(exc)[:50]}")
                await asyncio.sleep(_INTER_CALL_SLEEP_SEC)
                continue
            if len(bars) < 50:
                print(f"{pair:<14} ⛔ too few bars ({len(bars)})")
                await asyncio.sleep(_INTER_CALL_SLEEP_SEC)
                continue
            df = _bars_to_df(bars)
            df = add_indicators(df)
            signals = strategy(df, {})
            pair_fee = (fee_override if fee_override is not None
                        else _fee_for(args.platform, pair))
            outcomes = _simulate_trades(
                pair, df, signals,
                rr=args.rr, stop_atr_mult=args.stop_atr,
                max_hold_bars=args.max_hold,
                fee_rate=pair_fee,
                platform=args.platform,
                strategy_name=args.strategy,
            )
            all_outcomes.extend(outcomes)
            stats = _summarise(outcomes)
            stats["pair"] = pair
            # Walk-forward stability: split this pair's bar history into
            # consecutive segments and check whether the per-trade edge
            # holds in each one. Pooled stats can paper over regime
            # overfit (e.g. a 30-day BTC down-trend that won't repeat);
            # the per-segment ratio surfaces it.
            # `compute_segment_stability` returns None when history is
            # too short — we just leave the field off in that case so
            # the pair_selector's filter can treat it as "unknown" and
            # fall back to the pooled metrics.
            from app.spot_trading.walk_forward import (
                compute_segment_stability as _wf_stability,
            )
            stability = _wf_stability(
                df, strategy,
                segments=3, rr=args.rr, stop_atr=args.stop_atr,
                max_hold=args.max_hold,
            )
            if stability is not None:
                stats["segment_stability"] = stability.as_dict()
            per_pair_summary.append(stats)
            pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float('inf') else "∞"
            print(f"{pair:<14} {len(bars):>5} {stats['n']:>7} "
                  f"{stats['win_rate']*100:>5.1f}% {pf_str:>6} "
                  f"{stats['expectancy_R']:>+7.3f} {stats['sharpe']:>+7.3f} "
                  f"{stats['best_R']:>+7.2f} {stats['worst_R']:>+7.2f}")
            await asyncio.sleep(_INTER_CALL_SLEEP_SEC)
    finally:
        await platform.disconnect()

    print("-" * 82)
    overall = _summarise(all_outcomes)
    pf_str = f"{overall['profit_factor']:.2f}" if overall['profit_factor'] != float('inf') else "∞"
    print(f"{'OVERALL':<14} {'':>5} {overall['n']:>7} "
          f"{overall['win_rate']*100:>5.1f}% {pf_str:>6} "
          f"{overall['expectancy_R']:>+7.3f} {overall['sharpe']:>+7.3f} "
          f"{overall['best_R']:>+7.2f} {overall['worst_R']:>+7.2f}")
    print()

    if args.persist:
        # Stash either the explicit override or the marker for "per-pair
        # rate resolved by _fee_for()". Both are useful when reading the
        # results back later: an override is reproducible exactly, a
        # marker tells the reader to look at the resolver function.
        fee_meta = (fee_override if fee_override is not None
                    else "per-pair (see _fee_for in spot_backtest.py)")
        _persist_result(
            args.platform, args.strategy, args.resolution,
            args.days, args.rr, args.stop_atr, args.max_hold,
            fee_meta, per_pair_summary, overall,
        )
        print(f"✓ results persisted to {_RESULTS_PATH}")

    if overall["n"] > 0:
        if overall["expectancy_R"] > 0.05 and overall["profit_factor"] > 1.2:
            print("✅ Edge present.")
        elif overall["expectancy_R"] > 0:
            print("⚠️  Marginal edge.")
        else:
            print("❌ No edge.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--platform", default="kraken",
                   choices=["kraken", "kraken_futures", "capital_com"])
    p.add_argument("--strategy", default="bollinger_rev",
                   choices=available_strategies())
    p.add_argument("--pairs", nargs="+", default=None,
                   help="default: platform-specific basket")
    p.add_argument("--resolution", default=_DEFAULT_RESOLUTION,
                   choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d"])
    p.add_argument("--days", type=int, default=_DEFAULT_DAYS)
    p.add_argument("--rr", type=float, default=_DEFAULT_RR)
    p.add_argument("--stop-atr", dest="stop_atr", type=float,
                   default=_DEFAULT_STOP_ATR)
    p.add_argument("--max-hold", dest="max_hold", type=int,
                   default=_DEFAULT_MAX_HOLD_BARS)
    p.add_argument("--fee-rate", dest="fee_rate", type=float, default=None,
                   help="per-side fee (e.g. 0.0026 for Kraken Maker). "
                        "Default: per-platform sensible value.")
    p.add_argument("--persist", action="store_true", default=True,
                   help="write results to data/spot_backtest_results.json (default on)")
    p.add_argument("--no-persist", dest="persist", action="store_false")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse_args()))

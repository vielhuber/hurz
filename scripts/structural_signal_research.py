"""Preregistered development/holdout research for structural signals.

The command has deliberately separate phases:

    python3 scripts/structural_signal_research.py download ...
    python3 scripts/structural_signal_research.py development
    python3 scripts/structural_signal_research.py holdout

Development reads only the development files. Holdout evaluates only the four
family champions frozen by development and verifies that the preregistration
digest has not changed.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.platforms import OrderConstraints, PlatformAPIError, PlatformAuthError
from app.platforms.registry import clear_cache, get_platform
from app.spot_trading.position_sizing import (
    DEFAULT_NOTIONAL_CAP_USD,
    DEFAULT_TARGET_RISK_USD,
    MAX_ROUND_TRIP_COST_RISK_FRACTION,
    calculate_position_size,
)


HISTORY_START = pd.Timestamp("2023-11-30T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-02-01T00:00:00Z")
HISTORY_END = pd.Timestamp("2026-08-24T00:00:00Z")
WARMUP_DAYS = 90
PREREGISTRATION_PATH = Path("docs/STRUCTURAL_SIGNAL_PREREGISTRATION.md")
DEFAULT_DATA_DIR = Path("tmp/structural_signal_history")
UNIVERSE = (
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF",
    "BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "DOGEUSD", "XRPUSD",
    "DOTUSD", "AAVEUSD",
)
FX_BASKET = UNIVERSE[:6]
CRYPTO_BASKET = UNIVERSE[6:]
VARIANTS = (
    "mtf_break20_4h_ema20",
    "mtf_break55_4h_ema20",
    "mtf_break20_1d_ema20",
    "mtf_break55_1d_ema20",
    "vol_atr_q25_break20",
    "vol_atr_q25_break55",
    "vol_atr_q75_break20",
    "vol_atr_q75_break55",
    "vol_squeeze_q10_break20",
    "vol_squeeze_q20_break20",
    "lead_btc_1h_z075",
    "lead_btc_1h_z125",
    "lead_btc_4h_z075",
    "lead_btc_4h_z125",
    "relative_24h_rebalance12h",
    "relative_24h_rebalance24h",
    "relative_72h_rebalance12h",
    "relative_72h_rebalance24h",
)
FAMILY_BY_PREFIX = {
    "mtf_": "multi_timeframe",
    "vol_": "volatility_regime",
    "lead_": "cross_asset_leadership",
    "relative_": "relative_strength",
}


@dataclass(frozen=True)
class SignalIntent:
    pair: str
    timestamp: pd.Timestamp
    direction: int


@dataclass(frozen=True)
class ResearchTrade:
    pair: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    r_multiple: float
    realized_pnl: float


@dataclass(frozen=True)
class ExecutionConfig:
    stop_atr_multiple: float = 1.0
    venue_min_fraction: float = 0.0105
    risk_reward: float = 1.5
    max_hold_bars: int = 24
    target_risk_usd: float = DEFAULT_TARGET_RISK_USD
    notional_cap_usd: float = DEFAULT_NOTIONAL_CAP_USD
    max_cost_risk_fraction: float = MAX_ROUND_TRIP_COST_RISK_FRACTION


@dataclass(frozen=True)
class SimulationResult:
    trades: List[ResearchTrade]
    skipped_cost: int = 0
    skipped_sizing: int = 0


def _family_for(variant: str) -> str:
    for prefix, family in FAMILY_BY_PREFIX.items():
        if variant.startswith(prefix):
            return family
    raise ValueError(f"Unknown variant family: {variant}")


def _with_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    previous_close = df["close"].shift(1)
    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    if "atr_14" not in df:
        df["atr_14"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    mean = df["close"].rolling(20, min_periods=20).mean()
    standard_deviation = df["close"].rolling(20, min_periods=20).std(ddof=0)
    df["bollinger_width"] = (4 * standard_deviation / mean).replace([np.inf, -np.inf], np.nan)
    return df


def _breakout_directions(df: pd.DataFrame, lookback: int) -> pd.Series:
    prior_high = df["high"].shift(1).rolling(lookback, min_periods=lookback).max()
    prior_low = df["low"].shift(1).rolling(lookback, min_periods=lookback).min()
    directions = pd.Series(0, index=df.index, dtype=int)
    directions.loc[df["close"] > prior_high] = 1
    directions.loc[df["close"] < prior_low] = -1
    return directions


def generate_multi_timeframe_signals(
    history: Dict[str, pd.DataFrame],
    *,
    breakout_lookback: int,
    trend_timeframe: str,
    trend_ema: int,
) -> List[SignalIntent]:
    signals: List[SignalIntent] = []
    for pair, raw_frame in history.items():
        df = _with_indicators(raw_frame)
        indexed_close = df.set_index("timestamp")["close"]
        resample_rule = "1D" if trend_timeframe == "1d" else trend_timeframe
        completed_close = indexed_close.resample(
            resample_rule,
            label="right",
            closed="left",
        ).last().dropna()
        ema = completed_close.ewm(span=trend_ema, adjust=False, min_periods=trend_ema).mean()
        higher_direction = pd.Series(0, index=completed_close.index, dtype=int)
        higher_direction.loc[(completed_close > ema) & (ema.diff() > 0)] = 1
        higher_direction.loc[(completed_close < ema) & (ema.diff() < 0)] = -1
        aligned_direction = higher_direction.reindex(
            pd.DatetimeIndex(df["timestamp"]),
            method="ffill",
        ).fillna(0).astype(int).to_numpy()
        breakout = _breakout_directions(df, breakout_lookback).to_numpy()
        for index in np.flatnonzero((breakout != 0) & (breakout == aligned_direction)):
            signals.append(SignalIntent(
                pair=pair,
                timestamp=df.iloc[index]["timestamp"],
                direction=int(breakout[index]),
            ))
    return sorted(signals, key=lambda signal: (signal.timestamp, signal.pair))


def generate_volatility_signals(
    history: Dict[str, pd.DataFrame],
    *,
    breakout_lookback: int,
    mode: str,
    quantile: float,
) -> List[SignalIntent]:
    signals: List[SignalIntent] = []
    for pair, raw_frame in history.items():
        df = _with_indicators(raw_frame)
        breakout = _breakout_directions(df, breakout_lookback)
        if mode == "atr_low":
            threshold = df["atr_14"].shift(1).rolling(252, min_periods=252).quantile(quantile)
            accepted = df["atr_14"] <= threshold
        elif mode == "atr_high":
            threshold = df["atr_14"].shift(1).rolling(252, min_periods=252).quantile(quantile)
            accepted = df["atr_14"] >= threshold
        elif mode == "squeeze":
            threshold = df["bollinger_width"].shift(13).rolling(
                252,
                min_periods=252,
            ).quantile(quantile)
            recent_width = df["bollinger_width"].shift(1).rolling(12, min_periods=12).min()
            accepted = recent_width <= threshold
        else:
            raise ValueError(f"Unknown volatility mode: {mode}")
        for index in df.index[(breakout != 0) & accepted.fillna(False)]:
            signals.append(SignalIntent(
                pair=pair,
                timestamp=df.iloc[index]["timestamp"],
                direction=int(breakout.iloc[index]),
            ))
    return sorted(signals, key=lambda signal: (signal.timestamp, signal.pair))


def generate_cross_asset_signals(
    history: Dict[str, pd.DataFrame],
    *,
    return_hours: int,
    threshold: float,
) -> List[SignalIntent]:
    leader = _with_indicators(history["BTCUSD"]).set_index("timestamp")["close"]
    leader_return = leader.pct_change(return_hours)
    leader_volatility = leader_return.shift(1).rolling(252, min_periods=252).std(ddof=1)
    leader_z = (leader_return.shift(1) / leader_volatility).replace([np.inf, -np.inf], np.nan)
    signals: List[SignalIntent] = []
    for pair in CRYPTO_BASKET:
        if pair == "BTCUSD" or pair not in history:
            continue
        target_frame = _with_indicators(history[pair])
        target = target_frame.set_index("timestamp")["close"]
        target_return = target.pct_change(return_hours)
        target_volatility = target_return.shift(1).rolling(252, min_periods=252).std(ddof=1)
        target_z = (target_return.shift(1) / target_volatility).replace([np.inf, -np.inf], np.nan)
        aligned = pd.concat(
            {"leader": leader_z, "target": target_z},
            axis=1,
            sort=True,
        ).reindex(target.index)
        accepted = (
            aligned["leader"].abs().ge(threshold)
            & aligned["target"].abs().lt(aligned["leader"].abs() * 0.5)
        )
        for timestamp, row in aligned.loc[accepted].iterrows():
            signals.append(SignalIntent(
                pair=pair,
                timestamp=timestamp,
                direction=1 if row["leader"] > 0 else -1,
            ))
    return sorted(signals, key=lambda signal: (signal.timestamp, signal.pair))


def generate_relative_strength_signals(
    history: Dict[str, pd.DataFrame],
    *,
    lookback_hours: int,
    rebalance_hours: int,
) -> List[SignalIntent]:
    returns = {}
    for pair, raw_frame in history.items():
        df = _with_indicators(raw_frame)
        returns[pair] = df.set_index("timestamp")["close"].pct_change(lookback_hours)
    signals: List[SignalIntent] = []
    for basket in (FX_BASKET, CRYPTO_BASKET):
        basket_returns = pd.concat(
            {pair: returns[pair] for pair in basket if pair in returns},
            axis=1,
            sort=True,
        ).dropna(how="all")
        if basket_returns.shape[1] < 3:
            continue
        rebalance_rows = basket_returns.loc[
            (basket_returns.index.minute == 0)
            & (basket_returns.index.hour % rebalance_hours == 0)
        ]
        for timestamp, row in rebalance_rows.iterrows():
            ranked = row.dropna().sort_values()
            if len(ranked) < 3:
                continue
            signals.append(SignalIntent(pair=ranked.index[-1], timestamp=timestamp, direction=1))
            signals.append(SignalIntent(pair=ranked.index[0], timestamp=timestamp, direction=-1))
    return sorted(signals, key=lambda signal: (signal.timestamp, signal.pair))


def generate_variant_signals(
    variant: str,
    history: Dict[str, pd.DataFrame],
) -> List[SignalIntent]:
    if variant.startswith("mtf_"):
        parts = variant.split("_")
        return generate_multi_timeframe_signals(
            history,
            breakout_lookback=int(parts[1].replace("break", "")),
            trend_timeframe=parts[2],
            trend_ema=int(parts[3].replace("ema", "")),
        )
    if variant.startswith("vol_atr_"):
        parts = variant.split("_")
        quantile_number = int(parts[2].replace("q", ""))
        return generate_volatility_signals(
            history,
            breakout_lookback=int(parts[3].replace("break", "")),
            mode="atr_low" if quantile_number < 50 else "atr_high",
            quantile=quantile_number / 100,
        )
    if variant.startswith("vol_squeeze_"):
        parts = variant.split("_")
        return generate_volatility_signals(
            history,
            breakout_lookback=int(parts[3].replace("break", "")),
            mode="squeeze",
            quantile=int(parts[2].replace("q", "")) / 100,
        )
    if variant.startswith("lead_btc_"):
        parts = variant.split("_")
        return generate_cross_asset_signals(
            history,
            return_hours=int(parts[2].replace("h", "")),
            threshold=int(parts[3].replace("z", "")) / 100,
        )
    if variant.startswith("relative_"):
        parts = variant.split("_")
        return generate_relative_strength_signals(
            history,
            lookback_hours=int(parts[1].replace("h", "")),
            rebalance_hours=int(parts[2].replace("rebalance", "").replace("h", "")),
        )
    raise ValueError(f"Unknown variant: {variant}")


def simulate_signals(
    history: Dict[str, pd.DataFrame],
    signals: Iterable[SignalIntent],
    *,
    spread_fractions: Dict[str, float],
    constraints: Dict[str, OrderConstraints],
    config: ExecutionConfig = ExecutionConfig(),
) -> SimulationResult:
    prepared_history = {pair: _with_indicators(frame) for pair, frame in history.items()}
    signals_by_pair: Dict[str, List[SignalIntent]] = {}
    for signal in signals:
        signals_by_pair.setdefault(signal.pair, []).append(signal)
    trades: List[ResearchTrade] = []
    skipped_cost = 0
    skipped_sizing = 0
    for pair, pair_signals in signals_by_pair.items():
        if pair not in prepared_history:
            continue
        df = prepared_history[pair]
        index_by_time = {timestamp: index for index, timestamp in enumerate(df["timestamp"])}
        in_trade_until = -1
        for signal in sorted(pair_signals, key=lambda item: item.timestamp):
            index = index_by_time.get(signal.timestamp)
            if index is None or index <= in_trade_until:
                continue
            row = df.iloc[index]
            atr = float(row["atr_14"])
            if not math.isfinite(atr) or atr <= 0:
                continue
            entry = float(row["close"])
            stop_distance = max(
                config.stop_atr_multiple * atr,
                config.venue_min_fraction * entry,
            )
            spread_cost_per_unit = spread_fractions[pair] * entry
            cost_risk_fraction = spread_cost_per_unit / stop_distance
            if cost_risk_fraction > config.max_cost_risk_fraction:
                skipped_cost += 1
                continue
            stop = entry - stop_distance if signal.direction == 1 else entry + stop_distance
            target_distance = config.risk_reward * stop_distance
            target = entry + target_distance if signal.direction == 1 else entry - target_distance
            venue_constraints = constraints[pair]
            sizing = calculate_position_size(
                entry_price=entry,
                stop_loss=stop,
                target_risk=config.target_risk_usd,
                notional_cap=config.notional_cap_usd,
                min_size=venue_constraints.min_size,
                size_increment=venue_constraints.size_increment,
                max_size=venue_constraints.max_size,
            )
            if sizing.skipped:
                skipped_sizing += 1
                continue
            exit_index: Optional[int] = None
            exit_price: Optional[float] = None
            for future_index in range(index + 1, min(index + config.max_hold_bars + 1, len(df))):
                future = df.iloc[future_index]
                if signal.direction == 1:
                    stop_hit = future["low"] <= stop
                    target_hit = future["high"] >= target
                else:
                    stop_hit = future["high"] >= stop
                    target_hit = future["low"] <= target
                if stop_hit:
                    exit_index = future_index
                    exit_price = stop
                    break
                if target_hit:
                    exit_index = future_index
                    exit_price = target
                    break
            if exit_price is None and index + config.max_hold_bars < len(df):
                exit_index = index + config.max_hold_bars
                exit_price = float(df.iloc[exit_index]["close"])
            if exit_price is None or exit_index is None:
                continue
            gross_per_unit = (exit_price - entry) * signal.direction
            net_per_unit = gross_per_unit - spread_cost_per_unit
            realized_pnl = net_per_unit * sizing.size
            trades.append(ResearchTrade(
                pair=pair,
                entry_time=row["timestamp"],
                exit_time=df.iloc[exit_index]["timestamp"],
                direction=signal.direction,
                r_multiple=realized_pnl / sizing.planned_risk,
                realized_pnl=realized_pnl,
            ))
            in_trade_until = exit_index
    trades.sort(key=lambda trade: (trade.entry_time, trade.pair))
    return SimulationResult(
        trades=trades,
        skipped_cost=skipped_cost,
        skipped_sizing=skipped_sizing,
    )


def clustered_expectancy(
    trades: Iterable[ResearchTrade],
    *,
    family_tests: int,
    alpha: float = 0.05,
) -> dict:
    trade_list = list(trades)
    if not trade_list:
        return {
            "n": 0,
            "week_clusters": 0,
            "expectancy_r": 0.0,
            "standard_error_r": None,
            "corrected_lower_bound_r": None,
            "total_pnl_usd": 0.0,
        }
    values = np.asarray([trade.r_multiple for trade in trade_list], dtype=float)
    mean = float(values.mean())
    cluster_values: Dict[tuple, List[float]] = {}
    for trade in trade_list:
        timestamp = pd.Timestamp(trade.entry_time)
        calendar = timestamp.isocalendar()
        cluster_values.setdefault((int(calendar.year), int(calendar.week)), []).append(
            trade.r_multiple
        )
    cluster_count = len(cluster_values)
    standard_error: Optional[float] = None
    lower_bound: Optional[float] = None
    if cluster_count >= 2:
        influences = np.asarray([
            sum(cluster) - mean * len(cluster)
            for cluster in cluster_values.values()
        ])
        standard_error = float(
            math.sqrt(cluster_count / (cluster_count - 1) * np.square(influences).sum())
            / len(values)
        )
        critical = float(stats.t.ppf(1 - alpha / family_tests, df=cluster_count - 1))
        lower_bound = mean - critical * standard_error
    return {
        "n": len(trade_list),
        "week_clusters": cluster_count,
        "expectancy_r": mean,
        "standard_error_r": standard_error,
        "corrected_lower_bound_r": lower_bound,
        "total_pnl_usd": float(sum(trade.realized_pnl for trade in trade_list)),
    }


def _evaluate_variant(
    variant: str,
    history: Dict[str, pd.DataFrame],
    *,
    spread_fractions: Dict[str, float],
    constraints: Dict[str, OrderConstraints],
    entry_start: pd.Timestamp,
    entry_end: pd.Timestamp,
    family_tests: int,
) -> dict:
    signals = [
        signal for signal in generate_variant_signals(variant, history)
        if entry_start <= signal.timestamp < entry_end
    ]
    simulation = simulate_signals(
        history,
        signals,
        spread_fractions=spread_fractions,
        constraints=constraints,
    )
    statistics = clustered_expectancy(simulation.trades, family_tests=family_tests)
    boundaries = pd.date_range(entry_start, entry_end, periods=6)
    segment_expectancies = []
    for segment_index in range(5):
        segment_trades = [
            trade for trade in simulation.trades
            if boundaries[segment_index] <= trade.entry_time < boundaries[segment_index + 1]
        ]
        segment_expectancies.append(
            float(np.mean([trade.r_multiple for trade in segment_trades]))
            if segment_trades else None
        )
    positive_segments = sum(
        expectancy is not None and expectancy > 0
        for expectancy in segment_expectancies
    )
    lower_bound = statistics["corrected_lower_bound_r"]
    passed = (
        statistics["n"] >= 50
        and statistics["expectancy_r"] > 0
        and statistics["total_pnl_usd"] > 0
        and positive_segments >= 4
        and lower_bound is not None
        and lower_bound > 0
    )
    return {
        "variant": variant,
        "family": _family_for(variant),
        **statistics,
        "positive_segments": positive_segments,
        "stability_ratio": positive_segments / 5,
        "segment_expectancies_r": segment_expectancies,
        "signals": len(signals),
        "skipped_cost": simulation.skipped_cost,
        "skipped_sizing": simulation.skipped_sizing,
        "passed": passed,
    }


def _preregistration_digest() -> str:
    return hashlib.sha256(PREREGISTRATION_PATH.read_bytes()).hexdigest()


def _load_research_inputs(data_dir: Path, phase: str):
    metadata = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
    history = {}
    for pair in UNIVERSE:
        path = data_dir / f"{pair}.{phase}.csv.gz"
        history[pair] = pd.read_csv(path, parse_dates=["timestamp"])
    constraints = {
        pair: OrderConstraints(**values)
        for pair, values in metadata["constraints"].items()
    }
    return history, metadata["spread_fractions"], constraints, metadata


def _print_results(results: List[dict]) -> None:
    print(
        f"{'variant':<34} {'n':>5} {'E[R]':>8} {'PnL $':>9} "
        f"{'stable':>7} {'LCB':>8} {'cost-skip':>9} {'pass':>5}"
    )
    print("-" * 100)
    for result in results:
        lower_bound = result["corrected_lower_bound_r"]
        lower_label = "n/a" if lower_bound is None else f"{lower_bound:+.3f}"
        print(
            f"{result['variant']:<34} {result['n']:>5} "
            f"{result['expectancy_r']:>+8.3f} {result['total_pnl_usd']:>+9.2f} "
            f"{result['positive_segments']}/5{'':>4} {lower_label:>8} "
            f"{result['skipped_cost']:>9} {str(result['passed']):>5}"
        )


async def _connect_with_retry(attempts: int, delay_seconds: int):
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        clear_cache()
        platform = get_platform("capital_com")
        try:
            await platform.connect()
            return platform
        except PlatformAuthError as error:
            last_error = error
            if attempt < attempts:
                print(f"Capital.com login attempt {attempt} rejected; retrying in {delay_seconds}s")
                await asyncio.sleep(delay_seconds)
    raise RuntimeError(f"Capital.com login failed after {attempts} attempts: {last_error}")


async def _download(args: argparse.Namespace) -> None:
    from dotenv import load_dotenv

    load_dotenv(args.env_file)
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    spread_audit = json.loads(Path(args.spread_audit).read_text(encoding="utf-8"))
    spread_percent = spread_audit["spread_percent"]
    missing_spreads = sorted(set(UNIVERSE) - set(spread_percent))
    if missing_spreads:
        detail_audit = json.loads(
            Path(args.spread_detail_audit).read_text(encoding="utf-8")
        )
        detail_pairs = detail_audit.get("pairs", {})
        still_missing = []
        for pair in missing_spreads:
            if pair not in detail_pairs:
                still_missing.append(pair)
                continue
            spread_percent[pair] = float(detail_pairs[pair]["fee_per_side"]) * 200
        if still_missing:
            raise ValueError(f"Spread audits are missing: {still_missing}")
    platform = await _connect_with_retry(args.login_attempts, args.login_delay)
    constraints = {}
    try:
        for pair in UNIVERSE:
            print(f"Fetching {pair} ...", flush=True)
            bars = []
            chunk_start = HISTORY_START
            while chunk_start < HISTORY_END:
                chunk_end = min(chunk_start + pd.Timedelta(days=30), HISTORY_END)
                last_error: Optional[Exception] = None
                for fetch_attempt in range(1, 4):
                    try:
                        bars.extend(await platform.fetch_history(
                            pair,
                            from_ts=chunk_start.to_pydatetime(),
                            to_ts=chunk_end.to_pydatetime(),
                            resolution="1h",
                        ))
                        last_error = None
                        break
                    except PlatformAPIError as error:
                        last_error = error
                        if fetch_attempt < 3:
                            await asyncio.sleep(5 * fetch_attempt)
                if last_error is not None:
                    raise RuntimeError(
                        f"History fetch failed for {pair} at {chunk_start}: "
                        f"{last_error}; response={last_error.response_text}"
                    ) from last_error
                chunk_start = chunk_end
                await asyncio.sleep(0.05)
            frame = pd.DataFrame([{
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            } for bar in bars])
            if frame.empty:
                raise RuntimeError(f"No history returned for {pair}")
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            frame = frame.sort_values("timestamp").drop_duplicates("timestamp")
            development = frame.loc[frame["timestamp"] < HOLDOUT_START]
            holdout = frame.loc[
                (frame["timestamp"] >= HOLDOUT_START - pd.Timedelta(days=WARMUP_DAYS))
                & (frame["timestamp"] < HISTORY_END)
            ]
            development.to_csv(data_dir / f"{pair}.development.csv.gz", index=False)
            holdout.to_csv(data_dir / f"{pair}.holdout.csv.gz", index=False)
            constraints[pair] = asdict(await platform.order_constraints(pair))
            print(
                f"  {len(development)} development bars, "
                f"{len(holdout)} holdout/warm-up bars",
                flush=True,
            )
            await asyncio.sleep(args.inter_pair_delay)
    finally:
        await platform.disconnect()
    metadata = {
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "history_start": HISTORY_START.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(),
        "history_end": HISTORY_END.isoformat(),
        "universe": list(UNIVERSE),
        "preregistration_sha256": _preregistration_digest(),
        "spread_source": spread_audit.get("source"),
        "spread_fallback_source": detail_audit.get("source") if missing_spreads else None,
        "spread_fractions": {
            pair: spread_percent[pair] / 100
            for pair in UNIVERSE
        },
        "constraints": constraints,
    }
    (data_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )


def _development(data_dir: Path) -> None:
    history, spreads, constraints, metadata = _load_research_inputs(data_dir, "development")
    digest = _preregistration_digest()
    if metadata["preregistration_sha256"] != digest:
        raise RuntimeError("Preregistration changed after data acquisition")
    results = [
        _evaluate_variant(
            variant,
            history,
            spread_fractions=spreads,
            constraints=constraints,
            entry_start=HISTORY_START,
            entry_end=HOLDOUT_START,
            family_tests=len(VARIANTS),
        )
        for variant in VARIANTS
    ]
    champions = {}
    for family in sorted(set(FAMILY_BY_PREFIX.values())):
        family_results = [result for result in results if result["family"] == family]
        champions[family] = max(
            family_results,
            key=lambda result: (
                result["corrected_lower_bound_r"]
                if result["corrected_lower_bound_r"] is not None else -math.inf
            ),
        )["variant"]
    output = {
        "phase": "development",
        "preregistration_sha256": digest,
        "variant_count": len(VARIANTS),
        "bonferroni_tests": len(VARIANTS),
        "champions": champions,
        "results": results,
    }
    (data_dir / "development_results.json").write_text(
        json.dumps(output, indent=2),
        encoding="utf-8",
    )
    _print_results(results)
    print("\nFrozen family champions:")
    for family, variant in sorted(champions.items()):
        print(f"  {family}: {variant}")


def _holdout(data_dir: Path) -> None:
    development_path = data_dir / "development_results.json"
    development = json.loads(development_path.read_text(encoding="utf-8"))
    digest = _preregistration_digest()
    if development["preregistration_sha256"] != digest:
        raise RuntimeError("Preregistration changed after development")
    history, spreads, constraints, metadata = _load_research_inputs(data_dir, "holdout")
    if metadata["preregistration_sha256"] != digest:
        raise RuntimeError("Preregistration changed after data acquisition")
    champions = development["champions"]
    development_by_variant = {
        result["variant"]: result
        for result in development["results"]
    }
    results = []
    for variant in champions.values():
        result = _evaluate_variant(
            variant,
            history,
            spread_fractions=spreads,
            constraints=constraints,
            entry_start=HOLDOUT_START,
            entry_end=HISTORY_END,
            family_tests=len(champions),
        )
        result["development_passed"] = development_by_variant[variant]["passed"]
        result["deployable"] = result["passed"] and result["development_passed"]
        results.append(result)
    output = {
        "phase": "holdout",
        "preregistration_sha256": digest,
        "distinct_variant_count": len(VARIANTS),
        "holdout_variant_count": len(champions),
        "bonferroni_tests": len(champions),
        "results": results,
        "deployable_candidates": [
            result["variant"] for result in results if result["deployable"]
        ],
    }
    (data_dir / "holdout_results.json").write_text(
        json.dumps(output, indent=2),
        encoding="utf-8",
    )
    _print_results(results)
    print(f"\nDeployable candidates: {output['deployable_candidates'] or 'none'}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=("download", "development", "holdout"),
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--spread-audit", default="data/capital_spread_percent.json")
    parser.add_argument("--spread-detail-audit", default="data/capital_spreads.json")
    parser.add_argument("--login-attempts", type=int, default=5)
    parser.add_argument("--login-delay", type=int, default=60)
    parser.add_argument("--inter-pair-delay", type=float, default=0.6)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.phase == "download":
        asyncio.run(_download(args))
        return
    if args.phase == "development":
        _development(Path(args.data_dir))
        return
    _holdout(Path(args.data_dir))


if __name__ == "__main__":
    main()

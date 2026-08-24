"""Spot-trading auto-trade loop for Kraken / Capital.com.

Polling design (not WebSocket): every cycle we:
  1. Re-fetch the last N bars per active pair
  2. Compute indicators + run the configured strategy
  3. If the most-recent bar emits a signal AND no open position is
     held on that pair, place a market order with stop-loss +
     take-profit derived from ATR
  4. Walk forward — open positions are managed by the broker via the
     attached SL/TP. We just monitor the position list and react to
     fills as they happen.

Why polling: WebSocket streams add complexity (reconnects, gap
handling, race conditions) and our strategies operate on completed
bars, not ticks. A 60-second poll is more than fast enough for 1h
strategies. WS can be added later as a streaming backend behind the
same `Platform.stream_prices()` interface.

Safety:
  - PAPER_TRADE_ONLY (default on) blocks `place_order()` at the
    platform-adapter layer — even if this loop calls it, no real
    order goes out.
  - The cycle aborts if `active_pairs.json` is empty or the platform
    fails to connect — safer to do nothing than to flail.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

import numpy as np
import pandas as pd

from app.platforms import (
    Platform, get_platform, PlatformError, PlatformAuthError,
    PaperTradeOnlyError, OrderResult, Position, Bar,
)
from app.spot_trading.holding_period import (
    stale_exit_after_seconds,
    stale_exits_enabled,
)
from app.spot_trading.strategy_parameters import (
    DEFAULT_RISK_REWARD,
    risk_reward_for,
)
from app.spot_trading.position_sizing import (
    DEFAULT_NOTIONAL_CAP_USD,
    DEFAULT_TARGET_RISK_USD,
    MAX_ROUND_TRIP_COST_RISK_FRACTION,
    calculate_position_size,
    calculate_round_trip_cost_fraction,
)
from app.strategies import get_strategy, add_indicators

if TYPE_CHECKING:
    from app.spot_trading.regime import RegimeDecision


@dataclass
class TradeIntent:
    """A signal-driven request to open a position. Lives between
    strategy evaluation and platform.place_order — so we can log
    what we WANTED to do even if the platform refused."""
    pair: str
    direction: int
    entry_price: float
    stop_loss: float
    take_profit: float
    strategy: str
    confidence: float
    bar_time: datetime
    # ADX the regime gate saw when it let this signal through. Journalled
    # so the gate's thresholds can be evaluated against realized results
    # later — without it there is no way to tell a filter that separates
    # winners from one that only reduces the trade count.
    entry_adx: Optional[float] = None


# ---------------- bar resolution mapping ----------------

# Map our internal resolution string → minutes per bar (used to
# decide how many bars to pull and how often to poll).
_RES_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}
# donchian_trail joins v3 on 2026-08-24. Its trailing exit was never
# modelled in backtests, which scored it on an RR 5.0 backstop it almost
# never reaches. Simulated with its real live parameters (arm at 1.0R,
# ride 2.0xATR) it returns -0.648R over 62 trades at a profit factor of
# 0.19, t ~ -5.7, and the live journal agrees in sign (-0.136R, n=10).
# The trail arms at +1R and sits 2xATR back, so on a 1xATR stop it closes
# at roughly break-even on any ordinary pullback — it caps winners
# instead of letting them run, which is the one thing a trend follower
# must not do. Widening it to 4xATR does produce +4.97R winners, but the
# hit rate needed at that payoff is 18% against the 8.8% achieved.
#
# Entries only. Open positions keep their exit path, including the trail.
_DISABLED_LIVE_STRATEGIES = {"donchian_breakout_v3", "donchian_trail"}

from app.spot_trading.instrument_blocks import (
    COST_BLOCKED_PAIRS as _COST_BLOCKED_PAIRS,
)


def _safe_log(message: str) -> None:
    """Lightweight logger — keeps the spot-trading subsystem
    independent of the legacy `utils.print` singleton."""
    print(f"[spot] {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {message}")


_REGIME_VETO_ADX_LOG_DELTA = 1.0
_REGIME_VETO_SUMMARY_SECONDS = 3600


@dataclass
class _RegimeVetoState:
    reason_key: tuple
    first_seen_at: datetime
    last_logged_at: datetime
    last_logged_adx: Optional[float]
    repeat_count: int = 0
    suppressed_repeats: int = 0


class _RegimeVetoLogger:
    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log
        self._states: Dict[tuple, _RegimeVetoState] = {}

    def observe(
        self,
        pair: str,
        strategy_name: str,
        decision: "RegimeDecision",
        *,
        now: Optional[datetime] = None,
    ) -> None:
        observed_at = now or datetime.now(timezone.utc)
        key = (pair, strategy_name)
        state = self._states.get(key)
        if not decision.blocked:
            if state is None:
                return
            self._states.pop(key)
            first_seen = state.first_seen_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            self._log(
                f"⛓ regime-veto cleared {pair} {strategy_name} after "
                f"{state.repeat_count} repeats since {first_seen}: "
                f"{decision.reason}"
            )
            return

        reason_key = decision.reason
        if decision.adx is not None:
            value_suffix = f", got {decision.adx:.1f}"
            if reason_key.endswith(value_suffix):
                reason_key = reason_key[:-len(value_suffix)]
        current_reason_key = (decision.regime, reason_key)
        if state is None:
            self._states[key] = _RegimeVetoState(
                reason_key=current_reason_key,
                first_seen_at=observed_at,
                last_logged_at=observed_at,
                last_logged_adx=decision.adx,
            )
            self._log(
                f"⛓ regime-veto {pair} {strategy_name}: {decision.reason}"
            )
            return

        state.repeat_count += 1
        reason_changed = current_reason_key != state.reason_key
        value_changed = (
            decision.adx is None
            or state.last_logged_adx is None
            or abs(decision.adx - state.last_logged_adx)
            >= _REGIME_VETO_ADX_LOG_DELTA
        ) and decision.adx != state.last_logged_adx
        summary_due = (
            observed_at - state.last_logged_at
        ).total_seconds() >= _REGIME_VETO_SUMMARY_SECONDS
        if not reason_changed and not value_changed and not summary_due:
            state.suppressed_repeats += 1
            return

        event = "changed" if reason_changed else "update" if value_changed else "persists"
        repeat_label = (
            "repeat" if state.suppressed_repeats == 1 else "repeats"
        )
        first_seen = state.first_seen_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        self._log(
            f"⛓ regime-veto {event} {pair} {strategy_name}: "
            f"{decision.reason} ({state.suppressed_repeats} suppressed "
            f"{repeat_label}; active since {first_seen})"
        )
        state.reason_key = current_reason_key
        state.last_logged_at = observed_at
        state.last_logged_adx = decision.adx
        state.suppressed_repeats = 0


def _bars_to_df(bars: List[Bar]) -> pd.DataFrame:
    return pd.DataFrame([{
        "timestamp": b.timestamp,
        "open": b.open, "high": b.high, "low": b.low, "close": b.close,
        # The strategy contract in app/strategies/base.py promises a
        # volume column; dropping it here meant any strategy using it
        # would raise KeyError, so none could be written. Capital.com
        # populates it on every bar.
        "volume": b.volume,
    } for b in bars])


async def _fetch_recent_bars(platform: Platform, pair: str,
                             resolution: str, lookback_bars: int) -> List[Bar]:
    """Pull just enough bars to evaluate the strategy on the latest
    closed bar. We fetch ~3× the strict warmup to give indicators
    plenty of headroom."""
    minutes = _RES_MINUTES.get(resolution, 60)
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes * lookback_bars)
    return await platform.fetch_history(
        pair, from_ts=start, to_ts=end, resolution=resolution,
    )


def _derive_stop_target(entry: float, direction: int, atr: float,
                        stop_atr: float, rr: float) -> tuple:
    stop_dist = stop_atr * atr
    target_dist = rr * stop_dist
    if direction == +1:
        return entry - stop_dist, entry + target_dist
    return entry + stop_dist, entry - target_dist


# How far the cost filter may stretch a stop before giving up on the
# trade. Beyond roughly double, the position no longer resembles the one
# the strategy signalled.
_MAX_COST_STOP_WIDENING = 2.0

_DEFAULT_MIN_STOP_FRACTION = 0.01
_SPREAD_PERCENT_PATH = "data/capital_spread_percent.json"
_SPREAD_PERCENT_CACHE: Optional[Dict[str, float]] = None
_CRYPTO_SPREAD_FALLBACK_PER_SIDE = {
    "AAVEUSD": 0.0005,
    "APTUSD": 0.0005,
    "ARBUSD": 0.0005,
    "NEARUSD": 0.0005,
    "DOTUSD": 0.0005,
}


def _audited_round_trip_cost(pair: str, reference_price: float) -> float:
    """Round-trip spread from the cost audit, in price units.

    Used when the broker snapshot carries no quote — otherwise a closed
    market would silently disable the cost filter. Known crypto instruments
    retain their conservative fallback when the audit is unavailable."""
    global _SPREAD_PERCENT_CACHE
    if _SPREAD_PERCENT_CACHE is None:
        try:
            import json
            with open(_SPREAD_PERCENT_PATH, "r", encoding="utf-8") as handle:
                _SPREAD_PERCENT_CACHE = (
                    (json.load(handle) or {}).get("spread_percent") or {}
                )
        except (OSError, ValueError):
            _SPREAD_PERCENT_CACHE = {}
    if reference_price <= 0:
        return 0.0
    percent = float(_SPREAD_PERCENT_CACHE.get(pair) or 0.0)
    if percent > 0:
        return reference_price * percent / 100.0
    return (
        reference_price
        * 2.0
        * _CRYPTO_SPREAD_FALLBACK_PER_SIDE.get(pair, 0.0)
    )


def _widen_stop_and_target(
    intent: TradeIntent, reference_price: float, stop_distance: float,
) -> Optional[TradeIntent]:
    """Move stop and target out to `stop_distance`, keeping R:R intact.

    Returns None when the intent carries no usable reward distance to
    scale, so the caller can fall back to skipping the trade."""
    old_stop_distance = abs(reference_price - intent.stop_loss)
    if old_stop_distance <= 0 or stop_distance <= 0:
        return None
    rr = abs(intent.take_profit - reference_price) / old_stop_distance
    if rr <= 0:
        return None
    target_distance = rr * stop_distance
    if intent.direction == +1:
        stop = reference_price - stop_distance
        target = reference_price + target_distance
    else:
        stop = reference_price + stop_distance
        target = reference_price - target_distance
    return replace(intent, stop_loss=stop, take_profit=target)


def _min_stop_fraction() -> float:
    """Smallest stop distance, as a fraction of entry price, still worth
    trading. Set `HURZ_MIN_STOP_FRACTION=0` to disable the floor."""
    raw = os.environ.get("HURZ_MIN_STOP_FRACTION")
    if raw is None or raw == "":
        return _DEFAULT_MIN_STOP_FRACTION
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_MIN_STOP_FRACTION


def _last_signal_for_bar(signals, target_index: int):
    """Return the most-recent signal whose entry index is at or before
    `target_index`. We only act on the LATEST signal for a fresh bar
    to avoid double-triggering on already-evaluated bars."""
    for sig in reversed(signals):
        if sig.index == target_index:
            return sig
    return None


async def evaluate_pair(
    platform: Platform, pair: str, *,
    strategy_name: str, resolution: str,
    stop_atr: float, rr: float, lookback_bars: int,
    apply_venue_min: bool = False,
    regime_veto_logger: Optional[_RegimeVetoLogger] = None,
    on_rejected_intent: Optional[Callable[[TradeIntent, str], None]] = None,
) -> Optional[TradeIntent]:
    """Run a single strategy-evaluation cycle on `pair`. Returns a
    `TradeIntent` if the latest bar produced a signal, else None.

    `apply_venue_min=True` (used by the live loop) expands the ATR-
    derived stop to the venue's minimum if necessary, keeping R:R
    constant by stretching TP proportionally. Backtest mirrors the
    same logic via spot_backtest._simulate_trades(platform=...).
    Without this, FX-1h signals on Capital are virtually unhandelable
    (ATR ~0.0007 vs 1% minimum = 0.01)."""
    if strategy_name in _DISABLED_LIVE_STRATEGIES:
        return None
    if pair in _COST_BLOCKED_PAIRS:
        return None
    strategy = get_strategy(strategy_name)
    bars = await _fetch_recent_bars(platform, pair, resolution, lookback_bars)
    if len(bars) < 50:
        _safe_log(f"⚠ {pair}: only {len(bars)} bars — skipping")
        return None
    df = _bars_to_df(bars)
    df = add_indicators(df)
    signals = strategy(df, {})
    if not signals:
        return None
    # Only act on a signal whose index is the LAST bar (the just-
    # closed bar). Older signals were already evaluated in earlier
    # cycles or pre-date the loop's start.
    last_idx = len(df) - 1
    sig = _last_signal_for_bar(signals, last_idx)
    if sig is None:
        return None
    last_row = df.iloc[last_idx]
    atr = last_row.get("atr_14")
    if atr is None or not np.isfinite(atr) or atr <= 0:
        return None
    entry_price = float(last_row["close"])
    sl, tp = _derive_stop_target(
        entry_price, sig.direction, float(atr), stop_atr, rr,
    )
    if apply_venue_min:
        try:
            venue_min = await platform.min_stop_distance(
                pair, ref_price=entry_price,
            )
        except Exception:
            venue_min = 0.0
        stop_dist = abs(entry_price - sl)
        if venue_min > 0 and stop_dist < venue_min:
            new_stop_dist = venue_min
            new_target_dist = rr * new_stop_dist
            if sig.direction == +1:
                sl = entry_price - new_stop_dist
                tp = entry_price + new_target_dist
            else:
                sl = entry_price + new_stop_dist
                tp = entry_price - new_target_dist
    from app.spot_trading.regime import gate as _regime_gate
    decision = _regime_gate(strategy_name, df, last_idx)
    intent = TradeIntent(
        pair=pair, direction=sig.direction,
        entry_price=entry_price, stop_loss=sl, take_profit=tp,
        strategy=strategy_name, confidence=sig.confidence,
        bar_time=last_row["timestamp"], entry_adx=decision.adx,
    )
    if regime_veto_logger is not None:
        regime_veto_logger.observe(pair, strategy_name, decision)
    if decision.blocked and regime_veto_logger is None:
        _safe_log(f"⛓ regime-veto {pair} {strategy_name}: {decision.reason}")
    if decision.blocked:
        if on_rejected_intent is not None:
            on_rejected_intent(
                intent,
                f"skipped: regime filter: {decision.reason}",
            )
        return None
    # Volume floor. The original justification — narrow stops lose more
    # because spread eats a bigger share of a small risk budget — does not
    # survive booking against actual fills. Over 435 closed trades:
    # stops under 1% return -0.058R (n=56), stops at or above it -0.432R
    # (n=379). The floor removes the *better* side, not the worse one; the
    # old figures came from the signal-price PnL column that overstated
    # narrow-stop losses precisely because slippage is a larger share of a
    # small R.
    #
    # It stays on regardless, for a different reason: expectancy is
    # negative on both sides, so trading more of either only loses faster.
    # Removing it would lift the blended expectancy (-0.432 to -0.384) and
    # raise the loss in dollars, because it multiplies volume roughly
    # fifteenfold. Revisit only once expectancy is positive — at that point
    # the floor becomes the single largest constraint on frequency.
    min_stop_fraction = _min_stop_fraction()
    if min_stop_fraction > 0 and entry_price > 0:
        if abs(entry_price - sl) / entry_price < min_stop_fraction:
            if on_rejected_intent is not None:
                on_rejected_intent(
                    intent,
                    f"skipped: stop distance below "
                    f"{min_stop_fraction:.2%} floor",
                )
            return None
    return intent


async def execute_intent(
    platform: Platform, intent: TradeIntent, size: float,
) -> OrderResult:
    """Hand the intent to the platform. Errors are returned in the
    OrderResult — the loop should not crash on a single bad order."""
    if intent.pair in _COST_BLOCKED_PAIRS:
        return OrderResult(
            accepted=False, asset=intent.pair, direction=intent.direction,
            size=size,
            error=f"cost-blocked instrument: {intent.pair}",
        )
    if intent.strategy in _DISABLED_LIVE_STRATEGIES:
        return OrderResult(
            accepted=False, asset=intent.pair, direction=intent.direction,
            size=size, error=f"disabled live strategy: {intent.strategy}",
        )
    try:
        return await platform.place_order(
            asset=intent.pair, direction=intent.direction, size=size,
            stop_loss=intent.stop_loss, take_profit=intent.take_profit,
        )
    except PaperTradeOnlyError as exc:
        return OrderResult(
            accepted=False, asset=intent.pair, direction=intent.direction,
            size=size, error=f"paper-trade-only: {exc}",
        )
    except PlatformError as exc:
        return OrderResult(
            accepted=False, asset=intent.pair, direction=intent.direction,
            size=size, error=str(exc),
        )


def _has_open_position(positions: List[Position], pair: str) -> bool:
    return any(p.asset == pair for p in positions)


def _record_skip(
    intent: TradeIntent,
    error: str,
    *,
    platform_name: str,
    paper_mode: bool,
    size: Optional[float] = None,
) -> None:
    from app.spot_trading.journal import record
    record(
        intent,
        OrderResult(
            accepted=False,
            asset=intent.pair,
            direction=intent.direction,
            size=size,
            error=error,
        ),
        platform=platform_name,
        paper_mode=paper_mode,
        size=size,
    )


# Min seconds between stale-exit close attempts on the same position.
_STALE_RETRY_COOLDOWN = 1800
_DEFAULT_MAX_CONCURRENT_POSITIONS = 8

# donchian_trail exit parameters. The trail arms once price has moved
# `_TRAIL_ACTIVATION_R` × initial-risk in favor, then rides `_TRAIL_ATR_MULT`
# × ATR behind the best excursion, never giving back below break-even.
# Env-overridable for forward-test tuning without a code change.
_TRAIL_ACTIVATION_R = float(os.getenv("HURZ_TRAIL_ACTIVATION_R", "1.0"))
_TRAIL_ATR_MULT = float(os.getenv("HURZ_TRAIL_ATR_MULT", "2.0"))

# Correlation clusters for the concurrent-position cap. Pairs inside a
# cluster co-move (a crypto selloff, a USD rally, risk-on/off across
# indices), so N same-direction breakouts across them are one concentrated
# bet disguised as N independent edges. _CLUSTER_DIR_CAP limits how many
# same-direction positions may be open per cluster (env
# HURZ_CLUSTER_DIRECTION_CAP). Pairs not listed are uncapped — FX crosses
# and single commodities (EURAUD, CHFJPY, COPPER, WHEAT) are idiosyncratic
# enough to stay their own singletons; over-clustering weakly-correlated
# pairs would falsely throttle the book.
_CORRELATION_CLUSTERS = {
    "BTCUSD": "crypto", "ETHUSD": "crypto", "SOLUSD": "crypto",
    "XRPUSD": "crypto", "ADAUSD": "crypto", "DOGEUSD": "crypto",
    "LTCUSD": "crypto", "LINKUSD": "crypto", "AVAXUSD": "crypto",
    "DOTUSD": "crypto", "AAVEUSD": "crypto", "ATOMUSD": "crypto",
    "ARBUSD": "crypto", "APTUSD": "crypto", "NEARUSD": "crypto",
    "EURUSD": "usd_fx", "GBPUSD": "usd_fx", "AUDUSD": "usd_fx",
    "NZDUSD": "usd_fx", "USDJPY": "usd_fx", "USDCAD": "usd_fx",
    "USDCHF": "usd_fx",
    "DE40": "indices", "FR40": "indices", "UK100": "indices",
    "US30": "indices", "US500": "indices", "US100": "indices",
    "HK50": "indices", "J225": "indices", "AU200": "indices",
    "GOLD": "metals", "SILVER": "metals", "PALLADIUM": "metals",
    "OIL_BRENT": "energy", "OIL_CRUDE": "energy",
}
_CLUSTER_DIR_CAP = int(os.getenv("HURZ_CLUSTER_DIRECTION_CAP", "3"))


async def _resolve_closed_trade(
    platform: Platform, journal_row: Dict,
) -> Optional[Dict]:
    """For a position that's no longer in the broker's open list, consult
    the broker's close activity and then walk bars between its entry and
    now to detect which of (SL, TP) was crossed first. Returns dict with
    exit_price, exit_time, outcome, realized_pnl — or None if neither
    source is available.

    Outcome semantics:
      win     → take-profit hit
      loss    → stop-loss hit
      manual  → neither hit; position was closed externally (operator,
                margin call, broker-side action). Exit price set to
                last bar's close as a best-effort reference.
    """
    pair = journal_row["pair"]
    direction = int(journal_row["direction"])
    # Bar timestamps from fetch_history are tz-aware UTC, but the
    # DATETIME column in MySQL gives us back a tz-naive datetime.
    # Make them comparable.
    entry_time = journal_row["bar_time"]
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)
    entry_fill_price = (
        float(journal_row["fill_price"])
        if journal_row.get("fill_price") is not None else None
    )
    sl = float(journal_row["stop_loss"])
    tp = float(journal_row["take_profit"])
    size = float(journal_row["size"]) if journal_row.get("size") else 1.0

    deal_id = journal_row.get("deal_id")
    fetch_close = getattr(platform, "fetch_close_fill", None)
    if deal_id and fetch_close:
        try:
            fill = await fetch_close(deal_id, entry_time)
        except Exception:
            fill = None
        if fill:
            src = fill.get("source", "")
            if src == "SL":
                outcome = "loss"
            elif src in ("TP", "PROFIT"):
                outcome = "win"
            else:
                outcome = "manual"
            return _closure_payload(
                fill["close_time"], fill["close_level"], outcome,
                entry_fill_price, direction, size,
            )

    # Pull just enough bars to cover the trade window. 1h resolution
    # because that's what the autotrader runs on (other resolutions
    # would need a tracked-per-trade resolution column).
    end = datetime.now(timezone.utc)
    try:
        bars = await platform.fetch_history(
            pair, from_ts=entry_time, to_ts=end, resolution="1h",
        )
    except PlatformError:
        return None
    if not bars:
        return None

    # Walk forward — same SL-first conservatism the backtest uses when
    # a single bar's range covers both. Skip the entry bar itself; the
    # entry happens at its close, so its high/low are pre-entry.
    for b in bars:
        if b.timestamp <= entry_time:
            continue
        if direction == +1:
            hit_sl = b.low <= sl
            hit_tp = b.high >= tp
        else:
            hit_sl = b.high >= sl
            hit_tp = b.low <= tp
        if hit_sl:
            return _closure_payload(b.timestamp, sl, "loss",
                                    entry_fill_price, direction, size)
        if hit_tp:
            return _closure_payload(b.timestamp, tp, "win",
                                    entry_fill_price, direction, size)

    # No SL/TP cross detected in OHLC. Two real possibilities:
    #   1. Sub-bar SL/TP touch on bid/ask that didn't print on the
    #      1h OHLC mid (typical for fast crypto/FX wicks).
    #   2. Actual external close (operator, margin, broker action).
    # The venue activity lookup above was unavailable, so keep the
    # conservative "manual" with last-bar close as the estimate.
    last = bars[-1]
    return _closure_payload(
        last.timestamp, last.close, "manual",
        entry_fill_price, direction, size,
    )


def _closure_payload(exit_time: datetime, exit_price: float, outcome: str,
                     entry_fill_price: Optional[float],
                     direction: int, size: float) -> Dict:
    realized = None
    if entry_fill_price is not None:
        realized = (exit_price - entry_fill_price) * direction * size
    return {
        "exit_time": exit_time,
        "exit_price": exit_price,
        "outcome": outcome,
        "realized_pnl": realized,
    }


def _format_realized_pnl(realized_pnl: Optional[float]) -> str:
    if realized_pnl is None:
        return "unknown"
    return f"{realized_pnl:+.4f}"


def _drop_retired(active: List[Dict], platform_name: str) -> tuple:
    """Remove combos the live veto has retired from the active list.

    Returns `(kept, retired)`. A veto that cannot read its data leaves
    the list untouched: refusing to trade at all on a database hiccup is
    worse than trading the list as written, and the selector already
    fails closed when it writes."""
    try:
        from app.spot_trading.pair_selector import (
            VetoDataUnavailable, live_expectancy_veto,
            strategy_expectancy_veto,
        )
        vetoed = live_expectancy_veto(platform_name)
        vetoed_strategies = strategy_expectancy_veto(platform_name)
    except Exception:
        return active, []
    kept, retired = [], []
    for entry in active:
        combo = (entry.get("strategy"), entry.get("pair"))
        if combo in vetoed or combo[0] in vetoed_strategies:
            retired.append(combo)
        else:
            kept.append(entry)
    return kept, retired


async def run_loop(
    *,
    platform_name: str,
    strategy_name: str,
    resolution: str = "1h",
    stop_atr: float = 1.0,
    rr: float = DEFAULT_RISK_REWARD,
    poll_seconds: int = 60,
    size: float = 1.0,
    lookback_bars: int = 240,
    heartbeat_seconds: int = 3600,
    stop_event: Optional[asyncio.Event] = None,
    max_concurrent: Optional[int] = None,
    notional_per_trade: Optional[float] = None,
    risk_per_trade: Optional[float] = None,
) -> None:
    """Long-running coroutine. Polls active pairs, fires signals,
    places orders. Exit cleanly on `stop_event.set()`.

    `max_concurrent`: hard cap on simultaneous open positions. New
    signals are skipped (and journaled) once the cap is reached. None
    uses the safe default; a non-positive value disables it. Useful on
    platforms where correlated
    strategies fire identical-direction signals across pairs and would
    otherwise produce a single concentrated bet disguised as N trades.

    `risk_per_trade` targets a fixed dollar loss at the normalized stop.
    `notional_per_trade` remains a hard exposure cap. Broker minimum,
    maximum and increment constraints are applied without increasing size."""
    import os as _os
    if max_concurrent is None:
        try:
            max_concurrent = int(_os.getenv(
                "HURZ_MAX_CONCURRENT",
                str(_DEFAULT_MAX_CONCURRENT_POSITIONS),
            ))
        except ValueError:
            max_concurrent = _DEFAULT_MAX_CONCURRENT_POSITIONS
    if max_concurrent <= 0:
        max_concurrent = None
    if notional_per_trade is None and _os.getenv("HURZ_NOTIONAL_PER_TRADE"):
        try:
            notional_per_trade = float(_os.environ["HURZ_NOTIONAL_PER_TRADE"])
        except ValueError:
            pass
    if risk_per_trade is None and _os.getenv("HURZ_RISK_PER_TRADE"):
        try:
            risk_per_trade = float(_os.environ["HURZ_RISK_PER_TRADE"])
        except ValueError:
            pass
    if notional_per_trade is None:
        notional_per_trade = DEFAULT_NOTIONAL_CAP_USD
    if risk_per_trade is None:
        risk_per_trade = DEFAULT_TARGET_RISK_USD
    platform = get_platform(platform_name)
    await platform.connect()
    _safe_log(f"connected to {platform.name} (demo={platform.demo}, "
              f"paper_trade_only={platform.paper_trade_only})")
    if max_concurrent is not None:
        _safe_log(f"  max_concurrent_positions={max_concurrent}")
    # Size follows proven forward edge. The daily target needs roughly
    # ten times the base risk, but raising it while expectancy is
    # negative only scales the losses — so the budget is earned from
    # out-of-sample results rather than assumed.
    from app.spot_trading.edge_scaling import assess_edge
    # The equity ceiling is only as good as the balance we hand in; a
    # broker that will not answer must not silently unlock the full
    # multiple, so an unreadable balance keeps the base risk.
    try:
        balances = await platform.account_balance()
        account_equity = max(balances.values()) if balances else None
    except Exception as exc:
        account_equity = None
        _safe_log(f"  ⚠ account balance unavailable ({exc}) — risk held at base")
    edge = assess_edge(risk_per_trade, account_equity=account_equity)
    if edge.risk_usd > risk_per_trade:
        _safe_log(
            f"  ⬆ risk scaled ${risk_per_trade:.2f} → ${edge.risk_usd:.2f} "
            f"({edge.reason}; {edge.trades} trades, "
            f"lower bound {edge.lower_bound_r:+.3f}R)"
        )
        risk_per_trade = edge.risk_usd
    else:
        _safe_log(f"  risk held at base: {edge.reason}")
    _safe_log(f"  risk_per_trade=${risk_per_trade:.2f}")
    _safe_log(f"  notional_cap=${notional_per_trade:.2f}")
    from app.spot_trading.regime import summary as _regime_summary
    _safe_log(f"  regime_filter={_regime_summary()}")
    if stop_event is None:
        stop_event = asyncio.Event()

    from app.spot_trading.pair_selector import load_active_pairs
    from app.spot_trading import regime
    from app.spot_trading.regime import adx_at as _regime_adx_at
    from app.spot_trading.journal import (
        list_unresolved_open as _list_unresolved_open,
        list_recent_issued_times as _list_recent_issued_times,
    )

    # Per-(pair, bar_time) dedup: an in-flight bar would otherwise re-emit
    # the same signal on every poll. The broker position list is a cycle-start
    # snapshot, so strategy-specific keys can open the same pair twice before
    # the newly accepted position becomes visible.
    issued_intents: Dict[str, datetime] = {}
    issued_strategy_by_pair: Dict[str, str] = {}
    rejected_evaluations: Dict[tuple, datetime] = {}
    duplicate_intents_journaled: set = set()

    def journal_duplicate_if_needed(intent: TradeIntent) -> None:
        first_strategy = issued_strategy_by_pair.get(intent.pair)
        duplicate_key = (intent.pair, intent.bar_time, intent.strategy)
        if (first_strategy is None
                or first_strategy == intent.strategy
                or duplicate_key in duplicate_intents_journaled):
            return
        _record_skip(
            intent,
            "skipped: duplicate instrument signal for bar "
            f"{intent.bar_time.isoformat()}",
            platform_name=platform_name,
            paper_mode=platform.paper_trade_only,
        )
        duplicate_intents_journaled.add(duplicate_key)

    # Circuit breaker: hard daily cap on signals issued by this loop.
    # Defends against a runaway scenario where a strategy bug or
    # corrupted active_pairs.json fires hundreds of intents in a day.
    # Seed the rolling 24h window from the journal. The cap is generous
    # given the backtest
    # expectation of ~10-15 signals/day; anything above 100 is
    # almost certainly a bug.
    issued_since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_issued = _list_recent_issued_times(
        platform_name,
        platform.paper_trade_only,
        issued_since,
    )
    daily_cap_history_available = recent_issued is not None
    issued_log: List[datetime] = recent_issued or []
    if not daily_cap_history_available:
        _safe_log(
            "⛔ daily-cap history unavailable — new entries remain blocked"
        )
    # Combos already reported as retired-but-listed, so the warning
    # appears once rather than every cycle.
    retired_logged: set = set()
    # Log the daily-loss halt once per day, not once per blocked signal.
    daily_loss_logged = False
    daily_loss_day: Optional[str] = None
    daily_cap = 100
    # Cooldown for failed exit closes: a position the broker refuses
    # must not be retried every poll cycle. Keyed by journal deal_id →
    # last-attempt time; retried at most once per _STALE_RETRY_COOLDOWN.
    stale_exit_attempts: Dict[str, datetime] = {}
    stale_exit_closed_markets: set = set()

    regime_veto_logger = _RegimeVetoLogger(_safe_log)

    # Pairs the broker refuses to short (rejectReason LONG_ONLY, e.g.
    # AAVEUSD on Capital). Learned from rejections at runtime so the
    # loop stops re-submitting doomed short orders every bar.
    long_only_pairs: set = set()

    # Heartbeat: emit a status line every `heartbeat_seconds` so the
    # log shows the loop is alive even when no signals fire. Without
    # this a quiet day looks identical to a wedged process.
    last_heartbeat_at: Optional[datetime] = None

    # Exit-tracking. Each cycle we diff the current position deal_ids
    # against the previous cycle's set; missing ones are positions the
    # broker closed (SL hit, TP hit, manual close). We resolve their
    # outcome by walking the bar history and patch the spot_trades
    # journal row so analytics can finally compute realized win-rate.
    prev_deal_ids: Optional[set] = None
    # Reconcile the journal against the broker: any spot_trades row
    # marked accepted with no exit_time, whose deal_id is not in the
    # broker's current open list, was closed during a previous bot
    # lifetime or missed by the deal_id diff. Runs at startup AND
    # periodically — positions opened mid-run can carry an "o_"
    # dealReference (confirms poll didn't return the real dealId in
    # time) which the diff can never match, so without a recurring pass
    # their close is missed and they linger open in the DB forever.
    # The recurring pass adopts the real dealId while the position is
    # still live and resolves genuinely-closed rows.
    initial_reconcile_pending = True
    last_reconcile_at: Optional[datetime] = None
    reconcile_seconds = 300

    try:
        while not stop_event.is_set():
            active = load_active_pairs(platform=platform_name)
            # Filter to entries that match this loop's platform;
            # otherwise we'd try to fetch a Capital.com epic on Kraken.
            active = [p for p in active if p.get("platform") == platform_name]
            # Second line of defence. The active list is a file, and a
            # file can be stale, hand-edited, or written by a selector
            # run whose journal was unreachable — in which case it holds
            # every combo the vetoes had retired. Re-checking here costs
            # one query per cycle and keeps a bad file from trading.
            active, retired = _drop_retired(active, platform_name)
            for combo in retired:
                if combo not in retired_logged:
                    retired_logged.add(combo)
                    _safe_log(
                        f"⛔ {combo[1]} {combo[0]}: retired by the live "
                        f"veto but present in the active list — skipping"
                    )
            if not active:
                _safe_log(
                    f"no active pairs for {platform_name} in "
                    f"data/active_pairs.json — sleeping {poll_seconds}s"
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
                    break
                except asyncio.TimeoutError:
                    continue

            try:
                positions = await platform.list_positions()
            except PlatformError as exc:
                _safe_log(f"⚠ list_positions failed: {exc} — backing off")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
                    break
                except asyncio.TimeoutError:
                    continue

            # Exit-tracking: positions in prev_deal_ids that aren't in
            # the current set were closed during the last cycle. Resolve
            # outcome and patch the journal. Only runs after the first
            # cycle (we need a baseline to diff against).
            #
            # Capital quirk: `confirms.dealId` (what place_order returns
            # and we store in the journal) differs from
            # `positions[].dealId` (what list_positions returns) — the
            # latter is the position id, the former matches the working
            # order id stored in `position.workingOrderId`. Add both
            # to the matching set so the diff actually finds journaled
            # entries.
            current_deal_ids = set()
            for p in positions:
                if p.id:
                    current_deal_ids.add(p.id)
                pmeta = (p.meta or {}).get("position") or {}
                if pmeta.get("workingOrderId"):
                    current_deal_ids.add(pmeta["workingOrderId"])
            reconcile_now = (
                initial_reconcile_pending
                or last_reconcile_at is None
                or (datetime.now(timezone.utc) - last_reconcile_at)
                .total_seconds() >= reconcile_seconds
            )
            if reconcile_now:
                from app.spot_trading.journal import (
                    list_unresolved_open, record_exit, update_deal_id,
                )
                unresolved = list_unresolved_open(platform=platform_name)
                stale = [r for r in unresolved
                         if r["deal_id"] not in current_deal_ids]
                # A stale deal_id may be the order's dealReference rather
                # than the position's dealId (place_order falls back to it
                # when the confirms poll fails). If an unclaimed broker
                # position for the same pair+direction exists, adopt its
                # real dealId instead of phantom-closing a live position.
                # "Claimed" must cover positions matched via workingOrderId
                # too, or adoption could steal a healthy row's position.
                unresolved_ids = {r["deal_id"] for r in unresolved}
                claimed = set()
                for p in positions:
                    pmeta = (p.meta or {}).get("position") or {}
                    if (p.id in unresolved_ids
                            or pmeta.get("workingOrderId") in unresolved_ids):
                        claimed.add(p.id)
                still_stale = []
                for row in stale:
                    match = next(
                        (p for p in positions
                         if p.asset == row["pair"]
                         and p.direction == row["direction"]
                         and p.id and p.id not in claimed),
                        None,
                    )
                    if match is None:
                        still_stale.append(row)
                        continue
                    update_deal_id(
                        row["id"], match.id, fill_price=match.entry_price,
                    )
                    claimed.add(match.id)
                    current_deal_ids.add(match.id)
                    _safe_log(
                        f"🔗 reconcile {row['pair']} {row['strategy']}: "
                        f"adopted broker dealId {match.id} "
                        f"(journal had {row['deal_id']})"
                    )
                stale = still_stale
                if stale:
                    _safe_log(
                        f"reconcile: {len(stale)} unresolved trade(s) "
                        f"with no broker-side position — resolving"
                    )
                for row in stale:
                    try:
                        payload = await _resolve_closed_trade(platform, row)
                    except Exception:
                        payload = None
                    if payload is None:
                        continue
                    record_exit(
                        row["id"],
                        exit_price=payload["exit_price"],
                        exit_time=payload["exit_time"],
                        outcome=payload["outcome"],
                        realized_pnl=payload["realized_pnl"],
                    )
                    _safe_log(
                        f"📕 reconcile {row['pair']} "
                        f"{row['strategy']} {payload['outcome']} "
                        f"@ {payload['exit_price']:.5f} "
                        f"pnl={_format_realized_pnl(payload['realized_pnl'])}"
                    )
                initial_reconcile_pending = False
                last_reconcile_at = datetime.now(timezone.utc)
            if prev_deal_ids is not None:
                closed = prev_deal_ids - current_deal_ids
                if closed:
                    from app.spot_trading.journal import (
                        find_open_by_deal_id, record_exit,
                    )
                    for deal_id in closed:
                        row = find_open_by_deal_id(deal_id)
                        if row is None:
                            continue
                        try:
                            payload = await _resolve_closed_trade(
                                platform, row,
                            )
                        except Exception as exc:
                            _safe_log(
                                f"⚠ resolve_closed_trade failed for "
                                f"{deal_id}: {exc}"
                            )
                            payload = None
                        if payload is None:
                            continue
                        record_exit(
                            row["id"],
                            exit_price=payload["exit_price"],
                            exit_time=payload["exit_time"],
                            outcome=payload["outcome"],
                            realized_pnl=payload["realized_pnl"],
                        )
                        _safe_log(
                            f"📕 closed {row['pair']} "
                            f"{row['strategy']} {payload['outcome']} "
                            f"@ {payload['exit_price']:.5f} "
                            f"pnl={_format_realized_pnl(payload['realized_pnl'])}"
                        )
            prev_deal_ids = current_deal_ids

            # Map journal's stored deal_id (= workingOrderId on Capital) to
            # the broker-side position id required by close_position. Kraken
            # returns p.id only — that's already the close target. Shared by
            # the stale-exit, regime-flip-exit and trail-exit blocks below.
            close_id_by_journal_id: Dict[str, str] = {}
            market_status_by_journal_id: Dict[str, str] = {}
            for p in positions:
                market = (p.meta or {}).get("market") or {}
                market_status = market.get("marketStatus")
                if p.id:
                    close_id_by_journal_id[p.id] = p.id
                    if market_status:
                        market_status_by_journal_id[p.id] = market_status
                pmeta = (p.meta or {}).get("position") or {}
                woi = pmeta.get("workingOrderId")
                if woi and p.id:
                    close_id_by_journal_id[woi] = p.id
                    if market_status:
                        market_status_by_journal_id[woi] = market_status

            # Stale-position exit: mean-revert and breakout setups have a
            # bounded expected holding period (max N×bar). When a trade
            # exceeds it without hitting SL/TP, the original thesis is
            # already invalidated — close at market to free the per-pair
            # slot and stop tying up margin on dead conviction.
            # Configurable via HURZ_MAX_HOLD_BARS (default 24 bars).
            if stale_exits_enabled():
                now_utc = datetime.now(timezone.utc)
                for row in _list_unresolved_open(platform=platform_name):
                    if row.get("deal_id") not in current_deal_ids:
                        continue
                    created_at = row.get("created_at")
                    if created_at is None:
                        continue
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    strategy_name = row.get("strategy") or ""
                    max_hold_seconds = stale_exit_after_seconds(
                        strategy_name,
                        resolution,
                    )
                    if max_hold_seconds is None:
                        continue
                    age_seconds = (now_utc - created_at).total_seconds()
                    if age_seconds < max_hold_seconds:
                        continue
                    journal_deal_id = row["deal_id"]
                    market_status = market_status_by_journal_id.get(
                        journal_deal_id,
                    )
                    if market_status == "CLOSED":
                        stale_exit_attempts.pop(journal_deal_id, None)
                        if journal_deal_id not in stale_exit_closed_markets:
                            _safe_log(
                                f"⏸ stale-exit {row.get('pair', '?')} "
                                f"suspended after {age_seconds / 3600:.1f}h: "
                                "marketStatus=CLOSED — waiting for reopening"
                            )
                            stale_exit_closed_markets.add(journal_deal_id)
                        continue
                    stale_exit_closed_markets.discard(journal_deal_id)
                    # Back off positions the broker just refused to close
                    # instead of retrying every cycle and flooding the log.
                    last_attempt = stale_exit_attempts.get(journal_deal_id)
                    if last_attempt is not None and (
                            now_utc - last_attempt
                    ).total_seconds() < _STALE_RETRY_COOLDOWN:
                        continue
                    close_target = close_id_by_journal_id.get(
                        journal_deal_id, journal_deal_id,
                    )
                    pair_name = row.get("pair", "?")
                    age_h = age_seconds / 3600
                    try:
                        close_res = await platform.close_position(close_target)
                    except Exception as exc:
                        stale_exit_attempts[journal_deal_id] = now_utc
                        _safe_log(
                            f"⚠ stale-exit {pair_name} ({close_target}) "
                            f"close failed after {age_h:.1f}h: {exc}"
                        )
                        continue
                    if close_res.accepted:
                        stale_exit_attempts.pop(journal_deal_id, None)
                        _safe_log(
                            f"⏲ stale-exit {pair_name} ({close_target}) "
                            f"closed after {age_h:.1f}h "
                            f"(> {max_hold_seconds / 3600:.0f}h)"
                        )
                    else:
                        stale_exit_attempts[journal_deal_id] = now_utc
                        _safe_log(
                            f"⚠ stale-exit {pair_name} ({close_target}) "
                            f"rejected after {age_h:.1f}h: {close_res.error} "
                            f"— retry in {_STALE_RETRY_COOLDOWN // 60}min"
                        )

            # Regime-flip exit: the entry router blocks NEW mean-reversion
            # signals once ADX leaves the range, but positions opened while
            # ADX was still low keep running into their stops as a trend
            # builds. Force-close open MEAN-REVERSION positions the moment
            # ADX rises past the flip threshold (default = the range ceiling
            # ~20, NOT the trend floor 30 — most of the damage happens in the
            # 20-30 "no-trade zone" before a full trend, so waiting for 30
            # missed it: 24h of bollinger_rev bled -$55 while the ADX>=30
            # flip fired once). Trend-following positions (donchian_breakout,
            # momentum, turtle_breakout) are never touched — high ADX is
            # their edge. Shares the stale_exit_attempts cooldown so a broker
            # that refuses the close (e.g. weekend FX) isn't retried every
            # cycle.
            if regime.flip_exit_enabled():
                flip_adx = regime.flip_exit_threshold()
                now_utc = datetime.now(timezone.utc)
                for row in _list_unresolved_open(platform=platform_name):
                    if row.get("deal_id") not in current_deal_ids:
                        continue
                    strat = row.get("strategy") or ""
                    if regime.style_of(strat) != "mean_reversion":
                        continue
                    journal_deal_id = row["deal_id"]
                    last_attempt = stale_exit_attempts.get(journal_deal_id)
                    if last_attempt is not None and (
                            now_utc - last_attempt
                    ).total_seconds() < _STALE_RETRY_COOLDOWN:
                        continue
                    pair_name = row.get("pair", "?")
                    try:
                        bars = await _fetch_recent_bars(
                            platform, pair_name, resolution, lookback_bars,
                        )
                    except Exception as exc:
                        _safe_log(f"⚠ regime-exit {pair_name}: fetch failed: {exc}")
                        continue
                    if len(bars) < 50:
                        continue
                    df_adx = add_indicators(_bars_to_df(bars))
                    adx = _regime_adx_at(df_adx, len(df_adx) - 1)
                    if adx is None or adx < flip_adx:
                        continue
                    close_target = close_id_by_journal_id.get(
                        journal_deal_id, journal_deal_id,
                    )
                    try:
                        close_res = await platform.close_position(close_target)
                    except Exception as exc:
                        stale_exit_attempts[journal_deal_id] = now_utc
                        _safe_log(
                            f"⚠ regime-exit {pair_name} ({close_target}) "
                            f"close failed (ADX={adx:.1f}): {exc}"
                        )
                        continue
                    if close_res.accepted:
                        stale_exit_attempts.pop(journal_deal_id, None)
                        _safe_log(
                            f"🔀 regime-exit {pair_name} {strat} closed "
                            f"(ADX={adx:.1f} >= {flip_adx:.0f}, range ended)"
                        )
                    else:
                        stale_exit_attempts[journal_deal_id] = now_utc
                        _safe_log(
                            f"⚠ regime-exit {pair_name} ({close_target}) "
                            f"rejected (ADX={adx:.1f}): {close_res.error} "
                            f"— retry in {_STALE_RETRY_COOLDOWN // 60}min"
                        )

            # Trailing-stop exit for donchian_trail. This variant enters
            # exactly like donchian_breakout but is closed here with a
            # break-even + ATR trailing stop instead of a fixed TP — a
            # parallel forward-test of "let winners run, protect gains" vs
            # the fixed 1:1.5 target. Only 'donchian_trail' positions are
            # touched; the core book is untouched. A strategy-specific far
            # backstop caps the tail if the trail never arms. Shares
            # the stale_exit_attempts cooldown so a refused close (weekend
            # FX → HTTP 400) isn't retried every cycle.
            now_utc = datetime.now(timezone.utc)
            for row in _list_unresolved_open(platform=platform_name):
                if row.get("strategy") != "donchian_trail":
                    continue
                if row.get("deal_id") not in current_deal_ids:
                    continue
                journal_deal_id = row["deal_id"]
                last_attempt = stale_exit_attempts.get(journal_deal_id)
                if last_attempt is not None and (
                        now_utc - last_attempt
                ).total_seconds() < _STALE_RETRY_COOLDOWN:
                    continue
                pair_name = row.get("pair", "?")
                entry_px = float(row["entry_price"])
                sl_px = float(row["stop_loss"])
                d = int(row["direction"])
                risk = abs(entry_px - sl_px)
                if risk <= 0:
                    continue
                entry_bar_time = row.get("bar_time")
                if entry_bar_time is not None and entry_bar_time.tzinfo is None:
                    entry_bar_time = entry_bar_time.replace(tzinfo=timezone.utc)
                try:
                    bars = await _fetch_recent_bars(
                        platform, pair_name, resolution, lookback_bars,
                    )
                except Exception as exc:
                    _safe_log(f"⚠ trail-exit {pair_name}: fetch failed: {exc}")
                    continue
                if len(bars) < 50:
                    continue
                atr = add_indicators(_bars_to_df(bars)).iloc[-1].get("atr_14")
                if atr is None or not np.isfinite(atr) or atr <= 0:
                    continue
                # Best favorable excursion since entry (exclude the entry bar,
                # whose close IS the entry — its own extremes are pre-entry).
                post = [b for b in bars
                        if entry_bar_time is None or b.timestamp > entry_bar_time]
                if not post:
                    continue
                close_px = float(post[-1].close)
                if d == +1:
                    peak = max(b.high for b in post)
                    excursion = peak - entry_px
                    trail_level = max(entry_px, peak - _TRAIL_ATR_MULT * atr)
                    breached = close_px <= trail_level
                else:
                    peak = min(b.low for b in post)
                    excursion = entry_px - peak
                    trail_level = min(entry_px, peak + _TRAIL_ATR_MULT * atr)
                    breached = close_px >= trail_level
                # Not yet armed: original SL / far TP still govern.
                if excursion < _TRAIL_ACTIVATION_R * risk:
                    continue
                if not breached:
                    continue
                close_target = close_id_by_journal_id.get(
                    journal_deal_id, journal_deal_id,
                )
                try:
                    close_res = await platform.close_position(close_target)
                except Exception as exc:
                    stale_exit_attempts[journal_deal_id] = now_utc
                    _safe_log(f"⚠ trail-exit {pair_name} ({close_target}) "
                              f"close failed: {exc}")
                    continue
                if close_res.accepted:
                    stale_exit_attempts.pop(journal_deal_id, None)
                    locked_r = (close_px - entry_px) * d / risk
                    _safe_log(
                        f"📉 trail-exit {pair_name} donchian_trail closed "
                        f"@ {close_px:.5f} (locked {locked_r:+.2f}R)"
                    )
                else:
                    stale_exit_attempts[journal_deal_id] = now_utc
                    _safe_log(
                        f"⚠ trail-exit {pair_name} ({close_target}) "
                        f"rejected: {close_res.error} "
                        f"— retry in {_STALE_RETRY_COOLDOWN // 60}min"
                    )

            # Positions opened during THIS cycle, as (cluster, direction)
            # tuples — the cluster cap must see them because `positions` is
            # a cycle-start snapshot the broker won't refresh mid-loop, and
            # the whole point of the cap is the "everything fires at once"
            # burst.
            opened_this_cycle: List[tuple] = []
            opened_this_cycle_count = 0
            for entry in active:
                pair = entry.get("pair")
                entry_strategy = entry.get("strategy") or strategy_name
                entry_resolution = entry.get("resolution") or resolution
                if not pair:
                    continue
                if _has_open_position(positions, pair):
                    continue
                entry_rr = risk_reward_for(entry_strategy, rr)

                def journal_evaluation_rejection(
                    rejected_intent: TradeIntent,
                    error: str,
                ) -> None:
                    last_issued = issued_intents.get(rejected_intent.pair)
                    if (last_issued is not None
                            and last_issued >= rejected_intent.bar_time):
                        journal_duplicate_if_needed(rejected_intent)
                        return
                    rejection_key = (
                        rejected_intent.pair,
                        rejected_intent.strategy,
                    )
                    last_rejected = rejected_evaluations.get(rejection_key)
                    if (last_rejected is not None
                            and last_rejected >= rejected_intent.bar_time):
                        return
                    _record_skip(
                        rejected_intent,
                        error,
                        platform_name=platform_name,
                        paper_mode=platform.paper_trade_only,
                    )
                    rejected_evaluations[rejection_key] = (
                        rejected_intent.bar_time
                    )

                try:
                    intent = await evaluate_pair(
                        platform, pair,
                        strategy_name=entry_strategy,
                        resolution=entry_resolution,
                        stop_atr=stop_atr, rr=entry_rr,
                        lookback_bars=lookback_bars,
                        apply_venue_min=True,
                        regime_veto_logger=regime_veto_logger,
                        on_rejected_intent=journal_evaluation_rejection,
                    )
                except PlatformError as exc:
                    _safe_log(f"⚠ {pair}: evaluate failed: {exc}")
                    continue
                if intent is None:
                    continue
                # Dedup: skip if we've already issued an intent for this
                # (pair, bar_time). Resets when the bar closes.
                # MUST run before the venue-min-stop guard, otherwise a
                # rejected signal re-evaluates and re-journals every
                # 60-second poll until the bar closes (observed: 67
                # journal-spam entries in 3h on BTCUSD + ETHUSD).
                dedup_key = pair
                last_seen = issued_intents.get(dedup_key)
                if last_seen is not None and last_seen >= intent.bar_time:
                    journal_duplicate_if_needed(intent)
                    continue
                issued_strategy_by_pair[dedup_key] = intent.strategy
                # Venue-min-stop guard: if our ATR-derived stop is
                # tighter than the broker's minimum, the broker would
                # auto-clamp it — silently distorting R:R away from
                # the backtest assumption (e.g. 1:1.5 → 1:0.6). Skip
                # the trade and journal it so the operator sees how
                # often this happens. Backtest needs to mirror this
                # filter before we trust live fills against it.
                try:
                    venue_min = await platform.min_stop_distance(
                        intent.pair, ref_price=intent.entry_price,
                    )
                except PlatformError as exc:
                    _safe_log(f"⚠ {pair}: min_stop_distance failed: {exc}")
                    venue_min = 0.0
                stop_dist = abs(intent.entry_price - intent.stop_loss)
                # 1% slack against floating-point / mid-price drift
                # between evaluate_pair's expansion and this re-check.
                # Without slack, expansion that sets stop_dist exactly
                # to venue_min gets skipped by a fresh quote that nudged
                # venue_min up by a few units in the last 0.x seconds.
                if venue_min > 0 and stop_dist < venue_min * 0.99:
                    _safe_log(
                        f"⏭ {intent.pair}: ATR stop "
                        f"{stop_dist:.5f} < venue min {venue_min:.5f} — "
                        f"skipping ({intent.strategy})"
                    )
                    skip_result = OrderResult(
                        accepted=False, asset=intent.pair,
                        direction=intent.direction, size=size,
                        error=f"skipped: ATR stop {stop_dist:.5f} < "
                              f"venue min {venue_min:.5f}",
                    )
                    from app.spot_trading.journal import record as _journal_record
                    _journal_record(
                        intent, skip_result,
                        platform=platform_name,
                        paper_mode=platform.paper_trade_only,
                        size=size,
                    )
                    # Mark this (pair, strategy, bar_time) as handled so
                    # the dedup-check above short-circuits the next 60s
                    # poll and avoids logging a duplicate skip.
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                if intent.direction < 0 and intent.pair in long_only_pairs:
                    error = "skipped: broker is LONG_ONLY"
                    _safe_log(
                        f"⏭ {intent.pair}: broker is LONG_ONLY — "
                        f"skipping short ({intent.strategy})"
                    )
                    _record_skip(
                        intent,
                        error,
                        platform_name=platform_name,
                        paper_mode=platform.paper_trade_only,
                    )
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                # Correlation-cluster cap: limit same-direction exposure
                # across a co-moving group (crypto, USD majors, indices).
                # Counts both broker-open positions and ones opened earlier
                # this cycle. Direction is only known now (post-evaluate),
                # so the check lives here rather than at the pair-skip above.
                cluster = _CORRELATION_CLUSTERS.get(intent.pair)
                if cluster is not None:
                    same_dir = sum(
                        1 for p in positions
                        if _CORRELATION_CLUSTERS.get(p.asset) == cluster
                        and p.direction == intent.direction
                    ) + sum(
                        1 for c, dvec in opened_this_cycle
                        if c == cluster and dvec == intent.direction
                    )
                    if same_dir >= _CLUSTER_DIR_CAP:
                        error = (
                            f"skipped: cluster '{cluster}' "
                            f"direction {intent.direction:+d} cap "
                            f"{_CLUSTER_DIR_CAP} reached ({same_dir} open)"
                        )
                        _safe_log(
                            f"⏭ {intent.pair}: cluster '{cluster}' "
                            f"dir={intent.direction:+d} cap {_CLUSTER_DIR_CAP} "
                            f"reached ({same_dir} open) — skipping "
                            f"({intent.strategy})"
                        )
                        _record_skip(
                            intent,
                            error,
                            platform_name=platform_name,
                            paper_mode=platform.paper_trade_only,
                        )
                        issued_intents[dedup_key] = intent.bar_time
                        continue

                # Concurrent-position cap. Defended against the
                # "all 5 active pairs go long on the same 4h close"
                # pattern observed when a mean-reverter sees a correlated
                # selloff: without the cap, that's one concentrated bet
                # dressed as N independent trades.
                concurrent_positions = (
                    len(positions) + opened_this_cycle_count
                )
                if (max_concurrent is not None
                        and concurrent_positions >= max_concurrent):
                    error = (
                        f"skipped: max_concurrent={max_concurrent} reached "
                        f"({concurrent_positions} open)"
                    )
                    _safe_log(
                        f"⏭ {intent.pair}: max_concurrent={max_concurrent} "
                        f"reached ({concurrent_positions} open) — "
                        f"skipping ({intent.strategy})"
                    )
                    _record_skip(
                        intent,
                        error,
                        platform_name=platform_name,
                        paper_mode=platform.paper_trade_only,
                    )
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                try:
                    prepared = await platform.prepare_order(
                        asset=intent.pair,
                        direction=intent.direction,
                        reference_price=intent.entry_price,
                        stop_loss=intent.stop_loss,
                        take_profit=intent.take_profit,
                    )
                except PlatformError as exc:
                    error = f"skipped: order constraints unavailable: {exc}"
                    _safe_log(f"⏭ {intent.pair}: {error}")
                    skip_result = OrderResult(
                        accepted=False,
                        asset=intent.pair,
                        direction=intent.direction,
                        error=error,
                    )
                    from app.spot_trading.journal import record as _journal_record
                    _journal_record(
                        intent,
                        skip_result,
                        platform=platform_name,
                        paper_mode=platform.paper_trade_only,
                    )
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                intent = replace(
                    intent,
                    stop_loss=(
                        prepared.stop_loss
                        if prepared.stop_loss is not None else intent.stop_loss
                    ),
                    take_profit=(
                        prepared.take_profit
                        if prepared.take_profit is not None else intent.take_profit
                    ),
                )
                stop_distance = abs(
                    prepared.reference_price - intent.stop_loss
                )
                # The broker only reports a spread when the snapshot
                # carries both sides of the quote; with a closed market
                # it comes back as zero, and a zero silently waves the
                # trade past the cost filter. Fall back on the audited
                # spread for the instrument so the filter cannot be
                # switched off by a missing quote.
                round_trip_cost = prepared.round_trip_cost
                if round_trip_cost <= 0:
                    round_trip_cost = _audited_round_trip_cost(
                        intent.pair, prepared.reference_price,
                    )
                cost_fraction = calculate_round_trip_cost_fraction(
                    round_trip_cost=round_trip_cost,
                    stop_distance=stop_distance,
                )
                # Cost share falls linearly as the stop widens, and the
                # risk budget keeps the dollar risk constant by shrinking
                # size to match — so widening is free in risk terms where
                # the signal can carry it. Bounded by
                # _MAX_COST_STOP_WIDENING so a cheap instrument is nudged
                # out of the cost trap while a structurally unhandelable
                # one still falls through to the skip below rather than
                # being handed a stop its strategy never asked for.
                if (round_trip_cost > 0
                        and cost_fraction > MAX_ROUND_TRIP_COST_RISK_FRACTION
                        and stop_distance > 0):
                    needed = (round_trip_cost
                              / MAX_ROUND_TRIP_COST_RISK_FRACTION)
                    capped = min(needed,
                                 stop_distance * _MAX_COST_STOP_WIDENING)
                    if capped > stop_distance:
                        widened = _widen_stop_and_target(
                            intent, prepared.reference_price, capped,
                        )
                        if widened is not None:
                            intent = widened
                            _safe_log(
                                f"↔ {intent.pair}: stop widened "
                                f"{stop_distance:.8g} → {capped:.8g} to cut "
                                f"cost share {cost_fraction:.1%} → "
                                f"{round_trip_cost / capped:.1%} "
                                f"({intent.strategy})"
                            )
                            stop_distance = capped
                            cost_fraction = (
                                calculate_round_trip_cost_fraction(
                                    round_trip_cost=round_trip_cost,
                                    stop_distance=stop_distance,
                                )
                            )
                if (round_trip_cost > 0
                        and cost_fraction
                        > MAX_ROUND_TRIP_COST_RISK_FRACTION):
                    error = (
                        f"skipped: round-trip cost {round_trip_cost:.8g} "
                        f"is {cost_fraction:.1%} of stop distance "
                        f"{stop_distance:.8g}"
                    )
                    _safe_log(f"⏭ {intent.pair}: {error} ({intent.strategy})")
                    skip_result = OrderResult(
                        accepted=False,
                        asset=intent.pair,
                        direction=intent.direction,
                        error=error,
                    )
                    from app.spot_trading.journal import record as _journal_record
                    _journal_record(
                        intent,
                        skip_result,
                        platform=platform_name,
                        paper_mode=platform.paper_trade_only,
                    )
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                constraints = prepared.constraints
                sizing = calculate_position_size(
                    entry_price=prepared.reference_price,
                    stop_loss=intent.stop_loss,
                    target_risk=risk_per_trade,
                    notional_cap=notional_per_trade,
                    min_size=constraints.min_size,
                    size_increment=constraints.size_increment,
                    max_size=constraints.max_size,
                )
                if sizing.skipped:
                    error = f"skipped: {sizing.reason}"
                    _safe_log(f"⏭ {intent.pair}: {error} ({intent.strategy})")
                    skip_result = OrderResult(
                        accepted=False,
                        asset=intent.pair,
                        direction=intent.direction,
                        error=error,
                    )
                    from app.spot_trading.journal import record as _journal_record
                    _journal_record(
                        intent,
                        skip_result,
                        platform=platform_name,
                        paper_mode=platform.paper_trade_only,
                    )
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                # Circuit breaker: prune log to the rolling 24h window
                # and bail if we'd exceed the daily cap.
                now_utc = datetime.now(timezone.utc)
                cutoff = now_utc - timedelta(hours=24)
                issued_log = [t for t in issued_log if t >= cutoff]
                if not daily_cap_history_available:
                    error = "skipped: daily-cap history unavailable"
                    _record_skip(
                        intent,
                        error,
                        platform_name=platform_name,
                        paper_mode=platform.paper_trade_only,
                    )
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                if len(issued_log) >= daily_cap:
                    error = (
                        f"skipped: daily cap of {daily_cap} signals reached"
                    )
                    _safe_log(
                        f"⛔ daily cap of {daily_cap} signals reached — "
                        f"halting until rolling 24h window decays. "
                        f"This is a safety circuit breaker; investigate "
                        f"if this fires before live mode is enabled."
                    )
                    _record_skip(
                        intent,
                        error,
                        platform_name=platform_name,
                        paper_mode=platform.paper_trade_only,
                    )
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                # The count above is not a risk limit — a hundred small
                # trades and a hundred stop-outs look the same to it.
                # This one is measured in R, so it holds regardless of
                # what the risk budget has scaled itself to.
                from app.spot_trading.risk_guard import daily_loss
                today = now_utc.strftime("%Y-%m-%d")
                if today != daily_loss_day:
                    daily_loss_day = today
                    daily_loss_logged = False
                loss = daily_loss(now_utc)
                if loss.blocked:
                    error = (
                        f"skipped: daily loss guard unavailable: {loss.error}"
                        if loss.error else
                        f"skipped: daily loss {loss.realised_r:+.2f}R "
                        f"reached {loss.limit_r:.1f}R limit"
                    )
                    if not daily_loss_logged:
                        if loss.error:
                            _safe_log(
                                f"⛔ daily loss guard unavailable: "
                                f"{loss.error} — no new entries"
                            )
                        else:
                            _safe_log(
                                f"⛔ daily loss {loss.realised_r:+.2f}R "
                                f"reached the {loss.limit_r:.1f}R limit over "
                                f"{loss.trades} closes — no new entries today. "
                                f"Open positions keep their stops."
                            )
                        daily_loss_logged = True
                    _record_skip(
                        intent,
                        error,
                        platform_name=platform_name,
                        paper_mode=platform.paper_trade_only,
                    )
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                issued_intents[dedup_key] = intent.bar_time
                issued_log.append(now_utc)
                trade_size = sizing.size
                _safe_log(
                    f"signal {pair} dir={intent.direction:+d} "
                    f"entry={intent.entry_price:.5f} "
                    f"sizing_entry={prepared.reference_price:.5f} "
                    f"sl={intent.stop_loss:.5f} tp={intent.take_profit:.5f} "
                    f"strat={intent.strategy} size={trade_size:.6f} "
                    f"planned_risk=${sizing.planned_risk:.4f} "
                    f"notional=${sizing.notional:.2f}"
                )
                result = await execute_intent(platform, intent, size=trade_size)
                fill_risk = None
                if result.accepted:
                    _safe_log(f"  ✓ accepted: deal_id={result.deal_id}")
                    if result.fill_price is not None:
                        actual_size = result.size if result.size else trade_size
                        fill_risk = (
                            abs(float(result.fill_price) - intent.stop_loss)
                            * actual_size
                        )
                        _safe_log(
                            f"  risk: planned=${sizing.planned_risk:.4f} "
                            f"fill=${fill_risk:.4f}"
                        )
                    confirmation_error = result.raw.get("_confirmation_error")
                    if confirmation_error:
                        _safe_log(
                            f"  ⚠ confirms unresolved for {intent.pair}; "
                            f"using provisional deal_id={result.deal_id}: "
                            f"{confirmation_error}"
                        )
                    if cluster is not None:
                        opened_this_cycle.append((cluster, intent.direction))
                    opened_this_cycle_count += 1
                else:
                    _safe_log(f"  ⛔ rejected: {result.error}")
                    if "LONG_ONLY" in (result.error or ""):
                        long_only_pairs.add(intent.pair)
                # Journal — never crashes the loop on failure
                from app.spot_trading.journal import record as _journal_record
                # result.size carries the broker's clamping/increment
                # rounding, so it is the size actually opened — journalling
                # trade_size would record a position that never existed.
                _journal_record(
                    intent, result,
                    platform=platform_name,
                    paper_mode=platform.paper_trade_only,
                    size=result.size if result.size else trade_size,
                    sizing_reference_price=prepared.reference_price,
                    planned_risk=sizing.planned_risk,
                    fill_risk=fill_risk,
                )

            # Heartbeat: prove the loop is alive even on quiet cycles.
            # Always log on the first cycle so the operator gets quick
            # confirmation polling actually happened.
            now_utc = datetime.now(timezone.utc)
            if (last_heartbeat_at is None
                    or (now_utc - last_heartbeat_at).total_seconds()
                    >= heartbeat_seconds):
                cutoff_24h = now_utc - timedelta(hours=24)
                signals_24h = sum(1 for t in issued_log if t >= cutoff_24h)
                _safe_log(
                    f"heartbeat: scanned {len(active)} pairs, "
                    f"{len(positions)} open positions, "
                    f"{signals_24h} signals in last 24h"
                )
                last_heartbeat_at = now_utc
                # Refresh the static dashboard on each heartbeat so it
                # stays current whenever hurz runs — fire-and-forget, and
                # never let a dashboard hiccup disturb the trading loop.
                try:
                    repo_root = os.path.dirname(os.path.dirname(
                        os.path.dirname(os.path.abspath(__file__))))
                    subprocess.Popen(
                        [sys.executable,
                         os.path.join(repo_root, "scripts",
                                      "generate_dashboard.py"), "all"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        cwd=repo_root,
                    )
                except Exception:
                    pass

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
                break
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        # Propagated by an outer task cancellation — clean up before
        # re-raising so the platform session closes properly.
        pass
    finally:
        try:
            await platform.disconnect()
        except Exception:
            pass
        _safe_log(f"loop stopped, {platform.name} disconnected")

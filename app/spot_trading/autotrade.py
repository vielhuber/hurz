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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.platforms import (
    Platform, get_platform, PlatformError, PlatformAuthError,
    PaperTradeOnlyError, OrderResult, Position, Bar,
)
from app.strategies import get_strategy, add_indicators


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


# ---------------- bar resolution mapping ----------------

# Map our internal resolution string → minutes per bar (used to
# decide how many bars to pull and how often to poll).
_RES_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}


def _safe_log(message: str) -> None:
    """Lightweight logger — keeps the spot-trading subsystem
    independent of the legacy `utils.print` singleton."""
    print(f"[spot] {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {message}")


def _bars_to_df(bars: List[Bar]) -> pd.DataFrame:
    return pd.DataFrame([{
        "timestamp": b.timestamp,
        "open": b.open, "high": b.high, "low": b.low, "close": b.close,
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
) -> Optional[TradeIntent]:
    """Run a single strategy-evaluation cycle on `pair`. Returns a
    `TradeIntent` if the latest bar produced a signal, else None.

    `apply_venue_min=True` (used by the live loop) expands the ATR-
    derived stop to the venue's minimum if necessary, keeping R:R
    constant by stretching TP proportionally. Backtest mirrors the
    same logic via spot_backtest._simulate_trades(platform=...).
    Without this, FX-1h signals on Capital are virtually unhandelable
    (ATR ~0.0007 vs 1% minimum = 0.01)."""
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
    # Regime filter: block a signal whose strategy style is wrong for
    # the current regime (mean-reversion in a trend, trend-following in
    # a range). Same policy the backtest applies, so selection and live
    # stay consistent. Fails open on missing ADX.
    from app.spot_trading.regime import gate as _regime_gate
    decision = _regime_gate(strategy_name, df, last_idx)
    if decision.blocked:
        _safe_log(f"⛓ regime-veto {pair} {strategy_name}: {decision.reason}")
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
    return TradeIntent(
        pair=pair, direction=sig.direction,
        entry_price=entry_price, stop_loss=sl, take_profit=tp,
        strategy=strategy_name, confidence=sig.confidence,
        bar_time=last_row["timestamp"],
    )


async def execute_intent(
    platform: Platform, intent: TradeIntent, size: float,
) -> OrderResult:
    """Hand the intent to the platform. Errors are returned in the
    OrderResult — the loop should not crash on a single bad order."""
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


_BAR_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}

# Min seconds between stale-exit close attempts on the same position.
_STALE_RETRY_COOLDOWN = 1800

# Per-strategy risk:reward override. A strategy not listed here uses the
# loop's global `rr`. Used to forward-test higher-R:R trend variants in
# parallel — donchian_breakout's fixed 1:1.5 TP measurably caps its
# winners (backtest 2026-07-08: BTCUSD E[R] +0.33 at 1:1.5 vs +0.93 at
# 1:3.5). donchian_breakout itself is intentionally NOT listed, so it
# keeps the global rr and stays untouched; the _v2 / _v3 clones share its
# entry logic but exit at a wider target.
_STRATEGY_RR = {
    "donchian_breakout_v2": 2.5,
    "donchian_breakout_v3": 3.5,
    # Far backstop only — donchian_trail's real exit is the break-even +
    # ATR trailing stop managed in the loop (see the trailing-stop exit
    # block in run_loop). The wide TP just caps the tail if the trail
    # somehow never triggers.
    "donchian_trail": 5.0,
}

# donchian_trail exit parameters. The trail arms once price has moved
# `_TRAIL_ACTIVATION_R` × initial-risk in favor, then rides `_TRAIL_ATR_MULT`
# × ATR behind the best excursion, never giving back below break-even.
# Env-overridable for forward-test tuning without a code change.
_TRAIL_ACTIVATION_R = float(os.getenv("HURZ_TRAIL_ACTIVATION_R", "1.0"))
_TRAIL_ATR_MULT = float(os.getenv("HURZ_TRAIL_ATR_MULT", "2.0"))

# Per-strategy bar length for the stale-exit clock. The journal has no
# resolution column, so the 4h book's strategy names carry it: a 4h position
# must get 24×4h = 96h before the stale exit fires, not 24×(loop's 1h).
_STRATEGY_BAR_SECONDS = {
    "donchian_breakout_4h": 14400,
    "momentum_4h": 14400,
    "turtle_breakout_4h": 14400,
}

# Per-strategy override for the stale-exit leash. donchian_trail's whole
# edge is riding multi-day trends behind its ATR trail — the default
# 24-bar hold would force-close every ride before the trail can pay
# (its 1:5 TP is a far backstop, not the expected exit).
_STRATEGY_MAX_HOLD_BARS = {
    "donchian_trail": 240,
}


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
    "DE40": "indices", "FR40": "indices", "UK100": "indices",
    "US30": "indices", "US500": "indices", "US100": "indices",
    "HK50": "indices", "J225": "indices", "AU200": "indices",
    "GOLD": "metals", "SILVER": "metals", "PALLADIUM": "metals",
    "OIL_BRENT": "energy", "OIL_CRUDE": "energy",
}
_CLUSTER_DIR_CAP = int(os.getenv("HURZ_CLUSTER_DIRECTION_CAP", "3"))


def _bar_seconds(resolution: str) -> int:
    return _BAR_SECONDS.get(resolution, 3600)


async def _resolve_closed_trade(
    platform: Platform, journal_row: Dict,
) -> Optional[Dict]:
    """For a position that's no longer in the broker's open list, walk
    the bars between its entry and now to detect which of (SL, TP) was
    crossed first. Returns dict with exit_price, exit_time, outcome,
    realized_pnl — or None if no bars are available.

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
    entry_price = float(journal_row["entry_price"])
    sl = float(journal_row["stop_loss"])
    tp = float(journal_row["take_profit"])
    size = float(journal_row["size"]) if journal_row.get("size") else 1.0

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
                                    entry_price, sl, direction, size)
        if hit_tp:
            return _closure_payload(b.timestamp, tp, "win",
                                    entry_price, tp, direction, size)

    # No SL/TP cross detected in OHLC. Two real possibilities:
    #   1. Sub-bar SL/TP touch on bid/ask that didn't print on the
    #      1h OHLC mid (typical for fast crypto/FX wicks).
    #   2. Actual external close (operator, margin, broker action).
    # Try to recover the truth from the venue's activity log via the
    # stored deal_id. Capital reports the real close level and the
    # trigger source (SL/TP/USER/SYSTEM). If that lookup succeeds,
    # promote the outcome to loss/win accordingly; otherwise keep
    # the conservative "manual" with last-bar close as the estimate.
    last = bars[-1]
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
                entry_price, fill["close_level"], direction, size,
            )
    return _closure_payload(
        last.timestamp, last.close, "manual",
        entry_price, last.close, direction, size,
    )


def _closure_payload(exit_time: datetime, exit_price: float, outcome: str,
                     entry_price: float, fill_price: float,
                     direction: int, size: float) -> Dict:
    realized = (fill_price - entry_price) * direction * size
    return {
        "exit_time": exit_time,
        "exit_price": exit_price,
        "outcome": outcome,
        "realized_pnl": realized,
    }


async def run_loop(
    *,
    platform_name: str,
    strategy_name: str,
    resolution: str = "1h",
    stop_atr: float = 1.0,
    rr: float = 1.5,
    poll_seconds: int = 60,
    size: float = 1.0,
    lookback_bars: int = 240,
    heartbeat_seconds: int = 3600,
    stop_event: Optional[asyncio.Event] = None,
    max_concurrent: Optional[int] = None,
    notional_per_trade: Optional[float] = None,
) -> None:
    """Long-running coroutine. Polls active pairs, fires signals,
    places orders. Exit cleanly on `stop_event.set()`.

    `max_concurrent`: hard cap on simultaneous open positions. New
    signals are skipped (and journaled) once the cap is reached. None
    = no cap (legacy behavior). Useful on platforms where correlated
    strategies fire identical-direction signals across pairs and would
    otherwise produce a single concentrated bet disguised as N trades.

    `notional_per_trade`: when set, the platform-level order size is
    recomputed per-pair as `notional_per_trade / entry_price` so each
    trade carries roughly the same USD exposure regardless of the
    pair's price level. None = use the static `size` argument (legacy
    behavior — fine for venues where 1 lot has a venue-defined notional,
    breaks for Kraken Futures perpetuals where size=1 contract on BTC
    is $80k while size=1 on DOGE is $0.11)."""
    import os as _os
    if max_concurrent is None and _os.getenv("HURZ_MAX_CONCURRENT"):
        try:
            max_concurrent = int(_os.environ["HURZ_MAX_CONCURRENT"])
        except ValueError:
            pass
    if notional_per_trade is None and _os.getenv("HURZ_NOTIONAL_PER_TRADE"):
        try:
            notional_per_trade = float(_os.environ["HURZ_NOTIONAL_PER_TRADE"])
        except ValueError:
            pass
    platform = get_platform(platform_name)
    await platform.connect()
    _safe_log(f"connected to {platform.name} (demo={platform.demo}, "
              f"paper_trade_only={platform.paper_trade_only})")
    if max_concurrent is not None:
        _safe_log(f"  max_concurrent_positions={max_concurrent}")
    if notional_per_trade is not None:
        _safe_log(f"  notional_per_trade=${notional_per_trade:.2f}")
    from app.spot_trading.regime import summary as _regime_summary
    _safe_log(f"  regime_filter={_regime_summary()}")
    if stop_event is None:
        stop_event = asyncio.Event()

    from app.spot_trading.pair_selector import load_active_pairs
    from app.spot_trading import regime
    from app.spot_trading.regime import adx_at as _regime_adx_at
    from app.spot_trading.journal import (
        list_unresolved_open as _list_unresolved_open,
    )

    # Per-(pair, strategy, bar_time) dedup: an in-flight bar would
    # otherwise re-emit the same signal on every poll, spamming the
    # broker. Keyed on (pair, strategy) so two different strategies
    # tracking the same pair can each contribute (e.g. rsi_mr AND
    # bollinger_rev both watching EURUSD count as independent voters).
    issued_intents: Dict[tuple, datetime] = {}
    # Circuit breaker: hard daily cap on signals issued by this loop.
    # Defends against a runaway scenario where a strategy bug or
    # corrupted active_pairs.json fires hundreds of intents in a day.
    # Reset rolling 24h. The cap is generous given the backtest
    # expectation of ~10-15 signals/day; anything above 100 is
    # almost certainly a bug.
    issued_log: List[datetime] = []
    daily_cap = 100
    # Cooldown for failed stale-exit closes: a position the broker
    # refuses to close (e.g. FX market shut on the weekend → HTTP 400)
    # must not be retried every poll cycle. Keyed by journal deal_id →
    # last-attempt time; retried at most once per _STALE_RETRY_COOLDOWN.
    stale_exit_attempts: Dict[str, datetime] = {}

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
    # Run a one-time reconciliation against the journal: any spot_trades
    # row marked accepted with no exit_time, whose deal_id is not in
    # the broker's current open list, must have been closed during a
    # previous bot lifetime (or before exit-tracking landed). Resolve
    # those once at startup so they don't stay open in the DB forever.
    initial_reconcile_pending = True

    try:
        while not stop_event.is_set():
            active = load_active_pairs(platform=platform_name)
            # Filter to entries that match this loop's platform;
            # otherwise we'd try to fetch a Capital.com epic on Kraken.
            active = [p for p in active if p.get("platform") == platform_name]
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
            if initial_reconcile_pending:
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
                    update_deal_id(row["id"], match.id)
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
                        f"pnl={payload['realized_pnl']:+.4f}"
                    )
                initial_reconcile_pending = False
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
                            f"pnl={payload['realized_pnl']:+.4f}"
                        )
            prev_deal_ids = current_deal_ids

            # Map journal's stored deal_id (= workingOrderId on Capital) to
            # the broker-side position id required by close_position. Kraken
            # returns p.id only — that's already the close target. Shared by
            # the stale-exit, regime-flip-exit and trail-exit blocks below.
            close_id_by_journal_id: Dict[str, str] = {}
            for p in positions:
                if p.id:
                    close_id_by_journal_id[p.id] = p.id
                pmeta = (p.meta or {}).get("position") or {}
                woi = pmeta.get("workingOrderId")
                if woi and p.id:
                    close_id_by_journal_id[woi] = p.id

            # Stale-position exit: mean-revert and breakout setups have a
            # bounded expected holding period (max N×bar). When a trade
            # exceeds it without hitting SL/TP, the original thesis is
            # already invalidated — close at market to free the per-pair
            # slot and stop tying up margin on dead conviction.
            # Configurable via HURZ_MAX_HOLD_BARS (default 24 bars).
            try:
                max_hold_bars_env = _os.getenv("HURZ_MAX_HOLD_BARS")
                max_hold_bars = int(max_hold_bars_env) if max_hold_bars_env else 24
            except ValueError:
                max_hold_bars = 24
            if max_hold_bars > 0:
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
                    hold_bars = _STRATEGY_MAX_HOLD_BARS.get(
                        strategy_name, max_hold_bars)
                    max_hold_seconds = hold_bars * _STRATEGY_BAR_SECONDS.get(
                        strategy_name, _bar_seconds(resolution))
                    age_seconds = (now_utc - created_at).total_seconds()
                    if age_seconds < max_hold_seconds:
                        continue
                    journal_deal_id = row["deal_id"]
                    # Back off positions the broker just refused to close
                    # (e.g. weekend FX → HTTP 400) instead of retrying
                    # every cycle and flooding the log.
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
            # touched; the core book is untouched. A far backstop TP
            # (_STRATEGY_RR) caps the tail if the trail never arms. Shares
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
            for entry in active:
                pair = entry.get("pair")
                entry_strategy = entry.get("strategy") or strategy_name
                entry_resolution = entry.get("resolution") or resolution
                if not pair:
                    continue
                if _has_open_position(positions, pair):
                    continue
                entry_rr = _STRATEGY_RR.get(entry_strategy, rr)
                try:
                    intent = await evaluate_pair(
                        platform, pair,
                        strategy_name=entry_strategy,
                        resolution=entry_resolution,
                        stop_atr=stop_atr, rr=entry_rr,
                        lookback_bars=lookback_bars,
                        apply_venue_min=True,
                    )
                except PlatformError as exc:
                    _safe_log(f"⚠ {pair}: evaluate failed: {exc}")
                    continue
                if intent is None:
                    continue
                # Dedup: skip if we've already issued an intent for this
                # (pair, strategy, bar_time). Resets when the bar closes
                # — the new bar gets a fresh chance from each strategy.
                # MUST run before the venue-min-stop guard, otherwise a
                # rejected signal re-evaluates and re-journals every
                # 60-second poll until the bar closes (observed: 67
                # journal-spam entries in 3h on BTCUSD + ETHUSD).
                dedup_key = (pair, intent.strategy)
                last_seen = issued_intents.get(dedup_key)
                if last_seen is not None and last_seen >= intent.bar_time:
                    continue
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
                    _safe_log(
                        f"⏭ {intent.pair}: broker is LONG_ONLY — "
                        f"skipping short ({intent.strategy})"
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
                        _safe_log(
                            f"⏭ {intent.pair}: cluster '{cluster}' "
                            f"dir={intent.direction:+d} cap {_CLUSTER_DIR_CAP} "
                            f"reached ({same_dir} open) — skipping "
                            f"({intent.strategy})"
                        )
                        issued_intents[dedup_key] = intent.bar_time
                        continue

                # Concurrent-position cap. Defended against the
                # "all 5 active pairs go long on the same 4h close"
                # pattern observed when a mean-reverter sees a correlated
                # selloff: without the cap, that's one concentrated bet
                # dressed as N independent trades.
                if max_concurrent is not None and len(positions) >= max_concurrent:
                    _safe_log(
                        f"⏭ {intent.pair}: max_concurrent={max_concurrent} "
                        f"reached ({len(positions)} open) — skipping ({intent.strategy})"
                    )
                    issued_intents[dedup_key] = intent.bar_time
                    continue
                # Circuit breaker: prune log to the rolling 24h window
                # and bail if we'd exceed the daily cap.
                now_utc = datetime.now(timezone.utc)
                cutoff = now_utc - timedelta(hours=24)
                issued_log = [t for t in issued_log if t >= cutoff]
                if len(issued_log) >= daily_cap:
                    _safe_log(
                        f"⛔ daily cap of {daily_cap} signals reached — "
                        f"halting until rolling 24h window decays. "
                        f"This is a safety circuit breaker; investigate "
                        f"if this fires before live mode is enabled."
                    )
                    continue
                issued_intents[dedup_key] = intent.bar_time
                issued_log.append(now_utc)
                # Notional-normalized sizing — keeps USD exposure roughly
                # constant across pairs with very different price levels.
                # Falls back to the static `size` arg when not configured.
                trade_size = size
                if notional_per_trade is not None and intent.entry_price > 0:
                    trade_size = notional_per_trade / intent.entry_price
                _safe_log(
                    f"signal {pair} dir={intent.direction:+d} "
                    f"entry={intent.entry_price:.5f} "
                    f"sl={intent.stop_loss:.5f} tp={intent.take_profit:.5f} "
                    f"strat={intent.strategy} size={trade_size:.6f}"
                )
                result = await execute_intent(platform, intent, size=trade_size)
                if result.accepted:
                    _safe_log(f"  ✓ accepted: deal_id={result.deal_id}")
                    if cluster is not None:
                        opened_this_cycle.append((cluster, intent.direction))
                else:
                    _safe_log(f"  ⛔ rejected: {result.error}")
                    if "LONG_ONLY" in (result.error or ""):
                        long_only_pairs.add(intent.pair)
                # Journal — never crashes the loop on failure
                from app.spot_trading.journal import record as _journal_record
                _journal_record(
                    intent, result,
                    platform=platform_name,
                    paper_mode=platform.paper_trade_only,
                    size=trade_size,
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

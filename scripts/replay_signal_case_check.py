"""Case check: the replay's decisions against the bot's, signal by signal, 2026-10-01 to 2026-10-24.

The journal holds every signal the bot evaluated on its listed
combinations, entered or refused with a reason (section 439). This script
extends the bar cache past 2026-09-20 for the instruments those
combinations trade (one paced, sequential broker session, run on a
weekend; written to `HURZ_EXTENDED_CACHE`, never to the runtime cache)
and lets the replay's rules decide the same signals.

Fixed before any outcome was seen:
  - window: signal bars from 2026-10-01 03:00 to 2026-10-24 00:00 UTC
    (2026-10-22 08:00 in section 443); combinations: those the journal
    evaluated in the window plus today's active list;
  - replay decision per signal: the regime gate (ADX), the short block,
    the live entry terms (`trade_terms`: 2-ATR stop, venue minimum,
    3-ATR floor, cost ceiling), else entry; 4h signals on the broker's
    native 4h bars (section 446, fetched into `HURZ_EXTENDED_4H_CACHE`);
    a signal whose order falls on the bot's bank-holiday pause is
    "holiday pause" (section 453), which the bot never journals;
  - matching: same instrument, strategy, signal bar and direction;
  - tolerances: a decision that differs only because the ADX lies within
    1.0 of the gate (the bot computes it on its last 240 bars, the replay
    on the whole history) counts as explained; entry price within 0.05 %
    of the journal's signal price;
  - everything else is listed as unexplained.
Read-only on the journal; nothing here trades or changes the bot.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.regime import gate, trend_floor
from app.spot_trading.trading_blocks import direction_blocked
from app.strategies import get_strategy
import scripts.efficiency_weighted_selection as ews
from scripts.additional_indices import frames_for
import scripts.four_hour_stream as fhs
from scripts.rule_period_calibration import EXTENDED_CACHE, merge_bars

WINDOW = (np.datetime64("2026-10-01T03:00"), np.datetime64("2026-10-24T00:00"))
NATIVE_CACHE = os.environ.get("HURZ_EXTENDED_4H_CACHE", "/tmp/hurz-extended-4h")
ADX_TOLERANCE = 1.0
PRICE_TOLERANCE = 0.0005


async def extend(pairs):
    """Cached bars of `pairs` extended to now, one paced session, into EXTENDED_CACHE."""
    os.makedirs(EXTENDED_CACHE, exist_ok=True)
    now = datetime.now(timezone.utc)
    # A session per run is a login; the broker answers a second one in
    # quick succession with 429, so none is opened when nothing is missing.
    missing = [pair for pair in pairs if not os.path.exists(os.path.join(EXTENDED_CACHE, f"{pair}.json"))]
    if not missing:
        return
    clear_cache(); platform = get_platform(ews.PLAT); await platform.connect()
    try:
        for pair in missing:
            target = os.path.join(EXTENDED_CACHE, f"{pair}.json")
            cached = json.load(open(ews.cache_path(pair)))
            last = datetime.fromisoformat(cached[-1][0])
            last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            bars = await ews.fetch_paced(platform, pair, (now - last).days + 1, 0) or []
            fresh = [[b.timestamp.isoformat(), b.open, b.high, b.low, b.close, getattr(b, "volume", 0.0)]
                     for b in bars]
            merged = merge_bars(cached, fresh)
            json.dump(merged, open(target, "w"))
            print(f"{pair}: +{len(merged) - len(cached)} bars to {merged[-1][0]}", flush=True)
    finally:
        await platform.disconnect()


def decide(df, index, strategy, pair, direction, meta, floor, bar=np.timedelta64(1, "h")):
    """The replay's decision on one signal, in the bot's order: (kind, ADX, entry price)."""
    regime = gate(strategy, df, index)
    entry = float(df["close"].values[index])
    if ews.guarded_day(df["timestamp"].values[index] + bar):
        return "holiday pause", regime.adx, entry
    if regime.blocked:
        return "adx", regime.adx, entry
    if direction_blocked(pair, direction):
        return "short-blocked", regime.adx, entry
    if ews.trade_terms(df, index, pair, meta, floor) is None:
        return "atr floor or cost", regime.adx, entry
    return "enter", regime.adx, entry


def replay_decisions(frames, combos, meta, floor):
    out = {}
    four_hour = fhs.native_frames(sorted({pair for strategy, pair in combos if strategy.endswith("_4h")}),
                                  WINDOW[1].astype(datetime))
    for strategy, pair in combos:
        base = strategy[:-3] if strategy.endswith("_4h") else strategy
        df = four_hour.get(pair) if strategy.endswith("_4h") else frames.get(pair)
        if df is None:
            continue
        ts = df["timestamp"].values
        for signal in get_strategy(base)(df, {}):
            bar = np.datetime64(ts[signal.index], "m")
            if not WINDOW[0] <= bar < WINDOW[1]:
                continue
            width = np.timedelta64(4 if strategy.endswith("_4h") else 1, "h")
            kind, adx, entry = decide(df, signal.index, base, pair, signal.direction, meta, floor, width)
            out[(pair, strategy, bar, signal.direction)] = (kind, adx, entry)
    return out


def live_decisions(path):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    rows = connection.execute(
        """SELECT pair, strategy, bar_time, direction, accepted, error, entry_price, entry_adx
           FROM spot_trades WHERE platform = 'capital_com' AND paper_mode = 0
           AND created_at >= '2026-10-01 02:02:00' ORDER BY created_at""").fetchall()
    connection.close()
    out = {}
    for pair, strategy, bar_time, direction, accepted, error, entry, adx in rows:
        bar = np.datetime64(str(bar_time)[:16].replace(" ", "T"), "m")
        if not WINDOW[0] <= bar < WINDOW[1]:
            continue
        if accepted:
            kind = "enter"
        elif "ADX" in (error or ""):
            kind = "adx"
            adx = float((error or "").rsplit("got", 1)[1])
        elif "ATR floor" in (error or ""):
            kind = "atr floor or cost"
        else:
            kind = error or "refused"
        out[(pair, strategy, bar, direction)] = (kind, adx, entry)
    return out


async def main():
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    live = live_decisions(path)
    active = json.load(open(os.path.join("data", "active_pairs.capital_com.json")))["pairs"]
    combos = sorted({(strategy, pair) for pair, strategy, _, _ in live}
                    | {(row["strategy"], row["pair"]) for row in active})
    pairs = sorted({pair for _, pair in combos})
    print(f"window=[{WINDOW[0]}, {WINDOW[1]}) combinations={len(combos)} instruments={pairs}", flush=True)
    await extend(pairs)
    fhs.NATIVE_4H_CACHE = NATIVE_CACHE
    await fhs.fetch_native_4h(sorted({pair for strategy, pair in combos if strategy.endswith("_4h")}),
                              datetime(2025, 6, 1, tzinfo=timezone.utc), datetime.now(timezone.utc))
    meta = json.load(open(ews.META_CACHE))
    ews.BAR_CACHE = EXTENDED_CACHE
    ews.PAIRS = pairs
    frames = frames_for(await ews.load_history(), meta)
    floor = _min_stop_atr_multiple()
    replay = replay_decisions(frames, combos, meta, floor)
    same, explained, unexplained = [], [], []
    for key in sorted(set(live) | set(replay), key=lambda k: (k[2], k[0])):
        a, b = live.get(key), replay.get(key)
        label = f"{key[0]} {key[1]} bar {key[2]} dir {key[3]:+d}"
        if a is None and b[0] == "holiday pause":
            explained.append(f"{label}: replay holiday pause, the bot was idle")
            continue
        if a is None or b is None:
            side = "journal only" if b is None else "replay only"
            seen = a or b
            near = seen[1] is not None and abs(seen[1] - trend_floor(key[1].replace("_4h", ""))) <= ADX_TOLERANCE
            unexplained.append(f"{side}: {label} ({seen[0]}, ADX {seen[1]})"
                               + (" — near the gate" if near else ""))
            continue
        price_gap = abs(a[2] - b[2]) / a[2] if a[2] else 0.0
        if a[0] == b[0] and price_gap <= PRICE_TOLERANCE:
            same.append(label)
            continue
        floor_adx = trend_floor(key[1].replace("_4h", ""))
        if a[0] != b[0] and "adx" in (a[0], b[0]) and b[1] is not None and abs(b[1] - floor_adx) <= ADX_TOLERANCE:
            explained.append(f"{label}: journal {a[0]} (ADX {a[1]}), replay {b[0]} (ADX {b[1]:.1f}) — ADX within {ADX_TOLERANCE} of the gate")
            continue
        unexplained.append(f"{label}: journal {a[0]} (ADX {a[1]}, entry {a[2]}), replay {b[0]} "
                           f"(ADX {b[1] if b[1] is None else round(b[1], 1)}, entry {b[2]}), price gap {price_gap:.4%}")
    print(f"journal signals={len(live)} replay signals={len(replay)} same={len(same)} "
          f"explained={len(explained)} unexplained={len(unexplained)}")
    for line in explained + unexplained:
        print(line)


if __name__ == "__main__":
    asyncio.run(main())

"""Preregistered new income source: eight more liquid stock indices.

Live since the trend book started, indices book the best R of any class
(+0.21 R over 20 closes against -0.04 to +0.05 R elsewhere), yet the
universe holds only nine of them. Capital.com quotes eight further equity
indices at spreads the cost ceiling admits. The candidate adds them to the
weekly walk-forward; everything else is the current book.

Fixed before any outcome was seen:
  - candidates NL25, RTY, SW20, IT40, SP35, CN50, SG25, NYFANG; DXY is no
    equity index and AU200AU duplicates the blocked AU200;
  - per-side cost is half the broker spread quoted 2026-09-24 18:55 UTC,
    never below the 0.01 % default the other indices carry;
  - all eight join the risk_on correlation cluster like every index;
  - pins, vetoes, stops, sizing and every cap stay unchanged.
Adoption: all four OOS samples better and pooled paired daily t > +2.
Missing history (HTTP 404 before listing) counts as an empty page.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.base import PlatformAPIError
from app.platforms.registry import clear_cache
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple, _CORRELATION_CLUSTERS
import scripts.efficiency_weighted_selection as base
import scripts.pin_eligibility as pe
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    META_CACHE, RANK_DAYS, all_signals, cache_path, load_history, t_stat, to_frame,
)
from scripts.rank_holding_time import daily_series
from scripts.unscored_live_instruments import instrument_meta

SPREAD_PERCENT = {"NL25": 0.0090, "RTY": 0.0176, "SW20": 0.0208, "IT40": 0.0289,
                  "SP35": 0.0812, "CN50": 0.0697, "SG25": 0.0451, "NYFANG": 0.0093}
CANDIDATES = list(SPREAD_PERCENT)
FEES = {pair: max(percent / 100 / 2, 0.0001) for pair, percent in SPREAD_PERCENT.items()}
PAGE_PAUSE = float(os.environ.get("HURZ_PAGE_PAUSE") or 1.5)


def fee_for(original):
    return lambda platform, pair: FEES[pair] if pair in FEES else original(platform, pair)


async def fetch_page(platform, pair, start, end):
    for attempt in range(6):
        try:
            return await platform.fetch_history(pair, from_ts=start, to_ts=end, resolution="1h")
        except PlatformAPIError as error:
            if error.status == 404:
                return []
            print(f"{pair} page {start:%Y-%m-%d} failed ({error.status}), retry {attempt + 1}", flush=True)
            await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{pair}: page {start:%Y-%m-%d} failed six times")


async def ensure_candidates():
    meta = json.load(open(META_CACHE))
    need_bars = [pair for pair in CANDIDATES if not os.path.exists(cache_path(pair))]
    need_meta = [pair for pair in CANDIDATES if pair not in meta]
    if not need_bars and not need_meta:
        return meta
    clear_cache(); platform = get_platform(base.PLAT); await platform.connect()
    try:
        for pair in need_bars:
            now = datetime.now(timezone.utc)
            cursor = now - timedelta(days=base.SPAN)
            bars = {}
            while cursor < now:
                end = min(cursor + timedelta(days=base.PAGE_DAYS), now)
                for bar in await fetch_page(platform, pair, cursor, end):
                    bars[bar.timestamp] = bar
                cursor = end
                await asyncio.sleep(PAGE_PAUSE)
            rows = [[b.timestamp.isoformat(), b.open, b.high, b.low, b.close, b.volume]
                    for _, b in sorted(bars.items())]
            json.dump(rows, open(cache_path(pair), "w"))
            print(f"{pair}: {len(rows)} bars cached", flush=True)
        for pair in need_meta:
            rows = json.load(open(cache_path(pair)))
            if not rows: continue
            found = await instrument_meta(platform, pair, float(rows[-1][4]))
            if found is not None:
                meta[pair] = found
            await asyncio.sleep(1.0)
    finally:
        await platform.disconnect()
    json.dump(meta, open(META_CACHE, "w"))
    return meta


def frames_for(raw, meta):
    return {pair: add_indicators(to_frame(rows)) for pair, rows in raw.items()
            if pair in meta and len(rows) >= 2000}


async def main():
    meta = await ensure_candidates()
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    base._fee_for = fee_for(base._fee_for)
    _CORRELATION_CLUSTERS.update({pair: "risk_on" for pair in CANDIDATES})
    raw = await load_history()
    frames = {"live": frames_for(raw, meta)}
    for pair in CANDIDATES:
        raw[pair] = [(datetime.fromisoformat(t), o, h, l, c, v)
                     for t, o, h, l, c, v in json.load(open(cache_path(pair)))]
    frames["candidate"] = frames_for(raw, meta)
    added = sorted(set(frames["candidate"]) - set(frames["live"]))
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames["live"]), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signals = {arm: all_signals(arm_frames, _min_stop_atr_multiple(), meta)
               for arm, arm_frames in frames.items()}
    timestamps = {arm: np.array([trade["ts"] for trade in rows]) for arm, rows in signals.items()}
    start = np.datetime64(timestamps["live"].min(), "D") + np.timedelta64(RANK_DAYS, "D")
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames["live"].values())
    days = np.arange(start, end)
    print(f"instruments live={len(frames['live'])} candidate={len(frames['candidate'])} "
          f"added={','.join(added)} signals live={len(signals['live'])} "
          f"candidate={len(signals['candidate'])} pins={len(pins)} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}", flush=True)
    for pair in added:
        print(f"{pair}: bars={len(frames['candidate'][pair])} "
              f"from={np.datetime64(frames['candidate'][pair]['timestamp'].values[0], 'D')} "
              f"fee_per_side={FEES[pair]:.6f} step={meta[pair]['step']:g} "
              f"min={meta[pair]['min']:g} rate={meta[pair]['rate']:.6g}", flush=True)
    booked = {arm: [] for arm in signals}
    for arm, rows in signals.items():
        state = {"open": {}, "pair": {}}
        cut = start
        while cut < end:
            following = min(cut + np.timedelta64(7, "D"), end)
            low = int(np.searchsorted(timestamps[arm], cut - np.timedelta64(RANK_DAYS, "D")))
            high = int(np.searchsorted(timestamps[arm], cut))
            training = [trade for trade in rows[low:high] if trade["exit_ts"] < cut]
            order = active_order(training, pins, reserved, pin_order)
            window = rows[high:int(np.searchsorted(timestamps[arm], following))]
            admit(prioritize(window, order, False), set(order), "live", None, state, booked[arm], [])
            cut = following
    series = {arm: daily_series(trades, days) for arm, trades in booked.items()}
    for arm, trades in booked.items():
        closed = [trade for trade in trades if np.datetime64(trade["exit_ts"], "D") < end]
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} "
              f"mean_planned_risk={np.mean([trade['risk'] for trade in closed]):.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    for pair in added:
        taken = [trade for trade in booked["candidate"] if trade["pair"] == pair
                 and np.datetime64(trade["exit_ts"], "D") < end]
        print(f"{pair}: closes={len(taken)} usd={sum(trade['usd'] for trade in taken):+.4f}")
    delta = series["candidate"] - series["live"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"days={selected.sum()} live={series['live'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

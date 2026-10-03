"""Live against replay: the rule period since 2026-09-10, on a bar cache extended to today.

Section 337 matched live closes to the replay from 2026-08-01 to the end of
the bar cache and found execution in line (residual +0.041 R, t +1.15).
The cache still ends 2026-09-20, ten days into the rule period that began
on 2026-09-10; since then the live book also gained the strategy veto's
retirements (2026-09-25, 2026-10-01). This run extends a separate copy of
the cache to today, so the seven-year reference stays comparable, and
repeats section 337's execution split on the rule period, strategy by
strategy, with the account's overnight financing beside it.

Fixed before any outcome was seen:
  - window: bar_time from 2026-09-10 to the extended cache's end; the
    three hourly strategies, fills and planned risk journaled, as in
    section 337;
  - calibration candidate: add the pooled per-close residual (live result
    from the fill minus the replay's net result) to every replay trade,
    adopted only if its |t| exceeds 2; the change of the section 357
    reference is reported either way;
  - no other window, no diagnostic arm.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.spot_trading.autotrade import _min_stop_atr_multiple
import scripts.efficiency_weighted_selection as ews
import scripts.live_replay_calibration as lrc
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.rank_holding_time import daily_series

WINDOW_START = np.datetime64("2026-09-10")
EXTENDED_CACHE = os.environ.get("HURZ_EXTENDED_CACHE", "/tmp/hurz-extended-bars")


def merge_bars(cached, fresh):
    """Cached rows followed by the fresh ones that are newer than the last cached bar."""
    last = cached[-1][0]
    return cached + [row for row in fresh if row[0] > last]


async def extend_cache():
    os.makedirs(EXTENDED_CACHE, exist_ok=True)
    now = datetime.now(timezone.utc)
    clear_cache(); platform = get_platform(ews.PLAT); await platform.connect()
    try:
        for pair in ews.PAIRS:
            source = ews.cache_path(pair)
            target = os.path.join(EXTENDED_CACHE, os.path.basename(source))
            if not os.path.exists(source):
                continue
            cached = json.load(open(source))
            last = datetime.fromisoformat(cached[-1][0])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days_from = (now - last).days + 1
            bars = await ews.fetch_paced(platform, pair, days_from, 0) or []
            fresh = [[b.timestamp.isoformat(), b.open, b.high, b.low, b.close, getattr(b, "volume", 0.0)]
                     for b in bars]
            merged = merge_bars(cached, fresh)
            json.dump(merged, open(target, "w"))
            print(f"{pair}: +{len(merged) - len(cached)} bars to {merged[-1][0]}", flush=True)
    finally:
        await platform.disconnect()


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    await extend_cache()
    ews.BAR_CACHE = EXTENDED_CACHE
    frames = frames_for(await ews.load_history(), meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    lrc.WINDOW_START = WINDOW_START
    closes = lrc.journal_closes(end)
    matched, unmatched = lrc.decompose(closes, frames)
    print(f"window=[{WINDOW_START}, {end}) live_closes={len(closes)} matched={len(matched)} "
          f"unmatched={len(unmatched)} ({', '.join(sorted({row['pair'] for row in unmatched}))})")
    columns = ("r_live", "r_sim_net", "cost_r", "entry_slip", "exit_diff")
    for group in [None] + sorted({row["strategy"] for row in matched}):
        rows = [row for row in matched if group is None or row["strategy"] == group]
        residual = np.array([row["r_live"] - row["r_sim_net"] for row in rows])
        parts = " ".join(f"{column}={np.mean([row[column] for row in rows]):+.4f}" for column in columns)
        print(f"  {group or 'pooled'}: n={len(rows)} {parts} residual={residual.mean():+.4f} "
              f"t={ews.t_stat(residual):+.2f} live_usd={sum(float(r['realized_pnl']) for r in rows):+.2f}")
    residual = np.array([row["r_live"] - row["r_sim_net"] for row in matched])
    statistic = ews.t_stat(residual)

    eurusd = float(frames["EURUSD"]["close"].values[-1])
    start_dt = datetime.fromisoformat(str(WINDOW_START)).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(str(end)).replace(tzinfo=timezone.utc)
    nights, swap_usd = await lrc.financing(start_dt, end_dt, eurusd)
    all_closes = database.db_conn.execute(
        "SELECT COUNT(*) FROM spot_trades WHERE platform = 'capital_com' AND accepted = 1 "
        "AND paper_mode = 0 AND size > 0 AND exit_time >= ? AND exit_time < ?",
        (str(WINDOW_START), str(end))).fetchone()[0]
    print(f"financing: {nights} SWAP entries, {swap_usd:+.4f} USD over {all_closes} closes "
          f"= {swap_usd / max(all_closes, 1) / ews.DEFAULT_TARGET_RISK_USD:+.4f} R per close")

    ews.BAR_CACHE = os.path.join(_ROOT, "tmp", "eff_bars")
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signals = ews.all_signals(frames, _min_stop_atr_multiple(), meta)
    ref_end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    days = np.arange(np.datetime64(signals[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D"), ref_end)
    series = {}
    for arm, shift in (("current", 0.0), ("calibrated", float(residual.mean()))):
        rows = lrc.shifted(signals, shift) if shift else signals
        closed = [t for t in lrc.replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < ref_end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f}", flush=True)
    print(f"pooled residual={residual.mean():+.4f} R t={statistic:+.2f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

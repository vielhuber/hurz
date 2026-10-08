"""Live against replay: each instrument's spread by the hour of the signal.

The replay charges each instrument one per-side cost from a spread snapshot
whatever the hour (section 347 kept the snapshot over the sampled medians).
Since 2026-09-08 the bot has written 12,906 live quotes; by UTC hour they
show the European indices two to six times wider before 07:00 (DE40, FR40,
UK100, EU50) and most FX crosses, HK50, SILVER and OIL_BRENT wider from
21:00. Momentum's night signals lose (section 380), which a flat cost
cannot explain.

Fixed before any outcome was seen:
  - calibration candidate: each signal's per-side cost is the snapshot
    times its instrument's median sampled half-spread in the signal bar's
    UTC bucket (00-07, 07-13, 13-21, 21-24) over the instrument's overall
    sampled median; buckets with fewer than 20 samples and unsampled
    instruments keep the snapshot. Both sides at the entry bucket (the
    24-bar leash exits at the same hour of day);
  - both arms read today's vetoes (section 357's reference);
  - verdict CALIBRATE, adopting the hourly cost into the shared replay,
    only if pooled paired |t| > 2; otherwise IMMATERIAL; no diagnostic arm.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.spot_trading.autotrade import _min_stop_atr_multiple
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

SAMPLES_PATH = "data/spread_samples.jsonl"
BUCKETS = (0, 7, 13, 21)
MIN_SAMPLES = 20


def bucket(hour):
    return max(start for start in BUCKETS if start <= hour)


def hourly_ratios(rows):
    """{(pair, bucket): bucket median / overall median} for buckets with enough samples."""
    by_pair, by_bucket = {}, {}
    for row in rows:
        hour = int(row["ts"][11:13])
        by_pair.setdefault(row["pair"], []).append(row["half_spread_pct"])
        by_bucket.setdefault((row["pair"], bucket(hour)), []).append(row["half_spread_pct"])
    return {key: float(np.median(values)) / float(np.median(by_pair[key[0]]))
            for key, values in by_bucket.items()
            if len(values) >= MIN_SAMPLES and np.median(by_pair[key[0]]) > 0}


def with_hourly_costs(frames, floor, meta, ratios):
    """`all_signals` with each signal's spread scaled by its pair and entry-hour bucket."""
    terms, fee = ews.trade_terms, ews._fee_for
    current = {}

    def timed(df, e, pair, meta_, atr_floor):
        current["key"] = (pair, bucket(int(str(df["timestamp"].values[e])[11:13])))
        return terms(df, e, pair, meta_, atr_floor)

    try:
        ews.trade_terms = timed
        ews._fee_for = lambda platform, pair: fee(platform, pair) * ratios.get(current["key"], 1.0)
        return ews.all_signals(frames, floor, meta)
    finally:
        ews.trade_terms, ews._fee_for = terms, fee


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    with open(SAMPLES_PATH) as handle:
        ratios = hourly_ratios(json.loads(line) for line in handle)
    for pair in sorted({p for p, _ in ratios} & set(frames)):
        print(f"{pair:<10} " + " ".join(f"{b:02d}h x{ratios[(pair, b)]:.2f}" for b in BUCKETS if (pair, b) in ratios))
    arms = {"snapshot": ews.all_signals(frames, floor, meta),
            "hourly": with_hourly_costs(frames, floor, meta, ratios)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(arms["snapshot"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    for arm, rows in arms.items():
        r = np.array([t["r"] for t in rows if t["strat"] == "momentum"])
        print(f"{arm}: signals={len(rows)} momentum={len(r)} momentum_mean_R={r.mean():+.4f}")
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["hourly"] - series["snapshot"]
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"snapshot={series['snapshot'][selected].mean():+.6f} "
              f"hourly={series['hourly'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

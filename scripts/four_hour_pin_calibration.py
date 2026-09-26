"""Live against replay: the 4h pins trade live but not in the replay.

`data/pinned_pairs.json` pins donchian_breakout_4h (SILVER, NZDUSD),
turtle_breakout_4h (HK50, WHEAT) and momentum_4h (COPPER, CHFJPY), all
exclusive. The replay honours their reservation of those instruments but
books none of their trades; live they closed 26 trades for -7.43 USD.
Section 225 measured removing them (t +0.76); this run does not repeat
that, it asks whether the replay's baseline misses something material by
leaving their trades out.

Fixed before any outcome was seen:
  - calibration candidate: book the 4h pins that are live today (not
    vetoed, instrument in the replay universe) from 4h bars resampled
    from the cached hourly bars (section 302's 4h terms: 3 x ATR or venue
    minimum stop, 1.5 R, 24-bar leash), appended after the hourly pins in
    the active order and sharing every cap and cooldown with the hourly
    book; a 4h signal's times are moved to the last hourly bar of its 4h
    bar so it enters and exits at the same closes as live;
  - adopted into the shared replay only if the 4h pins change the daily
    gain materially, pooled paired |t| > 2; otherwise the omission is
    documented as immaterial and the replay stays hourly-only;
  - no other change, no diagnostic arm.
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
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.rank_holding_time import daily_series

LAST_HOURLY_BAR = np.timedelta64(3, "h")


def live_four_hour_pins(pairs, vetoed_strategies):
    with open(pe.PINS_PATH) as handle:
        combos = json.load(handle)["combos"]
    return [(c["strategy"], c["pair"]) for c in combos
            if c["resolution"] == "4h" and c["pair"] in pairs
            and (c["strategy"], c["pair"]) not in pe.VETOED and c["strategy"] not in vetoed_strategies]


def aligned(four_hour, keys):
    return [{**t, "ts": t["ts"] + LAST_HOURLY_BAR, "exit_ts": t["exit_ts"] + LAST_HOURLY_BAR}
            for t in four_hour if (t["strat"], t["pair"]) in keys]


def replay(hourly, four_hour, pins, reserved, pin_order, days, extra):
    signals = sorted(hourly + four_hour, key=lambda trade: trade["ts"])
    timestamps = np.array([trade["ts"] for trade in signals])
    hourly_ts = np.array([trade["ts"] for trade in hourly])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(hourly_ts, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(hourly_ts, cut))
        training = [trade for trade in hourly[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        for key in extra:
            order.setdefault(key, len(order))
        window = signals[int(np.searchsorted(timestamps, cut)):int(np.searchsorted(timestamps, following))]
        admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
        cut = following
    return booked


async def main():
    meta = json.load(open(META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    hourly = all_signals(frames, floor, meta)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}), meta, floor),
                        set(extra))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} hourly_signals={len(hourly)} live_4h_pins={extra} "
          f"4h_signals={len(four_hour)} reserved={sorted(reserved)} OOS=[{start}, {end}) "
          f"calendar_days={len(days)}")
    series = {}
    for arm, with_pins in (("hourly only", False), ("with 4h pins", True)):
        closed = [t for t in replay(hourly, four_hour if with_pins else [], pins, reserved, pin_order,
                                    days, extra if with_pins else [])
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"].endswith("_4h")]
        print(f"{arm}: closed={len(closed)} 4h_closes={len(taken)} "
              f"4h_usd={sum(t['usd'] for t in taken):+.4f} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["with 4h pins"] - series["hourly only"]
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"hourly={series['hourly only'][selected].mean():+.6f} "
              f"with_4h={series['with 4h pins'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"pooled_delta={delta.mean():+.6f} ({delta.mean() / abs(series['hourly only'].mean()):+.1%}) "
          f"pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

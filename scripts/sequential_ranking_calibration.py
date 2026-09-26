"""Live against replay: the selector ranks on sequential trades, the replay on every signal.

Section 339 found the live selector counting about half the trades per
combination the replay counts over the same trailing year (median 45
against 83). The cause is in `spot_backtest._simulate_trades`: a signal
arriving while the combination's previous trade is still open is skipped
(`in_trade_until`), so the selector's expectancy, profit factor and trade
count come from one position at a time. The replay's `active_order`
ranks on every signal, overlapping ones included, so its weekly lists
are built from a statistic the bot never computes.

Fixed before any outcome was seen:
  - calibration candidate: before ranking, keep per combination only the
    signals that start after the previously kept one has exited, exactly
    as the selector's backtest does; admission, caps, pins, vetoes and
    everything after ranking stay unchanged;
  - adopted into the shared replay (`pin_eligibility.RANK_SEQUENTIAL`,
    honoured by `active_order` and `pin_eligibility.lists`) because the
    bot's selector demonstrably ranks this way;
  - independently, if the current every-signal ranking beats the
    sequential one on all four samples with pooled paired t > +2, the
    live selector is switched to every-signal ranking (a real change);
    otherwise the bot keeps ranking as it does.
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
from scripts.rank_holding_time import daily_series


def replay(signals, pins, reserved, pin_order, days, calibrated):
    pe.RANK_SEQUENTIAL = calibrated
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        window = signals[high:int(np.searchsorted(timestamps, following))]
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
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signals = all_signals(frames, _min_stop_atr_multiple(), meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(signals)} OOS=[{start}, {end}) calendar_days={len(days)}")

    last_year = [t for t in signals if end - np.timedelta64(RANK_DAYS, "D") <= t["ts"] and t["exit_ts"] < end]
    live = json.load(open("data/active_pairs.capital_com.json"))
    listed = {(row["strategy"], row["pair"]): row["n"] for row in live["pairs"]
              if row["resolution"] == "1h" and not row["pinned"]}
    for label, rows in (("every signal", last_year), ("sequential", pe.sequential(last_year))):
        counts = {}
        for trade in rows:
            key = (trade["strat"], trade["pair"])
            counts[key] = counts.get(key, 0) + 1
        common = [key for key in listed if key in counts]
        ratio = np.median([counts[key] / listed[key] for key in common if listed[key]])
        pe.RANK_SEQUENTIAL = False
        order = active_order(rows, pins, reserved, pin_order)
        ranked = {key for key in order if key not in pins}
        print(f"last cut, {label}: median n replay/live={ratio:.2f} over {len(common)} listed combos; "
              f"ranked={len(ranked)} agree_with_live_ranked={len(ranked & set(listed))}/{len(listed)}")

    series = {}
    for arm, calibrated in (("every signal", False), ("sequential", True)):
        closed = [t for t in replay(signals, pins, reserved, pin_order, days, calibrated)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["every signal"] - series["sequential"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"sequential={series['sequential'][selected].mean():+.6f} "
              f"every_signal={series['every signal'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"every-signal ranking better in {improved}/4 samples, pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['sequential'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("SWITCH_LIVE_SELECTOR" if improved == 4 and statistic > 2 else "KEEP_LIVE_SELECTOR"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

"""Portfolio: the 4h pins re-qualified weekly on their own trailing two years.

The five live 4h pins are a fixed list. On the two-year book they lose in
the three older samples and earn in the newest (sections 410, 411), so
retiring them failed only there. The hourly list re-qualifies every
combination weekly; the pins never are.

Fixed before any outcome was seen:
  - candidate: at each weekly cut a 4h pin trades, and reserves its
    instrument, only while its own sequential 4h trades closed in the
    trailing 730 days clear the list's floors (pe.MIN_N trades, profit
    factor >= pe.MIN_PF, E[R] >= pe.MIN_ER); otherwise its trades are
    skipped that week and momentum may rank on its instrument;
  - reference: the five pins fixed (section 401's book);
  - both on the 730-day ranking with the shared 4h booking, the hourly
    ETHUSD pin, today's vetoes, caps, stops and sizes; no other floor, no
    diagnostic arm.
Adoption: all four OOS samples better and pooled paired daily t > +2.
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
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"


def qualifies(trades):
    """Whether a pin's sequential trailing trades clear the list's floors."""
    r = np.array([t["r"] for t in pe.sequential(trades)])
    if len(r) < pe.MIN_N:
        return False
    losses = -r[r < 0].sum()
    pf = 5.0 if losses <= 0 else float(r[r > 0].sum() / losses)
    return pf >= pe.MIN_PF and float(r.mean()) >= pe.MIN_ER


def replay(hourly, four_hour, hourly_pins, pin_order, days, extra, log=None):
    """`momentum_ranked_wider.replay` on 730 days with the 4h pins re-qualified each week."""
    signals = sorted(hourly + four_hour, key=lambda trade: trade["ts"])
    timestamps = np.array([trade["ts"] for trade in signals])
    hourly_ts = np.array([trade["ts"] for trade in hourly])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        start = cut - np.timedelta64(CANDIDATE_DAYS, "D")
        active = [key for key in extra
                  if qualifies([t for t in four_hour if (t["strat"], t["pair"]) == key
                                and start <= t["ts"] and t["exit_ts"] < cut])]
        if log is not None:
            log.append(len(active))
        low = int(np.searchsorted(hourly_ts, start))
        high = int(np.searchsorted(hourly_ts, cut))
        training = [trade for trade in hourly[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, set(hourly_pins) | set(active), {k[1] for k in active}, pin_order)
        for key in active:
            order.setdefault(key, len(order))
        for key in [k for k in order if k in extra and k not in active]:
            del order[key]
        window = signals[int(np.searchsorted(timestamps, cut)):int(np.searchsorted(timestamps, following))]
        admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
        cut = following
    return booked


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    hourly = ews.all_signals(frames, floor, meta)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    hourly_pins = {k for k in pins if k not in set(extra)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"live_4h_pins={extra} OOS=[{start}, {end}) calendar_days={len(days)}")
    active_counts = []
    arms = {
        "fixed": ranked_replay(CANDIDATE_DAYS, hourly, hourly, four_hour, pins, reserved, pin_order, days, extra),
        "requalified": replay(hourly, four_hour, hourly_pins, pin_order, days, extra, active_counts),
    }
    print(f"active 4h pins per week: mean={np.mean(active_counts):.2f} "
          f"weeks with none={sum(1 for c in active_counts if c == 0)} of {len(active_counts)}")
    series = {}
    for arm, booked in arms.items():
        closed = [t for t in booked if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == STRATEGY]
        pin_closes = [t for t in closed if t["strat"].endswith("_4h")]
        print(f"{arm}: closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"4h={len(pin_closes)} ({sum(t['usd'] for t in pin_closes):+.4f} USD) pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["requalified"] - series["fixed"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"fixed={series['fixed'][selected].mean():+.6f} requalified={series['requalified'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['fixed'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

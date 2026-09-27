"""Universe: the book without its FX pairs.

Section 344 added eleven instruments the replay had never seen; the six FX
crosses and USDCAD among them all lost (-101.8 USD together) and the set
was blocked. In section 343's baseline the eleven FX pairs still in the
replay net about +1 USD of the book's +247 USD over six years (NZDUSD
-41, EURAUD -24, GBPJPY +34, USDCHF +22), while they take slots and
risk_on cluster room from indices, commodities and crypto. The class
question has not been asked: section 121 halved FX risk on a journal gate
and section 267 refused FX longs only.

Fixed before any outcome was seen:
  - candidate: every FX pair leaves the universe (EURUSD, USDCHF, AUDNZD,
    EURAUD, NZDUSD, AUDJPY, CHFJPY, CADJPY, EURJPY, GBPJPY, USDJPY), and
    with it any FX pin; the other sixteen instruments, rules, caps,
    stops and sizing are unchanged;
  - the class as a whole, no subset of FX pairs, no diagnostic arm;
  - the evidence for trying it comes from section 344's instruments, not
    from the eleven being tested.
Adoption: all four OOS samples better and pooled paired daily t > +2.
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
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

FX = {"EURUSD", "USDCHF", "AUDNZD", "EURAUD", "NZDUSD", "AUDJPY", "CHFJPY", "CADJPY",
      "EURJPY", "GBPJPY", "USDJPY"}


def without_fx(frames):
    return {pair: frame for pair, frame in frames.items() if pair not in FX}


async def main():
    meta = json.load(open(META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = {"live": frames_for(await load_history(), meta)}
    frames["candidate"] = without_fx(frames["live"])
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    signals = {arm: all_signals(arm_frames, floor, meta) for arm, arm_frames in frames.items()}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames["live"].values())
    start = np.datetime64(signals["live"][0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments live={len(frames['live'])} candidate={len(frames['candidate'])} "
          f"removed={sorted(set(frames['live']) - set(frames['candidate']))} "
          f"signals live={len(signals['live'])} candidate={len(signals['candidate'])} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm in ("live", "candidate"):
        pins, reserved = pe.load_pins(set(frames[arm]), pe.VETOED, vetoed_strategies)
        closed = [t for t in replay(signals[arm], pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        fx = [t for t in closed if t["pair"] in FX]
        print(f"{arm}: closed={len(closed)} pins={len(pins)} fx_closes={len(fx)} "
              f"fx_usd={sum(t['usd'] for t in fx):+.4f} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["live"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"live={series['live'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['live'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

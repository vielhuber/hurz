"""New strategy family: the weekend gap faded in the indices' reopen hour.

Held momentum positions lose 0.025 R on average in the reopen bar after a
closed market (135 trades, section 425), and every weekend lever on
momentum (sections 405, 418, 419) moved the book by less than its noise;
the reopen itself was never a signal.

Fixed before any outcome was seen:
  - instruments: the cached equity indices (US30, US100, US500, DE40,
    EU50, FR40, UK100, J225, HK50);
  - signal: the first bar after a gap of more than 36 hours whose open
    lies more than 0.5 x the hourly ATR(14) of the last bar before the
    gap away from that bar's close; the direction is against the gap;
  - trade: entered at the reopen bar's open with the live stop distance,
    cost and size of the last bar before the gap (`trade_terms`), the
    stop checked before the 1.5 R target inside the bar, closed at the
    reopen bar's close; financing as every replay trade;
  - candidate: section 401's book with `gap_fade` offered beside the 4h
    pins, every cap and one position per instrument unchanged; no other
    threshold or holding time, no diagnostic arm.
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
from app.spot_trading.trading_blocks import direction_blocked
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series

STRATEGY = "gap_fade"
INDICES = ("US30", "US100", "US500", "DE40", "EU50", "FR40", "UK100", "J225", "HK50")
WEEKEND_GAP = np.timedelta64(36, "h")
MIN_GAP_ATR = 0.5


def reopen_bars(ts, O, C, atr):
    """(reopen bar, direction) for each reopen after a weekend gap wider than MIN_GAP_ATR x ATR."""
    out = []
    for b in np.flatnonzero(np.diff(ts) > WEEKEND_GAP) + 1:
        gap = O[b] - C[b - 1]
        if atr[b - 1] > 0 and abs(gap) > MIN_GAP_ATR * atr[b - 1]:
            out.append((int(b), -1 if gap > 0 else 1))
    return out


def fade(O, H, L, C, b, d, stop_d, cost_r):
    """R of a fade entered at the reopen bar's open and closed at its close."""
    entry = O[b]
    if ((L[b] if d == 1 else H[b]) - (entry - d * stop_d)) * d <= 0:
        return -1.0 - cost_r
    if ((H[b] if d == 1 else L[b]) - (entry + d * ews.RR * stop_d)) * d >= 0:
        return ews.RR - cost_r
    return (C[b] - entry) * d / stop_d - cost_r


def gap_signals(frames, meta, floor):
    out = []
    for pair in INDICES:
        df = frames.get(pair)
        if df is None:
            continue
        ts = df["timestamp"].values
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        for b, d in reopen_bars(ts, O, C, df["atr_14"].values):
            if direction_blocked(pair, d):
                continue
            terms = ews.trade_terms(df, b - 1, pair, meta, floor)
            if terms is None:
                continue
            _, stop_d, cost_r, risk_usd = terms
            r = fade(O, H, L, C, b, d, stop_d, cost_r)
            signal_ts = ts[b] - ews.HOUR
            r -= ews.night_charge(pair, d, O[b], stop_d) * ews.rollovers(ts[b], ts[b] + ews.HOUR)
            out.append({"ts": signal_ts, "exit_ts": ts[b], "pair": pair, "dir": d, "strat": STRATEGY,
                        "r": float(r), "usd": float(r) * risk_usd, "risk": risk_usd})
    out.sort(key=lambda trade: trade["ts"])
    return out


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
    fades = gap_signals(frames, meta, floor)
    keys = sorted({(STRATEGY, t["pair"]) for t in fades})
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    oos = [t for t in fades if start <= np.datetime64(t["ts"], "D") < end]
    print(f"indices={[k[1] for k in keys]} OOS=[{start}, {end}) calendar_days={len(days)}")
    print(f"gap fades in OOS={len(oos)} mean_R={np.mean([t['r'] for t in oos]):+.4f} "
          f"usd={sum(t['usd'] for t in oos):+.2f} t={ews.t_stat(np.array([t['r'] for t in oos])):+.2f}")
    arms = {"reference": (four_hour, extra),
            "candidate": (sorted(four_hour + fades, key=lambda t: t["ts"]), extra + keys)}
    series = {}
    for arm, (additional, combos) in arms.items():
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, hourly, additional, pins, reserved,
                                           pin_order, days, combos)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} gap_fade={len(taken)} ({sum(t['usd'] for t in taken):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["reference"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"reference={series['reference'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

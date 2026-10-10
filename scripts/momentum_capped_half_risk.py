"""Portfolio and position sizing: momentum's notional-capped class at half risk.

Of momentum's 870 signals the 625 sized below 2.5 USD of planned risk earn
+0.0800 R (t +2.89), the 245 sized at 2.5 to 2.95 USD lose -0.0442 R
(section 390's diagnostic). Sizing takes the smaller of the 3 USD risk size
and the 250 USD notional cap, then rounds down to the venue's step; the cap
binds when the stop is narrower than 1.2 % of price (3 / 250), which the
1.05 % venue-minimum stop makes common.

Fixed before any outcome was seen:
  - decomposition: every momentum signal classed as notional-capped
    (stop distance below 1.2 % of the entry) or risk-sized, with count,
    mean planned risk and mean R per class;
  - candidate: the notional-capped momentum signals at half their planned
    risk (the dollar result scaled with it; the venue's minimum size is
    not re-checked), the risk-sized ones unchanged; no risk is raised;
  - both arms read today's vetoes (section 357's reference); the first
    sample holds no momentum trade in either arm (section 379), so the
    four-sample rule cannot be met there; measured and reported anyway;
  - no other share or threshold, no diagnostic arm.
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
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
CAP_STOP = 3.0 / 250.0
SHARE = 0.5


def capped(entry, stop_d):
    """True when the 250 USD notional cap, not the 3 USD risk, sets the size."""
    return stop_d / entry < CAP_STOP


def classed_signals(frames, floor, meta, strategy):
    """The strategy's signals, each marked `capped` by the sizing rule that bound it."""
    strats, terms = ews.STRATS, ews.trade_terms
    marks = []

    def remembered(df, e, pair, meta_, atr_floor):
        out = terms(df, e, pair, meta_, atr_floor)
        if out is not None:
            marks.append((df["timestamp"].values[e], pair, capped(out[0], out[1])))
        return out

    try:
        ews.STRATS, ews.trade_terms = [strategy], remembered
        own = ews.all_signals(frames, floor, meta)
    finally:
        ews.STRATS, ews.trade_terms = strats, terms
    flags = {(ts, pair): flag for ts, pair, flag in marks}
    return [{**t, "capped": flags[(t["ts"], t["pair"])]} for t in own]


def halved(signals):
    return [{**t, "risk": t["risk"] * SHARE, "usd": t["usd"] * SHARE} if t.get("capped") else t
            for t in signals]


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
    own = classed_signals(frames, floor, meta, STRATEGY)
    for name, flag in (("notional-capped", True), ("risk-sized", False)):
        part = [t for t in own if t["capped"] == flag]
        r = np.array([t["r"] for t in part])
        print(f"{name}: n={len(part)} mean_risk={np.mean([t['risk'] for t in part]):.3f} "
              f"mean_R={r.mean():+.4f} t={ews.t_stat(r):+.2f} "
              f"pairs={sorted({t['pair'] for t in part})}")
    base = [t for t in ews.all_signals(frames, floor, meta) if t["strat"] != STRATEGY]
    arms = {"current": sorted(base + own, key=lambda t: t["ts"]),
            "candidate": sorted(base + halved(own), key=lambda t: t["ts"])}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(arms["current"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    for arm, rows in arms.items():
        r = np.array([t["r"] for t in rows if t["strat"] == STRATEGY])
        print(f"{arm}: momentum signals={len(r)} mean_R={r.mean():+.4f} "
              f"near_target={int((r > ews.RR - 0.2).sum())}")
    print(f"vetoed_strategies={sorted(pe.VETOED_STRATEGIES)} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["current"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['current'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

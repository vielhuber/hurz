"""Live against backtest: the replay over the journal's filter period, rule by rule.

The journal since the filter state of 2026-09-10 earns +0.25 USD/day; the
replay reference was last read on a cache ending 2026-09-20 (section 365
matched fills and financing there, close by close). Whether the book the
replay would have traded over the same days, under the rules in force on
each day, earns what the journal earns was never measured.

Fixed before any outcome was seen:
  - window: calendar days from 2026-09-10 to the extended cache's last
    day; journal closes of every strategy (hourly and 4h) by exit day;
  - replay: the shared weekly-ranked replay on the cache extended to
    today, the live 4h pins, today's combination vetoes, in four rule
    regimes keyed by entry day: all three hourly strategies until
    2026-09-25 (donchian_breakout retired), turtle_breakout and momentum
    until 2026-10-01 02:00 UTC (turtle_breakout retired), momentum ranked
    on 365 days until 2026-10-13 (two-year ranking), then on 730 days;
  - matching: a live close and a replay trade of the same strategy and
    instrument whose signal bars lie within four hours of each other;
  - calibration candidate: CALIBRATE only if the paired daily difference
    (journal minus replay) has |t| > 2; the group with the largest USD
    gap (matched, live only, replay only) names the calibration;
    otherwise the reference stands;
  - no other window, no diagnostic arm.
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
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_two_year_ranking import ranked_replay
from scripts.rank_holding_time import daily_series
from scripts.rule_period_calibration import EXTENDED_CACHE, extend_cache

WINDOW_START = np.datetime64("2026-09-10")
MATCH_HOURS = np.timedelta64(4, "h")
# (first entry hour, hourly strategies traded, ranking days)
REGIMES = [
    (np.datetime64("2026-09-10T00"), {"donchian_breakout", "turtle_breakout", "momentum"}, 365),
    (np.datetime64("2026-09-25T00"), {"turtle_breakout", "momentum"}, 365),
    (np.datetime64("2026-10-01T02"), {"momentum"}, 365),
    (np.datetime64("2026-10-13T00"), {"momentum"}, 730),
]


def journal(end):
    rows = database.db_conn.execute(
        """
        SELECT pair, strategy, bar_time, exit_time, realized_pnl
        FROM spot_trades
        WHERE platform = 'capital_com' AND accepted = 1 AND paper_mode = 0 AND size > 0
          AND fill_price IS NOT NULL AND realized_pnl IS NOT NULL
          AND COALESCE(outcome, '') <> 'abandoned'
          AND bar_time >= ? AND exit_time < ?
        ORDER BY bar_time
        """, (str(WINDOW_START), str(end))).fetchall()
    hour = lambda value: np.datetime64(str(value)[:19].replace(" ", "T"))
    return [{"strat": r["strategy"], "pair": r["pair"], "ts": hour(r["bar_time"]),
             "exit_ts": hour(r["exit_time"]), "usd": float(r["realized_pnl"])} for r in rows]


def match(live, replayed):
    """(matched pairs, live only, replay only); each replay trade is used once."""
    free = list(replayed)
    pairs, live_only = [], []
    for close in live:
        hit = next((t for t in free if t["strat"] == close["strat"] and t["pair"] == close["pair"]
                    and abs(t["ts"] - close["ts"]) <= MATCH_HOURS), None)
        if hit is None:
            live_only.append(close)
            continue
        free.remove(hit)
        pairs.append((close, hit))
    return pairs, live_only, free


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
    days = np.arange(WINDOW_START, end)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    hourly = ews.all_signals(frames, floor, meta)
    extra = live_four_hour_pins(set(frames), set())
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    replayed = []
    bounds = [start for start, _, _ in REGIMES[1:]] + [np.datetime64(end, "h")]
    for (start, strategies, window), stop in zip(REGIMES, bounds):
        vetoed = set(ews.STRATS) - strategies
        pe.VETOED_STRATEGIES.clear()
        pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed)
        traded = [t for t in hourly if t["strat"] in strategies]
        regime_days = np.arange(np.datetime64(start, "D"), np.datetime64(stop, "D") + np.timedelta64(1, "D"))
        booked = [t for t in ranked_replay(window, hourly, traded, four_hour, pins, reserved, pin_order,
                                           regime_days, extra)
                  if start <= t["ts"] < stop and np.datetime64(t["exit_ts"], "D") < end]
        replayed += booked
        print(f"regime from {start}: strategies={sorted(strategies)} ranking={window}d "
              f"closes={len(booked)} usd={sum(t['usd'] for t in booked):+.2f}", flush=True)
    live = journal(end)
    live_series, replay_series = daily_series(live, days), daily_series(replayed, days)
    print(f"window=[{WINDOW_START}, {end}) days={len(days)} live_4h_pins={extra}")
    print(f"journal: closes={len(live)} usd={live_series.sum():+.2f} USD/day={live_series.mean():+.4f} "
          f"sd={live_series.std(ddof=1):.4f}")
    print(f"replay: closes={len(replayed)} usd={replay_series.sum():+.2f} USD/day={replay_series.mean():+.4f} "
          f"sd={replay_series.std(ddof=1):.4f}")
    pairs, live_only, replay_only = match(live, replayed)
    gaps = {"matched": sum(c["usd"] - t["usd"] for c, t in pairs),
            "live only": sum(c["usd"] for c in live_only),
            "replay only": -sum(t["usd"] for t in replay_only)}
    print(f"matched: n={len(pairs)} live={sum(c['usd'] for c, _ in pairs):+.2f} "
          f"replay={sum(t['usd'] for _, t in pairs):+.2f}")
    for name, rows in (("live only", live_only), ("replay only", replay_only)):
        by_strategy = {}
        for row in rows:
            by_strategy[row["strat"]] = by_strategy.get(row["strat"], 0.0) + row["usd"]
        print(f"{name}: n={len(rows)} usd={sum(by_strategy.values()):+.2f} "
              f"{ {k: round(v, 2) for k, v in sorted(by_strategy.items())} }")
    delta = live_series - replay_series
    statistic = ews.t_stat(delta)
    largest = max(gaps, key=lambda name: abs(gaps[name]))
    print(f"gap journal-replay by group: { {k: round(v, 2) for k, v in gaps.items()} } largest={largest}")
    print(f"paired daily delta={delta.mean():+.4f} USD t={statistic:+.2f}")
    print("VERDICT=" + (f"CALIBRATE ({largest})" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

"""New strategy family: a 20-session breakout on the indices' broker session closes.

The book earns only through index momentum in its newest year (sections
401, 416), and every hourly and 4h exit, entry and sizing variant since
moved it by less than its noise (sections 416-421). A daily-bar trend
signal on the indices was measured only as 12-month time-series momentum
(sections 366, 368), never as a channel breakout.

Fixed before any outcome was seen:
  - instruments: the equity indices of the universe (US30, US100, US500,
    DE40, EU50, FR40, UK100, J225, HK50, AU200 where cached);
  - decision bar: each instrument's broker session close (section 368,
    `session_close.is_daily_close` on today's `openingHours`);
  - entry: a session close above the highest (below the lowest) of the
    previous 20 session closes, long (short), at that close with the live
    stop, cost and size (`trade_terms`);
  - exit: the stop (a gap through it at the open), or a session close
    below the lowest (above the highest) of the previous 10 session
    closes, at that close; financing per rollover held; one position per
    instrument at a time;
  - candidate: section 401's book with `daily_breakout` offered beside
    the 4h pins, every cap unchanged; no other lookback, no diagnostic arm.
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
from scripts.tsmom_session_closes import opening_hours, session_bars

STRATEGY = "daily_breakout"
INDICES = ("US30", "US100", "US500", "DE40", "EU50", "FR40", "UK100", "J225", "HK50", "AU200")
ENTRY_SESSIONS = 20
EXIT_SESSIONS = 10


def breakout_trades(ts, O, H, L, C, last, terms_at):
    """(entry bar, exit bar, direction, r before financing) per breakout on the session closes `last`."""
    out = []
    closes = C[last]
    day_of = np.full(len(C), -1); day_of[last] = np.arange(len(last))
    k = ENTRY_SESSIONS
    while k < len(last) - 1:
        window = closes[k - ENTRY_SESSIONS:k]
        direction = 1 if closes[k] > window.max() else -1 if closes[k] < window.min() else 0
        e = int(last[k])
        terms = terms_at(e, direction) if direction else None
        if terms is None:
            k += 1
            continue
        entry, stop_d, cost_r = terms
        sl = entry - direction * stop_d
        r = exit_bar = None
        for b in range(e + 1, len(C)):
            if (O[b] - sl) * direction <= 0:
                r, exit_bar = (O[b] - entry) * direction / stop_d, b
                break
            if ((L[b] if direction > 0 else H[b]) - sl) * direction <= 0:
                r, exit_bar = -1.0, b
                break
            j = day_of[b]
            if j >= EXIT_SESSIONS:
                recent = closes[j - EXIT_SESSIONS:j]
                if (direction > 0 and C[b] < recent.min()) or (direction < 0 and C[b] > recent.max()):
                    r, exit_bar = (C[b] - entry) * direction / stop_d, b
                    break
        if exit_bar is None:
            break
        out.append((e, exit_bar, direction, r - cost_r))
        following = int(np.searchsorted(last, exit_bar))
        k = following if following > k else k + 1
    return out


def breakout_signals(frames, floor, meta, decision_bars):
    out = []
    for pair in INDICES:
        df = frames.get(pair)
        if df is None:
            continue
        ts = df["timestamp"].values
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        sized = {}

        def terms_at(e, direction):
            if direction_blocked(pair, direction):
                return None
            terms = ews.trade_terms(df, e, pair, meta, floor)
            if terms is None:
                return None
            sized[e] = terms[3]
            return terms[:3]

        for e, xb, direction, r in breakout_trades(ts, O, H, L, C, decision_bars(pair, ts), terms_at):
            entry, stop_d, _, _ = ews.trade_terms(df, e, pair, meta, floor)
            r -= ews.night_charge(pair, direction, entry, stop_d) * ews.rollovers(ts[e] + ews.HOUR, ts[xb] + ews.HOUR)
            out.append({"ts": ts[e], "exit_ts": ts[xb], "pair": pair, "dir": direction, "strat": STRATEGY,
                        "r": float(r), "usd": float(r) * sized[e], "risk": sized[e]})
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
    hours = await opening_hours([p for p in INDICES if p in frames])
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
    breakouts = breakout_signals(frames, floor, meta, session_bars(hours))
    keys = sorted({(STRATEGY, t["pair"]) for t in breakouts})
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    oos = [t for t in breakouts if start <= np.datetime64(t["ts"], "D") < end]
    print(f"indices={[k[1] for k in keys]} OOS=[{start}, {end}) calendar_days={len(days)}")
    print(f"breakout signals in OOS={len(oos)} mean_R={np.mean([t['r'] for t in oos]):+.4f} "
          f"usd={sum(t['usd'] for t in oos):+.2f} t={ews.t_stat(np.array([t['r'] for t in oos])):+.2f} "
          f"mean_days={np.mean([(t['exit_ts'] - t['ts']) / np.timedelta64(1, 'D') for t in oos]):.1f}")
    arms = {"reference": (four_hour, extra),
            "candidate": (sorted(four_hour + breakouts, key=lambda t: t["ts"]), extra + keys)}
    series = {}
    for arm, (additional, combos) in arms.items():
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, hourly, additional, pins, reserved,
                                           pin_order, days, combos)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} breakout={len(taken)} ({sum(t['usd'] for t in taken):+.4f} USD) "
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

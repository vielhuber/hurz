"""New strategy family: a short-term reversal on the indices' session closes.

The indices' reopen hour retraces a wide weekend gap (753 fades at
+0.0206 R, t +2.45, section 427): at short horizons the indices revert
after large moves, while every trend signal on them since section 401 was
rejected. A daily reversal on the indices was never measured (the bot's
hourly mean-reversion strategies were retired on their live losses).

Fixed before any outcome was seen:
  - instruments: the cached equity indices (US30, US100, US500, DE40,
    EU50, FR40, UK100, J225, HK50); decision bar: each index's broker
    session close (section 368, today's `openingHours`);
  - entry: long at a session close whose 2-session RSI is below 10 while
    the close is above its 200-session average; long only;
  - stop: the larger of 2 x the 14-session average true range of the
    sessions (high and low from the hourly bars) and the venue's 1.05 %
    minimum, sized to the live 3 USD risk and 250 USD notional cap,
    refused above 10 % cost per risk; a gap through it at the open;
  - exit: the first session close above the 5-session average, else the
    10th session close; financing per rollover; one position per index;
  - candidate: section 401's book with `daily_reversal` offered beside the
    4h pins, every cap unchanged; no other threshold, no diagnostic arm.
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
from app.spot_trading.position_sizing import (
    DEFAULT_NOTIONAL_CAP_USD, DEFAULT_TARGET_RISK_USD, calculate_position_size,
)
from scripts.spot_backtest import _fee_for
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series
from scripts.tsmom_session_closes import opening_hours, session_bars

STRATEGY = "daily_reversal"
INDICES = ("US30", "US100", "US500", "DE40", "EU50", "FR40", "UK100", "J225", "HK50")
RSI_LOW = 10.0
TREND_SESSIONS = 200
EXIT_SESSIONS = 5
MAX_SESSIONS = 10
ATR_SESSIONS = 14
MIN_STOP = 0.0105


def rsi2(closes):
    """Wilder RSI over 2 sessions for each session close (nan before it is defined)."""
    out = np.full(len(closes), np.nan)
    change = np.diff(closes)
    gain, loss = np.clip(change, 0, None), np.clip(-change, 0, None)
    if len(change) < 2:
        return out
    up, down = gain[:2].mean(), loss[:2].mean()
    for k in range(2, len(closes)):
        if k > 2:
            up = (up + gain[k - 1]) / 2; down = (down + loss[k - 1]) / 2
        out[k] = 100.0 if down == 0 else 100.0 - 100.0 / (1.0 + up / down)
    return out


def session_ranges(H, L, C, last):
    """True range of each session, from the hourly bars between consecutive session closes."""
    tr = np.full(len(last), np.nan)
    for k in range(1, len(last)):
        lo, hi = last[k - 1] + 1, last[k] + 1
        high, low, prev = H[lo:hi].max(), L[lo:hi].min(), C[last[k - 1]]
        tr[k] = max(high - low, abs(high - prev), abs(low - prev))
    return tr


def reversal_trades(O, H, L, C, last, stop_at):
    """(entry bar, exit bar, r before cost and financing, k) for each long reversal on the session closes."""
    closes = C[last]
    rsi = rsi2(closes)
    tr = session_ranges(H, L, C, last)
    day_of = np.full(len(C), -1); day_of[last] = np.arange(len(last))
    out = []
    k = max(TREND_SESSIONS, ATR_SESSIONS + 1)
    while k < len(last) - 1:
        if not (rsi[k] < RSI_LOW and closes[k] > closes[k - TREND_SESSIONS:k].mean()):
            k += 1
            continue
        e = int(last[k])
        stop_d = stop_at(e, float(np.nanmean(tr[k - ATR_SESSIONS + 1:k + 1])))
        if stop_d is None:
            k += 1
            continue
        entry = C[e]; sl = entry - stop_d
        r = exit_bar = None
        for b in range(e + 1, len(C)):
            if O[b] <= sl:
                r, exit_bar = (O[b] - entry) / stop_d, b; break
            if L[b] <= sl:
                r, exit_bar = -1.0, b; break
            j = day_of[b]
            if j > k and (C[b] > closes[j - EXIT_SESSIONS + 1:j + 1].mean() or j - k >= MAX_SESSIONS):
                r, exit_bar = (C[b] - entry) / stop_d, b; break
        if exit_bar is None:
            break
        out.append((e, exit_bar, r, k))
        following = int(np.searchsorted(last, exit_bar))
        k = following if following > k else k + 1
    return out


def reversal_signals(frames, meta, decision_bars):
    out = []
    for pair in INDICES:
        df = frames.get(pair)
        m = meta.get(pair)
        if df is None or m is None:
            continue
        ts = df["timestamp"].values
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        fee = _fee_for(ews.PLAT, pair)
        terms = {}

        def stop_at(e, session_atr):
            entry = float(C[e])
            stop_d = max(2.0 * session_atr, MIN_STOP * entry)
            cost_r = 2.0 * fee * entry / stop_d
            if not np.isfinite(stop_d) or cost_r > 0.10:
                return None
            sized = calculate_position_size(
                entry_price=entry, stop_loss=entry - stop_d,
                target_risk=DEFAULT_TARGET_RISK_USD / m["rate"],
                notional_cap=DEFAULT_NOTIONAL_CAP_USD / m["rate"],
                size_increment=m["step"], min_size=m["min"], max_size=m["max"])
            if sized.size is None:
                return None
            terms[e] = (stop_d, cost_r, sized.planned_risk * m["rate"])
            return stop_d

        for e, xb, r, _ in reversal_trades(O, H, L, C, decision_bars(pair, ts), stop_at):
            stop_d, cost_r, risk_usd = terms[e]
            r -= cost_r + ews.night_charge(pair, 1, C[e], stop_d) * ews.rollovers(ts[e] + ews.HOUR, ts[xb] + ews.HOUR)
            out.append({"ts": ts[e], "exit_ts": ts[xb], "pair": pair, "dir": 1, "strat": STRATEGY,
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
    reversals = reversal_signals(frames, meta, session_bars(hours))
    keys = sorted({(STRATEGY, t["pair"]) for t in reversals})
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    oos = [t for t in reversals if start <= np.datetime64(t["ts"], "D") < end]
    print(f"indices={[k[1] for k in keys]} OOS=[{start}, {end}) calendar_days={len(days)}")
    print(f"reversal signals in OOS={len(oos)} mean_R={np.mean([t['r'] for t in oos]):+.4f} "
          f"usd={sum(t['usd'] for t in oos):+.2f} t={ews.t_stat(np.array([t['r'] for t in oos])):+.2f} "
          f"mean_days={np.mean([(t['exit_ts'] - t['ts']) / np.timedelta64(1, 'D') for t in oos]):.1f}")
    arms = {"reference": (four_hour, extra),
            "candidate": (sorted(four_hour + reversals, key=lambda t: t["ts"]), extra + keys)}
    series = {}
    for arm, (additional, combos) in arms.items():
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, hourly, additional, pins, reserved,
                                           pin_order, days, combos)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} reversal={len(taken)} ({sum(t['usd'] for t in taken):+.4f} USD) "
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

"""Exits and holding: close a nearly expired position before the financing charge.

Section 337 found the account paying -3.37 USD of overnight financing over
50 days, half the replay's daily gain, while neither the journal nor the
replay books it. Capital.com charges every calendar night at 21:00 UTC,
Saturday and Sunday included (28 days of SWAP entries, all stamped 21:00),
so a position alive at 21:00 pays one night and one alive at Friday 21:00
pays three. Most positions time out on the 24-bar leash, where the last
hours carry close to no drift; a position whose leash ends shortly after
the rollover pays a full night for those few hours.

Fixed before any outcome was seen:
  - both arms are charged financing: every 21:00 UTC rollover strictly
    after the entry bar's close and at or before the exit bar's close
    costs one night at section 154's rates (crypto long 0.050 R, metals
    long 0.013, other longs 0.005, shorts 0.003, crypto and metal shorts
    nothing);
  - candidate: a position still open at the close of the 19:00 UTC bar
    whose timeout bar lies at most seven bars later is closed at that
    close (20:00 UTC) with the same spread charge as any exit;
  - stops, targets, sizing, the leash itself and every entry rule and cap
    stay unchanged; no diagnostic arm.
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
from app.strategies import get_strategy
from app.spot_trading.autotrade import _min_stop_atr_multiple, _CORRELATION_CLUSTERS
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.efficiency_weighted_selection import (
    HOLD, META_CACHE, RANK_DAYS, RR, STRATS, load_history, t_stat, trade_terms,
)
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

NIGHT_R = {("crypto", 1): 0.050, ("metals", 1): 0.013, ("crypto", -1): 0.0, ("metals", -1): 0.0}
EARLY_BARS = 7
ROLLOVER = np.timedelta64(21, "h")
DAY = np.timedelta64(1, "D")


def night_rate(pair, direction):
    default = 0.005 if direction > 0 else 0.003
    return NIGHT_R.get((_CORRELATION_CLUSTERS.get(pair), direction), default)


def rollovers(entry_close, exit_close):
    """21:00 UTC instants in (entry_close, exit_close]."""
    count = lambda moment: int(np.floor((moment - ROLLOVER - np.datetime64("1970-01-01T00")) / DAY))
    return count(exit_close) - count(entry_close)


def book(O, H, L, C, hours, e, d, entry, stop_d, cost_r, n, early):
    """The replay's barrier walk, optionally leaving at 20:00 UTC before a late timeout."""
    sl = entry - d * stop_d; tp = entry + d * RR * stop_d
    timeout = e + HOLD
    for b in range(e + 1, timeout + 1):
        if b >= n: break
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b, False
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl): return -1.0 - cost_r, b, False
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp): return RR - cost_r, b, False
        if early and hours[b] == 19 and timeout < n and timeout - b <= EARLY_BARS:
            return (float(C[b]) - entry) * d / stop_d - cost_r, b, True
    if timeout < n: return (float(C[timeout]) - entry) * d / stop_d - cost_r, timeout, False
    return None, None, False


def signals(frames, atr_floor, meta, early):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        hours = ts.astype("datetime64[h]").astype(np.int64) % 24
        O = df["open"].values; H = df["high"].values; L = df["low"].values; C = df["close"].values
        for strategy in STRATS:
            for signal in get_strategy(strategy)(df, {}):
                if gate(strategy, df, signal.index).blocked: continue
                if direction_blocked(pair, signal.direction): continue
                terms = trade_terms(df, signal.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb, left_early = book(O, H, L, C, hours, signal.index, signal.direction,
                             entry, stop_d, cost_r, n, early)
                if r is None: continue
                nights = rollovers(ts[signal.index] + np.timedelta64(1, "h"), ts[xb] + np.timedelta64(1, "h"))
                r_net = r - nights * night_rate(pair, signal.direction)
                out.append({"ts": ts[signal.index], "exit_ts": ts[xb], "pair": pair,
                            "dir": signal.direction, "strat": strategy, "r": r_net,
                            "usd": r_net * risk_usd, "risk": risk_usd, "gross": r, "nights": nights,
                            "early": left_early})
    out.sort(key=lambda trade: trade["ts"])
    return out


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
    floor = _min_stop_atr_multiple()
    arms = {"live": signals(frames, floor, meta, False), "candidate": signals(frames, floor, meta, True)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(arms["live"][0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    gross = [{**trade, "r": trade["gross"], "usd": trade["gross"] * trade["risk"]} for trade in arms["live"]]
    reference = [t for t in replay(gross, pins, reserved, pin_order, days)
                 if np.datetime64(t["exit_ts"], "D") < end]
    print(f"instruments={len(frames)} signals={len(arms['live'])} OOS=[{start}, {end}) "
          f"calendar_days={len(days)}")
    print(f"reference before financing: closed={len(reference)} "
          f"pnl={daily_series(reference, days).sum():+.6f}")
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} early_exits={sum(t['early'] for t in closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} "
              f"nights/close={np.mean([t['nights'] for t in closed]):.4f} "
              f"financing_R/close={np.mean([t['gross'] - t['r'] for t in closed]):.5f} "
              f"gross_R/close={np.mean([t['gross'] for t in closed]):+.5f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["live"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"days={selected.sum()} live={series['live'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    change = delta.mean() / abs(series["live"].mean())
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} ({change:+.1%}) "
          f"pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

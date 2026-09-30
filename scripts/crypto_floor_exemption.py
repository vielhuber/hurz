"""Universe: the crypto class admitted past the 3-ATR stop floor.

The 3-ATR floor (section 190) refuses a signal whose stop sits closer than
three ATR. On the indices and commodities the venue's 1.05 % minimum puts
nearly every stop far beyond that, so the floor bites rarely (US500: 450
of 1,857 router-passed signals). Crypto moves 0.67 % (BTCUSD) and 0.91 %
(ETHUSD) an hour at the median, so the strategy's 2-ATR stop clears the
venue minimum and the floor refuses the class almost whole: 2,072 of 2,161
router-passed BTCUSD signals and 2,190 of 2,205 ETHUSD signals. The 89
BTCUSD survivors close at +0.36 R per trade in section 352's replay.
Section 323 widened every refused stop to 3 ATR across the universe and
lost; the class-level question — trade crypto at the stop the strategy
asks for — was never measured.

Fixed before any outcome was seen:
  - candidate: BTCUSD and ETHUSD (the replay's crypto class) are exempt
    from the ATR floor; their stop stays max(2 ATR, venue minimum), sized
    to the same 3 USD risk target under the same 250 USD notional cap;
  - every other instrument, the cost ceiling, gate, blocks, caps, cooldowns
    and financing at each instrument's own rate unchanged; both arms rank
    and admit on their own figures in the shared replay of section 352;
  - no other class, no other stop multiple, no diagnostic arm.
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
import scripts.efficiency_weighted_selection as base
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

CRYPTO = {"BTCUSD", "ETHUSD"}


def crypto_exempt(trade_terms):
    """`trade_terms` with the ATR floor lifted for the crypto class only."""
    def terms(df, e, pair, meta, atr_floor):
        return trade_terms(df, e, pair, meta, 0.0 if pair in CRYPTO else atr_floor)
    return terms


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
    signals = {"live": all_signals(frames, floor, meta)}
    original = base.trade_terms
    base.trade_terms = crypto_exempt(original)
    try:
        signals["candidate"] = all_signals(frames, floor, meta)
    finally:
        base.trade_terms = original
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals["live"][0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    count = lambda rows: sum(t["pair"] in CRYPTO for t in rows)
    print(f"instruments={len(frames)} signals live={len(signals['live'])} "
          f"candidate={len(signals['candidate'])} crypto live={count(signals['live'])} "
          f"candidate={count(signals['candidate'])} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in signals.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        crypto = [t for t in closed if t["pair"] in CRYPTO]
        print(f"{arm}: closed={len(closed)} crypto={len(crypto)} "
              f"crypto_usd={sum(t['usd'] for t in crypto):+.4f} "
              f"crypto_R/close={np.mean([t['r'] for t in crypto]) if crypto else 0:+.4f} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
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

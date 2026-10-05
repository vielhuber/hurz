"""Universe and timeframes: crypto momentum past the 3-ATR floor in the momentum-only book.

The 3-ATR floor refuses most crypto signals because crypto's 2-ATR stop
already clears the venue minimum (section 355). Section 355 lifted the
floor for every crypto signal while the breakout book still traded and
rejected it (1/4 samples). Since the strategy veto retired both hourly
breakouts (section 357) the book trades momentum alone, and BTCUSD's
trends carried the tsmom result of section 366.

Fixed before any outcome was seen:
  - candidate: momentum's BTCUSD and ETHUSD signals priced without the
    ATR floor (stop max(2 ATR, venue minimum), 3 USD risk, 250 USD
    notional cap), everything else as the reference;
  - the breakouts' crypto signals keep the floor; both arms read today's
    vetoes (section 357);
  - no other class or multiple, no diagnostic arm.
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
from scripts.crypto_floor_exemption import CRYPTO, crypto_exempt
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"


def with_crypto_momentum(frames, floor, meta):
    """`all_signals` with momentum's crypto signals priced past the ATR floor."""
    base = [t for t in ews.all_signals(frames, floor, meta)
            if not (t["strat"] == STRATEGY and t["pair"] in CRYPTO)]
    strats, terms = ews.STRATS, ews.trade_terms
    try:
        ews.STRATS, ews.trade_terms = [STRATEGY], crypto_exempt(terms)
        own = ews.all_signals({p: df for p, df in frames.items() if p in CRYPTO}, floor, meta)
    finally:
        ews.STRATS, ews.trade_terms = strats, terms
    return sorted(base + own, key=lambda trade: trade["ts"])


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    arms = {"current": ews.all_signals(frames, floor, meta), "candidate": with_crypto_momentum(frames, floor, meta)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    days = np.arange(np.datetime64(arms["current"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D"), end)
    for arm, rows in arms.items():
        own = np.array([t["r"] for t in rows if t["strat"] == STRATEGY and t["pair"] in CRYPTO])
        print(f"{arm}: crypto momentum signals={len(own)} mean_R={own.mean() if len(own) else 0:+.4f}")
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days) if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        crypto = [t for t in closed if t["pair"] in CRYPTO]
        print(f"{arm}: closed={len(closed)} crypto_closes={len(crypto)} crypto_usd={sum(t['usd'] for t in crypto):+.4f} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["current"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

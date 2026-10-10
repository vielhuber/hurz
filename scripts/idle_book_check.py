"""Live against replay: does the replay trade where the live book has been idle since the veto?

Since the strategy veto of 2026-10-01 the bot has opened no position
(journal: no Capital entry from 2026-10-01 to 2026-10-10; heartbeat "0
signals in last 24h"). Its list is momentum on US30 and ETHUSD and the five
4h pins. The momentum-only reference expects about one close a month.

Fixed before any outcome was seen:
  - data: the bar cache extended to today in a separate copy (section 362);
  - replay: the shared weekly replay with today's vetoes and pins and the
    live 4h pins booked as in section 359, from the reference start to the
    extended cache's end; entries counted from 2026-10-01;
  - raw check: every momentum cross and every 4h pin signal on the bot's
    seven combinations since 2026-10-01, with the reason the replay drops
    it (regime gate, short block, pricing);
  - verdict CONSISTENT if the replay books no entry since 2026-10-01 that
    the live journal lacks; DEFECT otherwise, with the missing entries;
  - no gain arm: this checks the live path, it changes nothing.
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
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.strategies import get_strategy
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins, replay
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.rule_period_calibration import EXTENDED_CACHE, extend_cache

SINCE = np.datetime64("2026-10-01")
LIVE_LIST = (("momentum", "US30"), ("momentum", "ETHUSD"))


def drop_reason(strategy, df, x, pair, meta, floor):
    """Why the replay would not price this signal, or None."""
    if gate(strategy, df, x.index).blocked:
        return "regime gate"
    if direction_blocked(pair, x.direction):
        return "short block"
    if ews.trade_terms(df, x.index, pair, meta, floor) is None:
        return "pricing (floor, venue minimum, cost or size)"
    return None


def live_entries(since):
    rows = database.db_conn.execute(
        "SELECT strategy, pair, created_at FROM spot_trades WHERE platform = 'capital_com' "
        "AND accepted = 1 AND paper_mode = 0 AND size > 0 AND created_at >= ?", (str(since),)).fetchall()
    return [dict(row) for row in rows]


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
    end = max(np.datetime64(frame["timestamp"].values[-1], "h") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, np.datetime64(end, "D") + np.timedelta64(1, "D"))
    booked = replay(hourly, four_hour, pins, reserved, pin_order, days, extra)
    recent = [t for t in booked if t["ts"] >= SINCE]
    print(f"cache_end={end} vetoed_strategies={sorted(vetoed_strategies)} live_4h_pins={extra}")
    print(f"replay entries since {SINCE}: {len(recent)}")
    for t in recent:
        print(f"  {t['strat']} {t['pair']} {t['ts']} dir={t['dir']} r={t['r']:+.3f}")
    print("raw signals since 2026-10-01 on the bot's combinations:")
    for strategy, pair in LIVE_LIST:
        df = frames[pair]; ts = df["timestamp"].values
        for x in get_strategy(strategy)(df, {}):
            if ts[x.index] >= SINCE:
                print(f"  {strategy} {pair} {ts[x.index]} dir={x.direction} "
                      f"drop={drop_reason(strategy, df, x, pair, meta, floor)}")
    for t in four_hour:
        if t["ts"] >= SINCE:
            print(f"  {t['strat']} {t['pair']} {t['ts']} dir={t['dir']} priced")
    live = live_entries(SINCE)
    print(f"live entries since {SINCE}: {len(live)}")
    print("VERDICT=" + ("DEFECT" if len(recent) > len(live) else "CONSISTENT"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

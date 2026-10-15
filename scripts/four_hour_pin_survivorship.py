"""Live against replay: are the 4h pins' newest-sample earnings the live veto's selection?

On the two-year book the five 4h pins earn only in the newest OOS sample
(sections 410-412). The pin file was chosen on cost; the live expectancy
veto retires a 4h pin once it has lost over at least eight live trades.
A selection by the veto can only act on the replay from the pins' live
period on: the first live 4h pin entry is 2026-07-10.

Fixed before any outcome was seen:
  - the pin trades the two-year reference books (section 401) in the
    newest OOS sample, split by exit before and on or after 2026-07-10,
    per pin; beside them each pin's live journal closes;
  - verdict SURVIVORSHIP if the pins lose before 2026-07-10 and earn on
    or after it within the newest sample (the earnings then sit where the
    veto selected), else NONE; nothing is calibrated in this run.
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
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay

LIVE_START = np.datetime64("2026-07-10")


def split(trades, boundary):
    """(USD before `boundary`, USD on or after it) by exit day."""
    before = sum(t["usd"] for t in trades if np.datetime64(t["exit_ts"], "D") < boundary)
    after = sum(t["usd"] for t in trades if np.datetime64(t["exit_ts"], "D") >= boundary)
    return before, after


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    live = {(r["strategy"], r["pair"]): (r["n"], r["usd"]) for r in database.db_conn.execute(
        "select strategy, pair, count(*) n, sum(realized_pnl) usd from spot_trades "
        "where accepted=1 and strategy like '%_4h' and exit_time is not null group by 1, 2")}
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
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    newest = end - np.timedelta64(365, "D")
    closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, hourly, four_hour, pins, reserved, pin_order, days, extra)
              if np.datetime64(t["exit_ts"], "D") < end]
    pinned = [t for t in closed if t["strat"].endswith("_4h") and np.datetime64(t["exit_ts"], "D") >= newest]
    print(f"newest sample [{newest}, {end}) pin closes={len(pinned)} live start {LIVE_START} "
          f"vetoed 4h pins in the pin file: {sorted(k for k in pin_order if k[0].endswith('_4h') and k not in set(extra))}")
    for key in extra:
        own = [t for t in pinned if (t["strat"], t["pair"]) == key]
        before, after = split(own, LIVE_START)
        n, usd = live.get(key, (0, 0.0))
        print(f"  {key[0]:22s} {key[1]:7s} replay closes={len(own):3d} before={before:+8.2f} after={after:+8.2f} "
              f"| live closes={n} usd={usd or 0:+.2f}")
    before, after = split(pinned, LIVE_START)
    print(f"all pins: before={before:+.2f} USD after={after:+.2f} USD")
    print("VERDICT=" + ("SURVIVORSHIP" if before < 0 < after else "NONE"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

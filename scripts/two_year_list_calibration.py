"""Live against replay: the bot's two-year momentum list against the replay's.

Section 401 moved the nightly selector to a 730-day window. The list the
bot trades since 2026-10-12 22:30 UTC was ranked from the momentum
backtest of 22:24 UTC on 730 days; section 374 matched the lists at 365
days only.

Fixed before any outcome was seen:
  - cut 2026-10-12 22:00 UTC, on the separate copy of the bar cache
    extended to today (section 362);
  - replay list: `active_order` on the replay's trailing 730 days before
    the cut with today's vetoes and pins; bot list: the momentum rows of
    the written active list; inputs per instrument as section 374;
  - verdict MATCH if both momentum lists are equal, else MISMATCH with
    the differing combinations and their inputs; a calibration follows
    only on MISMATCH, in its own run; no other cut, no diagnostic arm.
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
from scripts.burst_cost_priority import active_order
from scripts.momentum_list_calibration import ACTIVE_PATH, RESULTS_PATH, STRATEGY, bot_list, replay_inputs
from scripts.rule_period_calibration import EXTENDED_CACHE, extend_cache

CUT = np.datetime64("2026-10-12T22")
WINDOW_DAYS = 730


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    await extend_cache()
    ews.BAR_CACHE = EXTENDED_CACHE
    ews.RANK_DAYS = WINDOW_DAYS
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signals = ews.all_signals(frames, _min_stop_atr_multiple(), meta)
    stats, window = replay_inputs(signals, CUT, STRATEGY)
    order = active_order(window, pins, reserved, pin_order)
    replayed = {k for k in order if k[0] == STRATEGY}
    live = bot_list(json.load(open(ACTIVE_PATH)), STRATEGY)
    selector = json.load(open(RESULTS_PATH))[f"capital_com::{STRATEGY}::1h"]
    print(f"cut={CUT} window={WINDOW_DAYS} cache_end="
          f"{max(np.datetime64(f['timestamp'].values[-1], 'h') for f in frames.values())} "
          f"selector_generated={selector['generated_at']} selector_days={selector['params'].get('days')} "
          f"vetoed_strategies={sorted(pe.VETOED_STRATEGIES)} reserved={sorted(reserved)}")
    print(f"momentum replay={sorted(replayed)} bot={sorted(live)}")
    pairs = sorted(set(stats) | {p for p, s in selector["pairs"].items() if s.get("n", 0)})
    dn, de = [], []
    for pair in pairs:
        n, e, pf = stats.get(pair, (0, 0.0, 0.0))
        s = selector["pairs"].get(pair, {})
        sn, se, spf = int(s.get("n", 0)), float(s.get("expectancy_R", 0.0)), float(s.get("profit_factor", 0.0))
        if pair in frames:
            dn.append(n - sn)
            if n and sn:
                de.append(e - se)
        flag = " <-" if (n >= pe.MIN_N) != (sn >= pe.MIN_N) else ""
        print(f"  {pair:10s} replay n={n:3d} eR={e:+.3f} pf={pf:5.2f} | selector n={sn:3d} eR={se:+.3f} "
              f"pf={spf:5.2f}{'' if pair in frames else ' (not in replay)'}{flag}")
    dn, de = np.array(dn, float), np.array(de, float)
    print(f"n difference (replay - selector): mean={dn.mean():+.2f} t={ews.t_stat(dn):+.2f} over {len(dn)} instruments; "
          f"eR difference mean={de.mean():+.4f} t={ews.t_stat(de):+.2f} over {len(de)}")
    print("VERDICT=" + ("MATCH" if replayed == live else "MISMATCH"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

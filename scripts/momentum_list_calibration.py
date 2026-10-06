"""Live against replay: the bot's momentum list against the replay's at the same re-rank.

Since 2026-10-01 the strategy veto retires both hourly breakouts, so the
reference replay is the momentum-only book (section 357). Section 339
matched the live list of 2026-09-25 to the replay's on 41 of 42
combinations, but momentum then held one slot. The bot's list of
2026-10-05 05:46 UTC ranks momentum on US30 (n 11, +0.531 R) and pins it
on ETHUSD; nothing had checked that the replay ranks the same.

Fixed before any outcome was seen:
  - cut: 2026-10-05, the bot's last re-rank, on a separate copy of the bar
    cache extended to today (section 362), so the seven-year reference
    stays untouched;
  - replay list: `active_order` on the replay's trailing year before the
    cut with today's vetoes and pins; bot list: the momentum rows of the
    written active list;
  - inputs: per instrument, the replay's sequential momentum n, mean R and
    profit factor against the selector's persisted backtest of the same
    re-rank;
  - verdict MATCH if both momentum lists are equal, else MISMATCH with
    the differing combinations and their inputs; a calibration follows
    only on MISMATCH and in its own run;
  - no other cut, no diagnostic arm.
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
from scripts.rule_period_calibration import EXTENDED_CACHE, extend_cache

STRATEGY = "momentum"
CUT = np.datetime64("2026-10-05")
ACTIVE_PATH = "data/active_pairs.capital_com.json"
RESULTS_PATH = "data/spot_backtest_results.json"


def replay_inputs(signals, cut, strategy):
    """Per instrument (n, mean R, profit factor) of the sequential trailing-year window."""
    window = [t for t in signals
              if cut - np.timedelta64(ews.RANK_DAYS, "D") <= t["ts"] < cut and t["exit_ts"] < cut]
    out = {}
    for t in pe.sequential(window):
        if t["strat"] == strategy:
            out.setdefault(t["pair"], []).append(t["r"])
    stats = {}
    for pair, rs in out.items():
        r = np.array(rs)
        losses = -r[r < 0].sum()
        stats[pair] = (len(r), float(r.mean()), 5.0 if losses <= 0 else float(r[r > 0].sum() / losses))
    return stats, window


def bot_list(active, strategy):
    return {(c["strategy"], c["pair"]) for c in active["pairs"]
            if c["strategy"] == strategy and c["resolution"] == "1h"}


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
    print(f"cut={CUT} cache_end={max(np.datetime64(f['timestamp'].values[-1], 'D') for f in frames.values())} "
          f"vetoed_strategies={sorted(pe.VETOED_STRATEGIES)} reserved={sorted(reserved)} "
          f"selector_generated={selector['generated_at']}")
    print(f"replay list: {sorted(order)}")
    print(f"momentum replay={sorted(replayed)} bot={sorted(live)}")
    pairs = sorted(set(stats) | {p for p, s in selector["pairs"].items() if s.get("n", 0)})
    dn, de = [], []
    for pair in pairs:
        n, e, pf = stats.get(pair, (0, 0.0, 0.0))
        s = selector["pairs"].get(pair, {})
        sn, se, spf = int(s.get("n", 0)), float(s.get("expectancy_R", 0.0)), float(s.get("profit_factor", 0.0))
        dn.append(n - sn)
        if n and sn:
            de.append(e - se)
        flag = " <-" if (n >= pe.MIN_N) != (sn >= pe.MIN_N) else ""
        print(f"  {pair:10s} replay n={n:3d} eR={e:+.3f} pf={pf:5.2f} | selector n={sn:3d} eR={se:+.3f} "
              f"pf={spf:5.2f}{flag}")
    dn, de = np.array(dn, float), np.array(de, float)
    print(f"n difference (replay - selector): mean={dn.mean():+.2f} t={ews.t_stat(dn):+.2f} over {len(dn)} instruments; "
          f"eR difference mean={de.mean():+.4f} t={ews.t_stat(de):+.2f} over {len(de)}")
    print("VERDICT=" + ("MATCH" if replayed == live else "MISMATCH"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

"""Case check: the replay's nightly list against the lists the live selector wrote.

The selector archives every list it writes since 2026-10-24 (section
443). The replay builds its list at each nightly cut from its own ranking
(`active_order`, cold-started on the window, two years since section
401), today's pins, reservations and vetoes, plus the live 4h pins. This
script extends the bar cache to now for the replay's instruments (one
paced session into `HURZ_EXTENDED_CACHE`, never the runtime cache) and
builds the replay's list at the time each archived list was written.

Fixed before any outcome was seen:
  - cuts: the `generated_at` of every archived list; ranking window the
    730 days before the cut, trades closed before it;
  - verdict per cut: "same" when the sets of (strategy, pair) are equal;
  - a difference counts as explained when the combination sits at the
    ten-trade floor (9 to 11 trades) in the replay or in the selector's
    persisted backtest (newest cut only, older results are overwritten),
    or when its instrument is outside the replay's universe; everything
    else is listed as unexplained;
  - the selector's and the replay's trade count and expectancy are
    printed for every listed momentum combination of the newest cut.
Read-only on the journal and the active list; nothing here trades.
"""
import asyncio
from datetime import datetime
import glob
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
from scripts.four_hour_pin_calibration import live_four_hour_pins
from scripts.momentum_ranked_wider import cold_start, with_cold_start
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS
from scripts.replay_signal_case_check import extend
from scripts.rule_period_calibration import EXTENDED_CACHE

HISTORY = "data/active_pairs_history/capital_com"
RESULTS = "data/spot_backtest_results.json"
FLOOR_BAND = (9, 11)


def archived_lists(paths):
    """(cut, {(strategy, pair)}) per archived list, oldest first."""
    out = []
    for path in sorted(paths):
        payload = json.load(open(path))
        cut = np.datetime64(payload["generated_at"].rstrip("Z"), "m")
        out.append((cut, {(row["strategy"], row["pair"]) for row in payload["pairs"]}))
    return out


def replay_counts(training):
    """Trades and mean R per combination, as the replay ranks them."""
    if pe.RANK_SEQUENTIAL:
        training = pe.sequential(training)
    grouped = {}
    for trade in training:
        grouped.setdefault((trade["strat"], trade["pair"]), []).append(trade["r"])
    return {key: (len(values), float(np.mean(values))) for key, values in grouped.items()}


def explain(key, replay_n, selector_n, universe):
    if key[1] not in universe:
        return "instrument outside the replay's universe"
    for name, n in (("replay", replay_n), ("selector", selector_n)):
        if n is not None and FLOOR_BAND[0] <= n <= FLOOR_BAND[1]:
            return f"at the ten-trade floor in the {name} ({n})"
    return None


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    lists = archived_lists(glob.glob(os.path.join(HISTORY, "*.json")))
    await extend(list(ews.PAIRS))
    ews.BAR_CACHE = EXTENDED_CACHE
    frames = frames_for(await ews.load_history(), meta)
    universe = set(frames)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(universe, pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    ranking = ews.all_signals(frames, floor, meta)
    ranking_ts = np.array([trade["ts"] for trade in ranking])
    ranked = [s for s in ews.STRATS if s not in vetoed_strategies]
    extra = live_four_hour_pins(universe, vetoed_strategies)
    selector = json.load(open(RESULTS))
    selector_n = {(payload["strategy"], pair): (int(stats.get("n", 0)), float(stats.get("expectancy_R", 0.0)))
                  for payload in selector.values()
                  if payload.get("platform") == "capital_com" and payload.get("resolution") == "1h"
                  for pair, stats in (payload.get("pairs") or {}).items()}
    print(f"instruments={len(frames)} bars to {max(f['timestamp'].values[-1] for f in frames.values())} "
          f"lists={len(lists)} 4h pins={sorted(extra)}", flush=True)
    for position, (cut, live) in enumerate(lists):
        start = cut - np.timedelta64(CANDIDATE_DAYS, "D")
        window = ranking[int(np.searchsorted(ranking_ts, start)):int(np.searchsorted(ranking_ts, cut))]
        training = with_cold_start([t for t in window if t["exit_ts"] < cut],
                                   cold_start(frames, start, cut, floor, meta, ranked), start)
        order = active_order(training, pins, reserved, pin_order)
        replayed = set(order) | {(strategy, pair) for strategy, pair in extra}
        counts = replay_counts(training)
        newest = position == len(lists) - 1
        differences = sorted(live ^ replayed)
        print(f"cut {cut}: live {len(live)}, replay {len(replayed)} -> "
              f"{'same' if not differences else f'{len(differences)} different'}")
        for key in differences:
            side = "live only" if key in live else "replay only"
            r_n = counts.get(key, (0, 0.0))[0]
            s_n = selector_n.get(key, (None, None))[0] if newest else None
            reason = explain(key, r_n, s_n, universe)
            print(f"  {side}: {key[0]} {key[1]} replay n={r_n} selector n={s_n} -> "
                  f"{reason or 'unexplained'}")
        if newest:
            for key in sorted(k for k in live | replayed if k[0] == "momentum"):
                r_n, r_e = counts.get(key, (0, 0.0))
                s_n, s_e = selector_n.get(key, (None, None))
                print(f"  stats {key[1]}: replay n={r_n} E[R]={r_e:+.3f}; selector n={s_n} "
                      f"E[R]={s_e if s_e is None else round(s_e, 3)}")
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

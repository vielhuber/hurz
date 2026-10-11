"""Live against replay: why momentum's levers fail in the oldest sample only.

Momentum at ADX 25 (section 395) and momentum ranked on its crosses from
ADX 25 (section 396) gain in the three newer OOS samples and lose only in
2020-09-23 to 2021-09-21, the year in which the reference books no
momentum trade.

Fixed before any outcome was seen:
  - the section 396 arms rerun unchanged; the delta per sample is split
    into the trades only the candidate books (added) and those only the
    reference books (displaced);
  - per sample, the added momentum trades' gross R, cost R and
    financing R, and the instruments with bars at the sample's start;
  - verdict CALIBRATE if the oldest sample's mean cost R or financing R
    per added momentum trade exceeds the largest of the newer samples by
    more than half, or it has fewer instruments with bars; else EDGE (the
    oldest year's shortfall is momentum's outcome, the replay's inputs
    are in line). A calibration follows in its own run.
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
from scripts.momentum_adx_floor import momentum_gate
from scripts.momentum_ranked_wider import RANKING_FLOOR, STRATEGY, replay

SAMPLES = ((365, 0), (1095, 365), (1825, 1095), (2555, 1825))


def key(trade):
    return trade["strat"], trade["pair"], trade["ts"]


def split(candidate, reference):
    """Trades only the candidate books and trades only the reference books."""
    theirs = {key(t) for t in reference}
    ours = {key(t) for t in candidate}
    return [t for t in candidate if key(t) not in theirs], [t for t in reference if key(t) not in ours]


def components(trades, terms):
    """Mean gross, cost and financing R of `trades`, from the captured terms."""
    gross, cost, financing = [], [], []
    for t in trades:
        entry, stop_d, cost_r = terms[(t["pair"], t["ts"])]
        night = ews.night_charge(t["pair"], t["dir"], entry, stop_d) * \
            ews.rollovers(t["ts"] + ews.HOUR, t["exit_ts"] + ews.HOUR)
        gross.append(t["r"] + cost_r + night)
        cost.append(cost_r)
        financing.append(night)
    return float(np.mean(gross)), float(np.mean(cost)), float(np.mean(financing))


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    terms, pricing, base = {}, ews.trade_terms, ews.gate

    def captured(df, e, pair, meta_, atr_floor):
        result = pricing(df, e, pair, meta_, atr_floor)
        if result is not None:
            terms[(pair, df["timestamp"].values[e])] = result[:3]
        return result

    ews.trade_terms = captured
    try:
        traded = ews.all_signals(frames, floor, meta)
        ews.gate = momentum_gate(base, RANKING_FLOOR)
        widened = ews.all_signals(frames, floor, meta)
    finally:
        ews.trade_terms, ews.gate = pricing, base
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(traded[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    books = {arm: [t for t in replay(ranking, traded, four_hour, pins, reserved, pin_order, days, extra)
                   if np.datetime64(t["exit_ts"], "D") < end]
             for arm, ranking in (("reference", traded), ("candidate", widened))}
    print(f"OOS=[{start}, {end}) reference={len(books['reference'])} candidate={len(books['candidate'])}")
    firsts = {p: np.datetime64(f["timestamp"].values[0], "D") for p, f in frames.items()}
    rows = []
    for older, newer in SAMPLES:
        low, high = max(start, end - np.timedelta64(older, "D")), end - np.timedelta64(newer, "D")
        inside = lambda ts: low <= np.datetime64(ts, "D") < high
        added, displaced = split([t for t in books["candidate"] if inside(t["exit_ts"])],
                                 [t for t in books["reference"] if inside(t["exit_ts"])])
        momentum = [t for t in added if t["strat"] == STRATEGY]
        gross, cost, financing = components(momentum, terms) if momentum else (0.0, 0.0, 0.0)
        bars = sum(1 for first in firsts.values() if first <= low - np.timedelta64(ews.RANK_DAYS, "D"))
        per_pair = {}
        for t in momentum:
            per_pair.setdefault(t["pair"], []).append(t["usd"])
        rows.append((cost, financing, bars))
        print(f"sample=[{low}, {high}) added={len(added)} ({sum(t['usd'] for t in added):+.4f} USD) "
              f"displaced={len(displaced)} ({sum(t['usd'] for t in displaced):+.4f} USD) "
              f"added_momentum={len(momentum)} mean_R={np.mean([t['r'] for t in momentum]) if momentum else 0:+.4f} "
              f"gross_R={gross:+.4f} cost_R={cost:.4f} financing_R={financing:.4f} "
              f"instruments_with_ranking_year={bars}/{len(frames)}")
        print("  by instrument: " + ", ".join(f"{p} {len(v)} {sum(v):+.2f}"
                                               for p, v in sorted(per_pair.items(), key=lambda kv: sum(kv[1]))))
    oldest, newer_rows = rows[-1], rows[:-1]
    out_of_line = (oldest[0] > 1.5 * max(r[0] for r in newer_rows)
                   or oldest[1] > 1.5 * max(r[1] for r in newer_rows)
                   or oldest[2] < min(r[2] for r in newer_rows))
    print("VERDICT=" + ("CALIBRATE" if out_of_line else "EDGE"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

"""Case check: the reference's spread cost against the live quotes the bot samples.

The replay charges each instrument one per-side cost from the spread
snapshot of 2026-09-04 (`spot_backtest._fee_for`); the bot gates on the
live quote and samples every listed instrument hourly into
`data/spread_samples.jsonl`. Sections 347 and 382 compared the two on
older books; this check prices the canonical reference itself.

Fixed before any outcome was seen:
  - live cost: each instrument's median sampled half-spread over all
    samples to date; instruments with fewer than 100 samples keep the
    snapshot;
  - the canonical reference (`momentum_two_year_ranking`, the 730-day
    arm) is replayed unchanged; for every close the booked cost
    `2 * fee * entry / stop` is recomputed at the median, the difference
    times the close's USD risk is the cost difference;
  - verdict: "tolerance" when the total difference is at most 5 % of the
    reference's absolute result, else "calibrate" (the replay's booking
    cost moves to the sampled medians).
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

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
from scripts.four_hour_stream import daily_signals, fetch_native_4h, native_frames
from scripts.momentum_ranked_wider import NIGHTLY, NIGHTLY_REFRESH, cold_start
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.spot_backtest import _fee_for

SAMPLES_PATH = "data/spread_samples.jsonl"
MIN_SAMPLES = 100
TOLERANCE_SHARE = 0.05


def sampled_medians(lines):
    """Median half-spread per instrument as a per-side fraction, with the sample count."""
    by_pair = {}
    for line in lines:
        row = json.loads(line)
        if row.get("half_spread_pct") is not None:
            by_pair.setdefault(row["pair"], []).append(float(row["half_spread_pct"]))
    return {pair: (float(np.median(values)) / 100.0, len(values)) for pair, values in by_pair.items()}


def cost_difference(trades, medians, snapshot):
    """Per instrument: closes, booked cost USD and the cost USD at the sampled median."""
    out = {}
    for trade in trades:
        median, count = medians.get(trade["pair"], (None, 0))
        fee = snapshot(trade["pair"])
        live = median if count >= MIN_SAMPLES and fee > 0 else fee
        booked = trade["cost_r"] * trade["risk"]
        entry = out.setdefault(trade["pair"], [0, 0.0, 0.0])
        entry[0] += 1; entry[1] += booked; entry[2] += booked * live / fee if fee > 0 else booked
    return out


def with_cost(trades, frames, meta, floor):
    """Hourly closes with the cost the replay booked, recomputed at their signal bar."""
    out = []
    for trade in trades:
        if "cost_r" in trade:
            out.append(trade)
            continue
        ts = frames[trade["pair"]]["timestamp"].values
        index = int(np.searchsorted(ts, trade["ts"]))
        terms = ews.trade_terms(frames[trade["pair"]], index, trade["pair"], meta, floor)
        out.append({**trade, "cost_r": terms[2]})
    return out


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    with open(SAMPLES_PATH) as handle:
        lines = handle.readlines()
    medians = sampled_medians(lines)
    print(f"spread samples={len(lines)} instruments={len(medians)}", flush=True)
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    hourly = ews.all_signals(frames, floor, meta)
    traded = ews.wallclock_signals(frames, floor, meta)
    ranked = [s for s in ews.STRATS if s not in vetoed_strategies]
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    pin_pairs = sorted({k[1] for k in extra})
    last = max(frames[p]["timestamp"].values[-1] for p in pin_pairs) + np.timedelta64(1, "h")
    first = min(frames[p]["timestamp"].values[0] for p in pin_pairs)
    await fetch_native_4h(pin_pairs, pd.Timestamp(first).tz_localize("UTC").to_pydatetime(),
                          pd.Timestamp(last).tz_localize("UTC").to_pydatetime())
    four_hour = financed(aligned(daily_signals(native_frames(pin_pairs, last), meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, traded, four_hour, pins, reserved, pin_order, days,
                                       extra, cold=lambda s, c: cold_start(frames, s, c, floor, meta, ranked),
                                       step=NIGHTLY, offset=NIGHTLY_REFRESH)
              if np.datetime64(t["exit_ts"], "D") < end]
    result = sum(t["usd"] for t in closed)
    print(f"reference closes={len(closed)} pnl={result:+.4f} USD", flush=True)
    per_pair = cost_difference(with_cost(closed, frames, meta, floor), medians,
                               lambda pair: _fee_for(ews.PLAT, pair))
    booked = sum(v[1] for v in per_pair.values()); live = sum(v[2] for v in per_pair.values())
    for pair, (count, b, l) in sorted(per_pair.items(), key=lambda item: item[1][1] - item[1][2]):
        median, samples = medians.get(pair, (None, 0))
        print(f"  {pair}: closes={count} snapshot {_fee_for(ews.PLAT, pair) * 100:.4f} % "
              f"median {median * 100 if median is not None else float('nan'):.4f} % ({samples} samples) "
              f"cost booked {b:.4f} USD at median {l:.4f} USD")
    difference = live - booked
    verdict = "tolerance" if abs(difference) <= TOLERANCE_SHARE * abs(result) else "calibrate"
    print(f"cost booked={booked:.4f} USD at the sampled medians={live:.4f} USD difference={difference:+.4f} USD "
          f"({difference / abs(result):+.1%} of the result) -> {verdict}")
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

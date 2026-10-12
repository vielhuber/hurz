"""Universe: the two FX crosses the replay never held, NZDJPY and AUDCAD.

Section 344 blocked six FX crosses and USDCAD after they lost in the
selector's wider universe; NZDJPY and AUDCAD were in neither universe.
The momentum book is held back by its ten-trade list floor (sections
395, 396, 399), so instruments add combinations that can clear it.

Fixed before any outcome was seen:
  - candidate: NZDJPY and AUDCAD join the 27 instruments, seven years of
    hourly bars fetched paced into the shared cache, venue size and USD
    rate from the venue; per-side cost half the spread quoted at the
    fetch, never below the 0.01 % default; both join the risk_on cluster
    like every yen and commodity cross;
  - every strategy, gate, veto, cap, stop and size unchanged; the live
    4h pins (section 359) in both arms; no other instrument, no
    diagnostic arm.
Adoption: all four OOS samples better and pooled paired daily t > +2.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.spot_trading.autotrade import _min_stop_atr_multiple, _CORRELATION_CLUSTERS
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import fetch_page, frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_ranked_wider import replay
from scripts.rank_holding_time import daily_series
from scripts.unscored_live_instruments import instrument_meta

CANDIDATES = ["NZDJPY", "AUDCAD"]
SPREADS_PATH = os.path.join(_ROOT, "tmp", "two_fx_crosses_spreads.json")
PAGE_PAUSE = float(os.environ.get("HURZ_PAGE_PAUSE") or 1.5)
DEFAULT_FEE = 0.0001


def fee_per_side(bid, offer):
    """Half the quoted spread as a fraction of the mid, never below the default."""
    mid = (bid + offer) / 2
    return max((offer - bid) / mid / 2, DEFAULT_FEE)


async def ensure_candidates():
    """Bars, venue meta and the quoted per-side fee of the candidates."""
    meta = json.load(open(ews.META_CACHE))
    fees = json.load(open(SPREADS_PATH)) if os.path.exists(SPREADS_PATH) else {}
    need = [p for p in CANDIDATES if not os.path.exists(ews.cache_path(p)) or p not in meta or p not in fees]
    if not need:
        return meta, fees
    clear_cache(); platform = get_platform(ews.PLAT); await platform.connect()
    try:
        for pair in need:
            if not os.path.exists(ews.cache_path(pair)):
                now = datetime.now(timezone.utc)
                cursor, bars = now - timedelta(days=ews.SPAN), {}
                while cursor < now:
                    end = min(cursor + timedelta(days=ews.PAGE_DAYS), now)
                    for bar in await fetch_page(platform, pair, cursor, end):
                        bars[bar.timestamp] = bar
                    cursor = end
                    await asyncio.sleep(PAGE_PAUSE)
                rows = [[b.timestamp.isoformat(), b.open, b.high, b.low, b.close, b.volume]
                        for _, b in sorted(bars.items())]
                json.dump(rows, open(ews.cache_path(pair), "w"))
                print(f"{pair}: {len(rows)} bars cached, first {rows[0][0] if rows else None}", flush=True)
            rows = json.load(open(ews.cache_path(pair)))
            if pair not in meta and rows:
                found = await instrument_meta(platform, pair, float(rows[-1][4]))
                if found is not None:
                    meta[pair] = found
            rules = await platform._get_dealing_rules(pair)
            fees[pair] = fee_per_side(float(rules["bid"]), float(rules["offer"]))
            print(f"{pair}: bid {rules['bid']} offer {rules['offer']} fee/side {fees[pair]:.6f}", flush=True)
            await asyncio.sleep(1.0)
    finally:
        await platform.disconnect()
    json.dump(meta, open(ews.META_CACHE, "w"))
    json.dump(fees, open(SPREADS_PATH, "w"))
    return meta, fees


async def main():
    meta, fees = await ensure_candidates()
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    raw = await ews.load_history()
    for pair in CANDIDATES:
        raw[pair] = [(datetime.fromisoformat(t), o, h, l, c, v)
                     for t, o, h, l, c, v in json.load(open(ews.cache_path(pair)))]
    frames = frames_for(raw, meta)
    added = [p for p in CANDIDATES if p in frames]
    original_fee = ews._fee_for
    ews._fee_for = lambda platform, pair: fees[pair] if pair in fees else original_fee(platform, pair)
    _CORRELATION_CLUSTERS.update({pair: "risk_on" for pair in CANDIDATES})
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    floor = _min_stop_atr_multiple()
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    arms = {"27": {p: f for p, f in frames.items() if p not in CANDIDATES}, "29": frames}
    series, end, days = {}, None, None
    for arm, universe in arms.items():
        pins, reserved = pe.load_pins(set(universe), pe.VETOED, vetoed_strategies)
        hourly = ews.all_signals(universe, floor, meta)
        if days is None:
            end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in universe.values())
            start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
            days = np.arange(start, end)
            print(f"added={added} OOS=[{start}, {end}) calendar_days={len(days)}")
        closed = [t for t in replay(hourly, hourly, four_hour, pins, reserved, pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == "momentum"]
        new = [t for t in closed if t["pair"] in CANDIDATES]
        print(f"{arm}: signals={len(hourly)} on_new={sum(t['pair'] in CANDIDATES for t in hourly)} "
              f"closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"on_new={len(new)} ({sum(t['usd'] for t in new):+.4f} USD) pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["29"] - series["27"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"27={series['27'][selected].mean():+.6f} 29={series['29'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

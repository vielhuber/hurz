"""Live against replay: the strategy veto summed in quote currency instead of USD.

`_realized_expectancy` sums (exit - fill) x size and |fill - stop| x size
over every instrument of a strategy. Both are in the instrument's quote
currency, so a yen trade weighs about 157 times a dollar trade and an HK50
trade about 7.8 times. At 2026-10-01 02:02 UTC one EURJPY stale exit
(-1.84 USD) moved turtle_breakout to -0.111 R and the bot retired it; it had
retired donchian_breakout on 2026-09-25 the same way (-0.107 R). Weighted by
the USD risk the journal records per trade (`fill_risk_usd`), the two
strategies stand at -0.052 R and +0.001 R, both above the -0.10 threshold.
Since 02:03 UTC the bot evaluates 7 combinations instead of 26.

Fixed before any outcome was seen:
  - current arm: the strategy and combination vetoes as the bot computed
    them until today (quote units), applied to the replay's lists and pins;
  - candidate arm: the same vetoes with each row's PnL and risk converted
    by `fill_risk_usd` (rows without it keep quote units);
  - thresholds, minimum trade counts and every other rule unchanged;
  - built into the bot if all four OOS samples are better and pooled
    paired daily t > +2; no other weighting, no diagnostic arm.
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
from app.spot_trading import pair_selector as ps
from app.spot_trading.autotrade import _min_stop_atr_multiple
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

RISK = "ABS(COALESCE(fill_price, entry_price) - stop_loss) * size"
EXPECTANCY = """
    SELECT {group}, COUNT(*) AS n,
           SUM(CASE WHEN exit_price IS NOT NULL AND fill_price IS NOT NULL
                    THEN (exit_price - fill_price) * direction * size * {factor}
                    ELSE realized_pnl END) AS pnl,
           SUM({risk_sum}) AS risk
    FROM spot_trades
    WHERE accepted = 1 AND paper_mode = 0 AND exit_time IS NOT NULL
      AND realized_pnl IS NOT NULL AND size > 0
      AND ABS(COALESCE(fill_price, entry_price) - stop_loss) > 0
      AND COALESCE(outcome, '') <> 'abandoned' AND platform = 'capital_com'
    GROUP BY {group} HAVING COUNT(*) >= ?
"""


def vetoes(usd):
    """The combination and strategy vetoes, summed in quote units (the bot) or in USD."""
    if usd:
        query = EXPECTANCY.replace("{factor}", f"COALESCE(fill_risk_usd, {RISK}) / ({RISK})") \
                          .replace("{risk_sum}", f"COALESCE(fill_risk_usd, {RISK})")
    else:
        query = EXPECTANCY.replace("{factor}", "1").replace("{risk_sum}", RISK)
    combos = {(row["strategy"], row["pair"])
              for row in database.db_conn.execute(query.format(group="strategy, pair"),
                                                  (ps._VETO_MIN_TRADES,))
              if row["pnl"] / row["risk"] <= ps._VETO_MAX_EXPECTANCY_R}
    strategies = {row["strategy"]: row["pnl"] / row["risk"]
                  for row in database.db_conn.execute(query.format(group="strategy"),
                                                      (ps._STRATEGY_VETO_MIN_TRADES,))
                  if row["pnl"] / row["risk"] <= ps._STRATEGY_VETO_MAX_EXPECTANCY_R}
    return combos, strategies


async def main():
    meta = json.load(open(META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await load_history(), meta)
    arms = {"current": vetoes(usd=False), "candidate": vetoes(usd=True)}
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signals = all_signals(frames, _min_stop_atr_multiple(), meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(signals)} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, (combos, strategies) in arms.items():
        pe.VETOED.clear(); pe.VETOED.update(combos)
        pe.VETOED_STRATEGIES.clear()
        pins, reserved = pe.load_pins(set(frames), pe.VETOED, set(strategies))
        print(f"{arm} vetoes: strategies {({s: round(r, 4) for s, r in sorted(strategies.items())})} "
              f"combos {len(combos)}")
        closed = [t for t in replay(signals, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        by_strategy = {s: sum(1 for t in closed if t["strat"] == s) for s in sorted({t["strat"] for t in closed})}
        print(f"{arm}: closed={len(closed)} {by_strategy} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["current"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['current'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

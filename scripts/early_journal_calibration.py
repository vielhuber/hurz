"""Live against replay: the journal from 2026-05-12 to 2026-07-31.

Sections 337, 339 and 362 matched live closes to the replay only from
2026-08-01, the first day `planned_risk_usd` was journaled. The journal's
loss was booked mostly before: -190.40 USD over 396 closes from May to
July (dashboard all-time -93.13 USD on the active book, -372.60 USD on
retired combinations).

Fixed before any outcome was seen:
  - window: live Capital closes with bar_time from 2026-05-12 to
    2026-08-01, accepted, filled, closed, not abandoned, every strategy;
  - attribution: each close falls in exactly one group — a strategy the
    replay does not trade (anything but donchian_breakout, turtle_breakout
    and momentum), an instrument outside the replay universe, a bar the
    cache cannot match, or replayable; count and USD per group;
  - replayable closes: section 337's execution split (`decompose`: live R
    from the fill against the replay's net R at the journaled entry,
    stop and target, without needing the planned risk);
  - calibration candidate: CALIBRATE only if the replayable residual
    (live R minus replay net R) has |t| > 2; otherwise the replay's cost
    model stands and the early loss is attributed to the groups above;
  - no other window, no diagnostic arm.
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
import scripts.efficiency_weighted_selection as ews
from scripts.additional_indices import frames_for
from scripts.live_replay_calibration import decompose

WINDOW = ("2026-05-12", "2026-08-01")


def early_closes(window):
    rows = database.db_conn.execute(
        """
        SELECT pair, strategy, bar_time, direction, entry_price, stop_loss, take_profit,
               fill_price, exit_price, exit_time, outcome, realized_pnl
        FROM spot_trades
        WHERE platform = 'capital_com' AND accepted = 1 AND paper_mode = 0 AND size > 0
          AND fill_price IS NOT NULL AND exit_price IS NOT NULL AND realized_pnl IS NOT NULL
          AND COALESCE(outcome, '') <> 'abandoned'
          AND bar_time >= ? AND bar_time < ?
        ORDER BY bar_time
        """, window).fetchall()
    return [dict(row) for row in rows]


def group(row, strategies, pairs):
    """Which part of the early journal a close belongs to."""
    if row["strategy"] not in strategies:
        return "strategy outside the replay"
    if row["pair"] not in pairs:
        return "instrument outside the replay"
    return "replayable"


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    closes = early_closes(WINDOW)
    groups = {}
    for row in closes:
        groups.setdefault(group(row, set(ews.STRATS), set(frames)), []).append(row)
    matched, unmatched = decompose(groups.get("replayable", []), frames)
    if unmatched:
        groups["replayable"] = [r for r in groups["replayable"] if r not in unmatched]
        groups["bar not in the cache"] = unmatched
    print(f"window=[{WINDOW[0]}, {WINDOW[1]}) closes={len(closes)} "
          f"usd={sum(float(r['realized_pnl']) for r in closes):+.2f}")
    for name, rows in sorted(groups.items()):
        by_strategy = {}
        for r in rows:
            by_strategy[r["strategy"]] = by_strategy.get(r["strategy"], 0.0) + float(r["realized_pnl"])
        print(f"  {name}: n={len(rows)} usd={sum(by_strategy.values()):+.2f} "
              f"{ {k: round(v, 2) for k, v in sorted(by_strategy.items(), key=lambda kv: kv[1])} }")
    columns = ("r_live", "r_sim_net", "cost_r", "entry_slip", "exit_diff")
    for strategy in [None] + sorted({row["strategy"] for row in matched}):
        rows = [row for row in matched if strategy is None or row["strategy"] == strategy]
        residual = np.array([row["r_live"] - row["r_sim_net"] for row in rows])
        parts = " ".join(f"{c}={np.mean([row[c] for row in rows]):+.4f}" for c in columns)
        print(f"  {strategy or 'pooled'}: n={len(rows)} {parts} residual={residual.mean():+.4f} "
              f"t={ews.t_stat(residual):+.2f}")
    residual = np.array([row["r_live"] - row["r_sim_net"] for row in matched])
    statistic = ews.t_stat(residual)
    print(f"pooled residual={residual.mean():+.4f} R t={statistic:+.2f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())

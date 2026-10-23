"""Case check of the bot's decisions since the veto regime, against its own rules.

Read-only on the journal; no broker access. Every journaled signal since
2026-10-01 02:02 UTC is checked:
  - refusals: the ADX quoted in the reason is below the strategy's live
    trend floor; floor refusals are listed, because the replay widens a
    4h stop to the floor instead of refusing (STATUS.md, open deviation);
  - duplicates: the same signal (pair, strategy, bar, direction) journaled
    more than once;
  - entries: ADX at entry at or above the floor; fill within 0.10 R of
    the signal price; size and planned risk equal to the replay's sizing
    (`calculate_position_size`) on the journaled entry and stop, planned
    risk within 2 % (quote-to-USD rate of the bar cache against the live
    one); stale exit 24 wall-clock hours after entry, or the first close
    after the reopen when the market was shut at that mark; realised USD
    equal to (exit - fill) x direction x size at the journaled rate within
    0.01 USD.
Tolerances are fixed here, before the check runs.
"""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.spot_trading.journal_ledger import VETO_REGIME_FROM
from app.spot_trading.position_sizing import (
    DEFAULT_NOTIONAL_CAP_USD, DEFAULT_TARGET_RISK_USD, calculate_position_size,
)
from app.spot_trading.regime import trend_floor

FILL_TOLERANCE_R = 0.10
RISK_TOLERANCE = 0.02
PNL_TOLERANCE_USD = 0.01
STALE_HOURS = 24
STALE_TOLERANCE = timedelta(minutes=5)
FOUR_HOUR = ("_4h",)


def refusal(error):
    """(kind, ADX quoted or None) of a journaled refusal."""
    match = re.search(r"needs ADX>=([\d.]+), got ([\d.]+)", error or "")
    if match:
        return "adx", float(match.group(2))
    if "3×ATR floor" in (error or ""):
        return "atr floor", None
    return "other", None


def stale_exit_ok(entered, exited, hours=STALE_HOURS):
    """Exit at the leash, or later only by a closed market (no exit before it)."""
    due = entered + timedelta(hours=hours)
    if abs(exited - due) <= STALE_TOLERANCE:
        return True, "at the leash"
    if exited > due and (due.weekday() >= 5 or (due.weekday() == 4 and due.hour >= 21)):
        return True, "first close after the weekend"
    return False, f"{(exited - entered).total_seconds() / 3600:.1f} h after entry"


def main():
    meta = json.load(open(os.path.join("tmp", "eff_meta.json")))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT * FROM spot_trades WHERE platform = 'capital_com' AND paper_mode = 0
           AND created_at >= ? ORDER BY created_at""", (VETO_REGIME_FROM,)).fetchall()
    connection.close()
    findings, counts = [], {}
    seen = {}
    for row in rows:
        key = (row["pair"], row["strategy"], row["bar_time"], row["direction"])
        seen.setdefault(key, []).append(row["id"])
        floor = trend_floor(row["strategy"])
        if not row["accepted"]:
            kind, adx = refusal(row["error"])
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "adx" and adx >= floor:
                findings.append(f"#{row['id']} refused at ADX {adx} although the floor is {floor}")
            if kind == "atr floor":
                where = "replay widens the stop and trades" if row["strategy"].endswith(FOUR_HOUR) \
                    else "replay refuses too"
                findings.append(f"#{row['id']} {row['strategy']} {row['pair']} bar {row['bar_time']}: "
                                f"3-ATR floor refusal — {where}")
            if kind == "other":
                findings.append(f"#{row['id']} unclassified refusal: {row['error']}")
            continue
        counts["entered"] = counts.get("entered", 0) + 1
        checks = []
        if row["entry_adx"] is not None and row["entry_adx"] < floor:
            checks.append(f"ADX {row['entry_adx']:.1f} below {floor}")
        stop = abs(row["entry_price"] - row["stop_loss"])
        slip = (row["entry_price"] - row["fill_price"]) * row["direction"] / stop
        checks.append(f"fill {slip:+.4f} R {'ok' if abs(slip) <= FILL_TOLERANCE_R else 'OUTSIDE'}")
        m = meta.get(row["pair"])
        if m is None:
            checks.append("no sizing metadata")
        else:
            sized = calculate_position_size(
                entry_price=row["entry_price"], stop_loss=row["stop_loss"],
                target_risk=DEFAULT_TARGET_RISK_USD / m["rate"],
                notional_cap=DEFAULT_NOTIONAL_CAP_USD / m["rate"],
                size_increment=m["step"], min_size=m["min"], max_size=m["max"])
            replay_risk = sized.planned_risk * m["rate"]
            gap = replay_risk / row["planned_risk_usd"] - 1
            same = sized.size == row["size"] and abs(gap) <= RISK_TOLERANCE
            checks.append(f"size {row['size']} vs replay {sized.size}, planned risk {row['planned_risk_usd']:.4f} "
                          f"vs replay {replay_risk:.4f} USD ({gap:+.1%}) {'ok' if same else 'OUTSIDE'}")
        if row["exit_time"] is not None:
            entered = datetime.fromisoformat(str(row["created_at"])[:19])
            exited = datetime.fromisoformat(str(row["exit_time"])[:19])
            if row["outcome"] == "manual":
                ok, how = stale_exit_ok(entered, exited)
                checks.append(f"stale exit {how} {'ok' if ok else 'OUTSIDE'}")
            rate = row["fill_risk_usd"] / (abs(row["fill_price"] - row["stop_loss"]) * row["size"])
            recomputed = (row["exit_price"] - row["fill_price"]) * row["direction"] * row["size"] * rate
            diff = row["realized_pnl"] - recomputed
            checks.append(f"realised {row['realized_pnl']:+.4f} vs recomputed {recomputed:+.4f} USD "
                          f"{'ok' if abs(diff) <= PNL_TOLERANCE_USD else 'OUTSIDE'}")
        else:
            checks.append("open")
        findings.append(f"#{row['id']} entered {row['strategy']} {row['pair']} dir {row['direction']:+d} "
                        f"bar {row['bar_time']}: " + "; ".join(checks))
    duplicates = {key: ids for key, ids in seen.items() if len(ids) > 1}
    print(f"journal rows since {VETO_REGIME_FROM} UTC: {len(rows)} {counts}")
    for line in findings:
        print(line)
    for key, ids in duplicates.items():
        print(f"duplicate signal {key}: rows {ids}")
    outside = sum("OUTSIDE" in line or "although" in line or "unclassified" in line for line in findings)
    print(f"duplicates={len(duplicates)} outside_tolerance={outside}")


if __name__ == "__main__":
    main()

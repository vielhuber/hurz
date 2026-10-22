"""Live against backtest: entry slippage on bars that end at the home cash close.

The US30 momentum short of 2026-10-16, entered 49 s after its 19:00 UTC
bar closed at the New York cash close, sold 39.5 points (about 0.07 R)
below its signal price; the other two momentum fills since 2026-10-01
differ by 0.1 and 0.3 points. Section 337 measured entry slippage only
pooled (-0.0088 R, t -1.29, 45 closes).

Fixed before any outcome was seen:
  - fills: every accepted live Capital.com fill in the journal with a
    signal price and a stop; slippage in R is (signal - fill) * dir over
    the planned stop distance, negative when the fill is worse;
  - a cash-close entry is an index fill whose signal bar (1h, or 4h for
    the 4h strategies) ends within the hour after the home exchange's
    regular close in local time: New York 16:00 (US30, US100, US500),
    Frankfurt 17:30 (DE40, EU50), Paris 17:30 (FR40), London 16:30
    (UK100), Tokyo 15:30 (J225), Hong Kong 16:00 (HK50), Sydney 16:00
    (AU200);
  - test: cash-close index fills against the other index fills (Welch t);
    CALIBRATE only if the cash-close mean is worse with t < -2, then the
    replay charges the difference on cash-close entries; otherwise the
    reference stands;
  - no other split, no diagnostic arm.
Nothing here trades or changes the bot; the database is opened read-only.
"""
from datetime import datetime, time, timedelta
import os
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

CASH_CLOSE = {
    "US30": ("America/New_York", time(16, 0)), "US100": ("America/New_York", time(16, 0)),
    "US500": ("America/New_York", time(16, 0)), "DE40": ("Europe/Berlin", time(17, 30)),
    "EU50": ("Europe/Berlin", time(17, 30)), "FR40": ("Europe/Paris", time(17, 30)),
    "UK100": ("Europe/London", time(16, 30)), "J225": ("Asia/Tokyo", time(15, 30)),
    "HK50": ("Asia/Hong_Kong", time(16, 0)), "AU200": ("Australia/Sydney", time(16, 0)),
}


def at_cash_close(pair, strategy, bar_time):
    """Whether the signal bar ends within the hour after the pair's home cash close."""
    if pair not in CASH_CLOSE:
        return False
    zone, close = CASH_CLOSE[pair]
    bar_end = bar_time.replace(tzinfo=ZoneInfo("UTC")) + timedelta(hours=4 if strategy.endswith("_4h") else 1)
    local_end = bar_end.astimezone(ZoneInfo(zone))
    closed_at = datetime.combine(local_end.date(), close, tzinfo=ZoneInfo(zone))
    return closed_at <= local_end < closed_at + timedelta(hours=1)


def slippage_r(direction, signal, fill, stop):
    return (signal - fill) * direction / abs(signal - stop)


def welch_t(a, b):
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    return float((a.mean() - b.mean()) / np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))


def main():
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    rows = connection.execute(
        """
        SELECT pair, strategy, bar_time, direction, entry_price, fill_price, stop_loss
        FROM spot_trades
        WHERE platform = 'capital_com' AND accepted = 1 AND paper_mode = 0 AND size > 0
          AND fill_price IS NOT NULL AND entry_price IS NOT NULL AND stop_loss IS NOT NULL
          AND entry_price <> stop_loss
        ORDER BY bar_time
        """).fetchall()
    connection.close()
    groups = {"cash close": [], "other index": [], "non-index": []}
    for pair, strategy, bar_time, direction, signal, fill, stop in rows:
        slip = slippage_r(direction, signal, fill, stop)
        when = datetime.fromisoformat(str(bar_time)[:19])
        if at_cash_close(pair, strategy, when):
            groups["cash close"].append(slip)
            print(f"cash close: {pair} {strategy} bar {when} dir {direction:+d} slip {slip:+.4f} R")
        elif pair in CASH_CLOSE:
            groups["other index"].append(slip)
        else:
            groups["non-index"].append(slip)
    print(f"fills={len(rows)} span=[{rows[0][2]}, {rows[-1][2]}]")
    for name, values in groups.items():
        values = np.array(values)
        print(f"{name}: n={len(values)} mean={values.mean():+.4f} R median={np.median(values):+.4f} "
              f"t={values.mean() / (values.std(ddof=1) / np.sqrt(len(values))) if len(values) > 1 else float('nan'):+.2f}")
    close, other = np.array(groups["cash close"]), np.array(groups["other index"])
    statistic = welch_t(close, other)
    difference = close.mean() - other.mean() if len(close) else float("nan")
    print(f"cash close minus other index: {difference:+.4f} R welch_t={statistic:+.2f}")
    print("VERDICT=" + ("CALIBRATE" if statistic < -2 else "IMMATERIAL"))


if __name__ == "__main__":
    main()

"""The forward phase of the unchanged bot against the fixed minimum and the frozen reference.

Fixed on 2026-10-27, before the forward data it judges on 2026-11-12:
  - cohorts: the current bot's closes ordered since the two-year ranking
    (2026-10-12 22:25 UTC) and since the probation start (2026-10-21
    22:00 UTC), from the USD ledger (`journal_ledger`);
  - daily series: realised USD by UTC exit date over every calendar day
    from the cohort's start to the evaluation day, days without a close
    counting zero; the broker side beside it: TRADE and SWAP entries of
    `broker_transactions` dated in the same days, EUR at the ledger's
    fixed rate (they can include positions opened before the start);
  - reported: closes, USD, USD a calendar day, daily SD, worst day,
    maximum drawdown, a 95 % block-bootstrap interval of the daily mean
    (5-day blocks, 10,000 draws, seed 512) and the smallest daily mean the
    window could tell from zero (two standard errors);
  - verdicts only from 20 calendar days and at least one close ("too
    short" otherwise): "minimum met" when the daily mean reaches +1.00 USD and the
    interval lies above zero (EDGE_FINDINGS 456); "below the reference"
    when the interval lies below the frozen reference's +0.044042 USD a
    day; otherwise "consistent with the reference" — which, with a few
    dozen days, is no evidence of an edge.
Read-only on the journal; nothing here trades or changes the bot.
"""
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.spot_trading.journal_ledger import FIXED_USD_PER_QUOTE, PROBATION_FROM, TWO_YEAR_RANKING_FROM, ledger

MINIMUM_USD_PER_DAY = 1.00
REFERENCE_USD_PER_DAY = 0.044042
BLOCK_DAYS = 5
DRAWS = 10_000
MIN_DAYS = 20


def daily_series(amounts, first, last):
    """USD per calendar day from `first` to `last` inclusive; `amounts` is [(date, usd)]."""
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    totals = dict.fromkeys(days, 0.0)
    for day, usd in amounts:
        if day in totals:
            totals[day] += usd
    return np.array([totals[day] for day in days])


def block_interval(series, seed, blocks=BLOCK_DAYS, draws=DRAWS):
    """95 % block-bootstrap interval of the mean of `series`."""
    rng = np.random.default_rng(seed)
    n = len(series)
    blocks = min(blocks, n)
    starts = rng.integers(0, n - blocks + 1, size=(draws, int(np.ceil(n / blocks))))
    means = np.array([np.concatenate([series[s:s + blocks] for s in row])[:n].mean() for row in starts])
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def verdict(mean, low, high, days=MIN_DAYS, closes=1):
    if days < MIN_DAYS or closes == 0:
        return "too short"
    if mean >= MINIMUM_USD_PER_DAY and low > 0:
        return "minimum met"
    if high < REFERENCE_USD_PER_DAY:
        return "below the reference"
    return "consistent with the reference"


def report(name, series, closes):
    low, high = block_interval(series, 512)
    path = np.cumsum(series)
    drawdown = float((np.maximum.accumulate(np.concatenate([[0.0], path]))[1:] - path).max())
    detectable = 2 * series.std(ddof=1) / np.sqrt(len(series)) if len(series) > 1 else float("nan")
    print(f"{name}: days={len(series)} closes={closes} usd={series.sum():+.4f} usd_per_day={series.mean():+.6f} "
          f"daily_sd={series.std(ddof=1):.4f} worst_day={series.min():+.4f} max_drawdown={drawdown:.4f} "
          f"interval=[{low:+.6f}, {high:+.6f}] detectable_from_zero={detectable:.4f} "
          f"-> {verdict(series.mean(), low, high, len(series), closes)}")


def main(today=None):
    today = today or datetime.now(timezone.utc).date()
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    rows = [row for row in ledger(connection) if row["cohort"] == "current bot"]
    swaps = connection.execute(
        """SELECT date_utc, transaction_type, amount, currency FROM broker_transactions
           WHERE platform = 'capital_com' AND transaction_type IN ('TRADE', 'SWAP')""").fetchall()
    connection.close()
    for name, start in (("since the two-year ranking", TWO_YEAR_RANKING_FROM), ("since the probation start", PROBATION_FROM)):
        first = date.fromisoformat(start[:10])
        cohort = [row for row in rows if row["created_at"] >= start]
        journal = daily_series([(date.fromisoformat(row["exit_time"][:10]), row["usd"] or 0.0) for row in cohort],
                               first, today)
        report(f"journal {name}", journal, len(cohort))
        broker = daily_series([(date.fromisoformat(str(stamp)[:10]),
                                amount * (FIXED_USD_PER_QUOTE["EUR"] if currency == "EUR" else 1.0))
                               for stamp, _, amount, currency in swaps if str(stamp) >= start], first, today)
        report(f"broker TRADE+SWAP {name}", broker, sum(1 for stamp, kind, _, _ in swaps
                                                         if kind == "TRADE" and str(stamp) >= start))


if __name__ == "__main__":
    main()

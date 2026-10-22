"""Opening balance: the journal's realised USD results by cohort.

Read-only; financing (broker SWAP) is not in the journal and stays open.
"""
import os
from pathlib import Path
import sqlite3
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.spot_trading.journal_ledger import (
    PROBATION_FROM, TWO_YEAR_RANKING_FROM, VETO_REGIME_FROM, balance, ledger,
)


def main():
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    rows = ledger(connection)
    connection.close()
    capital = [row for row in rows if row["platform"] == "capital_com"]
    sections = [("whole history (Capital.com)", capital, None)]
    for name in ("retired strategies", "current strategies before the veto regime", "current bot", "kraken research"):
        sections.append((name, [row for row in rows if row["cohort"] == name], None))
    current = [row for row in rows if row["cohort"] == "current bot"]
    sections += [("current bot since the two-year ranking", current, TWO_YEAR_RANKING_FROM),
                 ("current bot since the probation start", current, PROBATION_FROM)]
    print(f"veto regime from {VETO_REGIME_FROM} UTC, two-year ranking from {TWO_YEAR_RANKING_FROM} UTC, "
          f"probation from {PROBATION_FROM} UTC")
    for name, selected, since in sections:
        result = balance(selected, since)
        print(f"{name}: closes={result['closes']} usd={result['usd']:+.2f} unconverted={result['unconverted']} "
              f"exits=[{result['first_exit']}, {result['last_exit']}] conversion={result['by_conversion']}")
    abandoned = [row for row in capital if row["outcome"] == "abandoned"]
    print(f"abandoned rows (in the totals): {len(abandoned)} usd={sum(row['usd'] or 0 for row in abandoned):+.2f}")
    print("financing: not in the journal (open)")


if __name__ == "__main__":
    main()

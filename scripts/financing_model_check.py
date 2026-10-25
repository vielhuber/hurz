"""Case check: the replay's financing model against the broker's SWAP entries, position by position.

The replay charges every 21:00 UTC rollover a position is open at the
fixed overnight rates of 2026-09-29 (`ews.CAPITAL_OVERNIGHT_RATES`), in R
`-rate/100 * entry / stop distance`, which is `-rate/100 * price * size`
in the quote currency. The broker books one SWAP entry per night in EUR,
rounded to the cent (`broker_transactions`, section 447).

Fixed before any outcome was seen:
  - attribution: a SWAP entry belongs to the journal position on that
    instrument opened before it and closed at most an hour before it
    (section 444's rule, the first such position, each entry used once);
    accepted live positions only, the open ones up to the newest imported
    entry;
  - nights: the replay's rollover count between order and exit against
    the position's SWAP entries;
  - amount per night: the fixed rate times fill (else entry) price times
    size, converted to USD at the ledger's rates and to EUR at 1.14866,
    rounded to the cent as the broker books; instruments without a fixed
    rate are counted but left out of the amount comparison;
  - verdict per window (before and since 2026-09-29, the day the rates
    were read): "consistent" when |broker - model| <= max(0.10 EUR,
    25 % of |broker|) over the positions with a rate, else "calibrate".
Read-only on the journal and the transactions; nothing here trades or
changes the bot.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.spot_trading.journal_ledger import FIXED_USD_PER_QUOTE, quote_currency
from scripts.efficiency_weighted_selection import CAPITAL_OVERNIGHT_RATES, rollovers

USD_PER_EUR = FIXED_USD_PER_QUOTE["EUR"]
RATES_READ = "2026-09-29 00:20:00"
ATTRIBUTION_SLACK = timedelta(hours=1)
TOLERANCE_EUR = 0.10
TOLERANCE_SHARE = 0.25


def utc(text):
    return datetime.fromisoformat(str(text)[:19])


def night_eur(pair, direction, price, size):
    """The replay's charge for one night in EUR as the broker books it (credit positive), or None without a fixed rate."""
    usd_per_quote = 1.0 if quote_currency(pair) == "USD" else FIXED_USD_PER_QUOTE.get(quote_currency(pair))
    if pair not in CAPITAL_OVERNIGHT_RATES or usd_per_quote is None:
        return None
    rate = CAPITAL_OVERNIGHT_RATES[pair][0 if direction > 0 else 1]
    return round(rate / 100.0 * price * size * usd_per_quote / USD_PER_EUR, 2)


def compare(positions, swaps, now):
    """Per position: model nights and EUR against the attributed SWAP entries and EUR."""
    out, used = [], set()
    for position in positions:
        opened = utc(position["created_at"])
        closed = utc(position["exit_time"]) if position["exit_time"] else now
        held = [i for i, (moment, pair, _) in enumerate(swaps) if i not in used
                and pair == position["pair"] and opened < moment <= closed + ATTRIBUTION_SLACK]
        used.update(held)
        booked = [swaps[i][2] for i in held]
        nights = rollovers(np.datetime64(opened), np.datetime64(closed))
        price = position["fill_price"] if position["fill_price"] is not None else position["entry_price"]
        per_night = night_eur(position["pair"], position["direction"], price, position["size"])
        out.append({**position, "model_nights": nights, "swap_entries": len(booked),
                    "broker_eur": sum(booked), "model_eur": None if per_night is None else per_night * nights})
    return out


def verdict(rows):
    rated = [row for row in rows if row["model_eur"] is not None]
    broker = sum(row["broker_eur"] for row in rated)
    model = sum(row["model_eur"] for row in rated)
    ok = abs(broker - model) <= max(TOLERANCE_EUR, TOLERANCE_SHARE * abs(broker))
    return {"positions": len(rows), "rated": len(rated), "broker_eur": broker, "model_eur": model,
            "nights_match": sum(row["model_nights"] == row["swap_entries"] for row in rows),
            "verdict": "consistent" if ok else "calibrate"}


def main():
    path = Path(os.getenv("DB_PATH", _ROOT / "data" / "hurz.sqlite")).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    positions = [dict(row) for row in connection.execute(
        """SELECT id, pair, strategy, direction, size, entry_price, fill_price, created_at, exit_time
           FROM spot_trades WHERE platform = 'capital_com' AND accepted = 1 AND paper_mode = 0
           AND size IS NOT NULL ORDER BY created_at""")]
    swaps = [(utc(row["date_utc"]), row["instrument"], row["amount"]) for row in connection.execute(
        """SELECT date_utc, instrument, amount FROM broker_transactions
           WHERE platform = 'capital_com' AND transaction_type = 'SWAP'""")]
    connection.close()
    rows = compare(positions, swaps, max(moment for moment, _, _ in swaps))
    attributed = sum(row["swap_entries"] for row in rows)
    print(f"SWAP entries {len(swaps)} ({sum(s[2] for s in swaps):+.2f} EUR), attributed to a position {attributed}")
    for name, selected in (("before the rates were read", [r for r in rows if r["created_at"] < RATES_READ]),
                           ("since the rates were read", [r for r in rows if r["created_at"] >= RATES_READ])):
        v = verdict(selected)
        print(f"{name}: positions={v['positions']} with a rate={v['rated']} nights match={v['nights_match']} "
              f"broker {v['broker_eur']:+.2f} EUR, model {v['model_eur']:+.2f} EUR -> {v['verdict']}")
    by_pair = {}
    for row in rows:
        if row["model_eur"] is None:
            continue
        entry = by_pair.setdefault(row["pair"], [0, 0.0, 0.0])
        entry[0] += row["swap_entries"]; entry[1] += row["broker_eur"]; entry[2] += row["model_eur"]
    for pair, (count, broker, model) in sorted(by_pair.items(), key=lambda item: item[1][1]):
        if count:
            print(f"  {pair}: {count} entries, broker {broker:+.2f} EUR, model {model:+.2f} EUR")
    for row in rows:
        if row["created_at"] >= RATES_READ:
            print(f"  since: {row['pair']} {row['strategy']} {row['direction']:+d} {row['created_at'][:16]}: "
                  f"nights {row['model_nights']}/{row['swap_entries']} broker {row['broker_eur']:+.2f} "
                  f"model {row['model_eur'] if row['model_eur'] is None else round(row['model_eur'], 2)} EUR")


if __name__ == "__main__":
    main()

"""The journal's realised results in one unit (USD), split into cohorts.

Rows closed since `USD_BOOKING_FROM` carry `realized_pnl` in USD. Older
rows booked it in the instrument's quote currency and against the signal
price, so their result is recomputed from fill and exit and converted:
USD-quoted instruments need nothing, rows with a journaled USD risk use
its rate, the rest use the fixed rates below and are counted as such.
Financing (broker SWAP entries) is not in the journal and is not here.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

USD_BOOKING_FROM = "2026-09-07 23:05:00"
# The bot trades only momentum and the 4h pins since the strategy veto
# retired turtle_breakout; the two-year ranking went live with 6fe17ae.
VETO_REGIME_FROM = "2026-10-01 02:02:00"
TWO_YEAR_RANKING_FROM = "2026-10-12 22:25:00"
PROBATION_FROM = "2026-10-21 22:00:00"
CURRENT_STRATEGIES = {"momentum", "donchian_breakout_4h", "turtle_breakout_4h", "momentum_4h"}

_USD_QUOTED = {"US30", "US100", "US500", "GOLD", "SILVER", "COPPER", "PALLADIUM", "PLATINUM",
               "OIL_BRENT", "OIL_CRUDE", "WHEAT", "CORN", "NATURALGAS"}
_INDEX_QUOTE = {"DE40": "EUR", "FR40": "EUR", "EU50": "EUR", "UK100": "GBP", "J225": "JPY",
                "HK50": "HKD", "AU200": "AUD"}
# USD per unit of quote currency from the bar cache's instrument data of
# 2026-10-12. Applied only to legacy rows without a journaled USD risk
# (56 closes, a few USD in total); exchange-rate drift since May moves
# that by well under 1 USD.
FIXED_USD_PER_QUOTE = {"EUR": 1.14866, "GBP": 1.33946, "JPY": 0.0063743, "CHF": 1.21643,
                       "HKD": 0.127455, "AUD": 0.7125, "CAD": 0.70709}


def quote_currency(pair: str) -> Optional[str]:
    if pair in _USD_QUOTED or pair.endswith("USD"):
        return "USD"
    if pair in _INDEX_QUOTE:
        return _INDEX_QUOTE[pair]
    if len(pair) == 6 and pair.isalpha():
        return pair[3:]
    return None


def usd_result(row: Dict) -> tuple:
    """(result in USD or None, how it was converted)."""
    if row["exit_time"] >= USD_BOOKING_FROM and row["realized_pnl"] is not None:
        return float(row["realized_pnl"]), "booked in USD"
    if row["exit_price"] is not None and row["fill_price"] is not None:
        quote = (row["exit_price"] - row["fill_price"]) * row["direction"] * row["size"]
    else:
        quote = row["realized_pnl"]
    if quote is None:
        return None, "no result"
    currency = quote_currency(row["pair"])
    if currency == "USD":
        return float(quote), "USD-quoted"
    usd_risk = row["fill_risk_usd"] if row["fill_risk_usd"] is not None else row["planned_risk_usd"]
    risk = abs((row["fill_price"] if row["fill_price"] is not None else row["entry_price"])
               - row["stop_loss"]) * row["size"] if row["stop_loss"] is not None else 0.0
    if usd_risk is not None and risk > 0:
        return float(quote) * usd_risk / risk, "journaled USD risk"
    if currency in FIXED_USD_PER_QUOTE:
        return float(quote) * FIXED_USD_PER_QUOTE[currency], "fixed rate"
    return None, "unconverted"


def cohort(row: Dict) -> str:
    if row["platform"] != "capital_com":
        return "kraken research"
    if row["strategy"] not in CURRENT_STRATEGIES:
        return "retired strategies"
    if row["created_at"] < VETO_REGIME_FROM:
        return "current strategies before the veto regime"
    return "current bot"


def ledger(connection) -> List[Dict]:
    """Every accepted, closed journal row with its USD result and cohort."""
    cursor = connection.execute("""
        SELECT id, platform, pair, strategy, direction, entry_price, stop_loss, size,
               fill_price, exit_price, exit_time, created_at, outcome, realized_pnl,
               planned_risk_usd, fill_risk_usd
        FROM spot_trades
        WHERE accepted = 1 AND paper_mode = 0 AND exit_time IS NOT NULL
          AND (realized_pnl IS NOT NULL OR (exit_price IS NOT NULL AND fill_price IS NOT NULL))
        ORDER BY exit_time""")
    names = [column[0] for column in cursor.description]
    rows = []
    for values in cursor.fetchall():
        row = {name: (str(value) if name in ("exit_time", "created_at") and value is not None else value)
               for name, value in zip(names, values)}
        row["usd"], row["conversion"] = usd_result(row)
        row["cohort"] = cohort(row)
        rows.append(row)
    return rows


def balance(rows: Iterable[Dict], since: Optional[str] = None) -> Dict:
    """Closes, USD sum and conversion counts, optionally for entries since `since`."""
    selected = [row for row in rows if since is None or row["created_at"] >= since]
    converted = [row for row in selected if row["usd"] is not None]
    by_conversion: Dict[str, int] = {}
    for row in selected:
        by_conversion[row["conversion"]] = by_conversion.get(row["conversion"], 0) + 1
    return {"closes": len(selected), "usd": sum(row["usd"] for row in converted),
            "unconverted": len(selected) - len(converted), "by_conversion": by_conversion,
            "first_exit": selected[0]["exit_time"] if selected else None,
            "last_exit": selected[-1]["exit_time"] if selected else None}

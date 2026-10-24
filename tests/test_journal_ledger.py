from __future__ import annotations

import sqlite3
import unittest

from app.spot_trading import journal_ledger
from scripts import generate_dashboard


def _journal(rows):
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """CREATE TABLE spot_trades (id INTEGER, platform TEXT, pair TEXT, strategy TEXT,
           direction INTEGER, entry_price REAL, stop_loss REAL, size REAL, accepted INTEGER,
           fill_price REAL, paper_mode INTEGER, exit_price REAL, exit_time TEXT, created_at TEXT,
           outcome TEXT, realized_pnl REAL, planned_risk_usd REAL, fill_risk_usd REAL,
           deal_id TEXT)""")
    for number, row in enumerate(rows):
        values = {"id": number, "platform": "capital_com", "direction": 1, "entry_price": 100.0,
                  "stop_loss": 99.0, "size": 1.0, "accepted": 1, "fill_price": 100.0, "paper_mode": 0,
                  "exit_price": 101.0, "outcome": "manual", "realized_pnl": 0.0,
                  "planned_risk_usd": None, "fill_risk_usd": None, "deal_id": "d", **row}
        connection.execute(f"INSERT INTO spot_trades ({', '.join(values)}) VALUES "
                           f"({', '.join('?' for _ in values)})", tuple(values.values()))
    return connection


class JournalLedgerTest(unittest.TestCase):
    def test_each_row_is_one_usd_amount_however_it_was_booked(self):
        rows = journal_ledger.ledger(_journal([
            # booked in USD after the switch: the column is read
            {"pair": "EURJPY", "strategy": "turtle_breakout", "exit_time": "2026-09-20 10:00:00",
             "created_at": "2026-09-19 10:00:00", "realized_pnl": 0.66},
            # legacy USD-quoted row booked against the signal: recomputed from the fill
            {"pair": "US30", "strategy": "momentum", "exit_time": "2026-08-10 10:00:00",
             "created_at": "2026-08-09 10:00:00", "realized_pnl": 5.0},
            # legacy yen row with a journaled USD risk of 2 for a risk of 300 JPY
            {"pair": "USDJPY", "strategy": "donchian_breakout", "exit_time": "2026-08-25 10:00:00",
             "created_at": "2026-08-24 10:00:00", "size": 300.0, "fill_risk_usd": 2.0},
            # legacy euro row without one: the fixed rate
            {"pair": "DE40", "strategy": "donchian_breakout", "exit_time": "2026-07-01 10:00:00",
             "created_at": "2026-06-30 10:00:00"},
        ]))
        by_pair = {row["pair"]: row for row in rows}
        self.assertAlmostEqual(0.66, by_pair["EURJPY"]["usd"])
        self.assertAlmostEqual(1.0, by_pair["US30"]["usd"])
        self.assertAlmostEqual(2.0, by_pair["USDJPY"]["usd"])
        self.assertAlmostEqual(journal_ledger.FIXED_USD_PER_QUOTE["EUR"], by_pair["DE40"]["usd"])
        self.assertEqual("fixed rate", by_pair["DE40"]["conversion"])

    def test_cohorts_separate_retired_strategies_from_the_current_bot(self):
        rows = journal_ledger.ledger(_journal([
            {"pair": "US30", "strategy": "turtle_breakout", "exit_time": "2026-10-02 10:00:00",
             "created_at": "2026-09-30 10:00:00"},
            {"pair": "US30", "strategy": "momentum", "exit_time": "2026-09-02 10:00:00",
             "created_at": "2026-09-01 10:00:00"},
            {"pair": "US30", "strategy": "momentum", "exit_time": "2026-10-14 10:00:00",
             "created_at": "2026-10-13 18:00:00"},
        ]))
        self.assertEqual(["current strategies before the veto regime", "retired strategies", "current bot"],
                         [row["cohort"] for row in rows])
        self.assertEqual(1, journal_ledger.balance(rows, journal_ledger.TWO_YEAR_RANKING_FROM)["closes"])
        self.assertEqual(0, journal_ledger.balance(rows, journal_ledger.PROBATION_FROM)["closes"])

    def test_an_unknown_quote_currency_is_left_unconverted(self):
        rows = journal_ledger.ledger(_journal([
            {"pair": "XYZ123", "strategy": "momentum", "exit_time": "2026-07-01 10:00:00",
             "created_at": "2026-06-30 10:00:00"}]))
        result = journal_ledger.balance(rows)
        self.assertEqual(1, result["unconverted"])
        self.assertEqual(0.0, result["usd"])


class DashboardCohortCardTest(unittest.TestCase):
    def _card(self, journal_rows):
        return generate_dashboard._render_cards(journal_ledger.ledger(_journal(journal_rows)), [])

    def test_the_headline_is_the_current_bot_and_history_is_labelled_as_such(self):
        html = self._card([
            {"pair": "US30", "strategy": "bollinger_rev", "exit_time": "2026-07-02 10:00:00",
             "created_at": "2026-07-01 10:00:00"},
            {"pair": "US30", "strategy": "momentum", "exit_time": "2026-10-14 10:00:00",
             "created_at": "2026-10-13 18:00:00", "realized_pnl": 0.76},
        ])
        self.assertIn("aktueller Bot seit 01.10.2026", html)
        self.assertIn("+$0.76", html)
        self.assertIn("stillgelegte Strategien (1 Trades)", html)
        self.assertIn("kein Nachweis für den aktuellen Bot", html)
        self.assertIn("noch keine Buchungen importiert", html)
        self.assertIn("Bewährungsfrist seit 22.10.2026 (0 Trades", html)

    def test_the_broker_side_is_shown_beside_the_journal(self):
        broker = [{"date_utc": "2026-09-20 21:00:00", "transaction_type": "SWAP", "amount": -0.5, "currency": "EUR"},
                  {"date_utc": "2026-10-14 18:00:30", "transaction_type": "TRADE", "amount": 0.65, "currency": "EUR"},
                  {"date_utc": "2026-10-14 21:00:00", "transaction_type": "SWAP", "amount": -0.02, "currency": "EUR"}]
        html = generate_dashboard._render_cards([], [], broker)
        rate = journal_ledger.FIXED_USD_PER_QUOTE["EUR"]
        self.assertIn(f"Trades +${0.65 * rate:,.2f}", html)
        self.assertIn(f"Swap −${0.02 * rate:,.2f}", html)
        self.assertIn("Broker-Konto seit 08.05.", html)

    def test_no_projection_of_selected_combinations(self):
        self.assertFalse(hasattr(generate_dashboard, "_render_projection"))


if __name__ == "__main__":
    unittest.main()

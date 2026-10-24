import unittest

from scripts import broker_ledger


def _close(pair, created, exit_time, usd, cohort="current bot"):
    return {"pair": pair, "created_at": created, "exit_time": exit_time, "usd": usd, "cohort": cohort}


class BrokerLedgerTest(unittest.TestCase):
    def test_trades_match_closes_and_swaps_go_to_the_position_held_over_the_rollover(self):
        closes = [_close("US30", "2026-10-13 18:00:05", "2026-10-14 18:00:00", 0.76),
                  _close("UK100", "2026-10-19 09:00:14", "2026-10-20 09:00:00", 0.97)]
        transactions = [
            {"transactionType": "TRADE", "instrumentName": "US30", "dateUtc": "2026-10-14T18:00:30",
             "size": "0.65", "currency": "EUR"},
            {"transactionType": "SWAP", "instrumentName": "US30", "dateUtc": "2026-10-13T21:00:00",
             "size": "-0.02", "currency": "EUR"},
            {"transactionType": "SWAP", "instrumentName": "GOLD", "dateUtc": "2026-10-13T21:00:00",
             "size": "-0.01", "currency": "EUR"},
        ]
        cohorts, unmatched_closes, unmatched_trades, unattributed = broker_ledger.reconcile(transactions, closes)
        rate = broker_ledger.FIXED_USD_PER_QUOTE["EUR"]
        self.assertEqual(1, cohorts["current bot"]["matched"])
        self.assertAlmostEqual(0.65 * rate, cohorts["current bot"]["broker"])
        self.assertAlmostEqual(-0.02 * rate, cohorts["current bot"]["swap"])
        self.assertEqual(["UK100"], [row["pair"] for row in unmatched_closes])
        self.assertEqual([], unmatched_trades)
        self.assertEqual(["GOLD"], [row["instrumentName"] for row in unattributed])


if __name__ == "__main__":
    unittest.main()

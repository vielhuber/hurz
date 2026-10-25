from datetime import datetime
import unittest

from scripts import financing_model_check as check


def position(**fields):
    base = {"id": 1, "pair": "NZDUSD", "strategy": "donchian_breakout_4h", "direction": 1, "size": 1000.0,
            "entry_price": 0.6, "fill_price": 0.6, "created_at": "2026-10-21 20:00:41",
            "exit_time": "2026-10-24 10:00:00"}
    return {**base, **fields}


class FinancingModelCheckTest(unittest.TestCase):
    def test_a_night_is_charged_at_the_fixed_rate_in_eur_rounded_to_the_cent(self):
        # 0.0078 % of 600 USD is 0.0468 USD, 0.041 EUR
        self.assertEqual(-0.04, check.night_eur("NZDUSD", 1, 0.6, 1000.0))
        self.assertIsNone(check.night_eur("DOTUSD", 1, 5.0, 10.0))

    def test_swap_entries_are_attributed_to_the_position_held_over_the_rollover(self):
        swaps = [(datetime(2026, 10, 21, 21, 0, 49), "NZDUSD", -0.04),
                 (datetime(2026, 10, 22, 21, 2, 27), "NZDUSD", -0.04),
                 (datetime(2026, 10, 23, 21, 2, 21), "NZDUSD", -0.04),
                 (datetime(2026, 10, 22, 21, 2, 27), "US30", -0.01),
                 (datetime(2026, 10, 25, 21, 0, 0), "NZDUSD", -0.04)]
        row, = check.compare([position()], swaps, now=datetime(2026, 10, 26))
        self.assertEqual((3, 3), (row["model_nights"], row["swap_entries"]))
        self.assertAlmostEqual(-0.12, row["broker_eur"])
        self.assertAlmostEqual(-0.12, row["model_eur"])
        self.assertEqual("consistent", check.verdict([row])["verdict"])

    def test_an_entry_is_attributed_once_when_two_positions_overlap(self):
        swaps = [(datetime(2026, 10, 22, 21, 2, 27), "NZDUSD", -0.04)]
        first, second = check.compare([position(), position(id=2)], swaps, now=datetime(2026, 10, 26))
        self.assertEqual((1, 0), (first["swap_entries"], second["swap_entries"]))

    def test_a_model_far_from_the_broker_asks_for_calibration(self):
        rows = [{"model_eur": -0.10, "broker_eur": -0.50, "model_nights": 5, "swap_entries": 5}]
        self.assertEqual("calibrate", check.verdict(rows)["verdict"])


if __name__ == "__main__":
    unittest.main()

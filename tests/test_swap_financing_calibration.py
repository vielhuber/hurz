from datetime import date, datetime, timezone
from unittest import TestCase

import scripts.swap_financing_calibration as experiment


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class SwapFinancingCalibrationTest(TestCase):
    def test_only_rollovers_strictly_inside_the_holding_count(self):
        self.assertEqual([date(2026, 9, 10), date(2026, 9, 11)],
                         experiment.rollover_dates(at("2026-09-10T20:00"), at("2026-09-12T09:00")))
        self.assertEqual([], experiment.rollover_dates(at("2026-09-10T21:30"), at("2026-09-11T20:00")))

    def test_the_replay_charges_the_sides_rate_on_the_usd_notional(self):
        positions = [{"pair": "US500", "direction": 1, "notional_usd": 200.0,
                      "opened": at("2026-09-10T15:00"), "closed": at("2026-09-11T10:00")},
                     {"pair": "WHEAT", "direction": 1, "notional_usd": 200.0,
                      "opened": at("2026-09-10T15:00"), "closed": at("2026-09-11T10:00")}]
        predicted = experiment.predicted_by_night(positions, {"US500": (-0.02, 0.001)})
        self.assertEqual({("US500", date(2026, 9, 10)): 0.04}, {k: round(v, 10) for k, v in predicted.items()})

    def test_booked_fees_are_costs_in_usd_per_instrument_and_night(self):
        swaps = [{"instrumentName": "EURJPY", "dateUtc": "2026-09-30T21:02:10.837", "size": "-0.02",
                  "currency": "EUR"},
                 {"instrumentName": "EURJPY", "dateUtc": "2026-09-30T21:02:11.000", "size": "-0.01",
                  "currency": "EUR"}]
        booked = experiment.booked_by_night(swaps, 1.1)
        self.assertAlmostEqual(0.033, booked[("EURJPY", date(2026, 9, 30))])

from __future__ import annotations

import json
import unittest

from app.spot_trading.autotrade import _CORRELATION_CLUSTERS


class CorrelationClusterMapTest(unittest.TestCase):
    """Three same-direction shorts on HK50, CHFJPY and AUDJPY were open with
    two of them outside every cluster. A year of hourly returns put EU50
    at 0.93 with DE40, COPPER at 0.58 with the metals and the yen crosses
    at 0.60 with each other; the measured singletons stay unmapped."""

    def test_measured_members_are_mapped(self):
        self.assertEqual("indices", _CORRELATION_CLUSTERS["EU50"])
        self.assertEqual("metals", _CORRELATION_CLUSTERS["COPPER"])
        self.assertEqual("jpy_crosses", _CORRELATION_CLUSTERS["AUDJPY"])
        self.assertEqual("jpy_crosses", _CORRELATION_CLUSTERS["CHFJPY"])

    def test_measured_singletons_stay_uncapped(self):
        for pair in ("EURAUD", "AUDNZD", "GBPCAD"):
            with self.subTest(pair=pair):
                self.assertNotIn(pair, _CORRELATION_CLUSTERS)

    def test_every_active_instrument_is_mapped_or_a_known_singleton(self):
        with open("data/active_pairs.capital_com.json", encoding="utf-8") as handle:
            active = {row["pair"] for row in json.load(handle)["pairs"]}
        unmapped = sorted(active - set(_CORRELATION_CLUSTERS))
        self.assertEqual(["AUDNZD", "EURAUD", "GBPCAD"], unmapped)


if __name__ == "__main__":
    unittest.main()

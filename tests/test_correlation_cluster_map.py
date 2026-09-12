from __future__ import annotations

import json
import unittest

from app.spot_trading.autotrade import _CORRELATION_CLUSTERS


class CorrelationClusterMapTest(unittest.TestCase):
    """Three same-direction shorts on HK50, CHFJPY and AUDJPY were open with
    two of them outside every cluster. A year of hourly returns put EU50
    at 0.93 with DE40, COPPER at 0.58 with the metals and the yen crosses
    at 0.60 with each other. EDGE_FINDINGS 241 then audited the map on two
    disjoint multi-year windows and merged indices, USD crosses and yen
    crosses into `risk_on`, because EURAUD/AUDJPY, EURAUD/NZDUSD and
    AUDJPY/J225 breach the map's own 0.5 criterion on both."""

    def test_measured_members_are_mapped(self):
        self.assertEqual("risk_on", _CORRELATION_CLUSTERS["EU50"])
        self.assertEqual("metals", _CORRELATION_CLUSTERS["COPPER"])
        for pair in ("AUDJPY", "CHFJPY", "EURJPY", "GBPJPY", "CADJPY"):
            with self.subTest(pair=pair):
                self.assertEqual("risk_on", _CORRELATION_CLUSTERS[pair])
        self.assertEqual("risk_on", _CORRELATION_CLUSTERS["USDJPY"])

    def test_the_audited_breaches_share_a_cluster(self):
        """The three pairs that broke the 0.5 rule on both windows."""
        for a, b in (("EURAUD", "AUDJPY"), ("EURAUD", "NZDUSD"),
                     ("AUDJPY", "J225")):
            with self.subTest(pair=(a, b)):
                self.assertEqual(_CORRELATION_CLUSTERS[a],
                                 _CORRELATION_CLUSTERS[b])

    def test_the_repair_only_merged(self):
        """No instrument left a cluster it shared before the merge."""
        for group in (("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY",
                       "USDCAD", "USDCHF"),
                      ("DE40", "FR40", "UK100", "US30", "US500", "US100",
                       "HK50", "J225", "AU200", "EU50"),
                      ("GOLD", "SILVER", "PALLADIUM", "COPPER"),
                      ("OIL_BRENT", "OIL_CRUDE")):
            with self.subTest(group=group[0]):
                self.assertEqual(
                    1, len({_CORRELATION_CLUSTERS[p] for p in group}))

    def test_measured_singletons_stay_uncapped(self):
        for pair in ("AUDNZD", "GBPCAD"):
            with self.subTest(pair=pair):
                self.assertNotIn(pair, _CORRELATION_CLUSTERS)

    def test_every_active_instrument_is_mapped_or_a_known_singleton(self):
        with open("data/active_pairs.capital_com.json", encoding="utf-8") as handle:
            active = {row["pair"] for row in json.load(handle)["pairs"]}
        unmapped = set(active) - set(_CORRELATION_CLUSTERS)
        # Equality against a fixed list broke whenever the nightly
        # selection legitimately changed — GBPCAD left the list when
        # section 192 blocked it and the test failed on that alone. The
        # property to protect is that nothing unmeasured goes uncapped.
        self.assertEqual(set(), unmapped - {"AUDNZD", "GBPCAD"})


if __name__ == "__main__":
    unittest.main()

from unittest import TestCase
from unittest.mock import patch

import scripts.additional_indices as replay
import scripts.efficiency_weighted_selection as base
from app.spot_trading.trading_blocks import UNIVERSE_BLOCKED_PAIRS
from scripts.selector_universe import CANDIDATES, configure


class SelectorUniverseTest(TestCase):
    def test_candidates_are_the_selector_instruments_outside_the_replay(self):
        self.assertEqual(11, len(CANDIDATES))
        self.assertFalse(set(CANDIDATES) & set(base.PAIRS))
        self.assertEqual(UNIVERSE_BLOCKED_PAIRS, set(CANDIDATES))

    def test_configure_keeps_live_costs_and_the_live_cluster_map(self):
        with patch.multiple(replay, SPREAD_PERCENT={"X": 1.0}, CANDIDATES=[], FEES={},
                            CLUSTERS={"X": "y"}, SHORT_BLOCKED={"X"}):
            with patch("scripts.selector_universe._fee_for", lambda platform, pair: 0.0001):
                configure()
            self.assertEqual(CANDIDATES, replay.CANDIDATES)
            self.assertEqual({}, replay.CLUSTERS)
            self.assertEqual(set(), replay.SHORT_BLOCKED)
            self.assertEqual({pair: 0.0001 for pair in CANDIDATES}, replay.FEES)

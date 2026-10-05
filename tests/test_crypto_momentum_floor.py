from unittest import TestCase
from unittest.mock import patch

import scripts.crypto_momentum_floor as experiment
import scripts.efficiency_weighted_selection as ews


def fake_signals(frames, floor, meta):
    return [{"strat": strategy, "pair": pair, "ts": 0, "floor": ews.trade_terms(None, 0, pair, meta, floor)}
            for pair in sorted(frames) for strategy in ews.STRATS]


class CryptoMomentumFloorTest(TestCase):
    def test_only_crypto_momentum_is_priced_without_the_floor(self):
        frames = {"BTCUSD": None, "US500": None}
        with patch.object(ews, "all_signals", fake_signals), \
             patch.object(ews, "trade_terms", lambda df, e, pair, meta, floor: floor):
            rows = experiment.with_crypto_momentum(frames, 3.0, {})
        floors = {(row["strat"], row["pair"]): row["floor"] for row in rows}
        self.assertEqual(0.0, floors[("momentum", "BTCUSD")])
        self.assertEqual(3.0, floors[("turtle_breakout", "BTCUSD")])
        self.assertEqual(3.0, floors[("momentum", "US500")])
        self.assertEqual(["donchian_breakout", "momentum", "turtle_breakout"], ews.STRATS)

from unittest import TestCase

import pandas as pd

import scripts.momentum_adx_floor as experiment


def blocked(strategy, df, index):
    return experiment.RegimeDecision(True, "base", None, "base")


class MomentumAdxFloorTest(TestCase):
    def test_only_momentum_moves_to_the_candidate_floor_below_the_ceiling(self):
        gate = experiment.momentum_gate(blocked, 25.0)
        df = pd.DataFrame({"adx_14": [24.9, 25.0, 29.9, 50.0, float("nan")]})
        self.assertEqual([True, False, False, True, True],
                         [gate("momentum", df, i).blocked for i in range(5)])
        self.assertTrue(gate("donchian_breakout", df, 1).blocked)

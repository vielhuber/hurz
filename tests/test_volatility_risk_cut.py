from unittest import TestCase

import numpy as np

from scripts.volatility_risk_cut import volatile


def closed(day_offset, usd):
    base = np.datetime64("2026-01-01T12")
    return dict(exit_ts=base + np.timedelta64(day_offset * 24, "h"), usd=usd)


class VolatilityRiskCutTest(TestCase):
    def test_a_recent_swing_marks_the_state_volatile(self):
        calm = [closed(i, 0.1) for i in range(0, 360, 3)]
        swing = [closed(360, -8.0), closed(362, 6.0)]
        self.assertTrue(volatile(calm + swing, np.datetime64("2026-12-31")))

    def test_an_old_swing_with_a_calm_recent_stretch_is_not_volatile(self):
        swing = [closed(10, -8.0), closed(12, 6.0)]
        calm = [closed(i, 0.1) for i in range(20, 365, 3)]
        self.assertFalse(volatile(swing + calm, np.datetime64("2026-12-31")))

    def test_trades_closing_on_or_after_the_day_are_not_known_yet(self):
        calm = [closed(i, 0.1) for i in range(0, 360, 3)]
        future = [closed(364, -8.0), closed(365, 6.0)]
        self.assertFalse(volatile(calm + future, np.datetime64("2026-12-31")))

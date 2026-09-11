from __future__ import annotations

import unittest

from app.spot_trading.position_sizing import (
    DEFAULT_NOTIONAL_CAP_USD,
    DEFAULT_TARGET_RISK_USD,
    calculate_position_size,
)


class NotionalCapScalingTest(unittest.TestCase):
    """Section 205/206: at a venue-pinned 1.05 % stop the notional term is
    already the binding one at base risk, so a raised risk budget with a
    fixed cap buys nothing."""

    ENTRY = 52500.0

    def _size(self, target_risk: float, cap: float):
        stop = self.ENTRY * (1 - 0.0105)
        return calculate_position_size(
            entry_price=self.ENTRY, stop_loss=stop, target_risk=target_risk,
            notional_cap=cap, size_increment=0.0001,
        )

    def test_a_fixed_cap_makes_a_raised_budget_inert(self) -> None:
        base = self._size(DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD)
        doubled = self._size(DEFAULT_TARGET_RISK_USD * 2,
                             DEFAULT_NOTIONAL_CAP_USD)

        self.assertEqual(base.notional, doubled.notional)
        self.assertEqual(base.planned_risk, doubled.planned_risk)

    def test_scaling_the_cap_alongside_makes_it_effective(self) -> None:
        base = self._size(DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD)
        scaled = self._size(DEFAULT_TARGET_RISK_USD * 2,
                            DEFAULT_NOTIONAL_CAP_USD * 2)

        self.assertGreater(scaled.planned_risk, base.planned_risk * 1.9)

    def test_the_base_case_is_unchanged_by_a_scale_of_one(self) -> None:
        base = self._size(DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD)
        same = self._size(DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD * 1.0)

        self.assertEqual(base.planned_risk, same.planned_risk)


if __name__ == "__main__":
    unittest.main()

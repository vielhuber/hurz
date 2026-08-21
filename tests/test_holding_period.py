from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.spot_trading.holding_period import (
    stale_exit_after_seconds,
    stale_exits_enabled,
)


class StaleExitAfterSecondsTest(unittest.TestCase):
    def test_uses_default_bar_budget_for_one_hour_strategy(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(24 * 3600, stale_exit_after_seconds(
                "donchian_breakout", "1h",
            ))

    def test_uses_strategy_resolution_for_four_hour_variant(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(24 * 4 * 3600, stale_exit_after_seconds(
                "donchian_breakout_4h", "1h",
            ))

    def test_uses_extended_bar_budget_for_trailing_strategy(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(240 * 3600, stale_exit_after_seconds(
                "donchian_trail", "1h",
            ))

    def test_disables_all_stale_exits_when_budget_is_not_positive(self) -> None:
        with patch.dict(os.environ, {"HURZ_MAX_HOLD_BARS": "0"}, clear=True):
            self.assertFalse(stale_exits_enabled())
            self.assertIsNone(stale_exit_after_seconds(
                "donchian_trail", "1h",
            ))

    def test_falls_back_to_default_for_invalid_environment_value(self) -> None:
        with patch.dict(os.environ, {"HURZ_MAX_HOLD_BARS": "invalid"}, clear=True):
            self.assertEqual(24 * 3600, stale_exit_after_seconds(
                "donchian_breakout", "1h",
            ))

    def test_environment_changes_default_but_not_strategy_override(self) -> None:
        with patch.dict(os.environ, {"HURZ_MAX_HOLD_BARS": "12"}, clear=True):
            self.assertEqual(12 * 3600, stale_exit_after_seconds(
                "donchian_breakout", "1h",
            ))
            self.assertEqual(240 * 3600, stale_exit_after_seconds(
                "donchian_trail", "1h",
            ))


if __name__ == "__main__":
    unittest.main()

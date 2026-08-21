from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scripts.generate_dashboard import _render_open


class RenderOpenPositionsTest(unittest.TestCase):
    def _position(self, strategy: str, age_hours: int) -> dict:
        return {
            "platform": "capital_com",
            "pair": "TEST",
            "strategy": strategy,
            "direction": 1,
            "created_at": datetime.now(timezone.utc) - timedelta(hours=age_hours),
        }

    def test_trailing_position_is_not_overdue_after_default_budget(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            html = _render_open([self._position("donchian_trail", 55)])

        self.assertNotIn("überfällig", html)
        self.assertNotIn("kein Limit", html)

    def test_disabled_stale_exit_is_shown_as_unlimited(self) -> None:
        with patch.dict(os.environ, {"HURZ_MAX_HOLD_BARS": "0"}, clear=True):
            html = _render_open([self._position("donchian_trail", 500)])

        self.assertIn("kein Limit", html)
        self.assertNotIn("überfällig", html)

    def test_default_strategy_stays_overdue_after_24_bars(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            html = _render_open([self._position("donchian_breakout", 25)])

        self.assertIn("überfällig", html)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.spot_trading import autotrade


class RealizedPnlCurrencyTest(unittest.IsolatedAsyncioTestCase):
    """The bar-walk prices a closure in the quote currency; the journal is
    USD. A HK50 stop-out is 21 HKD, not 21 USD."""

    def _row(self):
        return {"pair": "HK50", "direction": -1, "bar_time": datetime(2026, 9, 7, 22, tzinfo=timezone.utc),
                "fill_price": 25258.1, "stop_loss": 25558.1, "take_profit": 24804.4, "size": 0.07}

    async def _resolve(self, platform):
        quote_payload = {"exit_time": datetime(2026, 9, 8, 1, tzinfo=timezone.utc), "exit_price": 25558.1,
                         "outcome": "loss", "realized_pnl": -21.0}
        async def fake(_platform, _row):
            return dict(quote_payload)
        with patch.object(autotrade, "_resolve_closed_trade_in_quote", fake):
            return await autotrade._resolve_closed_trade(platform, self._row())

    async def test_quote_pnl_is_converted_with_the_venue_rate(self):
        async def prepare_order(**_kwargs):
            return SimpleNamespace(usd_per_quote=0.1276)
        payload = await self._resolve(SimpleNamespace(prepare_order=prepare_order))
        self.assertAlmostEqual(-21.0 * 0.1276, payload["realized_pnl"])

    async def test_a_venue_without_rates_books_one_to_one(self):
        payload = await self._resolve(SimpleNamespace())
        self.assertEqual(-21.0, payload["realized_pnl"])

    async def test_an_unknown_rate_leaves_the_pnl_unknown(self):
        async def prepare_order(**_kwargs):
            return SimpleNamespace(usd_per_quote=None)
        payload = await self._resolve(SimpleNamespace(prepare_order=prepare_order))
        self.assertIsNone(payload["realized_pnl"])
        self.assertEqual("loss", payload["outcome"])


if __name__ == "__main__":
    unittest.main()

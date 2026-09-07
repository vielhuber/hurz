from __future__ import annotations

import unittest
from unittest import IsolatedAsyncioTestCase

from app.platforms.base import PreparedOrder
from app.platforms.capital_com import _usd_per_quote


class QuoteCurrencyRateTest(IsolatedAsyncioTestCase):
    """The 3 USD risk budget was applied in the instrument's quote currency:
    3 GBP on UK100 (a third over the cap), 3 JPY on AUDJPY (nothing). The
    rate comes from the venue's own FX mid and is None when unknown, which
    the live loop treats as a reason not to trade."""

    async def _mid(self, epic: str):
        return {"EURUSD": 1.16, "GBPUSD": 1.35, "USDJPY": 154.0, "USDHKD": 7.84}.get(epic)

    async def test_usd_is_one(self):
        self.assertEqual(1.0, await _usd_per_quote("USD", self._mid))

    async def test_usd_based_pairs_use_the_direct_mid(self):
        self.assertAlmostEqual(1.16, await _usd_per_quote("EUR", self._mid))
        self.assertAlmostEqual(1.35, await _usd_per_quote("GBP", self._mid))

    async def test_usd_quoted_pairs_invert_the_mid(self):
        self.assertAlmostEqual(1 / 154.0, await _usd_per_quote("JPY", self._mid))
        self.assertAlmostEqual(1 / 7.84, await _usd_per_quote("HKD", self._mid))

    async def test_unknown_or_missing_rates_are_none(self):
        self.assertIsNone(await _usd_per_quote(None, self._mid))
        self.assertIsNone(await _usd_per_quote("XAU", self._mid))
        self.assertIsNone(await _usd_per_quote("AUD", self._mid))  # no AUDUSD mid in this fixture

    def test_prepared_order_defaults_to_usd(self):
        self.assertEqual(1.0, PreparedOrder(reference_price=1.0, stop_loss=None, take_profit=None).usd_per_quote)


if __name__ == "__main__":
    unittest.main()

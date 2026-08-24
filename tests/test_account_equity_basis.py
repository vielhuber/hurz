from __future__ import annotations

import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.platforms.capital_com import CapitalComPlatform


class AccountEquityBasisTest(IsolatedAsyncioTestCase):
    """The equity ceiling caps risk at a fraction of the account. Reading
    `available` instead of `balance` made that ceiling fall as positions
    were opened — 486 EUR against an actual 560 EUR balance with seven
    positions on the book."""

    async def _balance(self, payload):
        platform = CapitalComPlatform()
        with patch.object(platform, "require_auth"), \
                patch.object(platform, "_raw_request",
                             AsyncMock(return_value=payload)):
            return await platform.account_balance()

    async def test_the_account_size_is_reported_not_the_free_margin(self):
        result = await self._balance({"accounts": [{
            "currency": "EUR",
            "balance": {"balance": 559.77, "available": 485.96},
        }]})
        self.assertEqual({"EUR": 559.77}, result)

    async def test_available_is_used_when_balance_is_absent(self):
        result = await self._balance({"accounts": [{
            "currency": "EUR", "balance": {"available": 485.96},
        }]})
        self.assertEqual({"EUR": 485.96}, result)

    async def test_a_missing_balance_block_yields_zero(self):
        result = await self._balance({"accounts": [{"currency": "EUR"}]})
        self.assertEqual({"EUR": 0.0}, result)

    async def test_no_accounts_yields_an_empty_mapping(self):
        self.assertEqual({}, await self._balance({"accounts": []}))


if __name__ == "__main__":
    unittest.main()

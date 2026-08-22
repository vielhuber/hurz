from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.platforms import PlatformAPIError
from app.platforms.capital_com import CapitalComPlatform


class ConfirmationPlatform(CapitalComPlatform):
    def __init__(self, confirmation):
        super().__init__(
            credentials={
                "api_key": "test",
                "identifier": "test",
                "password": "test",
            },
            paper_trade_only=False,
        )
        self.confirmations = (
            list(confirmation) if isinstance(confirmation, list)
            else [confirmation]
        )
        self.confirmation_attempts = 0

    async def _get_dealing_rules(self, epic):
        return {}

    async def _raw_request(self, method, path, **kwargs):
        if method == "POST":
            return {"dealReference": "o_pending"}
        confirmation = self.confirmations[
            min(self.confirmation_attempts, len(self.confirmations) - 1)
        ]
        self.confirmation_attempts += 1
        if isinstance(confirmation, Exception):
            raise confirmation
        return confirmation


class CapitalComOrderConfirmationTest(IsolatedAsyncioTestCase):
    async def test_confirmation_http_error_is_exposed_on_fallback(self):
        platform = ConfirmationPlatform(PlatformAPIError(
            "capital_com: GET /api/v1/confirms/o_pending returned 404",
            status=404,
            response_text='{"errorCode":"error.confirms.deal-not-found"}',
        ))

        with patch("app.platforms.capital_com.asyncio.sleep", AsyncMock()):
            result = await platform.place_order(
                asset="AUDUSD", direction=1, size=300,
            )

        self.assertTrue(result.accepted)
        self.assertEqual("o_pending", result.deal_id)
        self.assertEqual(5, platform.confirmation_attempts)
        self.assertIn("returned 404", result.raw["_confirmation_error"])
        self.assertIn("error.confirms.deal-not-found",
                      result.raw["_confirmation_error"])

    async def test_confirmation_is_retried_before_falling_back(self):
        platform = ConfirmationPlatform([
            PlatformAPIError(
                "capital_com: GET /api/v1/confirms/o_pending returned 404",
                status=404,
            ),
            {
                "dealReference": "o_pending",
                "dealStatus": "ACCEPTED",
                "dealId": "00000000-order",
                "level": 0.71671,
            },
        ])

        with patch("app.platforms.capital_com.asyncio.sleep", AsyncMock()):
            result = await platform.place_order(
                asset="AUDUSD", direction=1, size=300,
            )

        self.assertEqual(2, platform.confirmation_attempts)
        self.assertEqual("00000000-order", result.deal_id)
        self.assertEqual(0.71671, result.fill_price)
        self.assertNotIn("_confirmation_error", result.raw)

    async def test_confirmation_without_deal_id_is_exposed_on_fallback(self):
        platform = ConfirmationPlatform({
            "dealReference": "o_pending",
            "dealStatus": "PENDING",
        })

        with patch("app.platforms.capital_com.asyncio.sleep", AsyncMock()):
            result = await platform.place_order(
                asset="ATOMUSD", direction=1, size=152,
            )

        self.assertTrue(result.accepted)
        self.assertEqual("o_pending", result.deal_id)
        self.assertEqual(5, platform.confirmation_attempts)
        self.assertIn("dealStatus=PENDING",
                      result.raw["_confirmation_error"])


if __name__ == "__main__":
    import unittest
    unittest.main()

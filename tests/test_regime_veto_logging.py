from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.spot_trading.autotrade import _RegimeVetoLogger
from app.spot_trading.regime import RegimeDecision


class RegimeVetoLoggerTest(unittest.TestCase):
    def test_summarizes_repeats_and_reports_value_change_and_clear(self) -> None:
        messages = []
        logger = _RegimeVetoLogger(messages.append)
        first_seen = datetime(2026, 8, 21, 12, 38, 22, tzinfo=timezone.utc)

        logger.observe(
            "COPPER",
            "momentum_4h",
            RegimeDecision(
                True,
                "no-trade-zone",
                25.2,
                "trend-following needs ADX>=30, got 25.2",
            ),
            now=first_seen,
        )
        logger.observe(
            "COPPER",
            "momentum_4h",
            RegimeDecision(
                True,
                "no-trade-zone",
                25.3,
                "trend-following needs ADX>=30, got 25.3",
            ),
            now=first_seen + timedelta(minutes=1),
        )

        self.assertEqual([
            "⛓ regime-veto COPPER momentum_4h: "
            "trend-following needs ADX>=30, got 25.2",
        ], messages)

        logger.observe(
            "COPPER",
            "momentum_4h",
            RegimeDecision(
                True,
                "no-trade-zone",
                26.3,
                "trend-following needs ADX>=30, got 26.3",
            ),
            now=first_seen + timedelta(minutes=2),
        )
        logger.observe(
            "COPPER",
            "momentum_4h",
            RegimeDecision(
                True,
                "no-trade-zone",
                26.3,
                "trend-following needs ADX>=30, got 26.3",
            ),
            now=first_seen + timedelta(minutes=32),
        )
        logger.observe(
            "COPPER",
            "momentum_4h",
            RegimeDecision(
                True,
                "no-trade-zone",
                26.4,
                "trend-following needs ADX>=30, got 26.4",
            ),
            now=first_seen + timedelta(hours=1, minutes=2),
        )
        logger.observe(
            "COPPER",
            "momentum_4h",
            RegimeDecision(
                False,
                "strong-trend",
                30.1,
                "trend-following in trend (ADX=30.1)",
            ),
            now=first_seen + timedelta(hours=1, minutes=3),
        )

        self.assertEqual(4, len(messages))
        self.assertIn("regime-veto update COPPER momentum_4h", messages[1])
        self.assertIn("got 26.3", messages[1])
        self.assertIn("1 suppressed repeat", messages[1])
        self.assertIn("regime-veto persists COPPER momentum_4h", messages[2])
        self.assertIn("1 suppressed repeat", messages[2])
        self.assertIn("regime-veto cleared COPPER momentum_4h", messages[3])
        self.assertIn("after 4 repeats", messages[3])
        self.assertIn("trend-following in trend (ADX=30.1)", messages[3])

    def test_reports_a_changed_veto_reason_immediately(self) -> None:
        messages = []
        logger = _RegimeVetoLogger(messages.append)
        first_seen = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

        logger.observe(
            "EURUSD",
            "bollinger_rev",
            RegimeDecision(
                True,
                "trend",
                35.0,
                "mean-reversion needs ADX<=20, got 35.0",
            ),
            now=first_seen,
        )
        logger.observe(
            "EURUSD",
            "bollinger_rev",
            RegimeDecision(
                True,
                "volatile",
                35.1,
                "mean-reversion blocked by volatility, got 35.1",
            ),
            now=first_seen + timedelta(minutes=1),
        )

        self.assertEqual(2, len(messages))
        self.assertIn("regime-veto changed EURUSD bollinger_rev", messages[1])
        self.assertIn("blocked by volatility, got 35.1", messages[1])


if __name__ == "__main__":
    unittest.main()

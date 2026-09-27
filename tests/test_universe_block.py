from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.spot_trading import autotrade
from app.spot_trading.trading_blocks import BLOCKED_PAIRS, UNIVERSE_BLOCKED_PAIRS
import scripts.efficiency_weighted_selection as base


class UniverseBlockTest(IsolatedAsyncioTestCase):
    """Section 344: the eleven selector instruments outside the replay lower
    the daily gain on all four samples and are blocked as a set."""

    def test_the_eleven_are_blocked_and_the_replay_universe_is_untouched(self):
        self.assertEqual(11, len(UNIVERSE_BLOCKED_PAIRS))
        self.assertTrue(UNIVERSE_BLOCKED_PAIRS <= BLOCKED_PAIRS)
        self.assertFalse(UNIVERSE_BLOCKED_PAIRS & set(base.PAIRS))
        self.assertEqual(27, len(base.PAIRS))

    async def test_entry_guards_refuse_a_universe_blocked_instrument(self):
        platform = SimpleNamespace(name="capital_com")
        intent = await autotrade.evaluate_pair(
            platform, "EURGBP", strategy_name="turtle_breakout",
            resolution="1h", stop_atr=2.0, rr=1.5, lookback_bars=240,
        )
        self.assertIsNone(intent)
        order = SimpleNamespace(pair="GBPAUD", direction=1, strategy="turtle_breakout")
        result = await autotrade.execute_intent(SimpleNamespace(), order, 1.0)
        self.assertFalse(result.accepted)
        self.assertIn("expectancy-blocked", result.error)

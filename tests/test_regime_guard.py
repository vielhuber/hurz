from __future__ import annotations

import unittest

from app.spot_trading.regime import decide


class RegimeGuardTest(unittest.TestCase):
    def test_missing_adx_blocks_classified_strategies(self) -> None:
        decision = decide("momentum", None)

        self.assertTrue(decision.blocked)
        self.assertEqual("unknown", decision.regime)


if __name__ == "__main__":
    unittest.main()

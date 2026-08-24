from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import spot_backtest


class ExplicitRiskRewardTest(unittest.TestCase):
    """--rr used to be folded into the strategy table's default lookup, so
    for any strategy carrying its own value the flag was silently dropped
    and an RR sweep measured one configuration N times."""

    def _parse(self, argv):
        with patch("sys.argv", ["spot_backtest.py", *argv]):
            return spot_backtest._parse_args()

    def test_explicit_rr_survives_a_strategy_with_its_own_value(self):
        args = self._parse(["--strategy", "donchian_breakout_v2", "--rr", "2.0"])
        self.assertEqual(2.0, args.rr)

    def test_omitted_rr_still_falls_back_to_the_strategy_table(self):
        args = self._parse(["--strategy", "donchian_breakout_v2"])
        self.assertEqual(2.5, args.rr)

    def test_omitted_rr_on_an_untabled_strategy_uses_the_global_default(self):
        args = self._parse(["--strategy", "donchian_breakout"])
        self.assertEqual(1.5, args.rr)


if __name__ == "__main__":
    unittest.main()

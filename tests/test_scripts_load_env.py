from __future__ import annotations

import pathlib
import unittest


SCRIPTS = [
    "forward_report.py",
    "generate_dashboard.py",
    "select_pairs.py",
    "spot_backtest.py",
    "structural_signal_research.py",
]


class ScriptsLoadEnvTest(unittest.TestCase):
    """Every analysis script must load .env itself.

    Sourcing it from the shell (`set -a; . ./.env`) lets bash expand `$`
    inside the quoted Capital.com password, which arrives corrupted and
    surfaces as a 401 that reads like a session limit. That cost one
    aborted backtest run and one misdiagnosis before it was understood."""

    def test_every_script_loads_the_environment(self):
        root = pathlib.Path(__file__).resolve().parent.parent / "scripts"
        missing = [
            name for name in SCRIPTS
            if "settings.load_env()" not in (root / name).read_text()
        ]

        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()


class RetiredComboSplitTest(unittest.TestCase):
    """The forward window must separate combos the veto has since
    retired. The first forward trade was a turtle_breakout/GOLD loss
    from a combo retired hours later — reported as the system's
    expectancy it would measure something the bot no longer does."""

    def test_report_splits_retired_from_live(self):
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from scripts import forward_report

        retired = {("turtle_breakout", "GOLD"), "bollinger_rev"}

        self.assertTrue(
            forward_report._is_retired("turtle_breakout", "GOLD", retired))
        # A retired strategy takes all of its pairs with it.
        self.assertTrue(
            forward_report._is_retired("bollinger_rev", "DE40", retired))
        self.assertFalse(
            forward_report._is_retired("donchian_breakout", "BTCUSD", retired))

    def test_unreadable_veto_data_reports_everything_as_live(self):
        from scripts import forward_report
        from unittest.mock import patch

        with patch("app.spot_trading.pair_selector.live_expectancy_veto",
                   side_effect=RuntimeError("journal down")):
            self.assertEqual(set(), forward_report._retired_combos())

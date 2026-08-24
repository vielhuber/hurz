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

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import capital_spread_audit


class SpreadAuditMergeTest(unittest.TestCase):
    """A run with --pairs used to replace the whole file. Auditing ten
    instruments silently dropped every other pair's spread, and the only
    symptom was the backtest falling back to its flat default for them —
    which is a tenfold understatement on crypto alts."""

    def test_existing_pairs_are_carried_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spreads.json"
            path.write_text(json.dumps({
                "pairs": {"EURUSD": {"bid": 1.0, "offer": 1.001}},
            }))

            with patch.object(capital_spread_audit, "_OUTPUT_PATH", str(path)):
                existing = capital_spread_audit._existing_pairs()

        self.assertIn("EURUSD", existing)

    def test_missing_file_yields_an_empty_seed(self):
        with patch.object(capital_spread_audit, "_OUTPUT_PATH", "/nonexistent"):
            self.assertEqual({}, capital_spread_audit._existing_pairs())

    def test_unreadable_file_yields_an_empty_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("not json")

            with patch.object(capital_spread_audit, "_OUTPUT_PATH", str(path)):
                self.assertEqual({}, capital_spread_audit._existing_pairs())


if __name__ == "__main__":
    unittest.main()

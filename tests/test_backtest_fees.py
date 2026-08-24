from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import spot_backtest


class AuditedSpreadFeeTest(unittest.TestCase):
    """The crypto fallback charged 0.05%/side to every alt while the
    broker quotes about 0.25% on ADAUSD, DOTUSD and LINKUSD — and 2.5%
    on APTUSD. A tenfold to fiftyfold understatement makes exactly the
    untradeable instruments look backtestable."""

    def setUp(self) -> None:
        spot_backtest._SPREAD_PCT_CACHE = None
        spot_backtest._SPREADS_CACHE = None

    def tearDown(self) -> None:
        spot_backtest._SPREAD_PCT_CACHE = None
        spot_backtest._SPREADS_CACHE = None

    def test_audited_percent_beats_the_crypto_fallback(self):
        spot_backtest._SPREADS_CACHE = {}
        spot_backtest._SPREAD_PCT_CACHE = {"ADAUSD": 0.5}

        # 0.5% round trip is 0.25% per side, not the 0.05% fallback.
        self.assertAlmostEqual(
            0.0025, spot_backtest._fee_for("capital_com", "ADAUSD"))

    def test_full_audit_file_still_yields_the_fallback_for_unknowns(self):
        spot_backtest._SPREADS_CACHE = {}
        spot_backtest._SPREAD_PCT_CACHE = {"ADAUSD": 0.5}

        self.assertAlmostEqual(
            0.0005, spot_backtest._fee_for("capital_com", "DOGEUSD"))

    def test_live_spread_file_still_takes_precedence(self):
        spot_backtest._SPREADS_CACHE = {"ADAUSD": {"fee_per_side": 0.001}}
        spot_backtest._SPREAD_PCT_CACHE = {"ADAUSD": 0.5}

        self.assertAlmostEqual(
            0.001, spot_backtest._fee_for("capital_com", "ADAUSD"))

    def test_missing_audit_file_does_not_raise(self):
        spot_backtest._SPREADS_CACHE = {}
        with patch.object(spot_backtest, "_SPREAD_PCT_PATH", "/nonexistent"):
            self.assertAlmostEqual(
                0.0005, spot_backtest._fee_for("capital_com", "ADAUSD"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import spot_backtest


class CapitalComFeeFallbackTest(unittest.TestCase):
    def test_known_crypto_pairs_use_crypto_fallback_without_audit_data(self) -> None:
        # Both audit sources must be empty for the fallback to be the
        # thing under test: the percentage audit now covers AAVEUSD and
        # APTUSD, and an audited spread rightly wins over the default.
        with patch.object(spot_backtest, "_SPREADS_CACHE", {}), \
                patch.object(spot_backtest, "_SPREAD_PCT_CACHE", {}):
            for pair in ("AAVEUSD", "APTUSD", "ARBUSD", "NEARUSD"):
                with self.subTest(pair=pair):
                    self.assertEqual(0.0005, spot_backtest._fee_for(
                        "capital_com", pair,
                    ))


if __name__ == "__main__":
    unittest.main()

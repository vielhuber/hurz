import json
import os
import tempfile
from unittest import TestCase

from scripts.sampled_spread_costs import median_half_spreads


class SampledSpreadCostsTest(TestCase):
    def test_median_half_spread_per_pair_as_a_fraction_of_price(self):
        rows = [{"pair": "DE40", "half_spread_pct": value} for value in (0.002, 0.008, 0.009)]
        rows += [{"pair": "GOLD", "half_spread_pct": 0.006}, {"pair": "CORN", "half_spread_pct": 0.1}]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write("\n".join(json.dumps(row) for row in rows) + "\n")
        try:
            medians = median_half_spreads(handle.name, {"DE40", "GOLD"})
        finally:
            os.unlink(handle.name)
        self.assertEqual({"DE40", "GOLD"}, set(medians))
        self.assertAlmostEqual(0.00008, medians["DE40"])
        self.assertAlmostEqual(0.00006, medians["GOLD"])

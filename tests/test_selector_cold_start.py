import unittest

import numpy as np

from scripts.momentum_ranked_wider import with_cold_start


def _trade(pair, day, r):
    return {"pair": pair, "ts": np.datetime64(day), "exit_ts": np.datetime64(day) + np.timedelta64(1, "D"), "r": r}


class SelectorColdStartTest(unittest.TestCase):
    def test_the_cold_span_is_ranked_on_the_cold_started_trades_only(self):
        start = np.datetime64("2024-10-15")
        warm = [_trade("FR40", "2024-10-15", 1.49), _trade("FR40", "2024-12-01", -1.0),
                _trade("US30", "2024-10-16", 0.5)]
        cold = {"FR40": (np.datetime64("2024-11-15"), [_trade("FR40", "2024-10-20", -0.3)])}
        training = with_cold_start(warm, cold, start)
        self.assertEqual([("US30", 0.5), ("FR40", -0.3), ("FR40", -1.0)],
                         [(t["pair"], t["r"]) for t in training])


if __name__ == "__main__":
    unittest.main()

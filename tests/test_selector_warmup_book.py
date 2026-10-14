from unittest import TestCase

import numpy as np

import scripts.selector_warmup_book as experiment

START = np.datetime64("2024-01-01T00")


def trade(pair, hours):
    return {"pair": pair, "ts": START + np.timedelta64(hours, "h"), "strat": "momentum"}


class SelectorWarmupBookTest(TestCase):
    def test_the_cold_span_is_replaced_and_the_rest_kept(self):
        warm = [trade("US30", 10), trade("US30", 600), trade("DE40", 20)]
        cold = {"US30": (START + np.timedelta64(500, "h"), [trade("US30", 12)])}
        out = experiment.with_cold_start(warm, cold, START)
        self.assertEqual([("US30", 12), ("DE40", 20), ("US30", 600)],
                         [(t["pair"], int((t["ts"] - START) / np.timedelta64(1, "h"))) for t in out])

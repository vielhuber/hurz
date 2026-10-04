from unittest import TestCase

import numpy as np

import scripts.tsmom_session_closes as experiment

HOURS = {"US500": {"thu": ["00:00 - 21:00", "21:05 - 00:00"], "fri": ["00:00 - 21:00"], "zone": "UTC"}}


class TsmomSessionClosesTest(TestCase):
    def test_the_decision_bars_are_the_broker_closes(self):
        ts = np.arange(np.datetime64("2026-10-01T00"), np.datetime64("2026-10-03T00"), np.timedelta64(1, "h"))
        bars = experiment.session_bars(HOURS)("US500", ts.astype("datetime64[ns]"))
        self.assertEqual([np.datetime64("2026-10-01T23"), np.datetime64("2026-10-02T20")],
                         [ts[i] for i in bars])

    def test_an_instrument_without_hours_has_no_decision(self):
        ts = np.arange(np.datetime64("2026-10-01T00"), np.datetime64("2026-10-02T00"), np.timedelta64(1, "h"))
        self.assertEqual(0, len(experiment.session_bars(HOURS)("GOLD", ts.astype("datetime64[ns]"))))

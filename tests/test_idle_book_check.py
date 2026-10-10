from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import scripts.idle_book_check as experiment

SIGNAL = SimpleNamespace(index=5, direction=-1)


class IdleBookCheckTest(TestCase):
    def test_the_regime_gate_is_named_first(self):
        with patch.object(experiment, "gate", return_value=SimpleNamespace(blocked=True)):
            self.assertEqual("regime gate", experiment.drop_reason("momentum", None, SIGNAL, "US30", {}, 3.0))

    def test_a_priced_signal_has_no_drop_reason(self):
        with patch.object(experiment, "gate", return_value=SimpleNamespace(blocked=False)), \
                patch.object(experiment, "direction_blocked", return_value=False), \
                patch.object(experiment.ews, "trade_terms", return_value=(100.0, 1.2, 0.01, 2.6)):
            self.assertIsNone(experiment.drop_reason("momentum", None, SIGNAL, "US30", {}, 3.0))

    def test_an_unpriceable_signal_is_dropped_by_pricing(self):
        with patch.object(experiment, "gate", return_value=SimpleNamespace(blocked=False)), \
                patch.object(experiment, "direction_blocked", return_value=False), \
                patch.object(experiment.ews, "trade_terms", return_value=None):
            self.assertTrue(experiment.drop_reason("momentum", None, SIGNAL, "US30", {}, 3.0).startswith("pricing"))

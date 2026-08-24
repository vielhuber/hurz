from __future__ import annotations

import unittest
from unittest.mock import patch

from app.spot_trading import autotrade
from app.spot_trading import pair_selector


ACTIVE = [
    {"platform": "capital_com", "strategy": "donchian_breakout",
     "pair": "BTCUSD", "resolution": "1h"},
    {"platform": "capital_com", "strategy": "donchian_breakout",
     "pair": "ETHUSD", "resolution": "1h"},
    {"platform": "capital_com", "strategy": "bollinger_rev",
     "pair": "DE40", "resolution": "1h"},
]


class DropRetiredTest(unittest.TestCase):
    """The active list is a file. A selector run whose journal was
    unreachable used to rewrite it without any vetoes applied, handing
    every retired combo back to the trader with a quietly growing list
    as the only symptom. This is the second line of defence."""

    def test_retired_combo_is_dropped(self):
        with patch.object(pair_selector, "live_expectancy_veto",
                          return_value={("donchian_breakout", "ETHUSD"): -0.25}), \
                patch.object(pair_selector, "strategy_expectancy_veto",
                             return_value={}):
            kept, retired = autotrade._drop_retired(ACTIVE, "capital_com")

        self.assertEqual([("donchian_breakout", "ETHUSD")], retired)
        self.assertEqual(2, len(kept))

    def test_retired_strategy_drops_all_its_combos(self):
        with patch.object(pair_selector, "live_expectancy_veto",
                          return_value={}), \
                patch.object(pair_selector, "strategy_expectancy_veto",
                             return_value={"bollinger_rev": -0.16}):
            kept, retired = autotrade._drop_retired(ACTIVE, "capital_com")

        self.assertEqual([("bollinger_rev", "DE40")], retired)
        self.assertEqual(2, len(kept))

    def test_clean_list_passes_through_untouched(self):
        with patch.object(pair_selector, "live_expectancy_veto",
                          return_value={}), \
                patch.object(pair_selector, "strategy_expectancy_veto",
                             return_value={}):
            kept, retired = autotrade._drop_retired(ACTIVE, "capital_com")

        self.assertEqual([], retired)
        self.assertEqual(ACTIVE, kept)

    def test_unavailable_veto_data_leaves_the_list_alone(self):
        # Refusing to trade at all on a database hiccup is worse than
        # trading the list as written; the selector already fails closed
        # on the writing side.
        with patch.object(pair_selector, "live_expectancy_veto",
                          side_effect=pair_selector.VetoDataUnavailable("down")):
            kept, retired = autotrade._drop_retired(ACTIVE, "capital_com")

        self.assertEqual([], retired)
        self.assertEqual(ACTIVE, kept)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.spot_trading import pair_selector
from app.spot_trading.pair_selector import PairScore
from app.utils import singletons


class StubDatabase:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.params = None

    def select(self, query, params=None) -> list:
        self.params = params
        return self.rows

    def query(self, query, params=None) -> None:
        return None


class FailingDatabase:
    def select(self, query, params=None) -> list:
        raise RuntimeError("journal unavailable")


def _score(strategy: str, pair: str) -> PairScore:
    return PairScore(
        platform="capital_com", strategy=strategy, resolution="1h",
        pair=pair, n=40, win_rate=0.4, profit_factor=1.4,
        expectancy_R=0.2, sharpe=0.5, score=1.0,
    )


class LiveExpectancyVetoTest(unittest.TestCase):
    def test_clear_loser_with_enough_trades_is_retired(self):
        database = StubDatabase([
            {"strategy": "keltner_breakout", "pair": "PALLADIUM",
             "n": 14, "total_r": -3.8},
        ])

        with patch.object(singletons, "database", database):
            vetoed = pair_selector.live_expectancy_veto("capital_com")

        self.assertIn(("keltner_breakout", "PALLADIUM"), vetoed)

    def test_thin_sample_is_never_retired(self):
        # The HAVING clause keeps small samples out of the result set;
        # this asserts the threshold is actually passed to the query.
        database = StubDatabase([])

        with patch.object(singletons, "database", database):
            pair_selector.live_expectancy_veto("capital_com")

        self.assertEqual(
            ("capital_com", pair_selector._VETO_MIN_TRADES), database.params,
        )

    def test_mildly_negative_combo_survives(self):
        database = StubDatabase([
            {"strategy": "turtle_breakout", "pair": "OIL_BRENT",
             "n": 17, "total_r": -0.5},
        ])

        with patch.object(singletons, "database", database):
            vetoed = pair_selector.live_expectancy_veto("capital_com")

        self.assertEqual({}, vetoed)

    def test_winners_are_never_promoted_by_this_path(self):
        database = StubDatabase([
            {"strategy": "turtle_breakout", "pair": "BTCUSD",
             "n": 11, "total_r": 4.0},
        ])

        with patch.object(singletons, "database", database):
            vetoed = pair_selector.live_expectancy_veto("capital_com")

        self.assertEqual({}, vetoed)

    def test_unreadable_journal_vetoes_nothing(self):
        with patch.object(singletons, "database", FailingDatabase()):
            self.assertEqual({}, pair_selector.live_expectancy_veto("capital_com"))


class VetoAppliesToSelectionTest(unittest.TestCase):
    def test_vetoed_pin_is_dropped_and_stops_reserving_its_pair(self):
        pins = [PairScore(
            platform="capital_com", strategy="keltner_breakout",
            resolution="1h", pair="PALLADIUM", n=0, win_rate=0.0,
            profit_factor=0.0, expectancy_R=0.0, sharpe=0.0, score=0.0,
            pinned=True, exclusive=True,
        )]
        ranked = [_score("donchian_breakout", "PALLADIUM")]

        with patch.object(pair_selector, "_pinned_scores", return_value=pins), \
                patch.object(pair_selector, "live_expectancy_veto",
                             return_value={("keltner_breakout", "PALLADIUM"): -0.27}), \
                patch.object(pair_selector.json, "dump"), \
                patch("builtins.open"), patch.object(pair_selector.os, "makedirs"):
            payload = pair_selector.persist_active_pairs(
                ranked, top_n=5, platform="capital_com",
            )

        combos = {(p["strategy"], p["pair"]) for p in payload["pairs"]}
        self.assertNotIn(("keltner_breakout", "PALLADIUM"), combos)
        # With the pin retired its exclusive reservation dies too, so the
        # ranked combo may finally use that pair.
        self.assertIn(("donchian_breakout", "PALLADIUM"), combos)

    def test_vetoed_ranked_combo_is_dropped(self):
        ranked = [_score("donchian_breakout", "OIL_CRUDE"),
                  _score("turtle_breakout", "BTCUSD")]

        with patch.object(pair_selector, "_pinned_scores", return_value=[]), \
                patch.object(pair_selector, "live_expectancy_veto",
                             return_value={("donchian_breakout", "OIL_CRUDE"): -0.24}), \
                patch.object(pair_selector.json, "dump"), \
                patch("builtins.open"), patch.object(pair_selector.os, "makedirs"):
            payload = pair_selector.persist_active_pairs(
                ranked, top_n=5, platform="capital_com",
            )

        combos = {(p["strategy"], p["pair"]) for p in payload["pairs"]}
        self.assertEqual({("turtle_breakout", "BTCUSD")}, combos)


class StrategyExpectancyVetoTest(unittest.TestCase):
    def test_structurally_unprofitable_strategy_is_retired(self):
        database = StubDatabase([
            {"strategy": "bollinger_rev", "n": 121, "total_r": -15.5},
        ])

        with patch.object(singletons, "database", database):
            vetoed = pair_selector.strategy_expectancy_veto("capital_com")

        self.assertIn("bollinger_rev", vetoed)

    def test_near_breakeven_strategy_survives(self):
        database = StubDatabase([
            {"strategy": "donchian_breakout", "n": 110, "total_r": -0.33},
        ])

        with patch.object(singletons, "database", database):
            self.assertEqual({}, pair_selector.strategy_expectancy_veto("capital_com"))

    def test_strategy_veto_uses_the_higher_trade_floor(self):
        database = StubDatabase([])

        with patch.object(singletons, "database", database):
            pair_selector.strategy_expectancy_veto("capital_com")

        self.assertEqual(
            ("capital_com", pair_selector._STRATEGY_VETO_MIN_TRADES),
            database.params,
        )

    def test_retired_strategy_removes_all_its_combos(self):
        ranked = [_score("keltner_breakout", "DE40"),
                  _score("keltner_breakout", "US30"),
                  _score("turtle_breakout", "BTCUSD")]

        with patch.object(pair_selector, "_pinned_scores", return_value=[]), \
                patch.object(pair_selector, "live_expectancy_veto", return_value={}), \
                patch.object(pair_selector, "strategy_expectancy_veto",
                             return_value={"keltner_breakout": -0.17}), \
                patch.object(pair_selector.json, "dump"), \
                patch("builtins.open"), patch.object(pair_selector.os, "makedirs"):
            payload = pair_selector.persist_active_pairs(
                ranked, top_n=5, platform="capital_com",
            )

        combos = {(p["strategy"], p["pair"]) for p in payload["pairs"]}
        self.assertEqual({("turtle_breakout", "BTCUSD")}, combos)


if __name__ == "__main__":
    unittest.main()

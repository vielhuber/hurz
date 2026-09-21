from unittest import TestCase

import numpy as np
import pandas as pd

from scripts.rolling_correlation_guard import admit_correlated, correlation_snapshot


class RollingCorrelationGuardTest(TestCase):
    def trade(self, pair, direction=1, hour=0):
        timestamp = np.datetime64("2025-01-01T00:00") + np.timedelta64(hour, "h")
        return dict(pair=pair, strat="donchian_breakout", dir=direction, ts=timestamp,
                    exit_ts=timestamp + np.timedelta64(24, "h"), r=0.5, usd=1.0, risk=2.0)

    def test_same_direction_correlated_trade_is_refused_without_occupying_slot(self):
        rows = [self.trade("AAA"), self.trade("BBB")]
        correlations = pd.DataFrame([[1, 0.9], [0.9, 1]], index=["AAA", "BBB"], columns=["AAA", "BBB"])
        state, booked = {"open": {}, "pair": {}}, []
        refused = admit_correlated(rows, {(row["strat"], row["pair"]) for row in rows},
                                   correlations, state, booked)
        self.assertEqual(1, refused)
        self.assertEqual(["AAA"], [row["pair"] for row in booked])
        self.assertEqual({"AAA"}, set(state["open"]))

    def test_negative_correlation_with_opposite_directions_is_same_risk(self):
        rows = [self.trade("AAA"), self.trade("BBB", -1)]
        correlations = pd.DataFrame([[1, -0.9], [-0.9, 1]], index=["AAA", "BBB"], columns=["AAA", "BBB"])
        self.assertEqual(1, admit_correlated(rows, {(row["strat"], row["pair"]) for row in rows},
                                           correlations, {"open": {}, "pair": {}}, []))

    def test_hedging_and_missing_correlations_do_not_add_a_refusal(self):
        for correlation in (0.9, np.nan):
            rows = [self.trade("AAA"), self.trade("BBB", -1)]
            matrix = pd.DataFrame([[1, correlation], [correlation, 1]],
                                  index=["AAA", "BBB"], columns=["AAA", "BBB"])
            booked = []
            self.assertEqual(0, admit_correlated(rows, {(row["strat"], row["pair"]) for row in rows},
                                               matrix, {"open": {}, "pair": {}}, booked))
            self.assertEqual(2, len(booked))

    def test_expired_position_does_not_block_new_entry(self):
        rows = [self.trade("AAA"), self.trade("BBB", hour=24)]
        matrix = pd.DataFrame(1.0, index=["AAA", "BBB"], columns=["AAA", "BBB"])
        booked = []
        self.assertEqual(0, admit_correlated(rows, {(row["strat"], row["pair"]) for row in rows},
                                           matrix, {"open": {}, "pair": {}}, booked))
        self.assertEqual(2, len(booked))

    def test_existing_cluster_cap_cannot_be_relaxed(self):
        rows = [self.trade(pair) for pair in ("DE40", "US500", "US30", "US100")]
        matrix = pd.DataFrame(0.0, index=[row["pair"] for row in rows], columns=[row["pair"] for row in rows])
        booked = []
        admit_correlated(rows, {(row["strat"], row["pair"]) for row in rows},
                         matrix, {"open": {}, "pair": {}}, booked)
        self.assertEqual(3, len(booked))

    def test_snapshot_excludes_cutoff_and_future_returns(self):
        dates = pd.date_range("2025-01-01", periods=400, freq="h")
        values = np.sin(np.arange(400))
        returns = pd.DataFrame({"AAA": values, "BBB": values}, index=dates)
        cutoff = dates[250]
        before = correlation_snapshot(returns, cutoff)
        returns.loc[cutoff:, "BBB"] *= -100
        pd.testing.assert_frame_equal(before, correlation_snapshot(returns, cutoff))
        self.assertAlmostEqual(1.0, before.loc["AAA", "BBB"])

    def test_snapshot_requires_200_common_observations(self):
        returns = pd.DataFrame({"AAA": np.arange(199), "BBB": np.arange(199)},
                               index=pd.date_range("2025-01-01", periods=199, freq="h"))
        self.assertTrue(np.isnan(correlation_snapshot(returns, pd.Timestamp("2025-02-01")).loc["AAA", "BBB"]))

    def test_snapshot_excludes_returns_older_than_60_days(self):
        dates = pd.date_range("2025-01-01", periods=1800, freq="h")
        values = np.sin(np.arange(len(dates)))
        returns = pd.DataFrame({"AAA": values, "BBB": values}, index=dates)
        cutoff = dates[-1] + pd.Timedelta(hours=1)
        returns.loc[returns.index < cutoff - pd.Timedelta(days=60), "BBB"] *= -100
        self.assertAlmostEqual(1.0, correlation_snapshot(returns, cutoff).loc["AAA", "BBB"])

    def test_existing_concurrent_cap_cannot_be_relaxed(self):
        rows = [self.trade(f"PAIR{number}") for number in range(9)]
        matrix = pd.DataFrame(0.0, index=[row["pair"] for row in rows], columns=[row["pair"] for row in rows])
        booked = []
        admit_correlated(rows, {(row["strat"], row["pair"]) for row in rows},
                         matrix, {"open": {}, "pair": {}}, booked)
        self.assertEqual(8, len(booked))

    def test_existing_cooldown_and_single_position_rule_remain(self):
        rows = [self.trade("AAA", hour=hour) for hour in (0, 1, 24, 29, 30)]
        rows[0]["r"] = -1.0
        matrix = pd.DataFrame(1.0, index=["AAA"], columns=["AAA"])
        booked = []
        admit_correlated(rows, {("donchian_breakout", "AAA")}, matrix,
                         {"open": {}, "pair": {}}, booked)
        self.assertEqual([rows[0]["ts"], rows[-1]["ts"]], [row["ts"] for row in booked])

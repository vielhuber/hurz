from unittest import TestCase
from unittest.mock import patch

import scripts.cluster_rotate_worst as crw
import scripts.two_year_cluster_cap as experiment


class TwoYearClusterCapTest(TestCase):
    def test_the_cap_applies_during_the_replay_and_is_restored(self):
        original = crw._CLUSTER_DIR_CAP
        seen = []
        with patch.object(experiment, "ranked_replay", lambda days, *args: seen.append(crw._CLUSTER_DIR_CAP)):
            experiment.capped_replay(2)
        self.assertEqual([2], seen)
        self.assertEqual(original, crw._CLUSTER_DIR_CAP)

    def test_a_third_same_direction_position_in_a_cluster_is_refused_at_two(self):
        def signal(pair, hour):
            return {"strat": "momentum", "pair": pair, "dir": 1, "ts": hour, "exit_ts": hour + 100, "r": 0.5}
        window = [signal("US30", 1), signal("DE40", 2), signal("EU50", 3)]
        active = {("momentum", p) for p in ("US30", "DE40", "EU50")}
        for cap, expected in ((3, 3), (2, 2)):
            booked = []
            with patch.object(crw, "_CLUSTER_DIR_CAP", cap):
                crw.admit(window, active, "live", None, {"open": {}, "pair": {}}, booked, [])
            self.assertEqual(expected, len(booked))

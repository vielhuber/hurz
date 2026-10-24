import json
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import scripts.four_hour_stream as fhs


class NativeFourHourFramesTest(unittest.TestCase):
    def test_bars_from_the_cache_end_where_the_hourly_history_ends(self):
        folder = tempfile.mkdtemp()
        rows = [[f"2026-09-{day:02d}T{hour:02d}:00:00+00:00", 1.0 + hour, 2.0 + hour, 0.5, 1.5]
                for day in range(1, 30) for hour in (0, 4, 8, 12, 16, 20)]
        json.dump(rows + rows[-1:], open(os.path.join(folder, "NZDUSD.json"), "w"))
        with patch.object(fhs, "NATIVE_4H_CACHE", folder):
            frame = fhs.native_frames(["NZDUSD"], np.datetime64("2026-09-20T00:00"))["NZDUSD"]
        self.assertEqual(np.datetime64("2026-09-19T20:00"), frame["timestamp"].values[-1].astype("datetime64[m]"))
        self.assertEqual(len(frame), frame["timestamp"].nunique())
        self.assertIn("atr_14", frame.columns)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sqlite3
import unittest

from scripts.generate_dashboard import _PNL


class DashboardPnlCurrencyTest(unittest.TestCase):
    """Since 2026-09-07 23:01 UTC the journal books `realized_pnl` in USD.
    The dashboard kept recomputing the result from exit and fill price in
    the instrument's quote currency, so three yen and HK50 stale exits
    worth +1.20 USD showed as +193.59 USD. Rows closed after the switch
    must read the column; the legacy rows keep the price recomputation
    that corrects their signal-price booking."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE spot_trades (exit_time TEXT, exit_price REAL, "
            "fill_price REAL, direction INTEGER, size REAL, realized_pnl REAL)"
        )
        self.conn.executemany(
            "INSERT INTO spot_trades VALUES (?, ?, ?, ?, ?, ?)",
            [
                # CHFJPY short closed after the switch: 101.8 JPY = 0.66 USD.
                ("2026-09-08 23:00:00", 189.945, 190.454, -1, 200.0, 0.6624),
                # Legacy row booked against the signal price: recompute.
                ("2026-08-10 10:00:00", 101.0, 100.0, 1, 2.0, 5.0),
                # Post-switch row without a booked result falls back.
                ("2026-09-09 01:00:00", 11.0, 10.0, 1, 3.0, None),
            ],
        )

    def _pnl(self, exit_time):
        return self.conn.execute(
            f"SELECT {_PNL} FROM spot_trades WHERE exit_time = ?", (exit_time,)
        ).fetchone()[0]

    def test_post_switch_rows_read_the_usd_column(self):
        self.assertAlmostEqual(0.6624, self._pnl("2026-09-08 23:00:00"))

    def test_legacy_rows_are_recomputed_from_prices(self):
        self.assertAlmostEqual(2.0, self._pnl("2026-08-10 10:00:00"))

    def test_post_switch_row_without_booking_uses_prices(self):
        self.assertAlmostEqual(3.0, self._pnl("2026-09-09 01:00:00"))


if __name__ == "__main__":
    unittest.main()

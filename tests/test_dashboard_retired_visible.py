from __future__ import annotations

import unittest

from scripts import generate_dashboard


class RetiredBookVisibleTest(unittest.TestCase):
    """The stat views exclude retired mean-reversion strategies by design,
    so the dashboard described the active book. But it labelled that
    'All-time' — showing -92.95 USD over 292 trades while the journal held
    -465.56 over 519. The excluded 227 trades were 80 % of the loss."""

    def _card(self, retired):
        return generate_dashboard._render_cards(
            summary=[{"platform": "capital_com", "trades": 292,
                      "wins": 100, "pnl": -92.95}],
            alltime=[{"platform": "capital_com", "pnl": -92.95}],
            open_pos=[], period="all-time", retired=retired,
        )

    def test_the_retired_total_is_rendered(self):
        html = self._card([{"trades": 227, "pnl": -372.60}])
        self.assertIn("stillgelegt (227 Trades)", html)
        self.assertIn("372.60", html)

    def test_the_combined_total_is_rendered(self):
        html = self._card([{"trades": 227, "pnl": -372.60}])
        self.assertIn("Gesamt inkl. stillgelegt", html)
        self.assertIn("465.55", html)

    def test_the_active_figure_is_labelled_as_the_active_book(self):
        html = self._card([{"trades": 227, "pnl": -372.60}])
        self.assertIn("All-time (aktives Buch)", html)

    def test_no_retired_trades_adds_no_rows(self):
        html = self._card([{"trades": 0, "pnl": 0.0}])
        self.assertNotIn("stillgelegt", html)

    def test_a_missing_retired_block_is_tolerated(self):
        html = self._card(None)
        self.assertIn("All-time (aktives Buch)", html)


if __name__ == "__main__":
    unittest.main()

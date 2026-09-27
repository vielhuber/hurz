"""Universe: the selector ranks eleven instruments the replay has never seen.

The nightly selector backtests 55 instruments (`data/spot_backtest_results
.json`); 17 are blocked, and eleven of the remaining 38 are outside the
replay's 27: ARBUSD, BNBUSD, DOGEUSD, EURCAD, EURCHF, EURGBP, GBPAUD,
GBPCHF, PLATINUM, USDCAD and WHEAT. Eight of them are missing from
`_CORRELATION_CLUSTERS`, so when listed they trade without a cluster cap;
GBPAUD was listed on 2026-09-25. Every replay verdict so far was drawn on
a universe smaller than the one the bot can trade.

Fixed before any outcome was seen:
  - the current state is the 38-instrument universe with the live cluster
    map, unmapped instruments unclustered exactly as live, and the live
    per-instrument costs from the spread caches;
  - candidate: restrict the selector to the replay's 27; adopted into the
    bot (the eleven excluded) only if the 27 beat the 38 on all four
    samples with pooled paired t > +2 — here the replay's "candidate"
    arm is the 38, so that means a delta below zero on all four samples
    and t < -2;
  - independently, the replay universe follows the live one (section
    245's rule) once the history is cached;
  - no cluster remapping, no subset, no diagnostic arm.
"""
import asyncio

import scripts.additional_indices as replay
from scripts.spot_backtest import _fee_for

CANDIDATES = ["ARBUSD", "BNBUSD", "DOGEUSD", "EURCAD", "EURCHF", "EURGBP", "GBPAUD",
              "GBPCHF", "PLATINUM", "USDCAD", "WHEAT"]


def configure():
    replay.CANDIDATES = CANDIDATES
    replay.SPREAD_PERCENT = {}
    replay.FEES = {pair: _fee_for(replay.base.PLAT, pair) for pair in CANDIDATES}
    replay.CLUSTERS = {}
    replay.SHORT_BLOCKED = set()


if __name__ == "__main__":
    configure()
    asyncio.run(replay.main())

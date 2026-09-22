"""Preregistered admission lever: higher sizing efficiency first on a shared bar.

With a common 3 USD target, descending planned dollar risk is descending
budget utilization after notional limits and broker size rounding. Sort
only simultaneous eligible signals; retain active-list order for ties.
No position is resized, no limit is loosened, and no diagnostic arm is
tested. Actual dollar exposure can rise despite unchanged risk ceilings.
Acceptance requires four better samples and pooled paired daily t > +2.

Reuse the weekly, cached-history admission replay and read-only journal.
"""
import asyncio

from scripts import burst_adx_priority as replay


def prioritize(window, order, efficient_first):
    active = [trade for trade in window if (trade["strat"], trade["pair"]) in order]
    return sorted(active, key=lambda trade: (
        trade["ts"], -trade["risk"] if efficient_first else 0,
        order[(trade["strat"], trade["pair"])],
    ))


if __name__ == "__main__":
    replay.prioritize = prioritize
    print("candidate=higher_sizing_efficiency_first", flush=True)
    asyncio.run(replay.main())

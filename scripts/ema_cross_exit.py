"""Preregistered exit lever: adverse EMA(12)/EMA(26) crossover.

Close a long at the completed hourly close when EMA(12) crosses below
EMA(26), inverse for a short. Both recursive means use the current close
and start at the first observed close. Existing stops/targets take
priority, and the 24-bar timeout, sizing and entry guards stay unchanged.
No diagnostic arm or parameter sweep. Acceptance requires four better
OOS samples and pooled paired daily t > +2 in the weekly selection replay.

Uses the existing cached-history crossover replay and read-only journal.
"""
import asyncio

from scripts import directional_exit as replay


def ema_balance(frame):
    close = frame["close"]
    return (close.ewm(span=12, adjust=False).mean()
            - close.ewm(span=26, adjust=False).mean()).to_numpy()


if __name__ == "__main__":
    replay.directional_balance = ema_balance
    print("candidate=adverse_ema_12_26_cross_exit", flush=True)
    asyncio.run(replay.main())

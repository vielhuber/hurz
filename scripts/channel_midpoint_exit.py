"""Preregistered exit lever: adverse crossing of the previous 20-bar midpoint.

The channel is halfway between the highest high and lowest low of the
twenty completed bars preceding the current bar. A long exits when the
completed close crosses below it; a short exits on the inverse crossing.
Stops and targets retain priority. No diagnostic arm, no risk relaxation.
Acceptance requires four better samples and pooled paired daily t > +2.

Reuse the crossover replay with only its causal balance series replaced.
Run from the runtime directory with this checkout on PYTHONPATH; history
is cached, journal access read-only, current pins and vetoes held fixed.
"""
import asyncio

from scripts import directional_exit as replay


def channel_balance(frame):
    upper = frame["high"].rolling(20).max().shift(1)
    lower = frame["low"].rolling(20).min().shift(1)
    return (frame["close"] - (upper + lower) / 2).to_numpy()


if __name__ == "__main__":
    replay.directional_balance = channel_balance
    print("candidate=previous_20_bar_channel_midpoint_exit", flush=True)
    asyncio.run(replay.main())

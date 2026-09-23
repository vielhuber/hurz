"""Preregistered admission gate: Aroon(25) must agree with trade direction.

Compare the ages of the latest high and low in 26 completed bars,
including the signal bar: oscillator = 100 * (low_age - high_age) / 25.
Repeated extremes use their most recent occurrence. Longs need positive
values, shorts negative; zero and undefined values refuse entry.
Baseline weekly rankings and all existing risk limits remain unchanged.
No diagnostic arm or threshold search. Adoption requires four better
OOS samples and pooled paired daily t > +2. Offline, read-only replay.
"""
import asyncio

import numpy as np
import pandas as pd

from scripts import burst_adx_priority as replay
from scripts.efficiency_weighted_selection import all_signals as base_signals


def aroon(frame):
    high_age = frame["high"].rolling(26).apply(lambda values: np.argmax(values[::-1]), raw=True)
    low_age = frame["low"].rolling(26).apply(lambda values: np.argmin(values[::-1]), raw=True)
    return (100 * (low_age - high_age) / 25).to_numpy()


def signals_with_aroon(frames, atr_floor, metadata):
    signals = base_signals(frames, atr_floor, metadata)
    values = {pair: pd.Series(aroon(frame), index=frame["timestamp"].to_numpy(dtype="datetime64[ns]"))
              for pair, frame in frames.items()}
    for trade in signals:
        trade["aroon"] = float(values[trade["pair"]].loc[trade["ts"]])
    return signals


def prioritize(window, order, enabled):
    active = [trade for trade in window if (trade["strat"], trade["pair"]) in order
              and (not enabled or trade["dir"] * trade["aroon"] > 0)]
    return sorted(active, key=lambda trade: (trade["ts"], order[(trade["strat"], trade["pair"])]))


if __name__ == "__main__":
    replay.all_signals = signals_with_aroon
    replay.prioritize = prioritize
    print("candidate=entry_aroon_25_agrees_with_direction", flush=True)
    asyncio.run(replay.main())

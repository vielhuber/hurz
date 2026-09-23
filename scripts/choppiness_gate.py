"""Preregistered entry-only filter: refuse Choppiness(14) above 61.8.

Use 100 * log10(sum(true range, 14) / (max(high, 14) - min(low, 14)))
/ log10(14), including the completed signal bar. Undefined values refuse
candidate entries. Keep baseline weekly rankings unchanged: this is an
admission gate, not a selector change. All existing risk limits remain.
No diagnostic arm; adoption needs four better OOS samples and pooled
paired daily t > +2. Cached history and read-only journal only.
"""
import asyncio

import numpy as np
import pandas as pd

from scripts import burst_adx_priority as replay
from scripts.efficiency_weighted_selection import all_signals as base_signals


def choppiness(frame):
    previous = frame["close"].shift(1)
    true_range = pd.concat([frame["high"] - frame["low"],
                            (frame["high"] - previous).abs(),
                            (frame["low"] - previous).abs()], axis=1).max(axis=1)
    width = frame["high"].rolling(14).max() - frame["low"].rolling(14).min()
    ratio = true_range.rolling(14).sum() / width.where(width > 0)
    return (100 * np.log10(ratio) / np.log10(14)).to_numpy()


def signals_with_choppiness(frames, atr_floor, metadata):
    signals = base_signals(frames, atr_floor, metadata)
    values = {pair: pd.Series(choppiness(frame), index=frame["timestamp"].to_numpy(dtype="datetime64[ns]"))
              for pair, frame in frames.items()}
    for trade in signals:
        trade["choppiness"] = float(values[trade["pair"]].loc[trade["ts"]])
    return signals


def prioritize(window, order, enabled):
    active = [trade for trade in window if (trade["strat"], trade["pair"]) in order
              and (not enabled or trade["choppiness"] <= 61.8)]
    return sorted(active, key=lambda trade: (trade["ts"], order[(trade["strat"], trade["pair"])]))


if __name__ == "__main__":
    replay.all_signals = signals_with_choppiness
    replay.prioritize = prioritize
    print("candidate=entry_choppiness_14_at_most_61_8", flush=True)
    asyncio.run(replay.main())

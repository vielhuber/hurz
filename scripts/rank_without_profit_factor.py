"""Preregistered selector lever: remove the profit-factor score multiplier.

Candidate score is expectancy * log1p(trade count), not multiplied by
profit factor. Keep all eligibility thresholds, top-40 selection, pins,
reservations and vetoes. Use each arm's ordered list for simultaneous
admissions. Sizes, stops and position guards remain unchanged, although
different admissions can change realised exposure within those limits.
No alternative score or diagnostic arm. Adoption needs four better OOS
samples and pooled paired daily t > +2. Offline read-only weekly replay.
"""
import asyncio
import math

import numpy as np

from scripts import burst_adx_priority as replay
from scripts import pin_eligibility as pe
from scripts.burst_cost_priority import active_order as baseline_order


def active_orders(window, pins, reserved, pin_order):
    baseline = baseline_order(window, pins, reserved, pin_order)
    if pe.RANK_SEQUENTIAL:
        window = pe.sequential(window)
    grouped = {}
    for trade in window:
        grouped.setdefault((trade["strat"], trade["pair"]), []).append(trade["r"])
    rows = []
    for key, values in grouped.items():
        if len(values) < pe.MIN_N:
            continue
        returns = np.array(values)
        expectancy = float(returns.mean())
        losses = -returns[returns < 0].sum()
        profit_factor = 5.0 if losses <= 0 else float(returns[returns > 0].sum() / losses)
        if profit_factor < pe.MIN_PF or expectancy < pe.MIN_ER:
            continue
        rows.append((expectancy * math.log1p(len(values)), key))
    rows.sort(reverse=True)
    ranked = [key for _, key in rows
              if (key[1] not in reserved or key in pins) and key not in pe.VETOED][:pe.LIVE_N]
    candidate = {key: index for index, key in enumerate(ranked)}
    for key in pin_order:
        if key in pins and key not in candidate:
            candidate[key] = len(candidate)
    return {key: (baseline.get(key), candidate.get(key)) for key in baseline | candidate}


def prioritize(window, order, enabled):
    active = [trade for trade in window if (trade["strat"], trade["pair"]) in order
              and order[(trade["strat"], trade["pair"])][enabled] is not None]
    return sorted(active, key=lambda trade: (trade["ts"], order[(trade["strat"], trade["pair"])][enabled]))


if __name__ == "__main__":
    replay.active_order = active_orders
    replay.prioritize = prioritize
    print("candidate=rank_without_profit_factor_multiplier", flush=True)
    asyncio.run(replay.main())

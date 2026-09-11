"""How much of the book is cost, and whether anything is left without it.

Section 220 established what is not the constraint — not capacity, not
the composition of the list, not its length or its thresholds — and named
what is left: the expectancy of the trade itself. Section 143 read that
expectancy on the live journal at -0.065 R, with the two barriers costing
0.09 R a trade and the stale exits carrying +0.065 R.

Before testing another filter on that axis it is worth bounding the axis.
Every entry in this harness is booked with an explicit cost term,
`cost_r = 2 * fee * entry / stop_distance`, the round-trip spread
expressed in units of risk. Setting that term to zero gives the gross
book: what the strategies would earn at a venue that charged nothing.

The two numbers decide where any further work belongs:

  - if the gross book is clearly positive and the net one is not, the
    cost term is the whole problem and the only levers that can matter
    are the ones that reduce it,
  - if the gross book is near zero as well, no filter, list or exit rule
    can be tuned into a profit, and the honest conclusion is that this
    strategy family has no edge at this venue on this data.

Same walk-forward as 215-220 at the live configuration, so the figures
are comparable with every measurement of the series. Reported per sample
in USD per calendar day and in R per trade, with the cost share of gross.

This run measures; it proposes no change by itself. See EDGE_FINDINGS 221.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book,
    MIN_RANK_TRADES, RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS, HOLD,
)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple

MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


def all_signals_both(frames, atr_floor, meta):
    """Every gated signal, booked twice: with the cost term and without."""
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for s in STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                net, xb = book(O, H, L, C, x.index, x.direction,
                               entry, stop_d, cost_r, n)
                if net is None: continue
                gross, _ = book(O, H, L, C, x.index, x.direction,
                                entry, stop_d, 0.0, n)
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "strat": s, "r": net, "gross_r": gross,
                            "cost_r": cost_r, "risk": risk_usd,
                            "usd": net * risk_usd, "gross_usd": gross * risk_usd})
    out.sort(key=lambda z: z["ts"])
    return out


def ranked(window):
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < MIN_RANK_TRADES: continue
        r = np.array([t["r"] for t in ts])
        eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains / losses)
        if pf < MIN_PF or eR < MIN_ER: continue
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    return [k for _, k in rows[:TOP_N]]


def replay(window, active):
    open_until = {}; taken = []
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, u in open_until.items() if u <= t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until: continue
        if len(open_until) >= base.MAX_CONCURRENT: continue
        open_until[t["pair"]] = t["exit_ts"]
        taken.append(t)
    return taken


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    sig = all_signals_both(frames, atr_floor, meta)
    print(f"instruments={len(frames)} signals={len(sig)}", flush=True)

    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step

    taken = []
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        taken += replay(tw, set(ranked(rw)))
    print(f"out-of-sample blocks: {len(blocks)}  trades: {len(taken)}", flush=True)

    ts = np.array([np.datetime64(t["exit_ts"], 'D') for t in taken])
    net_r = np.array([t["r"] for t in taken])
    gross_r = np.array([t["gross_r"] for t in taken])
    cost_r = np.array([t["cost_r"] for t in taken])
    net_usd = np.array([t["usd"] for t in taken])
    gross_usd = np.array([t["gross_usd"] for t in taken])
    today = np.datetime64(t1, 'D')

    print("\n{:<14}{:>8}{:>11}{:>11}{:>11}{:>13}{:>13}".format(
        "sample", "trades", "gross R", "cost R", "net R", "gross $/day", "net $/day"))
    for dfrom, dto in WINDOWS:
        m = (ts > today - np.timedelta64(dfrom, 'D')) & (ts <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(dfrom - dto)
        print(f"{f'{dto}-{dfrom} d':<14}{m.sum():>8}{gross_r[m].mean():>+11.4f}"
              f"{cost_r[m].mean():>11.4f}{net_r[m].mean():>+11.4f}"
              f"{gross_usd[m].sum()/span:>+13.4f}{net_usd[m].sum()/span:>+13.4f}")
    span_all = float((today - ts.min()) / np.timedelta64(1, 'D'))
    print(f"{'pooled':<14}{len(taken):>8}{gross_r.mean():>+11.4f}"
          f"{cost_r.mean():>11.4f}{net_r.mean():>+11.4f}"
          f"{gross_usd.sum()/span_all:>+13.4f}{net_usd.sum()/span_all:>+13.4f}")
    print(f"\ngross E[R] per trade {gross_r.mean():+.4f}  t {t_stat(gross_r):+.2f}")
    print(f"net   E[R] per trade {net_r.mean():+.4f}  t {t_stat(net_r):+.2f}")
    print(f"cost  per trade      {cost_r.mean():.4f} R "
          f"({cost_r.mean()/abs(gross_r.mean()) if gross_r.mean() else float('nan'):.2f}x the gross edge)")
asyncio.run(main())

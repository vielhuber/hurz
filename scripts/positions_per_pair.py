"""What the one-position-per-instrument rule costs the book.

Section 222 decomposed the daily figure into frequency x expectancy x
size and found frequency the widest of the three: the book opens 2.7
trades a day against a bound of 12.57 with every constraint removed.
That bound was computed as a bound — no cap, no per-pair rule, no list —
and dismissed as unimplementable in one line. It has never been measured
as a setting the bot could actually run.

The per-pair rule is the binding half of it. Section 224 found that
deleting turtle_breakout removes 43 % of all signals and 3.7 % of the
trades, because donchian already holds the instrument when the others
fire; section 218 found the same for a longer list. Both readings say
the same thing: signals are not scarce, instrument slots are. Section 13
measured the stacked three-strategy book at six times live throughput
with expectancy per trade unaffected.

So the question is whether a second concurrent position on one
instrument is additional frequency or disguised leverage. Those are not
the same thing. If the second entry fires in the same direction while
the first is still open, the pair is one position of twice the size, and
that is risk scaling — which this project gates on forward evidence and
does not open on a backtest (section 211, 212). If it fires later, or
against the first, it is genuine throughput the rule was discarding.

The candidate is fixed at two positions per instrument — the smallest
loosening available. The concurrent cap of 8, the cluster cap and every
other guard stay exactly where they are; three-per-instrument is printed
as a diagnostic only and cannot qualify.

Acceptance, fixed before the data were seen:

  (a) two-per-instrument earns more USD per calendar day than the live
      rule on ALL FOUR year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) fewer than half of the stacked entries are same-direction against
      a position still open on that instrument — otherwise the gain is
      leverage wearing a frequency costume and clause (a) is measuring
      a bigger bet, not a better one.

All three must hold. This raises per-instrument concentration by
construction, so the peak open book and the stacked share are reported
with the result rather than left implicit. See docs/EDGE_FINDINGS.md 227.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book,
    MIN_RANK_TRADES, RANK_DAYS, TRADE_DAYS, META_CACHE,
)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple

HOUR_STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]
CANDIDATE = 2


def signals_for(frames, atr_floor, meta):
    """Every gated, sizeable signal, carrying its direction.

    The base harness drops the direction once a trade is booked; the
    stacking diagnostic needs it to tell a second entry apart from a
    doubled one."""
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for strat in HOUR_STRATS:
            for x in get_strategy(strat)(df, {}):
                if gate(strat, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "strat": strat, "dir": x.direction, "r": r,
                            "usd": r * risk_usd})
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
    return {k for _, k in rows[:TOP_N]}


def replay(window, active, per_pair):
    """Replay one block, allowing `per_pair` concurrent entries per name."""
    open_pos = []; per_day = {}; n = 0
    stacked = 0; stacked_same_dir = 0; peak = 0
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        open_pos = [o for o in open_pos if o["exit_ts"] > t["ts"]]
        same = [o for o in open_pos if o["pair"] == t["pair"]]
        if len(same) >= per_pair: continue
        if len(open_pos) >= base.MAX_CONCURRENT: continue
        if same:
            stacked += 1
            if any(o["dir"] == t["dir"] for o in same): stacked_same_dir += 1
        open_pos.append({"pair": t["pair"], "exit_ts": t["exit_ts"], "dir": t["dir"]})
        peak = max(peak, len(open_pos))
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0) + t["usd"]
        n += 1
    return per_day, n, stacked, stacked_same_dir, peak


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    sig = signals_for(frames, atr_floor, meta)
    print(f"instruments={len(frames)} atr_floor={atr_floor:g} gated signals={len(sig)}",
          flush=True)

    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    print(f"span {np.datetime64(t0,'D')} … {np.datetime64(t1,'D')}  "
          f"blocks={len(blocks)}", flush=True)

    variants = [1, CANDIDATE, 3]
    daily = {v: {} for v in variants}; counts = {v: 0 for v in variants}
    stack = {v: [0, 0] for v in variants}; peaks = {v: 0 for v in variants}
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        active = ranked(rw)
        for v in variants:
            per_day, n, st, sd, pk = replay(tw, active, v)
            for d, val in per_day.items():
                daily[v][d] = daily[v].get(d, 0.0) + val
            counts[v] += n; stack[v][0] += st; stack[v][1] += sd
            peaks[v] = max(peaks[v], pk)

    print("\n{:<18}{:>9}{:>10}{:>10}{:>12}{:>7}".format(
        "per instrument", "trades", "vs live", "stacked", "same dir", "peak"))
    for v in variants:
        st, sd = stack[v]
        share = f"{sd/st:.0%}" if st else "—"
        print(f"{v:<18}{counts[v]:>9}{counts[v]/max(1,counts[1])-1:>+10.1%}"
              f"{st:>10}{share:>12}{peaks[v]:>7}")

    all_days = sorted(set().union(*[set(daily[v]) for v in variants]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {v: np.array([daily[v].get(d, 0.0) for d in all_days]) for v in variants}

    print("\n{:<14}{:>12}{:>12}{:>12}{:>12}".format(
        "sample", "live (1)", "two", "three", "two − live"))
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today - np.timedelta64(dfrom, 'D')) & (days <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        a = series[1][m].sum()/span; b = series[CANDIDATE][m].sum()/span
        c = series[3][m].sum()/span
        better.append(b > a)
        print(f"{f'{dto}-{dfrom} d':<14}{a:>+12.4f}{b:>+12.4f}{c:>+12.4f}{b-a:>+12.4f}")

    diff = series[CANDIDATE] - series[1]
    t = t_stat(diff)
    st, sd = stack[CANDIDATE]
    same_share = sd/st if st else 0.0
    print(f"\npooled two − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    print(f"live {series[1].sum()/len(all_days):+.4f} USD/day  "
          f"two {series[CANDIDATE].sum()/len(all_days):+.4f} USD/day")
    ok_a = all(better); ok_b = t > 2; ok_c = same_share < 0.5
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) same-direction stacking < 50 %: "
          f"{'YES' if ok_c else 'NO'} ({same_share:.0%})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())

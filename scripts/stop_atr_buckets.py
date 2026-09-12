"""Where the stop sits in ATR terms, above the floor that already exists.

Reconstructing the ATR at each journalled signal bar splits the live
book sharply: of 230 trades since 2026-07-01, the 137 whose stop sat
under 3 ATR stopped out at 45 %, the 93 at or above it at 15 % — and
15 % is the harness's own 13.6 %. In dollars the two groups are
indistinguishable (-0.037 against -0.130 USD a trade, t +0.26); what
differs is dispersion, 3.67 against 1.73. The floor commit 09254f9 put
in on 2026-09-10 is therefore a variance filter, not an earnings one,
and the live/harness gap stays unexplained.

That floor is in and this run does not reopen it. What it does is ask
whether the same quantity keeps ordering outcomes *above* the floor.
Stop distance in ATR is the strongest separator of stop rate this
project has measured, and the selector does not price it at all: it
ranks by expectancy in R, which section 130 showed does not transfer,
while stop-distance-in-ATR follows volatility against a fixed venue
minimum and is observable before the entry.

Quartile edges are fixed on the recent year and applied unchanged to the
older samples, as in sections 40 to 43.

Acceptance, fixed before the data were seen — the same bar those runs
used, so this cannot be a looser test than the ones it follows:

  a quartile is blocked only if it is significantly negative (t < -2) on
  BOTH disjoint samples, and its difference to the rest holds at
  |t| > 2 with the same sign on both.

A block removes entries and lifts no limit. See docs/EDGE_FINDINGS.md 230.
"""
import asyncio, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, trade_terms, book, META_CACHE,
)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple

STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
RECENT = 365; OLDER_FROM = 366; OLDER_TO = 1095


def collect(frames, meta, atr_floor):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        A = df["atr_14"].values
        for s in STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                t = trade_terms(df, x.index, pair, meta, atr_floor)
                if t is None: continue
                entry, stop_d, cost_r, _ = t
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "r": r, "mult": stop_d/float(A[x.index])})
    return out


def stats(v):
    v = np.asarray(v)
    if len(v) < 2: return float('nan'), float('nan')
    return float(v.mean()), float(v.mean()/(v.std(ddof=1)/np.sqrt(len(v))))


def diff_t(a, b):
    a = np.asarray(a); b = np.asarray(b)
    if len(a) < 2 or len(b) < 2: return float('nan')
    se = np.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
    return float((a.mean()-b.mean())/se) if se > 0 else float('nan')


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    sig = collect(frames, meta, atr_floor)
    ts = np.array([s["ts"] for s in sig])
    today = ts.max()
    mult = np.array([s["mult"] for s in sig]); rs = np.array([s["r"] for s in sig])
    recent = ts > today - np.timedelta64(RECENT, 'D')
    older = (ts <= today - np.timedelta64(OLDER_FROM, 'D')) & (ts > today - np.timedelta64(OLDER_TO, 'D'))
    print(f"signals={len(sig)}  floor={atr_floor:g}  recent={recent.sum()}  older={older.sum()}")
    print(f"stop distance in ATR: min {mult.min():.2f} median {np.median(mult):.2f} max {mult.max():.2f}")

    edges = np.percentile(mult[recent], [25, 50, 75])
    print(f"quartile edges fixed on the recent year: {edges.round(3)}")
    lab = lambda m: np.digitize(m, edges)
    names = [f"below {edges[0]:.2f}", f"{edges[0]:.2f}–{edges[1]:.2f}",
             f"{edges[1]:.2f}–{edges[2]:.2f}", f"above {edges[2]:.2f}"]

    print(f"\n{'bucket':<16}{'recent n/E[R]/t/t_diff':>34}{'older n/E[R]/t/t_diff':>34}")
    verdict = []
    for q in range(4):
        row = [names[q]]
        flags = []
        for mask in (recent, older):
            sel = mask & (lab(mult) == q)
            rest = mask & (lab(mult) != q)
            e, t = stats(rs[sel]); td = diff_t(rs[sel], rs[rest])
            row.append(f"{sel.sum()} / {e:+.3f} / {t:+.2f} / {td:+.2f}")
            flags.append((t < -2, abs(td) > 2, np.sign(td)))
        neg = flags[0][0] and flags[1][0]
        dif = flags[0][1] and flags[1][1] and flags[0][2] == flags[1][2]
        verdict.append(neg and dif)
        print(f"{row[0]:<16}{row[1]:>34}{row[2]:>34}   {'BLOCK' if (neg and dif) else '—'}")
    print(f"\nVERDICT: {'BUILD' if any(verdict) else 'DISCARD'}")
asyncio.run(main())

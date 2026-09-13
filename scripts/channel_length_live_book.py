"""The breakout channels, re-measured on the system that now trades.

`donchian_breakout` looks back 20 bars and `turtle_breakout` 55. Both
lengths were swept once, on 2026-09-07 (runs 9 and 14), and both sweeps
leaned the same way — the longer channel was better on both disjoint
samples and missed the bar only on t:

    donchian 80 vs 20:   +0.0106 R (t +0.30)   +0.0311 R (t +1.29)
    turtle  110 vs 55:   +0.0206 R (t +0.51)   +0.0251 R (t +0.93)

Neither sweep describes the book running today. Donchian was measured
at a 1-ATR stop, before section 11 doubled it; both ran on ten
instruments, pooled in R, before the ADX ceiling, the 3xATR floor,
dollar sizing, the consistency block, the merged cluster map, the live
selector and the 27-instrument universe existed. A longer channel under
a 2-ATR stop and a router that already refuses exhausted trends is a
different question, and two same-signed readings on a retired system
are a reason to ask it once properly — not a result.

Unlike the levers of sections 245 and 246, this one touches every trade
of the two strategies that make up 48 of the 55 live combinations, so a
null here cannot be a null of exposure.

One candidate, fixed from the earlier readings before these data were
seen: donchian 80 AND turtle 110 together, against 20 / 55. Each alone
is printed as a diagnostic with no standing. Measured on the harness as
it now matches production — the scheduler's strategy mix, the 27-instrument
live universe, and the selector the scheduler runs (top 40, pf >= 0.8,
eR >= -0.2).

Acceptance:

  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2,
  (c) USD per trade must rise — a longer channel fires less, and a
      variant that only moves dollars by trading differently in count is
      not the lever being tested.

No risk limit moves: stop, target, size, caps and guards are untouched.

See docs/EDGE_FINDINGS.md 248.
"""
import asyncio, json, math, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book, trade, admit,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS,
)

MIN_PF = 0.8; MIN_ER = -0.2; LIVE_N = 40
VARIANTS = {
    "live 20/55":        {"donchian_breakout": 20, "turtle_breakout": 55},
    "candidate 80/110":  {"donchian_breakout": 80, "turtle_breakout": 110},
    "diag donchian 80":  {"donchian_breakout": 80, "turtle_breakout": 55},
    "diag turtle 110":   {"donchian_breakout": 20, "turtle_breakout": 110},
}
BASE = "live 20/55"; CANDIDATE = "candidate 80/110"
YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]


def signals(frames, atr_floor, meta, periods):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for s in STRATS:
            params = {"period": periods[s]} if s in periods else {}
            for x in get_strategy(s)(df, params):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair, "dir": x.direction,
                            "strat": s, "r": r, "usd": r * risk_usd})
    out.sort(key=lambda z: z["ts"])
    return out


def rank_live(window):
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < 10: continue
        r = np.array([t["r"] for t in ts])
        eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains / losses)
        if pf < MIN_PF or eR < MIN_ER: continue
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    return {k for _, k in rows[:LIVE_N]}


def walk_forward(sig, blocks):
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    daily = {}; usd = []
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        active = rank_live(rw)
        per_day, _ = trade(tw, active)
        for d, v in per_day.items():
            daily[d] = daily.get(d, 0.0) + v
        usd.extend(t["usd"] for t in admit(tw, active))
    return daily, np.array(usd)


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {}
    for pair, rows in raw.items():
        if pair not in meta or len(rows) < 2000: continue
        frames[pair] = add_indicators(to_frame(rows))
    print(f"instruments={len(frames)} strategies={STRATS} atr_floor={atr_floor:g}",
          flush=True)

    sigs = {}
    for label, periods in VARIANTS.items():
        sigs[label] = signals(frames, atr_floor, meta, periods)
        by = {}
        for s in sigs[label]: by[s["strat"]] = by.get(s["strat"], 0) + 1
        print(f"{label:<18} signals {len(sigs[label]):>6}  {by}", flush=True)

    t0 = min(s["ts"] for s in sigs[BASE]); t1 = max(s["ts"] for s in sigs[BASE])
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"out-of-sample blocks: {len(blocks)}", flush=True)

    books = {label: walk_forward(sig, blocks) for label, sig in sigs.items()}
    base_daily, base_usd = books[BASE]

    for label in VARIANTS:
        if label == BASE: continue
        daily, usd = books[label]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days])
        b = np.array([daily.get(d, 0.0) for d in days])
        print(f"\n--- {label} vs {BASE} ---")
        print(f"trades {len(base_usd)} -> {len(usd)} "
              f"({len(usd)/max(1,len(base_usd))-1:+.1%});  USD per trade "
              f"{base_usd.mean():+.4f} -> {usd.mean():+.4f}")
        print(f"{'sample':<14}{'live':>11}{'variant':>11}{'diff':>10}{'t':>8}")
        up = 0
        for lo, hi in YEARS:
            sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                            < (now - np.timedelta64(lo, 'D')) for d in days])
            span = float(hi - lo)
            av = a[sel].sum() / span; bv = b[sel].sum() / span
            if bv > av: up += 1
            print(f"{lo}-{hi} d{'':<4}{av:>+11.4f}{bv:>+11.4f}{bv-av:>+10.4f}"
                  f"{t_stat(b[sel]-a[sel]):>+8.2f}")
        d_ = b - a
        print(f"{'pooled':<14}{a.sum()/len(days):>+11.4f}{b.sum()/len(days):>+11.4f}"
              f"{d_.sum()/len(days):>+10.4f}{t_stat(d_):>+8.2f}")
        if label == CANDIDATE:
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
                  f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")
            print(f"clause (c): USD/trade {base_usd.mean():+.4f} -> {usd.mean():+.4f} — "
                  f"{'PASS' if usd.mean() > base_usd.mean() else 'FAIL'}")

if __name__ == "__main__":
    asyncio.run(main())

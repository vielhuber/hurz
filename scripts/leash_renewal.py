"""Renewing the leash when the trend signals again, instead of refusing it.

Section 83: 83 % of signals arrive into an instrument the book already
holds, and 80 % of those point the SAME way. Today every one is refused
by `_has_open_position`. Section 227 let them in as a second position and
doubled the daily figure — which it then showed to be a doubling of
risk spelled as throughput, and rightly refused.

There is a third option neither run tried. A same-direction breakout on
a held instrument is a fresh, fully gated entry signal the book would
take if it were flat. Holding the existing position for another 24 bars
from that signal is economically that entry — minus the second spread,
and minus any added size. The open position keeps its stop and its
target; only the leash clock restarts.

  live       leash counted from the entry, same-direction signals refused
  candidate  a same-direction signal that passes every entry filter
             (router gate, direction block, 3xATR floor, cost ceiling,
             sizing) restarts the 24-bar leash; total hold capped at 96
  diag       the same, capped at 48

Stated risk change: positions are held longer. Money at risk never
exceeds the original 1 R — the stop does not move, no size is added.

Section 250 showed the open result at bar 24 carries no information and
section 229 that time alone adds nothing on average. The renewal is not
time alone: it is conditioned on a new entry signal, which is exactly
the information with the book's measured edge behind it.

Both books through the live-selector walk-forward (section 247's
harness), each ranking on its own R.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2,
  (c) on the trades the rule renews, the renewed R beats the R the live
      leash booked on the same trades — paired per trade, so refused
      marginal entries cannot pass for a gain.

See docs/EDGE_FINDINGS.md 251.
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
    load_history, to_frame, t_stat, trade_terms, trade,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS, RR, HOLD,
)

MIN_PF = 0.8; MIN_ER = -0.2; LIVE_N = 40
YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 96, "diag cap 48": 48}
CANDIDATE = "candidate"


def rank_live(window):
    """Top 40 under the scheduler's eligibility filter (section 217)."""
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


def admitted(tw, active):
    """The trades `trade()` admits, in order — for the per-trade clause."""
    open_until = {}; out = []
    for t in tw:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, u in open_until.items() if u <= t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until or len(open_until) >= 8: continue
        open_until[t["pair"]] = t["exit_ts"]; out.append(t)
    return out


def book(O, H, L, C, e, d, entry, stop_d, cost_r, n, renew, cap):
    """The harness's booking; `renew` = bars carrying a tradeable same-direction
    signal, `cap` = the longest total hold, or None for the live rule.

    Returns (r, exit_bar, renewed)."""
    sl = entry - d * stop_d; tp = entry + d * RR * stop_d
    last = e + HOLD; renewed = False
    b = e + 1
    while b <= last:
        if b >= n: return None, None, False
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b, renewed
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl):
            return -1.0 - cost_r, b, renewed
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp):
            return RR - cost_r, b, renewed
        if cap is not None and b in renew:
            new_last = min(b + HOLD, e + cap)
            if new_last > last:
                last = new_last; renewed = True
        b += 1
    if last < n:
        return (float(C[last]) - entry) * d / stop_d - cost_r, last, renewed
    return None, None, False


def signals(frames, atr_floor, meta):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        entries = []; tradeable = {+1: set(), -1: set()}
        for s in STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                tradeable[x.direction].add(x.index)
                entries.append((s, x, terms))
        for s, x, terms in entries:
            entry, stop_d, cost_r, risk_usd = terms
            args = (O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n,
                    tradeable[x.direction])
            booked = {arm: book(*args, cap) for arm, cap in ARMS.items()}
            if any(v[0] is None for v in booked.values()): continue
            r0 = booked["live"][0]
            base = {"ts": ts[x.index], "pair": pair, "strat": s}
            out.append({arm: dict(base, exit_ts=ts[bx], r=r, usd=r * risk_usd,
                                  renewed=rn, r_live=r0,
                                  bars=int(bx - x.index))
                        for arm, (r, bx, rn) in booked.items()})
    out.sort(key=lambda z: z["live"]["ts"])
    return out


def walk_forward(sig, blocks):
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    daily = {}; taken = []
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        active = rank_live(rw)
        per_day, _ = trade(tw, active)
        for d, v in per_day.items():
            daily[d] = daily.get(d, 0.0) + v
        taken.extend(admitted(tw, active))
    return daily, taken


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {}
    for pair, rows in raw.items():
        if pair not in meta or len(rows) < 2000: continue
        frames[pair] = add_indicators(to_frame(rows))
    print(f"instruments={len(frames)} strategies={STRATS} atr_floor={atr_floor:g}",
          flush=True)

    sig = signals(frames, atr_floor, meta)
    for arm in ARMS:
        if arm == "live": continue
        print(f"{arm:<12} renewed {sum(1 for s in sig if s[arm]['renewed'])} "
              f"of {len(sig)} signals", flush=True)

    books = {arm: [s[arm] for s in sig] for arm in ARMS}
    t0 = min(s["ts"] for s in books["live"]); t1 = max(s["ts"] for s in books["live"])
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"out-of-sample blocks: {len(blocks)}", flush=True)

    results = {arm: walk_forward(b, blocks) for arm, b in books.items()}
    base_daily, base_taken = results["live"]
    bu = np.array([t["usd"] for t in base_taken])
    bb = np.array([t["bars"] for t in base_taken])

    for arm in ARMS:
        if arm == "live": continue
        daily, taken = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days])
        b = np.array([daily.get(d, 0.0) for d in days])
        vu = np.array([t["usd"] for t in taken]); vb = np.array([t["bars"] for t in taken])
        print(f"\n--- {arm} ---")
        print(f"trades {len(base_taken)} -> {len(taken)} "
              f"({len(taken)/max(1,len(base_taken))-1:+.1%});  USD/trade "
              f"{bu.mean():+.4f} -> {vu.mean():+.4f};  mean hold "
              f"{bb.mean():.1f} -> {vb.mean():.1f} bars")
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
        ren = [t for t in taken if t["renewed"]]
        if ren:
            rr_ = np.array([t["r"] for t in ren]); rl = np.array([t["r_live"] for t in ren])
            print(f"renewed trades in the book {len(ren)} ({len(ren)/len(taken):.1%}): "
                  f"renewed R {rr_.mean():+.4f}, live R {rl.mean():+.4f}, paired "
                  f"{(rr_-rl).mean():+.4f} at t {t_stat(rr_-rl):+.2f}")
            print("  by sample (paired R): " + "  ".join(
                f"{lo}-{hi} d {np.mean([x['r']-x['r_live'] for x in ren if (now-np.timedelta64(hi,'D')) <= np.datetime64(x['ts'],'D') < (now-np.timedelta64(lo,'D'))] or [float('nan')]):+.4f}"
                for lo, hi in YEARS))
        if arm == CANDIDATE:
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
                  f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")
            if ren:
                print(f"clause (c): paired {(rr_-rl).mean():+.4f} R at t "
                      f"{t_stat(rr_-rl):+.2f} — {'PASS' if (rr_-rl).mean() > 0 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())

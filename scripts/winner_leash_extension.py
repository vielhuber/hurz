"""Letting only the winners run past the leash.

Every trend position that reaches neither its stop nor its 1.5 R target
is closed at the close of bar 24. Sections 198, 228 and 229 moved that
leash for every trade at once: shorter is worse, 36 looked better in the
sample that picked it and vanished outside it, and section 229's journal
replay showed why — twelve more bars bring 30 stops for 20 targets.

That replay never split the trades by where they stood at the leash, and
trend following says the split is the whole point: a position still
under water after 24 hours has had its chance, one in profit is the only
kind that can still travel to the target. The rule here keeps the leash
for losers exactly as live and extends only positions whose close at bar
24 is in profit.

  live       close at bar 24, whatever the open result
  candidate  if open R > 0 at bar 24, keep stop and target and hold to
             bar 48, else close at 24 as live
  diag       the same with a +0.5 R threshold, and with 36 bars

Stop, target, size and every guard are unchanged, so a trade can never
lose more than the 1 R stop. What rises is the time a profitable
position is held — stated as a risk change: exposure per position
lasts longer on the trades extended, and the one-position-per-instrument
rule then refuses more entries, which is exactly the occupancy confound
section 229 named. Clause (c) exists to see through it.

Both books through the live-selector walk-forward (section 247's
harness), each ranking on its own R.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2,
  (c) on the trades the rule actually extends, the extended R beats the
      R the live leash booked on the same trades — paired, per trade, so
      a gain from refused marginal entries cannot pass for one.

See docs/EDGE_FINDINGS.md 250.
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
    load_history, to_frame, t_stat, trade_terms, trade, admit,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS, RR, HOLD,
)

MIN_PF = 0.8; MIN_ER = -0.2; LIVE_N = 40


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


YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": (0.0, 48), "diag +0.5R/48": (0.5, 48),
        "diag 0R/36": (0.0, 36)}
CANDIDATE = "candidate"


def book(O, H, L, C, e, d, entry, stop_d, cost_r, n, extend):
    """The harness's booking; `extend` = (min open R, last bar) or None.

    Returns (r, exit_bar, extended)."""
    sl = entry - d * stop_d; tp = entry + d * RR * stop_d
    last = e + HOLD
    b = e + 1
    while b <= last:
        if b >= n: return None, None, False
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b, last > e + HOLD
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl):
            return -1.0 - cost_r, b, last > e + HOLD
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp):
            return RR - cost_r, b, last > e + HOLD
        if b == e + HOLD and extend is not None and last == e + HOLD:
            open_r = (float(C[b]) - entry) * d / stop_d
            if open_r > extend[0]:
                last = e + extend[1]
        b += 1
    if last < n:
        return (float(C[last]) - entry) * d / stop_d - cost_r, last, last > e + HOLD
    return None, None, False


def signals(frames, atr_floor, meta):
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
                args = (O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                booked = {arm: book(*args, ext) for arm, ext in ARMS.items()}
                if any(v[0] is None for v in booked.values()): continue
                r0 = booked["live"][0]
                base = {"ts": ts[x.index], "pair": pair, "strat": s, "dir": x.direction}
                out.append({arm: dict(base, exit_ts=ts[bx], r=r, usd=r * risk_usd,
                                      ext=ex, r_live=r0)
                            for arm, (r, bx, ex) in booked.items()})
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
        taken.extend(admit(tw, active))
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
        print(f"{arm:<14} extended {sum(1 for s in sig if s[arm]['ext'])} "
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

    for arm in ARMS:
        if arm == "live": continue
        daily, taken = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days])
        b = np.array([daily.get(d, 0.0) for d in days])
        vu = np.array([t["usd"] for t in taken])
        print(f"\n--- {arm} ---")
        print(f"trades {len(base_taken)} -> {len(taken)} "
              f"({len(taken)/max(1,len(base_taken))-1:+.1%});  USD/trade "
              f"{bu.mean():+.4f} -> {vu.mean():+.4f}")
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
        ext = [t for t in taken if t["ext"]]
        if ext:
            re_ = np.array([t["r"] for t in ext]); rl = np.array([t["r_live"] for t in ext])
            print(f"extended trades in the book {len(ext)} ({len(ext)/len(taken):.1%}): "
                  f"extended R {re_.mean():+.4f}, live R {rl.mean():+.4f}, paired "
                  f"{(re_-rl).mean():+.4f} at t {t_stat(re_-rl):+.2f}")
            print(f"  outcome of the extension: target {(re_ >= RR - 0.2).mean():.0%}, "
                  f"stop {(re_ <= -0.9).mean():.0%}, still open at the end "
                  f"{((re_ > -0.9) & (re_ < RR - 0.2)).mean():.0%}")
            print("  by sample (paired R): " + "  ".join(
                f"{lo}-{hi} d {np.mean([x['r']-x['r_live'] for x in ext if (now-np.timedelta64(hi,'D')) <= np.datetime64(x['ts'],'D') < (now-np.timedelta64(lo,'D'))] or [float('nan')]):+.4f}"
                for lo, hi in YEARS))
        if arm == CANDIDATE:
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
                  f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")
            if ext:
                print(f"clause (c): paired {(re_-rl).mean():+.4f} R at t "
                      f"{t_stat(re_-rl):+.2f} — {'PASS' if (re_-rl).mean() > 0 else 'FAIL'}")

if __name__ == "__main__":
    asyncio.run(main())

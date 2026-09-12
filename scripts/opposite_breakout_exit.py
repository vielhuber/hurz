"""Closing a trend position when the instrument breaks out the other way.

The live loop holds one position per instrument and refuses every entry
on a name that already has one (`_has_open_position`). Section 83
counted what that guard absorbs: 83 % of signals arrive while a position
is open, and 20 % of those point the OTHER way. Those are not entries
the book wants — but they are not nothing either. A short breakout on an
instrument held long means price has crossed the whole channel against
the position, and today the bot ignores it and lets the trade run on to
its stop, target or 24-bar leash.

No exit in the code reads it. The regime flip-exit closes mean-reversion
positions only and leaves trend positions alone by design. Section 182's
failed-breakout exit looked at a single bar — the one after the signal —
and failed because a third of the trades it cut went on to finish
positive. An opposite breakout is a later and much stronger invalidation:
by construction it can only fire after the move has reversed through the
far side of a channel.

Rule: while a position is open, if any of the scheduler's three
strategies fires the opposite direction on that instrument, close at
that bar's close. Stop and target keep priority inside the bar, so the
rule can only exit earlier than the live one, never later, and can never
lose more than the 1 R stop. It opens nothing: the exit does not reverse
into the new signal, so exposure time can only fall.

Both books run through the same walk-forward with the scheduler's
selector (top 40, pf >= 0.8, eR >= -0.2); each arm ranks on its own R,
as the nightly backtest would once the rule existed.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2,
  (c) on the trades the rule actually cuts, the cut R beats the R the
      live rule would have booked on the same trades — paired, so the
      exit is shown to save more than it gives away, not merely to
      shorten holds and free slots.

Router-gated opposite signals are printed as a diagnostic with no
standing. See docs/EDGE_FINDINGS.md 249.
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


def book(O, H, L, C, e, d, entry, stop_d, cost_r, n, opposite):
    """The harness's booking, with an optional exit on an opposite-signal bar.

    Returns (r, exit_bar, cut) — cut is True when the opposite signal
    closed the trade before stop, target or leash."""
    sl = entry - d * stop_d; tp = entry + d * RR * stop_d
    for b in range(e + 1, e + HOLD + 1):
        if b >= n: break
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b, False
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl):
            return -1.0 - cost_r, b, False
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp):
            return RR - cost_r, b, False
        if opposite is not None and b in opposite:
            return (float(C[b]) - entry) * d / stop_d - cost_r, b, True
    if e + HOLD < n:
        return (float(C[e + HOLD]) - entry) * d / stop_d - cost_r, e + HOLD, False
    return None, None, False


def signals(frames, atr_floor, meta):
    """Every gated signal, booked under the live exit and under both variants."""
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        raw = {s: get_strategy(s)(df, {}) for s in STRATS}
        opp_raw = {+1: set(), -1: set()}; opp_gated = {+1: set(), -1: set()}
        for s, xs in raw.items():
            for x in xs:
                opp_raw[x.direction].add(x.index)
                if not gate(s, df, x.index).blocked:
                    opp_gated[x.direction].add(x.index)
        for s, xs in raw.items():
            for x in xs:
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                args = (O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                r0, b0, _ = book(*args, None)
                if r0 is None: continue
                r1, b1, c1 = book(*args, opp_raw[-x.direction])
                r2, b2, c2 = book(*args, opp_gated[-x.direction])
                base = {"ts": ts[x.index], "pair": pair, "strat": s}
                out.append({
                    "live": dict(base, exit_ts=ts[b0], r=r0, usd=r0 * risk_usd),
                    "raw": dict(base, exit_ts=ts[b1], r=r1, usd=r1 * risk_usd,
                                cut=c1, r_live=r0),
                    "gated": dict(base, exit_ts=ts[b2], r=r2, usd=r2 * risk_usd,
                                  cut=c2, r_live=r0),
                })
    out.sort(key=lambda z: z["live"]["ts"])
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
    for arm in ("raw", "gated"):
        cuts = [s[arm] for s in sig if s[arm]["cut"]]
        print(f"{arm:<6} signals {len(sig)}, cut by an opposite breakout "
              f"{len(cuts)} ({len(cuts)/len(sig):.1%})", flush=True)

    books = {arm: [s[arm] for s in sig] for arm in ("live", "raw", "gated")}
    t0 = min(s["ts"] for s in books["live"]); t1 = max(s["ts"] for s in books["live"])
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"out-of-sample blocks: {len(blocks)}", flush=True)

    results = {arm: walk_forward(b, blocks) for arm, b in books.items()}
    base_daily, base_taken = results["live"]

    for arm, tag in (("raw", "candidate"), ("gated", "diagnostic")):
        daily, taken = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days])
        b = np.array([daily.get(d, 0.0) for d in days])
        print(f"\n--- opposite-breakout exit, {arm} signals ({tag}) ---")
        bu = np.array([t["usd"] for t in base_taken]); vu = np.array([t["usd"] for t in taken])
        print(f"trades {len(base_taken)} -> {len(taken)} "
              f"({len(taken)/max(1,len(base_taken))-1:+.1%});  USD/trade "
              f"{bu.mean():+.4f} -> {vu.mean():+.4f}")
        print(f"{'sample':<14}{'live':>11}{'exit':>11}{'diff':>10}{'t':>8}")
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

        cut_trades = [t for t in taken if t["cut"]]
        if cut_trades:
            rc = np.array([t["r"] for t in cut_trades])
            rl = np.array([t["r_live"] for t in cut_trades])
            print(f"cut trades in the book {len(cut_trades)} "
                  f"({len(cut_trades)/max(1,len(taken)):.1%}): cut R {rc.mean():+.4f}, "
                  f"live R on the same trades {rl.mean():+.4f}, paired "
                  f"{(rc-rl).mean():+.4f} at t {t_stat(rc-rl):+.2f}; "
                  f"{(rl > 0).mean():.0%} would have ended positive under live")
            print(f"  by sample (paired R): " + "  ".join(
                f"{lo}-{hi} d {np.mean([x['r']-x['r_live'] for x in cut_trades if (now-np.timedelta64(hi,'D')) <= np.datetime64(x['ts'],'D') < (now-np.timedelta64(lo,'D'))] or [float('nan')]):+.4f}"
                for lo, hi in YEARS))
        if arm == "raw":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
                  f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")
            if cut_trades:
                print(f"clause (c): paired cut vs live {(rc-rl).mean():+.4f} R at t "
                      f"{t_stat(rc-rl):+.2f} — {'PASS' if (rc-rl).mean() > 0 else 'FAIL'}")

asyncio.run(main())

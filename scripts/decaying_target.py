"""Lowering the take-profit for the second half of the leash.

Most positions end at the 24th bar having reached neither barrier, and the
1.5 R target sits 4.5 ATR or more away on a venue-pinned stop. A position
still open at bar 12 has half its time left to cover the full distance.
Every target run so far held the target fixed for the whole hold (RR
sweeps, the volatility-anchored target, the ADX-conditional target, half
out at +1 R); none lowered it as the leash ran down.

  live       target 1.5 R for all 24 bars
  candidate  1.5 R through bar 12, 1.0 R from bar 13 on
  diag       1.5 R through bar 12, 0.5 R from bar 13 on

A lowered target already passed by the bar's close is filled at that
bar's open if the open is beyond it, otherwise at the target. Stop, leash,
size and every guard unchanged; the selector ranks on each arm's own
booking.

Walk-forward on the live-faithful book of section 255.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 271.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.flat_before_weekend import admit
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS, RR, HOLD,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 1.0, "diag": 0.5}
SWITCH_BAR = 12


def book_decay(O, H, L, C, e, d, entry, stop_d, cost_r, n, late_rr):
    sl = entry - d * stop_d
    for b in range(e + 1, e + HOLD + 1):
        if b >= n: break
        rr = RR if late_rr is None or b - e <= SWITCH_BAR else late_rr
        tp = entry + d * rr * stop_d
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b
        if gap >= rr * stop_d: return gap / stop_d - cost_r, b
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl): return -1.0 - cost_r, b
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp): return rr - cost_r, b
    if e + HOLD < n: return (float(C[e + HOLD]) - entry) * d / stop_d - cost_r, e + HOLD
    return None, None


def signals(frames, atr_floor, meta, late_rr):
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
                r, xb = book_decay(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n, late_rr)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "dir": x.direction, "strat": s, "r": r, "usd": r * risk_usd})
    out.sort(key=lambda z: z["ts"])
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, late_rr in ARMS.items():
        sig = signals(frames, _min_stop_atr_multiple(), meta, late_rr)
        t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
        step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
        cut = np.datetime64(t0, 'D') + rank_w; daily = {}; taken = []
        while cut + step <= np.datetime64(t1, 'D'):
            ranked, _ = pe.lists([s for s in sig if cut - rank_w <= s["ts"] < cut], pins, reserved)
            for t in admit([s for s in sig if cut <= s["ts"] < cut + step], ranked | pins):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut += step
        results[arm] = daily
        print(f"{arm:<10} trades {len(taken)}, USD/trade {np.mean([t['usd'] for t in taken]):+.4f}, "
              f"mean R {np.mean([t['r'] for t in taken]):+.4f}", flush=True)

    base = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        other = results[arm]
        days = sorted(set(base) | set(other))
        a = np.array([base.get(d, 0.0) for d in days]); b = np.array([other.get(d, 0.0) for d in days])
        print(f"\n--- {arm} ---")
        print(f"{'sample':<14}{'live':>11}{'variant':>11}{'diff':>10}{'t':>8}")
        up = 0
        for lo, hi in YEARS:
            sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                            < (now - np.timedelta64(lo, 'D')) for d in days])
            span = float(hi - lo); av = a[sel].sum() / span; bv = b[sel].sum() / span
            up += bv > av
            print(f"{lo}-{hi} d{'':<4}{av:>+11.4f}{bv:>+11.4f}{bv-av:>+10.4f}{t_stat(b[sel]-a[sel]):>+8.2f}")
        d_ = b - a
        print(f"{'pooled':<14}{a.sum()/len(days):>+11.4f}{b.sum()/len(days):>+11.4f}"
              f"{d_.sum()/len(days):>+10.4f}{t_stat(d_):>+8.2f}")
        if arm == "candidate":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — {'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())

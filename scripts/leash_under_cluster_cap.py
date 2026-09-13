"""The 24-bar leash, re-asked on a book whose binding constraint is the cluster cap.

Sections 198, 228 and 229 swept the leash on harnesses without the cluster
cap, where capacity never bound: section 220 measured occupancy at 3.2 of
8 and concluded a freed slot is worth about nothing. Shorter leashes lost
because the book's expectancy comes from time (198); 36 bars looked better
only in the sample that chose it (229).

Section 252 put the cap into the harness, and on the live list it refuses
about four thousand `risk_on` entries. There a slot is not free: every hour
a position holds it, the next same-direction signal in that cluster is
refused. Section 251 already showed the consequence in one direction —
renewing the leash, harmless without the cap, turned negative under it.
The other direction has not been measured: a shorter leash hands contested
slots back sooner.

  live       24 bars
  candidate  20 bars
  diag       16 and 30 bars

Stop, target and size unchanged; a shorter hold refuses nothing and loosens
nothing — money at risk per position is the same, for less time.

Walk-forward on the live-faithful book of section 255 (ranked + pins,
today's vetoes and reservations, every entry guard incl. the cluster cap).
Each arm books and ranks its own trades. Booking checked against the shared
harness at 24 bars before the run.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 260.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, admit,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS, RR,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": 24, "candidate": 20, "diag 16": 16, "diag 30": 30}


def book(O, H, L, C, e, d, entry, stop_d, cost_r, n, hold):
    """The shared harness's booking with the leash as a parameter."""
    sl = entry - d * stop_d; tp = entry + d * RR * stop_d
    for b in range(e + 1, e + hold + 1):
        if b >= n: break
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl): return -1.0 - cost_r, b
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp): return RR - cost_r, b
    if e + hold < n: return (float(C[e + hold]) - entry) * d / stop_d - cost_r, e + hold
    return None, None


def signals(frames, atr_floor, meta, hold):
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
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n, hold)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "dir": x.direction, "strat": s, "r": r, "usd": r * risk_usd})
    out.sort(key=lambda z: z["ts"])
    return out


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    now = np.datetime64(datetime.now(timezone.utc).date())
    rank_w = np.timedelta64(RANK_DAYS, 'D'); step = np.timedelta64(TRADE_DAYS, 'D')

    results = {}; blocks = None
    for arm, hold in ARMS.items():
        sig = signals(frames, atr_floor, meta, hold)
        if blocks is None:
            t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
            blocks = []; cut = np.datetime64(t0, 'D') + rank_w
            while cut + step <= np.datetime64(t1, 'D'):
                blocks.append((cut, cut + step)); cut = cut + step
        daily = {}; taken = []
        for start, end in blocks:
            ranked, _ = pe.lists([s for s in sig if start - rank_w <= s["ts"] < start],
                                 pins, reserved)
            got = admit([s for s in sig if start <= s["ts"] < end], ranked | pins)
            for t in got:
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]
            taken.extend(got)
        results[arm] = (daily, taken)
        print(f"{arm:<10} hold {hold}: signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

    base_daily, _ = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        daily, _ = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days]); b = np.array([daily.get(d, 0.0) for d in days])
        print(f"\n--- {arm} (hold {ARMS[arm]}) vs live 24 ---")
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
        if arm == "candidate":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
                  f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())

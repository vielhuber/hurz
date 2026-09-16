"""Supertrend flips as a fourth signal source on the hourly book.

The live mix trades channel breakouts (donchian, turtle) and an EMA cross
(momentum); sections 224, 236 and 299 moved strategies already in the
repository in and out of that mix. A volatility-trailing trend line was
never offered: the supertrend flips when a close crosses a band set 3 x
ATR(10) off the bar's midpoint, which fires at a different point of a
move than either a channel break or an EMA cross.

  live       donchian, momentum, turtle
  candidate  the same plus supertrend(10, 3) flips, offered to the same
             ranked list of 40 under the trend router, with the live
             stop, 1.5 R target and 24-bar leash
  diag       supertrend(10, 2)

Pins, sizes and every cap are unchanged. Weekly re-ranking, open positions
and cooldowns carried across ranking boundaries (section 284). Run from
the bot's checkout (section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 308.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
from types import SimpleNamespace
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_rotate_worst import admit
from app.strategies import add_indicators, get_strategy
from app.strategies.base import warmup_bars
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book, RANK_DAYS, META_CACHE,
    STRATS,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 3.0, "diag": 2.0}
STEP = np.timedelta64(7, 'D')


def supertrend_flips(df, mult, period=10):
    H = df["high"].values; L = df["low"].values; C = df["close"].values
    tr = np.maximum(H[1:] - L[1:], np.maximum(abs(H[1:] - C[:-1]), abs(L[1:] - C[:-1])))
    atr = np.full(len(C), np.nan); atr[period] = tr[:period].mean()
    for k in range(period + 1, len(C)):
        atr[k] = (atr[k - 1] * (period - 1) + tr[k - 1]) / period
    mid = (H + L) / 2
    up = mid - mult * atr; dn = mid + mult * atr
    trend = 0; out = []
    for k in range(period + 1, len(C)):
        if trend >= 0 and up[k - 1] > up[k]: up[k] = up[k - 1]
        if trend <= 0 and dn[k - 1] < dn[k]: dn[k] = dn[k - 1]
        new = trend
        if trend <= 0 and C[k] > dn[k - 1]: new = 1
        elif trend >= 0 and C[k] < up[k - 1]: new = -1
        if trend != 0 and new != trend and k >= warmup_bars():
            out.append(SimpleNamespace(index=k, direction=new))
        trend = new if new != 0 else trend
    return out


def signals(frames, atr_floor, meta, mult):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        streams = [(s, s, get_strategy(s)(df, {})) for s in STRATS]
        if mult: streams.append(("supertrend", "donchian_breakout", supertrend_flips(df, mult)))
        for s, policy, xs in streams:
            for x in xs:
                if gate(policy, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "dir": x.direction, "strat": s, "r": r,
                            "usd": r * risk_usd, "risk": risk_usd,
                            "entry": entry, "stop_d": stop_d, "cost_r": cost_r,
                            "bars": xb - x.index})
    out.sort(key=lambda z: z["ts"])
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    atr_floor = _min_stop_atr_multiple()
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, mult in ARMS.items():
        sig = signals(frames, atr_floor, meta, mult)
        ts_all = np.array([s["ts"] for s in sig])
        cut = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
        booked = []; state = {"open": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, "live", None, state, booked, [])
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        print(f"{arm:<10} signals {len(sig)}, trades {len(booked)}, "
              f"USD/trade {np.mean([t['usd'] for t in booked]):+.4f}, "
              f"bars held {np.mean([t['bars'] for t in booked]):.1f}, "
              f"supertrend {sum(t['strat'] == 'supertrend' for t in booked)}", flush=True)

    base = results["live"]
    for arm in list(ARMS)[1:]:
        other = results[arm]
        days_ = sorted(set(base) | set(other))
        a = np.array([base.get(d, 0.0) for d in days_]); b = np.array([other.get(d, 0.0) for d in days_])
        print(f"\n--- {arm} ---")
        print(f"{'sample':<14}{'live':>11}{'variant':>11}{'diff':>10}{'t':>8}")
        up = 0
        for lo, hi in YEARS:
            sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                            < (now - np.timedelta64(lo, 'D')) for d in days_])
            span = float(hi - lo); av = a[sel].sum() / span; bv = b[sel].sum() / span
            up += bv > av
            print(f"{lo}-{hi} d{'':<4}{av:>+11.4f}{bv:>+11.4f}{bv-av:>+10.4f}{t_stat(b[sel]-a[sel]):>+8.2f}")
        d_ = b - a
        print(f"{'pooled':<14}{a.sum()/len(days_):>+11.4f}{b.sum()/len(days_):>+11.4f}"
              f"{d_.sum()/len(days_):>+10.4f}{t_stat(d_):>+8.2f}")
        if arm == "candidate":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — {'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())

"""Inside-bar breakouts as a fourth signal source on the hourly book.

The live mix enters on channel breaks (donchian, turtle) and an EMA cross
(momentum); section 308 added a trailing volatility line and lost. The
shortest compression pattern was never offered: a bar that trades wholly
inside the bar before it (the mother bar), followed by a close beyond the
mother bar's high or low within the next three bars. It fires at the end
of a pause rather than at a new extreme.

  live       donchian, momentum, turtle
  candidate  the same plus inside-bar breakouts, offered to the same
             ranked list of 40 under the trend router, with the live
             stop, 1.5 R target and 24-bar leash
  diag       the same, requiring two consecutive inside bars

Pins, sizes and every cap are unchanged. Weekly re-ranking, open positions
and cooldowns carried across ranking boundaries (section 284). Run from
the bot's checkout (section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 309.
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
ARMS = {"live": None, "candidate": 1, "diag": 2}
STEP = np.timedelta64(7, 'D')


def inside_bar_breaks(df, depth, window=3):
    H = df["high"].values; L = df["low"].values; C = df["close"].values
    out = []; k = warmup_bars()
    while k < len(C) - 1:
        m = k - depth
        if all(H[j] <= H[j - 1] and L[j] >= L[j - 1] for j in range(m + 1, k + 1)):
            for b in range(k + 1, min(k + 1 + window, len(C))):
                d = 1 if C[b] > H[m] else -1 if C[b] < L[m] else 0
                if d:
                    out.append(SimpleNamespace(index=b, direction=d)); k = b; break
        k += 1
    return out


def signals(frames, atr_floor, meta, depth):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        streams = [(s, s, get_strategy(s)(df, {})) for s in STRATS]
        if depth: streams.append(("inside_bar", "donchian_breakout", inside_bar_breaks(df, depth)))
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
    for arm, depth in ARMS.items():
        sig = signals(frames, atr_floor, meta, depth)
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
              f"inside bar {sum(t['strat'] == 'inside_bar' for t in booked)}", flush=True)

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

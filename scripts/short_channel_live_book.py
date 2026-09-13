"""A shorter donchian channel, on the book that now trades.

Section 248 found longer channels worse on the current system and gave a
reason: a longer channel fires later into a move, and the ADX ceiling of
section 185 already refuses late, exhausted trends — so the router had
removed most of what a long channel could add. The same reasoning runs
the other way. A shorter channel fires earlier, while ADX is still below
the ceiling, so more of its entries pass the router and they sit earlier
in the move.

The only earlier reading of a shorter channel is run 9 of 2026-09-07:
period 10 at a 1-ATR stop, ten instruments, no ceiling, no floor — -0.029
and +0.002 R on two samples. Nothing about that system survives.

donchian_breakout carries nearly all of the book's dollars (section 256's
decomposition: turtle and momentum together are about zero), so this
touches almost every trade.

  live       donchian 20, turtle 55
  candidate  donchian 15, turtle 55
  diag       donchian 10; donchian 30

Walk-forward on the live-faithful book of section 255: ranked + pins with
today's vetoes and reservations, every entry guard including the cluster
cap. Both arms rank on their own signals.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

A channel change touches no risk limit. Live warm-up: 15 bars need less
history than 20, so the 240-hour fetch is not a constraint.

See docs/EDGE_FINDINGS.md 258.
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
    load_history, to_frame, t_stat, trade_terms, book, admit,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": 20, "candidate": 15, "diag 10": 10, "diag 30": 30}


def signals(frames, atr_floor, meta, donchian_period):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for s in STRATS:
            params = {"period": donchian_period} if s == "donchian_breakout" else {}
            for x in get_strategy(s)(df, params):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
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
    for arm, period in ARMS.items():
        sig = signals(frames, atr_floor, meta, period)
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
        print(f"{arm:<10} donchian {period}: signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

    base_daily, _ = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        daily, taken = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days]); b = np.array([daily.get(d, 0.0) for d in days])
        print(f"\n--- {arm} (donchian {ARMS[arm]}) vs live 20 ---")
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

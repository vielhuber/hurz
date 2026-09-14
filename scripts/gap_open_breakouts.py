"""Refusing breakouts on the first bar after a session gap.

A channel broken by the opening bar of a session — Monday's open, an
index's daily reopen, the end of a holiday — was broken by the gap, not by
a move that built through the channel. Every entry filter so far read the
signal bar's trend, volatility, range or calendar; none asked whether the
bar was the first after the market had been shut. The open is also where
live spreads are widest, which the harness's flat cost does not charge, so
the refusing arms are if anything under-credited.

  live       as today
  candidate  refused if the signal bar opens more than 2 hours after the
             previous bar
  diag       refused on the first two bars after such a gap

Refusal only; the selector never sees a refused signal, as with the other
entry blocks. Crypto never gaps and is unaffected.

Walk-forward on the live-faithful book of section 255.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 269.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.flat_before_weekend import signals, admit
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, TRADE_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": 0, "candidate": 1, "diag": 2}
GAP = np.timedelta64(2, 'h')


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    base = signals(frames, _min_stop_atr_multiple(), meta, None)
    after_gap = {1: set(), 2: set()}
    for pair, df in frames.items():
        ts = df["timestamp"].values
        for i in np.nonzero(np.diff(ts) > GAP)[0] + 1:
            after_gap[1].add((pair, ts[i])); after_gap[2].add((pair, ts[i]))
            if i + 1 < len(ts): after_gap[2].add((pair, ts[i + 1]))
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, k in ARMS.items():
        sig = base if not k else [s for s in base if (s["pair"], s["ts"]) not in after_gap[k]]
        t0 = min(s["ts"] for s in base); t1 = max(s["ts"] for s in base)
        step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
        cut = np.datetime64(t0, 'D') + rank_w; daily = {}; taken = []
        while cut + step <= np.datetime64(t1, 'D'):
            ranked, _ = pe.lists([s for s in sig if cut - rank_w <= s["ts"] < cut], pins, reserved)
            for t in admit([s for s in sig if cut <= s["ts"] < cut + step], ranked | pins):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut += step
        results[arm] = daily
        gapped = [t for t in (taken if arm == "live" else []) if (t["pair"], t["ts"]) in after_gap[1]]
        print(f"{arm:<10} signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}"
              + (f", first-bar-after-gap trades {len(gapped)} (their USD "
                 f"{np.mean([t['usd'] for t in gapped]):+.4f}, t {t_stat(np.array([t['usd'] for t in gapped])):+.2f})"
                 if gapped else ""), flush=True)

    base_d = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        other = results[arm]
        days = sorted(set(base_d) | set(other))
        a = np.array([base_d.get(d, 0.0) for d in days]); b = np.array([other.get(d, 0.0) for d in days])
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

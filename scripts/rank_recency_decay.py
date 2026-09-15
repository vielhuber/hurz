"""Weighting the selector's trailing year toward its recent trades.

Section 279 found fresher lists earn more: re-ranking weekly beats monthly
and quarterly. Section 280 found a shorter window does not add to it — a
180-day memory halves the sample the eligibility thresholds rest on. A
decay keeps the full 365-day sample for the trade count and lets recent
trades count more in the two quality terms of the score.

  live       score eR * log1p(n) * min(5, pf) over 365 days, every trade
             weighted equally, re-ranked weekly
  candidate  eR and pf weighted by 0.5 ** (age / 120 days); n, the
             eligibility cut (n >= 10, pf >= 0.8, eR >= -0.2 — applied to
             the weighted eR and pf) and the top 40 unchanged
  diag       half-life 60 days

Pins, reservations, vetoes, caps and the 6-hour cooldown as live; all arms
over the same days.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 282.
"""
import asyncio, json, math, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.flat_before_weekend import signals
from scripts.stop_out_cooldown_length import admit_cooldown
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import load_history, to_frame, t_stat, RANK_DAYS, META_CACHE

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
STEP = np.timedelta64(7, 'D')
ARMS = {"live": None, "candidate": 120.0, "diag": 60.0}


def lists_decayed(window, pins, reserved, cut, half_life):
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < pe.MIN_N: continue
        r = np.array([t["r"] for t in ts])
        age = np.array([(cut - t["ts"]) / np.timedelta64(1, 'D') for t in ts])
        w = 0.5 ** (age / half_life)
        eR = float((w * r).sum() / w.sum())
        gains = (w * np.clip(r, 0, None)).sum(); losses = -(w * np.clip(r, None, 0)).sum()
        pf = 5.0 if losses <= 0 else float(gains / losses)
        if pf < pe.MIN_PF or eR < pe.MIN_ER: continue
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    ranked = [k for _, k in rows if (k[1] not in reserved or k in pins) and k not in pe.VETOED]
    return set(ranked[:pe.LIVE_N])


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta, None)
    ts_all = np.array([s["ts"] for s in sig])
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    start = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, half_life in ARMS.items():
        cut = start; daily = {}; taken = []; last_stop = {}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            if half_life is None:
                ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            else:
                ranked = lists_decayed(sig[lo:hi], pins, reserved, np.datetime64(cut, 'ns'), half_life)
            for t in admit_cooldown(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, 6, last_stop, []):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut = nxt
        results[arm] = daily
        print(f"{arm:<10} half-life {half_life}: trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

    base = results["live"]
    for arm in ARMS:
        if arm == "live": continue
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

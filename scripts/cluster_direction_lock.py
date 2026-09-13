"""No entry against a cluster's open direction.

The cluster cap counts same-direction positions only, so `risk_on` may
hold three longs and three shorts at the same time. Section 256 showed
that cluster is the book's binding constraint and that its P&L is one
factor bet. When members of one factor break out in opposite directions
at once, the factor is not trending — that is dispersion — and the book
pays two sets of spreads for positions that partly offset each other.

Rule: refuse an entry when its cluster already holds a position in the
opposite nominal direction. Nominal direction is what the cap itself
counts; sections 242–244 showed a signed factor model does not hold on
the live universe, so the rule deliberately does not try to sign
instruments.

  live       cap 3 per (cluster, direction), directions independent
  candidate  as live, plus: refuse if >= 1 open position in the cluster
             points the other way
  diag       refuse only if >= 2 point the other way

Refusal only — no limit is loosened in any state.

Walk-forward on the live-faithful book of section 255 (ranked + pins,
today's vetoes and reservations, every entry guard).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 257.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from app.strategies import add_indicators
from app.spot_trading.autotrade import (
    _min_stop_atr_multiple, _CORRELATION_CLUSTERS, _CLUSTER_DIR_CAP,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, all_signals, admit,
    RANK_DAYS, TRADE_DAYS, META_CACHE, MAX_CONCURRENT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 1, "diag": 2}


def admit_locked(window, active, lock, refused):
    """`admit()` plus a refusal when the cluster already points the other way."""
    open_pos = {}; out = []
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o[0] <= t["ts"]]:
            del open_pos[p_]
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None:
            dirs = [o[1] for p_, o in open_pos.items()
                    if _CORRELATION_CLUSTERS.get(p_) == cluster]
            if sum(1 for d in dirs if d == t["dir"]) >= _CLUSTER_DIR_CAP:
                continue
            if lock is not None and sum(1 for d in dirs if d == -t["dir"]) >= lock:
                refused.append(t)
                continue
        open_pos[t["pair"]] = (t["exit_ts"], t["dir"])
        out.append(t)
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = all_signals(frames, _min_stop_atr_multiple(), meta)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"instruments={len(frames)} signals={len(sig)} blocks={len(blocks)} pins={len(pins)}",
          flush=True)

    results = {}
    for arm, lock in ARMS.items():
        daily = {}; taken = []; refused = []
        for start, end in blocks:
            ranked, _ = pe.lists([s for s in sig if start - rank_w <= s["ts"] < start],
                                 pins, reserved)
            tw = [s for s in sig if start <= s["ts"] < end]
            got = admit_locked(tw, ranked | pins, lock, refused)
            for t in got:
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]
            taken.extend(got)
        results[arm] = (daily, taken, refused)

    base_daily, base_taken, _ = results["live"]
    print(f"live: trades {len(base_taken)}, USD/trade "
          f"{np.mean([t['usd'] for t in base_taken]):+.4f}")
    for arm in ARMS:
        if arm == "live": continue
        daily, taken, refused = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days]); b = np.array([daily.get(d, 0.0) for d in days])
        ru = np.array([t["usd"] for t in refused])
        print(f"\n--- {arm} (lock at {ARMS[arm]} opposite) ---")
        print(f"trades {len(taken)}, refused by the lock {len(refused)} "
              f"(their own USD {ru.mean() if len(ru) else float('nan'):+.4f} at t "
              f"{t_stat(ru) if len(ru) > 1 else float('nan'):+.2f}), USD/trade "
              f"{np.mean([t['usd'] for t in taken]):+.4f}")
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

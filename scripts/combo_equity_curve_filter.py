"""Standing aside while a combination's own recent signals are losing.

Every filter on the book so far read the market at the signal bar (ADX,
volatility, trend alignment, breadth, calendar) or the book's own open
positions (sections 261, 263). The selector reads each combination's
results, but only once a quarter over a trailing year (sections 215, 238),
and the live-expectancy veto reads the live journal over 40 trades. What no
run has measured is the short horizon of a combination's own results: the
classic equity-curve filter, which assumes a trend strategy's edge comes in
spells and a fresh run of losses marks a spell that has ended.

  live       every gated signal of an active combination is admissible
  candidate  refused if the combination's last 3 signals that closed
             before this one sum to a negative R
  diag       the same over the last 5

The record is the combination's shadow record — every gated signal the
harness books, taken or not — so a refusal cannot freeze its own record,
and the live bot can rebuild it from its bars as the selector already does.
Fewer closed signals than the window: whatever there is is summed; none:
admitted. Refusal only; no guard is loosened.

Walk-forward on the live-faithful book of section 255.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 265.
"""
import asyncio, bisect, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_slot_rotation import signals
from app.strategies import add_indicators
from app.spot_trading.autotrade import (
    _min_stop_atr_multiple, _CORRELATION_CLUSTERS, _CLUSTER_DIR_CAP,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, TRADE_DAYS, META_CACHE, MAX_CONCURRENT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 3, "diag": 5}


def mark_recent(sig):
    """Sum of R of each signal's combination's last k closed signals, per arm."""
    closed = {}
    for s in sig:
        closed.setdefault((s["strat"], s["pair"]), []).append((s["exit_ts"], s["r"]))
    for rows in closed.values():
        rows.sort(key=lambda z: z[0])
    exits = {k: [e for e, _ in rows] for k, rows in closed.items()}
    for s in sig:
        key = (s["strat"], s["pair"])
        i = bisect.bisect_right(exits[key], s["ts"])
        s["recent"] = {k: sum(r for _, r in closed[key][max(0, i - k):i])
                       for k in ARMS.values() if k}


def admit(window, active, k, refused):
    open_pos = {}; out = []
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o["exit_ts"] <= t["ts"]]:
            del open_pos[p_]
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None:
            same = [o for p_, o in open_pos.items()
                    if _CORRELATION_CLUSTERS.get(p_) == cluster and o["dir"] == t["dir"]]
            if len(same) >= _CLUSTER_DIR_CAP:
                continue
        if k and t["recent"][k] < 0:
            refused.append(t)
            continue
        open_pos[t["pair"]] = t; out.append(t)
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta)
    mark_recent(sig)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"instruments={len(frames)} signals={len(sig)} blocks={len(blocks)} pins={len(pins)}",
          flush=True)

    results = {}
    for arm, k in ARMS.items():
        daily = {}; taken = []; refused = []
        for start, end in blocks:
            ranked, _ = pe.lists([s for s in sig if start - rank_w <= s["ts"] < start],
                                 pins, reserved)
            got = admit([s for s in sig if start <= s["ts"] < end],
                        ranked | pins, k, refused)
            for t in got:
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]
            taken.extend(got)
        results[arm] = daily
        ru = np.array([t["usd"] for t in refused])
        print(f"{arm:<10} trades {len(taken)}, USD/trade {np.mean([t['usd'] for t in taken]):+.4f}, "
              f"refused by the rule {len(refused)}"
              + (f" (their USD {ru.mean():+.4f}, t {t_stat(ru):+.2f})" if len(ru) > 1 else ""),
              flush=True)

    base_daily = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        daily = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days]); b = np.array([daily.get(d, 0.0) for d in days])
        print(f"\n--- {arm} ---")
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

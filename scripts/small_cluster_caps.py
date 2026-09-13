"""The cluster cap is inert in every cluster smaller than four.

`_CLUSTER_DIR_CAP` allows three same-direction positions per correlation
cluster, and the comment above the map gives the reason: N same-direction
breakouts across co-moving instruments are one concentrated bet disguised
as N independent edges. Section 227 put a number on that — two positions
in one bet are a doubling of risk, not of edge.

The cap only binds where a cluster has more than three tradeable members.
After the cost and expectancy blocks, three clusters do not:

    crypto   BTCUSD, ETHUSD                  cap 3 on 2 members
    energy   OIL_BRENT, OIL_CRUDE            cap 3 on 2 members
    metals   GOLD, SILVER, COPPER            cap 3 on 3 members

A long in Brent beside a long in Crude is one oil position at twice the
size, and nothing refuses it. The guard exists for exactly that and
cannot act on it — only `risk_on`, with fifteen members, ever sees it.

Rule, fixed before the data were seen: a cluster's cap never exceeds its
tradeable member count minus one, so it can bind wherever it is mapped.

  live       3 everywhere
  candidate  crypto 1, energy 1, metals 2, risk_on 3
  diag       crypto 1, energy 1, metals 3, risk_on 3

This can only refuse entries; no limit is loosened in any state. Stated
effect on risk: fewer doubled positions in the three smallest clusters.

Walk-forward with the scheduler's selector on section 252's harness.

Acceptance:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

Risk figures — refusals by cluster, the daily figure's standard deviation
and worst day — are reported alongside, with no standing to qualify.

See docs/EDGE_FINDINGS.md 254.
"""
import asyncio, json, math, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple, _CORRELATION_CLUSTERS
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, all_signals,
    RANK_DAYS, TRADE_DAYS, META_CACHE, MAX_CONCURRENT,
)

MIN_PF = 0.8; MIN_ER = -0.2; LIVE_N = 40
YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {
    "live":      {},
    "candidate": {"crypto": 1, "energy": 1, "metals": 2},
    "diag":      {"crypto": 1, "energy": 1},
}
DEFAULT_CAP = 3
CANDIDATE = "candidate"


def rank_live(window):
    """Top 40 under the scheduler's eligibility filter (section 217)."""
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


def admit(window, active, caps, refused):
    """`admit()` of the shared harness with a cap per cluster."""
    open_pos = {}; out = []
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o[0] <= t["ts"]]:
            del open_pos[p_]
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None:
            same = sum(1 for p_, o in open_pos.items()
                       if _CORRELATION_CLUSTERS.get(p_) == cluster and o[1] == t["dir"])
            if same >= caps.get(cluster, DEFAULT_CAP):
                refused[cluster] = refused.get(cluster, 0) + 1
                continue
        open_pos[t["pair"]] = (t["exit_ts"], t["dir"])
        out.append(t)
    return out


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {}
    for pair, rows in raw.items():
        if pair not in meta or len(rows) < 2000: continue
        frames[pair] = add_indicators(to_frame(rows))
    members = {}
    for p in frames:
        c = _CORRELATION_CLUSTERS.get(p)
        if c: members.setdefault(c, []).append(p)
    print(f"instruments={len(frames)} atr_floor={atr_floor:g}")
    print("tradeable cluster members: " + "; ".join(
        f"{c} {len(ps)} ({', '.join(sorted(ps))})" if len(ps) < 6 else f"{c} {len(ps)}"
        for c, ps in sorted(members.items())), flush=True)

    sig = all_signals(frames, atr_floor, meta)
    print(f"gated, sized, booked signals: {len(sig)}", flush=True)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"out-of-sample blocks: {len(blocks)}", flush=True)

    results = {}
    for arm, caps in ARMS.items():
        daily = {}; taken = []; refused = {}
        for start, end in blocks:
            rw = [s for s in sig if start - rank_w <= s["ts"] < start]
            tw = [s for s in sig if start <= s["ts"] < end]
            got = admit(tw, rank_live(rw), caps, refused)
            for t in got:
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]
            taken.extend(got)
        results[arm] = (daily, taken, refused)

    base_daily, base_taken, base_ref = results["live"]
    for arm in ARMS:
        daily, taken, refused = results[arm]
        vals = np.array(list(daily.values()))
        print(f"\n--- {arm} {ARMS[arm] or '(cap 3 everywhere)'} ---")
        print(f"trades {len(taken)}; USD/trade {np.mean([t['usd'] for t in taken]):+.4f}; "
              f"refused by cluster {dict(sorted(refused.items()))}")
        print(f"daily USD sd {vals.std(ddof=1):.3f}, worst day {vals.min():+.2f}, "
              f"best day {vals.max():+.2f}")
        if arm == "live": continue
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days])
        b = np.array([daily.get(d, 0.0) for d in days])
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
        removed = [t for t in base_taken if id(t) not in {id(x) for x in taken}]
        if removed:
            ru = np.array([t["usd"] for t in removed])
            print(f"trades in the live book the variant does not take: {len(removed)}, "
                  f"mean {ru.mean():+.4f} USD at t {t_stat(ru):+.2f}")
        if arm == CANDIDATE:
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
                  f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())

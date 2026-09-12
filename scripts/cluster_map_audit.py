"""Does the hand-built cluster map satisfy the criterion it was built on?

Section 240 derived the map from correlations and found it stricter than
the curated one — indices, USD crosses and yen crosses are a single
fifteen-instrument family, not three. It earned more on all three
out-of-sample windows but reached only t +0.62, so it was not built.

That framing was wrong, and this run corrects it. The question is not
whether a different map earns more. It is whether the map in production
does what the guard above it claims. `autotrade.py` states the criterion
in its own comment — pairs cluster at median |corr| >= 0.5 on a year of
hourly returns — and the cap exists because "N same-direction breakouts
across them are one concentrated bet disguised as N independent edges".
If two instruments correlate at 0.7 and sit in different clusters, the
cap does not see that bet. Three positions on EURUSD, DE40 and US500 in
the same direction pass every guard while being close to one position of
triple size.

That is a defect in a risk control, not a lever, and section 223 is the
precedent: the duplicate-instrument guard was fixed on the evidence that
it did not do its job, with an effect on the daily figure that was not
measurable and not the point.

The audit runs on TWO disjoint correlation windows. A violation counts
only if it holds on both, so no single period's noise can merge anything.

The repair, if one is warranted, is deliberately one-directional: edges
may only be ADDED, merging clusters that the measurement says co-move.
No pair is ever split out of a cluster it currently sits in — COPPER
stays with the metals even though the measurement makes it a singleton.
A merge can only refuse more entries than today. This cannot loosen a
risk limit in any state of the data, which is why it does not need the
forward-evidence gate that section 211 applies to exposure increases.

See docs/EDGE_FINDINGS.md 241.
"""
import asyncio, json, os, sys
import numpy as np
import pandas as pd
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from scripts.efficiency_weighted_selection import load_history, to_frame, META_CACHE
from app.strategies import add_indicators
from app.spot_trading.autotrade import _CORRELATION_CLUSTERS

WINDOW_A = (2555, 1826)     # oldest
WINDOW_B = (1825, 1096)     # disjoint from A
THRESHOLD = 0.5


def corr_for(frames, lo_days, hi_days):
    today = max(df["timestamp"].max() for df in frames.values())
    lo = today - np.timedelta64(lo_days, 'D'); hi = today - np.timedelta64(hi_days, 'D')
    series = {}
    for pair, df in frames.items():
        m = (df["timestamp"] > lo) & (df["timestamp"] <= hi)
        sub = df.loc[m, ["timestamp", "close"]].copy()
        if len(sub) < 500: continue
        sub["ret"] = sub["close"].pct_change()
        series[pair] = sub.set_index("timestamp")["ret"]
    return pd.DataFrame(series).dropna(how="all").corr().abs()


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    a = corr_for(frames, *WINDOW_A)
    b = corr_for(frames, *WINDOW_B)
    common = [p for p in a.columns if p in b.columns]
    print(f"instruments with both windows: {len(common)}")
    print(f"window A: days {WINDOW_A[0]}-{WINDOW_A[1]}   "
          f"window B: days {WINDOW_B[0]}-{WINDOW_B[1]}")

    viol = []
    for i, x in enumerate(common):
        for y in common[i+1:]:
            ca, cb = float(a.loc[x, y]), float(b.loc[x, y])
            if not (np.isfinite(ca) and np.isfinite(cb)): continue
            if ca < THRESHOLD or cb < THRESHOLD: continue
            gx, gy = _CORRELATION_CLUSTERS.get(x), _CORRELATION_CLUSTERS.get(y)
            if gx is None or gy is None or gx != gy:
                viol.append((x, y, round(ca, 2), round(cb, 2), gx, gy))
    viol.sort(key=lambda z: -min(z[2], z[3]))
    print(f"\npairs correlating >= {THRESHOLD} on BOTH windows but not "
          f"in one cluster: {len(viol)}")
    for x, y, ca, cb, gx, gy in viol:
        print(f"  {x:<10} {y:<10} A={ca:.2f} B={cb:.2f}   "
              f"{str(gx):<12} vs {str(gy)}")

    if not viol:
        print("\nno defect: the production map satisfies its own criterion.")
        return

    # Merge-only repair: union the clusters the violations connect.
    groups = {}
    for p in common:
        groups[p] = _CORRELATION_CLUSTERS.get(p) or f"_single_{p}"
    parent = {g: g for g in set(groups.values())}
    def find(g):
        while parent[g] != g: parent[g] = parent[parent[g]]; g = parent[g]
        return g
    for x, y, *_ in viol:
        gx, gy = find(groups[x]), find(groups[y])
        if gx != gy: parent[gx] = gy
    merged = {}
    for p, g in groups.items():
        merged[p] = find(g)
    out = {}
    names = {}
    for p, g in sorted(merged.items()):
        if sum(1 for q in merged if merged[q] == g) < 2: continue
        if g not in names: names[g] = f"c{len(names)}"
        out[p] = names[g]
    print("\nmerge-only repaired map:")
    byc = {}
    for p, c in out.items(): byc.setdefault(c, []).append(p)
    for c, m in sorted(byc.items()): print(f"  {c}: {sorted(m)}")
    singles = sorted(p for p in common if p not in out)
    print(f"  singletons: {singles}")
    print("\nsanity: every production cluster must survive intact")
    for c, members in sorted(
            {v: [k for k in _CORRELATION_CLUSTERS if _CORRELATION_CLUSTERS[k] == v]
             for v in set(_CORRELATION_CLUSTERS.values())}.items()):
        present = [m for m in members if m in out]
        tgt = {out[m] for m in present}
        print(f"  {c:<12} -> {sorted(tgt) if tgt else 'not in universe'} "
              f"{'OK' if len(tgt) <= 1 else 'SPLIT!'}")
    json.dump(out, open("/tmp/repaired_cluster_map.json", "w"), indent=2)
    print("\nwrote /tmp/repaired_cluster_map.json")
asyncio.run(main())

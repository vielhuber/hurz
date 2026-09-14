"""The inverse of section 261: add to a cluster's direction only while it loses.

Section 261 refused same-direction additions into a cluster while its held
positions were losing and found the refused additions were the better
entries (+0.0822 USD a signal, t +3.39). Because that came from the data a
walk-forward would score, the inverse was first read on the live journal
(section 262): additions to a losing direction -0.052 R per trade over 100
trades, additions to a winning direction -0.244 R over 74, difference
+0.19 R at Welch t +1.47. The preregistered gate — losers-held beats
winners-held in the journal — passed, so the inverse rule comes here.

  live       same-direction additions admitted up to the cap
  candidate  an addition is admitted only if the held same-direction
             positions' mean open R at the signal bar is <= 0
  diag       only if every one of them is <= 0

The first position in a cluster direction is never affected. Refusal only.

Walk-forward on the live-faithful book of section 255.

Acceptance, fixed before the data were seen (section 261's, unchanged):
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 263.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_slot_rotation import signals, price_at
from app.strategies import add_indicators
from app.spot_trading.autotrade import (
    _min_stop_atr_multiple, _CORRELATION_CLUSTERS, _CLUSTER_DIR_CAP,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, TRADE_DAYS, META_CACHE, MAX_CONCURRENT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = ("live", "candidate", "diag")


def admit_losing(window, active, arm, px, refused):
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
            if same and arm != "live":
                opens = []
                for o in same:
                    price = px(o["pair"], t["ts"])
                    if price is None: continue
                    opens.append((price - o["entry"]) * o["dir"] / o["stop_d"])
                if opens:
                    ok = (np.mean(opens) <= 0) if arm == "candidate" else all(v <= 0 for v in opens)
                    if not ok:
                        refused.append(t)
                        continue
        open_pos[t["pair"]] = t; out.append(t)
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    f_ts = {p: df["timestamp"].values for p, df in frames.items()}
    f_c = {p: df["close"].values for p, df in frames.items()}
    px = lambda pair, when: price_at(f_ts, f_c, pair, when)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"instruments={len(frames)} signals={len(sig)} blocks={len(blocks)} pins={len(pins)}",
          flush=True)

    results = {}
    for arm in ARMS:
        daily = {}; taken = []; refused = []
        for start, end in blocks:
            ranked, _ = pe.lists([s for s in sig if start - rank_w <= s["ts"] < start],
                                 pins, reserved)
            got = admit_losing([s for s in sig if start <= s["ts"] < end],
                               ranked | pins, arm, px, refused)
            for t in got:
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]
            taken.extend(got)
        results[arm] = (daily, taken, refused)
        ru = np.array([t["usd"] for t in refused])
        print(f"{arm:<10} trades {len(taken)}, USD/trade {np.mean([t['usd'] for t in taken]):+.4f}, "
              f"refused by the rule {len(refused)}"
              + (f" (their USD {ru.mean():+.4f}, t {t_stat(ru):+.2f})" if len(ru) > 1 else ""),
              flush=True)

    base_daily = results["live"][0]
    for arm in ARMS[1:]:
        daily = results[arm][0]
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

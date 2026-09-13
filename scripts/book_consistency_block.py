"""Section 192's consistency rule, re-read on the book the bot trades.

Section 192 blocked AUDUSD, GBPCAD and GBPUSD: three disjoint training
samples must all read negative before an instrument is flagged, the most
recent year is held out, and the held-out year decides alone at paired
t > 2. It was derived on 24 instruments, in R, on gated signals — before
the live universe of 27 (section 245), the scheduler's strategy mix
(247), the cluster cap (252), and the live list with its pins, vetoes and
reservations (255). Section 244's rule applies: a verdict about which
instruments belong in the book must be re-read on the book.

This run applies the rule unchanged, one level closer to the daily
figure: on the trades the live-faithful walk-forward actually books, in
USD, so the cluster cap and the pins shape what each instrument earns.

  flag   an instrument whose booked USD is negative on ALL THREE
         training samples (days 366-1,095, 1,096-1,825, 1,826-2,555)
  test   the most recent year: the walk-forward with the flagged
         instruments blocked against the live book, paired per day
  build  only at paired t > +2 on the test year, as in section 192

Stated in advance, because it matters: a diagnostic decomposition read
before this run showed each instrument's recent-year and pooled-older
USD. The rule is section 192's and was not chosen from it, but the test
year is not blind. Section 192's own method check therefore also runs:
the rule reversed in time — train on the three newest samples, test on
the oldest, which that diagnostic never showed per sample.

Blocking only refuses entries; no limit is loosened.

See docs/EDGE_FINDINGS.md 256.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, all_signals, admit,
    RANK_DAYS, TRADE_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]


def book(sig, blocks, pins, reserved, blocked=frozenset()):
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    use = [s for s in sig if s["pair"] not in blocked]
    usable_pins = {p for p in pins if p[1] not in blocked}
    daily = {}; taken = []
    for start, end in blocks:
        ranked, _ = pe.lists([s for s in use if start - rank_w <= s["ts"] < start],
                             usable_pins, reserved)
        got = admit([s for s in use if start <= s["ts"] < end], ranked | usable_pins)
        for t in got:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        taken.extend(got)
    return daily, taken


def sample_of(ts, now):
    d = (now - np.datetime64(ts, 'D')) / np.timedelta64(1, 'D')
    for i, (lo, hi) in enumerate(YEARS):
        if lo <= d <= hi: return i
    return None


def run_rule(label, train, test, sig, blocks, pins, reserved, base_daily, base_taken, now):
    print(f"\n===== {label}: train on samples {[YEARS[i] for i in train]}, "
          f"test on {YEARS[test]} =====")
    per = {}
    for t in base_taken:
        i = sample_of(t["ts"], now)
        if i is None: continue
        per.setdefault(t["pair"], [0.0] * 4)[i] += t["usd"]
    flagged = sorted(p for p, v in per.items() if all(v[i] < 0 for i in train))
    for p in flagged:
        print(f"  flagged {p:<9} training USD " + " / ".join(f"{per[p][i]:+.2f}" for i in train))
    print(f"flagged {len(flagged)} of {len(per)} instruments "
          f"(chance count under no persistence: {len(per) * 0.5 ** len(train):.1f})")
    if not flagged:
        return None
    daily, taken = book(sig, blocks, pins, reserved, frozenset(flagged))
    days = sorted(set(base_daily) | set(daily))
    a = np.array([base_daily.get(d, 0.0) for d in days]); b = np.array([daily.get(d, 0.0) for d in days])
    lo, hi = YEARS[test]
    sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                    < (now - np.timedelta64(lo, 'D')) for d in days])
    tv = t_stat(b[sel] - a[sel])
    print(f"test sample: live {a[sel].sum()/(hi-lo):+.4f}  blocked {b[sel].sum()/(hi-lo):+.4f}  "
          f"diff {(b[sel]-a[sel]).sum()/(hi-lo):+.4f} USD/day at t {tv:+.2f}")
    fl_test = [t["usd"] for t in base_taken if t["pair"] in flagged and sample_of(t["ts"], now) == test]
    print(f"flagged instruments' own booked USD on the test sample: {sum(fl_test):+.2f} "
          f"over {len(fl_test)} trades")
    print(f"trades {len(base_taken)} -> {len(taken)}")
    for j, (lo2, hi2) in enumerate(YEARS):
        s2 = np.array([(now - np.timedelta64(hi2, 'D')) <= np.datetime64(d)
                       < (now - np.timedelta64(lo2, 'D')) for d in days])
        tag = "test" if j == test else "train"
        print(f"  {lo2}-{hi2} d ({tag}): {a[s2].sum()/(hi2-lo2):+.4f} -> {b[s2].sum()/(hi2-lo2):+.4f}")
    return flagged, tv


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
    print(f"instruments={len(frames)} signals={len(sig)} blocks={len(blocks)} "
          f"pins={len(pins)}", flush=True)
    base_daily, base_taken = book(sig, blocks, pins, reserved)

    fwd = run_rule("forward (preregistered)", [1, 2, 3], 0, sig, blocks, pins, reserved,
                   base_daily, base_taken, now)
    rev = run_rule("reversed in time (method check)", [0, 1, 2], 3, sig, blocks, pins,
                   reserved, base_daily, base_taken, now)
    if fwd is None:
        print("\nforward rule flags nothing — no block."); return
    flagged, tv = fwd
    print(f"\ndecision clause: forward test-year paired t {tv:+.2f} vs > +2 — "
          f"{'PASS' if tv > 2 else 'FAIL'}; flagged {flagged}")


if __name__ == "__main__":
    asyncio.run(main())

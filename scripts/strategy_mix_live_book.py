"""Run 31's strategy-mix drops, re-read on the book the bot trades.

Run 31 (section 224) dropped each of the scheduler's three strategies in
turn and found none qualifying: removing turtle read +0.0092 USD/day at
t +0.51, removing momentum +0.0059 at t +0.81. That harness had no
cluster cap, no pins, no vetoes and 23 instruments. Sections 245, 247,
252 and 255 have since made the harness the live book, and on it section
256's decomposition shows turtle's own booked trades negative on three
samples of four.

A strategy's booked P&L is not the effect of removing it — section 256
showed freed `risk_on` slots go to substitutes — so the outcome is open.
The acceptance is run 31's, unchanged:

  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

  live       donchian + momentum + turtle, ranked + pins of all three
  candidate  without turtle — its signals, its ranked combos and its pins
  diag       without momentum

A removed strategy loosens nothing; it only refuses entries.

See docs/EDGE_FINDINGS.md 259.
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
ARMS = {"live": None, "candidate": "turtle_breakout", "diag": "momentum"}


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
    for arm, drop in ARMS.items():
        use = [s for s in sig if s["strat"] != drop]
        use_pins = {p for p in pins if p[0] != drop}
        daily = {}; taken = []
        for start, end in blocks:
            ranked, _ = pe.lists([s for s in use if start - rank_w <= s["ts"] < start],
                                 use_pins, reserved)
            got = admit([s for s in use if start <= s["ts"] < end], ranked | use_pins)
            for t in got:
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]
            taken.extend(got)
        results[arm] = (daily, taken)
        by = {}
        for t in taken: by[t["strat"]] = by.get(t["strat"], 0) + 1
        print(f"{arm:<10} drop={drop}: signals {len(use)}, trades {len(taken)} {by}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

    base_daily, _ = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        daily, _ = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days]); b = np.array([daily.get(d, 0.0) for d in days])
        print(f"\n--- {arm}: without {ARMS[arm]} ---")
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

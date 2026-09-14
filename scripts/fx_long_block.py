"""Blocking FX longs at class level, gated on the live journal first.

On the live-faithful book (section 255), FX longs lose dollars on all four
year-samples (-0.025 / -0.063 / -0.025 / -0.004 USD a day) and occupy
`risk_on` slots the indices would fill. Run 39 of 2026-09-08 split direction
by class but reported no FX-long row; section 196 tested direction at
instrument granularity. The class-level FX long side has not been refused.

The rule was read off the book it would be scored on, so the four-sample
clause is not independent. Preregistered gate before the walk-forward
counts: the live journal, which did not generate it, must show FX longs
below the rest of the closed live trades. Journal (read-only, 542 closed
trades): FX longs -0.057 R (n 36), everything else -0.184 R, difference
+0.121 R at Welch t +1.29 — the gate fails, so the decision is fixed at
rejection whatever the walk-forward shows; it is run for the record.

  live       as today
  candidate  FX longs refused (six-letter currency pairs, direction +1)

Acceptance as before: (a) USD per calendar day better on all four
year-samples, (b) pooled paired t > +2 — and the journal gate.

See docs/EDGE_FINDINGS.md 267.
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
CCY = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"}


def is_fx_long(t):
    p = t["pair"]
    return t["dir"] == 1 and len(p) == 6 and p[:3] in CCY and p[3:] in CCY


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    base = signals(frames, _min_stop_atr_multiple(), meta, None)
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm in ("live", "candidate"):
        # the selector never sees a blocked side, as with SHORT_BLOCKED_PAIRS
        sig = base if arm == "live" else [s for s in base if not is_fx_long(s)]
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
        print(f"{arm:<10} trades {len(taken)}, fx longs {sum(map(is_fx_long, taken))}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

    a_d, b_d = results["live"], results["candidate"]
    days = sorted(set(a_d) | set(b_d))
    a = np.array([a_d.get(d, 0.0) for d in days]); b = np.array([b_d.get(d, 0.0) for d in days])
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
    print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
    print(f"clause (b): pooled t {t_stat(d_):+.2f} — {'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())

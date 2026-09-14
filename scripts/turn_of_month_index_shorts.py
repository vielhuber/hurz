"""No index shorts over the turn of the month.

Equity indices earn a disproportionate share of their return from the last
trading day of a month through the first three of the next — the
turn-of-the-month effect, documented across markets and decades and tied
to month-end flows (pension contributions, salary investment, fund
rebalancing). The book's calendar work split by weekday (run 60 of
2026-09-09) and guarded bank holidays; the month boundary was never read.
The rule was chosen from that literature before the book was looked at.

  live       as today
  candidate  index shorts refused from the last trading day of a month
             through the third trading day of the next
  diag       the same over the last two and first four trading days

Trading days are the instrument's own dates with bars. Refusal only; the
selector never sees a refused signal.

Walk-forward on the live-faithful book of section 255.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 273.
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
INDICES = {"DE40", "US500", "US30", "FR40", "UK100", "EU50", "US100", "HK50", "J225"}
ARMS = {"live": None, "candidate": (1, 3), "diag": (2, 4)}


def tom_dates(ts, last_n, first_n):
    days = np.unique(ts.astype('datetime64[D]'))
    days = days[(days.astype('datetime64[W]').astype(int) >= 0)]
    months = days.astype('datetime64[M]')
    out = set()
    for m in np.unique(months):
        in_m = days[months == m]
        out.update(in_m[-last_n:].tolist()); out.update(in_m[:first_n].tolist())
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    base = signals(frames, _min_stop_atr_multiple(), meta, None)
    windows = {arm: {p: tom_dates(frames[p]["timestamp"].values, *w) for p in INDICES if p in frames}
               for arm, w in ARMS.items() if w}
    def refused(s, arm):
        return (s["pair"] in INDICES and s["dir"] == -1
                and np.datetime64(s["ts"], 'D').tolist() in windows[arm][s["pair"]])
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm in ARMS:
        sig = base if arm == "live" else [s for s in base if not refused(s, arm)]
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
        extra = ""
        if arm == "live":
            hit = np.array([t["usd"] for t in taken if refused(t, "candidate")])
            extra = f", index shorts in the window: {len(hit)} at {hit.mean():+.4f} USD (t {t_stat(hit):+.2f})"
        print(f"{arm:<10} signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}{extra}", flush=True)

    base_d = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        other = results[arm]
        days_ = sorted(set(base_d) | set(other))
        a = np.array([base_d.get(d, 0.0) for d in days_]); b = np.array([other.get(d, 0.0) for d in days_])
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

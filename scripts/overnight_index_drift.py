"""An overnight long on the US indices as an additional signal source.

Most of the long-run return of US equity indices accrues between the close
and the next open; the cash session contributes close to nothing. That is
a structural flow effect (overnight inventory risk, retail open buying),
outside the channel family, and neither the eighteen preregistered
structural variants (time-of-day was deliberately excluded there) nor the
opening-range run of 2026-09-07 measured it.

  live       the book as today
  candidate  plus `overnight_long` on US500, US30 and US100: long at the
             close of the 20:00 UTC bar, closed at the close of the next
             12:00 UTC bar (before the cash open in both DST seasons),
             stop, target and sizing as every other trade, 0.02 R of
             financing charged per night held (three over a weekend) —
             the harness charges no financing anywhere else, so this arm
             is penalised against the rest of the book
  diag       the same without the financing charge

The new combinations go through the selector's ranking and eligibility
like any other and compete for the `risk_on` long cap; no guard is
loosened.

Walk-forward on the live-faithful book of section 255.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 274.
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
    load_history, to_frame, t_stat, trade_terms, RANK_DAYS, TRADE_DAYS, META_CACHE, RR, HOLD,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
US = ("US500", "US30", "US100")
ENTRY_HOUR, EXIT_HOUR, FIN_R = 20, 12, 0.02


def overnight(frames, atr_floor, meta, financing):
    out = []
    for pair in US:
        if pair not in frames: continue
        df = frames[pair]; n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values; L = df["low"].values; C = df["close"].values
        hours = (ts.astype('datetime64[h]').astype(int) % 24)
        for e in np.nonzero(hours == ENTRY_HOUR)[0]:
            terms = trade_terms(df, e, pair, meta, atr_floor)
            if terms is None: continue
            entry, stop_d, cost_r, risk_usd = terms
            sl = entry - stop_d; tp = entry + RR * stop_d
            r = xb = None
            for b in range(e + 1, min(e + HOLD, n - 1) + 1):
                if O[b] - entry <= -stop_d: r, xb = (O[b] - entry) / stop_d - cost_r, b; break
                if L[b] <= sl: r, xb = -1.0 - cost_r, b; break
                if H[b] >= tp: r, xb = RR - cost_r, b; break
                if hours[b] == EXIT_HOUR or b == e + HOLD:
                    r, xb = (C[b] - entry) / stop_d - cost_r, b; break
            if r is None: continue
            if financing:
                nights = int((ts[xb] - ts[e]) / np.timedelta64(1, 'D')) + 1
                r -= FIN_R * nights
            out.append({"ts": ts[e], "exit_ts": ts[xb], "pair": pair, "dir": 1,
                        "strat": "overnight_long", "r": r, "usd": r * risk_usd, "forced": False})
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    atr_floor = _min_stop_atr_multiple()
    base = signals(frames, atr_floor, meta, None)
    arms = {"live": base,
            "candidate": sorted(base + overnight(frames, atr_floor, meta, True), key=lambda z: z["ts"]),
            "diag": sorted(base + overnight(frames, atr_floor, meta, False), key=lambda z: z["ts"])}
    ov = [s for s in arms["candidate"] if s["strat"] == "overnight_long"]
    print(f"overnight signals {len(ov)}, mean R {np.mean([s['r'] for s in ov]):+.4f} "
          f"(t {t_stat(np.array([s['r'] for s in ov])):+.2f}), per index: "
          + ", ".join(f"{p} {np.mean([s['r'] for s in ov if s['pair'] == p]):+.4f}" for p in US), flush=True)
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, sig in arms.items():
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
        taken_ov = [t["usd"] for t in taken if t["strat"] == "overnight_long"]
        print(f"{arm:<10} trades {len(taken)}, overnight trades {len(taken_ov)}"
              + (f" at {np.mean(taken_ov):+.4f} USD" if taken_ov else "")
              + f", USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

    base_d = results["live"]
    for arm in ("candidate", "diag"):
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

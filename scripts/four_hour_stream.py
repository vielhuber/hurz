"""The same breakout strategies on 4h bars, beside the hourly book.

Section 298 offered daily combinations to the selector and found them
blocked by the eligibility rule: three signals per combination a year
against the ten trades the 365-day ranking window demands. The 4h bar
sits between the two — roughly a sixth of the 1h signal count, enough to
clear the rule — and it has never been booked as a second stream. Run 24
measured 4h *variants of the strategies* on the old harness and the 4h
pins were read in section 240; neither ranked 1h and 4h combinations
together on the live book.

  4h bars     1h bars resampled to 4h, indicators recomputed
  stop        3 x ATR(14) of the 4h bar, the floor `HURZ_MIN_STOP_ATR
              _MULTIPLE` demands, or the venue minimum if that is wider
  target      1.5 R as live; leash 24 4h bars (four days)
  financing   charged per night held in EVERY arm, from the audit of
              section 154: crypto longs 0.050 R a night, metals longs
              0.013, other longs 0.005, shorts 0.003 and crypto or metals
              shorts nothing (they are credited live)

  live        the 1h book, financing charged
  candidate   1h and 4h combinations ranked together, same list of 40,
              same caps, one position per instrument across both
  diag        the 4h book alone

Sizes, caps, cooldown, pins and vetoes are the live ones; the 4h
combinations are new entries in the same list, not extra slots. Weekly
re-ranking, carried positions and cooldowns (section 284). Run from the
bot's checkout (section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 302.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_slot_rotation import signals as hourly_signals
from scripts.cluster_rotate_worst import admit
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple, _CORRELATION_CLUSTERS
from scripts.spot_backtest import _fee_for
from app.spot_trading.position_sizing import (
    calculate_position_size, DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE, STRATS, RR, HOLD, PLAT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = ("live", "candidate", "diag")
STEP = np.timedelta64(7, 'D')
NIGHT = {("crypto", 1): 0.050, ("metals", 1): 0.013, ("crypto", -1): 0.0,
         ("metals", -1): 0.0}


def nights(t):
    """Financing in R for one booked trade."""
    held = (t["exit_ts"] - t["ts"]) / np.timedelta64(1, 'D')
    cluster = _CORRELATION_CLUSTERS.get(t["pair"])
    rate = NIGHT.get((cluster, t["dir"]), 0.005 if t["dir"] > 0 else 0.003)
    return float(held) * rate


def daily_frames(frames):
    out = {}
    for pair, df in frames.items():
        d = pd.DataFrame({"timestamp": df["timestamp"].values, "open": df["open"].values,
                          "high": df["high"].values, "low": df["low"].values,
                          "close": df["close"].values, "volume": df["volume"].values
                          if "volume" in df else 0.0})
        d = d.set_index(pd.DatetimeIndex(d["timestamp"])).resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        if len(d) < 2000: continue
        d = d.reset_index().rename(columns={"index": "timestamp"})
        out[pair] = add_indicators(d)
    return out


def daily_terms(df, e, pair, meta, floor):
    A = df["atr_14"].values; C = df["close"].values
    atr = A[e]
    if not np.isfinite(atr) or atr <= 0: return None
    entry = float(C[e]); stop_d = max(floor * atr, 0.0105 * entry)
    fee = _fee_for(PLAT, pair)
    cost_r = 2.0 * fee * entry / stop_d
    if cost_r > 0.10: return None
    m = meta.get(pair)
    if m is None: return None
    rate = m["rate"]
    sized = calculate_position_size(
        entry_price=entry, stop_loss=entry - stop_d,
        target_risk=DEFAULT_TARGET_RISK_USD / rate,
        notional_cap=DEFAULT_NOTIONAL_CAP_USD / rate,
        size_increment=m["step"], min_size=m["min"], max_size=m["max"])
    if sized.size is None: return None
    return entry, stop_d, cost_r, sized.planned_risk * rate


def book(O, H, L, C, e, d, entry, stop_d, cost_r, n):
    sl = entry - d * stop_d; tp = entry + d * RR * stop_d
    for b in range(e + 1, e + HOLD + 1):
        if b >= n: break
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl): return -1.0 - cost_r, b
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp): return RR - cost_r, b
    if e + HOLD < n: return (float(C[e + HOLD]) - entry) * d / stop_d - cost_r, e + HOLD
    return None, None


def daily_signals(frames, meta, floor):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for s in STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = daily_terms(df, x.index, pair, meta, floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "dir": x.direction, "strat": s + "_4h", "r": r,
                            "usd": r * risk_usd, "risk": risk_usd,
                            "entry": entry, "stop_d": stop_d, "cost_r": cost_r})
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    floor = _min_stop_atr_multiple()
    hourly = hourly_signals(frames, floor, meta)
    dframes = daily_frames(frames)
    daily = daily_signals(dframes, meta, floor)
    for t in hourly + daily:
        fin = nights(t)
        t["r"] -= fin; t["usd"] = t["r"] * t["risk"]
    print(f"hourly signals {len(hourly)}, 4h signals {len(daily)} on {len(dframes)} instruments, "
          f"financing charged in every arm", flush=True)
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm in ARMS:
        sig = hourly if arm == "live" else (daily if arm == "diag" else hourly + daily)
        sig = sorted(sig, key=lambda z: z["ts"])
        ts_all = np.array([s["ts"] for s in sig])
        cut = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
        booked = []; state = {"open": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, "live", None, state, booked, [])
            cut = nxt
        daily_usd = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily_usd[d] = daily_usd.get(d, 0.0) + t["usd"]
        results[arm] = daily_usd
        share = sum(1 for t in booked if t["strat"].endswith("_4h"))
        print(f"{arm:<10} trades {len(booked)} ({share} on 4h), "
              f"USD/trade {np.mean([t['usd'] for t in booked]):+.4f}", flush=True)

    base = results["live"]
    for arm in ARMS[1:]:
        other = results[arm]
        days_ = sorted(set(base) | set(other))
        a = np.array([base.get(d, 0.0) for d in days_]); b = np.array([other.get(d, 0.0) for d in days_])
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

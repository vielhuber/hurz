"""Closing a position at the last bar before the market's weekend close.

The leash is 24 bars, and on an instrument that closes for the weekend a
Friday-afternoon entry is carried through the closure into Monday. Run 5
of 2026-09-08 measured Friday *entries*, and the journal read of carried
stale exits found them harmless; the exit side — flattening what is open
when the market is about to close — was never put through the
walk-forward.

  live       stop, target or the 24th bar, as today
  candidate  additionally closed at the close of the last bar before a
             data gap longer than 36 hours (the weekend or a long
             holiday; crypto trades through and is never affected)
  diag       closed before any gap longer than 2 hours (every daily
             session break of the indices and commodities as well)

A forced close pays no extra cost: the round-trip cost is already charged
once per trade. Overnight financing is not charged by the harness at all,
so the arms that hold fewer nights are, if anything, under-credited.
Stop, target, size and every guard unchanged.

Walk-forward on the live-faithful book of section 255; the selector ranks
on each arm's own booking, as it would live.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 266.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import (
    _min_stop_atr_multiple, _CORRELATION_CLUSTERS, _CLUSTER_DIR_CAP,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS, MAX_CONCURRENT, RR, HOLD,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": np.timedelta64(36, 'h'), "diag": np.timedelta64(2, 'h')}


def book_flat(O, H, L, C, ts, e, d, entry, stop_d, cost_r, n, max_gap):
    """The harness's `book`, closing at a bar's close when the next bar is more than `max_gap` away."""
    sl = entry - d * stop_d; tp = entry + d * RR * stop_d
    for b in range(e + 1, e + HOLD + 1):
        if b >= n: break
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b, False
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl): return -1.0 - cost_r, b, False
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp): return RR - cost_r, b, False
        if max_gap is not None and b < e + HOLD and b + 1 < n and ts[b + 1] - ts[b] > max_gap:
            return (float(C[b]) - entry) * d / stop_d - cost_r, b, True
    if e + HOLD < n: return (float(C[e + HOLD]) - entry) * d / stop_d - cost_r, e + HOLD, False
    return None, None, False


def signals(frames, atr_floor, meta, max_gap):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for s in STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb, forced = book_flat(O, H, L, C, ts, x.index, x.direction,
                                          entry, stop_d, cost_r, n, max_gap)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "dir": x.direction, "strat": s, "r": r,
                            "usd": r * risk_usd, "forced": forced})
    out.sort(key=lambda z: z["ts"])
    return out


def admit(window, active):
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
        open_pos[t["pair"]] = t; out.append(t)
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, max_gap in ARMS.items():
        sig = signals(frames, _min_stop_atr_multiple(), meta, max_gap)
        t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
        step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
        blocks = []; cut = np.datetime64(t0, 'D') + rank_w
        while cut + step <= np.datetime64(t1, 'D'):
            blocks.append((cut, cut + step)); cut = cut + step
        daily = {}; taken = []
        for start, end in blocks:
            ranked, _ = pe.lists([s for s in sig if start - rank_w <= s["ts"] < start],
                                 pins, reserved)
            got = admit([s for s in sig if start <= s["ts"] < end], ranked | pins)
            for t in got:
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]
            taken.extend(got)
        results[arm] = daily
        forced = [t for t in taken if t["forced"]]
        print(f"{arm:<10} signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}, forced closes {len(forced)}"
              + (f" (their R {np.mean([t['r'] for t in forced]):+.3f})" if forced else ""),
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

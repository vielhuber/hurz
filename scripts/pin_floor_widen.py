"""The 3-ATR pin floor as a stop widening instead of a refusal.

Section 190 built the floor in: a signal whose stop sits closer than
3×ATR is refused, 29-38 % of router-passed signals, the largest cut in
trade count this project has made. Section 230 then read what the floor
actually separates — in dollars the two groups are indistinguishable
(-0.0367 against -0.1300 USD a trade, t +0.26); what differs is
dispersion, sd 3.67 against 1.73. A tighter stop buys a larger position
for the same 3 USD, so the same move arrives magnified. The floor is a
variance filter, not an earnings filter.

That is the whole hypothesis here. If the refused band carries the book's
dollar expectancy and only its variance is wrong, then the defect is the
position size, not the signal — and the way to fix a position that is too
large for its stop is to widen the stop and size down, exactly the move
that took the stop from 1 to 2 ATR in section 60. The floor throws the
signal away instead.

  live       stop at max(2×ATR, 1.05 % of price); below 3×ATR refused
  candidate  the same, but a stop below 3×ATR is set to 3×ATR
  diag       the same, set to 3.5×ATR

Widening only ever moves the stop further from the entry, so the cost
share per unit of risk falls with it and the position shrinks to hold the
3 USD target risk. The target follows the stop at 1.5 R, which is the
honest counter-hypothesis: in price the target is further away, so fewer
readmitted trades reach it and more end at the leash.

No risk control is loosened — risk per trade is unchanged, no cap moves
and no stop is ever tightened. What does rise is occupancy: readmitted
signals compete for the eight slots, so throughput is reported alongside.

Weekly re-ranking, open positions and cooldowns carried across ranking
boundaries (section 284). Run from the bot's checkout (section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 323.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_rotate_worst import admit
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.position_sizing import (
    calculate_position_size, DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD,
)
from scripts.spot_backtest import _fee_for
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, book, RANK_DAYS, META_CACHE,
    STRATS, RR, HOLD, STOP_ATR, PLAT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 3.0, "diag": 3.5}
STEP = np.timedelta64(7, 'D')


def trade_terms(df, e, pair, meta, atr_floor, widen_to):
    """`efficiency_weighted_selection.trade_terms` with the floor as a widening.

    `widen_to` None keeps the live refusal; a value sets the stop of a
    sub-floor signal to that ATR multiple, which is always further from
    the entry than the stop the signal would otherwise have carried."""
    A = df["atr_14"].values; C = df["close"].values
    atr = A[e]
    if not np.isfinite(atr) or atr <= 0: return None
    entry = float(C[e]); stop_d = STOP_ATR * atr
    vm = 0.0105 * entry
    if stop_d < vm: stop_d = vm
    widened = False
    if atr_floor > 0 and stop_d / atr < atr_floor:
        if widen_to is None: return None
        stop_d = widen_to * atr; widened = True
    fee = _fee_for(PLAT, pair)
    cost_r = 2.0 * fee * entry / stop_d
    if cost_r > 0.10:
        stop_d *= min(cost_r / 0.10, 2.0); cost_r = 2.0 * fee * entry / stop_d
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
    return entry, stop_d, cost_r, sized.planned_risk * rate, widened


def signals(frames, atr_floor, meta, widen_to):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for s in STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor, widen_to)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd, widened = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "dir": x.direction, "strat": s, "r": r,
                            "usd": r * risk_usd, "risk": risk_usd,
                            "entry": entry, "stop_d": stop_d, "cost_r": cost_r,
                            "widened": widened, "bars": xb - x.index})
    out.sort(key=lambda z: z["ts"])
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    atr_floor = _min_stop_atr_multiple()
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, widen_to in ARMS.items():
        sig = signals(frames, atr_floor, meta, widen_to)
        ts_all = np.array([s["ts"] for s in sig])
        cut = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
        booked = []; state = {"open": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, "live", None, state, booked, [])
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        wid = [t for t in booked if t["widened"]]
        extra = (f", readmitted {len(wid)} at {np.mean([t['usd'] for t in wid]):+.4f} USD "
                 f"(t {t_stat(np.array([t['usd'] for t in wid])):+.2f})") if wid else ""
        print(f"{arm:<10} signals {len(sig)}, trades {len(booked)}, "
              f"USD/trade {np.mean([t['usd'] for t in booked]):+.4f}, "
              f"bars held {np.mean([t['bars'] for t in booked]):.1f}{extra}", flush=True)

    base = results["live"]
    for arm in list(ARMS)[1:]:
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
        print(f"daily sd   live {a.std():.3f}   variant {b.std():.3f}   "
              f"worst day {a.min():+.2f} / {b.min():+.2f}")
        if arm == "candidate":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — {'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())

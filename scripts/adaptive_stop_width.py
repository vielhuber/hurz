"""Trading the volatile signals with a wider stop instead of refusing them.

Two facts from this session sit next to each other and have never been
put together.

Section 230: the 3-ATR floor refuses 60 % of live signals. The refused
ones stop out at 45 % against 15 % for the rest, but in dollars the two
groups are indistinguishable (t +0.26). What separates them is
dispersion — 3.67 against 1.73 — because a stop that is tight relative
to volatility buys a larger position for the same 3 USD.

Section 234's table: the live stop is `max(2 x ATR, 1.05 % of price)`
and the median booked stop is 5.71 ATR. The ATR term never binds. Every
stop in the book is the venue minimum, which is a fixed fraction of
price and knows nothing about volatility. The floor then deletes each
signal where that fixed fraction happens to be narrow in ATR terms.

So the system does not size its stop to volatility and then trade. It
sizes the stop to price, and throws away every signal where that stop is
too tight for the conditions. The floor is a refusal standing in for an
adjustment.

The candidate makes the adjustment instead. At `stop = max(4 x ATR,
venue minimum)` the ATR term binds wherever volatility is high, the
3-ATR floor is satisfied by construction and refuses nothing, and the
signals section 230 measured as merely more dispersed get traded at a
stop that fits them. Risk per trade stays 3 USD, the notional cap stays
250, the concurrent cap stays 8, one position per instrument stays.
What changes is that a volatile signal gets a wider stop and a smaller
position rather than getting deleted.

3.0 and 5.0 run as diagnostics with no standing to qualify.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than live on ALL FOUR year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) the gain is not concentration in disguise: mean occupancy may not
      exceed the live book's by more than 25 %, and the gain must
      survive normalising per unit of occupancy.

Clause (c) is the lesson of section 227 applied in advance — a variant
that earns more by holding more open risk at once is scaling, and this
project does not open exposure on backtest evidence.

See EDGE_FINDINGS 235.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, book, RANK_DAYS, TRADE_DAYS, META_CACHE,
    PLAT, _fee_for,
)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.position_sizing import (
    calculate_position_size, DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD,
)

HOUR_STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40; MIN_RANK_TRADES = 10
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]
LIVE = "live"; CANDIDATE = 4.0
VARIANTS = [LIVE, 3.0, 4.0, 5.0]


def terms_at(df, e, pair, meta, atr_floor, variant):
    A = df["atr_14"].values; C = df["close"].values
    atr = A[e]
    if not np.isfinite(atr) or atr <= 0: return None
    entry = float(C[e])
    stop_atr = 2.0 if variant == LIVE else float(variant)
    stop_d = stop_atr*atr
    vm = 0.0105*entry
    if stop_d < vm: stop_d = vm
    # The floor is applied unchanged; at the wider multiples it is
    # satisfied by construction and refuses nothing.
    if atr_floor > 0 and stop_d/atr < atr_floor: return None
    fee = _fee_for(PLAT, pair)
    cost_r = 2.0*fee*entry/stop_d
    if cost_r > 0.10:
        stop_d *= min(cost_r/0.10, 2.0); cost_r = 2.0*fee*entry/stop_d
        if cost_r > 0.10: return None
    m = meta.get(pair)
    if m is None: return None
    rate = m["rate"]
    sized = calculate_position_size(
        entry_price=entry, stop_loss=entry-stop_d,
        target_risk=DEFAULT_TARGET_RISK_USD/rate,
        notional_cap=DEFAULT_NOTIONAL_CAP_USD/rate,
        size_increment=m["step"], min_size=m["min"], max_size=m["max"])
    if sized.size is None: return None
    return entry, stop_d, cost_r, sized.planned_risk*rate


def signals_for(frames, atr_floor, meta, variant):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for strat in HOUR_STRATS:
            for x in get_strategy(strat)(df, {}):
                if gate(strat, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                t = terms_at(df, x.index, pair, meta, atr_floor, variant)
                if t is None: continue
                entry, stop_d, cost_r, risk_usd = t
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "strat": strat, "r": r, "usd": r*risk_usd})
    out.sort(key=lambda z: z["ts"])
    return out


def ranked(window):
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < MIN_RANK_TRADES: continue
        r = np.array([t["r"] for t in ts]); eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains/losses)
        if pf < MIN_PF or eR < MIN_ER: continue
        rows.append((eR*math.log1p(len(ts))*min(5.0, pf), key))
    rows.sort(reverse=True)
    return {k for _, k in rows[:TOP_N]}


def replay(window, active):
    open_until = {}; per_day = {}; n = 0; pos_hours = 0.0
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, u in open_until.items() if u <= t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until: continue
        if len(open_until) >= base.MAX_CONCURRENT: continue
        open_until[t["pair"]] = t["exit_ts"]
        pos_hours += (t["exit_ts"]-t["ts"])/np.timedelta64(1, 'h')
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0)+t["usd"]
        n += 1
    return per_day, n, pos_hours


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    sigs = {}
    for v in VARIANTS:
        sigs[v] = signals_for(frames, atr_floor, meta, v)
        print(f"variant {v}: signals={len(sigs[v])}", flush=True)
    t0 = min(s["ts"] for s in sigs[LIVE]); t1 = max(s["ts"] for s in sigs[LIVE])
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step

    daily = {v: {} for v in VARIANTS}; counts = {v: 0 for v in VARIANTS}
    hours = {v: 0.0 for v in VARIANTS}
    for start, end in blocks:
        for v in VARIANTS:
            sig = sigs[v]
            rw = [s for s in sig if start-rank_w <= s["ts"] < start]
            tw = [s for s in sig if start <= s["ts"] < end]
            per_day, n, ph = replay(tw, ranked(rw))
            for k, val in per_day.items(): daily[v][k] = daily[v].get(k, 0.0)+val
            counts[v] += n; hours[v] += ph

    all_days = sorted(set().union(*[set(daily[v]) for v in VARIANTS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {v: np.array([daily[v].get(d, 0.0) for d in all_days]) for v in VARIANTS}
    span_h = float(len(all_days))*24.0

    print("\n{:<10}{:>10}{:>10}{:>12}{:>12}{:>12}".format(
        "variant", "trades", "vs live", "occupancy", "USD/day", "per occ"))
    for v in VARIANTS:
        occ = hours[v]/span_h; usd = series[v].sum()/len(all_days)
        print(f"{str(v):<10}{counts[v]:>10}{counts[v]/max(1,counts[LIVE])-1:>+10.1%}"
              f"{occ:>12.2f}{usd:>+12.4f}{usd/max(occ,1e-9):>+12.4f}")

    print("\n{:<14}".format("sample") + "".join(f"{str(v):>11}" for v in VARIANTS) + f"{'cand − live':>13}")
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        vals = {v: series[v][m].sum()/span for v in VARIANTS}
        better.append(vals[CANDIDATE] > vals[LIVE])
        print(f"{f'{dto}-{dfrom} d':<14}" + "".join(f"{vals[v]:>+11.4f}" for v in VARIANTS)
              + f"{vals[CANDIDATE]-vals[LIVE]:>+13.4f}")

    diff = series[CANDIDATE]-series[LIVE]
    t = t_stat(diff)
    occ_l = hours[LIVE]/span_h; occ_c = hours[CANDIDATE]/span_h
    usd_l = series[LIVE].sum()/len(all_days); usd_c = series[CANDIDATE].sum()/len(all_days)
    print(f"\npooled {CANDIDATE} − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = bool(better) and all(better); ok_b = t > 2
    ok_c = (occ_c <= occ_l*1.25) and (usd_c/max(occ_c,1e-9) > usd_l/max(occ_l,1e-9))
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) not concentration: {'YES' if ok_c else 'NO'} "
          f"(occupancy {occ_c:.2f} vs {occ_l:.2f}, per-occ {usd_c/max(occ_c,1e-9):+.4f} vs {usd_l/max(occ_l,1e-9):+.4f})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())

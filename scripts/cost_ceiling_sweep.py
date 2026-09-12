"""Where the cost ceiling should sit, priced in dollars per day.

A signal is refused when the round trip costs more than 10 % of its
planned risk, after one attempt to widen the stop into compliance. That
10 % came from section 3, read on the live journal, and sections 11 and
24 later showed the relationship it rested on was an artefact of a few
expensive instruments — which is why the ceiling was kept as a backstop
rather than as a tuned value. It has never been swept in the harness
this series uses, and never in USD per calendar day.

Section 221 gives a reason to look again, and a direction. It priced the
whole cost axis by booking every trade twice: gross +0.0391 R, net
+0.0276 R, costs taking 30 % of the edge. A ceiling is the one instrument
that acts on that 30 % directly — it refuses the trades where the cost
share is worst. If the cost term is a flat tax the ceiling can only cost
throughput; if it is concentrated, a tighter ceiling keeps more of the
edge than it gives up in trades.

Candidate fixed beforehand at 5 %, the tighter side, because that is the
direction section 221's decomposition points. 15 % and 20 % run as
diagnostics with no standing to qualify.

A tighter ceiling removes entries and lifts no limit. A looser one would
admit instruments the cost blocklist of section 24 exists to keep out,
which is why the loose cells are diagnostics only and would need that
blocklist re-read before anything could be built from them.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live 10 % on ALL FOUR
      year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) throughput does not fall by more than 10 % — otherwise it is
      section 214 again, a variant that merely trades less.

See EDGE_FINDINGS 234.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, book, RANK_DAYS, TRADE_DAYS, META_CACHE,
    STOP_ATR, PLAT, _fee_for,
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
CEILINGS = [0.05, 0.10, 0.15, 0.20]
LIVE = 0.10; CANDIDATE = 0.05


def terms_at(df, e, pair, meta, atr_floor, ceiling):
    """trade_terms with the cost ceiling as a parameter."""
    A = df["atr_14"].values; C = df["close"].values
    atr = A[e]
    if not np.isfinite(atr) or atr <= 0: return None
    entry = float(C[e]); stop_d = STOP_ATR*atr
    vm = 0.0105*entry
    if stop_d < vm: stop_d = vm
    if atr_floor > 0 and stop_d/atr < atr_floor: return None
    fee = _fee_for(PLAT, pair)
    cost_r = 2.0*fee*entry/stop_d
    if cost_r > ceiling:
        stop_d *= min(cost_r/ceiling, 2.0); cost_r = 2.0*fee*entry/stop_d
        if cost_r > ceiling: return None
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


def signals_for(frames, atr_floor, meta, ceiling):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for strat in HOUR_STRATS:
            for x in get_strategy(strat)(df, {}):
                if gate(strat, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                t = terms_at(df, x.index, pair, meta, atr_floor, ceiling)
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
    open_until = {}; per_day = {}; n = 0
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, u in open_until.items() if u <= t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until: continue
        if len(open_until) >= base.MAX_CONCURRENT: continue
        open_until[t["pair"]] = t["exit_ts"]
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0)+t["usd"]
        n += 1
    return per_day, n


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    daily = {c: {} for c in CEILINGS}; counts = {c: 0 for c in CEILINGS}
    sigs = {}
    for c in CEILINGS:
        sigs[c] = signals_for(frames, atr_floor, meta, c)
        print(f"ceiling {c:.2f}: signals={len(sigs[c])}", flush=True)
    t0 = min(s["ts"] for s in sigs[LIVE]); t1 = max(s["ts"] for s in sigs[LIVE])
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step
    for start, end in blocks:
        for c in CEILINGS:
            sig = sigs[c]
            rw = [s for s in sig if start-rank_w <= s["ts"] < start]
            tw = [s for s in sig if start <= s["ts"] < end]
            per_day, n = replay(tw, ranked(rw))
            for k, v in per_day.items(): daily[c][k] = daily[c].get(k, 0.0)+v
            counts[c] += n

    all_days = sorted(set().union(*[set(daily[c]) for c in CEILINGS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {c: np.array([daily[c].get(d, 0.0) for d in all_days]) for c in CEILINGS}

    print("\n{:<10}{:>10}{:>10}{:>12}".format("ceiling", "trades", "vs live", "USD/day"))
    for c in CEILINGS:
        print(f"{c:<10.2f}{counts[c]:>10}{counts[c]/max(1,counts[LIVE])-1:>+10.1%}"
              f"{series[c].sum()/len(all_days):>+12.4f}")

    print("\n{:<14}".format("sample") + "".join(f"{c:>11.2f}" for c in CEILINGS) + f"{'cand − live':>13}")
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        vals = {c: series[c][m].sum()/span for c in CEILINGS}
        better.append(vals[CANDIDATE] > vals[LIVE])
        print(f"{f'{dto}-{dfrom} d':<14}" + "".join(f"{vals[c]:>+11.4f}" for c in CEILINGS)
              + f"{vals[CANDIDATE]-vals[LIVE]:>+13.4f}")

    diff = series[CANDIDATE]-series[LIVE]
    t = t_stat(diff); thr = counts[CANDIDATE]/max(1, counts[LIVE])-1
    print(f"\npooled {CANDIDATE:.2f} − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = bool(better) and all(better); ok_b = t > 2; ok_c = thr > -0.10
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) throughput within 10 %: {'YES' if ok_c else 'NO'} ({thr:+.1%})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())

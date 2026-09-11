"""What the 4h pins cost the 1h book.

Every measurement in this series runs at 1h, but the live list carries six
4h pins — donchian_breakout_4h on SILVER and NZDUSD, turtle_breakout_4h on
HK50 and WHEAT, momentum_4h on COPPER and CHFJPY. They have never been in
the harness, so their contribution has never been priced. Live they read
about -10 USD over 24 closed trades, which is too thin to judge on its
own.

There is a mechanism that makes them worth measuring beyond their own
result. A 4h position holds its leash for 24 bars — four days — and the
one-position-per-pair rule means that for those four days the 1h book
cannot enter that instrument at all. So a 4h pin does not merely add its
own trades; it removes 1h trades on the same name. Section 223 fixed the
case where both opened in the same second; this is the slower version of
the same interference.

4h bars are aggregated from the cached 1h bars, so this costs no API
calls. Both variants run the live configuration; the only difference is
whether the 4h combos may enter.

Acceptance, fixed before the data were seen:

  (a) the book WITHOUT the 4h pins earns more USD per calendar day on ALL
      FOUR year-samples,
  (b) the paired daily difference reaches t > 2 on at least one.

If both hold, what ships is the removal of those six entries from
`data/pinned_pairs.json` — no risk limit moves, and the 1h book gains the
instrument-days the 4h positions were occupying. If they do not, the pins
stay. See docs/EDGE_FINDINGS.md 225.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book,
    MIN_RANK_TRADES, RANK_DAYS, TRADE_DAYS, META_CACHE,
)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple

HOUR_STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
PIN_4H = [("donchian_breakout", "SILVER"), ("donchian_breakout", "NZDUSD"),
          ("turtle_breakout", "HK50"), ("momentum", "COPPER"),
          ("momentum", "CHFJPY")]          # WHEAT is outside the universe
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


def to_4h(rows):
    """Aggregate cached 1h bars into 4h bars on the 00/04/08... grid."""
    out = []; bucket = None
    for t, o, h, l, c, v in rows:
        start = t.replace(hour=(t.hour // 4) * 4, minute=0, second=0, microsecond=0)
        if bucket is None or bucket[0] != start:
            if bucket is not None: out.append(bucket)
            bucket = [start, o, h, l, c, v]
        else:
            bucket[2] = max(bucket[2], h); bucket[3] = min(bucket[3], l)
            bucket[4] = c; bucket[5] += v
    if bucket is not None: out.append(bucket)
    return [tuple(b) for b in out]


def signals_for(frames, atr_floor, meta, combos, tag):
    out = []
    for strat, pair in combos:
        df = frames.get(pair)
        if df is None: continue
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for x in get_strategy(strat)(df, {}):
            if gate(strat, df, x.index).blocked: continue
            if direction_blocked(pair, x.direction): continue
            terms = trade_terms(df, x.index, pair, meta, atr_floor)
            if terms is None: continue
            entry, stop_d, cost_r, risk_usd = terms
            r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
            if r is None: continue
            out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                        "strat": f"{strat}{tag}", "r": r, "usd": r * risk_usd,
                        "pinned": bool(tag)})
    return out


def ranked(window):
    agg = {}
    for t in window:
        if t["pinned"]: continue
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < MIN_RANK_TRADES: continue
        r = np.array([t["r"] for t in ts])
        eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains / losses)
        if pf < MIN_PF or eR < MIN_ER: continue
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    return {k for _, k in rows[:TOP_N]}


def replay(window, active, allow_4h):
    open_until = {}; per_day = {}; n = 0; n4 = 0
    for t in window:
        if t["pinned"]:
            if not allow_4h: continue
        elif (t["strat"], t["pair"]) not in active:
            continue
        for p_ in [p_ for p_, u in open_until.items() if u <= t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until: continue
        if len(open_until) >= base.MAX_CONCURRENT: continue
        open_until[t["pair"]] = t["exit_ts"]
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0) + t["usd"]
        n += 1; n4 += 1 if t["pinned"] else 0
    return per_day, n, n4


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    f1 = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
          if p in meta and len(rows) >= 2000}
    f4 = {p: add_indicators(to_frame(to_4h(rows))) for p, rows in raw.items()
          if p in meta and len(rows) >= 2000}
    sig = signals_for(f1, atr_floor, meta, [(s, p) for s in HOUR_STRATS for p in f1], "")
    sig4 = signals_for(f4, atr_floor, meta, PIN_4H, "_4h")
    print(f"1h signals={len(sig)}  4h pin signals={len(sig4)}", flush=True)
    allsig = sorted(sig + sig4, key=lambda z: z["ts"])

    t0 = min(s["ts"] for s in allsig); t1 = max(s["ts"] for s in allsig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step

    daily = {True: {}, False: {}}; counts = {True: 0, False: 0}; four = 0
    for start, end in blocks:
        rw = [s for s in allsig if start - rank_w <= s["ts"] < start]
        tw = [s for s in allsig if start <= s["ts"] < end]
        active = ranked(rw)
        for allow in (True, False):
            per_day, n, n4 = replay(tw, active, allow)
            for d, v in per_day.items():
                daily[allow][d] = daily[allow].get(d, 0.0) + v
            counts[allow] += n
            if allow: four += n4

    print(f"\nwith 4h pins: {counts[True]} trades ({four} of them 4h)   "
          f"without: {counts[False]} ({counts[False]/max(1,counts[True])-1:+.1%})")
    all_days = sorted(set(daily[True]) | set(daily[False]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    a_with = np.array([daily[True].get(d, 0.0) for d in all_days])
    a_without = np.array([daily[False].get(d, 0.0) for d in all_days])

    print("\n{:<14}{:>14}{:>14}{:>12}".format("sample", "with pins", "without", "diff"))
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today - np.timedelta64(dfrom, 'D')) & (days <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        w = a_with[m].sum()/span; o = a_without[m].sum()/span
        better.append(o > w)
        print(f"{f'{dto}-{dfrom} d':<14}{w:>+14.4f}{o:>+14.4f}{o-w:>+12.4f}")
    t = t_stat(a_without - a_with)
    print(f"\npooled without - with: "
          f"{(a_without.sum()-a_with.sum())/len(all_days):+.4f} USD/day  t {t:+.2f}")
    print(f"(a) better on all four: {'YES' if all(better) else 'NO'}   "
          f"(b) t > 2: {'YES' if t > 2 else 'NO'}")
asyncio.run(main())

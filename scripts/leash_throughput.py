"""The holding leash priced as throughput, not as expectancy.

Run 13 swept the leash at 12 / 24 / 48 / 96 bars and read it in E[R] on
ten instruments with no occupancy model: every signal was booked, so a
shorter leash could only change what a trade earned, never how many
trades the book could take. It found 24 the maximum on the recent year
and a flipped sign on the older one, and 24 stayed.

Section 227 changed what that measurement is worth. The binding resource
is not signals, it is instrument slots — and a leash is exactly how long
one slot stays occupied. Halving it hands the slot back twice as fast.
That is the one form of added frequency section 227 did not catch as
disguised leverage: the second trade starts after the first has closed,
so nothing about concurrent exposure changes. Same size, same stop, same
concurrent cap, same cluster cap — only the queue moves faster.

The live journal points the same way. Since 2026-08-24, 29 of 46 closed
trades exited on the leash at +0.12 R, while the 12 stops and 5 targets
together are negative. The leash is the profitable exit and it is also
the slowest one.

Candidate fixed beforehand at 12 bars — half the live leash, the largest
throughput gain available, and the only alternative run 13 read as level
(+0.0001 R) on its older sample. 18 / 36 / 48 run as diagnostics with no
standing to qualify.

Acceptance, fixed before the data were seen:

  (a) 12 bars earns more USD per calendar day than the live 24 on ALL
      FOUR year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) mean concurrent occupancy does NOT rise above the live rule's.
      Clause (c) is what separates this from section 227: the gain has
      to come from slots returned sooner, not from more open risk at
      once. A variant that holds more positions simultaneously is
      scaling again and does not count.

All three must hold. See docs/EDGE_FINDINGS.md 228.
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
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]
LIVE_HOLD = 24; CANDIDATE = 12
HOLDS = [12, 18, 24, 36, 48]


def signals_for(frames, atr_floor, meta, hold):
    """Book every gated signal at the given leash.

    `book` reads HOLD from the base module, so the leash is set there
    rather than threaded through the call."""
    base.HOLD = hold
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for strat in HOUR_STRATS:
            for x in get_strategy(strat)(df, {}):
                if gate(strat, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "strat": strat, "r": r, "usd": r * risk_usd})
    out.sort(key=lambda z: z["ts"])
    return out


def ranked(window):
    agg = {}
    for t in window:
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


def replay(window, active):
    """Live guards, plus the position-hours the book actually held."""
    open_until = {}; per_day = {}; n = 0; pos_hours = 0.0; rs = []
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, u in open_until.items() if u <= t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until: continue
        if len(open_until) >= base.MAX_CONCURRENT: continue
        open_until[t["pair"]] = t["exit_ts"]
        pos_hours += (t["exit_ts"] - t["ts"]) / np.timedelta64(1, 'h')
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0) + t["usd"]
        rs.append(t["r"]); n += 1
    return per_day, n, pos_hours, rs


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    print(f"instruments={len(frames)} atr_floor={atr_floor:g}", flush=True)

    daily = {}; counts = {}; hours = {}; ers = {}
    for hold in HOLDS:
        sig = signals_for(frames, atr_floor, meta, hold)
        t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
        step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
        blocks = []; cut = np.datetime64(t0, 'D') + rank_w
        while cut + step <= np.datetime64(t1, 'D'):
            blocks.append((cut, cut + step)); cut = cut + step
        d = {}; n = 0; ph = 0.0; rr = []
        for start, end in blocks:
            rw = [s for s in sig if start - rank_w <= s["ts"] < start]
            tw = [s for s in sig if start <= s["ts"] < end]
            active = ranked(rw)
            per_day, cnt, p_h, rs = replay(tw, active)
            for k, v in per_day.items(): d[k] = d.get(k, 0.0) + v
            n += cnt; ph += p_h; rr.extend(rs)
        daily[hold] = d; counts[hold] = n; hours[hold] = ph; ers[hold] = rr
        print(f"hold={hold:>3}  signals={len(sig)}  trades={n}", flush=True)
        globals()["_span"] = (t0, t1)

    t0, t1 = _span
    all_days = sorted(set().union(*[set(daily[h]) for h in HOLDS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {h: np.array([daily[h].get(d, 0.0) for d in all_days]) for h in HOLDS}
    span_hours = float(len(all_days)) * 24.0

    print("\n{:<8}{:>9}{:>10}{:>10}{:>12}{:>12}".format(
        "hold", "trades", "vs live", "E[R]", "occupancy", "USD/day"))
    for h in HOLDS:
        occ = hours[h] / span_hours
        print(f"{h:<8}{counts[h]:>9}{counts[h]/max(1,counts[LIVE_HOLD])-1:>+10.1%}"
              f"{float(np.mean(ers[h])):>+10.4f}{occ:>12.2f}"
              f"{series[h].sum()/len(all_days):>+12.4f}")

    print("\n{:<14}{:>12}{:>12}{:>12}".format("sample", "live (24)", "twelve", "diff"))
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today - np.timedelta64(dfrom, 'D')) & (days <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        a = series[LIVE_HOLD][m].sum()/span; b = series[CANDIDATE][m].sum()/span
        better.append(b > a)
        print(f"{f'{dto}-{dfrom} d':<14}{a:>+12.4f}{b:>+12.4f}{b-a:>+12.4f}")

    diff = series[CANDIDATE] - series[LIVE_HOLD]
    t = t_stat(diff)
    occ_live = hours[LIVE_HOLD]/span_hours; occ_cand = hours[CANDIDATE]/span_hours
    print(f"\npooled twelve − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = all(better); ok_b = t > 2; ok_c = occ_cand <= occ_live
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) occupancy not above live: {'YES' if ok_c else 'NO'} "
          f"({occ_cand:.2f} vs {occ_live:.2f})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())

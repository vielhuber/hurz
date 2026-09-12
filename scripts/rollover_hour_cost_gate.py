"""The rollover hour, charged and then refused.

Section 176 built the spread sampler on the heartbeat and left one
sentence open: once enough hours exist, the simulator can charge the
hour's spread instead of the audited daytime table. Four days of
`data/spread_samples.jsonl` now cover all 24 UTC hours across 31
instruments, and the profile is not noise:

    hour 21 UTC   fx 7.70x the instrument's own median half-spread (n=58)
                  index 2.23x (n=32)
    hour 22 UTC   index 1.49x, commodity 1.18x, fx 1.11x
    hour 04 UTC   index 1.32x
    crypto        1.00x in every hour — a 24/7 venue has no rollover

That is the daily rollover window, and it matters more than a costing
detail. The live cost filter meets a wide quote by WIDENING the stop up
to 2x and only refuses if the share is still above 10 %. An FX breakout
whose cost share is 2 % by the table sits at 15 % at 21:00 and is taken
with a 54 % wider stop — same 1.5 R target, now much further away. The
bot does not skip the rollover hour; it distorts the trade's geometry
to fit through it.

The lever is to refuse instead. Both arms are scored under the SAME
hour-aware cost model, so the comparison isolates the entry rule rather
than the repricing.

  arm A  every hour allowed, live widen-or-skip rule  (current behaviour)
  arm B  refuse when the hour multiplier is >= 2.0    (candidate)

Acceptance, fixed before the data were seen:

  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2,
  (c) the refused entries must themselves be non-positive in USD — the
      gain has to come from dropping losers, not from reshuffling which
      trades the concurrency guards happen to admit.

Thresholds 1.3 and 1.5 are printed as diagnostics with no standing to
qualify. No risk limit moves: the rule only refuses entries.

See docs/EDGE_FINDINGS.md 246.
"""
import asyncio, json, math, os, sys, collections, statistics
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.position_sizing import (
    calculate_position_size, DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD,
)
from scripts.spot_backtest import _fee_for
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, book, rank, trade,
    RANK_DAYS, TRADE_DAYS, META_CACHE, PAIRS, STRATS, STOP_ATR, PLAT,
)

SAMPLES = "data/spread_samples.jsonl"
CLASSES = {"crypto": ["BTCUSD", "ETHUSD"],
           "fx": ["EURUSD", "AUDUSD", "USDCHF", "AUDNZD", "EURAUD", "GBPUSD",
                  "NZDUSD", "GBPCAD", "AUDJPY", "CHFJPY", "CADJPY", "EURJPY",
                  "GBPJPY", "USDJPY"],
           "index": ["DE40", "US500", "US30", "FR40", "UK100", "EU50",
                     "US100", "HK50", "J225"],
           "commodity": ["OIL_CRUDE", "OIL_BRENT", "GOLD", "SILVER", "COPPER"]}
CLASS_OF = {p: c for c, ps in CLASSES.items() for p in ps}
MIN_CELL = 10           # a class-hour cell below this keeps the flat table
PRIMARY = 2.0
DIAGNOSTIC = (1.3, 1.5)
MIN_PF = 0.8; MIN_ER = -0.2; LIVE_N = 40   # what the scheduler actually asks for


def hour_multipliers():
    """Median half-spread per class and UTC hour, over the instrument's own median.

    Normalising per instrument first keeps a wide name from setting the
    class profile: the question is what the hour does to a spread, not
    which instrument is expensive."""
    rows = [json.loads(l) for l in open(SAMPLES)]
    per_pair = collections.defaultdict(list)
    for r in rows:
        if r["pair"] in CLASS_OF:
            per_pair[r["pair"]].append(r["half_spread_pct"])
    med = {p: statistics.median(v) for p, v in per_pair.items() if statistics.median(v) > 0}
    cell = collections.defaultdict(list)
    for r in rows:
        p = r["pair"]
        if p not in med: continue
        cell[(CLASS_OF[p], int(r["ts"][11:13]))].append(r["half_spread_pct"] / med[p])
    out = {}
    for key, vals in cell.items():
        out[key] = statistics.median(vals) if len(vals) >= MIN_CELL else 1.0
    return out


def trade_terms(df, e, pair, meta, atr_floor, mult):
    """As the harness's, with the hour's measured spread instead of the table."""
    A = df["atr_14"].values; C = df["close"].values
    atr = A[e]
    if not np.isfinite(atr) or atr <= 0: return None
    entry = float(C[e]); stop_d = STOP_ATR * atr
    vm = 0.0105 * entry
    if stop_d < vm: stop_d = vm
    if atr_floor > 0 and stop_d / atr < atr_floor: return None
    fee = _fee_for(PLAT, pair) * mult
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
    return entry, stop_d, cost_r, sized.planned_risk * rate


def all_signals(frames, atr_floor, meta, mults):
    """Every gated signal, priced at the spread its own hour actually carries."""
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        hours = ts.astype("datetime64[h]").astype(np.int64) % 24
        cls = CLASS_OF.get(pair)
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for s in STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                mult = mults.get((cls, int(hours[x.index])), 1.0)
                terms = trade_terms(df, x.index, pair, meta, atr_floor, mult)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "strat": s, "r": r, "usd": r * risk_usd,
                            "risk": risk_usd, "mult": mult})
    out.sort(key=lambda z: z["ts"])
    return out


def rank_live(window):
    """Top-40 under the eligibility filter the scheduler uses (section 217)."""
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < 10: continue
        r = np.array([t["r"] for t in ts])
        eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains / losses)
        if pf < MIN_PF or eR < MIN_ER: continue
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    return {k for _, k in rows[:LIVE_N]}


def walk_forward(sig, t0, t1, live=False):
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    daily = {}; n = 0
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        per_day, k = trade(tw, rank_live(rw) if live else rank(rw, False))
        for d, v in per_day.items():
            daily[d] = daily.get(d, 0.0) + v
        n += k
    return daily, n, len(blocks)


async def main():
    atr_floor = _min_stop_atr_multiple()
    mults = hour_multipliers()
    print("hour multipliers >= 1.10 (class, hour): " + ", ".join(
        f"{c}@{h:02d} {m:.2f}" for (c, h), m in sorted(mults.items()) if m >= 1.10))

    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {}
    for pair, rows in raw.items():
        if pair not in meta or len(rows) < 2000: continue
        frames[pair] = add_indicators(to_frame(rows))
    print(f"instruments={len(frames)} atr_floor={atr_floor:g}", flush=True)

    sig = all_signals(frames, atr_floor, meta, mults)
    print(f"gated, sized, booked signals: {len(sig)} "
          f"(of them in a multiplied hour: "
          f"{sum(1 for s in sig if s['mult'] >= PRIMARY)})", flush=True)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    now = np.datetime64(datetime.now(timezone.utc).date())

    flat = all_signals(frames, atr_floor, meta, {})
    flat_by = {(x["pair"], x["strat"], x["ts"]): x for x in flat}

    base_daily, base_n, nblocks = walk_forward(sig, t0, t1)
    print(f"out-of-sample blocks: {nblocks}", flush=True)

    print(f"\n{'threshold':<12}{'trades':>8}{'refused':>9}"
          f"{'pooled base':>13}{'pooled gate':>13}{'diff':>9}{'t':>7}{'up':>5}")
    results = {}
    for thr in (PRIMARY,) + DIAGNOSTIC:
        kept = [s for s in sig if s["mult"] < thr]
        daily, n, _ = walk_forward(kept, t0, t1)
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days])
        b = np.array([daily.get(d, 0.0) for d in days])
        up = 0; samples = 0; rows = []
        for lo, hi in [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]:
            sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                            < (now - np.timedelta64(lo, 'D')) for d in days])
            if sel.sum() == 0: continue
            samples += 1; span = float(hi - lo)
            av = a[sel].sum() / span; bv = b[sel].sum() / span
            if bv > av: up += 1
            rows.append((lo, hi, av, bv, t_stat(b[sel] - a[sel]), int(sel.sum())))
        d_ = b - a
        tag = "primary" if thr == PRIMARY else "diag"
        print(f"{thr:<6.1f}{tag:<6}{n:>8}{base_n-n:>9}"
              f"{a.sum()/len(days):>+13.4f}{b.sum()/len(days):>+13.4f}"
              f"{d_.sum()/len(days):>+9.4f}{t_stat(d_):>+7.2f}{up:>3}/{samples}")
        results[thr] = (rows, d_, up, samples, base_n - n)

    rows, d_, up, samples, refused = results[PRIMARY]
    print(f"\n--- primary threshold {PRIMARY} by sample ---")
    print(f"{'sample':<14}{'arm A':>12}{'arm B':>12}{'diff':>10}{'t':>8}{'days':>7}")
    for lo, hi, av, bv, tv, nd in rows:
        print(f"{lo}-{hi} d{'':<4}{av:>+12.4f}{bv:>+12.4f}{bv-av:>+10.4f}"
              f"{tv:>+8.2f}{nd:>7}")

    dropped = [s for s in sig if s["mult"] >= PRIMARY]
    if dropped:
        usd = np.array([s["usd"] for s in dropped]); rr = np.array([s["r"] for s in dropped])
        print(f"\nrefused signals: {len(dropped)}  mean {usd.mean():+.4f} USD "
              f"({rr.mean():+.4f} R) at t {t_stat(usd):+.2f}")
        by = collections.Counter(CLASS_OF.get(s["pair"]) for s in dropped)
        print("by class: " + ", ".join(f"{k} {v}" for k, v in by.most_common()))
    print(f"\nstrict harness (TOP_N=10, eR>0, pf>=1): {up}/{samples} samples up, "
          f"pooled t {t_stat(d_):+.2f} — but only {refused} of {len(dropped)} "
          f"affected signals reach that book, so it cannot answer the question.")

    # --- the same arms under the selector the bot actually runs ---------
    print(f"\n--- live selector (top {LIVE_N}, pf>={MIN_PF}, eR>={MIN_ER}) ---")
    live_base, live_n, _ = walk_forward(sig, t0, t1, live=True)
    kept = [x for x in sig if x["mult"] < PRIMARY]
    live_gate, live_k, _ = walk_forward(kept, t0, t1, live=True)
    days = sorted(set(live_base) | set(live_gate))
    a = np.array([live_base.get(d, 0.0) for d in days])
    b = np.array([live_gate.get(d, 0.0) for d in days])
    print(f"trades  arm A {live_n}   arm B {live_k}  "
          f"({live_k - live_n:+d}, {(live_k/max(1,live_n)-1):+.1%})")
    print(f"{'sample':<14}{'arm A':>12}{'arm B':>12}{'diff':>10}{'t':>8}{'days':>7}")
    up = 0; samples = 0
    for lo, hi in [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]:
        sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                        < (now - np.timedelta64(lo, 'D')) for d in days])
        if sel.sum() == 0: continue
        samples += 1; span = float(hi - lo)
        av = a[sel].sum() / span; bv = b[sel].sum() / span
        if bv > av: up += 1
        print(f"{lo}-{hi} d{'':<4}{av:>+12.4f}{bv:>+12.4f}{bv-av:>+10.4f}"
              f"{t_stat(b[sel]-a[sel]):>+8.2f}{int(sel.sum()):>7}")
    d_ = b - a
    print(f"{'pooled':<14}{a.sum()/len(days):>+12.4f}{b.sum()/len(days):>+12.4f}"
          f"{d_.sum()/len(days):>+10.4f}{t_stat(d_):>+8.2f}{len(days):>7}")

    # --- does the refused set read the same way on disjoint samples? ----
    print(f"\n--- the refused signals, by sample ---")
    print(f"{'sample':<14}{'n':>7}{'R (hour cost)':>16}{'t':>8}"
          f"{'R (flat table)':>17}{'rest R':>10}")
    dts = np.array([x["ts"] for x in dropped])
    for lo, hi in [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]:
        sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(v, 'D')
                        < (now - np.timedelta64(lo, 'D')) for v in dts])
        if sel.sum() == 0: continue
        sub = [x for x, k in zip(dropped, sel) if k]
        rr = np.array([x["r"] for x in sub])
        fl = [flat_by.get((x["pair"], x["strat"], x["ts"])) for x in sub]
        fr = np.array([x["r"] for x in fl if x is not None])
        rest = np.array([x["r"] for x in sig
                         if x["mult"] < PRIMARY
                         and (now - np.timedelta64(hi, 'D'))
                         <= np.datetime64(x["ts"], 'D')
                         < (now - np.timedelta64(lo, 'D'))])
        print(f"{lo}-{hi} d{'':<4}{len(sub):>7}{rr.mean():>+16.4f}"
              f"{t_stat(rr):>+8.2f}{fr.mean():>+17.4f}{rest.mean():>+10.4f}")

    print(f"\nclause (a): {up}/{samples} samples up — "
          f"{'PASS' if up == samples else 'FAIL'}")
    print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
          f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")
    if dropped:
        print(f"clause (c): refused mean {usd.mean():+.4f} USD — "
              f"{'PASS' if usd.mean() <= 0 else 'FAIL'}")

asyncio.run(main())

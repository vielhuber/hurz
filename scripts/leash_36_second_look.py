"""The 36-bar leash, tested on data it was not selected on.

Section 228 swept the leash at 12/18/24/36/48 and the preregistered
candidate (12) lost on all four samples. The sweep's diagnostic column
did something else: the daily figure rose monotonically to +0.2395
USD/day at 36 bars against the live +0.1558, the largest positive
reading in the log. That cell was picked after the fact out of five, on
the same four samples that would judge it — which is exactly how
sections 40 to 45 produced findings that evaporated on contact with an
independent sample. Section 115 wrote the rule that applies here: a
candidate that survives the time sample is read on the excluded
instruments and on the journal before it is believed.

Two samples, neither used to select the 36:

(A) Ten instruments outside the harness universe, eight of them quoted
    in USD so no conversion rate enters, with history already on disk
    from the structural-signal download (2023-11-30 to 2026-08-24, no
    API calls). Costs charged from that download's audited spreads,
    without the cost-ceiling skip, as in section 115's test (A).

(B) The live journal's own leash exits: 193 trades closed by the 24-hour
    leash since 2026-05-15, replayed 12 bars further through the cached
    1h history against the same stop and target the bot actually placed.
    This is the mechanism section 228 proposed — that a short leash
    truncates the right tail only — read directly on real fills.

Acceptance, fixed before either sample was looked at:

  (a) on the excluded instruments, 36 bars earns more USD per calendar
      day than 24,
  (b) on the journal's leash exits, the mean R gained by holding 12 bars
      longer is positive at t > 2,
  (c) the gain in (a) survives normalising for occupancy — 36 bars holds
      more positions at once, and only the part that is not simply more
      open risk counts.

All three must hold to build. See docs/EDGE_FINDINGS.md 229.
"""
import gzip, json, math, os, sqlite3, sys
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.utils.singletons import settings
settings.load_env()
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.position_sizing import (
    calculate_position_size, DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD,
)

STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
STOP_ATR = 2.0; RR = 1.5; MAX_CONCURRENT = 8
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
MIN_RANK_TRADES = 10; RANK_DAYS = 365; TRADE_DAYS = 90
LIVE_HOLD = 24; CANDIDATE = 36
STRUCT = "tmp/structural_signal_history"
BAR_CACHE = "/tmp/eff_bars"
# USD-quoted only: the conversion rate would otherwise have to be
# reconstructed per bar, and the selection is by quote currency, not by
# anything the result depends on.
EXCLUDED = ["AAVEUSD", "ADAUSD", "AUDUSD", "DOGEUSD",
            "DOTUSD", "GBPUSD", "SOLUSD", "XRPUSD"]


def load_excluded():
    meta = json.load(open(f"{STRUCT}/metadata.json"))
    frames = {}
    for pair in EXCLUDED:
        parts = []
        for phase in ("development", "holdout"):
            p = f"{STRUCT}/{pair}.{phase}.csv.gz"
            if os.path.exists(p):
                parts.append(pd.read_csv(p))
        if not parts: continue
        df = pd.concat(parts, ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        frames[pair] = add_indicators(df[["timestamp", "open", "high", "low", "close"]])
    return frames, meta


def book(O, H, L, C, e, d, entry, stop_d, cost_r, n, hold):
    sl = entry - d*stop_d; tp = entry + d*RR*stop_d
    for b in range(e+1, e+hold+1):
        if b >= n: break
        gap = (O[b]-entry)*d
        if gap <= -stop_d: return gap/stop_d-cost_r, b
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl): return -1.0-cost_r, b
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp): return RR-cost_r, b
    if e+hold < n: return (float(C[e+hold])-entry)*d/stop_d-cost_r, e+hold
    return None, None


def signals(frames, meta, atr_floor, hold):
    cons = meta["constraints"]; spreads = meta["spread_fractions"]
    out = []
    for pair, df in frames.items():
        if pair not in cons: continue
        c = cons[pair]; fee = spreads[pair]/2.0
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        A = df["atr_14"].values
        for strat in STRATS:
            for x in get_strategy(strat)(df, {}):
                e = x.index
                if gate(strat, df, e).blocked: continue
                if direction_blocked(pair, x.direction): continue
                atr = A[e]
                if not np.isfinite(atr) or atr <= 0: continue
                entry = float(C[e]); stop_d = STOP_ATR*atr
                vm = 0.0105*entry
                if stop_d < vm: stop_d = vm
                if atr_floor > 0 and stop_d/atr < atr_floor: continue
                cost_r = 2.0*fee*entry/stop_d          # no ceiling skip, as in section 115
                sized = calculate_position_size(
                    entry_price=entry, stop_loss=entry-stop_d,
                    target_risk=DEFAULT_TARGET_RISK_USD,
                    notional_cap=DEFAULT_NOTIONAL_CAP_USD,
                    size_increment=c["size_increment"], min_size=c["min_size"],
                    max_size=c["max_size"])
                if sized.size is None: continue
                r, xb = book(O, H, L, C, e, x.direction, entry, stop_d, cost_r, n, hold)
                if r is None: continue
                out.append({"ts": ts[e], "exit_ts": ts[xb], "pair": pair, "strat": strat,
                            "r": r, "usd": r*sized.planned_risk})
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
        if len(open_until) >= MAX_CONCURRENT: continue
        open_until[t["pair"]] = t["exit_ts"]
        pos_hours += (t["exit_ts"]-t["ts"])/np.timedelta64(1, 'h')
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0)+t["usd"]
        n += 1
    return per_day, n, pos_hours


def part_a(atr_floor):
    frames, meta = load_excluded()
    print(f"(A) excluded instruments: {sorted(frames)}", flush=True)
    res = {}
    for hold in (LIVE_HOLD, CANDIDATE):
        sig = signals(frames, meta, atr_floor, hold)
        t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
        step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
        blocks = []; cut = np.datetime64(t0, 'D')+rank_w
        while cut+step <= np.datetime64(t1, 'D'):
            blocks.append((cut, cut+step)); cut = cut+step
        d = {}; n = 0; ph = 0.0
        for start, end in blocks:
            rw = [s for s in sig if start-rank_w <= s["ts"] < start]
            tw = [s for s in sig if start <= s["ts"] < end]
            active = ranked(rw)
            per_day, cnt, p_h = replay(tw, active)
            for k, v in per_day.items(): d[k] = d.get(k, 0.0)+v
            n += cnt; ph += p_h
        res[hold] = (d, n, ph, len(sig), len(blocks))
        print(f"    hold={hold}: signals={len(sig)} trades={n} blocks={len(blocks)}", flush=True)
    days = sorted(set(res[LIVE_HOLD][0]) | set(res[CANDIDATE][0]))
    if not days: return None
    span = float(len(days)); span_h = span*24.0
    a = sum(res[LIVE_HOLD][0].values())/span
    b = sum(res[CANDIDATE][0].values())/span
    occ_a = res[LIVE_HOLD][2]/span_h; occ_b = res[CANDIDATE][2]/span_h
    print(f"\n    {'hold':<8}{'trades':>8}{'occupancy':>12}{'USD/day':>12}{'per occ':>12}")
    for h, v in ((LIVE_HOLD, a), (CANDIDATE, b)):
        occ = res[h][2]/span_h
        print(f"    {h:<8}{res[h][1]:>8}{occ:>12.2f}{v:>+12.4f}{v/max(occ,1e-9):>+12.4f}")
    return b > a, (b/max(occ_b, 1e-9)) > (a/max(occ_a, 1e-9)), a, b, occ_a, occ_b


def part_b():
    con = sqlite3.connect("data/hurz.sqlite"); cur = con.cursor()
    cur.execute("""select pair, direction, fill_price, entry_price, stop_loss, take_profit,
                          exit_time, exit_price
                   from spot_trades
                   where accepted=1 and outcome='manual' and exit_time is not null
                     and take_profit is not null and stop_loss is not null""")
    rows = cur.fetchall()
    bars = {}
    for f in os.listdir(BAR_CACHE):
        bars[f[:-5]] = json.load(open(os.path.join(BAR_CACHE, f)))
    gains = []; resolved = {"target": 0, "stop": 0, "still open": 0}
    for pair, direction, fill, entry_p, sl, tp, exit_time, exit_price in rows:
        raw = bars.get(pair)
        if raw is None: continue
        entry = float(fill if fill is not None else entry_p)
        d = 1 if str(direction).lower() in ("buy", "long", "1") else -1
        sl = float(sl); tp = float(tp); stop_d = abs(entry-sl)
        if stop_d <= 0: continue
        t_exit = datetime.fromisoformat(str(exit_time)).replace(tzinfo=timezone.utc)
        nxt = [b for b in raw if datetime.fromisoformat(b[0]) > t_exit][:12]
        if len(nxt) < 12: continue
        actual = (float(exit_price)-entry)*d/stop_d
        extended = None
        for _, o, h, l, c, _v in nxt:
            adverse = l if d == 1 else h; favor = h if d == 1 else l
            if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl):
                extended = -1.0; resolved["stop"] += 1; break
            if (d == 1 and favor >= tp) or (d == -1 and favor <= tp):
                extended = RR; resolved["target"] += 1; break
        if extended is None:
            extended = (float(nxt[-1][4])-entry)*d/stop_d; resolved["still open"] += 1
        gains.append(extended-actual)
    g = np.array(gains)
    if len(g) < 2: return None
    t = float(g.mean()/(g.std(ddof=1)/np.sqrt(len(g))))
    print(f"\n(B) journal leash exits held 12 bars longer: n={len(g)}")
    print(f"    resolution in the extra bars: {resolved}")
    print(f"    mean R gained {g.mean():+.4f}  median {np.median(g):+.4f}  t {t:+.2f}")
    return g.mean() > 0 and t > 2, g.mean(), t, len(g)


atr_floor = _min_stop_atr_multiple()
a = part_a(atr_floor)
b = part_b()
print("\n--- preregistered bar ---")
ok_a = bool(a and a[0]); ok_c = bool(a and a[1]); ok_b = bool(b and b[0])
print(f"(a) 36 > 24 on the excluded instruments: {'YES' if ok_a else 'NO'}")
print(f"(b) journal gain positive at t > 2: {'YES' if ok_b else 'NO'}")
print(f"(c) gain survives occupancy normalisation: {'YES' if ok_c else 'NO'}")
print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")

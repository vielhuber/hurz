"""The holding leash as a throughput lever: R per calendar day.

Every leash sweep in this log — sections 29, 49d, 124/125 and the 2026-09-07
runs — measured E[R] per TRADE and found 24 bars as good as anything. But
the objective is not R per trade, it is gain per day, and a position holds
its slot for the whole leash. With the concurrent cap at 8 and a 24-bar
leash, the book can open at most 8 positions a day; at 12 bars it could
open 16. A shorter leash at a lower per-trade expectancy can therefore
still pay more per day, and no run so far has asked that question in the
metric the objective is stated in.

This is also the last open term of the daily-gain arithmetic. Section 194
found no free frequency inside the guards, section 195 capped risk per
trade at 2x by account size, and section 197 confirmed the router floor
cannot be lowered. Throughput per slot-hour is what remains.

The measurement has to be book-level, and section 132's lesson applies
in full: a per-strategy sweep stacks three strategies on the same
instrument and inflates frequency about sixfold, so any per-day figure
computed that way is an artefact. This script therefore merges all three
strategies onto one timeline, allows ONE open position per instrument
(the duplicate-exposure guard), and enforces the concurrent cap of 8 —
the same constraints the live book runs under. Signals are generated per
walk-forward segment exactly as elsewhere, then merged chronologically.

Leashes 6 / 12 / 24 (live) / 48 bars. Acceptance, fixed before the data
were seen:

  (a) Sum R per calendar day is higher than the live leash on ALL FOUR
      disjoint samples,
  (b) the paired difference per calendar day reaches t > 2 on at least
      one,
  (c) per-trade expectancy does not turn negative on any sample — a
      throughput gain built on a negative edge multiplies a loss,
  (d) what ships is the leash closest to the live 24, so the change is
      the smallest one the evidence supports.

Nothing about risk per trade changes: sizing still targets a fixed
dollar risk and the stop is untouched. What changes is how long capital
is committed per trade. DAYS_FROM / DAYS_TO select the window. See
docs/EDGE_FINDINGS.md section 198.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.strategies import get_strategy, add_indicators
from app.spot_trading.trading_blocks import direction_blocked, BLOCKED_PAIRS
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.spot_backtest import _fee_for, _venue_min_distance
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

PAIRS=[p for p in ["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"] if p not in BLOCKED_PAIRS]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; STOP_ATR=2.0; RR=1.5; PLAT="capital_com"
HOLDS=[6,12,24,48]; LIVE_HOLD=24
MAX_CONCURRENT=8
PAGE_DAYS=35; PAGE_PAUSE=0.5


def book(O,H,L,C,e,d,entry,stop_d,cost_r,n,hold):
    """R of one trade at a given leash; returns (r, exit bar)."""
    sl=entry-d*stop_d; tp=entry+d*RR*stop_d
    for b in range(e+1,e+hold+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return RR-cost_r, b
    if e+hold<n: return (float(C[e+hold])-entry)*d/stop_d-cost_r, e+hold
    return None, None


def prepare(df, pair, e, atr_floor):
    """Stop geometry and cost for one signal, or None if a guard refuses."""
    A=df["atr_14"].values; C=df["close"].values
    atr=A[e]
    if not np.isfinite(atr) or atr<=0: return None
    entry=float(C[e]); stop_d=STOP_ATR*atr
    vm=_venue_min_distance(PLAT,pair,entry)
    if vm>0 and stop_d<vm: stop_d=vm
    if atr_floor>0 and stop_d/atr<atr_floor: return None
    fee=_fee_for(PLAT,pair)
    cost_r=2.0*fee*entry/stop_d
    if cost_r>0.10:
        stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10: return None
    return entry, stop_d, cost_r


def simulate(signals, frames, hold, atr_floor):
    """Merged book: one position per instrument, concurrent cap enforced.
    `signals` is chronological (timestamp, pair, seg_key, index, direction).
    Returns list of (exit_timestamp, r)."""
    open_until={}          # pair -> exit timestamp
    out=[]
    for ts, pair, key, e, d in signals:
        # close anything whose exit has passed, so the cap reflects the book
        for p_ in [p_ for p_, until in open_until.items() if until <= ts]:
            del open_until[p_]
        if pair in open_until: continue
        if len(open_until) >= MAX_CONCURRENT: continue
        df = frames[key]
        prep = prepare(df, pair, e, atr_floor)
        if prep is None: continue
        entry, stop_d, cost_r = prep
        O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
        r, xb = book(O,H,L,C,e,d,entry,stop_d,cost_r,len(df),hold)
        if r is None: continue
        exit_ts = df["timestamp"].values[xb]
        open_until[pair] = exit_ts
        out.append((exit_ts, float(r)))
    return out


async def fetch_paced(p, pair):
    now=datetime.now(timezone.utc)
    start=now-timedelta(days=DAYS_FROM); end=now-timedelta(days=DAYS_TO)
    bars=[]; cursor=start
    while cursor<end:
        page_end=min(cursor+timedelta(days=PAGE_DAYS), end)
        for attempt in range(4):
            try:
                bars.extend(await p.fetch_history(pair, from_ts=cursor, to_ts=page_end, resolution="1h")); break
            except Exception as ex:
                print(pair,"FETCH FAIL",attempt,str(ex)[:80],flush=True); await asyncio.sleep(3)
        else:
            return None
        cursor=page_end
        await asyncio.sleep(PAGE_PAUSE*2)
    seen=set(); uniq=[]
    for b in bars:
        if b.timestamp in seen: continue
        seen.add(b.timestamp); uniq.append(b)
    return uniq


async def main():
    atr_floor=_min_stop_atr_multiple()
    clear_cache(); p=get_platform(PLAT); await p.connect()
    frames={}; signals=[]
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                key=(pair,k); frames[key]=sdf
                ts=sdf["timestamp"].values
                for s in STRATS:
                    for x in get_strategy(s)(sdf,{}):
                        if gate(s,sdf,x.index).blocked: continue
                        if direction_blocked(pair,x.direction): continue
                        signals.append((ts[x.index], pair, key, x.index, x.direction))
            print(f"{pair} bars={n}",flush=True)
    finally:
        await p.disconnect()
    signals.sort(key=lambda r: r[0])
    span_days=max(1.0, (DAYS_FROM-DAYS_TO))
    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    print(f"\n===== HOLD AS THROUGHPUT ({DAYS_FROM}-{DAYS_TO} d, merged book, "
          f"cap {MAX_CONCURRENT}, atr floor {atr_floor:g}) =====")
    print(f"total router-passed signals on the merged timeline: {len(signals)}")
    daily={}
    for hold in HOLDS:
        res=simulate(signals, frames, hold, atr_floor)
        r=np.array([x for _,x in res])
        # sum R per calendar day, keyed by exit date
        per_day={}
        for ts,x in res:
            day=str(np.datetime64(ts,'D'))
            per_day[day]=per_day.get(day,0.0)+x
        daily[hold]=per_day
        days=np.array([per_day.get(d,0.0) for d in sorted(per_day)])
        print(f"\nhold {hold:>3} bars: trades={len(r):>5} E[R]/trade={r.mean():+.4f} "
              f"(t {t(r):+.2f})  sumR={r.sum():+.1f}")
        print(f"            trades/day={len(r)/span_days:.2f}  "
              f"sumR/day={r.sum()/span_days:+.4f}  active days={len(per_day)}")
    print(f"\n{'hold':<7}{'sumR/day':>11}{'vs live':>10}{'t_paired/day':>14}")
    all_days=sorted(set().union(*[set(d) for d in daily.values()]))
    base=np.array([daily[LIVE_HOLD].get(d,0.0) for d in all_days])
    for hold in HOLDS:
        v=np.array([daily[hold].get(d,0.0) for d in all_days])
        diff=v-base
        print(f"{hold:<7}{v.sum()/span_days:>+11.4f}{(v.sum()-base.sum())/span_days:>+10.4f}"
              f"{t(diff):>+14.2f}")
asyncio.run(main())

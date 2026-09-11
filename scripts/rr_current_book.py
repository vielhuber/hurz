"""Reward:risk on the book the filters actually left behind.

The RR was last swept in sections 63 and 124, and again this morning in
sections 184 and 185 — all of them on the pre-build book. The three filters
that shipped on 2026-09-10 did not trim that book evenly: the 3 x ATR floor
removed 94-99 % of crypto (section 203) and every least-pinned trade
(section 190), the instrument block removed three FX pairs carrying 76 % of
the recent year's loss (section 192), and the ADX ceiling removed the
high-trend tail (section 188). What remains is a different population —
almost entirely venue-pinned stops at 3+ ATR — and the target sits at
1.5 x that stop.

A target is a claim about how far price travels before it turns. Changing
which trades are in the book changes that distribution, so the parameter
deserves one measurement on the book that now exists rather than an
inherited setting from the book that does not.

Measured in R per calendar day on the merged one-position-per-instrument
timeline, because the RR changes both the per-trade expectancy and the
occupancy — a nearer target frees the slot sooner, and only the merged
book prices that. RR 1.0 / 1.5 (live) / 2.0 / 2.5.

Acceptance, fixed before the data were seen:

  (a) R per calendar day is higher than the live 1.5 on ALL FOUR samples,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) what ships is the qualifying value closest to 1.5.

The stop is untouched, so the 1 R loss limit stands and no risk control
moves; only the distance at which a winner is booked changes. See
docs/EDGE_FINDINGS.md section 210.
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
SEG=3; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
RRS=[1.0,1.5,2.0,2.5]; LIVE_RR=1.5
MAX_CONCURRENT=8
PAGE_DAYS=35; PAGE_PAUSE=0.5


def book(O,H,L,C,e,d,entry,stop_d,cost_r,n,rr):
    """R of one trade at a given target; returns (r, exit bar)."""
    sl=entry-d*stop_d; tp=entry+d*rr*stop_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return rr-cost_r, b
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r, e+HOLD
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


def simulate(signals, frames, rr, atr_floor):
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
        r, xb = book(O,H,L,C,e,d,entry,stop_d,cost_r,len(df),rr)
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
    print(f"\n===== REWARD:RISK ON THE CURRENT BOOK ({DAYS_FROM}-{DAYS_TO} d, "
          f"merged book, cap {MAX_CONCURRENT}, atr floor {atr_floor:g}) =====")
    print(f"total router-passed signals on the merged timeline: {len(signals)}")
    daily={}
    for rr in RRS:
        res=simulate(signals, frames, rr, atr_floor)
        r=np.array([x for _,x in res])
        # sum R per calendar day, keyed by exit date
        per_day={}
        for ts,x in res:
            day=str(np.datetime64(ts,'D'))
            per_day[day]=per_day.get(day,0.0)+x
        daily[rr]=per_day
        days=np.array([per_day.get(d,0.0) for d in sorted(per_day)])
        print(f"\nRR {rr:>4}: trades={len(r):>5} E[R]/trade={r.mean():+.4f} "
              f"(t {t(r):+.2f})  sumR={r.sum():+.1f}")
        print(f"            trades/day={len(r)/span_days:.2f}  "
              f"sumR/day={r.sum()/span_days:+.4f}  active days={len(per_day)}")
    print(f"\n{'RR':<7}{'sumR/day':>11}{'vs live':>10}{'t_paired/day':>14}")
    all_days=sorted(set().union(*[set(d) for d in daily.values()]))
    base=np.array([daily[LIVE_RR].get(d,0.0) for d in all_days])
    for rr in RRS:
        v=np.array([daily[rr].get(d,0.0) for d in all_days])
        diff=v-base
        print(f"{rr:<7}{v.sum()/span_days:>+11.4f}{(v.sum()-base.sum())/span_days:>+10.4f}"
              f"{t(diff):>+14.2f}")
asyncio.run(main())

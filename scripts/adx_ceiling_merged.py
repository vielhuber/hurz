"""The ADX ceiling's value, determined on the merged book.

Section 201's ablation found the ceiling the weakest of the three filters
shipped on 2026-09-10: removing it is worse on the two newer samples and
better on the two older, significantly so on days 1,096-1,825
(+0.045 R a day at t = +3.27). The reason is accounting. Section 188 chose
50 with occupancy fixed on the live variant, which is the fair test of a
rule in isolation — a refused trade does not hand its slot onward. But the
objective is per-day gain, and there the slot IS handed onward, so the
value that is right for the rule need not be the value that is right for
the book.

The value itself has never been swept on the merged book. This does that:
ceilings 45 / 50 (live) / 55 / 60 / none, on the merged
one-position-per-instrument timeline with the cap at 8 and occupancy
resolved per variant, with the volatility floor and the instrument block
in force throughout.

Acceptance, fixed before the data were seen:

  (a) sum R per calendar day is at least as high as the live ceiling on
      ALL FOUR samples,
  (b) it reaches t > 2 on at least one,
  (c) what ships is the qualifying value CLOSEST to the live 50, so the
      change is the smallest the evidence supports; if "none" is the only
      qualifying variant, the ceiling is removed.

If nothing qualifies, 50 stays — section 188's decision is not withdrawn
on an inconclusive retest, and section 201 already established that
removal outright is worse on two samples.

See docs/EDGE_FINDINGS.md section 202.
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
from app.spot_trading.trading_blocks import (
    direction_blocked, BLOCKED_PAIRS, COST_BLOCKED_PAIRS,
)
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.regime import decide as _regime_decide, adx_at as _regime_adx
from scripts.spot_backtest import _fee_for, _venue_min_distance
from scripts.walk_forward import _bars_to_df

# Section 192's three, held apart so the block can be ablated on its own.
SECTION_192 = {"AUDUSD", "GBPCAD", "GBPUSD"}
UNIVERSE = [p for p in ["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"]
       if p not in (BLOCKED_PAIRS - SECTION_192)]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; STOP_ATR=2.0; RR=1.5; HOLD=24; MAX_CONCURRENT=8; PLAT="capital_com"
CEILINGS=[45.0,50.0,55.0,60.0,None]   # None = no ceiling; 50 is live
LIVE_CEILING=50.0
PAGE_DAYS=35; PAGE_PAUSE=1.0


def book(O,H,L,C,e,d,entry,stop_d,cost_r,n):
    sl=entry-d*stop_d; tp=entry+d*RR*stop_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return RR-cost_r, b
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r, e+HOLD
    return None, None


def simulate(signals, frames, ceiling, atr_floor):
    open_until={}; out=[]
    for ts, pair, key, e, d, strat, adx in signals:
        if pair in SECTION_192: continue
        if ceiling is not None and adx is not None and adx >= ceiling: continue
        for p_ in [p_ for p_, until in open_until.items() if until <= ts]:
            del open_until[p_]
        if pair in open_until: continue
        if len(open_until) >= MAX_CONCURRENT: continue
        df=frames[key]
        A=df["atr_14"].values; C=df["close"].values
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        if atr_floor>0 and stop_d/atr<atr_floor: continue
        fee=_fee_for(PLAT,pair)
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        O=df["open"].values; H=df["high"].values; L=df["low"].values
        r,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,len(df))
        if r is None: continue
        open_until[pair]=df["timestamp"].values[xb]
        out.append((df["timestamp"].values[xb], float(r)))
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
        await asyncio.sleep(PAGE_PAUSE)
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
        for pair in UNIVERSE:
            bars=await fetch_paced(p, pair)
            if not bars: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                key=(pair,k); frames[key]=sdf; ts=sdf["timestamp"].values
                for s in STRATS:
                    for x in get_strategy(s)(sdf,{}):
                        adx=_regime_adx(sdf, x.index)
                        # Router floor and the no-trade zone stay in force in
                        # every variant; only the ceiling is ablated, so the
                        # gate is evaluated without it here and re-applied
                        # per variant in simulate().
                        if adx is None or adx < 30.0: continue
                        if direction_blocked(pair, x.direction): continue
                        signals.append((ts[x.index], pair, key, x.index, x.direction, s, float(adx)))
            print(f"{pair} bars={n}",flush=True)
    finally:
        await p.disconnect()
    signals.sort(key=lambda r: r[0])
    span=max(1.0,(DAYS_FROM-DAYS_TO))
    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    print(f"\n===== ADX CEILING SWEEP ({DAYS_FROM}-{DAYS_TO} d, merged book) =====")
    print(f"router-passed signals (floor 30, shorts blocked): {len(signals)}")
    daily={}
    for v in CEILINGS:
        res=simulate(signals, frames, v, atr_floor)
        r=np.array([x for _,x in res])
        per_day={}
        for ts,x in res:
            day=str(np.datetime64(ts,'D')); per_day[day]=per_day.get(day,0.0)+x
        daily[v]=per_day
        lab="none" if v is None else f"{v:g}"
        print(f"{lab:<10} trades={len(r):>5} E[R]={r.mean():+.4f} sumR={r.sum():+.1f} "
              f"R/day={r.sum()/span:+.4f} trades/day={len(r)/span:.2f}")
    all_days=sorted(set().union(*[set(d) for d in daily.values()]))
    base=np.array([daily[LIVE_CEILING].get(d,0.0) for d in all_days])
    print(f"\n{'ceiling':<10}{'R/day':>10}{'vs live':>10}{'t_paired':>10}")
    for v in CEILINGS:
        a=np.array([daily[v].get(d,0.0) for d in all_days]); diff=a-base
        lab="none" if v is None else f"{v:g}"
        print(f"{lab:<10}{a.sum()/span:>+10.4f}{(a.sum()-base.sum())/span:>+10.4f}"
              f"{t(diff) if v!=LIVE_CEILING else float('nan'):>+10.2f}")
asyncio.run(main())

"""Does each of today's three filters still carry once the others are in?

Sections 188, 190 and 192 were measured sequentially: the ADX ceiling
against the system as it was that morning, the volatility floor against
the system with the ceiling, the instrument block against the system with
both. Each was a fair test of its own addition, but none asked the
reverse question — whether an earlier filter still contributes now that
the later ones exist.

It is a real possibility and not a formality. The three overlap in what
they remove: AUDUSD, GBPCAD and GBPUSD are FX pairs whose ATR is often
large relative to price, so the volatility floor was already refusing
some of their signals before the block list named them; and the recent
year's high-ADX losses that motivated the ceiling were concentrated in
the same FX cluster. If an earlier filter is now redundant, removing it
returns trades that are no longer bad — which is the only place in this
system where frequency can still be recovered, since sections 194, 197
and 198 closed every other route.

Ablation, on the merged one-position-per-instrument book so that the
frequency each filter costs is counted honestly:

  full      all three (live)
  -ceiling  ADX ceiling removed, floor and block kept
  -floor    3 x ATR volatility floor removed, ceiling and block kept
  -block    instrument block removed, ceiling and floor kept

Acceptance, fixed before the data were seen. A filter is REMOVED only if
its ablation gives sum R per calendar day at least as high as live on ALL
FOUR samples and reaches t > 2 on at least one. A filter STAYS if its
ablation is worse on any sample. Anything in between — neutral
everywhere, significant nowhere — leaves it in place, because a filter
already justified on four samples is not withdrawn on an inconclusive
retest.

See docs/EDGE_FINDINGS.md section 201.
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
CEILING=50.0
VARIANTS=["full","-ceiling","-floor","-block"]
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


def simulate(signals, frames, variant, atr_floor):
    open_until={}; out=[]
    for ts, pair, key, e, d, strat, adx in signals:
        if variant != "-block" and pair in SECTION_192: continue
        if variant != "-ceiling" and adx is not None and adx >= CEILING: continue
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
        if variant != "-floor" and atr_floor>0 and stop_d/atr<atr_floor: continue
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
    print(f"\n===== FILTER ABLATION ({DAYS_FROM}-{DAYS_TO} d, merged book) =====")
    print(f"router-passed signals (floor 30, shorts blocked): {len(signals)}")
    daily={}
    for v in VARIANTS:
        res=simulate(signals, frames, v, atr_floor)
        r=np.array([x for _,x in res])
        per_day={}
        for ts,x in res:
            day=str(np.datetime64(ts,'D')); per_day[day]=per_day.get(day,0.0)+x
        daily[v]=per_day
        print(f"{v:<10} trades={len(r):>5} E[R]={r.mean():+.4f} sumR={r.sum():+.1f} "
              f"R/day={r.sum()/span:+.4f} trades/day={len(r)/span:.2f}")
    all_days=sorted(set().union(*[set(d) for d in daily.values()]))
    base=np.array([daily["full"].get(d,0.0) for d in all_days])
    print(f"\n{'variant':<10}{'R/day':>10}{'vs full':>10}{'t_paired':>10}")
    for v in VARIANTS:
        a=np.array([daily[v].get(d,0.0) for d in all_days]); diff=a-base
        print(f"{v:<10}{a.sum()/span:>+10.4f}{(a.sum()-base.sum())/span:>+10.4f}"
              f"{t(diff) if v!='full' else float('nan'):>+10.2f}")
asyncio.run(main())

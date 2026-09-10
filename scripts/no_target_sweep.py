"""Removing the target: the barrier the RR sweep never reached.

Section 131 measured that the barrier pair loses — at a 1.5 R target
the stop is hit nearly twice as often on both samples, so the two
together book -0.06 R and -0.04 R per trade — while the 24-bar drift
of the trades that reach neither is the only component in the black
(+0.055 and +0.068 R, t = 4.4 and 7.6). It then closed the target
question by inference from the RR 1.0-3.0 sweep of sections 63 and
124. Inference is not measurement: RR 3.0 still places a barrier, and
the rule section 131 actually implies is no target at all, the stop
and the 24-bar leash alone. That variant has never been booked.

Four rules on the same bar path of every live trade of the three 1h
trend strategies, occupancy on the live rule so the trade set is
identical and every difference is paired per trade: the live 1.5 R
target, 3.0 R, 6.0 R, and none. The stop is untouched in all four, so
the 1 R loss limit stands and no risk control is loosened; only the
upside barrier moves. Acceptance, fixed before the data were seen:
better than the live rule at paired t > 2 on both disjoint samples.
DAYS_FROM / DAYS_TO select the history window; DUMP writes the
per-trade R and exit kind of every variant. See docs/EDGE_FINDINGS.md
section 184.
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
from app.spot_trading.trading_blocks import direction_blocked
from scripts.spot_backtest import _fee_for, _venue_min_distance
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

PAIRS=["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
DUMP=os.getenv("DUMP","")
SEG=3; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
# None = no upside barrier; the trade runs to the stop or the leash.
TARGETS=[1.5,3.0,6.0,None]; LIVE=0
PAGE_DAYS=35; PAGE_PAUSE=0.5


def book(O,H,L,C,e,d,entry,stop_d,cost_r,rr,n):
    """R of one trade under one target rule; returns (r, exit bar, kind)."""
    sl=entry-d*stop_d
    tp=entry+d*rr*stop_d if rr is not None else None
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b, "stop"
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b, "stop"
        if tp is not None and ((d==1 and favor>=tp) or (d==-1 and favor<=tp)): return rr-cost_r, b, "target"
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r, e+HOLD, "timeout"
    return None, None, None

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; n=len(df)
    for e,d in sorted(entries):
        if e<=in_until or e>=n: continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        rs=[]; kinds=[]
        for i,rr in enumerate(TARGETS):
            r,xb,kind=book(O,H,L,C,e,d,entry,stop_d,cost_r,rr,n)
            if r is None: break
            rs.append(r); kinds.append(kind)
            if i==LIVE: in_until=xb
        if len(rs)==len(TARGETS): out.append((rs,kinds))
    return out

def table(name, tr):
    R=np.array([x for x,_ in tr]); K=[k for _,k in tr]
    if len(R)<4: return
    base=R[:,LIVE]
    print(f"\n--- {name}: n={len(R)} live RR 1.5 E[R]={base.mean():+.4f} sumR={base.sum():+.1f}")
    print(f"{'target':<10}{'E[R]':>9}{'t':>7}{'diff vs 1.5':>13}{'t_pair':>8}{'sum R':>9}{'win%':>7}{'targ%':>7}{'stop%':>7}{'time%':>7}")
    for i,rr in enumerate(TARGETS):
        r=R[:,i]; d=r-base; se=r.std(ddof=1)/np.sqrt(len(r))
        sd=d.std(ddof=1); tp=d.mean()/(sd/np.sqrt(len(d))) if sd>0 else float('nan')
        lab="none" if rr is None else f"{rr:g} R"
        share=lambda k: sum(1 for kk in K if kk[i]==k)/len(K)*100
        print(f"{lab:<10}{r.mean():>+9.4f}{r.mean()/se:>+7.2f}{d.mean():>+13.4f}{tp:>+8.2f}{r.sum():>+9.1f}{np.mean(r>0)*100:>7.1f}{share('target'):>7.0f}{share('stop'):>7.0f}{share('timeout'):>7.0f}")

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
    clear_cache(); p=get_platform(PLAT); await p.connect()
    per_pair={}; per_s={s:[] for s in STRATS}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; per_pair[pair]=[]
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    tr=run(sdf,sg,pair); per_pair[pair].extend(tr); per_s[s].extend(tr)
            print(f"{pair} bars={n} trades={len(per_pair[pair])}",flush=True)
    finally:
        await p.disconnect()
    allt=[t for tr in per_pair.values() for t in tr]
    if DUMP: np.savez(DUMP, R=np.array([x for x,_ in allt]))
    print(f"\n===== TARGET REMOVED, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allt)
    for s in STRATS: table(s, per_s[s])
    for pair in PAIRS:
        if pair in per_pair: table(pair, per_pair[pair])
asyncio.run(main())

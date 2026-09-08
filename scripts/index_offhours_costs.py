"""Index instruments with hour-dependent costs on the cost-charging simulator.

Off-hours signals are charged a measured off-hours half-spread, cash-hour
signals the audited table; the live widening rule is applied to both.
DAYS_FROM / DAYS_TO select the history window. See docs/EDGE_FINDINGS.md
section 107.
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
from scripts.spot_backtest import _fee_for, _venue_min_distance
from scripts.walk_forward import _bars_to_df

PAIRS = ["FR40","UK100","DE40","EU50","HK50"]
OFF={"FR40":0.000602,"UK100":0.000139,"DE40":0.000077,"EU50":0.000156,"HK50":0.000593}
CASH={"FR40":(7,15),"UK100":(7,15),"DE40":(7,15),"EU50":(7,15),"HK50":(1,8)}
STOPS = ['table','hourly']
TO={x:0 for x in STOPS}
STRATS = ["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; HOLD=24; PLAT="capital_com"

def run_trades(df, entries, pair, MODE):
    STOP_ATR=2.0; TS=df['timestamp']
    rs=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    H=df["high"].values; L=df["low"].values; C=df["close"].values; A=df["atr_14"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        f=fee
        if MODE=='hourly':
            h=TS.iloc[e].hour; a,b=CASH[pair]
            if not (a<=h<b): f=OFF[pair]
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d; cost_r=2.0*f*entry/stop_d
        offh=(MODE=='hourly' and f!=fee)
        if cost_r>0.10:
            factor=min(cost_r/0.10,2.0); stop_d*=factor; tp=entry+d*RR*stop_d; sl=entry-d*stop_d; cost_r=2.0*f*entry/stop_d
            if cost_r>0.10: continue
        r=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD; pass
        if r is not None: rs.append((r, cost_r, offh))
    return rs

def stats(a):
    a=np.asarray([x[0] for x in a]); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))
def costs(a):
    return float(np.mean([x[1] for x in a]))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={P:[] for P in STOPS}; strats=[get_strategy(x) for x in STRATS]
    try:
        for pair in PAIRS:
            bars=None
            for attempt in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
                except Exception as ex:
                    print(pair,"FETCH FAIL",attempt,str(ex)[:80],flush=True); await asyncio.sleep(3)
            if bars is None: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            print(f"{pair} bars={n}",flush=True)
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                sigs=[[(x.index,x.direction) for x in st(sdf,{})] for st in strats]
                for P in STOPS:
                    for sg in sigs: res[P].extend(run_trades(sdf,sg,pair,P))
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== INDEX COSTS: audited table vs hour-dependent ({DAYS_FROM}-{DAYS_TO} d) =====")
    for m in STOPS:
        a=np.array([x[0] for x in res[m]]); c=np.array([x[1] for x in res[m]]); o=np.array([x[2] for x in res[m]])
        se=a.std(ddof=1)/np.sqrt(len(a))
        print(f"{m:<7} n={len(a):>5} E[R]={a.mean():+.4f} t={a.mean()/se:+.2f} cost_r={c.mean():.4f} sumR={a.sum():+.1f}" + (f" | off-hours trades={int(o.sum())} E[R]_off={a[o].mean():+.4f} E[R]_cash={a[~o].mean():+.4f}" if m=='hourly' else ""))
asyncio.run(main())

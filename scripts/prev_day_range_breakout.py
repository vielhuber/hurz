"""Previous-day range breakout on the cost-charging walk-forward simulator.

The first hourly close of a UTC day above the previous day's high (below
its low) enters long (short), once per day, on the three commodities.
Count-matched random entries and the live donchian_breakout on the same
bars are the comparisons. DAYS_FROM / DAYS_TO select the history window.
See docs/EDGE_FINDINGS.md section 57.
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

OPEN_HOUR = {"GOLD":0, "OIL_CRUDE":0, "OIL_BRENT":0}
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=1.0; HOLD=24; PLAT="capital_com"; WINDOW=6
rng=np.random.default_rng(11)

def run_trades(df, entries, pair):
    rs=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    H=df["high"].values; L=df["low"].values; C=df["close"].values; A=df["atr_14"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d; cost_r=2.0*fee*entry/stop_d
        r=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None: rs.append(r)
    return rs

def orb_entries(df, _unused):
    ts=df["timestamp"]; H=df["high"].values; L=df["low"].values; C=df["close"].values; out=[]
    dates=[t.date() for t in ts]
    day_hi={}; day_lo={}
    for i,dt in enumerate(dates):
        day_hi[dt]=max(day_hi.get(dt,-1e18),H[i]); day_lo[dt]=min(day_lo.get(dt,1e18),L[i])
    prev={}; last=None
    for dt in sorted(set(dates)):
        prev[dt]=last; last=dt
    done=set()
    for i in range(60,len(df)):
        dt=dates[i]; pd_=prev.get(dt)
        if pd_ is None or dt in done or ts.iloc[i].weekday()>4: continue
        if C[i]>day_hi[pd_]: out.append((i,1)); done.add(dt)
        elif C[i]<day_lo[pd_]: out.append((i,-1)); done.add(dt)
    return out

def stats(a):
    a=np.asarray(a); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={}
    try:
        for pair,oh in OPEN_HOUR.items():
            bars=None
            for attempt in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
                except Exception as ex:
                    print(pair,"FETCH FAIL",attempt,str(ex)[:80],flush=True); await asyncio.sleep(3)
            if bars is None: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            res[pair]={"orb":[],"rand":[],"base":[]}
            dch=get_strategy("donchian_breakout")
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                ent=orb_entries(sdf,oh)
                res[pair]["orb"].extend(run_trades(sdf,ent,pair))
                res[pair]["base"].extend(run_trades(sdf,[(x.index,x.direction) for x in dch(sdf,{})],pair))
                for _ in range(5):
                    idx=rng.integers(60,len(sdf)-1,size=len(ent)); dirs=rng.choice([-1,1],size=len(ent))
                    res[pair]["rand"].extend(run_trades(sdf,list(zip(idx.tolist(),dirs.tolist())),pair))
            n_o,m_o,_=stats(res[pair]["orb"])
            print(f"{pair} bars={n} orb n={n_o} E={m_o:+.4f}",flush=True)
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== PREVIOUS-DAY RANGE BREAKOUT ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'pair':<8}{'n_orb':>7}{'E_orb':>9}{'t0':>7}{'win%':>7}{'E_rand':>9}{'orb-rand':>10}{'t':>7}{'E_donch':>9}{'orb-donch':>11}{'t':>7}")
    for key in list(res)+["POOLED"]:
        g=lambda kk: res[key][kk] if key!="POOLED" else [r for pp in res for r in res[pp][kk]]
        o=g("orb"); no,mo,so=stats(o); na,ma,sa=stats(g("rand")); nb,mb,sb=stats(g("base"))
        print(f"{key:<8}{no:>7}{mo:>+9.4f}{mo/so:>+7.2f}{(np.asarray(o)>0).mean()*100:>7.1f}{ma:>+9.4f}{mo-ma:>+10.4f}{(mo-ma)/np.sqrt(so**2+sa**2):>+7.2f}{mb:>+9.4f}{mo-mb:>+11.4f}{(mo-mb)/np.sqrt(so**2+sb**2):>+7.2f}")
asyncio.run(main())

"""Preregistered time-of-day split on the cost-charging walk-forward simulator.

Replays the three live 1h trend strategies at the live 24-bar leash and
splits every trade by whether its signal bar falls inside [07:00, 20:00)
UTC. DAYS_FROM / DAYS_TO select the history window so a disjoint sample
can be run as the independent check. See docs/EDGE_FINDINGS.md section 51.
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

PAIRS = ["BTCUSD","ETHUSD","OIL_CRUDE","OIL_BRENT","GOLD","DE40","US500","US30","EURUSD","AUDUSD"]
STRATS = ["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","1095")); DAYS_TO=int(os.getenv("DAYS_TO","365")); SEG=3; RR=1.5; STOP_ATR=1.0; HOLD=24; PLAT="capital_com"
WIN=(7,20)  # preregistered: [07:00, 20:00) UTC

def sim(df, signals, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    for sig in signals:
        i=sig.index
        if i<=in_until or i>=len(df): continue
        atr=df.iloc[i].get("atr_14")
        if atr is None or not np.isfinite(atr) or atr<=0: continue
        entry=float(df.iloc[i]["close"]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        tp_d=RR*stop_d
        sl=entry-stop_d if sig.direction==1 else entry+stop_d
        tp=entry+tp_d if sig.direction==1 else entry-tp_d
        cost_r=(2.0*fee*entry/stop_d) if stop_d>0 else 0.0
        ts=df.iloc[i]["timestamp"]; hour=ts.hour
        r=None
        for j in range(1,HOLD+1):
            if i+j>=len(df): break
            h=df.iloc[i+j]["high"]; l=df.iloc[i+j]["low"]
            if sig.direction==1:
                if l<=sl: r=-1.0-cost_r; in_until=i+j; break
                if h>=tp: r=RR-cost_r; in_until=i+j; break
            else:
                if h>=sl: r=-1.0-cost_r; in_until=i+j; break
                if l<=tp: r=RR-cost_r; in_until=i+j; break
        if r is None and i+HOLD<len(df):
            cl=float(df.iloc[i+HOLD]["close"]); risk=abs(entry-sl)
            if risk>0: r=(cl-entry)*sig.direction/risk-cost_r
            in_until=i+HOLD
        if r is not None: out.append((r,hour))
    return out

def stats(a):
    a=np.asarray(a); se=a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float('nan')
    return len(a), a.mean(), se

def welch(a,b):
    na,ma,sa=stats(a); nb,mb,sb=stats(b); return ma-mb, (ma-mb)/np.sqrt(sa**2+sb**2)

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    trades={s:[] for s in STRATS}; tz_seen=None
    try:
        for pair in PAIRS:
            bars=None
            for attempt in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
                except Exception as e:
                    print(pair,"FETCH FAIL",attempt,str(e)[:80],flush=True); await asyncio.sleep(3)
            if bars is None: continue
            tz_seen=bars[-1].timestamp.tzinfo
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            print(f"{pair} bars={n} last={bars[-1].timestamp}",flush=True)
            for s in STRATS:
                strat=get_strategy(s)
                for k in range(SEG):
                    lo=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo:hi].reset_index(drop=True)
                    trades[s].extend([(r,h,pair) for r,h in sim(sdf,strat(sdf,{}),pair)])
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print("tzinfo:",tz_seen)
    print("\n===== PREREGISTERED SPLIT: signal bar in [07:00,20:00) UTC vs rest (hold=24) =====")
    print(f"{'strategy':<20}{'n_in':>6}{'E_in':>9}{'n_out':>7}{'E_out':>9}{'n_all':>7}{'E_all':>9}{'diff':>9}{'t_diff':>8}{'in-all':>9}")
    allin=[];allout=[]
    for s in STRATS+["POOLED"]:
        tr=trades[s] if s!="POOLED" else [t for ss in STRATS for t in trades[ss]]
        rin=[r for r,h,_ in tr if WIN[0]<=h<WIN[1]]; rout=[r for r,h,_ in tr if not (WIN[0]<=h<WIN[1])]
        rall=[r for r,h,_ in tr]
        ni,mi,_=stats(rin); no,mo,_=stats(rout); na,ma,_=stats(rall); d,t=welch(rin,rout)
        print(f"{s:<20}{ni:>6}{mi:>+9.4f}{no:>7}{mo:>+9.4f}{na:>7}{ma:>+9.4f}{d:>+9.4f}{t:>+8.2f}{mi-ma:>+9.4f}")
    print("\n--- by asset class (pooled strategies): in vs out ---")
    groups={"crypto":{"BTCUSD","ETHUSD"},"fx":{"EURUSD","AUDUSD"},"index":{"DE40","US500","US30"},"commodity":{"OIL_CRUDE","OIL_BRENT","GOLD"}}
    trall=[t for ss in STRATS for t in trades[ss]]
    for g,ps in groups.items():
        rin=[r for r,h,pp in trall if pp in ps and WIN[0]<=h<WIN[1]]; rout=[r for r,h,pp in trall if pp in ps and not (WIN[0]<=h<WIN[1])]
        ni,mi,_=stats(rin); no,mo,_=stats(rout); d,t=welch(rin,rout)
        print(f"{g:<10} n_in={ni:>5} E_in={mi:+.4f}  n_out={no:>5} E_out={mo:+.4f}  diff={d:+.4f} t={t:+.2f}")
    print("\n--- context only (not acted on): pooled E[R] by 3h UTC bucket ---")
    tr=[t for ss in STRATS for t in trades[ss]]
    for b in range(0,24,3):
        rs=[r for r,h,_ in tr if b<=h<b+3]; n,m,se=stats(rs)
        print(f"{b:02d}-{b+3:02d}  n={n:>5}  E[R]={m:+.4f}  t={m/se:+.2f}")
asyncio.run(main())

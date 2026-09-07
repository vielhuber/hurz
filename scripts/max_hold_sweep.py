"""Sweep the stale-exit leash on the cost-charging walk-forward simulator.

Fetches each instrument once and replays the three live 1h trend
strategies at several `max_hold` values, pooling per-trade R across
instruments so alternatives can be compared with a Welch t against the
live 24-bar leash. See docs/EDGE_FINDINGS.md section 50.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.strategies import get_strategy, add_indicators
from scripts.spot_backtest import _fee_for, _venue_min_distance
from scripts.walk_forward import _bars_to_df

PAIRS = ["BTCUSD","ETHUSD","OIL_CRUDE","OIL_BRENT","GOLD","DE40","US500","US30","EURUSD","AUDUSD"]
STRATS = ["donchian_breakout","turtle_breakout","keltner_breakout"]
HOLDS = [6,12,24,48,96]
DAYS=365; SEG=3; RR=1.5; STOP_ATR=1.0; PLAT="capital_com"

def sim(df, signals, *, rr, stop_atr, max_hold, pair):
    rs=[]; modes={"win":0,"loss":0,"timeout":0}; in_until=-1; fee=_fee_for(PLAT,pair)
    for sig in signals:
        i=sig.index
        if i<=in_until or i>=len(df): continue
        atr=df.iloc[i].get("atr_14")
        if atr is None or not np.isfinite(atr) or atr<=0: continue
        entry=float(df.iloc[i]["close"]); stop_d=stop_atr*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        tp_d=rr*stop_d
        sl=entry-stop_d if sig.direction==1 else entry+stop_d
        tp=entry+tp_d if sig.direction==1 else entry-tp_d
        cost_r=(2.0*fee*entry/stop_d) if stop_d>0 else 0.0
        outcome=None
        for j in range(1,max_hold+1):
            if i+j>=len(df): break
            h=df.iloc[i+j]["high"]; l=df.iloc[i+j]["low"]
            if sig.direction==1:
                if l<=sl: outcome="loss"; in_until=i+j; break
                if h>=tp: outcome="win"; in_until=i+j; break
            else:
                if h>=sl: outcome="loss"; in_until=i+j; break
                if l<=tp: outcome="win"; in_until=i+j; break
        if outcome=="win": rs.append(rr-cost_r); modes["win"]+=1
        elif outcome=="loss": rs.append(-1.0-cost_r); modes["loss"]+=1
        elif i+max_hold<len(df):
            cl=float(df.iloc[i+max_hold]["close"]); pnl=(cl-entry)*sig.direction; risk=abs(entry-sl)
            if risk>0: rs.append(pnl/risk-cost_r); modes["timeout"]+=1
            in_until=i+max_hold
    return np.asarray(rs), modes

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={s:{h:{"rs":[], "pair_E":[], "to":0} for h in HOLDS} for s in STRATS}
    try:
        for pair in PAIRS:
            bars=None
            for attempt in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS), to_ts=datetime.now(timezone.utc), resolution="1h")
                    break
                except Exception as e:
                    print(pair,"FETCH FAIL",attempt,str(e)[:80], flush=True); await asyncio.sleep(3)
            if bars is None: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            print(f"{pair} bars={n}", flush=True)
            for s in STRATS:
                strat=get_strategy(s)
                seg_sig=[]
                for k in range(SEG):
                    lo=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo:hi].reset_index(drop=True)
                    seg_sig.append((sdf, strat(sdf,{})))
                for h in HOLDS:
                    prs=[]
                    for sdf,sigs in seg_sig:
                        a,m=sim(sdf,sigs,rr=RR,stop_atr=STOP_ATR,max_hold=h,pair=pair)
                        prs.append(a); res[s][h]["to"]+=m["timeout"]
                    a=np.concatenate(prs) if prs else np.array([])
                    if len(a): res[s][h]["rs"].append(a); res[s][h]["pair_E"].append(a.mean())
                    print(f"  {s:<18} hold={h:>3} n={len(a):>4} E[R]={a.mean() if len(a) else float('nan'):+.4f}", flush=True)
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print("\n===== SUMMARY (pooled over pairs; diff vs hold=24, Welch t) =====")
    for s in STRATS:
        base=np.concatenate(res[s][24]["rs"])
        print(f"\n{s}")
        print(f"{'hold':>5} {'n':>5} {'E[R]':>8} {'SE':>7} {'t0':>6} {'win%':>6} {'timeout%':>9} {'meanPairE':>10} {'diff':>8} {'t_diff':>7}")
        for h in HOLDS:
            a=np.concatenate(res[s][h]["rs"]); se=a.std(ddof=1)/np.sqrt(len(a))
            d=a.mean()-base.mean(); sd=np.sqrt(se**2+(base.std(ddof=1)**2/len(base)))
            print(f"{h:>5} {len(a):>5} {a.mean():>+8.4f} {se:>7.4f} {a.mean()/se:>+6.2f} {(a>0).mean()*100:>6.1f} {res[s][h]['to']/len(a)*100:>9.1f} {np.mean(res[s][h]['pair_E']):>+10.4f} {d:>+8.4f} {d/sd:>+7.2f}")
    # pooled across the three strategies
    print("\nALL THREE STRATEGIES POOLED")
    base=np.concatenate([np.concatenate(res[s][24]["rs"]) for s in STRATS])
    for h in HOLDS:
        a=np.concatenate([np.concatenate(res[s][h]["rs"]) for s in STRATS]); se=a.std(ddof=1)/np.sqrt(len(a))
        d=a.mean()-base.mean(); sd=np.sqrt(se**2+(base.std(ddof=1)**2/len(base)))
        print(f"hold={h:>3} n={len(a):>5} E[R]={a.mean():+.4f} SE={se:.4f} t0={a.mean()/se:+.2f} diff={d:+.4f} t_diff={d/sd:+.2f}")
asyncio.run(main())

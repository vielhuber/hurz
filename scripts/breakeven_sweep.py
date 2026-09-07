"""Break-even stop sweep on the cost-charging walk-forward simulator.

Once price has travelled `act` × stop distance in favour the stop moves
to entry; act=None is the live fixed stop. DAYS_FROM / DAYS_TO select the
history window. See docs/EDGE_FINDINGS.md section 52.
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
ACTS = [None, 0.5, 0.75, 1.0]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=1.0; HOLD=24; PLAT="capital_com"

def sim(df, signals, pair, act):
    rs=[]; modes={"win":0,"loss":0,"scratch":0,"timeout":0}; in_until=-1; fee=_fee_for(PLAT,pair)
    for sig in signals:
        i=sig.index
        if i<=in_until or i>=len(df): continue
        atr=df.iloc[i].get("atr_14")
        if atr is None or not np.isfinite(atr) or atr<=0: continue
        entry=float(df.iloc[i]["close"]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        d=sig.direction; tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        arm=entry+d*act*stop_d if act is not None else None
        cost_r=(2.0*fee*entry/stop_d) if stop_d>0 else 0.0
        r=None; armed=False
        for j in range(1,HOLD+1):
            if i+j>=len(df): break
            h=df.iloc[i+j]["high"]; l=df.iloc[i+j]["low"]
            adverse = l if d==1 else h; favor = h if d==1 else l
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl):
                if armed: r=(sl-entry)*d/stop_d-cost_r; modes["scratch"]+=1
                else: r=-1.0-cost_r; modes["loss"]+=1
                in_until=i+j; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp):
                r=RR-cost_r; modes["win"]+=1; in_until=i+j; break
            if arm is not None and not armed and ((d==1 and favor>=arm) or (d==-1 and favor<=arm)):
                armed=True; sl=entry
        if r is None and i+HOLD<len(df):
            cl=float(df.iloc[i+HOLD]["close"]); r=(cl-entry)*d/stop_d-cost_r; modes["timeout"]+=1; in_until=i+HOLD
        if r is not None: rs.append(r)
    return rs, modes

def stats(a):
    a=np.asarray(a); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={s:{a:{"rs":[],"m":{"win":0,"loss":0,"scratch":0,"timeout":0}} for a in ACTS} for s in STRATS}
    try:
        for pair in PAIRS:
            bars=None
            for attempt in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
                except Exception as e:
                    print(pair,"FETCH FAIL",attempt,str(e)[:80],flush=True); await asyncio.sleep(3)
            if bars is None: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            print(f"{pair} bars={n}",flush=True)
            for s in STRATS:
                strat=get_strategy(s)
                segs=[]
                for k in range(SEG):
                    lo=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo:hi].reset_index(drop=True); segs.append((sdf,strat(sdf,{})))
                for a in ACTS:
                    for sdf,sigs in segs:
                        rs,m=sim(sdf,sigs,pair,a); res[s][a]["rs"].extend(rs)
                        for k2,v in m.items(): res[s][a]["m"][k2]+=v
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== BREAK-EVEN STOP SWEEP ({DAYS_FROM}-{DAYS_TO} d) — diff vs baseline (no BE), Welch t =====")
    for s in STRATS+["POOLED"]:
        get=lambda a: (res[s][a]["rs"], res[s][a]["m"]) if s!="POOLED" else ([r for ss in STRATS for r in res[ss][a]["rs"]], {k:sum(res[ss][a]["m"][k] for ss in STRATS) for k in ("win","loss","scratch","timeout")})
        b,_=get(None); nb,mb,sb=stats(b)
        print(f"\n{s}\n{'act':>6}{'n':>6}{'E[R]':>9}{'t0':>7}{'win%':>7}{'loss%':>7}{'scr%':>7}{'to%':>6}{'diff':>9}{'t_diff':>8}")
        for a in ACTS:
            rs,m=get(a); n,mu,se=stats(rs); tot=sum(m.values())
            d=mu-mb; t=d/np.sqrt(se**2+sb**2) if a is not None else 0.0
            print(f"{str(a):>6}{n:>6}{mu:>+9.4f}{mu/se:>+7.2f}{m['win']/tot*100:>7.1f}{m['loss']/tot*100:>7.1f}{m['scratch']/tot*100:>7.1f}{m['timeout']/tot*100:>6.1f}{d:>+9.4f}{t:>+8.2f}")
asyncio.run(main())

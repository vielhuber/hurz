"""Breakout retest continuation on the cost-charging walk-forward simulator.

When a close returns inside the broken level within K bars of a breakout
signal, enter with the breakout at that close; count-matched random
entries serve as the control. DAYS_FROM / DAYS_TO select the history
window. See docs/EDGE_FINDINGS.md section 55.
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
KS = [3, 6]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=1.0; HOLD=24; PLAT="capital_com"
rng=np.random.default_rng(7)

def levels(df, s):
    if s=="donchian_breakout": per=20
    elif s=="turtle_breakout": per=55
    else:
        center=df["close"].ewm(span=20, adjust=False).mean()
        return (center+2.0*df["atr_14"]).shift(1).values, (center-2.0*df["atr_14"]).shift(1).values
    return df["high"].shift(1).rolling(per).max().values, df["low"].shift(1).rolling(per).min().values

def run_trades(df, entries, pair):
    """entries: list of (bar_index, direction); entry at close of bar."""
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

def reversal_entries(df, sigs, up, lo, K):
    C=df["close"].values; out=[]
    for sig in sigs:
        i=sig.index; d=sig.direction; level=up[i] if d==1 else lo[i]
        if not np.isfinite(level): continue
        for j in range(1,K+1):
            if i+j>=len(df): break
            if (d==1 and C[i+j]<level) or (d==-1 and C[i+j]>level):
                out.append((i+j,d)); break
    return out

def stats(a):
    a=np.asarray(a); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={s:{K:{"rev":[],"rand":[],"base":[]} for K in KS} for s in STRATS}
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
            for s in STRATS:
                strat=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True); sigs=strat(sdf,{}); up,lo=levels(sdf,s)
                    base=run_trades(sdf,[(x.index,x.direction) for x in sigs],pair)
                    for K in KS:
                        ent=reversal_entries(sdf,sigs,up,lo,K)
                        res[s][K]["rev"].extend(run_trades(sdf,ent,pair))
                        res[s][K]["base"].extend(base)
                        if ent:
                            idx=rng.integers(60,len(sdf)-1,size=len(ent)); dirs=rng.choice([-1,1],size=len(ent))
                            for _ in range(5):
                                res[s][K]["rand"].extend(run_trades(sdf,list(zip(idx.tolist(),dirs.tolist())),pair))
                                idx=rng.integers(60,len(sdf)-1,size=len(ent)); dirs=rng.choice([-1,1],size=len(ent))
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== BREAKOUT RETEST CONTINUATION ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'strategy':<20}{'K':>3}{'n_rev':>7}{'E_rev':>9}{'t0':>7}{'n_rand':>8}{'E_rand':>9}{'rev-rand':>10}{'t':>7}{'E_base':>9}{'rev-base':>10}{'t':>7}")
    for s in STRATS+["POOLED"]:
        for K in KS:
            g=lambda key: res[s][K][key] if s!="POOLED" else [r for ss in STRATS for r in res[ss][K][key]]
            nr,mr,sr=stats(g("rev")); na,ma,sa=stats(g("rand")); nb,mb,sb=stats(g("base"))
            print(f"{s:<20}{K:>3}{nr:>7}{mr:>+9.4f}{mr/sr:>+7.2f}{na:>8}{ma:>+9.4f}{mr-ma:>+10.4f}{(mr-ma)/np.sqrt(sr**2+sa**2):>+7.2f}{mb:>+9.4f}{mr-mb:>+10.4f}{(mr-mb)/np.sqrt(sr**2+sb**2):>+7.2f}")
asyncio.run(main())

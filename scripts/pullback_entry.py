"""Pullback entry on the cost-charging walk-forward simulator.

After a breakout signal the entry is a limit at the broken level, filled
only if price returns within K bars; K=0 is the live market entry at the
signal close. DAYS_FROM / DAYS_TO select the history window. See
docs/EDGE_FINDINGS.md section 53.
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
KS = [0, 3, 6, 12]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=1.0; HOLD=24; PLAT="capital_com"

def levels(df, s):
    if s=="donchian_breakout": per=20
    elif s=="turtle_breakout": per=55
    else:
        center=df["close"].ewm(span=20, adjust=False).mean()
        return (center+2.0*df["atr_14"]).shift(1).values, (center-2.0*df["atr_14"]).shift(1).values
    return df["high"].shift(1).rolling(per).max().values, df["low"].shift(1).rolling(per).min().values

def sim(df, signals, pair, K, up, lo):
    rs=[]; m={"win":0,"loss":0,"timeout":0,"nofill":0}; in_until=-1; fee=_fee_for(PLAT,pair)
    H=df["high"].values; L=df["low"].values; O=df["open"].values; C=df["close"].values; A=df["atr_14"].values
    for sig in signals:
        i=sig.index
        if i<=in_until or i>=len(df): continue
        atr=A[i]
        if not np.isfinite(atr) or atr<=0: continue
        d=sig.direction
        if K==0:
            e=i; entry=float(C[i])
        else:
            level=up[i] if d==1 else lo[i]
            if not np.isfinite(level): continue
            e=None
            for j in range(1,K+1):
                if i+j>=len(df): break
                if (d==1 and L[i+j]<=level) or (d==-1 and H[i+j]>=level):
                    e=i+j; entry=float(min(level,O[e]) if d==1 else max(level,O[e])); break
            if e is None: m["nofill"]+=1; continue
        stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        cost_r=(2.0*fee*entry/stop_d) if stop_d>0 else 0.0
        r=None
        first = e if K>0 else e+1   # a limit fill bar can also hit the stop; a close entry cannot
        for b in range(first, e+HOLD+1):
            if b>=len(df): break
            adverse = L[b] if d==1 else H[b]; favor = H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; m["loss"]+=1; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; m["win"]+=1; in_until=b; break
        if r is None and e+HOLD<len(df):
            r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; m["timeout"]+=1; in_until=e+HOLD
        if r is not None: rs.append(r)
    return rs, m

def stats(a):
    a=np.asarray(a); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={s:{k:{"rs":[],"m":{"win":0,"loss":0,"timeout":0,"nofill":0}} for k in KS} for s in STRATS}
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
                    for K in KS:
                        rs,mm=sim(sdf,sigs,pair,K,up,lo); res[s][K]["rs"].extend(rs)
                        for kk,v in mm.items(): res[s][K]["m"][kk]+=v
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== PULLBACK ENTRY ({DAYS_FROM}-{DAYS_TO} d) — K bars to fill at breakout level; diff vs K=0 (live), Welch t =====")
    for s in STRATS+["POOLED"]:
        get=lambda K: (res[s][K]["rs"], res[s][K]["m"]) if s!="POOLED" else ([r for ss in STRATS for r in res[ss][K]["rs"]], {kk:sum(res[ss][K]["m"][kk] for ss in STRATS) for kk in ("win","loss","timeout","nofill")})
        b,_=get(0); nb,mb,sb=stats(b)
        print(f"\n{s}\n{'K':>4}{'n':>6}{'fill%':>7}{'E[R]':>9}{'t0':>7}{'win%':>7}{'loss%':>7}{'to%':>6}{'sumR':>8}{'diff':>9}{'t_diff':>8}")
        for K in KS:
            rs,mm=get(K); n,mu,se=stats(rs); tot=n+mm["nofill"]
            d=mu-mb; t=d/np.sqrt(se**2+sb**2) if K else 0.0
            print(f"{K:>4}{n:>6}{n/tot*100:>7.1f}{mu:>+9.4f}{mu/se:>+7.2f}{mm['win']/n*100:>7.1f}{mm['loss']/n*100:>7.1f}{mm['timeout']/n*100:>6.1f}{mu*n:>+8.1f}{d:>+9.4f}{t:>+8.2f}")
asyncio.run(main())

"""Intrabar breakout entry versus close-confirmed entry on the cost-charging simulator.

The live loop evaluated the forming candle and entered on intrabar
channel crossings; every backtest enters on the confirmed close. Ten
instruments, three live trend strategies, 2-ATR stop. DAYS_FROM /
DAYS_TO select the history window. See docs/EDGE_FINDINGS.md section 92.
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
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
def levels(df, s):
    if s=="donchian_breakout": per=20
    elif s=="turtle_breakout": per=55
    else:
        center=df["close"].ewm(span=20, adjust=False).mean()
        return (center+2.0*df["atr_14"]).shift(1).values, (center-2.0*df["atr_14"]).shift(1).values
    return df["high"].shift(1).rolling(per).max().values, df["low"].shift(1).rolling(per).min().values
def intrabar_entries(df, up, lo):
    H=df["high"].values; L=df["low"].values; O=df["open"].values; out=[]; prev=0
    for i in range(60,len(df)):
        if not (np.isfinite(up[i]) and np.isfinite(lo[i])): prev=0; continue
        cur=0
        if H[i]>up[i]: cur=1
        elif L[i]<lo[i]: cur=-1
        if cur==1 and prev!=1: out.append((i,1,max(up[i],O[i])))
        elif cur==-1 and prev!=-1: out.append((i,-1,min(lo[i],O[i])))
        prev=cur
    return out
def run(df, entries, pair, same_bar):
    rs=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    H=df["high"].values; L=df["low"].values; C=df["close"].values; A=df["atr_14"].values
    for e,d,price in entries:
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(price); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d; cost_r=2.0*fee*entry/stop_d
        r=None
        for b in range((e if same_bar else e+1), e+HOLD+1):
            if b>=len(df): break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None: rs.append(r)
    return rs
def st(x): x=np.asarray(x); return len(x), x.mean(), x.std(ddof=1)/np.sqrt(len(x))
async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect(); res={s:{"close":[],"intrabar":[]} for s in STRATS}
    try:
        for pair in PAIRS:
            bars=None
            for a in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
                except Exception: await asyncio.sleep(3)
            if bars is None: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            for s in STRATS:
                strat=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True); up,lo=levels(sdf,s)
                    res[s]["close"].extend(run(sdf,[(x.index,x.direction,float(sdf["close"].iloc[x.index])) for x in strat(sdf,{})],pair,False))
                    res[s]["intrabar"].extend(run(sdf,intrabar_entries(sdf,up,lo),pair,True))
            print(f"{pair} bars={n}",flush=True); await asyncio.sleep(0.5)
    finally: await p.disconnect()
    print(f"\n===== INTRABAR BREAKOUT vs CLOSE-CONFIRMED ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'strategy':<19}{'n_close':>8}{'E_close':>9}{'n_intra':>8}{'E_intra':>9}{'diff':>9}{'t':>7}{'sumR_close':>11}{'sumR_intra':>11}")
    for s in STRATS+["POOLED"]:
        c=res[s]["close"] if s!="POOLED" else [r for ss in STRATS for r in res[ss]["close"]]
        i=res[s]["intrabar"] if s!="POOLED" else [r for ss in STRATS for r in res[ss]["intrabar"]]
        nc,mc,sc=st(c); ni,mi,si=st(i)
        print(f"{s:<19}{nc:>8}{mc:>+9.4f}{ni:>8}{mi:>+9.4f}{mi-mc:>+9.4f}{(mi-mc)/np.sqrt(sc**2+si**2):>+7.2f}{mc*nc:>+11.1f}{mi*ni:>+11.1f}")
asyncio.run(main())

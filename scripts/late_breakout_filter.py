"""Late-breakout filter: extension of the signal close from EMA(20) in ATR.

Replays the three live 1h trend strategies on the router-passed path at
the live 2-ATR stop over the whole tradeable universe and buckets every
trade by how far the signal close sat from the 20-bar EMA, in ATR(14)
units, directionally signed. Bucket edges are fixed on the recent year
and applied unchanged to the older sample (EDGES env, comma separated).
DAYS_FROM / DAYS_TO select the history window. See docs/EDGE_FINDINGS.md
section 110.
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
EDGES=[float(x) for x in os.getenv("EDGES","").split(",") if x.strip()]
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"; EMA_SPAN=20

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values; A=df["atr_14"].values
    ema=df["close"].ewm(span=EMA_SPAN, adjust=False).mean().values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        ext=(entry-ema[e])*d/atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        r=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            gap=(O[b]-entry)*d
            if gap<=-stop_d: r=gap/stop_d-cost_r; in_until=b; break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None: out.append((r,ext))
    return out

def stats(a):
    a=np.asarray(a,dtype=float)
    if len(a)<2: return len(a), float('nan'), float('nan')
    return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

def table(name, tr, edges):
    ext=np.array([x for _,x in tr]); r=np.array([x for x,_ in tr])
    print(f"\n--- {name}: n={len(r)} E[R]={r.mean():+.4f} ext median={np.median(ext):.2f} ATR")
    print(f"{'bucket':<22}{'n':>6}{'E[R]':>9}{'t':>7}{'rest E[R]':>11}{'diff':>9}{'t_diff':>8}")
    bounds=[-np.inf]+edges+[np.inf]
    for i in range(len(bounds)-1):
        m=(ext>=bounds[i])&(ext<bounds[i+1])
        n,mu,se=stats(r[m]); n2,mu2,se2=stats(r[~m])
        if n<2: continue
        diff=mu-mu2; t=diff/np.sqrt(se**2+se2**2)
        print(f"[{bounds[i]:>5.2f}, {bounds[i+1]:>5.2f})   {n:>6}{mu:>+9.4f}{mu/se:>+7.2f}{mu2:>+11.4f}{diff:>+9.4f}{t:>+8.2f}")

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    per={}; per_s={s:[] for s in STRATS}
    try:
        for pair in PAIRS:
            bars=None
            for attempt in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
                except Exception as ex:
                    print(pair,"FETCH FAIL",attempt,str(ex)[:80],flush=True); await asyncio.sleep(3)
            if bars is None: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; per[pair]=[]
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    tr=run(sdf,sg,pair); per[pair].extend(tr); per_s[s].extend(tr)
            print(f"{pair} bars={n} trades={len(per[pair])}",flush=True)
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    allt=[t for tr in per.values() for t in tr]
    ext=np.array([x for _,x in allt])
    edges=EDGES or [float(np.quantile(ext,q)) for q in (0.25,0.5,0.75)]
    print(f"\n===== LATE-BREAKOUT FILTER, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    print("edges (ATR from EMA20):", ",".join(f"{e:.3f}" for e in edges))
    table("ALL", allt, edges)
    for s in STRATS: table(s, per_s[s], edges)
asyncio.run(main())

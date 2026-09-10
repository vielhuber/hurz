"""Market breadth at a 1h signal: how much of the universe already leans the signal's way.

Peer confirmation (section 116) asked whether same-class instruments
broke out together and trend alignment (section 173) whether the
instrument sat beyond its own EMA(200); neither read the market as a
whole. Replays the three live 1h trend strategies on the router-passed
path at the live 2-ATR stop over the whole tradeable universe and tags
every trade with the directional breadth at its signal bar: the mean
over the other 25 instruments of sign(close - EMA200 of 1h closes),
each carried forward from its latest bar at or before the signal,
multiplied by the signal's direction. +1 means every other instrument
leans the signal's way, -1 that all lean against it. The primary split
is breadth >= 0 against < 0; quartile edges are fixed on the recent
year and applied unchanged to the older sample (EDGES env, comma
separated). DAYS_FROM / DAYS_TO select the history window; DUMP writes
the raw (r, feature) pairs. See docs/EDGE_FINDINGS.md section 178.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
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
DUMP=os.getenv("DUMP","")
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
EMA_SPAN=200
PAGE_DAYS=35; PAGE_PAUSE=0.5


def lean(df):
    """sign(close - causal EMA200) keyed by bar time; NaN inside the warmup."""
    e=df["close"].ewm(span=EMA_SPAN, adjust=False).mean().values.astype(float)
    s=np.sign(df["close"].values.astype(float)-e); s[:EMA_SPAN]=np.nan
    return pd.Series(s, index=pd.to_datetime(df["timestamp"],utc=True))

def run(df, entries, pair, feat_col):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; X=df[feat_col].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        if not np.isfinite(X[e]): continue
        feat=d*float(X[e])
        entry=float(C[e]); stop_d=STOP_ATR*atr
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
        if r is not None: out.append((r,feat))
    return out

def stats(a):
    a=np.asarray(a,dtype=float)
    if len(a)<2: return len(a), float('nan'), float('nan')
    return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

def row(label, m, r):
    n,mu,se=stats(r[m]); n2,mu2,se2=stats(r[~m])
    if n<2 or n2<2: return
    diff=mu-mu2; t=diff/np.sqrt(se**2+se2**2)
    print(f"{label:<28}{n:>6}{mu:>+9.4f}{mu/se:>+7.2f}{mu2:>+11.4f}{diff:>+9.4f}{t:>+8.2f}")

def table(name, tr, edges):
    feat=np.array([x for _,x in tr]); r=np.array([x for x,_ in tr])
    print(f"\n--- {name}: n={len(r)} E[R]={r.mean():+.4f} breadth median={np.median(feat):+.2f}  share against (< 0)={np.mean(feat<0)*100:.0f}%")
    print(f"{'bucket (directional breadth)':<28}{'n':>6}{'E[R]':>9}{'t':>7}{'rest E[R]':>11}{'diff':>9}{'t_diff':>8}")
    row("against the market (< 0)", feat<0, r)
    row("strongly against (< -0.5)", feat<-0.5, r)
    row("strongly with (>= 0.5)", feat>=0.5, r)
    bounds=[-np.inf]+edges+[np.inf]
    for i in range(len(bounds)-1):
        row(f"[{bounds[i]:>6.2f}, {bounds[i+1]:>6.2f})", (feat>=bounds[i])&(feat<bounds[i+1]), r)

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
    dfs={}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if bars: dfs[pair]=add_indicators(_bars_to_df(bars))
            print(f"{pair} bars={len(bars) if bars else 0}",flush=True)
    finally:
        await p.disconnect()
    leans=pd.concat({pair:lean(df) for pair,df in dfs.items()},axis=1).sort_index().ffill()
    total=leans.sum(axis=1, min_count=1); count=leans.notna().sum(axis=1)
    per={}; per_s={s:[] for s in STRATS}
    for pair,df in dfs.items():
        ts=pd.to_datetime(df["timestamp"],utc=True)
        own=leans[pair].reindex(ts).values
        others=((total.reindex(ts).values-np.nan_to_num(own))/(count.reindex(ts).values-np.isfinite(own)))
        df["breadth"]=others
        n=len(df); seg=n//SEG; per[pair]=[]
        for s in STRATS:
            st=get_strategy(s)
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                sg=[(x.index,x.direction) for x in st(sdf,{})
                    if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                tr=run(sdf,sg,pair,"breadth"); per[pair].extend(tr); per_s[s].extend(tr)
        print(f"{pair} trades={len(per[pair])}",flush=True)
    allt=[t for tr in per.values() for t in tr]
    if DUMP:
        np.savez(DUMP, r=np.array([x for x,_ in allt]), feat=np.array([x for _,x in allt]))
    feat=np.array([x for _,x in allt])
    edges=EDGES or [float(np.quantile(feat,q)) for q in (0.25,0.5,0.75)]
    print(f"\n===== DIRECTIONAL MARKET BREADTH AT THE 1H SIGNAL, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    print("edges (breadth):", ",".join(f"{e:.3f}" for e in edges))
    table("ALL", allt, edges)
    for s in STRATS: table(s, per_s[s], edges)
asyncio.run(main())

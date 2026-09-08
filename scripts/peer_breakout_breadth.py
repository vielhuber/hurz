"""Peer confirmation: breakouts of the same class in the same direction.

A breakout on one instrument may be part of a move across its class (a
dollar move across the FX pairs, a risk-on day across the indices) or a
lone break. Replays the three live 1h trend strategies on the
router-passed path at the live 2-ATR stop over the whole tradeable
universe and buckets every trade by how many *other* instruments of the
same class (fx / index / crypto / commodity) produced a router-passed
signal of any live strategy in the same direction during the 24 bars up
to and including the signal bar. Signals count whether or not the
simulator could take them. Buckets are fixed a priori: 0, 1, 2, >= 3
peers. DAYS_FROM / DAYS_TO select the history window; DUMP writes the raw
(r, feature) pairs. See docs/EDGE_FINDINGS.md section 116.
"""
import asyncio, os, sys, bisect
from datetime import datetime, timedelta, timezone
from collections import defaultdict
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

CLASSES={"crypto":["BTCUSD","ETHUSD"],
         "fx":["EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY"],
         "index":["DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225"],
         "commodity":["OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"]}
CLASS_OF={p:c for c,ps in CLASSES.items() for p in ps}
PAIRS=[p for ps in CLASSES.values() for p in ps]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
DUMP=os.getenv("DUMP","")
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"; WINDOW_H=24
PAGE_DAYS=35; PAGE_PAUSE=0.5

def run(df, entries, pair, peers):
    """peers: dict direction -> sorted list of (timestamp ns, pair) signals of the class."""
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; T=pd.to_datetime(df["timestamp"],utc=True).values.astype("datetime64[ns]").astype(np.int64)
    win=WINDOW_H*3600*10**9
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        ts=T[e]; times,names=peers[d]
        lo=bisect.bisect_left(times, ts-win); hi=bisect.bisect_right(times, ts)
        feat=len({names[i] for i in range(lo,hi) if names[i]!=pair})
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
    print(f"{label:<24}{n:>6}{mu:>+9.4f}{mu/se:>+7.2f}{mu2:>+11.4f}{diff:>+9.4f}{t:>+8.2f}")

def table(name, tr):
    feat=np.array([x for _,x in tr]); r=np.array([x for x,_ in tr])
    if len(r)<2: return
    print(f"\n--- {name}: n={len(r)} E[R]={r.mean():+.4f} peers median={np.median(feat):.0f} share>=1={np.mean(feat>=1)*100:.0f}%")
    print(f"{'same-direction peers':<24}{'n':>6}{'E[R]':>9}{'t':>7}{'rest E[R]':>11}{'diff':>9}{'t_diff':>8}")
    row("0 (lone break)", feat==0, r); row("1", feat==1, r); row("2", feat==2, r); row(">= 3", feat>=3, r)
    row(">= 1 (confirmed)", feat>=1, r); row(">= 2", feat>=2, r)

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
    segs={}   # pair -> list of (strategy, sdf, signals)
    sigs=defaultdict(lambda: defaultdict(list))  # class -> direction -> [(ts, pair)]
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: print(pair,"no history",flush=True); continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; segs[pair]=[]
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    segs[pair].append((s,sdf,sg))
                    T=pd.to_datetime(sdf["timestamp"],utc=True).values.astype("datetime64[ns]").astype(np.int64)
                    for i,d in sg: sigs[CLASS_OF[pair]][d].append((int(T[i]),pair))
            print(f"{pair} bars={n} signals={sum(len(sg) for _,_,sg in segs[pair])}",flush=True)
    finally:
        await p.disconnect()
    peers_by_class={}
    for c,dd in sigs.items():
        peers_by_class[c]={}
        for d in (1,-1):
            lst=sorted(dd.get(d,[])); peers_by_class[c][d]=([t for t,_ in lst],[q for _,q in lst])
    per_s={s:[] for s in STRATS}; per_c={c:[] for c in CLASSES}; allt=[]
    for pair,items in segs.items():
        peers=peers_by_class[CLASS_OF[pair]]
        for s,sdf,sg in items:
            tr=run(sdf,sg,pair,peers); per_s[s].extend(tr); per_c[CLASS_OF[pair]].extend(tr); allt.extend(tr)
    if DUMP:
        np.savez(DUMP, r=np.array([x for x,_ in allt]), feat=np.array([x for _,x in allt]))
    print(f"\n===== PEER CONFIRMATION (same class, same direction, {WINDOW_H} bars), router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allt)
    for s in STRATS: table(s, per_s[s])
    for c in CLASSES: table(f"class {c}", per_c[c])
asyncio.run(main())

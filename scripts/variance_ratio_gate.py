"""Return autocorrelation at a 1h signal: the variance ratio of the month before.

The router gates on ADX, a 14-bar directional statistic; whether the
instrument's own returns have been trending or mean-reverting on the
month scale had never been read. Replays the three live 1h trend
strategies on the router-passed path at the live 2-ATR stop over the
whole tradeable universe and tags every trade with the Lo-MacKinlay
variance ratio at q = 24 of the 1h log returns over the 720 bars before
the signal bar: var of overlapping 24-bar return sums divided by 24
times the var of 1-bar returns. VR > 1 means positively autocorrelated
(trending) returns, VR < 1 mean-reverting ones; nothing after the
signal is seen and the first month of each window carries no feature.
The primary split is VR < 1 against VR >= 1; quartile edges are fixed
on the recent year and applied unchanged to the older sample (EDGES
env, comma separated). DAYS_FROM / DAYS_TO select the history window;
DUMP writes the raw (r, feature) pairs to a file. See
docs/EDGE_FINDINGS.md section 176.
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
DUMP=os.getenv("DUMP","")
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
LOOKBACK=720; Q=24
PAGE_DAYS=35; PAGE_PAUSE=0.5


def variance_ratio(df):
    """Causal VR(24) of 1h log returns over the 720 bars before each bar; NaN inside the warmup."""
    c=df["close"].values.astype(float)
    r=np.full(len(c),np.nan); r[1:]=np.diff(np.log(c))
    out=np.full(len(c),np.nan)
    for i in range(LOOKBACK+1,len(c)):
        w=r[i-LOOKBACK:i]
        if not np.all(np.isfinite(w)): continue
        v1=w.var(ddof=1)
        if v1<=0: continue
        s=np.convolve(w,np.ones(Q),mode="valid")
        out[i]=s.var(ddof=1)/(Q*v1)
    return out

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; X=df["vr"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        feat=X[e]
        if not np.isfinite(feat): continue
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
    print(f"{label:<26}{n:>6}{mu:>+9.4f}{mu/se:>+7.2f}{mu2:>+11.4f}{diff:>+9.4f}{t:>+8.2f}")

def table(name, tr, edges):
    feat=np.array([x for _,x in tr]); r=np.array([x for x,_ in tr])
    print(f"\n--- {name}: n={len(r)} E[R]={r.mean():+.4f} VR median={np.median(feat):.3f}  share VR<1={np.mean(feat<1)*100:.0f}%")
    print(f"{'bucket (VR24, prior month)':<26}{'n':>6}{'E[R]':>9}{'t':>7}{'rest E[R]':>11}{'diff':>9}{'t_diff':>8}")
    row("mean-reverting (VR < 1)", feat<1, r)
    row("strongly MR (VR < 0.8)", feat<0.8, r)
    row("trending (VR >= 1.2)", feat>=1.2, r)
    bounds=[-np.inf]+edges+[np.inf]
    for i in range(len(bounds)-1):
        row(f"[{bounds[i]:>6.3f}, {bounds[i+1]:>6.3f})", (feat>=bounds[i])&(feat<bounds[i+1]), r)

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
    per={}; per_s={s:[] for s in STRATS}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: continue
            df=add_indicators(_bars_to_df(bars)); df["vr"]=variance_ratio(df); n=len(df); seg=n//SEG; per[pair]=[]
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    tr=run(sdf,sg,pair); per[pair].extend(tr); per_s[s].extend(tr)
            print(f"{pair} bars={n} trades={len(per[pair])}",flush=True)
    finally:
        await p.disconnect()
    allt=[t for tr in per.values() for t in tr]
    if DUMP:
        np.savez(DUMP, r=np.array([x for x,_ in allt]), feat=np.array([x for _,x in allt]),
                 **{f"r_{s}":np.array([x for x,_ in per_s[s]]) for s in STRATS},
                 **{f"feat_{s}":np.array([x for _,x in per_s[s]]) for s in STRATS})
    feat=np.array([x for _,x in allt])
    edges=EDGES or [float(np.quantile(feat,q)) for q in (0.25,0.5,0.75)]
    print(f"\n===== VARIANCE RATIO (q=24, prior 720 bars) AT THE 1H SIGNAL, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    print("edges (VR):", ",".join(f"{e:.3f}" for e in edges))
    table("ALL", allt, edges)
    for s in STRATS: table(s, per_s[s], edges)
asyncio.run(main())

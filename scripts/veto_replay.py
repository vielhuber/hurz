"""Live-expectancy veto replay per (strategy, pair) on the cost-charging simulator.

Retires a combo once its running mean R over at least N trades is at or
below a threshold, for the live rule and three variants, and measures
what the retired trades would have returned. DAYS_FROM / DAYS_TO select
the history window. See docs/EDGE_FINDINGS.md section 98.
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
STOPS = [2.0]
RULES=[(8,-0.15),(8,-0.30),(16,-0.15),(30,-0.10)]
TO={x:0 for x in STOPS}
STRATS = ["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; HOLD=24; PLAT="capital_com"

def run_trades(df, entries, pair, STOP_ATR, strat=''):
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
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD; pass
        if r is not None: rs.append((r, df['timestamp'].iloc[e], pair, strat))
    return rs

def stats(a):
    a=np.asarray([x[0] for x in a]); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))
def costs(a):
    return float(np.mean([x[1] for x in a]))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={P:[] for P in STOPS}; strats=[get_strategy(x) for x in STRATS]
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
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                for sname,st in zip(STRATS,strats):
                    sg=[(x.index,x.direction) for x in st(sdf,{})]
                    res[2.0].extend(run_trades(sdf,sg,pair,2.0,sname))
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    trades=sorted(res[2.0], key=lambda x: x[1])
    print(f"\n===== LIVE-EXPECTANCY VETO REPLAY per (strategy, pair) ({DAYS_FROM}-{DAYS_TO} d) =====")
    base=sum(t[0] for t in trades); print(f"no veto: n={len(trades)} sumR={base:+.1f} E[R]={base/len(trades):+.4f}")
    import collections
    for mn,thr in RULES:
        hist=collections.defaultdict(list); kept=[]; blocked=[]; retired=set()
        for r,ts,pair,strat in trades:
            key=(strat,pair)
            if key in retired: blocked.append(r); continue
            kept.append(r); hist[key].append(r)
            if len(hist[key])>=mn and np.mean(hist[key])<=thr: retired.add(key)
        nb=len(blocked); mb=np.mean(blocked) if nb else 0; sb=(np.std(blocked,ddof=1)/np.sqrt(nb)) if nb>1 else float('nan')
        print(f"rule >= {mn:>2} trades & mean <= {thr:+.2f}: retired combos={len(retired):>2} | blocked n={nb:>5} E[R]_blocked={mb:+.4f} t={mb/sb if nb>1 else 0:+.2f} | kept n={len(kept)} E[R]_kept={np.mean(kept):+.4f} | delta vs no veto={sum(kept)-base:+.1f}")
asyncio.run(main())

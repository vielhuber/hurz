"""Friday-afternoon entries against the rest with gap-aware stops on the cost-charging simulator.

A bar that opens beyond the stop books the open rather than the stop.
Three live trend strategies, ten instruments, 2-ATR stop. DAYS_FROM /
DAYS_TO select the history window. See docs/EDGE_FINDINGS.md section 75.
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

def run(df, sigs, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    H=df["high"].values; L=df["low"].values; O=df["open"].values; C=df["close"].values; A=df["atr_14"].values; T=df["timestamp"]
    for sig in sigs:
        e=sig.index; d=sig.direction
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d; cost_r=2.0*fee*entry/stop_d
        r=None; gapped=False
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            o=O[b]
            if (d==1 and o<=sl) or (d==-1 and o>=sl): r=(o-entry)*d/stop_d-cost_r; gapped=True; in_until=b; break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None:
            t=T.iloc[e]; fri=(t.weekday()==4 and t.hour>=12)
            out.append((r,fri,gapped))
    return out

def stats(a):
    a=np.asarray(a); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    allt=[]; per={}
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
                    sdf=df.iloc[lo_:hi].reset_index(drop=True); tr=run(sdf,st(sdf,{}),pair); per[pair].extend(tr); allt.extend(tr)
            print(f"{pair} bars={n}",flush=True); await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== FRIDAY-AFTERNOON ENTRIES vs REST, gap-aware ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'pair':<10}{'n_fri':>6}{'E_fri':>9}{'gap%':>6}{'n_rest':>7}{'E_rest':>9}{'gap%':>6}{'fri-rest':>10}{'t':>7}")
    for name,tr in list(per.items())+[("POOLED",allt)]:
        f=[x for x in tr if x[1]]; o=[x for x in tr if not x[1]]
        nf,mf,sf=stats([x[0] for x in f]); no,mo,so=stats([x[0] for x in o])
        print(f"{name:<10}{nf:>6}{mf:>+9.4f}{np.mean([x[2] for x in f])*100:>6.1f}{no:>7}{mo:>+9.4f}{np.mean([x[2] for x in o])*100:>6.1f}{mf-mo:>+10.4f}{(mf-mo)/np.sqrt(sf**2+so**2):>+7.2f}")
    g=[x[0] for x in allt if x[2]]
    print(f"gapped stop-outs overall: n={len(g)} mean R={np.mean(g) if g else float('nan'):+.3f} (a clean stop is -1 minus cost)")
asyncio.run(main())

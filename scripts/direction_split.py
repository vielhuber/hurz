"""Long versus short by asset class on the cost-charging walk-forward simulator.

Replays the three live 1h trend strategies on the router-passed path at
the live 2-ATR stop over the whole tradeable universe and splits every
trade by its direction within its asset class. DAYS_FROM / DAYS_TO select
the history window so a disjoint sample can be run as the independent
check. See docs/EDGE_FINDINGS.md section 109.
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
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

CLASSES={
    "crypto":["BTCUSD","ETHUSD"],
    "fx":["EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY"],
    "index":["DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225"],
    "commodity":["OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"],
}
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values; A=df["atr_14"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
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
        if r is not None: out.append((r,d))
    return out

def stats(a):
    a=np.asarray(a,dtype=float)
    if len(a)<2: return len(a), float('nan'), float('nan')
    return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

def row(name, tr):
    lo=[r for r,d in tr if d==1]; sh=[r for r,d in tr if d==-1]
    nl,ml,sl=stats(lo); ns,ms,ss=stats(sh)
    diff=ml-ms; t=diff/np.sqrt(sl**2+ss**2)
    print(f"{name:<14}{nl:>6}{ml:>+9.4f}{ml/sl:>+7.2f}{ns:>6}{ms:>+9.4f}{ms/ss:>+7.2f}{diff:>+10.4f}{t:>+7.2f}")

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    per={}
    try:
        for cls,pairs in CLASSES.items():
            for pair in pairs:
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
                        sg=[(x.index,x.direction) for x in st(sdf,{}) if not gate(s,sdf,x.index).blocked]
                        per[pair].extend(run(sdf,sg,pair))
                print(f"{pair} bars={n} trades={len(per[pair])}",flush=True)
                await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== LONG vs SHORT by asset class, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'class/pair':<14}{'n_L':>6}{'E_L':>9}{'t_L':>7}{'n_S':>6}{'E_S':>9}{'t_S':>7}{'L-S':>10}{'t':>7}")
    row("ALL",[t for tr in per.values() for t in tr])
    for cls,pairs in CLASSES.items():
        row(cls.upper(),[t for pp in pairs if pp in per for t in per[pp]])
        for pp in pairs:
            if pp in per: row("  "+pp,per[pp])
asyncio.run(main())

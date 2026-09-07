"""ADX router at the live 2-ATR stop on the cost-charging walk-forward simulator.

Passed versus rejected versus count-matched random entries for the three
live trend strategies on ten instruments. DAYS_FROM / DAYS_TO select the
history window. See docs/EDGE_FINDINGS.md section 67.
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
from app.spot_trading.regime import gate
from scripts.spot_backtest import _fee_for, _venue_min_distance
from scripts.walk_forward import _bars_to_df

PAIRS = ["BTCUSD","ETHUSD","OIL_CRUDE","OIL_BRENT","GOLD","DE40","US500","US30","EURUSD","AUDUSD"]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
rng=np.random.default_rng(9)

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
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
        if r is not None: out.append(r)
    return out

def stats(a):
    a=np.asarray(a); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={s:{"all":[],"passed":[],"rejected":[],"rand":[]} for s in STRATS}
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
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True); sigs=st(sdf,{})
                    passed=[(x.index,x.direction) for x in sigs if not gate(s,sdf,x.index).blocked]
                    rejected=[(x.index,x.direction) for x in sigs if gate(s,sdf,x.index).blocked]
                    res[s]["all"].extend(run(sdf,[(x.index,x.direction) for x in sigs],pair))
                    res[s]["passed"].extend(run(sdf,passed,pair)); res[s]["rejected"].extend(run(sdf,rejected,pair))
                    for _ in range(3):
                        idx=rng.integers(60,len(sdf)-1,size=len(sigs)); dirs=rng.choice([-1,1],size=len(sigs))
                        res[s]["rand"].extend(run(sdf,list(zip(idx.tolist(),dirs.tolist())),pair))
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== ADX ROUTER AT 2-ATR STOP ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'strategy':<19}{'n_all':>6}{'E_all':>8}{'n_pass':>7}{'E_pass':>8}{'n_rej':>6}{'E_rej':>8}{'pass-rej':>9}{'t':>6}{'E_rand':>8}{'all-rand':>9}{'t':>6}{'pass-rand':>10}{'t':>6}")
    for s in STRATS+["POOLED"]:
        g=lambda k: res[s][k] if s!="POOLED" else [r for ss in STRATS for r in res[ss][k]]
        na,ma,sa=stats(g("all")); npn,mp,sp=stats(g("passed")); nj,mj,sj=stats(g("rejected")); nr,mr,sr=stats(g("rand"))
        print(f"{s:<19}{na:>6}{ma:>+8.4f}{npn:>7}{mp:>+8.4f}{nj:>6}{mj:>+8.4f}{mp-mj:>+9.4f}{(mp-mj)/np.sqrt(sp**2+sj**2):>+6.2f}{mr:>+8.4f}{ma-mr:>+9.4f}{(ma-mr)/np.sqrt(sa**2+sr**2):>+6.2f}{mp-mr:>+10.4f}{(mp-mr)/np.sqrt(sp**2+sr**2):>+6.2f}")
asyncio.run(main())

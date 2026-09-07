"""ADX trend threshold sweep at the live 2-ATR stop on the cost-charging walk-forward simulator.

Router-passed expectancy per threshold against random entries drawn
from the same passing bars. DAYS_FROM / DAYS_TO select the history
window. See docs/EDGE_FINDINGS.md section 71.
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
rng=np.random.default_rng(13)
THRESH=[20,25,30,35]

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
    res={t:{"passed":[],"rand":[]} for t in THRESH}
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
                    for t in THRESH:
                        os.environ["HURZ_REGIME_ADX_TREND"]=str(t); os.environ["HURZ_REGIME_ADX_TREND_CORE"]=str(t)
                        passed=[(x.index,x.direction) for x in sigs if not gate(s,sdf,x.index).blocked]
                        res[t]["passed"].extend(run(sdf,passed,pair))
                        ok=[i for i in range(60,len(sdf)-1) if not gate(s,sdf,i).blocked]
                        if ok and passed:
                            for _ in range(3):
                                idx=rng.choice(ok,size=len(passed)); dirs=rng.choice([-1,1],size=len(passed))
                                res[t]["rand"].extend(run(sdf,list(zip(idx.tolist(),dirs.tolist())),pair))
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== ADX TREND THRESHOLD AT 2-ATR STOP ({DAYS_FROM}-{DAYS_TO} d) — diff vs 30 (live), Welch t =====")
    nb,mb,sb=stats(res[30]["passed"])
    print(f"{'adx>=':>6}{'n_pass':>7}{'E_pass':>9}{'t0':>7}{'E_rand':>9}{'pass-rand':>10}{'t':>6}{'diff30':>9}{'t':>6}{'sumR':>8}")
    for t in THRESH:
        n,m,se=stats(res[t]["passed"]); nr,mr,sr=stats(res[t]["rand"])
        print(f"{t:>6}{n:>7}{m:>+9.4f}{m/se:>+7.2f}{mr:>+9.4f}{m-mr:>+10.4f}{(m-mr)/np.sqrt(se**2+sr**2):>+6.2f}{m-mb:>+9.4f}{(m-mb)/np.sqrt(se**2+sb**2) if t!=30 else 0:>+6.2f}{m*n:>+8.1f}")
asyncio.run(main())

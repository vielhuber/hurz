"""GOLD per strategy with the live ADX router applied, on the cost-charging walk-forward simulator.

Signals and a count-matched random control drawn from router-passing
bars, four strategies, 2-ATR stop. DAYS_FROM / DAYS_TO select the
history window. See docs/EDGE_FINDINGS.md section 66.
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

PAIR="GOLD"; STRATS=["donchian_breakout","turtle_breakout","keltner_breakout","momentum"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
rng=np.random.default_rng(5)

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
    a=np.asarray(a); return len(a), a.mean(), (a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float("nan"))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={s:{"all":[],"passed":[],"rand":[],"rand_passed":[]} for s in STRATS}
    try:
        bars=None
        for attempt in range(4):
            try:
                bars=await p.fetch_history(PAIR, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
            except Exception as ex:
                print("FETCH FAIL",attempt,str(ex)[:80],flush=True); await asyncio.sleep(3)
    finally:
        await p.disconnect()
    df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
    print(f"{PAIR} bars={n}")
    for s in STRATS:
        st=get_strategy(s)
        for k in range(SEG):
            lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
            sdf=df.iloc[lo_:hi].reset_index(drop=True); sigs=st(sdf,{})
            sg=[(x.index,x.direction) for x in sigs]
            passed=[(x.index,x.direction) for x in sigs if not gate(s,sdf,x.index).blocked]
            res[s]["all"].extend(run(sdf,sg,PAIR)); res[s]["passed"].extend(run(sdf,passed,PAIR))
            ok=[i for i in range(60,len(sdf)-1) if not gate(s,sdf,i).blocked]
            for _ in range(5):
                idx=rng.integers(60,len(sdf)-1,size=len(sg)); dirs=rng.choice([-1,1],size=len(sg))
                res[s]["rand"].extend(run(sdf,list(zip(idx.tolist(),dirs.tolist())),PAIR))
                if ok and passed:
                    idx=rng.choice(ok,size=len(passed)); dirs=rng.choice([-1,1],size=len(passed))
                    res[s]["rand_passed"].extend(run(sdf,list(zip(idx.tolist(),dirs.tolist())),PAIR))
    print(f"\n===== GOLD PER STRATEGY, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'strategy':<19}{'n_all':>6}{'E_all':>8}{'vs_rand':>9}{'t':>6}{'n_pass':>7}{'E_pass':>8}{'t0':>6}{'vs_randP':>9}{'t':>6}")
    for s in STRATS:
        na,ma,sa=stats(res[s]["all"]); nr,mr,sr=stats(res[s]["rand"]); npn,mp,sp=stats(res[s]["passed"]) if res[s]["passed"] else (0,float("nan"),float("nan"))
        nq,mq,sq=stats(res[s]["rand_passed"]) if res[s]["rand_passed"] else (0,float("nan"),float("nan"))
        print(f"{s:<19}{na:>6}{ma:>+8.3f}{ma-mr:>+9.3f}{(ma-mr)/np.sqrt(sa**2+sr**2):>+6.2f}{npn:>7}{mp:>+8.3f}{mp/sp if npn>1 else float('nan'):>+6.2f}{mp-mq:>+9.3f}{(mp-mq)/np.sqrt(sp**2+sq**2) if npn>1 and nq>1 else float('nan'):>+6.2f}")
asyncio.run(main())

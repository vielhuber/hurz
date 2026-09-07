"""Channel-length sweep for donchian_breakout on the cost-charging walk-forward simulator.

Periods 10 / 20 / 40 / 80 against the live 20 on the ten unblocked
instruments. DAYS_FROM / DAYS_TO select the history window. See
docs/EDGE_FINDINGS.md section 58.
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
PERIODS = [10, 20, 40, 80]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=1.0; HOLD=24; PLAT="capital_com"

def run_trades(df, entries, pair):
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
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None: rs.append(r)
    return rs

def stats(a):
    a=np.asarray(a); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={P:[] for P in PERIODS}; strat=get_strategy("donchian_breakout")
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
                for P in PERIODS:
                    res[P].extend(run_trades(sdf,[(x.index,x.direction) for x in strat(sdf,{"period":P})],pair))
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== DONCHIAN PERIOD SWEEP ({DAYS_FROM}-{DAYS_TO} d) — diff vs period 20 (live), Welch t =====")
    nb,mb,sb=stats(res[20])
    print(f"{'period':>7}{'n':>6}{'E[R]':>9}{'t0':>7}{'win%':>7}{'sumR':>8}{'diff':>9}{'t_diff':>8}")
    for P in PERIODS:
        n,mu,se=stats(res[P]); d=mu-mb; t=d/np.sqrt(se**2+sb**2) if P!=20 else 0.0
        print(f"{P:>7}{n:>6}{mu:>+9.4f}{mu/se:>+7.2f}{(np.asarray(res[P])>0).mean()*100:>7.1f}{mu*n:>+8.1f}{d:>+9.4f}{t:>+8.2f}")
asyncio.run(main())

"""Minimum-size skips and planned risk at the 2-ATR stop against the former 1-ATR stop.

Uses the live broker constraints and the live sizing function on the
cost-charging walk-forward simulator. DAYS_FROM / DAYS_TO select the
history window. See docs/EDGE_FINDINGS.md section 64.
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
from app.spot_trading.position_sizing import calculate_position_size, DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD
from scripts.spot_backtest import _fee_for, _venue_min_distance
from scripts.walk_forward import _bars_to_df

PAIRS = ["BTCUSD","ETHUSD","OIL_CRUDE","OIL_BRENT","GOLD","DE40","US500","US30","EURUSD","AUDUSD"]
STRATS = ["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; HOLD=24; PLAT="capital_com"

def run(df, entries, pair, STOP_ATR, cons):
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
        sz=calculate_position_size(entry_price=entry, stop_loss=sl, target_risk=DEFAULT_TARGET_RISK_USD, notional_cap=DEFAULT_NOTIONAL_CAP_USD, min_size=cons.min_size, size_increment=cons.size_increment)
        r=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None: out.append((r, sz.skipped, sz.planned_risk or 0.0, stop_d/entry*100))
    return out

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={}
    try:
        for pair in PAIRS:
            cons=await p.order_constraints(pair)
            bars=None
            for attempt in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
                except Exception as ex:
                    print(pair,"FETCH FAIL",attempt,str(ex)[:80],flush=True); await asyncio.sleep(3)
            if bars is None: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            res[pair]={1.0:[],2.0:[],"cons":cons}
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True); sg=[(x.index,x.direction) for x in st(sdf,{})]
                    for sa in (1.0,2.0): res[pair][sa].extend(run(sdf,sg,pair,sa,cons))
            print(f"{pair} bars={n} min_size={cons.min_size} step={cons.size_increment}",flush=True)
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== MIN-SIZE SKIPS AND PLANNED RISK ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'pair':<10}{'stop':>5}{'n':>6}{'skip%':>7}{'risk$':>7}{'stop%':>7}{'E_all':>8}{'E_kept':>8}{'E_skip':>8}{'E$/trade':>9}")
    tot={1.0:[],2.0:[]}
    for pair in res:
        for sa in (1.0,2.0):
            a=res[pair][sa]; tot[sa].extend(a)
            r=np.array([x[0] for x in a]); sk=np.array([x[1] for x in a]); rk=np.array([x[2] for x in a]); sp=np.array([x[3] for x in a])
            kept=r[~sk]; skip=r[sk]
            print(f"{pair:<10}{sa:>5.1f}{len(r):>6}{sk.mean()*100:>7.1f}{rk[~sk].mean() if (~sk).any() else 0:>7.2f}{sp.mean():>7.2f}{r.mean():>+8.4f}{kept.mean() if len(kept) else 0:>+8.4f}{skip.mean() if len(skip) else 0:>+8.4f}{(kept*rk[~sk]).mean() if len(kept) else 0:>+9.3f}")
    for sa in (1.0,2.0):
        a=tot[sa]; r=np.array([x[0] for x in a]); sk=np.array([x[1] for x in a]); rk=np.array([x[2] for x in a])
        kept=r[~sk]
        print(f"{'POOLED':<10}{sa:>5.1f}{len(r):>6}{sk.mean()*100:>7.1f}{rk[~sk].mean():>7.2f}{'':>7}{r.mean():>+8.4f}{kept.mean():>+8.4f}{r[sk].mean() if sk.any() else 0:>+8.4f}{(kept*rk[~sk]).mean():>+9.3f}  usd/yr(kept)={(kept*rk[~sk]).sum():+.1f}")
asyncio.run(main())

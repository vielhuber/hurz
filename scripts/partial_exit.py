"""Partial take-profit on the live 1h trend trades: half out at +1 R.

The live exit is all-or-nothing: the whole position runs to the 1.5 R
target, the stop or the 24-bar leash. Sections 131 and 143 read that
the barriers lose and the drift wins, which asks whether banking half
of the position once it has travelled one stop distance in favour and
letting the rest run changes the expectancy. Replays the three live 1h
trend strategies on the router-passed path at the live 2-ATR stop over
the whole tradeable universe and, on the same bar path of every trade,
books three rules: the live rule, half out at +1 R with the remainder
unchanged (A), and half out at +1 R with the remainder's target at
2.5 R (B). The trade set is fixed by the live rule, so the differences
are paired per trade. DAYS_FROM / DAYS_TO select the history window.
See docs/EDGE_FINDINGS.md section 174.
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
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
# (partial level in R or None, target of the remainder in R)
RULES=[("live",None,RR),("A half@1R",1.0,RR),("B half@1R rest 2.5R",1.0,2.5)]
PAGE_DAYS=35; PAGE_PAUSE=0.5


def book(O,H,L,C,e,d,entry,stop_d,cost_r,part,rr_rest,n):
    """R of one trade under one rule; returns (r, exit bar, partial taken)."""
    took=False
    sl=entry-d*stop_d; tp=entry+d*rr_rest*stop_d
    tp_part=entry+d*part*stop_d if part is not None else None
    def blend(rest): return (0.5*part+0.5*rest if took else rest)-cost_r
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return blend(gap/stop_d), b, took
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return blend(-1.0), b, took
        if tp_part is not None and not took and ((d==1 and favor>=tp_part) or (d==-1 and favor<=tp_part)): took=True
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return blend(rr_rest), b, took
    if e+HOLD<n: return blend((float(C[e+HOLD])-entry)*d/stop_d), e+HOLD, took
    return None, None, took

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; n=len(df)
    for e,d in sorted(entries):
        if e<=in_until or e>=n: continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        rs=[]; took=False
        for _,part,rr_rest in RULES:
            r,xb,tk=book(O,H,L,C,e,d,entry,stop_d,cost_r,part,rr_rest,n)
            if r is None: break
            rs.append(r); took=took or tk
            if part is None: in_until=xb
        if len(rs)==len(RULES): out.append((rs,took))
    return out

def table(name, tr):
    R=np.array([x for x,_ in tr]); took=np.array([t for _,t in tr])
    if len(R)<2: return
    base=R[:,0]; se=base.std(ddof=1)/np.sqrt(len(base))
    print(f"\n--- {name}: n={len(R)} live E[R]={base.mean():+.4f} (t={base.mean()/se:+.2f})  half reached 1R on {took.mean()*100:.0f}% of trades")
    print(f"{'rule':<24}{'E[R]':>9}{'t':>7}{'diff':>9}{'t_pair':>8}{'win%':>7}")
    for i,(lab,_,_) in enumerate(RULES):
        r=R[:,i]; d=r-base; se=r.std(ddof=1)/np.sqrt(len(r))
        sd=d.std(ddof=1); tp=d.mean()/(sd/np.sqrt(len(d))) if sd>0 else float('nan')
        print(f"{lab:<24}{r.mean():>+9.4f}{r.mean()/se:>+7.2f}{d.mean():>+9.4f}{tp:>+8.2f}{np.mean(r>0)*100:>7.1f}")

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
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; per[pair]=[]
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
    print(f"\n===== PARTIAL EXIT AT +1R, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allt)
    for s in STRATS: table(s, per_s[s])
asyncio.run(main())

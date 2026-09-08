"""Holding leash by stop status: trades pinned to the venue minimum versus ATR-bound.

Section 124 showed 78 % of trades pinned to the venue's 1.05 % minimum
at a mean stop of 6.5 ATR. A stop that wide needs more than 24 hourly
bars to resolve, an ATR-bound 2-ATR stop does not. Replays the three
live 1h trend strategies on the router-passed path, venue minimum, live
widening rule, gap-aware stop booking and the commodity short block
over all 26 tradeable instruments at holds 24 / 48 / 72 / 96, and
reports each hold separately for pinned and ATR-bound trades. DAYS_FROM
/ DAYS_TO select the window. See docs/EDGE_FINDINGS.md section 125.
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
HOLDS=[24,48,72,96]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; STOP_ATR=2.0; RR=1.5; PLAT="capital_com"
PAGE_DAYS=35; PAGE_PAUSE=0.5

def run(df, entries, pair, hold):
    """Returns (r, pinned, atr_stop_ratio)."""
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr; pinned=False
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm; pinned=True
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d; pinned=True
            if cost_r>0.10: continue
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        r=None; timeout=False
        for b in range(e+1,e+hold+1):
            if b>=len(df): break
            gap=(O[b]-entry)*d
            if gap<=-stop_d: r=gap/stop_d-cost_r; in_until=b; break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+hold<len(df): r=(float(C[e+hold])-entry)*d/stop_d-cost_r; in_until=e+hold; timeout=True
        if r is not None: out.append((r,pinned,stop_d/atr,timeout))
    return out

def se(a): return a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float('nan')

def table(name, res):
    print(f"\n--- {name}")
    for label,want in (("pinned to the venue floor",True),("ATR-bound",False)):
        base=np.array([x[0] for x in res[24] if x[1]==want])
        if len(base)<2: continue
        ratio=np.array([x[2] for x in res[24] if x[1]==want])
        print(f"  [{label}] n={len(base)} share={len(base)/max(len(res[24]),1)*100:.0f}% stop/ATR mean={ratio.mean():.2f}")
        print(f"  {'hold':<6}{'n':>6}{'E[R]':>9}{'t':>7}{'win%':>7}{'timeout%':>9}{'sum R':>9}{'diff vs 24':>11}{'t_diff':>8}")
        for h in HOLDS:
            r=np.array([x[0] for x in res[h] if x[1]==want]); to=np.array([x[3] for x in res[h] if x[1]==want])
            if len(r)<2: continue
            d=r.mean()-base.mean(); t=d/np.sqrt(se(r)**2+se(base)**2) if h!=24 else 0.0
            print(f"  {h:<6}{len(r):>6}{r.mean():>+9.4f}{r.mean()/se(r):>+7.2f}{(r>0).mean()*100:>7.1f}{to.mean()*100:>9.1f}{r.sum():>+9.1f}{d:>+11.4f}{t:>+8.2f}")

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
    allres={h:[] for h in HOLDS}; per_s={s:{h:[] for h in HOLDS} for s in STRATS}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: print(pair,"no history",flush=True); continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; cnt=0; pin=0
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    for h in HOLDS:
                        tr=run(sdf,sg,pair,h); allres[h].extend(tr); per_s[s][h].extend(tr)
                        if h==24: cnt+=len(tr); pin+=sum(1 for x in tr if x[1])
            print(f"{pair} bars={n} trades={cnt} pinned={pin}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== HOLDING LEASH BY STOP STATUS, router-passed, 2-ATR stop, RR 1.5 ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allres)
    for s in STRATS: table(s, per_s[s])
asyncio.run(main())

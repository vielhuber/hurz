"""Time stop for losers: leave a trade that is under water after K bars.

Section 131 split the book by exit: the stop, pinned at the venue floor
on most trades, is hit twice as often as the target and costs 0.26 R a
trade, while the 24-bar drift is positive. A stop six ATR away protects
late; this variant leaves a trade that is below its entry at the close
of bar K (6 or 12) and books that close, keeping the hard stop and the
target otherwise. Replays the three live 1h trend strategies on the
router-passed path, venue minimum, live widening rule, gap-aware stop
booking and the commodity short block over all 26 tradeable instruments.
DAYS_FROM / DAYS_TO select the window. See docs/EDGE_FINDINGS.md
section 132.
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
COMBOS=[0,6,12,18]   # time stop for losers after K bars; 0 = live (none)
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
PAGE_DAYS=35; PAGE_PAUSE=0.5

def run(df, entries, pair, kbar):
    stop_atr=STOP_ATR; hold=HOLD
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=stop_atr*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
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
            if kbar and b==e+kbar and (float(C[b])-entry)*d<0: r=(float(C[b])-entry)*d/stop_d-cost_r; in_until=b; timeout=True; break
        if r is None and e+hold<len(df): r=(float(C[e+hold])-entry)*d/stop_d-cost_r; in_until=e+hold; timeout=True
        if r is not None: out.append((r,cost_r,timeout,stop_d/entry))
    return out

def se(a): return a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float('nan')

def table(name, res):
    base=np.array([x[0] for x in res[COMBOS[0]]])
    print(f"\n--- {name}")
    print(f"{'time stop':<10}{'n':>6}{'net E[R]':>10}{'t':>7}{'cost R':>8}{'gross':>9}{'early%':>9}{'stop%px':>8}{'sum R':>9}{'diff':>9}{'t_diff':>8}")
    for c in COMBOS:
        r=np.array([x[0] for x in res[c]]); cost=np.array([x[1] for x in res[c]]); to=np.array([x[2] for x in res[c]]); sw=np.array([x[3] for x in res[c]])
        if len(r)<2: continue
        d=r.mean()-base.mean(); t=d/np.sqrt(se(r)**2+se(base)**2) if c!=COMBOS[0] else 0.0
        print(f"{('none' if c==0 else 'K='+str(c)):<10}{len(r):>6}{r.mean():>+10.4f}{r.mean()/se(r):>+7.2f}{cost.mean():>8.4f}{(r+cost).mean():>+9.4f}{to.mean()*100:>9.1f}{sw.mean()*100:>8.2f}{r.sum():>+9.1f}{d:>+9.4f}{t:>+8.2f}")

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
    allres={c:[] for c in COMBOS}; per_s={s:{c:[] for c in COMBOS} for s in STRATS}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: print(pair,"no history",flush=True); continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; cnt=0
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    for c in COMBOS:
                        tr=run(sdf,sg,pair,c); allres[c].extend(tr); per_s[s][c].extend(tr)
                        if c==COMBOS[0]: cnt+=len(tr)
            print(f"{pair} bars={n} live-combo trades={cnt}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== TIME STOP FOR LOSERS vs live (none), router-passed, 2-ATR stop, hold 24 ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allres)
    for s in STRATS: table(s, per_s[s])
asyncio.run(main())

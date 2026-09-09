"""Strategy agreement on the signal bar: one, two or three live strategies firing together.

The live loop takes one entry per instrument and bar; when donchian,
turtle and keltner fire on the same bar in the same direction the
duplicate guard keeps the first and drops the rest. Whether an entry
confirmed by a second strategy carries more than a lone signal had only
been read on 44 live trades (EDGE_FINDINGS 5). Replays the merged
one-position-per-instrument timeline on the router-passed path, live
2-ATR stop, venue minimum, live widening rule, gap-aware stop booking,
commodity short block, live 6-hour stop-out cooldown, over all 26
tradeable instruments, and buckets every entry by the number of live
strategies that fired on its bar in its direction. DAYS_FROM / DAYS_TO
select the window. See docs/EDGE_FINDINGS.md section 128.
"""
import asyncio, os, sys
from collections import Counter
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
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
DUMP=os.getenv("DUMP","")
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"; STOP_COOLDOWN_H=6
PAGE_DAYS=35; PAGE_PAUSE=0.5

def run(df, entries, pair):
    """entries: dict (bar, direction) -> number of strategies. Returns (r, agreement)."""
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values
    T=pd.to_datetime(df["timestamp"],utc=True).values.astype("datetime64[s]").astype(np.int64)
    prev_kind=None; prev_exit_bar=None
    for (e,d),agree in sorted(entries.items()):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        since=(T[e]-T[prev_exit_bar])/3600.0 if prev_exit_bar is not None else float('inf')
        if prev_kind=="stop" and since<=STOP_COOLDOWN_H: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        r=None; kind=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            gap=(O[b]-entry)*d
            if gap<=-stop_d: r=gap/stop_d-cost_r; kind="stop"; in_until=b; break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; kind="stop"; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; kind="target"; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; kind="timeout"; in_until=e+HOLD
        if r is None: continue
        out.append((r,agree)); prev_kind=kind; prev_exit_bar=in_until
    return out

def se(a): return a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float('nan')

def table(name, tr):
    r=np.array([x[0] for x in tr]); k=np.array([x[1] for x in tr])
    if len(r)<2: return
    print(f"\n--- {name}: n={len(r)} E[R]={r.mean():+.4f} lone={np.mean(k==1)*100:.0f}% two={np.mean(k==2)*100:.0f}% three={np.mean(k==3)*100:.0f}%")
    print(f"{'strategies on the bar':<24}{'n':>6}{'E[R]':>9}{'t':>7}{'rest E[R]':>11}{'diff':>9}{'t_diff':>8}")
    for label,m in (("1 (lone)",k==1),("2",k==2),("3 (all)",k==3),(">= 2 (confirmed)",k>=2)):
        if m.sum()<2 or (~m).sum()<2: continue
        a=r[m]; b=r[~m]; d=a.mean()-b.mean(); t=d/np.sqrt(se(a)**2+se(b)**2)
        print(f"{label:<24}{m.sum():>6}{a.mean():>+9.4f}{a.mean()/se(a):>+7.2f}{b.mean():>+11.4f}{d:>+9.4f}{t:>+8.2f}")

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
    allt=[]; per_c={}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: print(pair,"no history",flush=True); continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; cnt=0
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                agree=Counter()
                for s in STRATS:
                    st=get_strategy(s)
                    for x in st(sdf,{}):
                        if gate(s,sdf,x.index).blocked or direction_blocked(pair,x.direction): continue
                        agree[(x.index,x.direction)]+=1
                tr=run(sdf,agree,pair); allt.extend(tr); cnt+=len(tr)
            print(f"{pair} bars={n} trades={cnt}",flush=True)
    finally:
        await p.disconnect()
    if DUMP: np.savez(DUMP, r=np.array([x[0] for x in allt]), agree=np.array([x[1] for x in allt]))
    print(f"\n===== STRATEGY AGREEMENT ON THE SIGNAL BAR, merged timeline, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allt)
asyncio.run(main())

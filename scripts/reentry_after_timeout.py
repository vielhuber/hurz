"""Re-entry after a timed-out exit on the same instrument versus the rest.

Run 16 measured and built in a cooldown after a stop-out. The other exit
that leaves an instrument's regime in doubt is the timeout: a position
held 24 bars without reaching either level, i.e. the break went nowhere.
Merged one-position-per-instrument timeline of the three live 1h trend
strategies, router-passed, commodity short block, live 2-ATR stop, venue
minimum, live widening rule, gap-aware stop booking, and the live 6-hour
stop-out cooldown applied so the baseline is the book as traded. Every
entry is classed by the previous exit on the instrument (timeout / stop /
target) and the hours since it; windows of 6 and 24 hours. DAYS_FROM /
DAYS_TO select the history window. See docs/EDGE_FINDINGS.md section 118.
"""
import asyncio, os, sys
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
WINDOWS=[6,24]
PAGE_DAYS=35; PAGE_PAUSE=0.5

def run(df, entries, pair):
    """Merged timeline. Returns (r, prev_exit_kind, hours_since_prev_exit)."""
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values
    T=pd.to_datetime(df["timestamp"],utc=True).values.astype("datetime64[s]").astype(np.int64)
    prev_kind=None; prev_exit_bar=None
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        since=(T[e]-T[prev_exit_bar])/3600.0 if prev_exit_bar is not None else float('inf')
        if prev_kind=="stop" and since<=STOP_COOLDOWN_H: continue   # live cooldown
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
        out.append((r, prev_kind, since))
        prev_kind=kind; prev_exit_bar=in_until
    return out

def se(a): return a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float('nan')

def table(name, tr):
    r=np.array([x[0] for x in tr]); kind=np.array([x[1] or "none" for x in tr]); since=np.array([x[2] for x in tr])
    if len(r)<2: return
    print(f"\n--- {name}: n={len(r)} E[R]={r.mean():+.4f} exits: timeout {np.mean(kind=='timeout')*100:.0f}% of entries follow one")
    print(f"{'previous exit, window':<28}{'n':>6}{'E[R]':>9}{'t':>7}{'rest E[R]':>11}{'diff':>9}{'t_diff':>8}")
    for k in ("timeout","target","stop"):
        for W in WINDOWS:
            m=(kind==k)&(since<=W)
            if m.sum()<2 or (~m).sum()<2: continue
            a=r[m]; b=r[~m]; d=a.mean()-b.mean(); t=d/np.sqrt(se(a)**2+se(b)**2)
            print(f"{k+' <= '+str(W)+'h':<28}{m.sum():>6}{a.mean():>+9.4f}{a.mean()/se(a):>+7.2f}{b.mean():>+11.4f}{d:>+9.4f}{t:>+8.2f}")
    m=np.isinf(since)|(since>24)
    a=r[m]; b=r[~m]
    if len(a)>1 and len(b)>1:
        d=a.mean()-b.mean(); print(f"{'no exit in prior 24h':<28}{m.sum():>6}{a.mean():>+9.4f}{a.mean()/se(a):>+7.2f}{b.mean():>+11.4f}{d:>+9.4f}{d/np.sqrt(se(a)**2+se(b)**2):>+8.2f}")

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
    allt=[]; per_pair={}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: print(pair,"no history",flush=True); continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; per_pair[pair]=[]
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                merged=set()
                for s in STRATS:
                    st=get_strategy(s)
                    merged.update((x.index,x.direction) for x in st(sdf,{})
                                  if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction))
                tr=run(sdf,sorted(merged),pair); per_pair[pair].extend(tr); allt.extend(tr)
            print(f"{pair} bars={n} trades={len(per_pair[pair])}",flush=True)
    finally:
        await p.disconnect()
    if DUMP:
        np.savez(DUMP, r=np.array([x[0] for x in allt]), kind=np.array([x[1] or "none" for x in allt]), since=np.array([x[2] for x in allt]))
    print(f"\n===== RE-ENTRY AFTER A TIMEOUT vs REST, merged one-position timeline, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allt)
asyncio.run(main())

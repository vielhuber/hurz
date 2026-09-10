"""Index entries priced at the hour they fire: cash hours against off-hours, on the sampled spread table.

Run 37 (section 107) charged a single 04:36 UTC snapshot and left one
cost correction open: sample the venue's spreads by hour, then charge
them in the simulator. The heartbeat sampler of section 108 has since
built that table. This replays the three live 1h trend strategies on
the router-passed path at the live 2-ATR stop over the nine index
instruments and charges every trade the median sampled half-spread of
its signal hour (data/spread_samples.jsonl; the audited table where an
hour has no sample), with the live widening rule and the 10 % ceiling.
Each trade is tagged cash-hours or off-hours by the index's cash
session in UTC. The preregistered question is whether off-hours index
entries, at their true cost, are a bucket to block: significantly
negative at t < -2 on both disjoint samples and |t| > 2 against the
cash-hours rest with the same sign on both. DAYS_FROM / DAYS_TO select
the history window. See docs/EDGE_FINDINGS.md section 179.
"""
import asyncio, json, os, sys
from collections import defaultdict
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

PAIRS=["DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225"]
# Cash session of the underlying, UTC hours of the 1h bars that open inside it.
CASH={"DE40":(7,16),"FR40":(7,16),"UK100":(7,16),"EU50":(7,16),
      "US500":(14,20),"US30":(14,20),"US100":(14,20),"HK50":(2,8),"J225":(0,6)}
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
SAMPLES="data/spread_samples.jsonl"
PAGE_DAYS=35; PAGE_PAUSE=0.5


def hour_table():
    """Median sampled half-spread (fraction of mid) per (pair, UTC hour)."""
    acc=defaultdict(list)
    with open(SAMPLES,encoding="utf-8") as handle:
        for line in handle:
            row=json.loads(line); acc[(row["pair"],int(row["ts"][11:13]))].append(row["half_spread_pct"]/100.0)
    return {k:float(np.median(v)) for k,v in acc.items()}

def run(df, entries, pair, table, hourly):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; hours=pd.to_datetime(df["timestamp"],utc=True).dt.hour.values
    lo,hi=CASH[pair]
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        h=int(hours[e]); cash=lo<=h<hi
        f=table.get((pair,h),fee) if hourly else fee
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*f*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*f*entry/stop_d
            if cost_r>0.10: continue
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        r=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            gap=(O[b]-entry)*d
            if gap<=-stop_d: r=gap/stop_d-cost_r; in_until=b; break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None: out.append((r,cash,cost_r))
    return out

def stats(a):
    a=np.asarray(a,dtype=float)
    if len(a)<2: return len(a), float('nan'), float('nan')
    return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

def split(name, tr):
    r=np.array([x[0] for x in tr]); cash=np.array([x[1] for x in tr]); c=np.array([x[2] for x in tr])
    if len(r)<4 or cash.sum()<2 or (~cash).sum()<2: print(f"{name:<10} n={len(r)} (too few)"); return
    n1,m1,s1=stats(r[~cash]); n2,m2,s2=stats(r[cash]); t=(m1-m2)/np.sqrt(s1**2+s2**2)
    print(f"{name:<10}{len(r):>6}{r.mean():>+9.4f}{c.mean():>8.4f} |{n1:>6}{m1:>+9.4f}{m1/s1:>+7.2f}{c[~cash].mean():>8.4f} |{n2:>6}{m2:>+9.4f}{m2/s2:>+7.2f}{c[cash].mean():>8.4f} |{m1-m2:>+9.4f}{t:>+8.2f}")

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
    table=hour_table()
    clear_cache(); p=get_platform(PLAT); await p.connect()
    per={}; per_s={s:[] for s in STRATS}; flat=[]
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
                    tr=run(sdf,sg,pair,table,True); per[pair].extend(tr); per_s[s].extend(tr)
                    flat.extend(run(sdf,sg,pair,table,False))
            print(f"{pair} bars={n} trades={len(per[pair])} hours sampled={sum(1 for h in range(24) if (pair,h) in table)}",flush=True)
    finally:
        await p.disconnect()
    allt=[t for tr in per.values() for t in tr]
    print(f"\n===== INDEX ENTRIES AT THE SAMPLED HOUR SPREAD, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    fr=np.array([x[0] for x in flat]); hr=np.array([x[0] for x in allt])
    print(f"audited table: n={len(fr)} E[R]={fr.mean():+.4f} cost_r={np.mean([x[2] for x in flat]):.4f} sumR={fr.sum():+.1f}   |   hour table: n={len(hr)} E[R]={hr.mean():+.4f} cost_r={np.mean([x[2] for x in allt]):.4f} sumR={hr.sum():+.1f}")
    print(f"\n{'':<10}{'n':>6}{'E[R]':>9}{'cost_r':>8} |{'off n':>6}{'E[R]':>9}{'t':>7}{'cost_r':>8} |{'cash n':>6}{'E[R]':>9}{'t':>7}{'cost_r':>8} |{'off-cash':>9}{'t_diff':>8}")
    split("ALL", allt)
    for s in STRATS: split(s, per_s[s])
    for pair in PAIRS:
        if pair in per: split(pair, per[pair])
asyncio.run(main())

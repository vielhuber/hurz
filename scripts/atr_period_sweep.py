"""The ATR lookback behind the stop: the last unswept axis of the grid.

Section 1's grid varied the stop multiple, the reward:risk, the hold
and the ADX threshold, and section 58 later found the Donchian
lookback had been left out of it; the ATR period behind the stop
distance is the same kind of omission. The stop is 2 x ATR(14) and the
14 has never been moved. Here it is swept at 7 / 14 / 28 / 56 bars for
the stop distance only — the signals are generated exactly as live
(the Keltner band keeps its own ATR(14)), so nothing but the stop, the
target derived from it and the resulting size changes. All variants
are booked on the same bar path of every signal and compared paired
per trade; occupancy follows the live variant so the trade set cannot
drift, and a signal is only counted where every variant can be priced
inside the 10 % cost ceiling (the skips are reported). Acceptance,
fixed before the data were seen: better than 14 at paired t > 2 on
both disjoint samples. DAYS_FROM / DAYS_TO select the history window;
DUMP writes the per-trade R of every variant. See
docs/EDGE_FINDINGS.md section 183.
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
DUMP=os.getenv("DUMP","")
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
PERIODS=[7,14,28,56]; LIVE=1
PAGE_DAYS=35; PAGE_PAUSE=0.5


def atr_series(df, period):
    """True range averaged over `period` bars, the project's own ATR with another lookback."""
    prev=df["close"].shift(1)
    tr=np.maximum.reduce([
        (df["high"]-df["low"]).values,
        (df["high"]-prev).abs().values,
        (df["low"]-prev).abs().values,
    ])
    import pandas as pd
    return pd.Series(tr, index=df.index).rolling(period).mean().values

def book(O,H,L,C,e,d,entry,stop_d,cost_r,n):
    tp=entry+d*RR*stop_d; sl=entry-d*stop_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return RR-cost_r, b
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r, e+HOLD
    return None, None

def run(df, entries, pair, atrs):
    out=[]; in_until=-1; skipped=0; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    n=len(df)
    for e,d in sorted(entries):
        if e<=in_until or e>=n: continue
        entry=float(C[e]); vm=_venue_min_distance(PLAT,pair,entry)
        rs=[]; floors=[]; xb_live=None
        for i,per in enumerate(PERIODS):
            atr=atrs[per][e]
            if not np.isfinite(atr) or atr<=0: break
            stop_d=STOP_ATR*atr; floored=(vm>0 and stop_d<vm)
            if floored: stop_d=vm
            cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10:
                stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
                if cost_r>0.10: break
            r,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,n)
            if r is None: break
            rs.append(r); floors.append(floored)
            if i==LIVE: xb_live=xb
        if len(rs)==len(PERIODS) and xb_live is not None:
            out.append((rs,floors)); in_until=xb_live
        else:
            skipped+=1
    return out, skipped

def table(name, tr):
    R=np.array([x for x,_ in tr]); F=np.array([f for _,f in tr])
    if len(R)<4: return
    base=R[:,LIVE]
    print(f"\n--- {name}: n={len(R)} live ATR(14) E[R]={base.mean():+.4f} sumR={base.sum():+.1f}")
    print(f"{'ATR period':<12}{'E[R]':>9}{'t':>7}{'diff vs 14':>12}{'t_pair':>8}{'sum R':>9}{'win%':>7}{'floor%':>8}")
    for i,per in enumerate(PERIODS):
        r=R[:,i]; d=r-base; se=r.std(ddof=1)/np.sqrt(len(r))
        sd=d.std(ddof=1); tp=d.mean()/(sd/np.sqrt(len(d))) if sd>0 else float('nan')
        print(f"{per:<12}{r.mean():>+9.4f}{r.mean()/se:>+7.2f}{d.mean():>+12.4f}{tp:>+8.2f}{r.sum():>+9.1f}{np.mean(r>0)*100:>7.1f}{F[:,i].mean()*100:>8.0f}")

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
    per_pair={}; per_s={s:[] for s in STRATS}; total_skipped=0
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; per_pair[pair]=[]
            atrs={q:atr_series(df,q) for q in PERIODS}
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sub={q:atrs[q][lo_:hi] for q in PERIODS}
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    tr,sk=run(sdf,sg,pair,sub); per_pair[pair].extend(tr); per_s[s].extend(tr); total_skipped+=sk
            print(f"{pair} bars={n} trades={len(per_pair[pair])}",flush=True)
    finally:
        await p.disconnect()
    allt=[t for tr in per_pair.values() for t in tr]
    if DUMP: np.savez(DUMP, R=np.array([x for x,_ in allt]))
    print(f"\n===== ATR PERIOD BEHIND THE STOP, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"signals dropped because a variant could not be priced: {total_skipped}")
    table("ALL", allt)
    for s in STRATS: table(s, per_s[s])
    for pair in PAIRS:
        if pair in per_pair: table(pair, per_pair[pair])
asyncio.run(main())

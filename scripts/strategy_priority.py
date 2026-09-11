"""Which strategy wins a contested bar — order of arrival, or measured rank?

Section 132 measured that five signals in six would pyramid or flip an
existing position, so the duplicate-exposure guard turns three strategies
into one bet per instrument. What it did not ask is WHICH of the three
gets that bet. Today the answer is arrival order, which for signals on the
same bar is simply the order the active list happens to iterate in.

Read across the four disjoint samples, the three are not equal:

  365 d      donchian +0.0155 > turtle +0.0040 > keltner -0.0389
  366-1,095  turtle   +0.0357 > donchian +0.0332 > keltner +0.0280
  1,096-1,825 turtle  +0.0268 > donchian +0.0220 > keltner +0.0162
  1,826-2,555 turtle  +0.0181 > keltner +0.0166 > donchian +0.0162

Averaging ranks over the three OLDER samples — the recent year is held
out — gives turtle (1.0) > donchian (2.3) > keltner (2.7). The variant
therefore resolves a contested bar in that order instead of the current
donchian > turtle > keltner.

The scope limit matters and is deliberate. Priority can only be applied
to signals on the SAME bar: preferring turtle over a donchian signal that
fired an hour earlier would mean declining a trade in the hope of a better
one later, which needs information the moment does not have. So this
measures the ceiling of an ordering rule, and if simultaneous contests are
rare the ceiling is low — which is itself the finding.

Acceptance, fixed before the data were seen:

  (a) sum R per calendar day is higher than the current order on ALL FOUR
      samples,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) the held-out recent year is not made worse — it is the sample where
      donchian outranks turtle, so a rule fitted to the older three must
      at least not cost there.

Merged book throughout, as section 132 requires: one position per
instrument, concurrent cap 8, ADX ceiling + 3xATR floor + block list in
force. Nothing about risk, stop or leash changes; only which of two
simultaneous signals is taken. See docs/EDGE_FINDINGS.md section 199.
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
from app.spot_trading.trading_blocks import direction_blocked, BLOCKED_PAIRS
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.spot_backtest import _fee_for, _venue_min_distance
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

PAIRS=[p for p in ["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"] if p not in BLOCKED_PAIRS]
CURRENT=["donchian_breakout","turtle_breakout","keltner_breakout"]
RANKED=["turtle_breakout","donchian_breakout","keltner_breakout"]
ORDERS={"current":CURRENT,"ranked":RANKED}
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; STOP_ATR=2.0; RR=1.5; HOLD=24; MAX_CONCURRENT=8; PLAT="capital_com"
PAGE_DAYS=35; PAGE_PAUSE=1.0


def book(O,H,L,C,e,d,entry,stop_d,cost_r,n):
    sl=entry-d*stop_d; tp=entry+d*RR*stop_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return RR-cost_r, b
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r, e+HOLD
    return None, None

def prepare(df, pair, e, atr_floor):
    A=df["atr_14"].values; C=df["close"].values
    atr=A[e]
    if not np.isfinite(atr) or atr<=0: return None
    entry=float(C[e]); stop_d=STOP_ATR*atr
    vm=_venue_min_distance(PLAT,pair,entry)
    if vm>0 and stop_d<vm: stop_d=vm
    if atr_floor>0 and stop_d/atr<atr_floor: return None
    fee=_fee_for(PLAT,pair)
    cost_r=2.0*fee*entry/stop_d
    if cost_r>0.10:
        stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10: return None
    return entry, stop_d, cost_r

def simulate(signals, frames, order, atr_floor):
    """signals: (timestamp, pair, key, index, direction, strategy), sorted by
    timestamp then by the given strategy order."""
    rank={s:i for i,s in enumerate(order)}
    sigs=sorted(signals, key=lambda r:(r[0], rank.get(r[5], 9)))
    open_until={}; out=[]
    for ts, pair, key, e, d, strat in sigs:
        for p_ in [p_ for p_, until in open_until.items() if until <= ts]:
            del open_until[p_]
        if pair in open_until: continue
        if len(open_until) >= MAX_CONCURRENT: continue
        df=frames[key]
        prep=prepare(df, pair, e, atr_floor)
        if prep is None: continue
        entry, stop_d, cost_r = prep
        O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
        r,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,len(df))
        if r is None: continue
        open_until[pair]=df["timestamp"].values[xb]
        out.append((df["timestamp"].values[xb], float(r), strat))
    return out

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
    atr_floor=_min_stop_atr_multiple()
    clear_cache(); p=get_platform(PLAT); await p.connect()
    frames={}; signals=[]
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                key=(pair,k); frames[key]=sdf; ts=sdf["timestamp"].values
                for s in CURRENT:
                    for x in get_strategy(s)(sdf,{}):
                        if gate(s,sdf,x.index).blocked: continue
                        if direction_blocked(pair,x.direction): continue
                        signals.append((ts[x.index], pair, key, x.index, x.direction, s))
            print(f"{pair} bars={n}",flush=True)
    finally:
        await p.disconnect()
    # how often is a bar actually contested on the same instrument?
    from collections import Counter
    c=Counter((r[0], r[1]) for r in signals)
    contested=sum(1 for v in c.values() if v>1)
    print(f"\n===== STRATEGY PRIORITY ({DAYS_FROM}-{DAYS_TO} d, merged book) =====")
    print(f"signals={len(signals)}  distinct (bar,instrument) slots={len(c)}  "
          f"contested slots={contested} ({100*contested/max(1,len(c)):.1f} %)")
    span=max(1.0,(DAYS_FROM-DAYS_TO))
    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    daily={}
    for name,order in ORDERS.items():
        res=simulate(signals, frames, order, atr_floor)
        r=np.array([x for _,x,_ in res])
        per_day={}
        for ts,x,_ in res:
            day=str(np.datetime64(ts,'D')); per_day[day]=per_day.get(day,0.0)+x
        daily[name]=per_day
        mix=Counter(s for _,_,s in res)
        print(f"\n{name:<8} trades={len(r):>5} E[R]={r.mean():+.4f} sumR={r.sum():+.1f} "
              f"sumR/day={r.sum()/span:+.4f}")
        print(f"         mix: " + "  ".join(f"{k.split('_')[0]}={v}" for k,v in sorted(mix.items())))
    all_days=sorted(set(daily["current"])|set(daily["ranked"]))
    a=np.array([daily["current"].get(d,0.0) for d in all_days])
    b=np.array([daily["ranked"].get(d,0.0) for d in all_days])
    diff=b-a
    print(f"\nranked minus current: {(b.sum()-a.sum())/span:+.4f} R/day   "
          f"t_paired/day={t(diff):+.2f}   ACCEPT if > +2 and positive on all four")
asyncio.run(main())

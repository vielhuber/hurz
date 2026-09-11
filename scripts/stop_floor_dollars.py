"""The stop floor measured in dollars, with the notional cap modelled.

Section 136 swept the venue stop floor at 1.5 / 2 / 3 % and kept 1.05 %:
monotonic gain on the recent year, monotonic loss on the older one, so its
first clause failed. That measurement was in R per trade, and R is
normalised by the stop — which is exactly the quantity being changed.

Section 205 showed what that hides. A 3 USD risk at a 1.05 % stop needs
286 USD of notional against a 250 USD cap, so the cap binds and planned
risk lands at 2.41 USD. A WIDER floor needs LESS notional for the same
dollar risk: at 1.2 % it needs 250, at 1.5 % only 200. Past roughly 1.2 %
the cap stops binding and the trade finally carries the 3 USD the
configuration asks for — 24 % more dollars per unit of R, at no change to
the exposure limit.

So the question section 136 answered in R has a different answer in
dollars, and dollars is the unit the objective is stated in. This run
measures USD per calendar day on the merged one-position-per-instrument
book, sizing every trade through the live `calculate_position_size` so the
cap, the broker increment and the rejections it causes are all real.

Floors 1.05 % (live) / 1.2 % / 1.5 % / 2.0 %. The stop and the target move
together, as they do live, so the wider floor is not a hidden RR change.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day is higher than the live floor on ALL FOUR
      disjoint samples,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) planned risk per trade does not exceed the configured 3.00 USD on
      any sample — the point is to reach the budget, never to exceed it,
  (d) what ships is the smallest qualifying floor.

On risk: the dollar loss at the stop stays at its configured 3 USD
ceiling, so no risk limit is loosened. What does change is that a wider
stop holds a smaller position for longer, and section 190's volatility
floor interacts with it — a wider venue floor raises every trade's pin
ratio and so admits trades the 3 x ATR floor currently refuses. That
interaction is modelled, not assumed, because both filters run here.

See docs/EDGE_FINDINGS.md section 207.
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
from app.spot_trading.position_sizing import (
    calculate_position_size, DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD,
)
from app.spot_trading.regime import gate
from scripts.spot_backtest import _fee_for
from scripts.walk_forward import _bars_to_df

PAIRS=[p for p in ["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"] if p not in BLOCKED_PAIRS]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; STOP_ATR=2.0; RR=1.5; HOLD=24; MAX_CONCURRENT=8; PLAT="capital_com"
FLOORS=[0.0105,0.012,0.015,0.020]   # first entry is live
INCREMENT=0.0001
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


def simulate(signals, frames, floor, atr_floor):
    open_until={}; out=[]; risks=[]
    for ts, pair, key, e, d in signals:
        for p_ in [p_ for p_, until in open_until.items() if until <= ts]:
            del open_until[p_]
        if pair in open_until: continue
        if len(open_until) >= MAX_CONCURRENT: continue
        df=frames[key]
        A=df["atr_14"].values; C=df["close"].values
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=floor*entry
        if stop_d<vm: stop_d=vm
        if atr_floor>0 and stop_d/atr<atr_floor: continue
        fee=_fee_for(PLAT,pair)
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        sl = entry - d*stop_d
        sized = calculate_position_size(
            entry_price=entry, stop_loss=sl, target_risk=DEFAULT_TARGET_RISK_USD,
            notional_cap=DEFAULT_NOTIONAL_CAP_USD, size_increment=INCREMENT)
        if sized.size is None: continue
        O=df["open"].values; H=df["high"].values; L=df["low"].values
        r,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,len(df))
        if r is None: continue
        open_until[pair]=df["timestamp"].values[xb]
        out.append((df["timestamp"].values[xb], r*sized.planned_risk))
        risks.append(sized.planned_risk)
    return out, risks


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
                for s in STRATS:
                    for x in get_strategy(s)(sdf,{}):
                        if gate(s,sdf,x.index).blocked: continue
                        if direction_blocked(pair,x.direction): continue
                        signals.append((ts[x.index], pair, key, x.index, x.direction))
            print(f"{pair} bars={n}",flush=True)
    finally:
        await p.disconnect()
    signals.sort(key=lambda r: r[0])
    span=max(1.0,(DAYS_FROM-DAYS_TO))
    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    print(f"\n===== STOP FLOOR IN DOLLARS ({DAYS_FROM}-{DAYS_TO} d, merged book, "
          f"cap {DEFAULT_NOTIONAL_CAP_USD:g}, target risk {DEFAULT_TARGET_RISK_USD:g}) =====")
    daily={}
    for f in FLOORS:
        res, risks = simulate(signals, frames, f, atr_floor)
        usd=np.array([x for _,x in res]); rk=np.array(risks)
        per_day={}
        for ts,x in res:
            day=str(np.datetime64(ts,'D')); per_day[day]=per_day.get(day,0.0)+x
        daily[f]=per_day
        print(f"{f:.2%}  trades={len(usd):>5} mean risk={rk.mean():.2f} USD "
              f"sumUSD={usd.sum():>+8.2f} USD/day={usd.sum()/span:>+7.4f} "
              f"trades/day={len(usd)/span:.2f}")
    all_days=sorted(set().union(*[set(d) for d in daily.values()]))
    base=np.array([daily[FLOORS[0]].get(d,0.0) for d in all_days])
    print(f"\n{'floor':<8}{'USD/day':>10}{'vs live':>10}{'t_paired':>10}")
    for f in FLOORS:
        a=np.array([daily[f].get(d,0.0) for d in all_days]); diff=a-base
        print(f"{f:<8.2%}{a.sum()/span:>+10.4f}{(a.sum()-base.sum())/span:>+10.4f}"
              f"{t(diff) if f!=FLOORS[0] else float('nan'):>+10.2f}")
asyncio.run(main())

"""The 1 % price floor, revisited at a positive expectancy.

The floor's own comment in `autotrade.evaluate_pair` names the
condition for reopening it: "expectancy is negative on both sides, so
trading more of either only loses faster. Removing it would lift the
blended expectancy and raise the loss in dollars, because it multiplies
volume roughly fifteenfold. Revisit only once expectancy is positive —
at that point the floor becomes the single largest constraint on
frequency."

That condition is now met on three of the four samples. After the ADX
ceiling (188), the volatility floor (190) and the instrument block
(192), the book reads +0.032 R at t = +3.36, +0.022 at t = +2.02 and
+0.017 at t = +1.75 on the three older samples and -0.006 at t = -0.45
on the recent year. The daily objective is trades-per-day x E[R] x risk,
and today's three builds cut the recent year's trade count by 47 %, so
frequency is where the arithmetic now binds.

This lever ADDS trades rather than removing them, which inverts the
risk question, so the acceptance rule gains a term the exclusion
filters did not need:

  (a) the paired difference is positive on ALL FOUR samples,
  (b) it reaches t > 2 on at least one,
  (c) the ADDED trades carry E[R] >= 0 on all four — a lower floor that
      pays on average while importing losers would be buying volume
      with expectancy,
  (d) what ships is the most conservative qualifying floor — the
      highest one that still qualifies, admitting the fewest trades.

Note what this does and does not touch. Risk per trade is unchanged:
sizing targets a fixed dollar risk, so a narrower stop takes a larger
position for the same loss at the stop. The concurrent-position cap,
the cluster cap and the daily entry cap all stay in force and are the
binding constraints on how much of the added volume can actually be
taken; this measurement is the upper bound, not a forecast. The 3 x ATR
volatility floor of section 190 stays in force alongside — the two
floors measure different things, and a trade below 1 % of price can sit
well above 3 ATR when volatility is low.

DAYS_FROM / DAYS_TO select the window. See docs/EDGE_FINDINGS.md
section 194.
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
from app.spot_trading.autotrade import _min_stop_atr_multiple, _min_stop_fraction
from scripts.spot_backtest import _fee_for, _venue_min_distance
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

PAIRS=[p for p in ["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"] if p not in BLOCKED_PAIRS]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; STOP_ATR=2.0; HOLD=24; RR=1.5; PLAT="capital_com"
FLOORS=[0.01,0.0075,0.005,0.0025,0.0]   # first entry is live
PAGE_DAYS=35; PAGE_PAUSE=0.5


def book(O,H,L,C,e,d,entry,stop_d,cost_r,n):
    """R of one trade under the live rule; returns (r, exit bar)."""
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

def run(df, entries, pair, atr_floor):
    """One pass per floor. Occupancy is resolved per floor, because a
    lower floor admits trades that then block later ones — the whole
    point is the frequency change, so it must be modelled, not held
    fixed on the live variant as the exclusion filters did."""
    fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; n=len(df)
    out={f:[] for f in FLOORS}
    for f in FLOORS:
        in_until=-1
        for e,d in sorted(entries):
            if e<=in_until or e>=n: continue
            atr=A[e]
            if not np.isfinite(atr) or atr<=0: continue
            entry=float(C[e]); stop_d=STOP_ATR*atr
            vm=_venue_min_distance(PLAT,pair,entry)
            if vm>0 and stop_d<vm: stop_d=vm
            if f>0 and entry>0 and stop_d/entry<f: continue
            if atr_floor>0 and stop_d/atr<atr_floor: continue
            cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10:
                stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
                if cost_r>0.10: continue
            r,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,n)
            if r is None: continue
            in_until=xb
            out[f].append((e, float(r)))
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
    agg={f:[] for f in FLOORS}; added={f:[] for f in FLOORS}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; cnt=0
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    res=run(sdf,sg,pair,atr_floor)
                    live_idx={i for i,_ in res[FLOORS[0]]}
                    for f in FLOORS:
                        agg[f].extend(r for _,r in res[f])
                        added[f].extend(r for i,r in res[f] if i not in live_idx)
                    cnt+=len(res[FLOORS[0]])
            print(f"{pair} bars={n} live-trades={cnt}",flush=True)
    finally:
        await p.disconnect()
    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    base=np.array(agg[FLOORS[0]])
    print(f"\n===== STOP FLOOR REVISIT ({DAYS_FROM}-{DAYS_TO} d, atr floor {atr_floor:g}) =====")
    print(f"{'floor':<10}{'n':>7}{'E[R]':>10}{'t':>8}{'sum R':>10}{'d sumR':>9}"
          f"{'added n':>9}{'added E[R]':>12}{'added t':>9}")
    for f in FLOORS:
        r=np.array(agg[f]); a=np.array(added[f])
        lab="off" if f==0 else f"{f:.2%}"
        if len(a)>1:
            print(f"{lab:<10}{len(r):>7}{r.mean():>+10.4f}{t(r):>+8.2f}{r.sum():>+10.1f}"
                  f"{r.sum()-base.sum():>+9.1f}{len(a):>9}{a.mean():>+12.4f}{t(a):>+9.2f}")
        else:
            print(f"{lab:<10}{len(r):>7}{r.mean():>+10.4f}{t(r):>+8.2f}{r.sum():>+10.1f}"
                  f"{r.sum()-base.sum():>+9.1f}{len(a):>9}{'—':>12}{'—':>9}")
asyncio.run(main())

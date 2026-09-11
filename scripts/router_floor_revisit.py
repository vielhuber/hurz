"""The router's ADX floor, re-measured against the current system.

The floor at 30 is the largest single constraint on frequency in the
book — it refuses every trend signal below it, which on an hourly chart
is most of them. Its last examination (section 153) replayed sixteen
days of live intents and found floor 25 worse than 30, but sixteen days
is a fortnight of noise, and that reading predates all three of
2026-09-10's builds: the ADX ceiling, the 3 x ATR volatility floor and
the instrument block. Those three changed both which trades survive and
what the surviving book earns, so the floor deserves the same four-sample
treatment they got.

The daily objective is trades-per-day x E[R] x risk. Section 194 showed
there is no free frequency inside the existing guards and section 195
showed risk per trade is capped at 2x by the account. That leaves the
floor: if the 25-30 band is not actually negative under the current
filter set, it is the one place where frequency can still be bought.

Like the price-floor revisit this lever ADDS trades, so the acceptance
rule carries the extra term:

  (a) the paired difference is positive on ALL FOUR disjoint samples,
  (b) it reaches t > 2 on at least one,
  (c) the ADDED trades carry E[R] >= 0 on all four — admitting volume
      that loses on average is buying frequency with expectancy,
  (d) what ships is the most conservative qualifying floor, i.e. the
      highest one that still qualifies.

Floors 30 (live) / 27.5 / 25 / 22.5 / 20. Below 20 the router's
mean-reversion regime begins and the question changes, so the sweep
stops there. The ADX ceiling of section 188 stays in force at every
floor, as does the volatility floor and the block list — this measures
the floor against the system as it is, not against a stripped one.

Both `HURZ_REGIME_ADX_TREND` and `HURZ_REGIME_ADX_TREND_CORE` are set
per variant: the core 1h strategies read their own override, and leaving
it at 30 would silently keep three of the three strategies gated.

DAYS_FROM / DAYS_TO select the window. See docs/EDGE_FINDINGS.md
section 197.
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
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; STOP_ATR=2.0; HOLD=24; RR=1.5; PLAT="capital_com"
FLOORS=[30.0,27.5,25.0,22.5,20.0]   # first entry is live
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
    """Occupancy is resolved per floor — a lower floor admits trades that
    then block later ones, and that frequency change is the subject."""
    fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; n=len(df)
    out=[]
    in_until=-1
    for e,d in sorted(entries):
        if e<=in_until or e>=n: continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        if atr_floor>0 and stop_d/atr<atr_floor: continue
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        r,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,n)
        if r is None: continue
        in_until=xb
        out.append((e, float(r)))
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
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sigs=list(st(sdf,{}))
                    per_floor={}
                    for f in FLOORS:
                        os.environ["HURZ_REGIME_ADX_TREND"]=str(f)
                        os.environ["HURZ_REGIME_ADX_TREND_CORE"]=str(f)
                        sg=[(x.index,x.direction) for x in sigs
                            if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                        per_floor[f]=run(sdf,sg,pair,atr_floor)
                    live_idx={i for i,_ in per_floor[FLOORS[0]]}
                    for f in FLOORS:
                        agg[f].extend(r for _,r in per_floor[f])
                        added[f].extend(r for i,r in per_floor[f] if i not in live_idx)
            print(f"{pair} bars={n} live-trades={len(per_floor[FLOORS[0]])}",flush=True)
    finally:
        os.environ.pop("HURZ_REGIME_ADX_TREND",None)
        os.environ.pop("HURZ_REGIME_ADX_TREND_CORE",None)
        await p.disconnect()
    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    base=np.array(agg[FLOORS[0]])
    print(f"\n===== ROUTER FLOOR REVISIT ({DAYS_FROM}-{DAYS_TO} d, atr floor {atr_floor:g}) =====")
    print(f"{'floor':<9}{'n':>7}{'E[R]':>10}{'t':>8}{'sum R':>10}{'d sumR':>9}"
          f"{'added n':>9}{'added E[R]':>12}{'added t':>9}")
    for f in FLOORS:
        r=np.array(agg[f]); a=np.array(added[f])
        if len(a)>1:
            print(f"{f:<9g}{len(r):>7}{r.mean():>+10.4f}{t(r):>+8.2f}{r.sum():>+10.1f}"
                  f"{r.sum()-base.sum():>+9.1f}{len(a):>9}{a.mean():>+12.4f}{t(a):>+9.2f}")
        else:
            print(f"{f:<9g}{len(r):>7}{r.mean():>+10.4f}{t(r):>+8.2f}{r.sum():>+10.1f}"
                  f"{r.sum()-base.sum():>+9.1f}{len(a):>9}{'—':>12}{'—':>9}")
asyncio.run(main())

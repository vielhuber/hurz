"""The 1.20 % stop floor, tested with a statistic that has power.

Section 207 measured floors of 1.05 / 1.2 / 1.5 / 2.0 % in USD per
calendar day and found 1.20 % the only variant positive on all four
samples — mean planned risk 2.62 to 2.99 USD, because at 1.2 % the notional
cap stops binding and the configured 3 USD budget is finally reached — but
missed its own t > 2 bar everywhere, best reading +0.49. The reason was
stated there: most days carry two or three trades and many carry none, so a
per-calendar-day paired series is dominated by single-trade variance.

This run asks the same question with the unit that has the power. Every
signal is booked under both floors with occupancy resolved per variant.
Trades the two variants share — same instrument, same bar — are compared
PAIRED IN USD, which is where the cap effect lives and where n is in the
thousands rather than the hundreds. Trades only the wider floor takes are
reported separately, because a wider floor lifts pin ratios past section
190's 3 x ATR threshold and admits signals the live rule refuses; those are
added exposure, not a paired improvement, and they have to stand on their
own.

Acceptance, fixed before the data were seen:

  (a) the paired USD difference on shared trades is positive on ALL FOUR
      samples,
  (b) it reaches t > 2 on at least one and falls below t = -2 on none,
  (c) the trades only the wider floor takes carry mean USD >= 0 on all
      four — admitted volume must not be bought with expectancy,
  (d) planned risk per trade never exceeds the configured 3.00 USD.

Failing any of these, 1.05 % stays. This is the last preregistered attempt
at the stop floor; section 136 settled it in R, section 207 in dollars per
day without power, and if the paired test does not settle it either then
the floor is not a lever and the log should stop asking.

No risk limit is loosened: the dollar loss at the stop stays at its
configured 3 USD ceiling, and the point of the change is to reach that
ceiling rather than to exceed it. See docs/EDGE_FINDINGS.md section 208.
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
FLOORS=[0.0105,0.012]   # live, candidate
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
    """Returns {(pair, key, index): (usd, exit_ts)} so variants can be
    matched trade by trade, plus the planned-risk series."""
    open_until={}; out={}; risks=[]
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
        out[(pair,key,e)]=(r*sized.planned_risk, df["timestamp"].values[xb])
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
    results={}
    for f in FLOORS:
        res, risks = simulate(signals, frames, f, atr_floor)
        results[f]=(res, np.array(risks))
        usd=np.array([v[0] for v in res.values()])
        print(f"{f:.2%}  trades={len(res):>5} mean risk={np.mean(risks):.2f} USD "
              f"sumUSD={usd.sum():>+8.2f} USD/day={usd.sum()/span:>+7.4f}")
    live_res, live_risk = results[FLOORS[0]]
    cand_res, cand_risk = results[FLOORS[1]]
    shared=sorted(set(live_res) & set(cand_res))
    only_cand=sorted(set(cand_res) - set(live_res))
    only_live=sorted(set(live_res) - set(cand_res))
    a=np.array([live_res[k][0] for k in shared])
    b=np.array([cand_res[k][0] for k in shared])
    diff=b-a
    print(f"\nshared trades: {len(shared)}   only 1.20 %: {len(only_cand)}   "
          f"only 1.05 %: {len(only_live)}")
    print(f"PAIRED on shared, USD: mean={diff.mean():+.4f}  t={t(diff):+.2f}  "
          f"sum={diff.sum():+.2f}")
    if only_cand:
        oc=np.array([cand_res[k][0] for k in only_cand])
        print(f"only-1.20 % trades: n={len(oc)} mean USD={oc.mean():+.4f} "
              f"t={t(oc):+.2f} sum={oc.sum():+.2f}")
    if only_live:
        ol=np.array([live_res[k][0] for k in only_live])
        print(f"only-1.05 % trades: n={len(ol)} mean USD={ol.mean():+.4f} "
              f"sum={ol.sum():+.2f}")
    print(f"max planned risk: live {live_risk.max():.2f}  candidate {cand_risk.max():.2f}"
          f"   (configured ceiling 3.00)")
asyncio.run(main())

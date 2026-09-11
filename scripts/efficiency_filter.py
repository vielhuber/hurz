"""Selecting on dollar efficiency instead of expectancy in R.

Section 213 measured what the selector cannot see. At the live 3 USD /
250 USD configuration the realised risk per trade ranges from 47 % of
target (GBPJPY) to 87 % (SILVER), mean 77 %, driven entirely by the
broker's size step against the raw size. At equal expectancy in R,
the low-efficiency instruments produce half the dollars.

That matters because of which property persists. Section 130 established
that the selector's ranking — expectancy in R — does not transfer between
samples. Efficiency does: it follows price level, step size and minimum
size, none of them regime-dependent. So the question is whether refusing
the structurally inefficient instruments raises the daily dollar figure,
even though doing so removes trades.

Efficiency is recomputed per trade at that trade's own entry price, with
the venue's real `order_constraints` and its `usd_per_quote`, sized
through the live `calculate_position_size`. Thresholds 60 / 70 / 80 %: a
signal is refused when its realised risk would fall below that share of
the 3 USD budget.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day is higher than live on ALL FOUR samples,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) what ships is the lowest qualifying threshold, refusing the fewest
      trades.

Two limitations stated up front: step size, minimum size and the quote
rate are today's, applied to historical prices. Steps change rarely and
the rate moves slowly against a 3 USD budget, but both are assumptions,
not measurements.

History is fetched once per instrument over the full 2,555 d span and
sliced into the four samples locally — a quarter of the pages of four
separate runs, which matters while the bot is trading (the pacing below
is the same as the sibling scripts').

No risk limit moves — the rule refuses entries whose size the broker's
increment would shrink below a share of the budget, and never increases
any position. See docs/EDGE_FINDINGS.md section 214.
"""
import asyncio, os, sys, json
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
SEG=3; STOP_ATR=2.0; RR=1.5; HOLD=24; MAX_CONCURRENT=8; PLAT="capital_com"
EFF_MINS=[0.0,0.60,0.70,0.80]   # 0.0 = live (no efficiency filter)
WINDOWS=[(365,0),(1095,366),(1825,1096),(2555,1826)]
SPAN=max(d for d,_ in WINDOWS)
PAGE_DAYS=35; PAGE_PAUSE=1.0
META_CACHE="/tmp/eff_meta.json"


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


def simulate(signals, frames, meta, eff_min, atr_floor):
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
        vm=0.0105*entry
        if stop_d<vm: stop_d=vm
        if atr_floor>0 and stop_d/atr<atr_floor: continue
        fee=_fee_for(PLAT,pair)
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        sl = entry - d*stop_d
        m=meta.get(pair)
        if m is None: continue
        rate=m["rate"]
        # The budgets are USD, size x stop distance is in the quote
        # currency — size like the live loop does (EDGE_FINDINGS 77).
        sized = calculate_position_size(
            entry_price=entry, stop_loss=sl,
            target_risk=DEFAULT_TARGET_RISK_USD/rate,
            notional_cap=DEFAULT_NOTIONAL_CAP_USD/rate,
            size_increment=m["step"], min_size=m["min"], max_size=m["max"])
        if sized.size is None: continue
        risk_usd = sized.planned_risk * rate
        if eff_min > 0 and risk_usd < eff_min * DEFAULT_TARGET_RISK_USD:
            continue
        O=df["open"].values; H=df["high"].values; L=df["low"].values
        r,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,len(df))
        if r is None: continue
        open_until[pair]=df["timestamp"].values[xb]
        out.append((df["timestamp"].values[xb], r*risk_usd))
        risks.append(risk_usd)
    return out, risks


async def fetch_paced(p, pair, days_from, days_to):
    now=datetime.now(timezone.utc)
    start=now-timedelta(days=days_from); end=now-timedelta(days=days_to)
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


async def instrument_meta(p, pair, reference_price):
    """Real size constraints and the USD value of the quote currency."""
    try:
        con=await p.order_constraints(pair)
    except Exception as ex:
        print(pair,"CONSTRAINTS FAIL",str(ex)[:60],flush=True); return None
    try:
        prepared=await p.prepare_order(asset=pair, direction=1,
            reference_price=reference_price, stop_loss=None, take_profit=None)
        rate=prepared.usd_per_quote
    except Exception as ex:
        print(pair,"RATE FAIL",str(ex)[:60],flush=True); return None
    if rate is None or rate<=0:
        print(pair,"no USD rate — skipped as live would",flush=True); return None
    mx=getattr(con,"max_size",None)
    return {"step":float(con.size_increment or 0.0), "min":float(con.min_size or 0.0),
            "max":(float(mx) if mx else None), "rate":float(rate)}


def build(bars, days_from, days_to):
    """Signals and frames for one sample, cut from the full history."""
    now=datetime.now(timezone.utc)
    lo=now-timedelta(days=days_from); hi=now-timedelta(days=days_to)
    sel=[b for b in bars if lo<=b.timestamp<hi]
    if len(sel)<400: return None,None
    return sel, None


async def main():
    atr_floor=_min_stop_atr_multiple()
    clear_cache(); p=get_platform(PLAT); await p.connect()
    history={}; meta={}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair, SPAN, 0)
            if not bars:
                print(f"{pair} no history",flush=True); continue
            m=await instrument_meta(p, pair, float(bars[-1].close))
            if m is None: continue
            history[pair]=bars; meta[pair]=m
            print(f"{pair} bars={len(bars)} step={m['step']:g} min={m['min']:g} "
                  f"rate={m['rate']:.6g}",flush=True)
    finally:
        await p.disconnect()
    json.dump(meta, open(META_CACHE,"w"))

    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    print(f"\n===== EFFICIENCY FILTER (merged book, cap {DEFAULT_NOTIONAL_CAP_USD:g}, "
          f"target {DEFAULT_TARGET_RISK_USD:g}, atr_floor={atr_floor:g}) =====")
    summary={}
    for days_from, days_to in WINDOWS:
        span=float(days_from-days_to)
        frames={}; signals=[]
        for pair, bars in history.items():
            sel,_=build(bars, days_from, days_to)
            if sel is None: continue
            df=add_indicators(_bars_to_df(sel)); n=len(df); seg=n//SEG
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                key=(pair,k); frames[key]=sdf; ts=sdf["timestamp"].values
                for s in STRATS:
                    for x in get_strategy(s)(sdf,{}):
                        if gate(s,sdf,x.index).blocked: continue
                        if direction_blocked(pair,x.direction): continue
                        signals.append((ts[x.index], pair, key, x.index, x.direction))
        signals.sort(key=lambda r: r[0])
        label=f"{days_to or 0}-{days_from} d"
        print(f"\n--- sample {label}: {len(signals)} gated signals ---")
        daily={}
        for f in EFF_MINS:
            res, risks = simulate(signals, frames, meta, f, atr_floor)
            usd=np.array([x for _,x in res]); rk=np.array(risks)
            per_day={}
            for ts,x in res:
                day=str(np.datetime64(ts,'D')); per_day[day]=per_day.get(day,0.0)+x
            daily[f]=per_day
            print(f"{f:.0%}  trades={len(usd):>5} mean risk={rk.mean() if len(rk) else 0:.3f} USD "
                  f"sumUSD={usd.sum():>+9.2f} USD/day={usd.sum()/span:>+8.4f} "
                  f"trades/day={len(usd)/span:.2f}",flush=True)
        all_days=sorted(set().union(*[set(d) for d in daily.values()]))
        base=np.array([daily[EFF_MINS[0]].get(d,0.0) for d in all_days])
        rows=[]
        for f in EFF_MINS[1:]:
            a=np.array([daily[f].get(d,0.0) for d in all_days]); diff=a-base
            rows.append((f,(a.sum()-base.sum())/span,t(diff)))
        summary[label]=(base.sum()/span,rows)
        for f,d,tv in rows:
            print(f"   eff>={f:.0%}  vs live {d:>+8.4f} USD/day  t_paired {tv:>+6.2f}")

    print("\n===== SUMMARY: USD/day vs live, paired t =====")
    print(f"{'sample':<14}{'live':>9}" + "".join(f"{f'>={f:.0%}':>20}" for f in EFF_MINS[1:]))
    for label,(live,rows) in summary.items():
        print(f"{label:<14}{live:>+9.4f}" + "".join(f"{d:>+12.4f} (t{tv:>+5.2f})" for _,d,tv in rows))
asyncio.run(main())

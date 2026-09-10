"""Is the near target's flipped sign an ADX effect?

Section 185 measured a volatility-anchored target — pulled in to the
venue floor, a realised reward:risk just under 1.0 — and found it
worth +0.019 R at t = +2.93 on the recent year and -0.011 R at
t = -2.85 on the older one. Five points of win rate in both, opposite
expectancy. The obvious candidate for the difference is trend
strength: a near target caps the runners, so it should pay where the
runners do not run and cost where they do. The regime router already
gates trend entries at ADX >= 30, so every trade in this book carries
an ADX above that — but not the same one.

This run reads the paired difference (near target minus live target)
by ADX bucket at the entry bar, on both samples. Preregistered: the
bucket gradient must carry the same sign on both samples before any
threshold rule is built; a gradient visible on one sample only is the
same regime accident section 185 already rejected. If it does carry,
the resulting conditional rule — near target below the threshold, live
target above — is then booked as its own variant and must clear paired
t > 2 on both samples like every other lever.

The stop is untouched throughout, so the 1 R loss limit stands and the
target can only move closer. DAYS_FROM / DAYS_TO select the window.
See docs/EDGE_FINDINGS.md section 186.
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
SEG=3; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
NEAR_ATR=1.5; LIVE_RR=1.5
# Thresholds booked as conditional rules: near target while ADX < t.
THRESHOLDS=[35.0,40.0,45.0]
BUCKETS=[(0,35),(35,40),(40,45),(45,50),(50,999)]
PAGE_DAYS=35; PAGE_PAUSE=0.5


def book(O,H,L,C,e,d,entry,stop_d,cost_r,tp_d,n):
    """R of one trade under one target distance; returns (r, exit bar)."""
    sl=entry-d*stop_d
    tp=entry+d*tp_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return tp_d/stop_d-cost_r, b
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r, e+HOLD
    return None, None

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; X=df["adx_14"].values; n=len(df)
    for e,d in sorted(entries):
        if e<=in_until or e>=n: continue
        atr=A[e]; adx=X[e]
        if not np.isfinite(atr) or atr<=0 or not np.isfinite(adx): continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        r_live,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,LIVE_RR*stop_d,n)
        if r_live is None: continue
        r_near,_=book(O,H,L,C,e,d,entry,stop_d,cost_r,max(NEAR_ATR*atr,vm),n)
        if r_near is None: continue
        in_until=xb
        out.append((float(adx), r_live, r_near))
    return out

def report(name, tr):
    if len(tr)<4: return
    adx=np.array([a for a,_,_ in tr]); live=np.array([l for _,l,_ in tr]); near=np.array([n for _,_,n in tr])
    diff=near-live
    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    print(f"\n--- {name}: n={len(tr)} live E[R]={live.mean():+.4f} near E[R]={near.mean():+.4f} "
          f"diff={diff.mean():+.4f} t={t(diff):+.2f}")
    print(f"{'ADX bucket':<14}{'n':>7}{'live E[R]':>11}{'near E[R]':>11}{'diff':>10}{'t_pair':>8}")
    for lo,hi in BUCKETS:
        m=(adx>=lo)&(adx<hi)
        if m.sum()<20: continue
        print(f"{f'{lo:g}-{hi:g}' if hi<900 else f'{lo:g}+':<14}{m.sum():>7}{live[m].mean():>+11.4f}"
              f"{near[m].mean():>+11.4f}{diff[m].mean():>+10.4f}{t(diff[m]):>+8.2f}")
    print(f"{'conditional':<14}{'n_near':>7}{'live E[R]':>11}{'rule E[R]':>11}{'diff':>10}{'t_pair':>8}")
    for th in THRESHOLDS:
        m=adx<th
        rule=np.where(m, near, live)
        d=rule-live
        print(f"{f'ADX<{th:g}':<14}{int(m.sum()):>7}{live.mean():>+11.4f}{rule.mean():>+11.4f}"
              f"{d.mean():>+10.4f}{t(d):>+8.2f}")

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
    allt=[]; per_s={s:[] for s in STRATS}
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
                    tr=run(sdf,sg,pair); allt.extend(tr); per_s[s].extend(tr); cnt+=len(tr)
            print(f"{pair} bars={n} trades={cnt}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== NEAR TARGET BY ADX, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    report("ALL", allt)
    for s in STRATS: report(s, per_s[s])
asyncio.run(main())

"""A volatility-anchored target instead of a stop-anchored one.

Section 124 read the reward:risk separately for pinned and ATR-bound
trades and closed with the sentence this run picks up: the pinned
trades — 78 % of the book at a mean stop of 6.5 ATR — need a
volatility-scaled distance the venue does not offer. It offers it on
the target. The live target sits at 1.5 x the stop, so on a pinned
trade it asks for roughly 10 ATR of travel inside 24 bars, which is
why 61 % of the book times out. Anchoring the target to the ATR
instead makes it the same distance on every trade regardless of what
the venue floor did to the stop.

The venue's minimum distance applies to the target as well, so a
target closer than that floor is not placeable and is clamped to it —
on a pinned trade, where the stop IS the floor, that caps the variant
at RR 1.0. Section 124's group-wise RR sweep never carried this
constraint. The stop is untouched in all four variants, so the 1 R
loss limit stands and no risk control is loosened; only the upside
barrier moves, and it can only move closer.

Four rules on the same bar path of every live trade of the three 1h
trend strategies, occupancy on the live rule so the trade set is
identical and every difference is paired per trade: the live 1.5 R
target, then targets at 1.5 / 3.0 / 4.5 ATR. Acceptance, fixed before
the data were seen: better than the live rule at paired t > 2 on both
disjoint samples. DAYS_FROM / DAYS_TO select the history window. See
docs/EDGE_FINDINGS.md section 185.
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
# None = the live rule (1.5 x stop distance); a number = that many ATR.
VARIANTS=[None,1.5,3.0,4.5]; LIVE=0; LIVE_RR=1.5
PAGE_DAYS=35; PAGE_PAUSE=0.5


def book(O,H,L,C,e,d,entry,stop_d,cost_r,tp_d,n):
    """R of one trade under one target distance; returns (r, exit bar, kind)."""
    sl=entry-d*stop_d
    tp=entry+d*tp_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b, "stop"
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b, "stop"
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return tp_d/stop_d-cost_r, b, "target"
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r, e+HOLD, "timeout"
    return None, None, None

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; n=len(df)
    for e,d in sorted(entries):
        if e<=in_until or e>=n: continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        rs=[]; kinds=[]; rrs=[]
        for i,v in enumerate(VARIANTS):
            # The venue rejects a target inside its minimum distance just
            # as it does a stop, so an ATR target can never sit closer.
            tp_d=LIVE_RR*stop_d if v is None else max(v*atr, vm)
            r,xb,kind=book(O,H,L,C,e,d,entry,stop_d,cost_r,tp_d,n)
            if r is None: break
            rs.append(r); kinds.append(kind); rrs.append(tp_d/stop_d)
            if i==LIVE: in_until=xb
        if len(rs)==len(VARIANTS): out.append((rs,kinds,rrs))
    return out

def table(name, tr):
    R=np.array([x for x,_,_ in tr]); K=[k for _,k,_ in tr]; RR=np.array([r for _,_,r in tr])
    if len(R)<4: return
    base=R[:,LIVE]
    print(f"\n--- {name}: n={len(R)} live E[R]={base.mean():+.4f} sumR={base.sum():+.1f}")
    print(f"{'target':<11}{'E[R]':>9}{'t':>7}{'diff':>10}{'t_pair':>8}{'sum R':>9}{'win%':>7}{'mean rr':>9}{'targ%':>7}{'stop%':>7}{'time%':>7}")
    for i,v in enumerate(VARIANTS):
        r=R[:,i]; dd=r-base; se=r.std(ddof=1)/np.sqrt(len(r))
        sd=dd.std(ddof=1); tp=dd.mean()/(sd/np.sqrt(len(dd))) if sd>0 else float('nan')
        lab="live 1.5R" if v is None else f"{v:g} ATR"
        share=lambda k: sum(1 for kk in K if kk[i]==k)/len(K)*100
        print(f"{lab:<11}{r.mean():>+9.4f}{r.mean()/se:>+7.2f}{dd.mean():>+10.4f}{tp:>+8.2f}{r.sum():>+9.1f}"
              f"{np.mean(r>0)*100:>7.1f}{RR[:,i].mean():>9.2f}{share('target'):>7.0f}{share('stop'):>7.0f}{share('timeout'):>7.0f}")

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
    per_pair={}; per_s={s:[] for s in STRATS}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; per_pair[pair]=[]
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    tr=run(sdf,sg,pair); per_pair[pair].extend(tr); per_s[s].extend(tr)
            print(f"{pair} bars={n} trades={len(per_pair[pair])}",flush=True)
    finally:
        await p.disconnect()
    allt=[t for tr in per_pair.values() for t in tr]
    print(f"\n===== ATR-ANCHORED TARGET, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allt)
    for s in STRATS: table(s, per_s[s])
    for pair in PAIRS:
        if pair in per_pair: table(pair, per_pair[pair])
asyncio.run(main())

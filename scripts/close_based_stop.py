"""Close-based stop against the live intrabar stop, paired on the same bar path.

The live stop sits at the broker and fills on a touch, 2 ATR from the
entry; section 131 read that the barriers lose and the drift wins.
The one stop variant never measured is a close-confirmed stop: the
position is closed at the bar's close only if that close lies beyond
the 2-ATR level, so a wick through the level that closes back inside
no longer stops the trade out. Two variants, fixed before the data
were seen: A keeps a broker-side catastrophe stop at 3 ATR (touch)
behind the close-based 2-ATR stop; B has no intrabar stop at all and
is an upper bound only, not a rule the bot could run. The target
stays intrabar at 1.5 R and the leash at 24 bars; gaps through a
level are booked at the open. Both variants can lose more than 1 R on
a trade, which weakens the risk limit and is part of the reading.
Acceptance: better than the live rule at paired t > 2 on both
disjoint samples. DAYS_FROM / DAYS_TO select the history window. See
docs/EDGE_FINDINGS.md section 180.
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
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
# (label, close-based stop in stop units or None, intrabar stop in stop units or None)
RULES=[("live intrabar 2ATR",None,1.0),("A close 2ATR + touch 3ATR",1.0,1.5),("B close 2ATR only",1.0,None)]
PAGE_DAYS=35; PAGE_PAUSE=0.5


def book(O,H,L,C,e,d,entry,stop_d,cost_r,close_k,touch_k,n):
    """R of one trade under one rule; returns (r, exit bar, kind)."""
    tp=entry+d*RR*stop_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if touch_k is not None and gap<=-touch_k*stop_d: return gap/stop_d-cost_r, b, "gap"
        adverse=(L[b] if d==1 else H[b]); adv=(adverse-entry)*d
        if touch_k is not None and adv<=-touch_k*stop_d: return -touch_k-cost_r, b, "touch"
        favor=(H[b] if d==1 else L[b])
        if (favor-entry)*d>=RR*stop_d: return RR-cost_r, b, "target"
        if close_k is not None and (C[b]-entry)*d<=-close_k*stop_d: return (C[b]-entry)*d/stop_d-cost_r, b, "close"
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
        rs=[]; kinds=[]
        for _,ck,tk in RULES:
            r,xb,kind=book(O,H,L,C,e,d,entry,stop_d,cost_r,ck,tk,n)
            if r is None: break
            rs.append(r); kinds.append(kind)
            if ck is None: in_until=xb
        if len(rs)==len(RULES): out.append((rs,kinds))
    return out

def table(name, tr):
    R=np.array([x for x,_ in tr]); K=[k for _,k in tr]
    if len(R)<2: return
    base=R[:,0]; se=base.std(ddof=1)/np.sqrt(len(base))
    print(f"\n--- {name}: n={len(R)} live E[R]={base.mean():+.4f} (t={base.mean()/se:+.2f})")
    print(f"{'rule':<28}{'E[R]':>9}{'t':>7}{'diff':>9}{'t_pair':>8}{'win%':>7}{'stop%':>7}{'worst R':>9}{'<-1R %':>8}")
    for i,(lab,_,_) in enumerate(RULES):
        r=R[:,i]; d=r-base; se=r.std(ddof=1)/np.sqrt(len(r))
        sd=d.std(ddof=1); tp=d.mean()/(sd/np.sqrt(len(d))) if sd>0 else float('nan')
        stops=sum(1 for k in K if k[i] in ("touch","close","gap"))/len(K)*100
        print(f"{lab:<28}{r.mean():>+9.4f}{r.mean()/se:>+7.2f}{d.mean():>+9.4f}{tp:>+8.2f}{np.mean(r>0)*100:>7.1f}{stops:>7.0f}{r.min():>+9.2f}{np.mean(r<-1.0-0.1)*100:>8.1f}")

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
    per={}; per_s={s:[] for s in STRATS}
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
                    tr=run(sdf,sg,pair); per[pair].extend(tr); per_s[s].extend(tr)
            print(f"{pair} bars={n} trades={len(per[pair])}",flush=True)
    finally:
        await p.disconnect()
    allt=[t for tr in per.values() for t in tr]
    print(f"\n===== CLOSE-BASED STOP vs LIVE INTRABAR STOP, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allt)
    for s in STRATS: table(s, per_s[s])
asyncio.run(main())

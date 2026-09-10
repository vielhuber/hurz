"""The confirmation as an exit: cut the trade when the next bar closes back inside the level.

Section 181 located the loss — a breakout whose next bar closes back
inside the level it broke loses 0.22-0.29 R at t beyond 14, on both
samples and all three strategies — and showed that acting on it as an
entry filter pays for the information with a delayed entry. This asks
the other half: keep the entry where the live rule has it, and use the
same confirmation as an exit. Three rules are booked on the same bar
path of every live trade, so the trade set is identical and every
difference is paired per trade:

  live  the position runs to the 2-ATR stop, the 1.5 R target or the
        24-bar leash, whichever comes first;
  A     as live, but if the position is still open at the close of the
        bar after the signal and that close lies back inside the level,
        it is closed at that close;
  B     as A, but only if the position is also at a loss there, so a
        trade that slipped inside a moving band while in profit is
        left alone.

Neither variant can lose more than the live rule's 1 R: it exits
strictly earlier, at a close the stop has not yet been reached at.
Occupancy stays on the live rule's schedule so the trade set cannot
drift. Acceptance, fixed before the data were seen: better than the
live rule at paired t > 2 on both disjoint samples. DAYS_FROM /
DAYS_TO select the history window; DUMP writes the per-trade R of
every rule. See docs/EDGE_FINDINGS.md section 182.
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
RULES=["live","A cut on unconfirmed close","B cut if unconfirmed and losing"]
PAGE_DAYS=35; PAGE_PAUSE=0.5


def levels(df, s):
    """The level a signal at bar i broke: the channel of the bars before i."""
    if s=="donchian_breakout": per=20
    elif s=="turtle_breakout": per=55
    else:
        center=df["close"].ewm(span=20, adjust=False).mean()
        return (center+2.0*df["atr_14"]).shift(1).values, (center-2.0*df["atr_14"]).shift(1).values
    return df["high"].shift(1).rolling(per).max().values, df["low"].shift(1).rolling(per).min().values

def book(O,H,L,C,e,d,entry,stop_d,cost_r,lvl,mode,n):
    """R of one trade under one rule; returns (r, exit bar, kind)."""
    tp=entry+d*RR*stop_d; sl=entry-d*stop_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b, "gap"
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b, "stop"
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return RR-cost_r, b, "target"
        if b==e+1 and mode!="live":
            unconfirmed=(C[b]-lvl)*d<=0
            r_now=(float(C[b])-entry)*d/stop_d-cost_r
            if unconfirmed and (mode=="A" or r_now<0):
                return r_now, b, "cut"
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r, e+HOLD, "timeout"
    return None, None, None

def run(df, entries, pair, up, lo):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; n=len(df)
    for e,d in sorted(entries):
        if e<=in_until or e>=n: continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        lvl=up[e] if d==1 else lo[e]
        if not np.isfinite(lvl): continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        rs=[]; kinds=[]
        for mode in ("live","A","B"):
            r,xb,kind=book(O,H,L,C,e,d,entry,stop_d,cost_r,lvl,mode,n)
            if r is None: break
            rs.append(r); kinds.append(kind)
            if mode=="live": in_until=xb
        if len(rs)==3: out.append((rs,kinds))
    return out

def table(name, tr):
    R=np.array([x for x,_ in tr]); K=[k for _,k in tr]
    if len(R)<4: return
    base=R[:,0]; se=base.std(ddof=1)/np.sqrt(len(base))
    print(f"\n--- {name}: n={len(R)} live E[R]={base.mean():+.4f} (t={base.mean()/se:+.2f}) sumR={base.sum():+.1f}")
    print(f"{'rule':<32}{'E[R]':>9}{'t':>7}{'diff':>9}{'t_pair':>8}{'sum R':>9}{'win%':>7}{'cut%':>7}{'worst':>8}")
    for i,lab in enumerate(RULES):
        r=R[:,i]; d=r-base; se=r.std(ddof=1)/np.sqrt(len(r))
        sd=d.std(ddof=1); tp=d.mean()/(sd/np.sqrt(len(d))) if sd>0 else float('nan')
        cut=sum(1 for k in K if k[i]=="cut")/len(K)*100
        print(f"{lab:<32}{r.mean():>+9.4f}{r.mean()/se:>+7.2f}{d.mean():>+9.4f}{tp:>+8.2f}{r.sum():>+9.1f}{np.mean(r>0)*100:>7.1f}{cut:>7.0f}{r.min():>+8.2f}")

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
                    up,lo=levels(sdf,s)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    tr=run(sdf,sg,pair,up,lo); per[pair].extend(tr); per_s[s].extend(tr)
            print(f"{pair} bars={n} trades={len(per[pair])}",flush=True)
    finally:
        await p.disconnect()
    allt=[t for tr in per.values() for t in tr]
    if DUMP:
        np.savez(DUMP, R=np.array([x for x,_ in allt]))
    print(f"\n===== FAILED-BREAKOUT EXIT, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allt)
    for s in STRATS: table(s, per_s[s])
    for pair in PAIRS:
        if pair in per: table(pair, per[pair])
asyncio.run(main())

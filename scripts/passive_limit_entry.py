"""Passive limit entry at the signal close, with the entry half-spread saved.

Runs 4 and 21 measured limit entries with the full round-trip spread
charged on the passive fill and the fill counted when the mid price
touched the limit. Neither is how a resting order trades: a buy limit at
price P fills when the *ask* reaches P, i.e. when the mid has moved a
half-spread below it, and the position then pays the spread only once,
on the exit. This replay models exactly that against the market entry
at the signal close on the three live 1h trend strategies,
router-passed, 2-ATR stop, venue minimum, live widening rule, gap-aware
stop booking, commodity short block, all 26 tradeable instruments. A
stop hit inside the fill bar is counted as a loss (the order of events
inside the bar is unknown, so the limit is measured pessimistically);
K = 1 and K = 3 bars of validity. DAYS_FROM / DAYS_TO select the window.
See docs/EDGE_FINDINGS.md section 117.
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
MODES=["market","limit1","limit3"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
PAGE_DAYS=35; PAGE_PAUSE=0.5

def run(df, entries, pair, mode):
    """Return (list of R, signals eligible, unfilled)."""
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair); eligible=0; unfilled=0
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values
    K=0 if mode=="market" else int(mode[-1])
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        sig_close=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,sig_close)
        if vm>0 and stop_d<vm: stop_d=vm
        # Live widening rule on the market entry's cost; the same stop is
        # then used for the limit variant so only the entry differs.
        cost_full=2.0*fee*sig_close/stop_d
        if cost_full>0.10:
            stop_d*=min(cost_full/0.10,2.0); cost_full=2.0*fee*sig_close/stop_d
            if cost_full>0.10: continue
        eligible+=1
        if K==0:
            entry=sig_close; start=e+1; cost_r=cost_full; check_fill_bar=False
        else:
            h=fee*sig_close; filled=None; entry=None
            for j in range(1,K+1):
                b=e+j
                if b>=len(df): break
                if d==1:
                    if O[b]*(1+fee)<=sig_close: filled=b; entry=O[b]*(1+fee); break
                    if L[b]<=sig_close-h: filled=b; entry=sig_close; break
                else:
                    if O[b]*(1-fee)>=sig_close: filled=b; entry=O[b]*(1-fee); break
                    if H[b]>=sig_close+h: filled=b; entry=sig_close; break
            if filled is None: unfilled+=1; continue
            start=filled; cost_r=fee*entry/stop_d; check_fill_bar=True
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        r=None; last=start+HOLD-1
        for b in range(start,last+1):
            if b>=len(df): break
            if not (check_fill_bar and b==start):
                gap=(O[b]-entry)*d
                if gap<=-stop_d: r=gap/stop_d-cost_r; in_until=b; break
            adverse=L[b] if d==1 else H[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if check_fill_bar and b==start: continue
            favor=H[b] if d==1 else L[b]
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and last<len(df): r=(float(C[last])-entry)*d/stop_d-cost_r; in_until=last
        if r is not None: out.append(r)
    return out, eligible, unfilled

def se(a): return a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float('nan')

def table(name, res):
    base=np.asarray(res["market"][0])
    print(f"\n--- {name}")
    print(f"{'entry':<10}{'signals':>8}{'filled':>8}{'fill%':>7}{'E[R]':>9}{'t':>7}{'sum R':>9}{'R/signal':>10}{'diff':>9}{'t_diff':>8}")
    for m in MODES:
        r=np.asarray(res[m][0]); elig=res[m][1]
        if len(r)<2: continue
        d=r.mean()-base.mean(); t=d/np.sqrt(se(r)**2+se(base)**2) if m!="market" else 0.0
        print(f"{m:<10}{elig:>8}{len(r):>8}{100*len(r)/max(elig,1):>7.1f}{r.mean():>+9.4f}{r.mean()/se(r):>+7.2f}{r.sum():>+9.1f}{r.sum()/max(elig,1):>+10.4f}{d:>+9.4f}{t:>+8.2f}")

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

def new_res(): return {m:([],0,0) for m in MODES}
def add(res, m, tr):
    r,e,u=res[m]; res[m]=(r+tr[0], e+tr[1], u+tr[2])

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    allres=new_res(); per_s={s:new_res() for s in STRATS}; per_pair={}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: print(pair,"no history",flush=True); continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; per_pair[pair]=new_res()
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    for m in MODES:
                        tr=run(sdf,sg,pair,m); add(allres,m,tr); add(per_s[s],m,tr); add(per_pair[pair],m,tr)
            print(f"{pair} bars={n} market trades={len(per_pair[pair]['market'][0])} limit1 fills={len(per_pair[pair]['limit1'][0])}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== PASSIVE LIMIT AT THE SIGNAL CLOSE vs MARKET, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allres)
    for s in STRATS: table(s, per_s[s])
    print("\n--- per instrument: market E[R] / limit1 E[R] / limit3 E[R] / mean half-spread cost saved (R)")
    for pair,res in per_pair.items():
        vals=[np.mean(res[m][0]) if len(res[m][0])>1 else float('nan') for m in MODES]
        print(f"{pair:<10}"+"".join(f"{v:>+9.4f}" for v in vals))
asyncio.run(main())

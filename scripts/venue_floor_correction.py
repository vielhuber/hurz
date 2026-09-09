"""The venue floor re-read: 0.01 % of price, not 1 %.

The dealing rules give minStopOrProfitDistance as {unit PERCENTAGE, value
0.01} next to maxStopOrProfitDistance {PERCENTAGE, 100}: the unit is plain
percent, the minimum is 0.01 % of price (GOLD 0.001 %), and the journal
holds broker-honoured stops at 0.18 % and 0.31 %. The project reads the
value as a fraction and floors every stop at 1.05 % of price, which pins
78 % of trades at a mean 6.5 ATR (EDGE_FINDINGS 124). This replay runs
the three live 1h trend strategies on the router-passed path with the
live widening rule, gap-aware stop booking and the commodity short block
over all 26 tradeable instruments under the live floor (1.05 %) and under
the venue's actual floor (0.0105 %) at 1, 2 and 3 ATR. DAYS_FROM /
DAYS_TO select the window; DUMP stores (r, variant index). See
docs/EDGE_FINDINGS.md section 135.
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
from scripts.spot_backtest import _fee_for
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

PAIRS=["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
# (label, floor as fraction of price, stop_atr); the first is the live setting
VARIANTS=[("live: floor 1.05 %, 2 ATR",0.0105,2.0),("venue floor, 2 ATR",0.000105,2.0),("venue floor, 1 ATR",0.000105,1.0),("venue floor, 3 ATR",0.000105,3.0)]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
DUMP=os.getenv("DUMP","")
SEG=3; RR=1.5; HOLD=24; PLAT="capital_com"
PAGE_DAYS=35; PAGE_PAUSE=0.5

def run(df, entries, pair, floor, stop_atr):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair); skipped=0
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=stop_atr*atr; pinned=False
        vm=floor*entry
        if stop_d<vm: stop_d=vm; pinned=True
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: skipped+=1; continue
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        r=None; kind="timeout"
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            gap=(O[b]-entry)*d
            if gap<=-stop_d: r=gap/stop_d-cost_r; kind="stop"; in_until=b; break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; kind="stop"; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; kind="target"; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None: out.append((r,cost_r,kind,stop_d/entry,pinned))
    return out, skipped

def se(a): return a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float('nan')

def table(name, res, skips):
    base=np.array([x[0] for x in res[0]])
    print(f"\n--- {name}")
    print(f"{'variant':<28}{'n':>6}{'skip':>6}{'net E[R]':>10}{'t':>7}{'cost R':>8}{'gross':>9}{'stop%':>7}{'tgt%':>6}{'to%':>6}{'stop%px':>8}{'pinned%':>8}{'sum R':>9}{'diff':>9}{'t_diff':>8}")
    for i,(label,_,_) in enumerate(VARIANTS):
        r=np.array([x[0] for x in res[i]])
        if len(r)<2: continue
        cost=np.array([x[1] for x in res[i]]); kind=np.array([x[2] for x in res[i]]); sw=np.array([x[3] for x in res[i]]); pin=np.array([x[4] for x in res[i]])
        d=r.mean()-base.mean(); t=d/np.sqrt(se(r)**2+se(base)**2) if i else 0.0
        print(f"{label:<28}{len(r):>6}{skips[i]:>6}{r.mean():>+10.4f}{r.mean()/se(r):>+7.2f}{cost.mean():>8.4f}{(r+cost).mean():>+9.4f}{np.mean(kind=='stop')*100:>7.1f}{np.mean(kind=='target')*100:>6.1f}{np.mean(kind=='timeout')*100:>6.1f}{sw.mean()*100:>8.2f}{pin.mean()*100:>8.1f}{r.sum():>+9.1f}{d:>+9.4f}{t:>+8.2f}")

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
    allres={i:[] for i in range(len(VARIANTS))}; skips={i:0 for i in range(len(VARIANTS))}
    per_s={s:{i:[] for i in range(len(VARIANTS))} for s in STRATS}; per_pair={}
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: print(pair,"no history",flush=True); continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; per_pair[pair]={i:[] for i in range(len(VARIANTS))}
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    for i,(_,floor,sa) in enumerate(VARIANTS):
                        tr,sk=run(sdf,sg,pair,floor,sa); allres[i].extend(tr); skips[i]+=sk; per_s[s][i].extend(tr); per_pair[pair][i].extend(tr)
            print(f"{pair} bars={n} live trades={len(per_pair[pair][0])} venue-floor 2ATR trades={len(per_pair[pair][1])}",flush=True)
    finally:
        await p.disconnect()
    if DUMP:
        np.savez(DUMP, **{f"r{i}":np.array([x[0] for x in allres[i]]) for i in allres},
                 **{f"pair{i}":np.array([pp for pp,res in per_pair.items() for _ in res[i]]) for i in allres})
    print(f"\n===== VENUE FLOOR 1.05 % (live) vs 0.0105 % (actual), router-passed ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", allres, skips)
    for s in STRATS: table(s, per_s[s], {i:0 for i in allres})
    print("\n--- per instrument: live E[R] / venue-floor 2 ATR E[R] / n live / n venue")
    for pair,res in per_pair.items():
        a=np.array([x[0] for x in res[0]]); b=np.array([x[0] for x in res[1]])
        print(f"{pair:<10}{a.mean() if len(a) else float('nan'):>+9.4f}{b.mean() if len(b) else float('nan'):>+9.4f}{len(a):>7}{len(b):>7}")
asyncio.run(main())

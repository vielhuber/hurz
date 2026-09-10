"""The three-sample consistency rule at combination granularity.

Section 192 blocked three instruments by requiring three disjoint
training samples to agree before flagging, with the most recent year
held out — paired +0.0163 R at t = +5.22 on data that did not select
them. The nightly selector does not rank instruments, it ranks
instrument-strategy combinations, so the same rule belongs at that
granularity: donchian on DE40 and keltner on DE40 are separate bets and
one can be sound while the other is not.

The multiplicity is larger and the guard against it is the same. With
~63 combinations surviving the block list, chance alone produces about
eight that read negative in all three training samples, so the training
agreement is again worth nothing on its own and the held-out year
decides alone. A minimum of 30 trades per training sample keeps
combinations whose "consistency" is three tiny samples out of the flag
set.

Acceptance, fixed before the test sample is read:

  (a) the paired difference on the TEST sample (last 365 days) is
      positive at t > 2,
  (b) the flag set is not a restatement of section 192 — the three
      instruments blocked there are excluded from the universe, so any
      effect found here is additional to it,
  (c) the same rule run with the time direction reversed points the
      same way, as it did in section 192.

Failing any of these, nothing is blocked. This script dumps per-trade
(pair, strategy, direction, R) for each sample so the flagging and the
test are computed from the same booking rather than from aggregates of
aggregates.

Baseline is the system as it now stands: ADX ceiling in `gate()`, the
3 x ATR floor from `_min_stop_atr_multiple()`, and the expectancy block
list including section 192's three pairs. Occupancy follows the live
rule; the rule only removes entries. DAYS_FROM / DAYS_TO select the
window, DUMP the output path. See docs/EDGE_FINDINGS.md section 193.
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
from scripts.spot_backtest import _fee_for, _venue_min_distance
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

PAIRS=[p for p in ["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"] if p not in BLOCKED_PAIRS]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
DUMP=os.getenv("DUMP","")
SEG=3; STOP_ATR=2.0; HOLD=24; RR=1.5; PLAT="capital_com"
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

def run(df, entries, pair, strat, atr_floor, sink):
    in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; n=len(df)
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
        sink.append({"pair":pair,"strategy":strat,"direction":int(d),"r":float(r)})

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
    sink=[]
    try:
        for pair in PAIRS:
            bars=await fetch_paced(p, pair)
            if not bars: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; before=len(sink)
            for s in STRATS:
                st=get_strategy(s)
                for k in range(SEG):
                    lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                    sdf=df.iloc[lo_:hi].reset_index(drop=True)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    run(sdf,sg,pair,s,atr_floor,sink)
            print(f"{pair} bars={n} trades={len(sink)-before}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== COMBO DUMP ({DAYS_FROM}-{DAYS_TO} d, atr floor {atr_floor:g}) trades={len(sink)} =====")
    if DUMP:
        with open(DUMP,"w") as fh: json.dump(sink, fh)
        print(f"written {DUMP}")
asyncio.run(main())

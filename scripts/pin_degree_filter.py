"""The pin degree as an exclusion: stops the venue floor has stretched
far past the volatility they were meant to measure.

Section 124 found 78 % of the book pinned — the venue's 1.05 % minimum
distance wider than the intended 2 x ATR — at a mean stop of 6.5 ATR,
and closed with the observation that those trades need a
volatility-scaled distance the venue does not offer. Section 183 showed
the ATR period behind the stop is inert for exactly that reason, and
section 185 showed the target cannot be pulled in below the floor
either. What none of them did was ask whether the worst-pinned trades
should be taken at all.

The mechanism is structural rather than regime-dependent, which is why
it is worth a filter. A trade pinned at 10 ATR carries a 1.5 R target
15 ATR away; inside a 24-bar leash that target is unreachable by
construction, so the trade can only end at the stop or time out near
zero — while paying its full spread either way. If that reasoning
holds, the penalty should appear in every sample, not just the one
whose regime happens to suit it.

Filter thresholds: skip the signal when the stop, after the floor and
the cost-widening rule, exceeds k x ATR for k in 4 / 6 / 8 / 10.
Acceptance, fixed before the data were seen and identical to the rule
that settled the ADX ceiling in section 188:

  (a) the paired difference is positive in the point estimate on ALL
      THREE disjoint samples,
  (b) it reaches t > 2 on at least one,
  (c) the cut segment carries E[R] <= 0 on all three,
  (d) what ships is the mildest threshold meeting (a)-(c), not the one
      with the best t.

Occupancy follows the live rule, so a filtered trade does not free its
slot for a later signal and the measurement understates the filter.
Every signal's live R is set against the variant's R, zero where the
variant does not trade, and compared paired. The stop itself is
untouched — the rule only removes entries, so no risk control is
loosened. DAYS_FROM / DAYS_TO select the window. See
docs/EDGE_FINDINGS.md section 189.
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
SEG=3; STOP_ATR=2.0; HOLD=24; RR=1.5; PLAT="capital_com"
PIN_CAPS=[4.0,6.0,8.0,10.0]
BUCKETS=[(0,3),(3,5),(5,8),(8,12),(12,999)]
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
        r,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,n)
        if r is None: continue
        in_until=xb
        out.append((stop_d/atr, r))
    return out

def report(name, tr):
    if len(tr)<4: return
    pin=np.array([p for p,_ in tr]); live=np.array([r for _,r in tr])
    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    print(f"\n--- {name}: n={len(tr)} live E[R]={live.mean():+.4f} sumR={live.sum():+.1f} "
          f"mean pin={pin.mean():.2f} ATR median={np.median(pin):.2f}")
    print(f"{'pin bucket':<14}{'n':>7}{'E[R]':>10}{'t':>8}")
    for lo,hi in BUCKETS:
        m=(pin>=lo)&(pin<hi)
        if m.sum()<20: continue
        print(f"{f'{lo:g}-{hi:g}' if hi<900 else f'{lo:g}+':<14}{int(m.sum()):>7}{live[m].mean():>+10.4f}{t(live[m]):>+8.2f}")
    print(f"{'cap':<14}{'kept':>7}{'cut':>6}{'cut E[R]':>11}{'rule E[R]':>11}{'diff':>10}{'t_pair':>8}{'rule sumR':>11}")
    for c in PIN_CAPS:
        keep=pin<=c; cut=~keep
        if cut.sum()<10: continue
        rule=np.where(keep, live, 0.0); d=rule-live
        print(f"{f'pin<={c:g}':<14}{int(keep.sum()):>7}{int(cut.sum()):>6}{live[cut].mean():>+11.4f}"
              f"{rule.mean():>+11.4f}{d.mean():>+10.4f}{t(d):>+8.2f}{rule.sum():>+11.1f}")

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
    allt=[]; per_s={s:[] for s in STRATS}; per_pair={}
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
                    tr=run(sdf,sg,pair); allt.extend(tr); per_s[s].extend(tr); per_pair[pair].extend(tr)
            print(f"{pair} bars={n} trades={len(per_pair[pair])}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== PIN DEGREE FILTER ({DAYS_FROM}-{DAYS_TO} d) =====")
    report("ALL", allt)
    for s in STRATS: report(s, per_s[s])
    for pair in PAIRS:
        if pair in per_pair: report(pair, per_pair[pair])
asyncio.run(main())

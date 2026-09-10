"""Does instrument expectancy transfer when three samples have to agree?

Section 130 tested the selector's premise with one prior sample against
one following one and found nothing: quartiles did not carry, and the
rank correlation was -0.22. The venue now serves enough history for a
stricter form of the same question — three training samples must ALL
agree an instrument is negative before it is flagged, and the fourth,
most recent one is the held-out test.

Run offline against the per-instrument tables of section 191, the
training samples (days 366-1,095, 1,096-1,825, 1,826-2,555) flag three
of the 24 instruments present throughout: AUDUSD, GBPCAD, GBPUSD. That
count is exactly the chance expectation under no transfer at all
(24 x 0.5^3 = 3.0), which is why the test sample decides this and the
training agreement does not.

On the recent year those three read -0.096 R against -0.006 for the
other 21, but that is an aggregate of aggregates. This script books the
block as a rule on the test sample the way every other filter in this
log is booked — every signal's live R against the variant's R, zero
where the variant does not trade, paired per trade — so the difference
carries a real standard error.

Acceptance, fixed before the run: the paired difference on the TEST
sample (last 365 days) is positive at t > 2. The training samples are
negative by construction and prove nothing; they are not counted. A
flag set that is merely chance-sized must beat chance on data it did
not choose, or no instrument is blocked.

Baseline is the system as it stands: ADX ceiling in `gate()`, the
3 x ATR floor from `_min_stop_atr_multiple()`. The rule only removes
entries; no risk control is loosened. See docs/EDGE_FINDINGS.md
section 192.
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
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.spot_backtest import _fee_for, _venue_min_distance
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

PAIRS=["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
# Flagged by the three training samples, before the test sample was read.
FLAGGED={"AUDUSD","GBPCAD","GBPUSD"}
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
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

def run(df, entries, pair, atr_floor):
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
        if atr_floor>0 and stop_d/atr<atr_floor: continue
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
            if cost_r>0.10: continue
        r,xb=book(O,H,L,C,e,d,entry,stop_d,cost_r,n)
        if r is None: continue
        in_until=xb
        out.append((pair, r))
    return out

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
    allt=[]
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
                    tr=run(sdf,sg,pair,atr_floor); allt.extend(tr); cnt+=len(tr)
            print(f"{pair} bars={n} trades={cnt}",flush=True)
    finally:
        await p.disconnect()
    names=np.array([p_ for p_,_ in allt]); live=np.array([r for _,r in allt])
    flagged=np.isin(names, list(FLAGGED))
    rule=np.where(flagged, 0.0, live); d=rule-live
    t=lambda v: v.mean()/(v.std(ddof=1)/np.sqrt(len(v))) if len(v)>1 and v.std(ddof=1)>0 else float('nan')
    print(f"\n===== INSTRUMENT CONSISTENCY BLOCK ({DAYS_FROM}-{DAYS_TO} d, atr floor {atr_floor:g}) =====")
    print(f"flagged: {sorted(FLAGGED)}")
    print(f"\nlive:    n={len(live):>5} E[R]={live.mean():+.4f} sumR={live.sum():+.1f} t={t(live):+.2f}")
    print(f"flagged: n={int(flagged.sum()):>5} E[R]={live[flagged].mean():+.4f} sumR={live[flagged].sum():+.1f} t={t(live[flagged]):+.2f}")
    print(f"rest:    n={int((~flagged).sum()):>5} E[R]={live[~flagged].mean():+.4f} sumR={live[~flagged].sum():+.1f} t={t(live[~flagged]):+.2f}")
    print(f"\nrule:    n={len(rule):>5} E[R]={rule.mean():+.4f} sumR={rule.sum():+.1f}")
    print(f"paired diff={d.mean():+.4f}  t={t(d):+.2f}   ACCEPT if t > +2")
    print("\nper flagged instrument (test sample):")
    for f in sorted(FLAGGED):
        m=names==f
        if m.sum()<2: continue
        print(f"  {f:<9} n={int(m.sum()):>4} E[R]={live[m].mean():>+.4f} sumR={live[m].sum():>+7.1f} t={t(live[m]):>+6.2f}")
asyncio.run(main())

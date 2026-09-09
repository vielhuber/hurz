"""The variance ratio read on the live journal: the third sample for section 176.

Section 176 found breakouts after a mean-reverting month (VR(24) of the
prior 720 bars below 1) better than the rest on both disjoint history
samples, the first signal-bar feature with a stable sign — and still
short of the preregistered bar. This reads the same feature on the live
journal of the 1h trend strategies: the VR at each closed trade's
signal bar is reconstructed from the 1h history and the realised R is
taken at the actual fill against the booked stop, as in
scripts/htf_adx_second_look.py. Buckets and edges are those of section
176 (0.805 / 0.914 / 1.046), applied unchanged.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from scripts.walk_forward import _bars_to_df

PLAT="capital_com"
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
EDGES=[0.805,0.914,1.046]
LOOKBACK=720; Q=24
PAGE_DAYS=35; PAGE_PAUSE=0.5
FORWARD_FROM="2026-08-24"


def variance_ratio(df):
    """Causal VR(24) of 1h log returns over the 720 bars before each bar, keyed by bar time."""
    c=df["close"].values.astype(float)
    r=np.full(len(c),np.nan); r[1:]=np.diff(np.log(c))
    out=np.full(len(c),np.nan)
    for i in range(LOOKBACK+1,len(c)):
        w=r[i-LOOKBACK:i]
        if not np.all(np.isfinite(w)): continue
        v1=w.var(ddof=1)
        if v1<=0: continue
        s=np.convolve(w,np.ones(Q),mode="valid")
        out[i]=s.var(ddof=1)/(Q*v1)
    return pd.Series(out, index=pd.to_datetime(df["timestamp"],utc=True))

def stats(a):
    a=np.asarray(a,dtype=float)
    if len(a)<2: return len(a), float('nan'), float('nan')
    return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

def row(label, m, r):
    n,mu,se=stats(r[m]); n2,mu2,se2=stats(r[~m])
    if n<2 or n2<2: return
    diff=mu-mu2; t=diff/np.sqrt(se**2+se2**2)
    print(f"{label:<28}{n:>6}{mu:>+9.4f}{mu/se:>+7.2f}{mu2:>+11.4f}{diff:>+9.4f}{t:>+8.2f}")

def table(name, r, f):
    r=np.array(r); f=np.array(f)
    if len(r)<4: print(f"\n--- {name}: n={len(r)} (too few)"); return
    print(f"\n--- {name}: n={len(r)} E[R]={r.mean():+.4f} (t={r.mean()/(r.std(ddof=1)/np.sqrt(len(r))):+.2f}) VR median={np.median(f):.3f} share VR<1={np.mean(f<1)*100:.0f}%")
    print(f"{'bucket (VR24, prior month)':<28}{'n':>6}{'E[R]':>9}{'t':>7}{'rest E[R]':>11}{'diff':>9}{'t_diff':>8}")
    row("mean-reverting (VR < 1)", f<1.0, r)
    row("strongly MR (VR < 0.8)", f<0.8, r)
    row("trending (VR >= 1.2)", f>=1.2, r)
    bounds=[-np.inf]+EDGES+[np.inf]
    for i in range(len(bounds)-1):
        row(f"[{bounds[i]:>6.3f}, {bounds[i+1]:>6.3f})", (f>=bounds[i])&(f<bounds[i+1]), r)

async def fetch_paced(p, pair, start, end):
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
    from app.utils.singletons import database
    rows=database.select("""
        SELECT pair, strategy, bar_time, created_at, realized_pnl, size,
               COALESCE(fill_price, entry_price) AS px, stop_loss
        FROM spot_trades
        WHERE accepted = 1 AND paper_mode = 0 AND platform = 'capital_com'
          AND exit_time IS NOT NULL AND realized_pnl IS NOT NULL AND size > 0
          AND ABS(COALESCE(fill_price, entry_price) - stop_loss) > 0
          AND COALESCE(outcome, '') <> 'abandoned'
          AND strategy IN ('donchian_breakout','turtle_breakout','keltner_breakout')
        ORDER BY bar_time
    """, ())
    by_pair={}
    for row_ in rows: by_pair.setdefault(row_["pair"],[]).append(row_)
    clear_cache(); p=get_platform(PLAT); await p.connect()
    out=[]
    try:
        for pair,trs in sorted(by_pair.items()):
            times=[pd.Timestamp(str(t["bar_time"]),tz="UTC") for t in trs]
            start=min(times).to_pydatetime()-timedelta(days=60)
            end=min(max(times).to_pydatetime()+timedelta(days=1), datetime.now(timezone.utc))
            bars=await fetch_paced(p, pair, start, end)
            if not bars: print(pair,"no history",flush=True); continue
            vr=variance_ratio(_bars_to_df(bars))
            for t,ts in zip(trs,times):
                v=vr.get(ts, np.nan)
                if not np.isfinite(v): continue
                r=float(t["realized_pnl"])/(abs(float(t["px"])-float(t["stop_loss"]))*float(t["size"]))
                out.append((r,float(v),t["strategy"],str(t["created_at"])>=FORWARD_FROM))
            print(f"{pair} trades={len(trs)} cumulative with VR={len(out)}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== LIVE JOURNAL, 1h trend strategies, realised R at the actual fill, VR(24) of the prior month =====")
    table("ALL closed", [x[0] for x in out], [x[1] for x in out])
    fw=[x for x in out if x[3]]
    table(f"since {FORWARD_FROM} (forward)", [x[0] for x in fw], [x[1] for x in fw])
    for s in STRATS:
        sub=[x for x in out if x[2]==s]
        table(s, [x[0] for x in sub], [x[1] for x in sub])
asyncio.run(main())

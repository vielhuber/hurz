"""The two-bar confirmation read on the live journal: the third sample for section 181.

For every closed live trade of the three 1h trend strategies, the
level its signal broke and the close of the bar after the signal are
reconstructed from the 1h history; the trade's realised R at the
actual fill is set against the variant of section 181, entered at
that later close with its own 2-ATR stop and simulated at the audited
cost, or 0 where the close had already slipped back inside the level.
The pairs are compared per signal as in scripts/two_bar_confirmation.py.
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
from app.strategies import add_indicators
from scripts.spot_backtest import _fee_for, _venue_min_distance
from scripts.walk_forward import _bars_to_df

PLAT="capital_com"
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
RR=1.5; STOP_ATR=2.0; HOLD=24
PAGE_DAYS=35; PAGE_PAUSE=0.5
FORWARD_FROM="2026-08-24"


def levels(df, s):
    if s=="donchian_breakout": per=20
    elif s=="turtle_breakout": per=55
    else:
        center=df["close"].ewm(span=20, adjust=False).mean()
        return (center+2.0*df["atr_14"]).shift(1).values, (center-2.0*df["atr_14"]).shift(1).values
    return df["high"].shift(1).rolling(per).max().values, df["low"].shift(1).rolling(per).min().values

def book(O,H,L,C,A,pair,fee,e,d,n):
    atr=A[e]
    if not np.isfinite(atr) or atr<=0: return None
    entry=float(C[e]); stop_d=STOP_ATR*atr
    vm=_venue_min_distance(PLAT,pair,entry)
    if vm>0 and stop_d<vm: stop_d=vm
    cost_r=2.0*fee*entry/stop_d
    if cost_r>0.10:
        stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10: return None
    tp=entry+d*RR*stop_d; sl=entry-d*stop_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return RR-cost_r
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r
    return None

def paired(name, rows):
    if len(rows)<4: print(f"\n--- {name}: n={len(rows)} (too few)"); return
    L=np.array([r[0] for r in rows]); V=np.array([r[1] for r in rows]); sk=(V==0.0)
    d=V-L; t=d.mean()/(d.std(ddof=1)/np.sqrt(len(d)))
    print(f"\n--- {name}: n={len(rows)} live E[R]={L.mean():+.4f} sumR={L.sum():+.1f} | variant E[R]={V[~sk].mean() if (~sk).any() else float('nan'):+.4f} n={(~sk).sum()} sumR={V.sum():+.1f} | skipped {sk.sum()} at live E[R]={L[sk].mean() if sk.any() else float('nan'):+.4f}")
    print(f"    paired variant - live: {d.mean():+.4f} R per signal, t={t:+.2f}, sum {d.sum():+.1f} R")

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
        SELECT pair, strategy, direction, bar_time, created_at, realized_pnl, size,
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
    for row in rows: by_pair.setdefault(row["pair"],[]).append(row)
    clear_cache(); p=get_platform(PLAT); await p.connect()
    out=[]
    try:
        for pair,trs in sorted(by_pair.items()):
            times=[pd.Timestamp(str(t["bar_time"]),tz="UTC") for t in trs]
            start=min(times).to_pydatetime()-timedelta(days=20)
            end=min(max(times).to_pydatetime()+timedelta(days=3), datetime.now(timezone.utc))
            bars=await fetch_paced(p, pair, start, end)
            if not bars: print(pair,"no history",flush=True); continue
            df=add_indicators(_bars_to_df(bars)); ts=pd.to_datetime(df["timestamp"],utc=True)
            idx={t:i for i,t in enumerate(ts)}
            O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values; A=df["atr_14"].values; n=len(df)
            fee=_fee_for(PLAT,pair); lv={s:levels(df,s) for s in STRATS}
            for t,tt in zip(trs,times):
                e=idx.get(tt)
                if e is None or e+1>=n: continue
                d=1 if str(t["direction"]).lower() in ("1","long","buy") else -1
                up,lo=lv[t["strategy"]]; lvl=up[e] if d==1 else lo[e]
                if not np.isfinite(lvl): continue
                r_live=float(t["realized_pnl"])/(abs(float(t["px"])-float(t["stop_loss"]))*float(t["size"]))
                confirmed=(C[e+1]-lvl)*d>0
                r_var=book(O,H,L,C,A,pair,fee,e+1,d,n) if confirmed else 0.0
                if r_var is None: continue
                out.append((r_live,r_var,t["strategy"],str(t["created_at"])>=FORWARD_FROM))
            print(f"{pair} trades={len(trs)} cumulative paired={len(out)}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== LIVE JOURNAL, 1h trend strategies: realised live R against the next-close variant =====")
    paired("ALL closed", [(a,b) for a,b,_,_ in out])
    paired(f"since {FORWARD_FROM} (forward)", [(a,b) for a,b,_,f in out if f])
    for s in STRATS: paired(s, [(a,b) for a,b,st,_ in out if st==s])
asyncio.run(main())

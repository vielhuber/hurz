"""Re-entry within 24 h of a target exit, on samples no earlier run selected on.

Section 119 found re-entries within a day of a target exit negative on
both walk-forward samples (-0.144 and -0.086 R) but below the block bar
and not preregistered. This script reads the same split on (A) the
instruments excluded from the live book, three years, merged
one-position timeline, router-passed, 2-ATR stop, costs charged at their
actual spread without the ceiling skip; and (B) the live journal of the
1h trend strategies: closed trades whose instrument had a target exit in
the 24 h before the signal bar, against the rest. MODE=excluded or
MODE=journal. See docs/EDGE_FINDINGS.md section 122.
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
from app.strategies import get_strategy, add_indicators
from app.spot_trading.trading_blocks import BLOCKED_PAIRS, direction_blocked
from scripts.spot_backtest import _fee_for, _venue_min_distance
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
MODE=os.getenv("MODE","journal")
DAYS_FROM=int(os.getenv("DAYS_FROM","1095")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"; STOP_COOLDOWN_H=6; WINDOW_H=24
PAGE_DAYS=35; PAGE_PAUSE=0.5

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values
    T=pd.to_datetime(df["timestamp"],utc=True).values.astype("datetime64[s]").astype(np.int64)
    prev_kind=None; prev_exit_bar=None
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        since=(T[e]-T[prev_exit_bar])/3600.0 if prev_exit_bar is not None else float('inf')
        if prev_kind=="stop" and since<=STOP_COOLDOWN_H: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        r=None; kind=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            gap=(O[b]-entry)*d
            if gap<=-stop_d: r=gap/stop_d-cost_r; kind="stop"; in_until=b; break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; kind="stop"; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; kind="target"; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; kind="timeout"; in_until=e+HOLD
        if r is None: continue
        out.append((r, prev_kind=="target" and since<=WINDOW_H))
        prev_kind=kind; prev_exit_bar=in_until
    return out

def se(a): return a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float('nan')

def split(name, pairs):
    r=np.array([x for x,_ in pairs]); m=np.array([f for _,f in pairs],bool)
    if m.sum()<2 or (~m).sum()<2: print(f"--- {name}: n={len(r)} too few"); return
    a=r[m]; b=r[~m]; d=a.mean()-b.mean(); t=d/np.sqrt(se(a)**2+se(b)**2)
    print(f"--- {name}: n={len(r)} E[R]={r.mean():+.4f}")
    print(f"  after target <= 24h: n={m.sum():>5} E[R]={a.mean():+.4f} t={a.mean()/se(a):+.2f} | rest n={(~m).sum():>5} E[R]={b.mean():+.4f} | diff={d:+.4f} t_diff={t:+.2f}")

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

async def excluded(p):
    now=datetime.now(timezone.utc); start=now-timedelta(days=DAYS_FROM); end=now-timedelta(days=DAYS_TO)
    allt=[]
    for pair in sorted(BLOCKED_PAIRS):
        bars=await fetch_paced(p, pair, start, end)
        if not bars: print(pair,"no history",flush=True); continue
        df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; cnt=0
        for k in range(SEG):
            lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
            sdf=df.iloc[lo_:hi].reset_index(drop=True)
            merged=set()
            for s in STRATS:
                st=get_strategy(s)
                merged.update((x.index,x.direction) for x in st(sdf,{})
                              if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction))
            tr=run(sdf,sorted(merged),pair); allt.extend(tr); cnt+=len(tr)
        print(f"{pair} bars={n} trades={cnt}",flush=True)
    print(f"\n===== (A) EXCLUDED INSTRUMENTS, merged timeline, router-passed, costs charged, no ceiling skip ({DAYS_FROM}-{DAYS_TO} d) =====")
    split("ALL", allt)

def journal():
    from app.utils.singletons import database
    rows=database.select("""
        SELECT pair, strategy, bar_time, exit_time, outcome, realized_pnl, size,
               COALESCE(fill_price, entry_price) AS px, stop_loss, created_at
        FROM spot_trades
        WHERE accepted = 1 AND paper_mode = 0 AND platform = 'capital_com'
          AND exit_time IS NOT NULL AND realized_pnl IS NOT NULL AND size > 0
          AND ABS(COALESCE(fill_price, entry_price) - stop_loss) > 0
          AND COALESCE(outcome, '') <> 'abandoned'
          AND strategy IN ('donchian_breakout','turtle_breakout','keltner_breakout','momentum')
        ORDER BY bar_time
    """, ())
    wins={}
    for r in rows:
        if r["outcome"]=="win": wins.setdefault(r["pair"],[]).append(pd.Timestamp(str(r["exit_time"])))
    pairs=[]; fw=[]
    for r in rows:
        bt=pd.Timestamp(str(r["bar_time"]))
        flag=any(0<=(bt-w).total_seconds()<=WINDOW_H*3600 for w in wins.get(r["pair"],[]))
        R=float(r["realized_pnl"])/(abs(float(r["px"])-float(r["stop_loss"]))*float(r["size"]))
        pairs.append((R,flag))
        if str(r["created_at"])>="2026-08-24": fw.append((R,flag))
    print("\n===== (B) LIVE JOURNAL, 1h trend strategies, realised R at the fill =====")
    print("outcomes:", sorted({r["outcome"] for r in rows}))
    split("ALL closed", pairs); split("forward since 2026-08-24", fw)

async def main():
    if MODE=="journal": journal(); return
    clear_cache(); p=get_platform(PLAT); await p.connect()
    try: await excluded(p)
    finally: await p.disconnect()
asyncio.run(main())

"""Second look at the 4h-ADX median split on data no earlier run selected on.

Section 114 found that 1h breakouts fired while the 4h chart already
trends (4h ADX(14) >= 25.5, the median) lose against the rest on both
walk-forward samples, but the preregistered block bar was missed. This
script reads the same split, fixed at 25.532, on two samples that were
not part of that selection: (A) the instruments excluded from the live
book (cost and expectancy blocklists), three years, costs charged at
their actual spread without the 10 % ceiling skip so trades exist; and
(B) the live journal of the 1h trend strategies, with the 4h ADX at each
trade's signal bar reconstructed from the 1h history. MODE=excluded or
MODE=journal selects the part. See docs/EDGE_FINDINGS.md section 115.
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
from app.strategies.base import _compute_adx
from app.spot_trading.trading_blocks import BLOCKED_PAIRS, direction_blocked
from scripts.spot_backtest import _fee_for, _venue_min_distance
from app.spot_trading.regime import gate
from scripts.walk_forward import _bars_to_df

SPLIT=25.532
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
JOURNAL_STRATS=STRATS+["momentum"]
MODE=os.getenv("MODE","journal")
DAYS_FROM=int(os.getenv("DAYS_FROM","1095")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
PAGE_DAYS=35; PAGE_PAUSE=0.5

def htf_adx_series(df):
    """ADX(14) of the 4h bars keyed by 4h open time."""
    ts=pd.to_datetime(df["timestamp"],utc=True)
    key=ts.dt.floor("4h")
    agg=df.assign(_k=key).groupby("_k").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"))
    adx=_compute_adx(agg.reset_index(drop=True)); adx.index=agg.index
    return key, adx

def htf_adx(df):
    key,adx=htf_adx_series(df)
    return (key-pd.Timedelta(hours=4)).map(adx).values.astype(float)

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; X=df["adx_4h"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        feat=X[e]
        if not np.isfinite(feat): continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        cost_r=2.0*fee*entry/stop_d
        # Live widening rule kept, the ceiling skip is not: these
        # instruments are blocked for cost, the question is the split.
        if cost_r>0.10:
            stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d
        r=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            gap=(O[b]-entry)*d
            if gap<=-stop_d: r=gap/stop_d-cost_r; in_until=b; break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None: out.append((r,feat,cost_r))
    return out

def split_table(name, r, feat, gross=None):
    r=np.asarray(r,float); feat=np.asarray(feat,float)
    up=feat>=SPLIT; lo=~up
    def se(x): return x.std(ddof=1)/np.sqrt(len(x)) if len(x)>1 else float('nan')
    print(f"\n--- {name}: n={len(r)} E[R]={r.mean():+.4f} 4h ADX median={np.median(feat):.1f}")
    print(f"{'half':<22}{'n':>6}{'E[R]':>9}{'t':>7}{'win%':>7}")
    for lab,m in (("4h ADX < 25.5",lo),("4h ADX >= 25.5",up)):
        if m.sum()<2: continue
        print(f"{lab:<22}{m.sum():>6}{r[m].mean():>+9.4f}{r[m].mean()/se(r[m]):>+7.2f}{(r[m]>0).mean()*100:>7.1f}")
    if up.sum()>1 and lo.sum()>1:
        d=r[up].mean()-r[lo].mean(); t=d/np.sqrt(se(r[up])**2+se(r[lo])**2)
        print(f"upper - lower: {d:+.4f} R, t_diff = {t:+.2f}")
        if gross is not None:
            g=np.asarray(gross,float)
            dg=g[up].mean()-g[lo].mean(); tg=dg/np.sqrt(se(g[up])**2+se(g[lo])**2)
            print(f"gross of cost: lower {g[lo].mean():+.4f} upper {g[up].mean():+.4f} diff {dg:+.4f} t_diff {tg:+.2f}")

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
    now=datetime.now(timezone.utc)
    start=now-timedelta(days=DAYS_FROM); end=now-timedelta(days=DAYS_TO)
    per_s={s:[] for s in STRATS}; allt=[]
    for pair in sorted(BLOCKED_PAIRS):
        bars=await fetch_paced(p, pair, start, end)
        if not bars: print(pair,"no history",flush=True); continue
        df=add_indicators(_bars_to_df(bars)); df["adx_4h"]=htf_adx(df); n=len(df); seg=n//SEG; cnt=0
        for s in STRATS:
            st=get_strategy(s)
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                sg=[(x.index,x.direction) for x in st(sdf,{})
                    if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                tr=run(sdf,sg,pair); per_s[s].extend(tr); allt.extend(tr); cnt+=len(tr)
        print(f"{pair} bars={n} trades={cnt}",flush=True)
    print(f"\n===== (A) EXCLUDED INSTRUMENTS, router-passed, 2-ATR stop, costs charged, no ceiling skip ({DAYS_FROM}-{DAYS_TO} d) =====")
    r=[x[0] for x in allt]; f=[x[1] for x in allt]; g=[x[0]+x[2] for x in allt]
    split_table("ALL", r, f, g)
    for s in STRATS:
        split_table(s, [x[0] for x in per_s[s]], [x[1] for x in per_s[s]], [x[0]+x[2] for x in per_s[s]])

async def journal(p):
    from app.utils.singletons import database
    rows=database.select("""
        SELECT pair, strategy, bar_time, created_at, realized_pnl, size,
               COALESCE(fill_price, entry_price) AS px, stop_loss
        FROM spot_trades
        WHERE accepted = 1 AND paper_mode = 0 AND platform = 'capital_com'
          AND exit_time IS NOT NULL AND realized_pnl IS NOT NULL AND size > 0
          AND ABS(COALESCE(fill_price, entry_price) - stop_loss) > 0
          AND COALESCE(outcome, '') <> 'abandoned'
          AND strategy IN ('donchian_breakout','turtle_breakout','keltner_breakout','momentum')
        ORDER BY bar_time
    """, ())
    by_pair={}
    for row in rows: by_pair.setdefault(row["pair"],[]).append(row)
    out=[]
    for pair,trs in sorted(by_pair.items()):
        times=[pd.Timestamp(str(t["bar_time"]),tz="UTC") for t in trs]
        start=min(times).to_pydatetime()-timedelta(days=45)
        end=min(max(times).to_pydatetime()+timedelta(days=1), datetime.now(timezone.utc))
        bars=await fetch_paced(p, pair, start, end)
        if not bars: print(pair,"no history",flush=True); continue
        key,adx=htf_adx_series(_bars_to_df(bars))
        for t,ts in zip(trs,times):
            k=ts.floor("4h")-pd.Timedelta(hours=4)
            v=adx.get(k, np.nan)
            if not np.isfinite(v): continue
            r=float(t["realized_pnl"])/(abs(float(t["px"])-float(t["stop_loss"]))*float(t["size"]))
            out.append((r,float(v),t["strategy"],str(t["created_at"])>="2026-08-24"))
        print(f"{pair} trades={len(trs)} cumulative with 4h ADX={len(out)}",flush=True)
    print(f"\n===== (B) LIVE JOURNAL, 1h trend strategies, realised R at the actual fill =====")
    r=[x[0] for x in out]; f=[x[1] for x in out]
    split_table("ALL closed", r, f)
    fw=[x for x in out if x[3]]
    split_table("since 2026-08-24 (forward)", [x[0] for x in fw], [x[1] for x in fw])
    for s in JOURNAL_STRATS:
        sub=[x for x in out if x[2]==s]
        if len(sub)>=10: split_table(s, [x[0] for x in sub], [x[1] for x in sub])

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    try:
        if MODE=="journal": await journal(p)
        else: await excluded(p)
    finally:
        await p.disconnect()
asyncio.run(main())

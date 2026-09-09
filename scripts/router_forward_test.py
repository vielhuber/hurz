"""Forward test of the ADX router on the journal's rejected intents.

Since 2026-08-24 the live loop journals every intent the router refuses
with its pair, direction, signal price, stop and target. Replaying those
intents against the 1h history that followed gives the counterfactual
result of the trades the router kept out, on signals the simulator never
selected; the accepted intents are replayed the same way so both sides
share one method (24-bar hold from the signal bar, gap-aware stop, the
audited spread). The router's standing rule (EDGE_FINDINGS 46b, 49e): it
stays on until a forward test reads passed minus rejected below t = -2.
Read-only. See docs/EDGE_FINDINGS.md section 127.
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
from scripts.spot_backtest import _fee_for
from scripts.walk_forward import _bars_to_df

SINCE=os.getenv("SINCE","2026-08-24"); HOLD=24; PLAT="capital_com"

def replay(df, intents, pair):
    fee=_fee_for(PLAT,pair)
    ts=pd.to_datetime(df["timestamp"],utc=True).values.astype("datetime64[s]").astype(np.int64)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    out=[]
    for it in intents:
        bt=int(pd.Timestamp(str(it["bar_time"]),tz="UTC").timestamp())
        idx=np.searchsorted(ts, bt)
        if idx>=len(ts) or ts[idx]!=bt: continue
        d=int(it["direction"]); entry=float(it["entry_price"]); sl=float(it["stop_loss"]); tp=float(it["take_profit"])
        stop_d=abs(entry-sl)
        if stop_d<=0: continue
        cost_r=2.0*fee*entry/stop_d; r=None
        for b in range(idx+1, idx+HOLD+1):
            if b>=len(df): break
            gap=(O[b]-entry)*d
            if gap<=-stop_d: r=gap/stop_d-cost_r; break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=(abs(tp-entry)/stop_d)-cost_r; break
        if r is None and idx+HOLD<len(df): r=(float(C[idx+HOLD])-entry)*d/stop_d-cost_r
        if r is not None: out.append((r, it["strategy"], it["accepted"], it.get("entry_adx")))
    return out

def se(a): return a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else float('nan')

async def main():
    from app.utils.singletons import database
    rows=database.select("""
        SELECT pair, strategy, direction, entry_price, stop_loss, take_profit, bar_time, accepted, entry_adx, error
        FROM spot_trades
        WHERE platform='capital_com' AND paper_mode=0 AND created_at >= %s
          AND bar_time IS NOT NULL AND stop_loss IS NOT NULL AND take_profit IS NOT NULL
          AND (accepted=1 OR COALESCE(error,'') LIKE 'skipped: regime filter%%')
        ORDER BY bar_time
    """, (SINCE,))
    by={}
    for r in rows: by.setdefault(r["pair"],[]).append(r)
    clear_cache(); p=get_platform(PLAT); await p.connect(); res=[]
    try:
        for pair,its in sorted(by.items()):
            times=[pd.Timestamp(str(i["bar_time"]),tz="UTC") for i in its]
            start=min(times).to_pydatetime()-timedelta(hours=2)
            end=min(max(times).to_pydatetime()+timedelta(hours=HOLD+2), datetime.now(timezone.utc))
            bars=None
            for attempt in range(4):
                try: bars=await p.fetch_history(pair, from_ts=start, to_ts=end, resolution="1h"); break
                except Exception as ex: print(pair,"FETCH FAIL",attempt,str(ex)[:80],flush=True); await asyncio.sleep(3)
            if not bars: print(pair,"no history",flush=True); continue
            tr=replay(_bars_to_df(bars), its, pair); res.extend(tr)
            print(f"{pair} intents={len(its)} replayed={len(tr)}",flush=True)
            await asyncio.sleep(0.5)
    finally:
        await p.disconnect()
    r=np.array([x[0] for x in res]); acc=np.array([bool(x[2]) for x in res])
    print(f"\n===== ROUTER FORWARD TEST on journalled intents since {SINCE} (replayed, 24-bar hold, audited spread) =====")
    for label,m in (("accepted by router (traded)",acc),("rejected by router",~acc)):
        a=r[m]
        if len(a)>1: print(f"{label:<30} n={len(a):>4} E[R]={a.mean():+.4f} t={a.mean()/se(a):+.2f} win%={(a>0).mean()*100:.1f} sum R={a.sum():+.1f}")
    a=r[acc]; b=r[~acc]
    if len(a)>1 and len(b)>1:
        d=a.mean()-b.mean(); print(f"passed - rejected = {d:+.4f} R, t = {d/np.sqrt(se(a)**2+se(b)**2):+.2f}")
    adx=np.array([x[3] if x[3] is not None else np.nan for x in res],float)
    for lo,hi in ((0,20),(20,25),(25,30),(30,40),(40,100)):
        m=(adx>=lo)&(adx<hi)
        if m.sum()>1: print(f"  ADX [{lo:>2},{hi:>3}): n={m.sum():>4} E[R]={r[m].mean():+.4f} t={r[m].mean()/se(r[m]):+.2f}")
    print("\nper strategy (rejected): ")
    for s in sorted({x[1] for x in res}):
        m=np.array([x[1]==s for x in res])&~acc
        if m.sum()>1: print(f"  {s:<22} n={m.sum():>4} E[R]={r[m].mean():+.4f} t={r[m].mean()/se(r[m]):+.2f}")
asyncio.run(main())

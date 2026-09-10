"""Spread mean reversion between related instruments: the one signal family never opened.

Section 7 tested multi-timeframe confirmation, volatility filters, a
BTC lead and cross-sectional relative strength; a relative-value bet
between two related instruments was never among them, and the retired
single-instrument mean-reversion family (section 2) asked a different
question. Rule, fixed before the data were seen: z-score of the log
ratio of the two closes against its causal 240-bar mean and standard
deviation; enter when the close crosses |z| = 2 (short the ratio when
z rises through +2: short A, long B; long the ratio when z falls
through -2), one position per pair at a time; exit at the close when z
crosses back through 0 (target), when |z| reaches 3.5 (stop), or after
48 bars (leash). Risk is the log-ratio distance from the entry to the
stop level; both legs are charged their audited round-trip spread, and
a signal whose cost exceeds the live 10 % ceiling is cost-skipped, not
traded. Acceptance is pooled E[R] > 0 at t > 2 on both disjoint
samples. DAYS_FROM / DAYS_TO select the history window. See
docs/EDGE_FINDINGS.md section 177.
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

SPREADS=[("OIL_BRENT","OIL_CRUDE"),("GOLD","SILVER"),("US500","US100"),("US500","US30"),
         ("DE40","EU50"),("DE40","FR40"),("EURUSD","GBPUSD"),("AUDUSD","NZDUSD"),("BTCUSD","ETHUSD")]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
PLAT="capital_com"
WINDOW=240; Z_IN=2.0; Z_STOP=3.5; HOLD=48; COST_CEIL=0.10
PAGE_DAYS=35; PAGE_PAUSE=0.5


def simulate(lr, fee_a, fee_b):
    """Trades of one spread: (r, exit kind); plus the number of cost-skipped signals."""
    s=pd.Series(lr); mu=s.rolling(WINDOW).mean().values; sd=s.rolling(WINDOW).std(ddof=1).values
    z=(lr-mu)/sd
    out=[]; skipped=0; in_until=-1; n=len(lr)
    for e in range(WINDOW,n-1):
        if e<=in_until or not np.isfinite(z[e]) or not np.isfinite(z[e-1]): continue
        if z[e]>=Z_IN and z[e-1]<Z_IN: d=-1
        elif z[e]<=-Z_IN and z[e-1]>-Z_IN: d=1
        else: continue
        risk=(Z_STOP-abs(z[e]))*sd[e]
        if not np.isfinite(risk) or risk<=0: continue
        cost_r=2.0*(fee_a+fee_b)/risk
        if cost_r>COST_CEIL: skipped+=1; continue
        r=None; kind=None
        for b in range(e+1,min(e+HOLD,n-1)+1):
            move=d*(lr[b]-lr[e])
            zb=z[b] if np.isfinite(z[b]) else (lr[b]-mu[e])/sd[e]
            if -d*zb>=Z_STOP: r=move/risk-cost_r; kind="stop"; break
            if d*zb>=0: r=move/risk-cost_r; kind="target"; break
            if b==e+HOLD: r=move/risk-cost_r; kind="timeout"; break
        if r is None: continue
        in_until=b; out.append((r,kind))
    return out, skipped

def table(name, tr, skipped):
    r=np.array([x for x,_ in tr]); k=[y for _,y in tr]
    if len(r)<2: print(f"{name:<20} n={len(r)} (too few) cost-skipped={skipped}"); return
    se=r.std(ddof=1)/np.sqrt(len(r))
    print(f"{name:<20}{len(r):>6}{r.mean():>+9.4f}{r.mean()/se:>+7.2f}{np.mean(r>0)*100:>7.1f}"
          f"{k.count('target')/len(k)*100:>8.0f}{k.count('stop')/len(k)*100:>7.0f}{k.count('timeout')/len(k)*100:>9.0f}{skipped:>9}{r.sum():>+9.1f}")

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
    cache={}; results={}
    try:
        for a,b in SPREADS:
            for pair in (a,b):
                if pair not in cache:
                    bars=await fetch_paced(p, pair)
                    cache[pair]=_bars_to_df(bars).set_index("timestamp")["close"] if bars else None
            if cache[a] is None or cache[b] is None: print(a,b,"no history",flush=True); continue
            j=pd.concat([cache[a].rename("a"),cache[b].rename("b")],axis=1,join="inner").sort_index()
            lr=np.log(j["a"].values.astype(float)/j["b"].values.astype(float))
            tr,sk=simulate(lr,_fee_for(PLAT,a),_fee_for(PLAT,b))
            results[f"{a}/{b}"]=(tr,sk)
            print(f"{a}/{b} bars={len(j)} trades={len(tr)} cost-skipped={sk}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== SPREAD MEAN REVERSION, z(240) in at 2 / out at 0 / stop 3.5 / leash 48, both legs charged ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'spread':<20}{'n':>6}{'E[R]':>9}{'t':>7}{'win%':>7}{'target%':>8}{'stop%':>7}{'timeout%':>9}{'skipped':>9}{'sum R':>9}")
    allt=[]; allsk=0
    for name,(tr,sk) in results.items():
        table(name,tr,sk); allt.extend(tr); allsk+=sk
    table("ALL",allt,allsk)
asyncio.run(main())

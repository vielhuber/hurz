"""Entry timing on the one-position-per-instrument timeline.

Signal-bar close versus next open versus a limit at the close valid one or
three bars, on the cost-charging simulator at the live 2-ATR stop.
DAYS_FROM / DAYS_TO select the history window. See docs/EDGE_FINDINGS.md
section 91.
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
from scripts.spot_backtest import _fee_for, _venue_min_distance
from scripts.walk_forward import _bars_to_df

PAIRS = ["BTCUSD","ETHUSD","OIL_CRUDE","OIL_BRENT","GOLD","DE40","US500","US30","EURUSD","AUDUSD"]
STOPS = [2.0]
MODES=['close','next_open','limit1','limit3']
TO={x:0 for x in STOPS}
STRATS = ["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; HOLD=24; PLAT="capital_com"

def run_trades(df, entries, pair, MODE):
    STOP_ATR=2.0; O=df['open'].values
    rs=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    H=df["high"].values; L=df["low"].values; C=df["close"].values; A=df["atr_14"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        sig_close=float(C[e]); stop_d=STOP_ATR*atr
        if MODE=='close': entry=sig_close
        elif MODE=='next_open':
            if e+1>=len(df): continue
            entry=float(O[e+1]); e=e+1
        else:
            K=1 if MODE=='limit1' else 3; filled=None
            for j in range(1,K+1):
                if e+j>=len(df): break
                if (d==1 and L[e+j]<=sig_close) or (d==-1 and H[e+j]>=sig_close): filled=e+j; break
            if filled is None: continue
            entry=sig_close if not ((d==1 and O[filled]<sig_close) or (d==-1 and O[filled]>sig_close)) else float(O[filled]); e=filled
        vm=_venue_min_distance(PLAT,pair,entry)
        if vm>0 and stop_d<vm: stop_d=vm
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d; cost_r=2.0*fee*entry/stop_d
        r=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD; pass
        if r is not None: rs.append((r, cost_r, df['timestamp'].iloc[e], df['timestamp'].iloc[min(in_until, len(df)-1)]))
    return rs

def stats(a):
    a=np.asarray([x[0] for x in a]); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))
def costs(a):
    return float(np.mean([x[1] for x in a]))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={P:[] for P in MODES}; strats=[get_strategy(x) for x in STRATS]
    try:
        for pair in PAIRS:
            bars=None
            for attempt in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
                except Exception as ex:
                    print(pair,"FETCH FAIL",attempt,str(ex)[:80],flush=True); await asyncio.sleep(3)
            if bars is None: continue
            df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG
            print(f"{pair} bars={n}",flush=True)
            for k in range(SEG):
                lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                sdf=df.iloc[lo_:hi].reset_index(drop=True)
                merged=sorted({(x.index,x.direction) for st in strats for x in st(sdf,{})})
                for P in MODES:
                    res[P].extend(run_trades(sdf,merged,pair,P))
            await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== ENTRY TIMING, ONE POSITION PER INSTRUMENT ({DAYS_FROM}-{DAYS_TO} d) =====")
    def st(x): x=np.asarray([t[0] for t in x]); return len(x), x.mean(), x.std(ddof=1)/np.sqrt(len(x))
    nb,mb,sb=st(res['close']); nsig=len(res['close'])
    for m in MODES:
        n,mu,se=st(res[m]); print(f"{m:<10} n={n:>5} fill%={n/nsig*100:>5.1f} E[R]={mu:+.4f} t0={mu/se:+.2f} sumR={mu*n:+.1f} diff_vs_close={mu-mb:+.4f} t={(mu-mb)/np.sqrt(se**2+sb**2) if m!='close' else 0:+.2f}")
asyncio.run(main())

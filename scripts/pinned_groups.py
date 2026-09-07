"""Pinned versus ATR-bound instruments on the cost-charging walk-forward simulator.

Instruments the venue floor pins (indices, FX) against those where the
2-ATR stop binds (crypto, gold, oils): signal expectancy against
count-matched random entries per group and per instrument. DAYS_FROM /
DAYS_TO select the history window. See docs/EDGE_FINDINGS.md section 65.
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

GROUPS={"atr_bound":["BTCUSD","ETHUSD","GOLD","OIL_CRUDE","OIL_BRENT"],"pinned":["DE40","US500","US30","EURUSD","AUDUSD"]}
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
DAYS_FROM=int(os.getenv("DAYS_FROM","365")); DAYS_TO=int(os.getenv("DAYS_TO","0"))
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
rng=np.random.default_rng(3)

def run(df, entries, pair):
    out=[]; in_until=-1; fee=_fee_for(PLAT,pair)
    H=df["high"].values; L=df["low"].values; C=df["close"].values; A=df["atr_14"].values
    for e,d in sorted(entries):
        if e<=in_until or e>=len(df): continue
        atr=A[e]
        if not np.isfinite(atr) or atr<=0: continue
        entry=float(C[e]); stop_d=STOP_ATR*atr
        vm=_venue_min_distance(PLAT,pair,entry); pinned = vm>0 and stop_d<vm
        if pinned: stop_d=vm
        tp=entry+d*RR*stop_d; sl=entry-d*stop_d; cost_r=2.0*fee*entry/stop_d
        r=None
        for b in range(e+1,e+HOLD+1):
            if b>=len(df): break
            adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
            if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): r=-1.0-cost_r; in_until=b; break
            if (d==1 and favor>=tp) or (d==-1 and favor<=tp): r=RR-cost_r; in_until=b; break
        if r is None and e+HOLD<len(df): r=(float(C[e+HOLD])-entry)*d/stop_d-cost_r; in_until=e+HOLD
        if r is not None: out.append((r,pinned))
    return out

def stats(a):
    a=np.asarray(a); return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

async def main():
    clear_cache(); p=get_platform(PLAT); await p.connect()
    res={g:{"sig":[],"rand":[]} for g in GROUPS}; per={}
    try:
        for g,pairs in GROUPS.items():
            for pair in pairs:
                bars=None
                for attempt in range(4):
                    try:
                        bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_FROM), to_ts=datetime.now(timezone.utc)-timedelta(days=DAYS_TO), resolution="1h"); break
                    except Exception as ex:
                        print(pair,"FETCH FAIL",attempt,str(ex)[:80],flush=True); await asyncio.sleep(3)
                if bars is None: continue
                df=add_indicators(_bars_to_df(bars)); n=len(df); seg=n//SEG; per[pair]={"sig":[],"rand":[]}
                for s in STRATS:
                    st=get_strategy(s)
                    for k in range(SEG):
                        lo_=k*seg; hi=(k+1)*seg if k<SEG-1 else n
                        sdf=df.iloc[lo_:hi].reset_index(drop=True); sg=[(x.index,x.direction) for x in st(sdf,{})]
                        tr=run(sdf,sg,pair); per[pair]["sig"].extend(tr); res[g]["sig"].extend(tr)
                        for _ in range(3):
                            idx=rng.integers(60,len(sdf)-1,size=len(sg)); dirs=rng.choice([-1,1],size=len(sg))
                            rr_=run(sdf,list(zip(idx.tolist(),dirs.tolist())),pair); per[pair]["rand"].extend(rr_); res[g]["rand"].extend(rr_)
                pin=np.mean([x[1] for x in per[pair]["sig"]])*100
                print(f"{pair} bars={n} pinned={pin:.0f}%",flush=True)
                await asyncio.sleep(0.6)
    finally:
        await p.disconnect()
    print(f"\n===== PINNED vs ATR-BOUND INSTRUMENTS at 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    print(f"{'group/pair':<12}{'n':>6}{'E_sig':>9}{'t0':>7}{'E_rand':>9}{'sig-rand':>10}{'t':>7}{'pinned%':>9}")
    def row(name,d):
        s=[x[0] for x in d["sig"]]; r=[x[0] for x in d["rand"]]; ns,ms,ss=stats(s); nr,mr,sr=stats(r)
        print(f"{name:<12}{ns:>6}{ms:>+9.4f}{ms/ss:>+7.2f}{mr:>+9.4f}{ms-mr:>+10.4f}{(ms-mr)/np.sqrt(ss**2+sr**2):>+7.2f}{np.mean([x[1] for x in d['sig']])*100:>9.0f}")
    for g in GROUPS:
        row(g.upper(),res[g])
        for pair in GROUPS[g]:
            if pair in per: row("  "+pair,per[pair])
    a=[x[0] for x in res["atr_bound"]["sig"]]; b=[x[0] for x in res["pinned"]["sig"]]
    na,ma,sa=stats(a); nb,mb,sb=stats(b)
    print(f"\nATR-bound minus pinned (signals): {ma-mb:+.4f}  t={(ma-mb)/np.sqrt(sa**2+sb**2):+.2f}")
asyncio.run(main())

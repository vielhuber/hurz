"""Two-bar confirmation: the breakout must still hold at the next close.

The pullback entry (section 53), the retest (55) and the next-open
timing (91) each changed where the entry sits; none asked the plain
question whether a breakout that is still beyond its level one bar
later is a different trade from one that has already slipped back.
Replays the three live 1h trend strategies on the router-passed path
at the live 2-ATR stop over the whole tradeable universe and tags
every live trade by whether the close of the bar after the signal
still lies beyond the level the signal broke (the prior 20/55-bar
extreme, or the Keltner band). Two readings: (1) the live trades
split confirmed against unconfirmed — the preregistered block rule
applies to the unconfirmed bucket (t < -2 on both disjoint samples,
|t| > 2 against the rest with the same sign on both); (2) the
confirmed-entry variant itself, entered at that later close with its
own 2-ATR stop, against the live book in total R. DAYS_FROM / DAYS_TO
select the history window; DUMP writes the per-signal pairs (live R,
variant R with 0 for a signal the variant skips) so the two rules can
be compared paired per signal. See docs/EDGE_FINDINGS.md section 181.
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
DUMP=os.getenv("DUMP","")
SEG=3; RR=1.5; STOP_ATR=2.0; HOLD=24; PLAT="capital_com"
PAGE_DAYS=35; PAGE_PAUSE=0.5


def levels(df, s):
    """The level a signal at bar i broke: the channel of the bars before i."""
    if s=="donchian_breakout": per=20
    elif s=="turtle_breakout": per=55
    else:
        center=df["close"].ewm(span=20, adjust=False).mean()
        return (center+2.0*df["atr_14"]).shift(1).values, (center-2.0*df["atr_14"]).shift(1).values
    return df["high"].shift(1).rolling(per).max().values, df["low"].shift(1).rolling(per).min().values

def book(O,H,L,C,A,pair,fee,e,d,n):
    """R of a trade entered at the close of bar e; None if it cannot be priced or booked."""
    atr=A[e]
    if not np.isfinite(atr) or atr<=0: return None, None
    entry=float(C[e]); stop_d=STOP_ATR*atr
    vm=_venue_min_distance(PLAT,pair,entry)
    if vm>0 and stop_d<vm: stop_d=vm
    cost_r=2.0*fee*entry/stop_d
    if cost_r>0.10:
        stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10: return None, None
    tp=entry+d*RR*stop_d; sl=entry-d*stop_d
    for b in range(e+1,e+HOLD+1):
        if b>=n: break
        gap=(O[b]-entry)*d
        if gap<=-stop_d: return gap/stop_d-cost_r, b
        adverse=L[b] if d==1 else H[b]; favor=H[b] if d==1 else L[b]
        if (d==1 and adverse<=sl) or (d==-1 and adverse>=sl): return -1.0-cost_r, b
        if (d==1 and favor>=tp) or (d==-1 and favor<=tp): return RR-cost_r, b
    if e+HOLD<n: return (float(C[e+HOLD])-entry)*d/stop_d-cost_r, e+HOLD
    return None, None

def run(df, entries, pair, up, lo):
    live=[]; conf=[]; pairs=[]; in_live=-1; in_conf=-1; fee=_fee_for(PLAT,pair)
    O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
    A=df["atr_14"].values; n=len(df)
    for e,d in sorted(entries):
        if e+1>=n: continue
        lvl=up[e] if d==1 else lo[e]
        if not np.isfinite(lvl): continue
        confirmed=(C[e+1]-lvl)*d>0
        r_live=None
        if e>in_live:
            r_live,xb=book(O,H,L,C,A,pair,fee,e,d,n)
            if r_live is not None: live.append((r_live,confirmed)); in_live=xb
        r_var=None
        if confirmed and e+1>in_conf:
            r_var,xb=book(O,H,L,C,A,pair,fee,e+1,d,n)
            if r_var is not None: conf.append(r_var); in_conf=xb
        if r_live is not None: pairs.append((r_live, r_var if r_var is not None else 0.0))
    return live, conf, pairs

def stats(a):
    a=np.asarray(a,dtype=float)
    if len(a)<2: return len(a), float('nan'), float('nan')
    return len(a), a.mean(), a.std(ddof=1)/np.sqrt(len(a))

def table(name, live, conf):
    r=np.array([x for x,_ in live]); c=np.array([x for _,x in live]); v=np.array(conf)
    if len(r)<4 or c.sum()<2 or (~c).sum()<2: return
    n1,m1,s1=stats(r[~c]); n2,m2,s2=stats(r[c]); t=(m1-m2)/np.sqrt(s1**2+s2**2)
    nv,mv,sv=stats(v)
    print(f"\n--- {name}: live n={len(r)} E[R]={r.mean():+.4f} sumR={r.sum():+.1f}  confirmed share={c.mean()*100:.0f}%")
    print(f"{'bucket':<30}{'n':>6}{'E[R]':>9}{'t':>7}{'sum R':>8}")
    print(f"{'live, unconfirmed next close':<30}{n1:>6}{m1:>+9.4f}{m1/s1:>+7.2f}{r[~c].sum():>+8.1f}")
    print(f"{'live, confirmed next close':<30}{n2:>6}{m2:>+9.4f}{m2/s2:>+7.2f}{r[c].sum():>+8.1f}")
    print(f"{'  unconfirmed - confirmed':<30}{'':>6}{m1-m2:>+9.4f}{t:>+7.2f}")
    print(f"{'variant: enter at next close':<30}{nv:>6}{mv:>+9.4f}{mv/sv:>+7.2f}{v.sum():>+8.1f}")

def paired(name, pairs):
    """Per signal: live R against the variant's R (0 where the variant does not trade)."""
    a=np.array([x for x,_ in pairs]); b=np.array([y for _,y in pairs])
    if len(a)<4: return
    d=b-a; sd=d.std(ddof=1); t=d.mean()/(sd/np.sqrt(len(d))) if sd>0 else float('nan')
    print(f"{'  paired per signal: variant - live':<30}{len(d):>6}{d.mean():>+9.4f}{t:>+7.2f}{d.sum():>+8.1f}")

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
    live_all=[]; conf_all=[]; pairs_all=[]; live_s={s:[] for s in STRATS}; conf_s={s:[] for s in STRATS}; pairs_s={s:[] for s in STRATS}
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
                    up,lo=levels(sdf,s)
                    sg=[(x.index,x.direction) for x in st(sdf,{})
                        if not gate(s,sdf,x.index).blocked and not direction_blocked(pair,x.direction)]
                    lv,cf,pr=run(sdf,sg,pair,up,lo)
                    live_all.extend(lv); conf_all.extend(cf); pairs_all.extend(pr)
                    live_s[s].extend(lv); conf_s[s].extend(cf); pairs_s[s].extend(pr); cnt+=len(lv)
            print(f"{pair} bars={n} trades={cnt}",flush=True)
    finally:
        await p.disconnect()
    print(f"\n===== TWO-BAR CONFIRMATION, router-passed, 2-ATR stop ({DAYS_FROM}-{DAYS_TO} d) =====")
    table("ALL", live_all, conf_all); paired("ALL", pairs_all)
    for s in STRATS: table(s, live_s[s], conf_s[s]); paired(s, pairs_s[s])
    if DUMP:
        np.savez(DUMP, live=np.array([a for a,_ in pairs_all]), var=np.array([b for _,b in pairs_all]),
                 **{f"live_{s}":np.array([a for a,_ in pairs_s[s]]) for s in STRATS},
                 **{f"var_{s}":np.array([b for _,b in pairs_s[s]]) for s in STRATS})
asyncio.run(main())

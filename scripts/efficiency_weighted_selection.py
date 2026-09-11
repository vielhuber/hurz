"""Ranking the selector's candidates by dollars instead of by R.

Section 213 measured the one instrument property that demonstrably
persists — dollar efficiency, the share of the 3 USD budget that survives
the broker's size increment — and section 130 showed the property the
selector actually ranks by, expectancy in R, does not transfer between
samples. The selector's composite score is `eR * log1p(n) * pf`; nothing
in it sees that GBPJPY converts a given R into 47 % of the dollars
SILVER does.

Section 214 tried to use efficiency as a refusal at trade time and failed:
it removed trades, and the apparent gain came from which trades a given
year happened to lose on. This lever does not remove anything. The active
list keeps its size — only its ordering changes, so throughput is held
roughly constant and the confound of 214 cannot produce the result.

Design is a walk-forward over the same 2,555 days: rank every
(strategy, pair) combination on the trailing 365 days, take the top ten,
trade the following 90 days with only those ten, step forward, repeat.
Baseline ranks by the live composite score; the candidate multiplies that
score by the combination's efficiency at the ranking cut-off. Both are
scored in USD per calendar day on the out-of-sample blocks only.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than baseline on ALL FOUR year-samples
      of out-of-sample blocks,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) throughput must not fall by more than 10 % — a variant that merely
      trades less is 214 again and does not count.

No risk limit moves: the rule reorders a ranking, it does not enlarge a
position, widen a stop or lift a cap. See docs/EDGE_FINDINGS.md 215.

History is fetched once per instrument over the full span, paced like the
sibling scripts because the bot is trading, and cached under /tmp so a
re-run costs no API calls.
"""
import asyncio, os, sys, json, math
from datetime import datetime, timedelta, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.strategies import get_strategy, add_indicators
from app.spot_trading.trading_blocks import direction_blocked, BLOCKED_PAIRS
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.position_sizing import (
    calculate_position_size, DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD,
)
from app.spot_trading.regime import gate
from scripts.spot_backtest import _fee_for
from scripts.walk_forward import _bars_to_df

PAIRS=[p for p in ["BTCUSD","ETHUSD",
       "EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225",
       "OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"] if p not in BLOCKED_PAIRS]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]
STOP_ATR=2.0; RR=1.5; HOLD=24; MAX_CONCURRENT=8; PLAT="capital_com"
TOP_N=10; RANK_DAYS=365; TRADE_DAYS=90; MIN_RANK_TRADES=10
SPAN=2555; PAGE_DAYS=35; PAGE_PAUSE=1.0
BAR_CACHE="/tmp/eff_bars"; META_CACHE="/tmp/eff_meta.json"


def book(O,H,L,C,e,d,entry,stop_d,cost_r,n):
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


def trade_terms(df, e, pair, meta, atr_floor):
    """Entry price, stop distance, cost and the USD risk the venue allows.

    None when the live path would not take the signal at all. Sizing
    depends on the stop distance only, so the direction does not enter
    here — the long-side stop stands in for both."""
    A=df["atr_14"].values; C=df["close"].values
    atr=A[e]
    if not np.isfinite(atr) or atr<=0: return None
    entry=float(C[e]); stop_d=STOP_ATR*atr
    vm=0.0105*entry
    if stop_d<vm: stop_d=vm
    if atr_floor>0 and stop_d/atr<atr_floor: return None
    fee=_fee_for(PLAT,pair)
    cost_r=2.0*fee*entry/stop_d
    if cost_r>0.10:
        stop_d*=min(cost_r/0.10,2.0); cost_r=2.0*fee*entry/stop_d
        if cost_r>0.10: return None
    m=meta.get(pair)
    if m is None: return None
    rate=m["rate"]
    sized=calculate_position_size(
        entry_price=entry, stop_loss=entry-stop_d,
        target_risk=DEFAULT_TARGET_RISK_USD/rate,
        notional_cap=DEFAULT_NOTIONAL_CAP_USD/rate,
        size_increment=m["step"], min_size=m["min"], max_size=m["max"])
    if sized.size is None: return None
    return entry, stop_d, cost_r, sized.planned_risk*rate


def all_signals(frames, atr_floor, meta):
    """Every gated signal of the span, priced once.

    Each entry carries its R outcome and its USD outcome, so both the
    ranking window and the trading window read the same numbers."""
    out=[]
    for pair, df in frames.items():
        n=len(df); ts=df["timestamp"].values
        O=df["open"].values; H=df["high"].values; L=df["low"].values; C=df["close"].values
        for s in STRATS:
            for x in get_strategy(s)(df,{}):
                if gate(s,df,x.index).blocked: continue
                if direction_blocked(pair,x.direction): continue
                terms=trade_terms(df,x.index,pair,meta,atr_floor)
                if terms is None: continue
                entry,stop_d,cost_r,risk_usd=terms
                r,xb=book(O,H,L,C,x.index,x.direction,entry,stop_d,cost_r,n)
                if r is None: continue
                out.append({"ts":ts[x.index],"exit_ts":ts[xb],"pair":pair,
                            "strat":s,"r":r,"usd":r*risk_usd,"risk":risk_usd})
    out.sort(key=lambda z: z["ts"])
    return out


def rank(window, weighted):
    """Top-N combinations by the live composite score, optionally in USD.

    The live score is `eR * log1p(n) * pf`. The weighted variant scales it
    by mean realised risk over the target — the combination's efficiency
    as the window saw it, which is what turns an edge in R into dollars.
    """
    agg={}
    for t in window:
        agg.setdefault((t["strat"],t["pair"]),[]).append(t)
    rows=[]
    for key, ts in agg.items():
        if len(ts)<MIN_RANK_TRADES: continue
        r=np.array([t["r"] for t in ts])
        eR=float(r.mean())
        if eR<=0: continue
        gains=r[r>0].sum(); losses=-r[r<0].sum()
        pf=5.0 if losses<=0 else min(5.0, float(gains/losses))
        if pf<1.0: continue
        score=eR*math.log1p(len(ts))*pf
        if weighted:
            eff=float(np.mean([t["risk"] for t in ts]))/DEFAULT_TARGET_RISK_USD
            score*=eff
        rows.append((score,key))
    rows.sort(reverse=True)
    return {k for _,k in rows[:TOP_N]}


def trade(window, active):
    """Replay one out-of-sample block with the live concurrency guards."""
    open_until={}; per_day={}; n=0
    for t in window:
        if (t["strat"],t["pair"]) not in active: continue
        for p_ in [p_ for p_,u in open_until.items() if u<=t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until: continue
        if len(open_until)>=MAX_CONCURRENT: continue
        open_until[t["pair"]]=t["exit_ts"]
        day=str(np.datetime64(t["exit_ts"],'D'))
        per_day[day]=per_day.get(day,0.0)+t["usd"]
        n+=1
    return per_day, n


async def fetch_paced(p, pair, days_from, days_to):
    now=datetime.now(timezone.utc)
    start=now-timedelta(days=days_from); end=now-timedelta(days=days_to)
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


def cache_path(pair):
    return os.path.join(BAR_CACHE, f"{pair}.json")


async def load_history():
    """Bars per instrument, from the cache when it is already there."""
    os.makedirs(BAR_CACHE, exist_ok=True)
    missing=[p_ for p_ in PAIRS if not os.path.exists(cache_path(p_))]
    frames={}
    if missing:
        clear_cache(); p=get_platform(PLAT); await p.connect()
        try:
            for pair in missing:
                bars=await fetch_paced(p, pair, SPAN, 0)
                if not bars:
                    print(f"{pair} no history",flush=True); continue
                json.dump([[b.timestamp.isoformat(), b.open, b.high, b.low, b.close,
                            getattr(b,"volume",0.0)] for b in bars],
                          open(cache_path(pair),"w"))
                print(f"{pair} cached {len(bars)} bars",flush=True)
        finally:
            await p.disconnect()
    for pair in PAIRS:
        if not os.path.exists(cache_path(pair)): continue
        raw=json.load(open(cache_path(pair)))
        frames[pair]=[(datetime.fromisoformat(t), o, h, l, c, v) for t,o,h,l,c,v in raw]
    return frames


def to_frame(rows):
    import pandas as pd
    return pd.DataFrame([{"timestamp":t,"open":o,"high":h,"low":l,"close":c}
                         for t,o,h,l,c,_ in rows])


def t_stat(v):
    if len(v)<2 or v.std(ddof=1)==0: return float('nan')
    return float(v.mean()/(v.std(ddof=1)/np.sqrt(len(v))))


async def main():
    atr_floor=_min_stop_atr_multiple()
    raw=await load_history()
    meta=json.load(open(META_CACHE))
    frames={}
    for pair, rows in raw.items():
        if pair not in meta: continue
        if len(rows)<2000: continue
        frames[pair]=add_indicators(to_frame(rows))
    print(f"instruments={len(frames)} atr_floor={atr_floor:g}",flush=True)

    sig=all_signals(frames, atr_floor, meta)
    print(f"gated, sized, booked signals: {len(sig)}",flush=True)
    if not sig: return
    t0=min(s["ts"] for s in sig); t1=max(s["ts"] for s in sig)
    print(f"span {np.datetime64(t0,'D')} … {np.datetime64(t1,'D')}",flush=True)

    step=np.timedelta64(TRADE_DAYS,'D'); rank_w=np.timedelta64(RANK_DAYS,'D')
    blocks=[]
    cut=np.datetime64(t0,'D')+rank_w
    while cut+step<=np.datetime64(t1,'D'):
        blocks.append((cut,cut+step)); cut=cut+step
    print(f"out-of-sample blocks: {len(blocks)}",flush=True)

    daily={False:{}, True:{}}; counts={False:0, True:0}; overlap=[]
    for start,end in blocks:
        rw=[s for s in sig if start-rank_w<=s["ts"]<start]
        tw=[s for s in sig if start<=s["ts"]<end]
        sets={}
        for weighted in (False, True):
            active=rank(rw, weighted); sets[weighted]=active
            per_day,n=trade(tw, active)
            for d,v in per_day.items():
                daily[weighted][d]=daily[weighted].get(d,0.0)+v
            counts[weighted]+=n
        both=sets[False]&sets[True]
        overlap.append(len(both)/max(1,len(sets[False])))

    print(f"\nmean overlap of the two top-{TOP_N} lists: {np.mean(overlap):.0%}")
    print(f"trades  live-rank {counts[False]}   efficiency-weighted {counts[True]}"
          f"   ({counts[True]/max(1,counts[False])-1:+.1%})")

    all_days=sorted(set(daily[False])|set(daily[True]))
    base=np.array([daily[False].get(d,0.0) for d in all_days])
    cand=np.array([daily[True].get(d,0.0) for d in all_days])
    years=[(0,365),(366,1095),(1096,1825),(1826,2555)]
    now=np.datetime64(datetime.now(timezone.utc).date())
    print(f"\n{'sample':<14}{'live rank':>12}{'eff-weighted':>14}{'diff':>10}{'t':>8}{'days':>7}")
    ok_all=True; best_t=0.0
    for lo,hi in years:
        sel=np.array([(now-np.timedelta64(hi,'D'))<=np.datetime64(d)<(now-np.timedelta64(lo,'D'))
                      for d in all_days])
        if sel.sum()==0: continue
        span=float(hi-lo)
        b=base[sel].sum()/span; c=cand[sel].sum()/span
        d_=cand[sel]-base[sel]; tv=t_stat(d_)
        if c<=b: ok_all=False
        if abs(tv)==abs(tv) and tv>best_t: best_t=tv
        print(f"{lo}-{hi} d{'':<4}{b:>+12.4f}{c:>+14.4f}{c-b:>+10.4f}{tv:>+8.2f}{int(sel.sum()):>7}")
    span_all=float((np.datetime64(t1,'D')-np.datetime64(t0,'D')).astype(int))
    print(f"\npooled  live {base.sum()/span_all:+.4f}  weighted {cand.sum()/span_all:+.4f} USD/day"
          f"  paired t {t_stat(cand-base):+.2f}")
    print(f"\n(a) better on all four samples: {'YES' if ok_all else 'NO'}")
    print(f"(b) t > 2 on at least one:      {'YES' if best_t>2 else 'NO'} (best {best_t:+.2f})")
    thr=counts[True]/max(1,counts[False])-1
    print(f"(c) throughput within -10 %:    {'YES' if thr>=-0.10 else 'NO'} ({thr:+.1%})")


if __name__ == "__main__":
    asyncio.run(main())

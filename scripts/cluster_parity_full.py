"""Cluster sign parity over the FULL live instrument set, not the harness's.

Section 243 resolved cluster signs by union-find with parity and found
zero contradictions on both audited windows, with EURAUD and USDCHF
inverted on each. On that basis the signed cap passed clauses (a) and
(c') and was one verification away from shipping.

It was measured on the 23 instruments the walk-forward harness carries.
The live book clusters 27. The five missing ones — AU200, CADJPY,
EURJPY, GBPJPY and USDJPY — were fetched for this check, because USDJPY
is a USD-base pair like the inverted USDCHF and a wrong sign there would
open exactly the hole the signed rule exists to close.

They break the structure:

    window A: 66 edges, 1 contradiction   (USDCHF / USDJPY)
    window B: 63 edges, 2 contradictions  (NZDUSD / US500,
                                           EURAUD / NZDUSD)

and the signs are no longer window-stable: EURUSD and NZDUSD flip
between A and B. A contradiction is a cycle whose negative edges do not
multiply to +1 — direct proof that no single factor explains the
cluster, however well a subset of it behaves.

So section 243's clean result was an artefact of a universe smaller than
the one the bot trades. The rule this leaves standing is the one already
in production: gross counting, which needs no signs and is the
conservative reading when none can be assigned.
"""
import asyncio, json, os, sys
from datetime import datetime
import numpy as np, pandas as pd
sys.path.insert(0,"/var/www/hurz"); os.chdir("/var/www/hurz")
from scripts.efficiency_weighted_selection import load_history, to_frame, META_CACHE
from app.strategies import add_indicators
from app.spot_trading.autotrade import _CORRELATION_CLUSTERS
THRESHOLD=0.5

def parity(corr, members):
    parent={p:p for p in members}; rel={p:1 for p in members}
    def find(p):
        if parent[p]==p: return p,1
        r,s=find(parent[p]); parent[p]=r; rel[p]=rel[p]*s; return r,rel[p]
    contra=[]; edges=[]
    for i,x in enumerate(members):
        for y in members[i+1:]:
            if _CORRELATION_CLUSTERS.get(x)!=_CORRELATION_CLUSTERS.get(y): continue
            v=float(corr.loc[x,y])
            if not np.isfinite(v) or abs(v)<THRESHOLD: continue
            edges.append((abs(v),x,y,1 if v>0 else -1))
    edges.sort(reverse=True)
    for _,x,y,s in edges:
        rx,sx=find(x); ry,sy=find(y)
        if rx==ry:
            if sx*sy!=s: contra.append((x,y,s,sx*sy))
            continue
        parent[rx]=ry; rel[rx]=s*sx*sy
    return {p:find(p)[1] for p in members}, contra, len(edges)

async def main():
    raw=await load_history(); meta=json.load(open(META_CACHE))
    for f in os.listdir("/tmp/sign_bars"):
        pair=f[:-5]
        rows=json.load(open(f"/tmp/sign_bars/{f}"))
        raw[pair]=[(datetime.fromisoformat(t),o,h,l,c,v) for t,o,h,l,c,v in rows]
    frames={p:to_frame(r) for p,r in raw.items() if len(r)>=2000}
    print("instruments:",len(frames))
    today=max(df["timestamp"].max() for df in frames.values())
    res={}
    for lbl,lo_d,hi_d in (("A",2555,1826),("B",1825,1096)):
        lo=today-np.timedelta64(lo_d,'D'); hi=today-np.timedelta64(hi_d,'D')
        ser={}
        for p,df in frames.items():
            m=(df["timestamp"]>lo)&(df["timestamp"]<=hi)
            s=df.loc[m,["timestamp","close"]].copy()
            if len(s)<500: continue
            s["ret"]=s["close"].pct_change(); ser[p]=s.set_index("timestamp")["ret"]
        corr=pd.DataFrame(ser).dropna(how="all").corr()
        members=[p for p in corr.columns if _CORRELATION_CLUSTERS.get(p)]
        signs,contra,edges=parity(corr,members)
        inv=sorted(p for p,v in signs.items() if v<0)
        print(f"window {lbl}: members {len(members)}, edges {edges}, "
              f"contradictions {len(contra)}")
        if contra: print("   ",contra[:8])
        print(f"   inverted: {inv}")
        res[lbl]=signs
    a,b=res["A"],res["B"]
    common=[p for p in a if p in b]
    for cl in {_CORRELATION_CLUSTERS[p] for p in common}:
        mem=[p for p in common if _CORRELATION_CLUSTERS[p]==cl]
        rel={p:a[p]*b[p] for p in mem}
        ok=len(set(rel.values()))<=1
        print(f"cluster {cl}: window-stable {'YES' if ok else 'NO'}")
        if not ok: print("   ",rel)
asyncio.run(main())

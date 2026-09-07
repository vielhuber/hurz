"""Hourly-return correlations of the active instruments against the correlation clusters.

A year of 1h closes for the mapped and unmapped instruments, pairwise
correlations and the median absolute correlation of each unmapped
instrument with each cluster. See docs/EDGE_FINDINGS.md section 82.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
import numpy as np, pandas as pd
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
PAIRS=["HK50","J225","AU200","DE40","FR40","EU50","UK100","US500","AUDJPY","CHFJPY","USDJPY","AUDUSD","AUDNZD","EURAUD","GBPCAD","EURUSD","GBPUSD","COPPER","GOLD","SILVER"]
async def main():
    clear_cache(); p=get_platform("capital_com"); await p.connect(); series={}
    try:
        for pair in PAIRS:
            bars=None
            for a in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=365), to_ts=datetime.now(timezone.utc), resolution="1h"); break
                except Exception as e: await asyncio.sleep(3)
            if bars is None: print(pair,"FAIL"); continue
            s=pd.Series([b.close for b in bars], index=[b.timestamp for b in bars]); series[pair]=np.log(s).diff()
            await asyncio.sleep(0.5)
    finally: await p.disconnect()
    df=pd.DataFrame(series).dropna(how="all"); corr=df.corr(min_periods=500)
    pd.set_option("display.width",250); print(corr.round(2).to_string())
    groups={"indices":["HK50","J225","AU200","DE40","FR40","UK100","US500"],"usd_fx":["AUDUSD","EURUSD","GBPUSD","USDJPY"],"metals":["GOLD","SILVER"]}
    print("\nmedian |corr| of unmapped instruments with each cluster's members:")
    for u in ["EU50","AUDJPY","CHFJPY","AUDNZD","EURAUD","GBPCAD","COPPER"]:
        row={g:float(np.median([abs(corr.loc[u,m]) for m in ms if m!=u and m in corr])) for g,ms in groups.items()}
        print(f"  {u:<8}", {k:round(v,2) for k,v in row.items()}, " signed vs USDJPY:", round(float(corr.loc[u,'USDJPY']),2), " vs HK50:", round(float(corr.loc[u,'HK50']),2))
asyncio.run(main())

"""Hourly-return correlations of the yen crosses against the correlation clusters.

A year of 1h closes for the yen crosses, the USD majors and three
indices, pairwise correlations and the median absolute correlation of
each unmapped cross with each cluster. See docs/EDGE_FINDINGS.md
section 95.
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
PAIRS=["AUDJPY","CHFJPY","EURJPY","GBPJPY","CADJPY","USDJPY","EURUSD","GBPUSD","AUDUSD","NZDUSD","USDCAD","USDCHF","J225","US500","DE40","GOLD"]
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
    groups={"jpy_crosses":["AUDJPY","CHFJPY"],"usd_fx":["EURUSD","GBPUSD","AUDUSD","NZDUSD","USDJPY","USDCAD","USDCHF"],"indices":["J225","US500","DE40"]}
    print("\nmedian |corr| of unmapped instruments with each cluster's members:")
    for u in ["EURJPY","GBPJPY","CADJPY","USDJPY"]:
        row={g:float(np.median([abs(corr.loc[u,m]) for m in ms if m!=u and m in corr])) for g,ms in groups.items()}
        print(f"  {u:<8}", {k:round(v,2) for k,v in row.items()}, " signed vs USDJPY:", round(float(corr.loc[u,'USDJPY']),2), " vs AUDJPY:", round(float(corr.loc[u,'AUDJPY']),2), " vs CHFJPY:", round(float(corr.loc[u,'CHFJPY']),2))
asyncio.run(main())

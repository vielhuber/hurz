"""Reconcile the journal's realised PnL with the broker's TRADE transactions.

Matches every journal close of the last 30 days to the account's TRADE
transaction on the same instrument within 90 minutes (the broker's dealId
differs from the journalled opening reference) and lists the gap per trade
in USD and as a share of notional. Read-only. See docs/EDGE_FINDINGS.md
section 133.
"""
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.utils.singletons import settings
settings.load_env()
from app.utils.singletons import database
from app.platforms import get_platform
from app.platforms.registry import clear_cache
import pandas as pd
EUR_USD=1.1699
async def main():
    clear_cache(); p=get_platform("capital_com"); await p.connect()
    try:
        now=datetime.now(timezone.utc); frm=(now-timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S"); to=now.strftime("%Y-%m-%dT%H:%M:%S")
        data=await p._raw_request("GET", f"/api/v1/history/transactions?from={frm}&to={to}&detailed=true", auth=True)
        txs=[t for t in (data.get("transactions") or []) if t["transactionType"]=="TRADE"]
    finally:
        await p.disconnect()
    rows=database.select("""
        SELECT pair, direction, size, fill_price, exit_price, exit_time, realized_pnl, outcome FROM spot_trades
        WHERE platform='capital_com' AND paper_mode=0 AND accepted=1 AND exit_time >= %s AND realized_pnl IS NOT NULL
        ORDER BY exit_time""", ((now-timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),))
    used=set(); tot_j=0; tot_b=0
    print(f"{'pair':<10}{'exit_time':<17}{'outcome':<8}{'journal USD':>12}{'broker EUR':>11}{'->USD':>8}{'delta USD':>10}{'delta/notional %':>17}")
    for r in rows:
        et=pd.Timestamp(str(r["exit_time"]),tz="UTC"); best=None
        for i,t in enumerate(txs):
            if i in used or t["instrumentName"]!=r["pair"]: continue
            dt=abs((pd.Timestamp(t["dateUtc"],tz="UTC")-et).total_seconds())
            if dt<=5400 and (best is None or dt<best[0]): best=(dt,i)
        if best is None: print(f"{r['pair']:<10}{str(r['exit_time'])[:16]:<17}{str(r['outcome']):<8}{float(r['realized_pnl']):>+12.2f}{'-':>11}"); continue
        used.add(best[1]); amt=float(str(txs[best[1]]["size"]).replace(",","")); j=float(r["realized_pnl"]); b=amt*EUR_USD
        notional=float(r["size"])*float(r["fill_price"]); tot_j+=j; tot_b+=b
        print(f"{r['pair']:<10}{str(r['exit_time'])[:16]:<17}{str(r['outcome']):<8}{j:>+12.2f}{amt:>+11.2f}{b:>+8.2f}{j-b:>+10.2f}{(j-b)/notional*100 if notional else 0:>17.3f}")
    print(f"\nmatched {len(used)}/{len(rows)}: journal {tot_j:+.2f} USD vs broker {tot_b:+.2f} USD (at 1.1699) -> journal overstates by {tot_j-tot_b:+.2f} USD = {(tot_j-tot_b)/max(len(used),1):+.3f} USD per trade")
asyncio.run(main())

"""Overnight financing audit: broker SWAP transactions and per-instrument rates.

The journal books realised PnL from the bot's own price arithmetic, so
the venue's overnight fee (charged 21:00 UTC on every open position)
never appears in it or in the dashboard. This read-only audit lists the
last 30 days of SWAP and TRADE transactions from the account history and
the current overnight rates per tradeable instrument, expressed as the
cost of one night in R at the live 250 USD notional and 3 USD risk. See
docs/EDGE_FINDINGS.md section 126.
"""
import asyncio, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache

PAIRS=["BTCUSD","ETHUSD","EURUSD","AUDUSD","USDCHF","AUDNZD","EURAUD","GBPUSD","NZDUSD","GBPCAD","AUDJPY","CHFJPY",
       "DE40","US500","US30","FR40","UK100","EU50","US100","HK50","J225","OIL_CRUDE","OIL_BRENT","GOLD","SILVER","COPPER"]
NOTIONAL_USD=250.0; RISK_USD=3.0; DAYS=int(os.getenv("DAYS","30"))

async def main():
    clear_cache(); p=get_platform("capital_com"); await p.connect()
    try:
        now=datetime.now(timezone.utc)
        frm=(now-timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%S"); to=now.strftime("%Y-%m-%dT%H:%M:%S")
        data=await p._raw_request("GET", f"/api/v1/history/transactions?from={frm}&to={to}&detailed=true", auth=True)
        txs=data.get("transactions") or []
        by=defaultdict(lambda: defaultdict(lambda: [0,0.0]))
        for t in txs:
            amount=float(str(t.get("size") or 0).replace(",",""))
            cell=by[t.get("transactionType")][t.get("instrumentName")]; cell[0]+=1; cell[1]+=amount
        print(f"transactions in the last {DAYS} d: {len(txs)} (the endpoint may cap the list; earliest {min((t['dateUtc'] for t in txs), default='-')})")
        for kind, rows in sorted(by.items()):
            total=sum(v[1] for v in rows.values()); count=sum(v[0] for v in rows.values())
            print(f"\n{kind}: {count} entries, {total:+.2f} EUR")
            for name,(n,s) in sorted(rows.items(), key=lambda x: x[1][1]):
                print(f"  {name:<10} n={n:>3} sum={s:+.2f} EUR  per entry={s/n:+.4f}")
        print(f"\n{'epic':<11}{'ccy':<5}{'long %/night':>13}{'short %/night':>14}{'long R/night':>13}{'short R/night':>14}")
        for e in PAIRS:
            d=await p._raw_request("GET", f"/api/v1/markets/{e}", auth=True)
            inst=d.get("instrument") or {}; fee=inst.get("overnightFee") or {}
            lr=fee.get("longRate"); sr=fee.get("shortRate")
            def r_per_night(rate):
                return float('nan') if rate is None else -float(rate)/100.0*NOTIONAL_USD/RISK_USD
            print(f"{e:<11}{inst.get('currency','?'):<5}{str(lr):>13}{str(sr):>14}{r_per_night(lr):>+13.4f}{r_per_night(sr):>+14.4f}")
            await asyncio.sleep(0.4)
        print("\n(positive R/night = the position pays; negative = it receives)")
    finally:
        await p.disconnect()
asyncio.run(main())

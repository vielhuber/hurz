"""Cross-strategy signal overlap on the cost-free signal timeline.

Counts signals of the three live trend strategies that arrive while a
24-bar position is already open on the instrument, as the live
one-position-per-instrument guard would refuse them. See
docs/EDGE_FINDINGS.md section 83.
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
from scripts.walk_forward import _bars_to_df
PAIRS = ["BTCUSD","ETHUSD","OIL_CRUDE","OIL_BRENT","GOLD","DE40","US500","US30","EURUSD","AUDUSD"]
STRATS=["donchian_breakout","turtle_breakout","keltner_breakout"]; HOLD=24
async def main():
    clear_cache(); p=get_platform("capital_com"); await p.connect(); tot=0; blocked=0; same_dir=0; per={}
    try:
        for pair in PAIRS:
            bars=None
            for a in range(4):
                try:
                    bars=await p.fetch_history(pair, from_ts=datetime.now(timezone.utc)-timedelta(days=365), to_ts=datetime.now(timezone.utc), resolution="1h"); break
                except Exception: await asyncio.sleep(3)
            if bars is None: continue
            df=add_indicators(_bars_to_df(bars))
            sigs=sorted([(x.index,x.direction,s) for s in STRATS for x in get_strategy(s)(df,{})])
            open_until=-1; open_dir=0; open_strat=None; n=0; b=0; sd=0
            for i,d,s in sigs:
                n+=1
                if i<=open_until:
                    b+=1
                    if d==open_dir: sd+=1
                    continue
                open_until=i+HOLD; open_dir=d; open_strat=s
            per[pair]=(n,b,sd); tot+=n; blocked+=b; same_dir+=sd
            print(f"{pair:<10} signals={n:>5} blocked_by_open_position={b:>5} ({b/n*100:.0f}%)  same_direction={sd:>4} ({sd/max(b,1)*100:.0f}% of blocked)",flush=True)
            await asyncio.sleep(0.5)
    finally: await p.disconnect()
    print(f"POOLED signals={tot} blocked={blocked} ({blocked/tot*100:.1f}%) same-direction share of blocked={same_dir/max(blocked,1)*100:.0f}%")
asyncio.run(main())

"""The expectancy-based instrument blocks, re-read on the live-faithful book.

`EXPECTANCY_BLOCKED_PAIRS` holds AU200 (2026-09-07, run 21: router-passed
R on a 2-ATR simulator) and AUDUSD, GBPCAD, GBPUSD (2026-09-10, section 192:
gated-signal R). Both predate the 3-ATR floor's current form, the ADX
ceiling, dollar sizing, the live selector and the cluster caps. Section
256 re-read section 192's *rule* on the book and found no new block to
add; nobody removed the existing four, and the harness has never held
their bars, so the blocks were never scored in dollars on the book the bot
runs. Section 256 also showed the mechanism that could reverse them: under
a binding `risk_on` cap an instrument's P&L is not its marginal
contribution.

  live       the four stay blocked (the harness universe of section 255)
  candidate  all four admitted
  diag       AU200 only admitted

Bars are fetched once into their own cache, paced at 2 s a page so the
running bot keeps its rate budget; size constraints and quote rates come
from the venue as for every other instrument. Refusal of nothing; the
cooldown, caps and every guard stay in force.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 277.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
import scripts.efficiency_weighted_selection as ews
from scripts.flat_before_weekend import signals
from scripts.stop_out_cooldown_length import admit_cooldown
from app.platforms import get_platform
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.trading_blocks import EXPECTANCY_BLOCKED_PAIRS
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, TRADE_DAYS, META_CACHE, SPAN, PLAT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
EXTRA = sorted(EXPECTANCY_BLOCKED_PAIRS)
EXTRA_CACHE = "/var/tmp/hurz_blocked_bars"
ARMS = {"live": set(), "candidate": set(EXTRA), "diag": {"AU200"}}


async def instrument_meta(p, pair, reference_price):
    """Venue size constraints and the quote currency's USD value, as the harness's meta cache holds them."""
    con = await p.order_constraints(pair)
    prepared = await p.prepare_order(asset=pair, direction=1, reference_price=reference_price,
                                     stop_loss=None, take_profit=None)
    rate = prepared.usd_per_quote
    if rate is None or rate <= 0:
        return None
    mx = getattr(con, "max_size", None)
    return {"step": float(con.size_increment or 0.0), "min": float(con.min_size or 0.0),
            "max": (float(mx) if mx else None), "rate": float(rate)}


async def load_extra():
    os.makedirs(EXTRA_CACHE, exist_ok=True)
    meta_path = os.path.join(EXTRA_CACHE, "meta.json")
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    missing = [p for p in EXTRA if not os.path.exists(os.path.join(EXTRA_CACHE, f"{p}.json"))
               or p not in meta]
    if missing:
        ews.PAGE_PAUSE = 2.0
        p = get_platform(PLAT); await p.connect()
        try:
            for pair in missing:
                bars = await ews.fetch_paced(p, pair, SPAN, 0)
                if not bars:
                    print(f"{pair} no history", flush=True); continue
                json.dump([[b.timestamp.isoformat(), b.open, b.high, b.low, b.close,
                            getattr(b, "volume", 0.0)] for b in bars],
                          open(os.path.join(EXTRA_CACHE, f"{pair}.json"), "w"))
                m = await instrument_meta(p, pair, bars[-1].close)
                if m:
                    meta[pair] = m
                    json.dump(meta, open(meta_path, "w"))
                print(f"{pair} cached {len(bars)} bars, meta {m}", flush=True)
        finally:
            await p.disconnect()
    raw = {}
    for pair in EXTRA:
        path = os.path.join(EXTRA_CACHE, f"{pair}.json")
        if os.path.exists(path):
            raw[pair] = [(datetime.fromisoformat(t), o, h, l, c, v) for t, o, h, l, c, v in json.load(open(path))]
    return raw, meta


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    extra_raw, extra_meta = await load_extra()
    meta.update(extra_meta)
    frames = {p: add_indicators(to_frame(r)) for p, r in {**raw, **extra_raw}.items()
              if p in meta and len(r) >= 2000}
    print(f"added instruments with bars and meta: {sorted(set(frames) & set(EXTRA))}", flush=True)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    base = signals(frames, _min_stop_atr_multiple(), meta, None)
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, admitted in ARMS.items():
        allowed = (set(frames) - set(EXTRA)) | (admitted & set(frames))
        pins, reserved = pe.load_pins(allowed, pe.VETOED,
                                      set(pe.strategy_expectancy_veto("capital_com")))
        sig = [s for s in base if s["pair"] in allowed]
        t0 = min(s["ts"] for s in base); t1 = max(s["ts"] for s in base)
        step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
        cut = np.datetime64(t0, 'D') + rank_w; daily = {}; taken = []; last_stop = {}
        while cut + step <= np.datetime64(t1, 'D'):
            ranked, _ = pe.lists([s for s in sig if cut - rank_w <= s["ts"] < cut], pins, reserved)
            for t in admit_cooldown([s for s in sig if cut <= s["ts"] < cut + step],
                                    ranked | pins, 6, last_stop, []):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut += step
        results[arm] = daily
        per = {p: np.array([t["usd"] for t in taken if t["pair"] == p]) for p in EXTRA}
        print(f"{arm:<10} signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}; "
              + ", ".join(f"{p} {len(v)} at {v.mean():+.3f}" for p, v in per.items() if len(v)), flush=True)

    base_d = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        other = results[arm]
        days_ = sorted(set(base_d) | set(other))
        a = np.array([base_d.get(d, 0.0) for d in days_]); b = np.array([other.get(d, 0.0) for d in days_])
        print(f"\n--- {arm} ---")
        print(f"{'sample':<14}{'live':>11}{'variant':>11}{'diff':>10}{'t':>8}")
        up = 0
        for lo, hi in YEARS:
            sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                            < (now - np.timedelta64(lo, 'D')) for d in days_])
            span = float(hi - lo); av = a[sel].sum() / span; bv = b[sel].sum() / span
            up += bv > av
            print(f"{lo}-{hi} d{'':<4}{av:>+11.4f}{bv:>+11.4f}{bv-av:>+10.4f}{t_stat(b[sel]-a[sel]):>+8.2f}")
        d_ = b - a
        print(f"{'pooled':<14}{a.sum()/len(days_):>+11.4f}{b.sum()/len(days_):>+11.4f}"
              f"{d_.sum()/len(days_):>+10.4f}{t_stat(d_):>+8.2f}")
        if arm == "candidate":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — {'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())

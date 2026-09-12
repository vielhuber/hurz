"""The four live instruments the walk-forward harness never scored.

Section 244 closed with a rule: the harness carries 23 instruments, the
bot clusters 27, and verdicts about structure must be re-read on the
live universe before they are believed. This run takes that rule to the
place it bites hardest — not correlation structure, but the selection
rules themselves.

`data/active_pairs.capital_com.json` of 2026-09-11 holds 25 instruments,
among them CADJPY, EURJPY, GBPJPY and USDJPY. Every modern selection
rule — the ADX ceiling, the 3xATR floor, the cost ceiling, dollar
sizing, the consistency block of section 192 — was derived on a universe
that excludes all four. Section 192 flagged three of *24* instruments;
these four were never eligible to be flagged, and the bot has been
trading them ever since.

Section 96 measured the yen group against random entries a fortnight
ago, before the ceiling, the floor and dollar sizing existed. That is
not the system now running.

The lever is section 192's rule, applied to the instruments it could not
see. Its form is kept exactly: three disjoint TRAINING samples must all
read negative before an instrument is flagged, the most recent year is
held out entirely, and the held-out year decides alone.

Acceptance, fixed before the data were seen:

  (a) at least one candidate reads negative on ALL THREE training
      samples — with four candidates the chance count is 4 x 0.5^3 =
      0.5, so a flag is not yet evidence and the held-out year decides;
  (b) on the held-out year the paired per-signal difference of blocking
      the flagged set reaches t > +2, as in section 192;
  (c) pooled USD per calendar day over the walk-forward does not fall
      and at least three of the four year-samples improve.

Nothing here loosens a risk limit: the only production change it can
produce is an addition to EXPECTANCY_BLOCKED_PAIRS, which refuses
entries and can never enlarge one.

History for the four is assembled from the section-244 fetch (days
2,555-1,096, already on disk) plus a paced fetch of the remaining
1,100 days, so the added API load is a third of a full download.

See docs/EDGE_FINDINGS.md 245.
"""
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, all_signals, rank, trade,
    fetch_paced, cache_path, RANK_DAYS, TRADE_DAYS, META_CACHE, PAIRS,
)

CANDIDATES = ["CADJPY", "EURJPY", "GBPJPY", "USDJPY"]
OLD_BARS = "/tmp/sign_bars"          # days 2,555-1,096, fetched for section 244
RECENT_DAYS = 1100                   # overlaps the old cache by four days
TRAIN = [(366, 1095), (1096, 1825), (1826, 2555)]
TEST = (0, 365)


async def instrument_meta(plat, pair, reference_price):
    """Real size constraints and the USD value of the quote currency.

    A copy rather than an import: `scripts/efficiency_filter` runs its
    own `main()` at module level, so importing it re-downloads the whole
    universe as a side effect."""
    con = await plat.order_constraints(pair)
    prepared = await plat.prepare_order(asset=pair, direction=1,
        reference_price=reference_price, stop_loss=None, take_profit=None)
    rate = prepared.usd_per_quote
    if rate is None or rate <= 0:
        print(f"{pair}: no USD rate — skipped as live would", flush=True)
        return None
    mx = getattr(con, "max_size", None)
    return {"step": float(con.size_increment or 0.0),
            "min": float(con.min_size or 0.0),
            "max": (float(mx) if mx else None), "rate": float(rate)}


async def ensure_candidate_history():
    """Bars and venue meta for the four, cached like every other pair."""
    meta = json.load(open(META_CACHE))
    need_bars = [p for p in CANDIDATES if not os.path.exists(cache_path(p))]
    need_meta = [p for p in CANDIDATES if p not in meta]
    if not need_bars and not need_meta:
        return meta
    clear_cache(); plat = get_platform(base.PLAT); await plat.connect()
    try:
        for pair in CANDIDATES:
            if pair in need_bars:
                recent = await fetch_paced(plat, pair, RECENT_DAYS, 0)
                if not recent:
                    print(f"{pair}: no recent history", flush=True); continue
                rows = [[b.timestamp.isoformat(), b.open, b.high, b.low, b.close,
                         getattr(b, "volume", 0.0)] for b in recent]
                old_path = os.path.join(OLD_BARS, f"{pair}.json")
                if os.path.exists(old_path):
                    rows = json.load(open(old_path)) + rows
                seen = set(); uniq = []
                for row in sorted(rows, key=lambda r: r[0]):
                    if row[0] in seen: continue
                    seen.add(row[0]); uniq.append(row)
                json.dump(uniq, open(cache_path(pair), "w"))
                print(f"{pair}: {len(uniq)} bars cached "
                      f"({uniq[0][0][:10]} … {uniq[-1][0][:10]})", flush=True)
            if pair in need_meta and os.path.exists(cache_path(pair)):
                rows = json.load(open(cache_path(pair)))
                m = await instrument_meta(plat, pair, float(rows[-1][4]))
                if m is None: continue
                meta[pair] = m
                print(f"{pair}: step={m['step']:g} min={m['min']:g} "
                      f"rate={m['rate']:.6g}", flush=True)
    finally:
        await plat.disconnect()
    json.dump(meta, open(META_CACHE, "w"))
    return meta


def sample_mask(values, lo, hi, now):
    return np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(v, 'D')
                     < (now - np.timedelta64(lo, 'D')) for v in values])


async def main():
    atr_floor = _min_stop_atr_multiple()
    meta = await ensure_candidate_history()

    raw = await load_history()
    for pair in CANDIDATES:
        if not os.path.exists(cache_path(pair)): continue
        raw[pair] = [(datetime.fromisoformat(t), o, h, l, c, v)
                     for t, o, h, l, c, v in json.load(open(cache_path(pair)))]
    frames = {}
    for pair, rows in raw.items():
        if pair not in meta or len(rows) < 2000: continue
        frames[pair] = add_indicators(to_frame(rows))
    carried = [p for p in CANDIDATES if p in frames]
    print(f"instruments={len(frames)} (harness {len(PAIRS)} + "
          f"{len(carried)} unscored: {', '.join(carried)}) "
          f"atr_floor={atr_floor:g}", flush=True)

    sig = all_signals(frames, atr_floor, meta)
    print(f"gated, sized, booked signals: {len(sig)}", flush=True)
    now = np.datetime64(datetime.now(timezone.utc).date())
    ts = np.array([s["ts"] for s in sig])
    r = np.array([s["r"] for s in sig])
    pairs = np.array([s["pair"] for s in sig])

    # ---- (a) flagging on the three training samples -------------------
    print("\n--- per-instrument R on the training samples (candidates) ---")
    print(f"{'pair':<9}" + "".join(f"{f'{lo}-{hi} d':>16}" for lo, hi in TRAIN)
          + f"{'flag':>7}")
    flagged = []
    for pair in carried:
        cells = []; negatives = 0
        for lo, hi in TRAIN:
            m = sample_mask(ts, lo, hi, now) & (pairs == pair)
            if m.sum() < 10:
                cells.append("n<10"); continue
            mean = float(r[m].mean()); cells.append(f"{mean:+.4f} ({int(m.sum())})")
            if mean < 0: negatives += 1
        hit = negatives == len(TRAIN)
        if hit: flagged.append(pair)
        print(f"{pair:<9}" + "".join(f"{c:>16}" for c in cells)
              + f"{'YES' if hit else '-':>7}")

    # the 23 already-scored instruments, for scale
    others = sorted(set(pairs) - set(carried))
    other_means = []
    for lo, hi in TRAIN:
        m = sample_mask(ts, lo, hi, now) & ~np.isin(pairs, carried)
        other_means.append(float(r[m].mean()) if m.sum() else float('nan'))
    print(f"{'(other 23)':<9}" + "".join(f"{v:>+16.4f}" for v in other_means))

    if not flagged:
        print("\nclause (a) fails — no candidate reads negative on all three "
              "training samples, so section 192's rule produces no block.")
        print("Falling through to the dollar question the daily gain actually "
              "asks: what do the four contribute to the walk-forward? Bar for "
              "a block on that evidence alone, fixed here before it is seen — "
              "the harness's standard clause: better on ALL FOUR year-samples "
              "and t > +2. Anything less is diagnostic only.")
        flagged = list(carried); decided_by_consistency = False
    else:
        decided_by_consistency = True
        print(f"\nflagged: {', '.join(flagged)}")

    # ---- (b) the held-out year decides --------------------------------
    lo, hi = TEST
    test_m = sample_mask(ts, lo, hi, now)
    blocked_m = test_m & np.isin(pairs, flagged)
    diff = np.where(blocked_m, -r, 0.0)[test_m]
    tv = t_stat(diff)
    print(f"\n--- held-out year ({lo}-{hi} d), paired per signal ---")
    print(f"signals {int(test_m.sum())}, of them on the flagged set "
          f"{int(blocked_m.sum())}")
    print(f"flagged set R {float(r[blocked_m].mean()):+.4f} "
          f"(t {t_stat(r[blocked_m]):+.2f})" if blocked_m.sum() > 1 else "")
    rest = test_m & ~np.isin(pairs, flagged)
    print(f"the rest     R {float(r[rest].mean()):+.4f} (t {t_stat(r[rest]):+.2f})")
    print(f"paired difference {diff.mean():+.4f} R at t {tv:+.2f}")

    # ---- (c) the walk-forward in dollars ------------------------------
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    t0 = min(ts); t1 = max(ts)
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    sig_block = [s for s in sig if s["pair"] not in flagged]
    daily = {"live": {}, "block": {}}; counts = {"live": 0, "block": 0}
    for start, end in blocks:
        for label, source in (("live", sig), ("block", sig_block)):
            rw = [s for s in source if start - rank_w <= s["ts"] < start]
            tw = [s for s in source if start <= s["ts"] < end]
            per_day, n = trade(tw, rank(rw, False))
            for d, v in per_day.items():
                daily[label][d] = daily[label].get(d, 0.0) + v
            counts[label] += n
    all_days = sorted(set(daily["live"]) | set(daily["block"]))
    a = np.array([daily["live"].get(d, 0.0) for d in all_days])
    b = np.array([daily["block"].get(d, 0.0) for d in all_days])
    print(f"\n--- walk-forward, {len(blocks)} out-of-sample blocks ---")
    print(f"trades  full universe {counts['live']}   with block {counts['block']}")
    print(f"\n{'sample':<14}{'full':>12}{'blocked':>12}{'diff':>10}{'t':>8}{'days':>7}")
    better = 0; samples = 0
    for lo_, hi_ in [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]:
        sel = sample_mask(all_days, lo_, hi_, now)
        if sel.sum() == 0: continue
        span = float(hi_ - lo_); samples += 1
        av = a[sel].sum() / span; bv = b[sel].sum() / span
        if bv > av: better += 1
        print(f"{lo_}-{hi_} d{'':<4}{av:>+12.4f}{bv:>+12.4f}{bv-av:>+10.4f}"
              f"{t_stat(b[sel]-a[sel]):>+8.2f}{int(sel.sum()):>7}")
    pooled = (b - a)
    print(f"{'pooled':<14}{a.sum()/len(all_days):>+12.4f}"
          f"{b.sum()/len(all_days):>+12.4f}{pooled.sum()/len(all_days):>+10.4f}"
          f"{t_stat(pooled):>+8.2f}{len(all_days):>7}")
    if decided_by_consistency:
        print(f"\nclause (b): t {tv:+.2f} vs > +2 — "
              f"{'PASS' if tv > 2 else 'FAIL'}")
        print(f"clause (c): {better}/{samples} samples up, pooled "
              f"{pooled.sum()/len(all_days):+.4f} USD/day — "
              f"{'PASS' if better >= 3 and pooled.sum() >= 0 else 'FAIL'}")
    else:
        print(f"\ndropping all four on dollar evidence alone: "
              f"{better}/{samples} samples up, pooled "
              f"{pooled.sum()/len(all_days):+.4f} USD/day at t "
              f"{t_stat(pooled):+.2f} — "
              f"{'PASS' if better == samples and t_stat(pooled) > 2 else 'FAIL'}")

asyncio.run(main())

"""Operator pins, held to the eligibility filter the ranked list passes.

`data/pinned_pairs.json` pins 1h donchian and turtle on almost every
instrument of the merged `risk_on` cluster — DE40, FR40, HK50, J225,
UK100, US30, US100, US500, EURUSD, USDCHF, NZDUSD, AUDJPY, CHFJPY and
EURAUD — plus the crypto, metals and oil names. A pin bypasses ranking,
eligibility and the top-40 cut, so these combinations sit in the active
list whatever their trailing year looks like; the list of 2026-09-11
carries pins at scores down to -0.33. The exclusive 4h pins additionally
reserve SILVER, NZDUSD, HK50, COPPER and CHFJPY against ranked 1h combos.

Section 219 compared the ranked list, the pins and both, and found three
lists within 0.017 USD/day of each other. It ran without the cluster cap.
With the cap in place (section 252) the `risk_on` cluster refuses about
4,000 entries, so every slot there is contested — and a pin that fails
the eligibility filter holds such a slot as readily as a ranked
combination that passes it. Run 219 could not see that interaction.

The lever keeps every pin in principle and asks only that a pin meet the
filter the scheduler already applies to everything else (at least 10
trades in the trailing year, pf >= 0.8, eR >= -0.2). It adds nothing to
the list and moves no guard.

  live       ranked top 40 (after exclusive reservations) + every 1h pin
  candidate  ranked top 40 + the 1h pins that pass eligibility
  diag       ranked top 40 only; pins only

Pins and today's live vetoes are taken for all seven years in every arm,
so the comparison isolates the filter, not the operator's choice of pins.
A retired pin reserves nothing, as in `persist_active_pairs`. 4h pins lie
outside the harness; the reservations of the live ones are kept.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 255.
"""
import asyncio, json, math, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.strategies import add_indicators
from app.utils.singletons import settings
settings.load_env()
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.trading_blocks import DISABLED_LIVE_STRATEGIES
from app.spot_trading.pair_selector import live_expectancy_veto, strategy_expectancy_veto
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, all_signals, admit,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS,
)

MIN_PF = 0.8; MIN_ER = -0.2; MIN_N = 10; LIVE_N = 40
YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
PINS_PATH = "data/pinned_pairs.json"
VETOED = set()
# The bot skips every listed combination of a retired strategy, ranked or
# pinned (section 339), so the replay's lists drop them too.
VETOED_STRATEGIES = set()


def load_pins(pairs, vetoed, vetoed_strategies):
    """Today's pins as `persist_active_pairs` sees them.

    Retired pins are dropped before they reserve anything, exactly as
    the selector does: a vetoed or disabled strategy's exclusive pin
    reserves no pair."""
    def retired(c):
        return (c["strategy"] in DISABLED_LIVE_STRATEGIES
                or c["strategy"] in vetoed_strategies
                or (c["strategy"], c["pair"]) in vetoed)
    VETOED_STRATEGIES.update(vetoed_strategies)
    combos = [c for c in json.load(open(PINS_PATH))["combos"] if not retired(c)]
    pins = {(c["strategy"], c["pair"]) for c in combos
            if c["resolution"] == "1h" and c["strategy"] in STRATS and c["pair"] in pairs}
    reserved = {c["pair"] for c in combos if c.get("exclusive")}
    return pins, reserved


def lists(window, pins, reserved):
    """(ranked top 40, eligible set) for one ranking window."""
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []; eligible = set()
    for key, ts in agg.items():
        if len(ts) < MIN_N: continue
        r = np.array([t["r"] for t in ts])
        eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains / losses)
        if pf < MIN_PF or eR < MIN_ER: continue
        eligible.add(key)
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    ranked = [k for _, k in rows
              if (k[1] not in reserved or k in pins) and k not in VETOED
              and k[0] not in VETOED_STRATEGIES]
    return set(ranked[:LIVE_N]), eligible


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {}
    for pair, rows in raw.items():
        if pair not in meta or len(rows) < 2000: continue
        frames[pair] = add_indicators(to_frame(rows))
    VETOED.update(live_expectancy_veto("capital_com"))
    vetoed_strategies = set(strategy_expectancy_veto("capital_com"))
    pins, reserved = load_pins(set(frames), VETOED, vetoed_strategies)
    print(f"today's vetoes applied to every arm: combos {sorted(VETOED)}; "
          f"strategies {sorted(vetoed_strategies)}", flush=True)
    print(f"instruments={len(frames)} atr_floor={atr_floor:g}; 1h pins in the harness "
          f"{len(pins)}; reserved pairs {sorted(reserved & set(frames))}", flush=True)

    sig = all_signals(frames, atr_floor, meta)
    print(f"gated, sized, booked signals: {len(sig)}", flush=True)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"out-of-sample blocks: {len(blocks)}", flush=True)

    arms = ("live", "candidate", "diag ranked only", "diag pins only")
    daily = {a: {} for a in arms}; taken = {a: [] for a in arms}
    sizes = {a: [] for a in arms}; dropped_pins = []
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        ranked, eligible = lists(rw, pins, reserved)
        kept_pins = {p for p in pins if p in eligible}
        dropped_pins.append(len(pins) - len(kept_pins))
        active = {"live": ranked | pins, "candidate": ranked | kept_pins,
                  "diag ranked only": ranked, "diag pins only": set(pins)}
        for a in arms:
            got = admit(tw, active[a])
            sizes[a].append(len(active[a]))
            for t in got:
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[a][d] = daily[a].get(d, 0.0) + t["usd"]
            taken[a].extend(got)
    print(f"pins failing eligibility per block: mean {np.mean(dropped_pins):.1f} "
          f"of {len(pins)}", flush=True)

    base = daily["live"]
    for a in arms:
        u = np.array([t["usd"] for t in taken[a]])
        print(f"\n--- {a} --- list {np.mean(sizes[a]):.1f} combos, trades {len(taken[a])}, "
              f"USD/trade {u.mean():+.4f}")
        if a == "live": continue
        days = sorted(set(base) | set(daily[a]))
        x = np.array([base.get(d, 0.0) for d in days]); y = np.array([daily[a].get(d, 0.0) for d in days])
        print(f"{'sample':<14}{'live':>11}{'variant':>11}{'diff':>10}{'t':>8}")
        up = 0
        for lo, hi in YEARS:
            sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                            < (now - np.timedelta64(lo, 'D')) for d in days])
            span = float(hi - lo)
            av = x[sel].sum() / span; bv = y[sel].sum() / span
            if bv > av: up += 1
            print(f"{lo}-{hi} d{'':<4}{av:>+11.4f}{bv:>+11.4f}{bv-av:>+10.4f}"
                  f"{t_stat(y[sel]-x[sel]):>+8.2f}")
        d_ = y - x
        print(f"{'pooled':<14}{x.sum()/len(days):>+11.4f}{y.sum()/len(days):>+11.4f}"
              f"{d_.sum()/len(days):>+10.4f}{t_stat(d_):>+8.2f}")
        if a == "candidate":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
                  f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())

"""Section 261's reversed hypothesis, read on the live journal first.

Section 261 refused same-direction entries into a cluster whose held
positions were losing, and found the refused entries were the better
ones: +0.0822 USD a signal at t +3.39 over seven years of harness
history. That statistic came from the same data any walk-forward of the
inverse rule would score on, so section 115's rule applies — read it on
data that did not select it before building anything.

The live journal did not select it. For every closed live trade the bot
opened while positions of the same cluster and direction were already
open, the held positions' mean open R at the moment of entry is
reconstructed from the hourly bar cache, and the entries are split:

  L  held positions' mean open R <= 0 (adding to losers)
  W  held positions' mean open R >  0 (adding to winners)
  N  no same-cluster, same-direction position held (for context)

Preregistered gate (section 261): the inverse rule is only taken to the
walk-forward if L beats W here too, in R per trade. The journal is read
only.

See docs/EDGE_FINDINGS.md 262.
"""
import json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.utils.singletons import settings
settings.load_env()
from app.utils.singletons import database
from app.spot_trading.autotrade import _CORRELATION_CLUSTERS
from scripts.efficiency_weighted_selection import BAR_CACHE, t_stat


def parse(ts):
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def main():
    rows = database.select("""
        SELECT id, created_at, pair, strategy, direction, fill_price, entry_price,
               stop_loss, exit_price, exit_time, size, outcome
        FROM spot_trades
        WHERE accepted = 1 AND paper_mode = 0 AND platform = 'capital_com'
          AND exit_time IS NOT NULL AND exit_price IS NOT NULL AND size > 0
          AND COALESCE(outcome, '') <> 'abandoned'
    """, ())
    trades = []
    for r in rows:
        fill = r["fill_price"] or r["entry_price"]
        risk = abs(fill - r["stop_loss"])
        if not risk: continue
        trades.append({**r, "fill": fill, "risk": risk,
                       "open": parse(r["created_at"]), "close": parse(r["exit_time"]),
                       "R": (r["exit_price"] - fill) * r["direction"] / risk,
                       "cluster": _CORRELATION_CLUSTERS.get(r["pair"])})
    print(f"closed live trades: {len(trades)}, "
          f"{min(t['open'] for t in trades):%Y-%m-%d} … {max(t['open'] for t in trades):%Y-%m-%d}")

    bars = {}
    def close_at(pair, when):
        if pair not in bars:
            path = os.path.join(BAR_CACHE, f"{pair}.json")
            if not os.path.exists(path): bars[pair] = None
            else:
                raw = json.load(open(path))
                bars[pair] = (np.array([np.datetime64(parse(x[0]).replace(tzinfo=None)) for x in raw]),
                              np.array([x[4] for x in raw]))
        b = bars[pair]
        if b is None: return None
        # the bar stamped at hour h closes at h+1; only completed bars count
        i = int(np.searchsorted(b[0], np.datetime64(when.replace(tzinfo=None)) - np.timedelta64(1, 'h'),
                                side="right")) - 1
        if i < 0 or b[0][-1] < np.datetime64(when.replace(tzinfo=None)) - np.timedelta64(2, 'h'):
            return None
        return float(b[1][i])

    groups = {"L": [], "W": [], "N": []}; unpriced = 0
    for e in trades:
        if e["cluster"] is None:
            continue
        held = [h for h in trades if h is not e and h["cluster"] == e["cluster"]
                and h["direction"] == e["direction"] and h["open"] < e["open"] < h["close"]]
        if not held:
            groups["N"].append(e); continue
        opens = []
        for h in held:
            px = close_at(h["pair"], e["open"])
            if px is None: continue
            opens.append((px - h["fill"]) * h["direction"] / h["risk"])
        if not opens:
            unpriced += 1; continue
        groups["L" if np.mean(opens) <= 0 else "W"].append(e)

    print(f"entries with held positions but no bar to price them: {unpriced}")
    print(f"\n{'group':<34}{'n':>5}{'mean R':>10}{'t':>8}{'median R':>10}{'win %':>8}")
    labels = {"L": "L  added to a losing direction", "W": "W  added to a winning direction",
              "N": "N  nothing held in the direction"}
    for g in ("L", "W", "N"):
        r = np.array([x["R"] for x in groups[g]])
        if len(r) == 0:
            print(f"{labels[g]:<34}{0:>5}"); continue
        print(f"{labels[g]:<34}{len(r):>5}{r.mean():>+10.4f}{t_stat(r):>+8.2f}"
              f"{np.median(r):>+10.4f}{(r > 0).mean():>8.0%}")
    rl = np.array([x["R"] for x in groups["L"]]); rw = np.array([x["R"] for x in groups["W"]])
    if len(rl) > 1 and len(rw) > 1:
        diff = rl.mean() - rw.mean()
        se = np.sqrt(rl.var(ddof=1) / len(rl) + rw.var(ddof=1) / len(rw))
        print(f"\nL - W: {diff:+.4f} R per trade, Welch t {diff / se:+.2f}")
        print(f"preregistered gate (L beats W): {'PASS' if diff > 0 else 'FAIL'}")


if __name__ == "__main__":
    main()

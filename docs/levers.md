# Lever log

One entry per run: the lever, how it was measured, the result and the
decision. Read this before testing anything — an idea listed here is not
tested a second time. Detailed measurements live in `EDGE_FINDINGS.md`;
the section numbers below point there.

## Levers already tested before this log existed

| lever | measurement | result | decision | ref |
|---|---|---|---|---|
| parameter grid (RR 1.0–3.5, stop 0.75–2.0 ATR, hold 6–48, ADX 15–40, 1h/4h/1d, 9 families) | walk-forward, 154,560 variants | 0 survive Bonferroni | tuning closed | 1 |
| cost ceiling (cost/risk ≤ 10 %) | live journal, 481 trades | monotonic; later shown to be an artefact of expensive pairs | ceiling kept as backstop | 3, 11, 24 |
| high-cost instrument blocklist | instrument-level spread audit vs live stops | 13 instruments structurally untradeable | blocked | 24, 42 |
| OIL_CRUDE block | spread audit | 3.9 % of risk, same as OIL_BRENT | not blocked | 49b |
| reward:risk 1.0–3.0 | walk-forward net of costs, 60 d, 10 pairs | 1.5 among the best, no ordering | 1.5 stays | 15, 49d |
| stop_atr multiple | 1h/4h sweep | noise, floor dominates | 1.0 stays | 25 (retracted path), 28 |
| trailing exit (donchian_trail) | backtest with trail modelled, 4h 150 d | -0.648 R, PF 0.19 | strategy blocked | 27 |
| holding leash 24 vs 240 bars (donchian_trail) | backtest 1h 30 d | both ≈ 0 | n/a, strategy blocked | 17 |
| ADX regime router | passed vs rejected, 1 y / 3 y / live forward | t = -1.98 → -0.34 → -0.35 | router stays on | 46, 46b, 49e |
| 15m timeframe | backtest | more trades, worse E[R] | rejected | 29 |
| cross-sectional relative strength | 1 y, properly powered | negative | rejected | 31, 33 |
| volume filter / volume signal | 3 y | no effect | rejected | 41, 41b |
| mean reversion family | live, 121+ trades | -0.128 R live despite best backtest | retired | 2 |
| random-entry benchmark | 1 y / 3 y | signals ≈ random, sign flips | no edge established | 30, 32, 45 |
| tighter selection filters | selector sweep | not supported | unchanged | 22, 35, 36 |
| time-based exits (+0.118 R after risk floor) | live journal, 111 exits | t = 1.76, selected after the fact | recorded, not acted on | 47 |

## 2026-09-07 — holding leash (`max_hold`) for the live trend strategies

- **Lever:** exit logic — the 24-bar stale-exit leash. Section 47 hinted
  that time-based exits carry the positive tail, and 49d showed FX
  resolving 100 % by timeout, so the leash length was the one exit
  parameter not yet measured on the corrected, cost-charging simulator.
- **Measurement:** `scripts/max_hold_sweep.py` — the walk-forward
  simulator of `scripts/walk_forward.py` (spread and venue minimum stop
  charged), capital_com, 1h, 365 days, 3 segments, RR 1.5, stop 1.0 ATR,
  10 unblocked instruments (BTCUSD, ETHUSD, OIL_CRUDE, OIL_BRENT, GOLD,
  DE40, US500, US30, EURUSD, AUDUSD), the three live 1h trend strategies,
  holds 6 / 12 / 24 / 48 / 96 bars. Baseline = current 24 bars; the
  difference column is a Welch t on pooled per-trade R.
- **Result (pooled over the three strategies):**

  | hold | n | E[R] | t vs 0 | timeout % | diff vs 24 | t_diff |
  |---:|---:|---:|---:|---:|---:|---:|
  | 6 | 8,336 | -0.0143 | -1.79 | 72 | -0.0195 | -1.27 |
  | 12 | 7,062 | -0.0067 | -0.65 | 57 | -0.0119 | -0.72 |
  | **24 (live)** | 5,832 | **+0.0052** | +0.40 | 38 | — | — |
  | 48 | 5,001 | -0.0105 | -0.67 | 20 | -0.0157 | -0.77 |
  | 96 | 4,533 | -0.0241 | -1.38 | 9 | -0.0293 | -1.34 |

  Per strategy the ordering is the same: 24 bars is the maximum for
  donchian_breakout (+0.0142), keltner_breakout (+0.0073) and within
  noise of it for turtle_breakout (-0.0120 vs +0.0023 at 12 bars,
  t = 0.07). Every alternative is worse or indistinguishable, and no
  value is significant against zero.
- **Decision:** not built in — the lever is dead. `max_hold` stays at 24
  bars. Nothing was changed in the trading code; no restart.

## 2026-09-07 (second run) — session-window filter (time-of-day regime)

- **Lever:** regime filter — take signals only inside the European/US
  session. Section 7 had deliberately never opened time-of-day variants
  because a free search over windows overfits; this run opened exactly
  one, preregistered before the data were seen: signal bar in
  [07:00, 20:00) UTC vs the rest, acceptance at t > 2.0 on the difference.
- **Measurement:** `scripts/session_split.py` — the cost-charging
  walk-forward simulator, capital_com, 1h, hold 24, RR 1.5, stop 1.0 ATR,
  the three live 1h trend strategies, the same 10 unblocked instruments as
  the max-hold sweep. Two disjoint samples: the last 365 days, then the
  two years before them (days 366–1,095) as the independent check.
- **Result (pooled over strategies, E[R] net of costs):**

  | sample | n in | E[R] in | n out | E[R] out | in − out | t |
  |---|---:|---:|---:|---:|---:|---:|
  | last 365 d | 4,065 | -0.0130 | 1,764 | **+0.0498** | -0.0628 | **-2.17** |
  | prior 730 d | 8,614 | -0.0101 | 3,172 | **-0.0487** | +0.0386 | **+1.89** |

  The preregistered direction (session better) fails on the recent
  sample and the reverse reading, which would have passed a naive t
  threshold, flips sign on the independent one. Per asset class the
  older sample shows index +0.0002 (t = 0.00) and only FX at t = +2.13,
  which is one of four classes and post hoc. The 3-hour buckets
  disagree between the samples bucket by bucket (00–09 UTC: +0.07 to
  +0.11 recent, -0.02 to -0.05 prior).
- **Decision:** not built in — dead in both directions. Fifth time a
  |t| ≈ 2 reading dissolved on an independent sample. Nothing changed in
  the trading code; no restart.

## 2026-09-07 (third run) — break-even stop move

- **Lever:** exit logic — once price has travelled `act` × stop distance
  in favour, move the stop to the entry price (no trailing beyond that).
  Distinct from the rejected ATR trail (27), which kept riding behind
  price and capped winners; this only removes the full −1 R leg after a
  trade has already worked.
- **Measurement:** `scripts/breakeven_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 365 days, 3 segments, hold 24,
  RR 1.5, stop 1.0 ATR, three live trend strategies, the same 10 unblocked
  instruments. Activation at 0.5 / 0.75 / 1.0 R against the live fixed
  stop. A stop touched after arming books a scratch at entry minus costs.
- **Result (pooled over strategies):**

  | activation | n | E[R] | win % | loss % | scratch % | timeout % | diff vs live | t |
  |---:|---:|---:|---:|---:|---:|---:|---:|---:|
  | **none (live)** | 5,827 | +0.0063 | 24.3 | 36.9 | 0.0 | 38.8 | — | — |
  | 0.5 R | 6,333 | +0.0110 | 17.4 | 25.2 | 30.0 | 27.4 | +0.0046 | +0.28 |
  | 0.75 R | 6,062 | +0.0048 | 20.1 | 30.2 | 17.4 | 32.2 | -0.0015 | -0.09 |
  | 1.0 R | 5,931 | +0.0010 | 21.9 | 33.5 | 9.6 | 35.1 | -0.0054 | -0.30 |

  The move converts a third of the full stops into scratches and the
  same share of full targets into scratches; the two cancel to within
  0.005 R at every level and in every strategy (largest single reading
  keltner 0.5 R at t = +0.71). Full-loss frequency drops, expectancy
  does not.
- **Decision:** not built in — dead. No code change, no restart.

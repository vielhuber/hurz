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

## 2026-09-07 (fourth run) — pullback entry at the breakout level

- **Lever:** new entry logic — after a breakout signal, do not enter at
  the signal close but place a limit at the breakout level (the channel
  high/low or the Keltner band the close just crossed) and enter only if
  price retraces to it within K bars. Trades that never retrace are
  skipped. Preregistered as the first "new signal source" candidate
  after the exit side was closed.
- **Measurement:** `scripts/pullback_entry.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 365 days, 3 segments, hold 24
  from the fill bar, RR 1.5, stop 1.0 ATR, full round-trip spread still
  charged on the limit fill, three live trend strategies, the same 10
  unblocked instruments. K = 3 / 6 / 12 bars against K = 0 (live market
  entry at the signal close).
- **Result (pooled over strategies):**

  | K | n | fill % | E[R] | win % | loss % | Σ R / year | diff vs live | t |
  |---:|---:|---:|---:|---:|---:|---:|---:|---:|
  | **0 (live)** | 5,830 | 100 | +0.0063 | 24.3 | 36.9 | +36.7 | — | — |
  | 3 | 4,499 | 67 | -0.0058 | 22.7 | 36.2 | -26.0 | -0.0121 | -0.61 |
  | 6 | 4,789 | 74 | +0.0015 | 23.2 | 36.4 | +7.1 | -0.0048 | -0.25 |
  | 12 | 4,941 | 80 | +0.0193 | 23.9 | 35.7 | +95.4 | +0.0130 | +0.67 |

  The better price on the fill (roughly the breakout overshoot) is paid
  for by the trades that never come back — the strongest breakouts —
  and by a filled trade starting closer to its stop in time. No ordering
  in K, turtle_breakout worse at every K (-0.045 to -0.014), nothing
  near significance. Live implementation would in addition need working
  orders at the venue, which this measurement did not have to model.
- **Decision:** not built in — dead. No code change, no restart.

## 2026-09-07 (fifth run) — failed-breakout reversal

- **Lever:** new signal source — when a close returns inside the broken
  level within K bars of a breakout signal, enter *against* the breakout
  at that close (the "fade the failed breakout" idea). Measured against
  count-matched random entries through the identical simulator, as the
  random benchmark sections 30 / 45 require, and against the live
  breakout entry.
- **Measurement:** `scripts/failed_breakout.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 365 days, 3 segments, hold 24,
  RR 1.5, stop 1.0 ATR, three live trend strategies as the breakout
  source, the same 10 unblocked instruments, K = 3 / 6, five random
  draws per segment for the control.
- **Result (pooled over strategies):**

  | K | n | E[R] | t vs 0 | E[R] random | diff vs random | t | diff vs live breakout | t |
  |---:|---:|---:|---:|---:|---:|---:|---:|---:|
  | 3 | 3,904 | **-0.0632** | **-4.14** | -0.0230 | -0.0402 | **-2.38** | -0.0695 | **-3.45** |
  | 6 | 4,584 | -0.0473 | -3.30 | -0.0277 | -0.0196 | -1.24 | -0.0536 | -2.76 |

  Negative in every strategy at every K (keltner K = 3: -0.081, t =
  -3.13). This is the first signal in the project that is significantly
  *worse* than random, which is the mean-reversion payoff shape retired
  in July (2, 49e) showing up again from a different trigger.
- **Decision:** not built in — dead. The mirror image (re-entering with
  the breakout after the first close back inside) is not a free lever:
  reversing direction flips the gross leg but doubles the cost leg and
  breaks the 1.5:1 asymmetry, and it would be post hoc. No code change,
  no restart.

## 2026-09-07 (sixth run) — breakout retest continuation

- **Lever:** new signal source, preregistered at the end of the fifth
  run — when a close returns inside the broken level within K bars of a
  breakout, enter *with* the breakout at that close (the classic
  "retest" entry). Same simulator, controls and instruments as the
  failed-breakout run, only the direction differs.
- **Measurement:** `scripts/retest_continuation.py` — cost-charging
  walk-forward, capital_com, 1h, 365 days, 3 segments, hold 24, RR 1.5,
  stop 1.0 ATR, three live trend strategies as the trigger, 10 unblocked
  instruments, K = 3 / 6, count-matched random control.
- **Result (pooled over strategies):**

  | K | n | E[R] | t vs 0 | vs random | t | vs live breakout | t |
  |---:|---:|---:|---:|---:|---:|---:|---:|
  | 3 | 3,776 | -0.0082 | -0.52 | +0.0147 | +0.85 | -0.0145 | -0.71 |
  | 6 | 4,420 | -0.0201 | -1.36 | +0.0077 | +0.47 | -0.0264 | -1.34 |

  Indistinguishable from random, slightly below the live entry, no
  strategy above t = 1.4. The fade (54) lost significantly and its
  mirror does not win: the difference between the two is the doubled
  cost leg and the inverted payoff, exactly as section 54 said.
- **Decision:** not built in — dead. No code change, no restart.

## 2026-09-07 (seventh run) — opening-range breakout on the indices

- **Lever:** new signal source, structurally outside the channel family
  — the cash-open hour bar (DE40 07:00 UTC, US500 / US30 / US100 13:00
  UTC) is the range; the first close beyond it within six bars of the
  same day enters in that direction, one trade per day. Preregistered:
  accepted only if better than count-matched random entries at t > 2.0
  on the last year *and* on the disjoint two years before.
- **Measurement:** `scripts/orb_breakout.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 1.0 ATR, random control with five draws per segment, live
  donchian_breakout on the same bars as the second comparison.
- **Result (pooled over the four indices):**

  | sample | n | E[R] | t vs 0 | vs random | t | vs donchian | t |
  |---|---:|---:|---:|---:|---:|---:|---:|
  | last 365 d | 609 | **+0.0843** | **+2.46** | +0.0792 | **+2.09** | +0.0925 | +2.03 |
  | prior 730 d | 1,166 | +0.0096 | +0.41 | +0.0111 | +0.43 | -0.0198 | -0.63 |

  The recent year clears every threshold at once — and the two years
  before, with twice the trades, read zero. Per index the sign is not
  stable either: DE40 +0.115 then -0.002, US30 negative in both. Sixth
  near-threshold value in this project to dissolve on an independent
  sample.
- **Decision:** not built in — dead. No code change, no restart.

## 2026-09-07 (eighth run) — previous-day range breakout on the commodities

- **Lever:** new signal source — the first hourly close of a UTC day
  above the previous day's high (below its low) enters long (short),
  one trade per day, on GOLD, OIL_CRUDE and OIL_BRENT. Preregistered
  with the same two-sample criterion as the opening-range run (better
  than count-matched random entries at t > 2.0 on both samples).
- **Measurement:** `scripts/prev_day_range_breakout.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 1.0 ATR, random control with five draws per segment, live
  donchian_breakout on the same bars as the second comparison.
- **Result (pooled over the three commodities):**

  | sample | n | E[R] | t vs 0 | vs random | t | vs donchian | t |
  |---|---:|---:|---:|---:|---:|---:|---:|
  | last 365 d | 524 | +0.0071 | +0.14 | +0.0464 | +0.84 | -0.0588 | -0.89 |
  | prior 730 d | 976 | -0.0363 | -1.11 | +0.0105 | +0.29 | +0.0322 | +0.74 |

  Zero on both samples, both oils negative on both, GOLD positive but
  at t = 1.6 / 1.3 and post hoc as a single instrument. Below the live
  entry on the recent year.
- **Decision:** not built in — dead. No code change, no restart.

## 2026-09-07 (ninth run) — donchian channel length

- **Lever:** strategy parameter — the 20-bar channel of
  `donchian_breakout`, the one grid dimension section 1 never swept
  (RR, stop, hold and ADX were). Periods 10 / 40 / 80 against the live
  20, two-sample criterion (better than live at t > 2.0 on both).
- **Measurement:** `scripts/donchian_period_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 1.0 ATR, the 10 unblocked instruments, last 365 days and the
  disjoint two years before.
- **Result (pooled over instruments, diff vs live period 20):**

  | period | last 365 d: n / E[R] / diff / t | prior 730 d: n / E[R] / diff / t |
  |---:|---|---|
  | 10 | 2,864 / -0.0143 / -0.0292 / -1.03 | 5,570 / -0.0320 / +0.0024 / +0.12 |
  | **20 (live)** | 2,277 / +0.0149 / — | 4,576 / -0.0344 / — |
  | 40 | 1,725 / -0.0323 / -0.0472 / -1.48 | 3,458 / -0.0176 / +0.0168 / +0.77 |
  | 80 | 1,178 / +0.0255 / +0.0106 / +0.30 | 2,440 / -0.0033 / +0.0311 / +1.29 |

  Period 40 flips sign between the samples, 80 is positive on both but
  far from threshold and halves the trade count, 10 adds trades at
  lower expectancy. The live 20 stays.
- **Decision:** not built in — dead. No code change, no restart.

## 2026-09-07 (tenth run) — keltner band width

- **Lever:** strategy parameter — the 2.0 × ATR band of
  `keltner_breakout`, never swept before. Multiples 1.0 / 1.5 / 3.0
  against the live 2.0, two-sample criterion.
- **Measurement:** `scripts/keltner_width_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 1.0 ATR, the 10 unblocked instruments, last 365 days and the
  disjoint two years before, run sequentially.
- **Result (pooled over instruments, diff vs live 2.0):**

  | atr_mult | last 365 d: n / E[R] / diff / t | prior 730 d: n / E[R] / diff / t |
  |---:|---|---|
  | 1.0 | 2,761 / -0.0025 / -0.0110 / -0.38 | 5,275 / -0.0315 / -0.0301 / -1.50 |
  | 1.5 | 2,469 / +0.0080 / -0.0005 / -0.02 | 4,919 / -0.0191 / -0.0177 / -0.87 |
  | **2.0 (live)** | 2,109 / +0.0085 / — | 4,264 / -0.0014 / — |
  | 3.0 | 1,213 / -0.0191 / -0.0276 / -0.77 | 2,633 / +0.0067 / +0.0082 / +0.34 |

  Narrower bands add trades at lower expectancy on both samples; the
  wider band is worse on the recent year and negligibly better on the
  older one. The live 2.0 stays.
- **Decision:** not built in — dead. No code change, no restart.

## 2026-09-07 (eleventh run) — stop width (stop_atr) — BUILT IN

- **Lever:** exit logic / cost — the stop distance in ATR multiples.
  Section 25's sweep had run through the faulty pre-28 path, so the
  live 1.0 had never been measured on the corrected, cost-charging
  simulator. Multiples 1.5 / 2.0 / 3.0 against 1.0, three live trend
  strategies pooled, two disjoint samples, with the net result split
  into its gross and cost components.
- **Measurement:** `scripts/stop_width_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  the 10 unblocked instruments, run sequentially per sample.
- **Result (pooled, diff vs live 1.0):**

  | stop_atr | sample | n | E[R] net | cost R | gross | net diff | t | gross diff | timeout % |
  |---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
  | 1.0 | last 365 d | 5,831 | +0.0061 | 0.0301 | +0.0362 | — | — | — | 38.8 |
  | 1.5 | last 365 d | 5,664 | +0.0086 | 0.0266 | +0.0352 | +0.0025 | +0.13 | -0.0011 | 41.5 |
  | **2.0** | last 365 d | 5,428 | +0.0284 | 0.0228 | +0.0512 | **+0.0223** | +1.21 | +0.0150 | 47.0 |
  | 3.0 | last 365 d | 5,098 | +0.0289 | 0.0174 | +0.0463 | +0.0228 | +1.29 | +0.0101 | 60.6 |
  | 1.0 | prior 730 d | 11,792 | -0.0203 | 0.0316 | +0.0113 | — | — | — | 43.1 |
  | 1.5 | prior 730 d | 11,508 | -0.0166 | 0.0287 | +0.0120 | +0.0037 | +0.29 | +0.0008 | 45.1 |
  | **2.0** | prior 730 d | 11,158 | -0.0121 | 0.0253 | +0.0131 | **+0.0082** | +0.66 | +0.0019 | 49.1 |
  | 3.0 | prior 730 d | 10,516 | -0.0083 | 0.0195 | +0.0112 | +0.0121 | +0.99 | -0.0000 | 60.7 |

  Same sign on both samples and monotonic across four levels on both —
  the first lever in this log to do either. The gain decomposes: the
  cost term falls arithmetically (0.030 → 0.023 R at 2.0, because the
  spread is charged on price but measured against the stop), and the
  gross term is not worse on either sample (+0.015, +0.002). The
  statistical part of the claim is only "gross does not get worse"; the
  cost part is an identity, not a hypothesis. The live journal points
  the same way independently: trades whose stop sat on the venue floor
  (wider than 1 ATR) returned +0.19 R against -0.43 R for ATR-set stops
  (49d, n = 108 / 53, not significant).
- **Decision:** built in at **2.0**, not 3.0 — 3.0 leaves three trades
  in five to the 24-bar timeout, which turns the strategy into a
  barrier-less hold, while 2.0 keeps a working stop and takes most of
  the cost saving. Risk per trade is unchanged: sizing divides the same
  3 USD by a wider stop, so positions halve, and the fail-closed
  minimum-size guard skips rather than enlarges any trade the halved
  size cannot fill. Expected effect is small and honest — about
  +0.007 R per trade from cost alone, +0.02 R if the recent-year gross
  reading holds — but it is the only lever measured here whose sign the
  second sample did not overturn. `DEFAULT_STOP_ATR` in
  `strategy_parameters.py` is now read by the live loop, both backtest
  CLIs and the walk-forward function; `tests/test_stop_atr_parity.py`
  pins them together. Nightly backtest results persisted at 1.0 will be
  replaced by the next selector run. Hurz restarted.

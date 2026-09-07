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

## 2026-09-07 (twelfth run) — reward:risk at the new 2-ATR stop

- **Lever:** exit logic — the 1.5 target, re-measured against the
  configuration now live. 49d had swept it at the 1-ATR stop over 60
  days; the wider stop changes the cost-per-R balance, so the question
  was open for the current state. RR 1.0 / 2.0 / 3.0 against 1.5,
  stop fixed at 2.0 ATR, three live trend strategies pooled, two
  disjoint samples, gross and cost split.
- **Measurement:** `scripts/rr_at_stop_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, the 10
  unblocked instruments, run sequentially per sample.
- **Result (pooled, diff vs live 1.5):**

  | rr | last 365 d: net diff / t / gross diff / timeout % | prior 730 d: net diff / t / gross diff / timeout % |
  |---:|---|---|
  | 1.0 | -0.0185 / -1.09 / -0.0181 / 35 | +0.0001 / +0.01 / +0.0006 / 38 |
  | **1.5 (live)** | — / — / — / 47 | — / — / — / 49 |
  | 2.0 | +0.0095 / +0.50 / +0.0094 / 54 | +0.0009 / +0.07 / +0.0007 / 55 |
  | 3.0 | +0.0157 / +0.78 / +0.0155 / 61 | +0.0125 / +0.92 / +0.0120 / 62 |

  Unlike the stop width there is no arithmetic component here — the
  cost term is flat across rr — so the whole difference is a gross
  claim, and it sits at t < 1 on both samples while pushing three
  trades in five to the timeout. Same sign twice is necessary, not
  sufficient.
- **Decision:** not built in — 1.5 stays. No code change, no restart.

## 2026-09-07 (thirteenth run) — holding leash at the new 2-ATR stop

- **Lever:** exit logic — the 24-bar leash re-measured at the
  configuration now live, since the wider stop raised the timeout share
  from 39 % to 47 % and the first sweep (run 1) ran at the 1-ATR stop.
  Holds 12 / 48 / 96 against 24, stop 2.0 ATR, RR 1.5, three live trend
  strategies pooled, two disjoint samples, gross and cost split.
- **Measurement:** `scripts/max_hold_at_stop_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, the 10 unblocked
  instruments, run sequentially per sample.
- **Result (pooled, diff vs live 24):**

  | hold | last 365 d: net diff / t / timeout % | prior 730 d: net diff / t / timeout % |
  |---:|---|---|
  | 12 | -0.0292 / -1.82 / 67 | +0.0001 / +0.01 / 68 |
  | **24 (live)** | — / — / 47 | — / — / 49 |
  | 48 | -0.0300 / -1.45 / 26 | +0.0050 / +0.35 / 28 |
  | 96 | -0.0411 / -1.81 / 11 | +0.0169 / +1.06 / 12 |

  24 is the maximum on the recent year at every alternative; on the
  older sample the long leash is mildly positive, so the direction flips
  between samples. The cost column barely moves, so nothing here is
  mechanical.
- **Decision:** not built in — 24 stays. No code change, no restart.

## 2026-09-07 (fourteenth run) — turtle channel length

- **Lever:** strategy parameter — the 55-bar channel of
  `turtle_breakout`, measured at the configuration now live (2-ATR
  stop). Periods 20 / 110 against 55, two-sample criterion.
- **Measurement:** `scripts/turtle_period_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, the 10 unblocked instruments, run sequentially per sample.
- **Result (pooled over instruments, diff vs live 55):**

  | period | last 365 d: n / E[R] / diff / t | prior 730 d: n / E[R] / diff / t |
  |---:|---|---|
  | 20 | 2,105 / +0.0329 / +0.0238 / +0.72 | 4,303 / -0.0236 / -0.0048 / -0.21 |
  | **55 (live)** | 1,363 / +0.0091 / — | 2,816 / -0.0188 / — |
  | 110 | 928 / +0.0296 / +0.0206 / +0.51 | 2,001 / +0.0064 / +0.0251 / +0.93 |

  20 flips sign between samples (and is donchian's channel anyway).
  110 is positive on both but at t < 1 with a third fewer trades, and
  it carries no cost component that would make part of it arithmetic.
- **Decision:** not built in — 55 stays. No code change, no restart.

## 2026-09-07 (fifteenth run) — minimum-size skips at the halved position

- **Lever:** position sizing / pair selection — the 2-ATR stop halves
  the position, so the fail-closed minimum-size guard could start
  refusing trades. Measured how many, on which instruments, with what
  expectancy, and whether those instruments should leave the active
  list. Live broker constraints and the live sizing function were used.
- **Measurement:** `scripts/min_size_skips.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 365 days, 3 segments, hold 24,
  RR 1.5, three live trend strategies, the 10 unblocked instruments,
  `calculate_position_size` with the broker's min size and increment,
  at the former 1-ATR and the live 2-ATR stop.
- **Result:**

  | stop | n | skipped | planned risk kept | E[R] all | E[R] kept | E[R] skipped | USD / trade kept |
  |---:|---:|---:|---:|---:|---:|---:|---:|
  | 1.0 | 5,831 | 0.1 % | 2.52 USD | +0.0060 | +0.0074 | -1.01 | +0.015 |
  | **2.0 (live)** | 5,428 | **1.4 %** | 2.59 USD | +0.0283 | +0.0303 | -0.11 | +0.078 |

  Skips occur only on OIL_CRUDE (6.3 %) and OIL_BRENT (7.5 %), whose
  1-lot minimum is coarse against a wider stop; every other instrument
  sizes at 0 % skips. The skipped trades carry negative expectancy on
  this sample, so refusing them costs nothing. Planned risk moves
  closer to the 3 USD target because the wider stop needs less notional
  and the 250 USD cap binds less often. The stop change only bites on
  BTCUSD, ETHUSD, the oils and GOLD — indices and FX stay pinned at the
  1.05 % venue minimum either way.
- **Decision:** no change — the guard is doing its job and the active
  list stays. No code change, no restart.

## 2026-09-07 (sixteenth run) — pinned versus ATR-bound instruments

- **Lever:** pair selection — section 64 showed the 2-ATR stop is inert
  on instruments the 1.05 % venue floor pins (indices, FX) and only
  bites on crypto, gold and the oils. Tested whether the book should
  drop the pinned group: signal expectancy per group against
  count-matched random entries, two disjoint samples, 2-ATR stop.
- **Measurement:** `scripts/pinned_groups.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  three live trend strategies, five instruments per group, three random
  draws per segment, run sequentially per sample.
- **Result (signals, pooled per group):**

  | sample | ATR-bound E[R] | vs random | t | pinned E[R] | vs random | t | ATR-bound − pinned | t |
  |---|---:|---:|---:|---:|---:|---:|---:|---:|
  | last 365 d | +0.0435 | +0.0915 | +4.04 | +0.0070 | +0.0206 | +1.29 | +0.0365 | +1.51 |
  | prior 730 d | -0.0275 | +0.0117 | +0.77 | +0.0099 | +0.0065 | +0.59 | -0.0374 | -2.28 |

  The group difference flips sign, and the ATR-bound group's strong
  recent reading against random collapses to t = 0.77 on the older
  sample — the oils turn significantly negative there. The pinned group
  is flat on both. No basis for dropping either group.
- **Decision:** not built in — dead. No code change, no restart.
- **Preregistered for the next run:** GOLD is the one instrument that
  beats its random control on *both* samples (+0.152 R, t = 2.71;
  +0.100 R, t = 3.24), read after the fact as one of ten. It is
  currently in the active list for turtle_breakout only. Next run
  measures GOLD per strategy, with the live ADX router applied, on both
  samples; acceptance for pinning GOLD to the other two trend strategies
  is t > 2.0 against random per strategy on both samples after the
  router.

## 2026-09-07 (seventeenth run) — GOLD per strategy, router applied

- **Lever:** pair selection — the preregistered follow-up to run 16:
  pin GOLD to the trend strategies that do not rank it (keltner_breakout
  is the only one; donchian and turtle already carry GOLD in the active
  list) if each strategy beats count-matched random entries at t > 2.0
  on both samples *after* the live ADX router.
- **Measurement:** `scripts/gold_strategies.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, GOLD only, four strategies, router applied to the
  signals and the control drawn from router-passing bars, five draws.
- **Result (router applied, vs router-passing random):**

  | strategy | last 365 d: n / E[R] / vs random / t | prior 730 d: n / E[R] / vs random / t |
  |---|---|---|
  | donchian_breakout | 79 / +0.086 / +0.101 / +0.75 | 143 / +0.097 / +0.108 / +1.49 |
  | turtle_breakout | 66 / +0.184 / +0.228 / +1.55 | 129 / +0.080 / +0.095 / +1.24 |
  | keltner_breakout | 62 / +0.176 / +0.148 / +1.02 | 132 / +0.038 / +0.073 / +0.95 |
  | momentum | 10 / -0.109 / -0.099 / -0.25 | 17 / -0.078 / -0.039 / -0.15 |

  Every trend strategy is positive against its control on both samples
  and none is significant on either; the section-65 pooled t = 2.7 / 3.2
  was three strategies added together, and the router removes six
  signals in ten. Pre-router, turtle reaches t = 2.66 on the older
  sample and t = 1.00 on the recent one — the familiar shape.
- **Decision:** not built in — the preregistered bar was not met. GOLD
  stays active for donchian and turtle as ranked; no pin for keltner. No
  code change, no restart.

## 2026-09-07 (eighteenth run) — ADX router at the 2-ATR stop

- **Lever:** regime filter — the router re-measured at the
  configuration now live (46b and 49e settled it at the 1-ATR stop).
  Passed vs rejected vs count-matched random, three live trend
  strategies, ten instruments, two disjoint samples. By the standing
  rule the router only comes off on an independent *forward* reading
  below t = -2.0, so this run could at most confirm.
- **Measurement:** `scripts/router_at_stop.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, live `regime.gate` applied per signal, three random
  draws per segment, run sequentially per sample.
- **Result (pooled):**

  | sample | n passed | E[R] passed | n rejected | E[R] rejected | passed − rejected | t | all − random | t | passed − random | t |
  |---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
  | last 365 d | 2,155 | -0.0037 | 4,061 | +0.0334 | -0.0371 | -1.47 | +0.0396 | +2.67 | +0.0075 | +0.35 |
  | prior 730 d | 4,424 | +0.0095 | 8,347 | -0.0238 | +0.0333 | +1.94 | +0.0100 | +1.00 | +0.0317 | +2.13 |

  The router's effect flips sign between the samples for the third time
  (46, 46b, now this), and every cell that clears t = 2 on one sample
  reads below 1 on the other. Signals as a whole beat random on the
  recent year (t = 2.67) and not on the older two (t = 1.00) — the same
  reversal section 45 recorded for the 1-ATR stop.
- **Decision:** not changed — the router stays on, per the standing
  rule and because nothing here points anywhere twice. No code change,
  no restart.

## 2026-09-07 (nineteenth run) — cost ceiling at the 2-ATR stop

- **Lever:** cost filter — the 10 % cost-per-risk ceiling re-examined at
  the configuration now live, where the wider stop halves every
  instrument's cost share. Trades bucketed by their own round-trip cost
  in R, and the ceiling evaluated at 10 / 5 / 3 %, on both samples.
- **Measurement:** `scripts/cost_buckets.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, three live trend strategies, the 10 unblocked
  instruments, run sequentially per sample.
- **Result:**

  | bucket | last 365 d: share / E[R] net / gross | prior 730 d: share / E[R] net / gross |
  |---|---|---|
  | < 2 % | 56 % / +0.0250 / +0.0324 | 50 % / +0.0119 / +0.0188 |
  | 2–5 % | 32 % / +0.0394 / +0.0752 | 36 % / **-0.0561** / -0.0178 |
  | 5–10 % | 13 % / +0.0119 / +0.0708 | 13 % / +0.0163 / +0.0754 |

  | ceiling | last 365 d: n / E[R] / Σ R | prior 730 d: n / E[R] / Σ R |
  |---:|---|---|
  | **10 % (live)** | 5,428 / +0.0279 / +151.5 | 11,161 / -0.0122 / -135.7 |
  | 5 % | 4,752 / +0.0302 / +143.5 | 9,668 / -0.0166 / -160.1 |
  | 3 % | 3,493 / +0.0207 / +72.5 | 6,214 / -0.0041 / -25.6 |

  No monotonic cost effect: the most expensive bucket is not the worst
  on either sample, and the middle bucket swings from the best to
  significantly negative between them. Lowering the ceiling reduces the
  yearly R sum on the recent year at every level and helps on the older
  sample only at 3 %, where it removes 44 % of the trades. Section 11's
  finding — the cost band was an artefact of which instruments were
  expensive — holds at the wider stop; with all ten instruments under
  10 %, the ceiling is a backstop, not a lever.
- **Decision:** not changed — 10 % stays. No code change, no restart.

## 2026-09-07 (twentieth run) — ADX regime exit

- **Lever:** exit logic — close an open trade at the first bar whose
  ADX(14) drops below a threshold (15 / 20 / 25), on the theory that a
  trend trade should not sit through a range; measured against the live
  fixed 24-bar leash at the 2-ATR stop, three live trend strategies,
  ten instruments, two disjoint samples.
- **Measurement:** `scripts/regime_exit.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, run sequentially per sample.
- **Result (pooled, diff vs live):**

  | ADX exit below | last 365 d: n / E[R] / diff / t / early exits | prior 730 d: n / E[R] / diff / t / early exits |
  |---:|---|---|
  | **none (live)** | 5,428 / +0.0280 / — | 11,161 / -0.0122 / — |
  | 15 | 5,916 / +0.0084 / -0.0196 / -1.13 / 18 % | 12,227 / -0.0095 / +0.0026 / +0.22 / 20 % |
  | 20 | 6,977 / -0.0024 / -0.0304 / -1.93 / 47 % | 14,708 / -0.0180 / -0.0059 / -0.55 / 49 % |
  | 25 | 8,165 / -0.0155 / -0.0435 / -2.96 / 68 % | 17,190 / -0.0179 / -0.0058 / -0.58 / 70 % |

  Worse at every threshold on the recent year, monotonically, and
  significantly so at 25; flat on the older sample. The exit frees
  capital for more trades (n rises) but each early close books a
  random-walk residual plus the spread already paid, which is the same
  arithmetic that killed the break-even stop (52).
- **Decision:** not built in — dead. No code change, no restart.

## 2026-09-07 (twenty-first run) — the remaining active instruments — AU200 BLOCKED

- **Lever:** pair selection — the 17 instruments in the active list that
  no measurement of this session had covered (FR40, UK100, EU50, US100,
  USDCHF, AUDNZD, EURAUD, HK50, SILVER, AU200, GBPUSD, NZDUSD, J225,
  COPPER, GBPCAD, AUDJPY, CHFJPY), each against count-matched random
  entries and, as live trades them, on the router-passed subset. Both
  disjoint samples, 2-ATR stop, three live trend strategies.
  Preregistered block rule: significantly negative on both samples.
- **Measurement:** `scripts/other_instruments.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, live `regime.gate` for the passed subset, run
  sequentially per sample.
- **Result:**

  | | last 365 d | prior 730 d |
  |---|---|---|
  | group, all signals | n 7,861 / **-0.0272 R** / t **-3.19** | n 15,596 / **-0.0290 R** / t **-4.91** |
  | group vs random | -0.0185 / t -1.88 | -0.0103 / t -1.52 |
  | group, router-passed (live path) | n 2,977 / **-0.0885 R** / t **-6.43** | n 5,896 / -0.0074 R / t -0.75 |
  | core ten, router-passed (run 18) | -0.0037 R | +0.0095 R |
  | **AU200, router-passed** | n 146 / **-0.301 R** / t **-5.48** | n 263 / **-0.114 R** / t **-2.58** |
  | AU200, random entries | -0.079 | -0.041 |
  | SILVER, router-passed | -0.133 / t -1.84 | -0.141 / t -2.83 |

  The group as a whole loses on both samples before the router and on
  the live path loses heavily on the recent year but reads flat on the
  older one, so a group block does not meet the two-sample bar. One
  instrument does: **AU200** is significantly negative on the live path
  on both samples, its random control loses too (the instrument, not
  the signal — 98 % of its stops sit on the venue floor), and all five
  live trades since July lost (-4.25 USD). SILVER misses the bar on the
  recent year (t = -1.84) and is recorded as a watch, not blocked.
  Per-instrument "worse than random at t < -2 on both" is met by none.
- **Decision:** AU200 blocked for entries via a new
  `EXPECTANCY_BLOCKED_PAIRS` in `trading_blocks.py`, kept apart from
  the cost audit's list; `BLOCKED_PAIRS` is their union and is what the
  selector and both entry guards consult. Existing AU200 pins fall out
  of the active file at the next selector run and are refused at entry
  immediately. Tests cover both lists and both guards. Hurz restarted.
  Expected effect is small (AU200 was 5 of 266 live trades) and
  one-directional: it removes a component that loses in every reading.

## 2026-09-08 — ADX trend threshold at the 2-ATR stop

- **Lever:** regime filter — the router's trend floor (30 for the 1h
  core) at 20 / 25 / 35, measured as router-passed expectancy per
  threshold against random entries drawn from the same passing bars,
  three live trend strategies, ten instruments, two disjoint samples.
- **Measurement:** `scripts/adx_threshold.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, live `regime.gate` with the threshold set per run, run
  sequentially per sample.
- **Result (pooled, diff vs live 30):**

  | ADX ≥ | last 365 d: n / E[R] / vs random / diff vs 30 / t | prior 730 d: n / E[R] / vs random / diff vs 30 / t |
  |---:|---|---|
  | 20 | 4,205 / -0.0096 / +0.0101 / -0.0060 / -0.24 | 8,545 / -0.0195 / -0.0024 / -0.0272 / -1.58 |
  | 25 | 3,135 / +0.0061 / +0.0295 / +0.0097 / +0.37 | 6,353 / -0.0143 / +0.0118 / -0.0219 / -1.21 |
  | **30 (live)** | 2,159 / -0.0036 / -0.0187 / — | 4,437 / +0.0077 / +0.0339 / — |
  | 35 | 1,451 / -0.0467 / +0.0026 / -0.0431 / -1.34 | 2,978 / +0.0013 / +0.0381 / -0.0064 / -0.29 |

  25 is mildly better on the recent year and worse on the older one; 20
  and 35 are worse or flat on both. The live floor is the best value on
  the older sample and within noise of the best on the recent one.
- **Decision:** not changed — 30 stays. No code change, no restart.
- **Forward note:** the bot reconnected at midnight and closed three
  stale positions opened before the stop change (US30 +1.92, US500
  +0.89, US100 -0.58 USD); those are not attributable to either lever
  built in on 2026-09-07. First entries at the 2-ATR stop are still
  pending.

## 2026-09-08 (second run) — the 4h strategy variants

- **Lever:** strategy parameter / pair selection — `donchian_breakout_4h`
  and `turtle_breakout_4h` sit in the live rotation through pins
  (SILVER, HK50) and never appeared in a measurement of this session.
  Measured on 4h bars at the 2-ATR stop against count-matched random
  entries and with the router-passed subset, ten instruments, two
  disjoint samples. Preregistered: disable the 4h names if significantly
  worse than random on both samples; promote nothing on one.
- **Measurement:** `scripts/h4_variants.py` — cost-charging walk-forward
  simulator, capital_com, 4h, 3 segments, hold 24 bars, RR 1.5, stop
  2.0 ATR, three random draws per segment, run sequentially per sample.
- **Result (pooled over both strategies):**

  | sample | n | E[R] all | vs random | t | n passed | E[R] passed | vs random | t |
  |---|---:|---:|---:|---:|---:|---:|---:|---:|
  | last 365 d | 981 | -0.0401 | -0.0430 | -1.09 | 380 | -0.0181 | -0.0211 | -0.35 |
  | prior 730 d | 2,060 | -0.0065 | +0.0243 | +0.89 | 878 | +0.0048 | +0.0357 | +0.91 |

  Random on both samples, in both directions, before and after the
  router. Nothing to disable, nothing to promote.
- **Decision:** not changed. No code change, no restart.

## 2026-09-08 (third run) — the momentum strategy

- **Lever:** strategy / pair selection — `momentum` is ranked first in
  the active list on four indices yet produced two live trades since
  July. Measured against count-matched random entries and with the
  router-passed subset on twelve instruments (the ten core plus US100
  and EU50 where it is ranked), 1h, 2-ATR stop, two disjoint samples.
- **Measurement:** `scripts/momentum_check.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, three random draws per segment, run sequentially per
  sample.
- **Result:**

  | sample | n | E[R] all | vs random | t | n passed | E[R] passed | vs random | t |
  |---|---:|---:|---:|---:|---:|---:|---:|---:|
  | last 365 d | 1,042 | +0.0341 | +0.0514 | +1.41 | 148 | +0.0255 | +0.0428 | +0.53 |
  | prior 730 d | 1,941 | -0.0279 | +0.0271 | +1.06 | 298 | +0.0174 | +0.0725 | +1.32 |

  Positive against its control on both samples, before and after the
  router, never above t = 1.4 — the GOLD shape (66) again. The router
  passes one momentum signal in seven, which is why the live book
  barely sees the strategy; loosening the router for it would be run
  18 and 22 over again, and those were flat.
- **Decision:** not changed. No code change, no restart.

## 2026-09-08 (fourth run) — minimum-size skips on the remaining instruments

- **Lever:** position sizing — after the first live intent under the
  new rules (HK50, donchian_breakout, 1.19 % stop = 2 ATR) was refused
  by the minimum-size guard (0.0099 against a 0.01 minimum; the 1.05 %
  floor would have given 0.0112), the 16 remaining active instruments
  were replayed with the live sizing function at the 1-ATR and 2-ATR
  stop to see how much throughput the wider stop removes there.
- **Measurement:** `scripts/min_size_skips.py` with the remaining
  instruments, 365 days, three live trend strategies, live broker
  constraints. Caveat: the script sizes in the instrument's quote
  currency without FX conversion, so its skip figures for JPY- and
  HKD-quoted instruments (J225, AUDJPY, CHFJPY, HK50) are not the live
  figures — live converts and did trade AUDJPY and HK50 in July/August.
- **Result (USD-quoted instruments, reliable):**

  | instrument | skips at 1 ATR | skips at 2 ATR | E[R] at 2 ATR |
  |---|---:|---:|---:|
  | SILVER | 1.5 % | 5.8 % | -0.006 |
  | US100, COPPER, USD-quoted FX, EU indices | 0 % | 0 % | -0.003 to -0.121 |

  Live evidence for HKD: HK50 now skips at 2 ATR because its
  ATR-bound stop (1.19 %) exceeds the venue floor and the halved size
  falls under the 0.01 minimum; at 1 ATR it was pinned to the floor and
  traded. HK50 measured -0.0625 / -0.0353 R on the two samples (70), so
  the guard is removing a losing instrument. The group as a whole loses
  -352 USD per backtest year at the 2-ATR stop on the recent sample,
  consistent with 70.
- **Decision:** not changed — the guard is doing what section 64
  described, on instruments the book is better off without. No code
  change, no restart. Follow-up recorded: the sizing replay script
  should convert quote currencies before its JPY/HKD rows are read as
  live behaviour.

## 2026-09-08 (fifth run) — Friday-afternoon entries and weekend gaps

- **Lever:** regime filter with a cost mechanism — entries on Friday
  from 12:00 UTC are held across the weekend and exposed to gap risk
  the simulator had never charged (a stop hit by a gap booked exactly
  -1 R). The simulator now books the open when a bar opens beyond the
  stop; Friday-afternoon entries against the rest, three live trend
  strategies, ten instruments, 2-ATR stop, two disjoint samples.
  Preregistered: block Friday-afternoon entries if worse than the rest
  at t < -2.0 on both samples.
- **Measurement:** `scripts/weekend_entries.py` — cost-charging,
  gap-aware walk-forward simulator, capital_com, 1h, 3 segments, hold
  24, RR 1.5, run sequentially per sample.
- **Result (pooled):**

  | sample | n Friday | E[R] Friday | gapped | n rest | E[R] rest | diff | t | gapped stop mean R |
  |---|---:|---:|---:|---:|---:|---:|---:|---:|
  | last 365 d | 505 | +0.0067 | 4.8 % | 4,922 | +0.0254 | -0.0187 | -0.41 | -1.75 |
  | prior 730 d | 1,107 | -0.0479 | 1.9 % | 10,063 | -0.0111 | -0.0369 | -1.28 | -1.78 |

  Same sign twice, far from threshold. A gapped stop really costs
  1.75 R rather than 1, but only one Friday trade in twenty to fifty
  gaps, so the mechanism is real and small. The oils are the
  consistent losers on Fridays (-0.30 / -0.22 R against the rest, t =
  -1.5 / -1.2) — recorded as a watch, not acted on.
- **Decision:** not built in — dead. No code change, no restart. The
  gap-aware booking is kept in this script only; the shared simulator
  in `spot_backtest.py` still books a clean -1 R on gaps, which the
  figures above show understates losses by about 0.75 R on 0.1–0.2 %
  of all trades.

## 2026-09-08 (sixth run) — gap-aware stop booking in the shared simulator — BUILT IN

- **Lever:** cost side of the ranking — the shared backtest simulator
  (`scripts/spot_backtest.py`), from which the nightly selector ranks
  and persists every combo, booked a stop hit by a gap as a fill at the
  stop. Run 5 measured the real fill at 1.75 R on average, on 10–15 %
  of Friday oil trades and 0.1–0.2 % of all trades.
- **Measurement:** `scripts/weekend_entries.py` (run 5): gapped stops
  book -1.75 / -1.78 R on the two samples against the -1 R minus cost
  the shared simulator recorded; the pooled effect is about -0.005 R on
  the recent year and -0.002 R on the older one, concentrated on
  OIL_CRUDE (9.8 % of Friday entries gapped) and OIL_BRENT (15.2 %).
- **Decision:** built in. `_simulate_trades` now books the open when a
  bar opens beyond the stop, matching a stop order's actual fill; a
  test covers the gapped and the in-bar case. This changes no live
  behaviour, so Hurz was not restarted; the nightly selector run
  inherits it and will rank the oils on their real stop cost. It is a
  removal of a flattering measurement, the same class as 9, 20, 27 and
  49c, not a profit lever.

## 2026-09-08 (seventh run) — quote-currency conversion in sizing — BUILT IN

- **Lever:** position sizing — the 3 USD risk budget and the 250 USD
  notional cap were applied in the instrument's quote currency. The
  broker's market details put the active instruments in eight
  currencies (DE40/FR40/EU50 EUR, UK100 GBP, HK50 HKD, J225/AUDJPY/CHFJPY
  JPY, GBPCAD CAD, AUDNZD NZD, USDCHF CHF, AU200 AUD), the account itself
  is EUR. Measured on the journal: size × stop distance is 1.9–2.7 quote
  units on every instrument, so the real USD risk ran from 3 GBP =
  4.06 USD on UK100 (35 % over the cap) down to 3 JPY = 0.02 USD on
  AUDJPY, and JPY/HKD signals were refused at the broker minimum for
  the wrong reason.
- **Measurement:** broker market details (currency per instrument),
  journal audit of size × stop per instrument, and an end-to-end replay
  of the new sizing against live quotes: UK100 3.08 → 1.54 USD (coarse
  0.01 increment), DE40 2.86 → 2.54, AUDJPY skip → 300 units at 2.27
  USD, HK50 skip → 0.07 at 2.37 USD, BTCUSD unchanged at 2.57 USD.
- **Decision:** built in. `prepare_order` now carries `usd_per_quote`
  from the venue's own FX mid (EURUSD, GBPUSD, AUDUSD, NZDUSD direct;
  USDJPY, USDCAD, USDCHF, USDHKD inverted); the live loop divides both
  budgets by it and journals planned and fill risk in USD; an unknown
  rate is a skip, not a trade. Tests cover the rate table and the
  default. **Risk statement:** this restores the cap where it was
  exceeded (GBP, EUR, CHF instruments) and raises exposure from
  near zero to the intended budget on JPY and HKD instruments, all
  inside the 3 USD limit; those instruments measured as random (70),
  so the expected gain effect is neutral and the change is about the
  guard being true, not about profit. The backtest's dollar figures
  keep the same defect (R-multiples are unaffected) and are recorded
  as follow-up. Hurz restarted.

## 2026-09-08 (eighth run) — quote-currency conversion in the backtest — BUILT IN

- **Lever:** pair selection through the nightly ranking — the shared
  backtest sized in the quote currency exactly as live had (run 7), so
  the JPY instruments never produced a single sizeable trade and the
  selector could not rank them: the persisted `donchian_breakout` file
  (2026-09-06) carries n = 0 for J225, AUDJPY and CHFJPY and 12 trades
  in 365 days for HK50, against 125 for BTCUSD.
- **Measurement:** the same six instruments re-run through
  `scripts/spot_backtest.py` after the change (120 days, `--no-persist`):
  J225 19 trades, AUDJPY 16, CHFJPY 16, HK50 21, DE40 18, UK100 12,
  BTCUSD 27 — every instrument now sizes at the USD budget; USD figures
  for EUR/GBP instruments scale by the rate (UK100 -34.20 USD persisted
  was -25.25 GBP).
- **Decision:** built in. The backtest fetches `usd_per_quote` through
  `prepare_order`, divides both budgets by it and converts planned
  risk, notional and realised PnL of every outcome back to USD; an
  unknown rate skips the instrument as live does. No live behaviour
  changed, so no restart; the next nightly selector run ranks the JPY
  and HKD instruments for the first time and prices the European
  indices in dollars. Whether they *should* be ranked is section 70's
  question, which their expectancy (random, 70) will now answer through
  the selector's own gates rather than through a sizing accident.

## 2026-09-08 (ninth run) — realised PnL booked in USD — BUILT IN

- **Lever:** accounting behind every gain figure — with risk now sized
  in USD (run 7), the journal's realised PnL was still the bar-walk's
  quote-currency figure: a full HK50 stop would have booked 21 HKD as
  -21 USD against 2.7 USD of risk. Measured on the journal: for full
  stop-outs |realised PnL| / fill risk = 1.00–1.03 on every instrument
  (n = 8 with a fill-risk column), which proves the PnL is the bot's own
  quote-unit arithmetic, not a broker figure in account currency (the
  account is EUR). For USD instruments the journal was therefore right;
  for EUR/GBP/HKD/JPY instruments it summed foreign currency as dollars.
- **Decision:** built in. `_resolve_closed_trade` now converts the
  quote-currency PnL with the venue rate at close; venues without rates
  book one-to-one; an unknown rate leaves the PnL unknown rather than
  wrong. Tests cover the three cases. Hurz restarted with the HK50
  position open (reconcile adopts it). Expected gain effect: none in
  expectation — it makes the number the goal is measured by true for
  the non-USD half of the book.

## 2026-09-08 (tenth run) — the legacy journal's currency mix

- **Lever:** accounting behind the gain figures — how much of the
  historical PnL sums the dashboard shows is foreign currency booked as
  USD (runs 7–9 fixed the path forward; this measures the past).
  Closed trades before the fix, split by the instrument's quote
  currency and converted at today's venue rates.
- **Measurement (read-only, journal):**

  | window | closed trades | booked "USD" | foreign rows | corrected USD | delta |
  |---|---:|---:|---:|---:|---:|
  | all-time | 523 | -241.66 | 62 | -250.82 | -9.17 |
  | since 2026-07-10 | 268 | -81.95 | 35 | -86.60 | -4.65 |
  | forward since 2026-08-24 | 29 | -8.11 | 6 | -10.24 | -2.13 |

  The foreign rows are 12 % of trades; their booked +11.36 in the
  active window is +6.71 USD in truth (HKD 2.97 → 0.38, NZD 3.78 →
  2.22, EUR 2.39 → 2.78). Direction as always: the flattering one.
- **Decision:** no change. Production data are read-only by project
  rule and a dashboard-side correction would need historical rates the
  journal never stored; the distortion is under 4 % of every total and
  stops accruing with run 9. The figures above are the reference for
  reading pre-2026-09-08 sums. Reported daily-gain tables from here on
  use the booked journal values and carry this footnote.

## 2026-09-08 (eleventh run) — the 250 USD notional cap

- **Lever:** position sizing — the notional cap binds on instruments
  whose stop the venue floor pins at 1.05 % (a 3 USD risk there needs
  286 USD of notional), so planned risk on US500, US30, AUDUSD and GOLD
  sits at 2.2–2.5 USD instead of 3. Measured with the live sizing
  function at caps of 250 / 300 / 400 USD, ten core instruments, 2-ATR
  stop, 365 days.
- **Measurement:** `scripts/notional_cap_sweep.py`.

  | cap | n | planned risk, kept trades | E[R] | USD per trade | USD per backtest year |
  |---:|---:|---:|---:|---:|---:|
  | **250 (live)** | 5,426 | 2.59 | +0.0286 | +0.079 | +420.7 |
  | 300 | 5,426 | 2.82 | +0.0286 | +0.087 | +463.3 |
  | 400 | 5,426 | 2.82 | +0.0286 | +0.087 | +463.3 |

  The cap removes 0.23 USD of target risk per trade and nothing else:
  E[R] is identical by construction, the dollar line scales with the
  risk taken, and 400 adds nothing over 300 because the broker's size
  increments bind next. Raising it is a 20 % loosening of a hard
  exposure limit in exchange for a proportional change in dollar
  outcome whose sign is the book's expectancy — which every section of
  this document puts at zero.
- **Decision:** not changed — the cap stays at 250 USD. Buying dollar
  gain through a looser exposure limit is exactly what the project rules
  forbid, and at zero edge it buys variance. No code change, no restart.

## 2026-09-08 (twelfth run) — correlation-cluster coverage — BUILT IN

- **Lever:** risk guard — the same-direction cap of 3 per correlation
  cluster only counts mapped instruments. Seven of the 27 active
  instruments were unmapped (EU50, COPPER, AUDJPY, CHFJPY, AUDNZD,
  EURAUD, GBPCAD), and at the time of measurement three same-direction
  shorts were open on HK50, CHFJPY and AUDJPY with two of them outside
  every cluster. Preregistered: map an instrument to a cluster whose
  members it matches at a median |corr| of 0.5 or more on a year of
  hourly log returns; leave the rest as singletons.
- **Measurement:** `scripts/cluster_correlations.py` — 365 days of
  1h closes for 20 instruments, pairwise return correlations.

  | instrument | indices | usd_fx | metals | note |
  |---|---:|---:|---:|---|
  | EU50 | **0.76** | 0.34 | 0.38 | 0.93 with DE40 |
  | COPPER | 0.40 | 0.34 | **0.58** | 0.56 GOLD, 0.59 SILVER |
  | AUDJPY | 0.35 | 0.36 | 0.33 | 0.60 with CHFJPY, 0.64 AUDUSD, -0.71 EURAUD |
  | CHFJPY | 0.09 | 0.21 | 0.16 | 0.60 with AUDJPY, 0.53 USDJPY |
  | AUDNZD | 0.12 | 0.09 | 0.09 | singleton |
  | EURAUD | 0.38 | 0.15 | 0.34 | singleton (inverse of AUD) |
  | GBPCAD | 0.22 | 0.39 | 0.18 | singleton |

- **Decision:** built in. EU50 → indices, COPPER → metals, AUDJPY and
  CHFJPY → a new `jpy_crosses` cluster (measured members only; the
  other yen crosses in the default universe are not active and were not
  measured). AUDNZD, EURAUD and GBPCAD stay uncapped by measurement. A
  test pins the mapping and asserts that every active instrument is
  either mapped or one of those three. Hurz restarted. Expected gain
  effect: none; this closes a gap in a risk control the project rules
  require to be effective, and with three same-direction positions
  already open it was not hypothetical.

## 2026-09-08 (thirteenth run) — duplicate exposure across strategies

- **Lever:** risk guard — whether the same breakout can be opened
  several times on one instrument by donchian, turtle and keltner
  firing on neighbouring bars. The live loop refuses any entry on an
  instrument that already has an open position, whatever the strategy
  (`_has_open_position`), so the guard exists; measured what it absorbs.
- **Measurement:** `scripts/strategy_overlap.py` — signals of the three
  live trend strategies on the ten core instruments over 365 days,
  merged in time with a 24-bar hold, counting signals that arrive while
  a position is open.

  | instruments | signals | arriving while a position is open | same direction as the open trade |
  |---|---:|---:|---:|
  | 10 core, pooled | 11,325 | 9,441 (83.4 %) | 80 % of those |

  Five signals in six would pyramid or flip an existing position; the
  guard turns the three strategies into one bet per instrument, which
  is what the cluster cap (82) does across instruments. One consequence
  for reading this log: every per-strategy sweep here simulated each
  strategy with its own open-trade state, so summed backtest frequency
  is about six times what the live book can open — expectancy per trade
  is unaffected, throughput comparisons are not additive.
- **Decision:** not changed — the guard is in place and doing the
  work. No code change, no restart.

## 2026-09-08 (fourteenth run) — the daily-loss limit

- **Lever:** risk guard — the 6 R daily-loss limit (entries stop for
  the rest of the UTC day once closed R reaches -6). Replayed at 3 / 6 /
  9 R on both samples, first on the pooled three-strategy book and then,
  because run 13 showed that book is six times live throughput, with
  one position per instrument as live trades; live journal since July
  as the forward reading.
- **Measurement:** `scripts/daily_loss_replay.py`.

  | book | limit | last 365 d: blocked n / E[R] blocked / delta | prior 730 d: blocked n / E[R] blocked / delta |
  |---|---:|---|---|
  | three strategies stacked | 3 R | 1,813 / -0.050 / +90 R | 3,771 / -0.077 / +290 R |
  | three strategies stacked | 6 R | 952 / -0.014 / +14 R | 1,844 / -0.083 / +153 R |
  | **one position per instrument** | 3 R | 364 / -0.032 / +12 R | 723 / **+0.008** / -6 R |
  | **one position per instrument** | **6 R (live)** | 53 / +0.007 / -0.4 R | 63 / -0.034 / +2 R |
  | one position per instrument | 9 R | 18 / +0.656 / -12 R | 14 / -0.095 / +1 R |
  | live journal since 2026-07-10 | 3 R | 30 trades after the limit, -0.011 R | — |
  | live journal since 2026-07-10 | 6 R | 9 trades after the limit, -0.252 R (one day) | — |

  On the stacked book a bad day predicted a worse rest-of-day at t ≈ 2
  to 5 on both samples — and that was three strategies booking the same
  instrument's loss three times, so the "day" was correlated with itself.
  At live throughput the 3 R reading flips sign between samples and the
  6 R limit touches nine to thirteen days a year at zero expectancy.
- **Decision:** not changed — 6 R stays; it is a tail guard, not a
  lever, and tightening it would act on an artefact. No code change, no
  restart. Lesson recorded: any book-level (as opposed to per-trade)
  measurement in this log must use the merged one-position-per-
  instrument timeline, or it will manufacture day-level autocorrelation.

## 2026-09-08 (fifteenth run) — the concurrent-position cap

- **Lever:** risk guard — the cap of 8 simultaneous positions,
  replayed at 4 / 6 / 8 / 12 on the merged one-position-per-instrument
  timeline (run 14's lesson), ten core instruments, three live trend
  strategies, 2-ATR stop, both samples. Peak concurrency without a cap
  is 10 on this book; the live universe is 27 instruments, so the cap
  binds more often live than here.
- **Measurement:** `scripts/concurrent_cap_replay.py`.

  | cap | last 365 d: blocked / E[R] blocked / E[R] kept / delta | prior 730 d: blocked / E[R] blocked / E[R] kept / delta |
  |---:|---|---|
  | 4 | 44.9 % / -0.015 / +0.075 / +15.7 R | 44.7 % / -0.015 / -0.009 / +31.0 R |
  | 6 | 22.7 % / -0.073 / +0.066 / +38.1 R | 23.0 % / **+0.011** / -0.018 / -11.5 R |
  | **8 (live)** | 6.4 % / -0.026 / +0.039 / +3.9 R | 6.5 % / -0.034 / -0.010 / +10.6 R |
  | 12 | 0 % | 0 % |

  At 8 the refused entries (149 and 308 trades) carry a slightly
  negative expectancy on both samples, at t of -0.3 and -0.6 — the cap
  costs nothing measurable and keeps the book's exposure bounded. A
  tighter cap of 6 looks strong on the recent year and reverses on the
  older one; 4 removes almost half the trades for a gain that is inside
  the noise of what it removes.
- **Decision:** not changed — 8 stays. No code change, no restart.

## 2026-09-08 (sixteenth run) — stop-out re-entry cooldown — BUILT IN

- **Lever:** regime filter after a stop-out — the project's risk rules
  foresee a cooldown before re-entering an instrument that just stopped
  out, and none existed in the code (only the stale-exit retry backoff).
  Measured on the merged one-position-per-instrument timeline: entries
  within 6 / 24 / 72 h after a stop-out (R ≤ -0.9) on the same
  instrument against all other entries, ten core instruments, three
  live trend strategies, 2-ATR stop, both samples; live journal since
  July as the forward reading.
- **Measurement:** `scripts/reentry_after_stop.py`.

  | window after a stop-out | last 365 d: n / E[R] / vs rest / t | prior 730 d: n / E[R] / vs rest / t |
  |---|---|---|
  | **6 h** | 301 / -0.083 / **-0.135** / **-2.11** | 698 / -0.066 / **-0.064** / -1.50 |
  | 24 h | 612 / +0.014 / -0.028 / -0.58 | 1,344 / -0.053 / -0.058 / -1.77 |
  | 72 h | 719 / +0.048 / +0.019 / +0.40 | 1,531 / -0.052 / -0.060 / -1.91 |
  | live journal, 6 h | 32 / +0.008 / — | — |

  The immediate re-entry is the one window that is negative on both
  samples: the instrument that just stopped out re-breaks the same
  level and fails again. Longer windows dilute it on the recent year.
  It clears t = 2 on one sample and reads t = 1.5 on the other, so it
  does not meet the bar this log applies to signal claims — but it is
  not a signal claim, it is the risk rule the project already specifies,
  measured to point the right way twice.
- **Decision:** built in at 6 hours as `stop_out_cooldown` in
  `risk_guard.py`, read from the journal's last `loss` exit per
  instrument, fail-closed when the journal cannot be read,
  `HURZ_STOP_OUT_COOLDOWN_HOURS` ≤ 0 disables it. Checked against the
  live journal before restart (no instrument blocked, no error). Tests
  cover block, expiry, no history, unreadable journal and disable. It
  removes about one entry in eight on this book at -0.08 R; expected
  effect +0.06 to +0.14 R on those entries, zero on the rest. Hurz
  restarted.

## 2026-09-08 (seventeenth run) — the rolling-24h entry cap

- **Lever:** risk guard — the circuit breaker of 100 entries per
  rolling 24 h, replayed at 4 / 6 / 8 / 12 / 20 on the merged
  one-position-per-instrument timeline, ten core instruments, three
  live trend strategies, 2-ATR stop, both samples.
- **Measurement:** `scripts/entry_cap_replay.py`.

  | cap / 24 h | last 365 d: blocked / E[R] blocked / E[R] kept / delta | prior 730 d: blocked / E[R] blocked / E[R] kept / delta |
  |---:|---|---|
  | 4 | 54 % / -0.001 / +0.077 / +1.0 R | 54 % / -0.021 / -0.001 / +52.7 R |
  | 6 | 37 % / -0.030 / +0.073 / +25.7 R | 36 % / -0.017 / -0.008 / +29.3 R |
  | 8 | 22 % / -0.025 / +0.052 / +12.7 R | 21 % / -0.005 / -0.013 / +4.5 R |
  | 12 | 3 % / -0.005 / +0.036 / +0.4 R | 2 % / -0.031 / -0.011 / +3.4 R |
  | 20 and **100 (live)** | 0 % | 0 % |

  The live value never binds — the book never issues twenty entries in
  a day, let alone a hundred — so it is a circuit breaker, which is
  what it was written as. Tighter caps read positive on both samples,
  but the refused entries are not significantly worse than zero (t of
  -1.0 and -0.8 at 6) and the gap to the kept ones collapses on the
  older sample; on the live 27-instrument book any such cap would also
  bind at a different point than on these ten.
- **Decision:** not changed — 100 stays. No code change, no restart.
  All eight entry-side guards (cluster, duplicate, daily loss,
  concurrent, notional, minimum size, cooldown, entry cap) are now
  measured at the live configuration.

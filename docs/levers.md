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

## 2026-09-08 (eighteenth run) — cost-filter stop widening

- **Lever:** cost filter — when a live quote puts the round-trip cost
  above 10 % of the stop distance, the loop widens the stop up to 2×
  to bring the share back to 10 % (the shared backtest skips such
  trades instead). Seen once live under the new rules: HK50 at 23:55
  UTC, 265 → 300 HKD, 11.3 % → 10.0 %. Measured whether widened trades
  differ from the rest.
- **Measurement:** `scripts/cost_widening_check.py` replays the
  widening rule on the 16 remaining instruments at the 2-ATR stop,
  both samples: **zero** trades exceed 10 % against the audited spread
  table (0 of 7,425 and 0 of 14,764). The rule only fires on live
  quotes wider than the audit — off-hours spreads — which no bar
  history carries. The live trail is a log line the session wrapper
  truncates at every restart, and the journal has no widening flag, so
  the one known case cannot be joined to an outcome yet.
- **Decision:** not changed. Two facts recorded for later: the backtest
  and the live loop diverge here (skip vs widen), and the divergence is
  invisible to every measurement in this log because it lives in the
  spread at the moment of the quote. A journal flag for widened stops
  would make it measurable; that is a schema change and is left as
  follow-up rather than done on one observation.

## 2026-09-08 (nineteenth run) — the bank-holiday guard

- **Lever:** exit logic / regime — the guard shuts the whole loop down on
  any DE, US, GB or CH bank holiday and lets open positions run through
  the pause, closing them as stale exits afterwards (yesterday: US30
  +1.92, US500 +0.89, US100 -0.58 USD after 81 h). Measured on the
  journal since July whether positions carried through long pauses come
  back worse than regular stale exits, and how many weekdays the guard
  removes.
- **Measurement (journal, read-only):**

  | exit class | n | mean R | t | Σ R |
  |---|---:|---:|---:|---:|
  | stale exit ≤ 30 h | 91 | +0.016 | +0.20 | +1.4 |
  | stale exit 30–60 h (weekend) | 7 | -0.077 | -1.90 | -0.5 |
  | stale exit > 60 h (holiday / long weekend) | 20 | **+0.118** | +0.98 | +2.4 |
  | stop or target | 150 | -0.196 | -1.96 | -29.5 |

  Carrying a position through a pause has not hurt: the 20 long holds
  came back at +0.118 R and the 31 shutdown closes since July sum to
  +5.32 USD. Calendar cost: 18 of 261 weekdays in 2026 are holidays in
  at least one of the four centres (6.9 %), and on each of them every
  instrument stands still, including crypto, Asian indices and the
  markets that are open.
- **Decision:** not changed. The guard costs about 7 % of weekdays at
  zero measured expectancy and protects against holiday liquidity on
  the instruments that are actually closed; narrowing it per instrument
  would be a throughput lever on a zero, which is not a lever. No code
  change, no restart.

## 2026-09-08 (twentieth run) — broker-side stop slippage

- **Lever:** cost filter — whether stop orders at the venue fill
  materially beyond the stored stop, which would be a cost neither the
  spread table nor the simulator charges. Measured on the journal: fill
  versus stored stop for every stop-out, fill versus target for every
  target, and fill versus signal price for every entry since July.
- **Measurement (journal, read-only, all Capital.com closes):**

  | leg | n | mean | median | note |
  |---|---:|---:|---:|---|
  | stop-out fill vs stop | 235 | **-0.027 R** | -0.003 R | 63 % beyond the stop, 35 % exactly on it, worst -1.77 R (the ATOMUSD weekend gap) |
  | target fill vs target | 116 | +0.004 R | — | targets fill where they sit |
  | **entry fill vs signal price** | 202 | **-0.128 R** | — | since 2026-07-10; negative = worse |

  Stops cost 0.027 R of slippage on average, almost all of it in a
  handful of gaps that section 76 now books; the broker executes stops
  where they are placed. The entry leg is another matter: the live fill
  sits 0.128 R behind the bar close the simulator enters at — half a
  spread plus the move between the signal bar's close and the order,
  which for a breakout is by construction in the wrong direction.
- **Decision:** stop and target placement unchanged — nothing to fix on
  the exit side. The entry gap is preregistered as the next lever:
  simulate entry at the next bar's open (what live approximates), a
  limit at the signal close valid for one bar, and for three bars,
  against the close entry, on the merged timeline and both samples.

## 2026-09-08 (twenty-first run) — entry timing

- **Lever:** cost side of the entry — run 20 measured the live fill
  0.128 R behind the signal close the simulator enters at. Tested on
  the merged one-position-per-instrument timeline, ten core
  instruments, three live trend strategies, 2-ATR stop, both samples:
  entry at the signal close (simulator), at the next bar's open (the
  live market order, one hour later at worst), a limit at the signal
  close valid one bar, and valid three bars.
- **Measurement:** `scripts/entry_timing.py`.

  | entry | last 365 d: fill / E[R] / vs close / t | prior 730 d: fill / E[R] / vs close / t |
  |---|---|---|
  | close (simulator) | 100 % / +0.0338 / — | 100 % / -0.0119 / — |
  | next open (≈ live market) | 98.3 % / +0.0192 / -0.0147 / -0.52 | 98.1 % / -0.0211 / -0.0092 / -0.47 |
  | limit at close, 1 bar | 97.7 % / +0.0129 / -0.0209 / -0.74 | 97.4 % / -0.0224 / -0.0105 / -0.54 |
  | limit at close, 3 bars | 97.8 % / +0.0154 / -0.0185 / -0.65 | 97.6 % / -0.0212 / -0.0093 / -0.48 |

  Two things follow. The hourly-bar move between close and next open
  costs 0.009–0.015 R, so the simulator's close entry flatters live by
  that much and the rest of the measured 0.128 R lives below the bar —
  spread and latency at the moment of the order, which an hourly
  history cannot see. And a limit at the close does not recover it: it
  fills 97 % of the time (hourly bars almost always revisit the prior
  close) and the fills are no better than a market order, because the
  entries that come back to the limit are the ones that had already
  stopped running.
- **Decision:** not changed — market entry stays. The 0.128 R live gap
  is real and is now the largest unmodelled cost in the book; the only
  measurable handle on it is sub-bar execution (order latency, quote
  timing), which needs tick data the venue does not serve. Recorded as
  the open cost item, not as a lever. No code change, no restart.

## 2026-09-08 (twenty-second run) — entries on the forming bar — BUILT IN

- **Lever:** signal timing, the largest live/backtest divergence found
  in this log. Run 20's latency check showed orders leaving a median
  31 minutes *before* the signal bar closed: the venue serves the
  current, still-forming candle as the last row and `evaluate_pair`
  treated it as "the just-closed bar", so live entered on intrabar
  channel crossings while every backtest in this document enters on the
  confirmed close. Measured the two entries on ten core instruments,
  three live trend strategies, 2-ATR stop, both samples.
- **Measurement:** `scripts/intrabar_vs_close.py`.

  | strategy | last 365 d: n close / E[R] close / n intrabar / E[R] intrabar / diff / t | prior 730 d: same |
  |---|---|---|
  | donchian_breakout | 2,106 / +0.033 / 2,576 / -0.006 / -0.038 / -1.35 | 4,308 / -0.025 / 5,130 / -0.013 / +0.012 / +0.63 |
  | turtle_breakout | 1,363 / +0.009 / 1,824 / -0.065 / -0.073 / -2.15 | 2,819 / -0.021 / 3,608 / -0.062 / -0.041 / -1.75 |
  | keltner_breakout | 1,957 / +0.035 / 2,295 / -0.014 / -0.049 / -1.66 | 4,042 / +0.005 / 4,637 / -0.041 / -0.046 / -2.26 |
  | **pooled** | 5,426 / +0.028 / 6,695 / **-0.025** / **-0.052** / **-2.98** | 11,169 / -0.013 / 13,375 / **-0.036** / **-0.023** / **-1.88** |
  | Σ R per sample | close +149 / intrabar **-164** | close -148 / intrabar **-478** |

  The forming bar produces a fifth more entries, and the extra ones are
  the crossings the close takes back: -0.052 R and -0.023 R against
  the confirmed entry, same sign on both samples, five of six strategy
  cells negative. It also explains most of run 20's 0.128 R fill gap —
  the "signal price" in the journal was a provisional close.
- **Decision:** built in. `_fetch_recent_bars` now drops trailing bars
  whose period has not ended (`_completed_bars`), so the loop sees the
  confirmed close within its next poll and enters on what the backtests
  measure. Four tests cover the drop, the keep, naive timestamps and 4h
  bars. Consequences named: about a fifth fewer entries; orders now
  leave up to a minute after the close instead of half an hour before
  it; the journal's `bar_time` from here on is the closed bar. Risk
  limits are untouched. Hurz restarted.

## 2026-09-08 (twenty-third run) — keltner_breakout in the nightly rotation

- **Lever:** strategy / pair selection — `keltner_breakout` is kept out
  of the nightly allow-list by operator decision (scheduler comment,
  2026-07-10: it would land on the donchian book's pairs and take
  entries through the one-position-per-instrument guard); it traded via
  pins in July and August (33 trades, -12.79 USD, all in the
  forming-bar era) and has no pins today. On the close-confirmed
  simulator it is the best of the three trend strategies on both
  samples, so the question was whether that earns it a place.
  Preregistered: add it if it beats its random control at t > 2.0 on
  both samples (runs 18 and 22 already hold the figures).
- **Measurement (from this session's runs, 2-ATR stop, ten core
  instruments):**

  | reading | last 365 d | prior 730 d |
  |---|---:|---:|
  | keltner E[R], close-confirmed | +0.035 (n 1,957) | +0.005 (n 4,042) |
  | donchian E[R], close-confirmed | +0.033 | -0.025 |
  | turtle E[R], close-confirmed | +0.009 | -0.021 |
  | keltner vs random, all signals | +0.046, t = 1.87 | +0.020, t = 1.17 |
  | keltner vs random, router-passed | +0.003, t = 0.08 | +0.013, t = 0.51 |
  | live, pins, forming-bar era | 33 trades, -12.79 USD | — |

  Best of three by a small margin on both samples, and not different
  from random at threshold on either; after the router the difference
  vanishes. "Best of three randoms" is not a lever, and the guard
  concern in the scheduler comment is real: on this book five signals
  in six already arrive on an occupied instrument (run 13).
- **Decision:** not changed — keltner stays out of the nightly list and
  unpinned. Its persisted backtest (2026-07-10, 30 days) is stale and
  should be refreshed by the next manual backtest run so the record
  matches the close-confirmed figures above. No code change, no restart.

## 2026-09-08 (twenty-fourth run) — the ranking on the corrected simulator

- **Lever:** pair selection — the persisted `donchian_breakout` ranking
  the selector reads dated from 2026-09-06 and was computed at the
  1-ATR stop, before gap booking (76) and quote-currency sizing (78).
  Refreshed it with `scripts/spot_backtest.py --persist` exactly as the
  nightly job does (55 default instruments, 365 days) and measured how
  far the ranking moved.
- **Measurement (persisted file, before vs after):**

  | | 2026-09-06 (1 ATR) | 2026-09-08 (2 ATR, gaps, currency) |
  |---|---|---|
  | trades | 2,078 | 2,574 |
  | pooled E[R] | -0.0399 | -0.0399 |
  | total USD | -170.2 | -230.1 |
  | top-10 by E[R] | GOLD, OIL_BRENT, AUDNZD, ETHUSD, BTCUSD, USDCHF, DE40, FR40, EURUSD, NZDUSD | NATURALGAS, BTCUSD, GBPJPY, GOLD, EURJPY, AUDNZD, CHFJPY, AUDJPY, USDCHF, USDJPY |
  | top-10 overlap | 4 of 10 | |
  | newly sizeable (n < 5 → ≥ 20) | — | AUDJPY, CADJPY, CHFJPY, EURJPY, GBPJPY, J225, USDJPY, WHEAT |
  | sign flips among pairs with n ≥ 20 on both | 2 | |

  The pooled expectancy is identical to four decimals — the corrections
  moved the dollars, not the R — but the order changed because eight
  instruments that used to size to nothing now trade. Five of the new
  top ten are yen crosses; AUDJPY and CHFJPY measured random in run 21
  and the other three have no measurement here yet. NATURALGAS tops the
  list and is cost-blocked, which the selector's guard handles.
- **Decision:** nothing to build — the file is the selector's input, not
  the book, and it is not versioned; the nightly run at 05:49 UTC will
  recompute all three strategies the same way. Recorded so the next
  active list, which will carry yen crosses for the first time, is read
  as a consequence of run 8 and not as a discovered edge. The guards
  that apply to them (jpy_crosses cluster, USD sizing, cooldown) are all
  in place from this session.

## 2026-09-08 (twenty-fifth run) — the remaining yen crosses in the cluster cap — BUILT IN

- **Lever:** risk guard — run 24 showed the corrected ranking pushes
  EURJPY, GBPJPY and CADJPY into the top ten for the first time, and
  none of them was in a correlation cluster, so the same-direction cap
  of 3 would not have counted them against AUDJPY and CHFJPY. Same
  preregistered criterion as run 12: median |corr| ≥ 0.5 with a
  cluster's members on a year of hourly log returns.
- **Measurement:** `scripts/yen_cluster_correlations.py`.

  | instrument | jpy_crosses | usd_fx | indices | vs USDJPY |
  |---|---:|---:|---:|---:|
  | EURJPY | **0.77** | 0.14 | 0.14 | 0.68 |
  | GBPJPY | **0.72** | 0.19 | 0.21 | 0.60 |
  | CADJPY | **0.67** | 0.19 | 0.07 | 0.82 |
  | USDJPY | 0.50 | 0.46 | 0.10 | — |

  The yen crosses are one bet: pairwise 0.67–0.84 among themselves and
  below 0.25 with everything that is not yen. USDJPY sits exactly on
  the threshold with the crosses and is the USD leg the `usd_fx`
  cluster already counts; an instrument belongs to one cluster, and it
  keeps the one it has.
- **Decision:** built in — EURJPY, GBPJPY and CADJPY join
  `jpy_crosses`; the test now asserts all five crosses and USDJPY's
  cluster. Hurz restarted. Effect on gain: none by construction; it
  keeps the cap true for the instruments the next active list will
  carry.

## 2026-09-08 (twenty-sixth run) — the newly sizeable yen instruments

- **Lever:** pair selection — EURJPY, GBPJPY, CADJPY, J225 and USDJPY
  size for the first time since run 7 and enter the corrected ranking's
  top ten (run 24), unmeasured. Measured each against count-matched
  random entries and on the router-passed path, 2-ATR stop, three live
  trend strategies, both samples; block rule as in run 21 (significantly
  negative on both samples).
- **Measurement:** `scripts/yen_instruments.py`.

  | instrument | last 365 d: E[R] / vs random / t / router-passed E[R] | prior 730 d: same |
  |---|---|---|
  | EURJPY | +0.009 / +0.013 / +0.60 / +0.040 | -0.005 / +0.018 / +0.89 / +0.010 |
  | GBPJPY | -0.014 / -0.019 / -0.75 / +0.038 | +0.001 / +0.016 / +0.74 / -0.027 |
  | CADJPY | +0.029 / +0.030 / +1.29 / +0.000 | -0.008 / +0.019 / +0.88 / -0.039 |
  | J225 | +0.030 / +0.021 / +0.39 / -0.150 | +0.006 / -0.004 / -0.10 / +0.015 |
  | USDJPY | +0.015 / +0.009 / +0.34 / +0.001 | -0.005 / +0.013 / +0.54 / -0.070 |
  | **group** | +0.015 / +0.012 / +0.73 / -0.014 | -0.002 / +0.012 / +1.06 / -0.023 |

  Random on both samples, before and after the router; no instrument
  is significantly negative twice (USDJPY's -0.070 after the router on
  the older sample, t = -2.26, reads +0.001 on the recent one). They
  join the book on the same footing as everything else in it: zero
  expectancy, guards in place (jpy_crosses cluster, USD sizing,
  cooldown, one position per instrument).
- **Decision:** not changed — nothing blocked, nothing pinned. No code
  change, no restart.

## 2026-09-08 (twenty-seventh run) — does the instrument ranking persist?

- **Lever:** pair selection — whether an instrument's expectancy in one
  period predicts the next, which is what ranking by backtest assumes.
  Tested on three disjoint yearly windows of the three-year history for
  the 31 measured instruments (21 with router-passed figures in every
  window), pooled over the three live trend strategies, 2-ATR stop:
  Spearman rank correlation of instrument E[R] across consecutive
  windows, and the next-period E[R] of the prior period's top and
  bottom ten.
- **Measurement:** `scripts/all_instruments_check.py` for the two
  older windows, this session's runs 16, 21 and 26 for the recent one.

  | transition | Spearman, all signals | Spearman, router-passed | next-period E[R]: prior top-10 / bottom-10 / all |
  |---|---:|---:|---|
  | year 3 → year 2 | +0.02 (p = 0.92) | +0.51 (p = 0.02) | -0.064 / -0.057 / -0.060 |
  | year 2 → year 1 | +0.10 (p = 0.67) | +0.32 (p = 0.16) | -0.015 / -0.032 / -0.021 |
  | year 3 → year 1 | +0.32 (p = 0.15) | +0.62 (p = 0.00) | -0.002 / -0.044 / -0.021 |

  On all signals there is no persistence: the first transition ranks
  the previous winners *below* the previous losers. On the
  router-passed path something persists, on 21 instruments with a few
  hundred trades each — the same size of effect this document has
  watched dissolve five times, and it is not stable across transitions
  either. The recurring names at the bottom (UK100, FR40, AU200) are
  the structural losers run 21 already examined; AU200 is blocked, and
  FR40 and UK100 fail the live-path criterion.
- **Decision:** not changed — no ranking rule is added, and the
  selector's existing ranking is read as what it is: a list of
  instruments that trade, not a forecast. No code change, no restart.

## 2026-09-08 (twenty-eighth run) — the live-expectancy veto

- **Lever:** pair selection — the selector retires a (strategy, pair)
  combo once its live mean R over at least 8 closed trades is at or
  below -0.15 (six combos retired today, among them donchian/OIL_CRUDE
  at -0.25). Replayed the rule and three variants per combo on the
  per-strategy backtest timeline, ten core instruments, 2-ATR stop,
  both samples: what would the retired combos have returned afterwards?
- **Measurement:** `scripts/veto_replay.py`.

  | rule | last 365 d: retired / blocked n / E[R] blocked / t / delta | prior 730 d: same |
  |---|---|---|
  | **≥ 8 trades, mean ≤ -0.15 (live)** | 12 / 2,271 / **+0.061** / +2.87 / -138.8 R | 17 / 7,088 / **-0.034** / -2.82 / +242.6 R |
  | ≥ 8, ≤ -0.30 | 8 / 1,600 / +0.080 / +3.08 / -128.0 R | 6 / 2,565 / -0.013 / -0.61 / +33.1 R |
  | ≥ 16, ≤ -0.15 | 6 / 1,155 / +0.063 / +2.09 / -72.2 R | 13 / 4,888 / -0.019 / -1.32 / +91.6 R |
  | ≥ 30, ≤ -0.10 | 5 / 754 / +0.043 / +1.09 / -32.2 R | 13 / 4,927 / -0.042 / -2.77 / +207.9 R |

  On the recent year the veto retires combos that then go on to be the
  book's best (+0.061 R); on the two years before it retires the
  structural losers (-0.034 R). Both readings are "significant" and
  they point opposite ways, which is what a rule keyed to eight trades
  of a zero-expectancy process has to do (34, 40). No variant is
  consistent either.
- **Decision:** not changed — the veto stays as the fail-closed retire
  rule it was written as; neither tightening nor removing it is
  supported. The one live signal that would matter — a combo that is
  worse than random on both samples — is the per-instrument test of
  runs 21 and 26, and AU200 is the only one that met it. No code
  change, no restart.

## 2026-09-08 (twenty-ninth run) — the strategy-level veto

- **Lever:** pair selection one level up — the selector retires a whole
  strategy once its live mean R over ≥ 25 closed trades is ≤ -0.10.
  Live it retires the mean-reversion family and `donchian_breakout_v3`
  (all already retired by decision) and `keltner_breakout` at -0.25 R
  over 33 trades — every one of them a forming-bar-era trade, and the
  strategy is out of the rotation regardless. Replayed the rule and two
  variants per strategy on both backtest samples.
- **Measurement:** `scripts/strategy_veto_replay.py`.

  | rule | last 365 d: retired / E[R] of what it blocks / t / delta | prior 730 d: same |
  |---|---|---|
  | **≥ 25, ≤ -0.10 (live)** | all three trend strategies / +0.030 / +2.31 / -160.3 R | all three / -0.012 / -1.34 / +130.5 R |
  | ≥ 50, ≤ -0.10 | donchian, keltner / +0.039 / +2.60 / -156.0 R | all three / -0.011 / -1.27 / +123.5 R |
  | ≥ 100, ≤ -0.05 | donchian / +0.038 / +1.76 / -75.4 R | all three / -0.011 / -1.25 / +121.0 R |

  A running mean over the first few dozen trades of a zero-expectancy
  process crosses -0.10 by chance, so the rule retires every trend
  strategy on every sample within its first 25–140 trades — forfeiting
  the recent year's positive book and avoiding the older years'
  negative one. It is not selecting strategies; it is timing when the
  book stops. The live rule has never retired a rotation strategy
  (donchian +60 USD, turtle +1.6 USD over 127 and 67 trades), so it has
  cost nothing so far.
- **Decision:** not changed. Noted for the record: the strategy veto's
  live evidence is entirely from the forming-bar era (run 22), so the
  keltner entry should be re-read once close-confirmed trades exist —
  moot while the strategy is out of the rotation by decision (run 23).
  No code change, no restart.

## 2026-09-08 (thirtieth run) — the selector's minimum trade count

- **Lever:** pair selection — the sample-size floor below which a
  (strategy, pair) combo is not ranked. The CLI default is 30; the
  nightly job passes 10 (lowered 2026-06-29 because the router-gated
  backtest left the list empty at 15). Measured per combo on the
  router-passed path, 26 instruments × three trend strategies, whether
  the ranking year's trade count predicts anything about the next
  year, and where the floor actually binds.
- **Measurement:** `scripts/combo_counts.py` (ranking year = days
  730–365, next year = last 365).

  | reading | value |
  |---|---|
  | router-passed combos, trend strategies | 78, median 59 trades in the ranking year, none below 30 |
  | next-year E[R] of those combos | -0.0525 (combo mean), -0.0457 trade-weighted |
  | Spearman (ranking-year E → next-year E) | +0.24 (p = 0.04) |
  | persisted ranking, donchian: pairs with n < 30 | 14, all cost-blocked crypto plus PLATINUM / PALLADIUM |
  | persisted ranking, turtle: pairs with n = 0 | 21 |
  | persisted ranking, momentum: median n | 1; the eight ranked momentum combos sit on 10–21 trades |

  For the three trend strategies the floor never binds — every combo
  clears 30 on its own. It binds on momentum, which passes the router
  one time in seven (run 23) and is ranked first on the indices on
  10–21 trades; those combos have produced two live trades since July,
  so the noise they carry into the list has cost nothing so far. The
  weak rank persistence (+0.24) is the run-27 result again.
- **Decision:** not changed. Raising the floor to 30 would empty the
  momentum rows without touching the trend book, and lowering it
  further would rank on even less; neither is a gain lever. Recorded:
  the momentum ranks in the active list rest on samples section 40
  says cannot be validated. No code change, no restart.

## 2026-09-08 (thirty-first run) — the stale exit by wall clock

- **Lever:** exit logic — live closes a stale position 24 wall-clock
  hours after entry, the backtests after 24 bars. For instruments with
  session breaks (indices, commodities, every weekend) 24 hours hold
  fewer bars, so the live leash is shorter than the measured one.
  Simulated both leashes on ten core instruments, three live trend
  strategies, 2-ATR stop, both samples.
- **Measurement:** `scripts/wallclock_leash.py`.

  | leash | last 365 d: n / E[R] / timeouts | prior 730 d: n / E[R] / timeouts | diff / t |
  |---|---|---|---|
  | 24 bars (backtest) | 5,415 / +0.0276 / 2,539 | 11,170 / -0.0134 / 5,474 | — |
  | 24 wall-clock hours (live) | 5,700 / +0.0200 / 2,944 | 11,811 / -0.0153 / 6,277 | -0.0075 / -0.43 and -0.0019 / -0.16 |

  The live leash frees the instrument a little earlier (5 % more
  trades, 15 % more timeouts) and gives back 0.002–0.008 R for it, same
  sign on both samples and well inside the noise. A live/backtest
  divergence, but a small one, and in the conservative direction.
- **Decision:** not changed — the wall-clock leash stays; converting it
  to a bar count would need session calendars per instrument for a
  difference this size. No code change, no restart.

## 2026-09-08 (thirty-second run) — nightly selector reliability

- **Lever:** pair selection, operational — how often the nightly
  ranking refresh has actually run since July and how stale the active
  list the bot trades has been. Read from the application log's nightly
  markers (one per day when the scheduler fired) and the active list's
  own timestamp.
- **Measurement (logs, read-only):**

  | period | nightly markers | missing days | cause |
  |---|---:|---|---|
  | 2026-07-08 – 2026-08-26 | 49 of 50 days | 08-01 | one gap, unexplained |
  | 2026-08-27 – 2026-09-04 | 0 of 9 days | all | the reboot outage of section 49 (bot down) |
  | 2026-09-05 – 2026-09-06 | 2 of 2 | — | |
  | 2026-09-07 | 0 of 1 | 09-07 | bank-holiday guard suspends the whole loop, scheduler included |
  | active list today | generated 2026-09-06 05:49 UTC | two days old | today's run is due 05:49 UTC |

  When the bot runs, the scheduler runs; the ranking goes stale only
  when the bot is down or idled by the holiday guard, which also idles
  the backtest that needs no open market. Two days of staleness on a
  ranking that section 97 shows carries no forecast is not a cost the
  book can measure.
- **Decision:** not changed — letting the scheduler run through
  holidays would be correct in principle and worth nothing in
  expectancy, so it is recorded, not built. No code change, no restart.

## 2026-09-08 (thirty-third run) — the cluster cap and the other refusals in practice

- **Lever:** risk guards as they actually fired — the journal's refused
  intents since 2026-07-10, by reason, to see which guards do work in
  the live book and what the refused signals would have returned. The
  counterfactual replay of refused signals exists for the router (49e);
  for the other guards the question was first whether there is
  anything to replay.
- **Measurement (journal, read-only, 2026-07-10 to today):**

  | refusal | count | note |
  |---|---:|---|
  | ADX regime router | 202 | replayed in 49e and 67: flat |
  | broker order rejected (HTTP 400) | 20 | 18 stop-level errors on APTUSD, AAVEUSD, ARBUSD in July — all cost-blocked since; 2 OIL_CRUDE "market closed" in the 21:00–22:00 UTC break |
  | duplicate signal for the bar | 15 | dedup, by design |
  | order deleted by broker | 12 | all July, blocked crypto |
  | stop below the 1 % floor | 3 | |
  | concurrent cap (8) | 2 | |
  | **correlation-cluster cap** | **0** | never fired |
  | daily-loss limit | 0 | never fired |
  | stop-out cooldown | 0 | new today |

  The cluster cap has not refused a single signal since July: with one
  position per instrument, the router taking two signals in three and
  a book of three to eight positions, three same-direction positions in
  one cluster has simply not occurred — including yesterday's three
  yen-and-HK50 shorts, which spanned two clusters. There is nothing to
  replay. Of the broker rejections only the oil break survives the
  blocklists, two in two months, and the dedup then drops that bar's
  signal; a signal every month is not worth a session calendar.
- **Decision:** not changed. The guards that bind are the router and
  the one-position rule; the rest are backstops that have not been
  needed, which is the state a backstop should be in. No code change,
  no restart.

## 2026-09-08 (thirty-fourth run) — GOLD under the stop floor — BUILT IN

- **Lever:** cost filter / stop placement — the 1.00 % stop floor
  (commit 167ec40, 2026-08-24) refused three GOLD signals and nothing
  else since July. Reason: GOLD is the only active instrument whose
  venue minimum is 0.1 % of price instead of 1 %, so the live loop
  never widened its 2-ATR stop (about 0.9 %) and the floor refused it,
  while every backtest — GOLD absent from the minimum-distance cache —
  widened it to the 1.05 % default and measured it there. Live accepted
  13 GOLD trades before the floor at 0.32–0.52 % stops and 0 after.
  Simulated GOLD's three treatments on both samples, three live trend
  strategies.
- **Measurement:** `scripts/gold_stop_treatment.py`.

  | treatment | last 365 d: n / E[R] / t / Σ R | prior 730 d: n / E[R] / t / Σ R |
  |---|---|---|
  | raw 2-ATR stop (venue min 0.1 %) | 558 / +0.090 / +1.81 / +50.2 | 1,213 / +0.009 / +0.27 / +11.1 |
  | **widened to 1.05 % (backtest)** | 499 / **+0.130** / **+2.64** / +64.9 | 918 / **+0.073** / **+2.70** / +66.8 |
  | refused under 1.00 % (live floor) | 182 / +0.056 / +0.66 / +10.2 | 67 / -0.060 / -0.47 / -4.0 |

  The widened treatment is positive and significant on both samples,
  beats its random control on both (run 16: +0.152 R, t = 2.71;
  +0.100 R, t = 3.24), and is what the book's other 26 instruments get
  by venue rule. The floor kept 6–33 % of GOLD's signals — the ones
  fired in high-volatility hours — and those read worse. Mechanism as
  in run 11: the wider stop lowers the cost per R (0.0099 against
  0.0139) and the gross does not get worse.
- **Decision:** built in. The live loop now widens every stop to at
  least `VENUE_MIN_STOP_FRACTION` (1.05 % of price, shared with the
  backtest's default) before the floor is checked, so GOLD is treated
  as the measurements assumed and the floor stays as the backstop it
  is for the venue-less path. Two tests cover the widening and the
  preserved reward ratio. Risk per trade unchanged (sizing shrinks the
  position). Hurz restarted. This is the first lever in the session
  that is significant on both samples *and* on an instrument that beats
  random on both.

## 2026-09-08 (thirty-fifth run) — the backtest's minimum-distance cache

- **Lever:** cost filter, backtest basis — run 34 found GOLD missing
  from `data/capital_min_distances.json`, so the question was for which
  other instruments the backtest's 1.05 % default disagrees with the
  venue's actual rule. Compared the 14 cached entries and the default
  against the broker's dealing rules for all 27 active instruments and
  the cache's own names (35 in total).
- **Measurement (broker API, read-only):**

  | | count |
  |---|---:|
  | instruments checked | 35 |
  | venue minimum 1 % (effective 1.05 %) | 34 |
  | venue minimum 0.1 % | 1 (GOLD) |
  | backtest ≠ venue-effective minimum | 1 (GOLD, now widened live to the same 1.05 %) |
  | cached entries, all 1 % | 14 |

  Every cached entry and every uncached default lands on the same
  1.05 % the venue enforces, so the cache carries no information the
  default does not; the one instrument whose venue rule differs is the
  one run 34 aligned by widening. Live and backtest now place the same
  minimum stop on every instrument in the book.
- **Decision:** not changed — nothing to add to the cache and nothing
  to correct in the default. No code change, no restart.

## 2026-09-08 (thirty-sixth run) — GOLD's cost assumption

- **Lever:** cost filter — with GOLD now tradeable at the widened stop
  (run 34) and measured as the book's one consistently positive
  instrument, its cost input had to be right. Compared the audited
  spread the simulator charges with the venue's live quote and with the
  twelve live GOLD fills in the journal.
- **Measurement (broker quote and journal, read-only):**

  | reading | value |
  |---|---|
  | audited spread per side | 0.0056 % → 0.0112 % round trip, 1.1 % of R at the 1.05 % stop |
  | live quote now, half spread | 0.0056 % (SILVER 0.0373 vs 0.0374 audited, BTCUSD 0.0317 vs 0.0308, US500 0.0039 vs 0.0039) |
  | live fills, fill vs sizing mid, median | **0.0282 %** — five times the half spread |
  | live fills, fill vs signal close, mean | +0.0199 % (forming-bar era) |

  The spread table is exact for GOLD and for the three controls. What
  the twelve fills show is execution beyond the spread — the order
  reached the book 0.028 % of price away from the quote it was sized
  on, worth about 5 % of R at the widened stop, or four times the
  spread cost the simulator charges. Every one of those fills is from
  the forming-bar era, when orders went out mid-bar on a moving price;
  whether it persists with close-confirmed entries is unknown until
  the first such GOLD trades exist.
- **Decision:** not changed — the audited cost stays; the fill
  deviation is recorded as the figure to check on the first
  close-confirmed GOLD trades, and if it holds, GOLD's cost input
  should be raised to it. No code change, no restart.

## 2026-09-08 (thirty-seventh run) — off-hours spreads on the indices

- **Lever:** cost filter — the audited spread table holds daytime
  spreads; at 04:36 UTC the live quotes ran 13× the table on FR40,
  6× on HK50, 3× on UK100, 2.7× on DE40 and AUDJPY, with FR40 and HK50
  above the 10 % cost ceiling at the 1.05 % stop. Half of all index
  signals fire outside cash hours (FR40 46 %, UK100 46 %, DE40 50 %,
  EU50 49 %, HK50 47 %, US500 47 %). Live charges the quote at the
  moment of the order (and widens or refuses); the backtest charges the
  table at every hour. Simulated the five European and Asian indices
  with hour-dependent costs — the measured off-hours half-spread
  outside cash hours, the table inside — against the table alone,
  three live trend strategies, 2-ATR stop, both samples.
- **Measurement:** `scripts/index_offhours_costs.py` (off-hours
  spreads from a single 04:36 UTC snapshot).

  | costs | last 365 d: n / E[R] / t / cost R / Σ R | prior 730 d: same |
  |---|---|---|
  | audited table (backtest) | 2,292 / -0.048 / -2.76 / 0.013 / -109 | 4,624 / -0.039 / -3.20 / 0.013 / -179 |
  | hour-dependent | 2,243 / -0.057 / -3.30 / 0.029 / -127 | 4,526 / -0.052 / -4.33 / 0.028 / -236 |
  | of which off-hours trades | 961 at -0.054 R | 1,851 at -0.066 R |

  The five indices are significantly negative on both samples with
  either cost model, and the true costs make them a further
  0.01–0.013 R worse: the cost per R doubles because half the trades
  are opened into an off-hours spread the table does not know. The
  live cost filter sees that spread and widens or refuses, so live is
  protected where the backtest is not — the ranking that puts these
  instruments in the book is the thing that is wrong.
- **Decision:** not built in — an hour-dependent cost model needs a
  spread-by-hour table, and one 04:36 snapshot is not that table. The
  per-instrument block criterion (run 21) is still not met on the
  router-passed path for any of the five. Recorded as the one cost
  correction still open: sample the venue's spreads by hour for a week,
  then charge them in the simulator. No code change, no restart.

## 2026-09-08 (thirty-eighth run) — spread sampler on the heartbeat — BUILT IN

- **Lever:** cost filter, data foundation — run 37 showed the audited
  spread table is a daytime table and that half of all index signals
  fire into off-hours spreads up to thirteen times wider, which the
  backtest that ranks the instruments cannot see. The correction needs
  a spread-by-hour table, which the project did not have; this run
  builds the collector.
- **Measurement:** run 37's figures (FR40 13×, HK50 6×, UK100 3×,
  DE40 2.7× the table at 04:36 UTC; -0.01 R further on the five
  European and Asian indices when charged). The sampler itself is
  measured by its first output after restart.
- **Decision:** built in. On every heartbeat (hourly) the live loop
  now appends one bid/offer line per active instrument to
  `data/spread_samples.jsonl` from the session it already holds, with
  the half-spread in percent; venues without dealing rules contribute
  nothing, and a failing sample never touches the trading loop. Two
  tests cover the line format and the no-rules case. No trading
  behaviour changes; the file is git-ignored. After a week the table
  exists and the simulator can charge the hour's spread instead of the
  day's. Hurz restarted.

## 2026-09-08 (thirty-ninth run) — long versus short by asset class — COMMODITY SHORTS BLOCKED

- **Lever:** regime filter, direction — section 4 had read direction
  off the early live journal only (long -0.074 R, short -0.163 R,
  pooled over everything). Never measured on the simulator at the
  current configuration. Preregistered rule: a class's short (or long)
  side is blocked only if significantly negative (t < -2) on both
  disjoint samples and the long-short difference holds at |t| > 2 with
  the same sign on both.
- **Measurement:** `scripts/direction_split.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, three live trend strategies, all 26
  tradeable instruments in four classes. Last 365 days, then days
  366–1,095 as the independent check.
- **Result (E[R] net of costs):**

  | class, side | last 365 d: n / E[R] / t | prior 730 d: n / E[R] / t |
  |---|---|---|
  | index shorts | 852 / **-0.199** / **-6.26** | 1,632 / +0.017 / +0.77 |
  | **commodity shorts** | 491 / **-0.160** / **-3.32** | 1,004 / **-0.102** / **-3.17** |
  | commodity longs | 559 / +0.035 / +0.76 | 1,022 / +0.010 / +0.33 |
  | commodity long − short | +0.195 / t +2.92 | +0.112 / t +2.49 |
  | fx shorts | 784 / -0.027 / -1.58 | 1,639 / +0.003 / +0.21 |
  | crypto shorts | 340 / +0.133 / +2.19 | 590 / +0.065 / +1.36 |

  Index shorts, the largest reading of the recent year, dissolve on the
  older sample — the bull year, not a lever. Commodity shorts meet the
  preregistered bar on both samples; SILVER carries most of it (shorts
  -0.33 / -0.26 R at t -3.5 / -3.9, longs flat), the oils' shorts are
  negative on both and significant on the recent year, GOLD and COPPER
  shorts flat. Live journal agrees in sign: 29 commodity shorts
  -20.04 USD, 58 longs -6.86 USD. Simulator gain from removing the
  class's shorts: about 79 R over the last year, 51 R a year before
  that, before the one-position rule and the caps merge signals.
- **Decision:** built in. `SHORT_BLOCKED_PAIRS` (the five commodities)
  and `direction_blocked()` in `trading_blocks.py`; `evaluate_pair`
  journals a short signal there as a rejected intent, `execute_intent`
  refuses it, `_simulate_trades` and the walk-forward stability check
  skip it so the nightly ranking sees the same book. Longs and the
  instruments stay active; no risk limit is touched. Three tests cover
  the list, the refusal and the untouched long side; full suite green
  (266 tests). Hurz restarted. Open positions keep their exit path.

## 2026-09-08 (fortieth run) — late-breakout filter (extension from EMA20)

- **Lever:** regime filter, entry quality — the breakout bar's features
  had never been measured; every signal was taken as fired. Hypothesis:
  a signal close already far from its 20-bar EMA (in ATR) is a late
  entry that reverts into the stop, so a maximum-extension filter would
  raise E[R]. Preregistered rule: a quartile bucket is blocked only if
  significantly negative (t < -2) on both disjoint samples and its
  difference to the rest holds at |t| > 2 with the same sign on both;
  edges fixed on the recent year and applied unchanged to the older.
- **Measurement:** `scripts/late_breakout_filter.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments. Last 365 days, then days
  366–1,095 as the independent check.
- **Result (E[R] net of costs):**

  | extension bucket | last 365 d: n / E[R] / t / t_diff | prior 730 d: n / E[R] / t / t_diff |
  |---|---|---|
  | below 1.97 ATR (weak) | 1,126 / **-0.080** / **-3.44** / **-2.28** | 2,237 / -0.009 / -0.54 / -1.70 |
  | 1.97–2.35 | 1,125 / -0.002 / -0.06 / +1.52 | 2,316 / -0.003 / -0.16 / -1.27 |
  | 2.35–2.92 | 1,126 / -0.029 / -1.19 / +0.20 | 2,273 / +0.046 / +2.68 / +2.05 |
  | above 2.92 ATR (late) | 1,126 / -0.022 / -0.86 / +0.49 | 2,287 / +0.028 / +1.56 / +0.80 |

  Late breakouts are not worse on either sample — the hypothesis is
  dead. The recent year instead flags the weak end (closes within two
  ATR of the mean: -0.080 R, t -3.44, the marginal break that reverses),
  and that bucket is the worst of the four on the older sample too, but
  at -0.009 R, t -0.54 and t_diff -1.70 it misses the bar on both
  counts. Same shape per strategy, significant nowhere on the older
  sample. This is `donchian_atr`'s buffered breakout (rejected
  2026-07-08) measured as a feature of the live signals; it lands where
  that did.
- **Decision:** not built in — dead in both directions. No code change,
  no restart. Section 110.

## 2026-09-08 (forty-first run) — breakout-bar strength (signal bar range in ATR)

- **Lever:** regime filter, entry quality — after run 40 measured the
  close's extension from its mean, the one remaining unmeasured feature
  of the signal bar is the bar itself: its high-low range in ATR(14).
  Hypothesis: a wide-range breakout bar is a momentum bar that carries
  through, a narrow one is the marginal break that reverses, so a
  minimum-range filter would raise E[R]. Preregistered rule as in run
  40: a quartile bucket is blocked only if significantly negative
  (t < -2) on both disjoint samples and its difference to the rest holds
  at |t| > 2 with the same sign on both; edges fixed on the recent year
  and applied unchanged to the older.
- **Measurement:** `scripts/breakout_bar_range.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments. Last 365 days, then days
  366–1,095 as the independent check. History fetched in paced 35-day
  pages so the replay stays off the live loop's request budget (one 429
  on the bot during the run against 21 during run 40's fetch).
- **Result (E[R] net of costs):**

  | signal bar range | last 365 d: n / E[R] / t / t_diff | prior 730 d: n / E[R] / t / t_diff |
  |---|---|---|
  | below 1.00 ATR | 1,126 / -0.054 / -2.39 / -1.05 | 2,089 / -0.005 / -0.31 / -1.38 |
  | 1.00–1.45 | 1,125 / -0.059 / -2.45 / -1.23 | 2,237 / -0.004 / -0.23 / -1.31 |
  | 1.45–2.09 | 1,125 / -0.061 / -2.51 / -1.32 | 2,438 / **+0.034** / **+2.09** / +1.35 |
  | above 2.09 ATR (wide) | 1,127 / **+0.042** / +1.58 / **+3.36** | 2,351 / +0.032 / +1.81 / +1.12 |

  On the recent year the wide quarter is the only positive bucket and
  beats the rest by +0.100 R at t = +3.36, while the three narrower
  quarters each read about -0.06 R at t ≈ -2.4 — the shape the
  hypothesis predicts, in all three strategies. On the older sample the
  sign of the wide quarter persists (+0.032 R) but at t = 1.81 and a
  difference of only t = +1.12, none of the narrower buckets is
  negative, and the 1.45–2.09 bucket a filter would remove is the best
  of the four there at +0.034 R, t = +2.09. Neither half of the
  preregistered bar is met on the older sample.
- **Decision:** not built in — the recent-year reading is a bull-year
  artefact of the same kind as run 39's index shorts, not a stable
  feature. No code change, no restart. Section 111.

## 2026-09-08 (forty-second run) — ADX slope at the signal bar

- **Lever:** regime filter — the router gates on the ADX *level*
  (>= 30, settled in runs 18 and 22); the *direction* of ADX at the
  signal had never been read. Hypothesis: a breakout that fires while
  ADX is still rising is a trend building, one that fires as ADX flattens
  or turns is a trend fading into the stop. Feature: ADX(14) change over
  the three bars before the signal. Preregistered rule as in runs 40 and
  41: a quartile bucket is blocked only if significantly negative
  (t < -2) on both disjoint samples and its difference to the rest holds
  at |t| > 2 with the same sign on both; edges fixed on the recent year
  and applied unchanged to the older.
- **Measurement:** `scripts/adx_slope_filter.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, paced 35-day history pages.
  Last 365 days, then days 366–1,095 as the independent check.
- **Result (E[R] net of costs):**

  | ADX change over 3 bars | last 365 d: n / E[R] / t / t_diff | prior 730 d: n / E[R] / t / t_diff |
  |---|---|---|
  | below +0.22 (fading) | 1,126 / **-0.081** / **-3.34** / **-2.33** | 2,519 / -0.017 / -1.07 / **-2.19** |
  | +0.22 to +2.58 | 1,125 / -0.004 / -0.17 / +1.31 | 2,432 / +0.035 / +2.13 / +1.55 |
  | +2.58 to +4.43 | 1,126 / -0.037 / -1.54 / -0.25 | 2,057 / +0.009 / +0.51 / -0.25 |
  | above +4.43 | 1,127 / -0.006 / -0.23 / +1.24 | 2,093 / +0.028 / +1.55 / +0.95 |

  The fading quarter is the worst bucket on both samples and its gap to
  the rest holds at |t| > 2 with the same sign twice — the first
  signal-bar feature to do that (runs 40 and 41 both dissolved). But on
  the older sample the bucket itself reads -0.017 R at t = -1.07, not
  the t < -2 the rule requires, so the first half of the bar is missed.
  Same shape in all three strategies, significant nowhere on the older
  sample; the recent-year reading is carried by turtle_breakout.
- **Decision:** not built in — the preregistered bar was not met. No
  code change, no restart. Section 112. The feature is the strongest
  candidate this log has recorded and is left to the forward test:
  `entry_adx` is already journalled, so the slope can be read live
  without changing what trades.
- **Operational note:** a second Charly session reset the worktree
  (`git reset --hard`, clean) at 18:04 CEST during this run and removed
  the uncommitted replay script once; it was recreated and committed
  before the second sample ran.

## 2026-09-08 (forty-third run) — age of the broken level (base versus running trend)

- **Lever:** regime filter, entry quality — runs 40 to 42 measured the
  signal bar; the level it clears had not been read. Hypothesis: a
  breakout that resolves a base (channel extreme printed long ago) has
  more room than one that extends a trend already running (extreme
  printed a bar or two ago), so filtering out fresh-level breaks would
  raise E[R]. Feature: bars since the broken channel extreme, over the
  strategy's own window (20 donchian / keltner, 55 turtle), as a
  fraction of the window. Preregistered rule as in runs 40 to 42: a
  quartile bucket is blocked only if significantly negative (t < -2) on
  both disjoint samples and its difference to the rest holds at |t| > 2
  with the same sign on both; edges fixed on the recent year and
  applied unchanged to the older.
- **Measurement:** `scripts/breakout_base_age.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, paced 35-day history pages
  (one 429 on the replay's fetch, none on the bot). Last 365 days, then
  days 366–1,095 as the independent check.
- **Result (E[R] net of costs):**

  | age of broken level / window | last 365 d: n / E[R] / t / t_diff | prior 730 d: n / E[R] / t / t_diff |
  |---|---|---|
  | below 0.05 (fresh) | 606 / -0.041 / -1.22 / -0.30 | 1,178 / +0.011 / +0.47 / -0.22 |
  | 0.05–0.145 | 1,618 / -0.028 / -1.41 / +0.21 | 3,234 / +0.010 / +0.73 / -0.48 |
  | 0.145–0.35 | 1,145 / -0.024 / -0.98 / +0.38 | 2,301 / +0.031 / +1.78 / +1.01 |
  | above 0.35 (base) | 1,125 / -0.040 / -1.57 / -0.37 | 2,416 / +0.011 / +0.65 / -0.32 |

  Flat on both samples: no bucket is significantly negative anywhere and
  no difference to the rest exceeds |t| = 1.01. The deep-base split
  (age > 0.5, a quarter of the trades) reads the same as the rest on
  both samples too. Per strategy only noise, with a turtle bucket
  flipping sign between the samples. The rule is missed already on the
  recent year.
- **Decision:** not built in — dead on both samples. No code change,
  no restart. Section 113. The signal bar (extension, range, ADX slope)
  and the level it breaks (age) are now all measured; none is a lever,
  the ADX slope of run 42 remains the only one worth a forward read.

## 2026-09-08 (forty-fourth run) — 4h trend strength as a second regime gate

- **Lever:** regime filter — the router gates 1h trend entries on the
  1h ADX only. Hypothesis: a breakout with the 4h chart already
  trending has the higher timeframe behind it, so a 4h-ADX floor would
  raise E[R]. Feature: ADX(14) of the 4h bars resampled from the same
  history, read at the last 4h bar completed before the signal.
  Preregistered rule as in runs 40 to 43 (quartile bucket blocked only
  if t < -2 on both disjoint samples and |t| > 2 for the difference to
  the rest with the same sign on both; edges fixed on the recent year).
  After the recent year and before the older sample: the median split
  (4h ADX >= 25.5) preregistered as a second candidate, same bar.
- **Measurement:** `scripts/htf_adx_gate.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, paced 35-day history pages
  (no 429 on either fetch or on the bot). Last 365 days, then days
  366–1,095 as the independent check.
- **Result (E[R] net of costs):**

  | 4h ADX at the 1h signal | last 365 d: n / E[R] / t / t_diff | prior 730 d: n / E[R] / t / t_diff |
  |---|---|---|
  | below 19.8 | 1,106 / +0.019 / +0.80 / +2.22 | 2,140 / +0.051 / +2.94 / +2.37 |
  | 19.8–25.5 | 1,107 / +0.005 / +0.20 / +1.54 | 2,112 / +0.055 / +3.11 / +2.60 |
  | 25.5–33.2 | 1,105 / **-0.073** / **-3.01** / **-2.15** | 2,176 / +0.031 / +1.78 / +1.05 |
  | above 33.2 | 1,108 / **-0.062** / **-2.43** / -1.56 | 2,637 / **-0.060** / **-3.73** / **-5.57** |
  | upper half (>= 25.5) | 2,213 / **-0.068** / **-3.83** / **-3.23** | 4,813 / -0.019 / -1.60 / **-4.21** |

  The hypothesis is reversed: 1h breakouts that fire while the 4h chart
  is *not* yet trending are the profitable side on both samples, and
  the lower-versus-upper difference holds at t = -3.23 and -4.21 — the
  first filter whose gain has the same sign on both samples (book
  -0.028 → +0.012 R and +0.015 → +0.053 R, at half the entries). The
  bar is still missed: the recent year's qualifying quartile turns
  positive on the older sample, and the upper half is not significantly
  negative on its own there (t = -1.60 against t < -2) — the same
  clause the ADX slope of run 42 failed, and the two features are
  related. The top quarter (above 33.2) passes the first clause on both
  samples and fails the difference on the recent year (t = -1.56); it
  was not preregistered.
- **Decision:** not built in — the preregistered bar was not met on
  either candidate. No code change, no restart. Section 114. This
  replaces the ADX slope as the strongest candidate for the forward
  test; the 4h ADX at entry is reconstructable from each journalled
  trade's `bar_time` and the 1h history, so no journal change is
  needed to read it forward. A cleaner second look would fix the split
  at the median once and test it on a sample neither run has seen,
  which the history endpoint does not currently offer.

## 2026-09-08 (forty-fifth run) — the 4h-ADX split on data no run had selected on

- **Lever:** regime filter, second look — run 44 left the 4h-ADX median
  split (block 1h trend entries when 4h ADX >= 25.5) as the strongest
  candidate on record, with the difference to the rest holding on both
  walk-forward samples but the block bar missed. Both samples were the
  same 26 instruments. Preregistered: the ceiling is built in only if,
  on the instruments excluded from the live book, the upper half is
  worse than the lower at t < -2, and the live journal agrees in sign.
- **Measurement:** `scripts/htf_adx_second_look.py`. (A) The thirteen
  excluded instruments with history (cost blocklist, CORN, NATURALGAS,
  AU200; APTUSD has none), three years, cost-charging walk-forward
  simulator, router-passed path, 2-ATR stop, venue minimum, live
  widening rule, costs charged without the ceiling skip, three live
  trend strategies. (B) The live journal: 231 closed Capital.com trades
  of the 1h trend strategies, realised R at the actual fill, 4h ADX at
  the signal bar reconstructed from the 1h history. Split fixed at
  25.532 from run 44.
- **Result:**

  | sample | n | lower half E[R] | upper half E[R] | upper − lower / t |
  |---|---|---|---|---|
  | (A) excluded instruments, net of cost | 9,257 | -0.131 | -0.125 | +0.006 / +0.33 |
  | (A) excluded instruments, gross | 9,257 | +0.010 | +0.002 | -0.008 / -0.42 |
  | (B) journal, all closed | 231 | -0.083 | +0.013 | +0.096 / +0.72 |
  | (B) journal, forward since 2026-08-24 | 27 | -0.138 | +0.073 | +0.211 / +0.52 |

  Flat on the excluded instruments in all three strategies, net and
  gross; reversed in sign in the journal in every slice. Run 44's
  reading (t_diff -3.23 and -4.21) does not exist outside the 26
  instruments it was found on.
- **Decision:** not built in — both preregistered conditions failed.
  No code change, no restart. Section 115. The 4h ADX is struck from
  the forward-test list; the ADX slope (run 42) remains there with the
  caveat that it, too, has only ever been read on the 26. Rule added
  for the log: a candidate that survives the independent time sample
  is read on the excluded instruments and on the journal before it is
  believed.

## 2026-09-08 (forty-sixth run) — peer confirmation (same-class breakouts in the same direction)

- **Lever:** regime filter, new signal source — the first
  cross-instrument reading after the single-instrument entry features
  were measured out in runs 40 to 45. Hypothesis: a breakout that is
  part of a class-wide move (other fx pairs, indices, commodities
  breaking the same way within 24 bars) carries further than a lone
  break, so a confirmation filter would raise E[R]. Feature: number of
  other instruments of the same class with a router-passed signal of
  any live strategy in the same direction in the 24 bars up to the
  signal; buckets fixed a priori at 0 / 1 / 2 / >= 3. Preregistered
  rule as in runs 40 to 44 (t < -2 for the bucket and |t| > 2 for the
  difference to the rest, same sign, on both disjoint samples).
- **Measurement:** `scripts/peer_breakout_breadth.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, paced history pages (three
  429s on the bot during the two fetches, none on the fetches). Last
  365 days, then days 366–1,095 as the independent check.
- **Result (E[R] net of costs):**

  | same-direction peers | last 365 d: n / E[R] / t / t_diff | prior 730 d: n / E[R] / t / t_diff |
  |---|---|---|
  | 0 (lone break) | 922 / +0.022 / +0.77 / +2.19 | 1,974 / +0.034 / +1.71 / +1.12 |
  | 1 | 1,210 / +0.027 / +1.04 / +2.80 | 2,503 / -0.002 / -0.10 / -1.14 |
  | 2 | 743 / **-0.073** / **-2.64** / -1.55 | 1,553 / **-0.055** / **-3.04** / **-4.11** |
  | >= 3 (crowded) | 1,619 / **-0.091** / **-4.78** / **-3.65** | 3,105 / **+0.051** / **+3.74** / **+3.12** |

  The hypothesis is reversed on the recent year (crowded breakouts are
  the worst bucket at t = -4.78) and the bucket qualifies under the
  rule there — then flips sign on the older sample, where it is the
  best bucket at t = +3.74. The crowded bucket is 60 % index trades: a
  class-wide index breakout was a short into a bull-market dip in the
  recent year and a long in a rising market before, so the feature
  reads the index regime of the sample (run 39's index shorts). The
  two-peer bucket is negative on both samples but a two-bad, three-good
  shape is not a mechanism and was not a preregistered split.
- **Decision:** not built in — sign flip on the qualifying bucket, the
  sharpest in this log. No code change, no restart. Section 116.
  Cross-sectional information has now been read three ways (relative
  strength, BTC lead, class breadth) and none carries.

## 2026-09-09 — passive limit entry at the signal close (entry half-spread saved)

- **Lever:** cost filter, entry side — runs 4 and 21 measured limit
  entries with the full round-trip spread charged on the passive fill
  and the fill counted on a mid-price touch; the half-spread a resting
  order saves (0.02–0.05 R here, the size of the book's expectancy) had
  never been modelled. Hypothesis: the saving outweighs the signals
  that never fill. Preregistered rule: built in only if the same
  validity (1 or 3 bars) beats the market entry on both disjoint
  samples in E[R] per trade at t > 2 and in total R per sample.
- **Measurement:** `scripts/passive_limit_entry.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24 from the
  fill bar, RR 1.5, stop 2.0 ATR, venue minimum, live widening rule,
  gap-aware stop booking, router-passed path, commodity short block,
  three live trend strategies, all 26 tradeable instruments. Fill only
  when the ask/bid reaches the limit (mid moves a half-spread beyond
  the close), gap-through fills at the open's ask/bid, cost = one
  half-spread, a stop hit inside the fill bar booked as a loss. Last
  365 days.
- **Result (E[R] net of costs):**

  | entry | signals | filled | fill % | E[R] | Σ R | R / signal | vs market / t |
  |---|---|---|---|---|---|---|---|
  | market at close | 4,560 | 4,499 | 98.7 | -0.033 | -148.9 | -0.033 | — |
  | limit, 1 bar | 4,688 | 4,353 | 92.9 | -0.039 | -169.0 | -0.036 | -0.006 / -0.33 |
  | limit, 3 bars | 4,635 | 4,411 | 95.2 | -0.039 | -173.7 | -0.038 | -0.006 / -0.36 |

  Behind on every count in all three strategies; the saving is paid for
  by the one signal in fourteen that runs without coming back and by
  the filled trade starting a bar later, half a spread nearer its stop.
- **Decision:** not built in — the conjunctive rule fails on the first
  sample by a wide margin, so the older sample was not run (the market
  entry's figures there are on record from runs 44 to 46). No code
  change, no restart. Section 117. The entry-cost side is closed as far
  as hourly bars can see it; the live fill gap of run 20 stays the open
  cost item.

## 2026-09-09 (second run) — live entry slippage after the forming-bar fix

- **Lever:** cost side, live — run 20 measured 0.128 R between signal
  and fill and run 22 removed the forming-bar cause; whether a gap
  remains had not been read. Slippage is near-deterministic per trade,
  so the entries since 2026-08-24 suffice.
- **Measurement (journal, read-only):** fill against journalled signal
  price in stop-distance units, positive = worse for the trade.

  | entries | n | mean | median | s.e. |
  |---|---:|---:|---:|---:|
  | before the fix (08-24 to 09-08 04:00 UTC) | 28 | +0.017 R | +0.011 R | 0.007 |
  | after the fix | 5 | +0.004 R | -0.001 R | 0.020 |

  Orders leave 2–40 s after the close; what remains is the half-spread
  the simulator already charges. The open cost item of run 21 is closed.
- **Decision:** nothing to change. No code change, no restart. Section 118.

## 2026-09-09 (third run) — re-entry after a timeout exit

- **Lever:** regime filter after an exit — run 16 built in the stop-out
  cooldown; the timeout (24 bars, neither level reached) is the other
  exit that leaves the instrument's regime in doubt. Hypothesis: a
  re-entry within 6 or 24 h of a timeout re-breaks into the same chop.
  Preregistered rule: blocked only if negative at t < -2 and worse than
  the rest at |t| > 2 on both disjoint samples.
- **Measurement:** `scripts/reentry_after_timeout.py` — merged
  one-position-per-instrument timeline, cost-charging walk-forward
  simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5, stop 2.0 ATR,
  venue minimum, live widening rule, gap-aware stop booking,
  router-passed path, commodity short block, live 6-h stop-out cooldown,
  three live trend strategies, all 26 tradeable instruments. Last 365
  days, then days 366–1,095.
- **Result (E[R] net of costs):**

  | previous exit, window | last 365 d: n / E[R] / t / t_diff | prior 730 d: n / E[R] / t / t_diff |
  |---|---|---|
  | timeout, <= 6 h | 214 / +0.019 / +0.38 / +1.06 | 503 / +0.007 / +0.25 / +0.25 |
  | timeout, <= 24 h | 448 / +0.014 / +0.41 / +1.45 | 941 / +0.020 / +0.92 / +0.98 |
  | target, <= 24 h (not preregistered) | 184 / -0.144 / -1.89 / -1.60 | 353 / -0.086 / -1.53 / -1.65 |

  Re-entries after a timeout are not worse on either sample — dead, and
  six entries in ten follow a timeout, so the cooldown would have
  removed most of the book for nothing. The re-entry within a day of a
  target exit is negative on both samples but misses both clauses of
  the bar twice; recorded, not to be re-run on the same data.
- **Decision:** not built in. No code change, no restart. Section 119.

## 2026-09-09 (fourth run) — dashboard PnL summed quote currency as dollars — FIXED

- **Lever:** accounting behind the gain figure — the dashboard showed
  +193.59 USD for the day after three stale exits the journal booked at
  +1.20 USD. Its closed-trade result is recomputed from exit and fill
  price (to correct legacy rows booked against the signal price) in the
  instrument's quote currency, so the yen trades counted 154× and HK50
  8× their dollar result. Run 9 had fixed the journal, not this path.
- **Measurement (journal, read-only):** journal `realized_pnl` against
  the dashboard expression for every close since 2026-09-07: USD
  instruments identical, HK50 ×7.8, CHFJPY ×153.7, AUDJPY ×153.6.
- **Decision:** fixed. `_PNL` in `generate_dashboard.py` reads
  `realized_pnl` for rows closed from 2026-09-07 23:05 UTC (the USD
  booking's live time) and keeps the price recomputation only for the
  legacy rows. Test `test_dashboard_pnl_currency.py` pins both branches
  and the fallback; the dashboard tests pass. Three failures in
  `test_runtime_guard_wiring.py` are pre-existing and unrelated: the
  wired stop-out cooldown reads the live journal, which holds today's
  COPPER stop-out, and the tests pass with the cooldown disabled and
  fail identically on the previous commit. Dashboard regenerated: today
  +1.20 USD, all-time -96.72 USD; the test URL serves the corrected
  page. No trading change, no restart (the dashboard loop picks the
  script up on its next 30-second pass). Section 120.

## 2026-09-09 (fifth run) — class-level risk tilt (halve FX risk)

- **Lever:** position sizing — the class tables of runs 44 to 46 read
  FX negative and crypto and commodities positive on both walk-forward
  samples. Candidate: halve the FX risk per trade, no limit loosened.
  Preregistered before reading the journal: built in only if FX is
  negative in the live journal too and below the rest at t < -2 on both
  walk-forward samples.
- **Measurement (journal, read-only):** 238 closed 1h-trend trades,
  realised R at the fill, by class.

  | class | walk-forward E[R] (365 d / prior 730 d) | journal n / E[R] / t_diff vs rest |
  |---|---|---|
  | fx | -0.021 / -0.018 | 26 / **+0.085** / +1.01 |
  | crypto | +0.094 / +0.032 | 86 / +0.032 / +0.59 |
  | index | -0.115 / +0.041 | 26 / -0.019 / +0.02 |
  | commodity | +0.033 / +0.011 | 100 / **-0.096** / -0.94 |

  The journal ranks the classes the other way round — FX best,
  commodities worst — in the whole sample and in the forward window;
  nothing is significant on either side. The first preregistered
  condition fails, so the walk-forward t-test was not run.
- **Decision:** not built in. No code change, no restart. Section 121.
  The class dimension is measured and unpredictive, like the instrument
  ranking (run 27).

## 2026-09-09 (sixth run) — re-entry after a target exit, on unselected samples

- **Lever:** regime filter after an exit — run 47's unpreregistered
  reading (re-entry within 24 h of a target exit -0.144 / -0.086 R on
  the two walk-forward samples, t_diff -1.6 each). Preregistered before
  running: a 24-h target-exit cooldown is built in only if the bucket is
  below the rest at t < -2 on the excluded instruments and the live
  journal agrees in sign.
- **Measurement:** `scripts/target_reentry_second_look.py` — (A) the 13
  excluded instruments with history, three years, merged one-position
  timeline, router-passed, 2-ATR stop, venue minimum, live widening
  rule, gap-aware booking, live 6-h stop-out cooldown, costs charged
  without the ceiling skip; (B) the live journal, 238 closed 1h-trend
  trades, flagged when the instrument had a target exit in the 24 h
  before the signal bar.

  | sample | n flagged | E[R] flagged | rest | diff / t |
  |---|---|---|---|---|
  | journal | 41 | -0.139 | +0.003 | -0.142 / -0.73 |
  | excluded instruments | 469 | -0.066 | -0.130 | **+0.063 / +1.30** |

  The journal agrees in sign, weakly; the excluded instruments point
  the other way. None of the four readings is significant.
- **Decision:** not built in — the first preregistered condition fails.
  No code change, no restart. Section 122. The exit-kind dimension is
  measured in full; only the stop-out cooldown stands.

## 2026-09-09 (seventh run) — 3-ATR stop with a 48-bar leash

- **Lever:** strategy parameters, cost mechanism — run 11 rejected the
  3-ATR stop at the 24-bar leash for its timeout share and run 13 found
  no better leash at 2 ATR; the combination 3.0 / 48 (a third less cost
  per R, time for the wider stop) was unmeasured. Preregistered: built
  in if net E[R] beats the live 2.0 / 24 on both samples, gross is not
  worse on either, and the timeout share is under 50 %.
- **Measurement:** `scripts/stop_hold_combo.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, RR 1.5, venue
  minimum, live widening rule, gap-aware booking, router-passed path,
  commodity short block, three live trend strategies, 26 instruments;
  combos 3.0 / 48, 3.0 / 36, 2.5 / 36 against 2.0 / 24.

  | stop / hold | last 365 d: net / gross / timeout % / Σ R / vs live, t | prior 730 d: same |
  |---|---|---|
  | 2.0 / 24 (live) | -0.033 / -0.016 / 61.1 / -147.3 / — | +0.016 / +0.034 / 64.1 / +142.9 / — |
  | 3.0 / 48 | -0.024 / -0.009 / 51.1 / -89.2 / +0.009, t 0.48 | +0.019 / +0.035 / 50.5 / +145.7 / +0.004, t 0.26 |

  Better net on both samples at t = 0.5 and 0.3, not worse gross,
  timeouts 51.1 % and 50.5 % against the 50 % clause — missed by a
  point on each sample, on a clause the live setting itself fails at
  61–64 %. Cost falls only 0.017 → 0.014 R because most stops sit on
  the venue's 1.05 % minimum regardless of the ATR multiple.
- **Decision:** not built in — third clause failed, and t ≈ 0.3–0.5 is
  not evidence; total R on the older sample is equal at 17 % fewer
  trades. No code change, no restart. Section 123. The stop-width lever
  is exhausted: the venue floor sets the cost of this book.

## 2026-09-09 (eighth run) — reward:risk by stop status (pinned vs ATR-bound)

- **Lever:** strategy parameter — after run 51 showed the stop on the
  venue floor on most trades, the RR was read separately for pinned
  trades (stop widened to 1.05 % of price) and ATR-bound trades.
  Preregistered: a different RR for pinned trades only if better than
  1.5 at t > 2 on both samples.
- **Measurement:** `scripts/rr_by_pin_status.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, stop
  2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path, commodity short block, three live trend
  strategies, 26 instruments, RR 1.0 / 1.5 / 2.0 / 2.5. Last 365 days.

  | group | share | mean stop | E[R] at RR 1.0 / 1.5 / 2.0 / 2.5 | best vs 1.5, t |
  |---|---|---|---|---|
  | pinned | 78 % | 6.5 ATR | -0.023 / -0.035 / -0.030 / -0.027 | +0.012, t 0.70 |
  | ATR-bound | 22 % | 2.0 ATR | -0.001 / -0.026 / -0.019 / -0.010 | +0.024, t 0.54 |

  No ordering in either group, nothing near the bar; the older sample
  was not run. Finding to keep: 78 % of trades are pinned at a mean
  stop of 6.5 ATR — the book trades a 1.05 % stop with a 1.6 % target
  on hourly bars, which is why 61 % of trades time out.
- **Decision:** not built in — 1.5 stays. No code change, no restart.
  Section 124.

## 2026-09-09 (ninth run) — holding leash by stop status (pinned vs ATR-bound)

- **Lever:** exit logic — after run 52 (78 % pinned at 6.5 ATR), the
  leash was read separately for pinned and ATR-bound trades.
  Preregistered: 48 bars for pinned trades only if better than 24 on
  that subset at t > 2 on both samples.
- **Measurement:** `scripts/hold_by_pin_status.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, RR 1.5, stop
  2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path, commodity short block, three live trend
  strategies, 26 instruments, holds 24 / 48 / 72 / 96. Last 365 days.

  | group | E[R] at 24 / 48 / 72 / 96 | timeout % at 24 → 96 | best vs 24, t |
  |---|---|---|---|
  | pinned (78 %) | -0.035 / -0.031 / -0.031 / -0.027 | 72 → 36 | +0.008, t 0.33 |
  | ATR-bound (22 %) | -0.026 / -0.072 / -0.073 / -0.069 | 23 → 1 | -0.043, t -0.83 |

  More time changes the pinned trades by a hundredth of an R and makes
  the ATR-bound trades worse; nothing near the bar, the older sample
  was not run.
- **Decision:** not built in — 24 stays. No code change, no restart.
  Section 125. The pinned trades need a volatility-scaled stop the
  venue does not offer, not more time.

## 2026-09-09 (tenth run) — the overnight fee

- **Lever:** cost filter — the venue's overnight financing, charged
  21:00 UTC on open positions, had never been read; the journal and
  simulator are price-based and blind to it. With 61 % of trades timing
  out at 24 bars nearly every trade crosses one rollover.
- **Measurement (read-only):** `scripts/overnight_fee_audit.py` — the
  account's transaction history (last 30 days) and the per-instrument
  overnight rates.

  | | |
  |---|---|
  | SWAP entries / sum | 70 position-nights / -0.51 EUR (0.007 EUR ≈ 0.003 R a night) |
  | TRADE closes / sum, same window | 24 / -4.26 EUR |
  | crypto long / index long / metal long, R per night at 250 USD notional | 0.051 / 0.014–0.018 / 0.013 |
  | crypto and metal shorts | credited (BTC, ETH shorts +0.03 EUR a night) |

  Twelve per cent of the month's realised result, 0.003 R a trade on
  the current FX-and-short-heavy book; material only for crypto longs
  (three spreads per night), which read about +0.01 / -0.03 R after
  financing on the two samples — not a block. A financing-aware cost
  ceiling would refuse nothing.
- **Decision:** not built in; recorded as the second known
  understatement of the gain figure (after run 10's currency mix). No
  code change, no restart. Section 126.

## 2026-09-09 (eleventh run) — the guards in practice, and the router's forward test

- **Lever:** regime filter / risk guards — what the live guards refuse
  (journal since 2026-08-24, read-only) and, for the router, the
  counterfactual of what it refused, which is the forward test its
  standing rule asks for (off only at passed − rejected < t -2).
- **Measurement:** journal rejection reasons; `scripts/router_forward_test.py`
  replays rejected and accepted intents alike from the 1h history with
  their journalled stop and target (24-bar hold, gap-aware, audited
  spread).

  | guard, since 2026-08-24 | refusals |
  |---|---:|
  | regime router | 246 (against 33 accepted) |
  | duplicate instrument signal | 19 |
  | stop below floor | 3 |
  | concurrent cap (8) | 2 |
  | size below broker minimum | 2 |

  | router forward test | n | E[R] | t | Σ R |
  |---|---|---|---|---|
  | accepted (traded) | 30 | -0.115 | -0.74 | -3.4 |
  | rejected | 197 | -0.177 | -3.28 | -34.9 |
  | passed − rejected | | +0.062 | +0.38 | |

  The cap does not bind (2 refusals), so signal prioritisation at the
  cap is not a lever. The router's rule is not met (t = +0.38) and it
  stays on; first forward reading with the right sign, on one bad
  fortnight, no power on the accepted side.
- **Decision:** nothing changed. No code change, no restart. Section 127.

## 2026-09-09 (twelfth run) — strategy agreement on the signal bar

- **Lever:** regime filter / entry quality — whether an entry confirmed
  by a second live strategy on the same bar carries more than a lone
  signal (section 5 read +0.108 vs -0.161 R on 44 live trades). The
  live duplicate guard already keeps one entry per instrument and bar.
  Preregistered: lone signals blocked only if t < -2 and |t_diff| > 2
  on both samples.
- **Measurement:** `scripts/strategy_agreement.py` — merged one-position
  timeline, cost-charging walk-forward simulator, capital_com, 1h, 3
  segments, hold 24, RR 1.5, stop 2.0 ATR, venue minimum, live widening
  rule, gap-aware booking, router-passed path, commodity short block,
  live stop-out cooldown, 26 instruments. Last 365 days.

  | strategies on the bar | share | E[R] | vs rest / t |
  |---|---|---|---|
  | 1 (lone) | 42 % | -0.035 | -0.010 / -0.26 |
  | 2 | 37 % | -0.043 | -0.022 / -0.56 |
  | 3 | 21 % | +0.007 | +0.046 / +1.00 |

  Flat; the first sample misses the bar and the second was not run.
  Three breakout definitions on one series agree by construction.
- **Decision:** not built in. No code change to trading, no restart.
  Section 128.
- **Operational:** the three failures in `test_runtime_guard_wiring.py`
  noted in the fourth run are fixed — the tests now patch the stop-out
  cooldown like the daily-loss guard instead of reading the live
  journal; full suite green (269 tests).

## 2026-09-09 (thirteenth run) — direction by instrument

- **Lever:** pair selection by side — the instrument-level cut of run
  39's class-level direction block, the only filter of the series that
  passed. Preregistered: block a side only with >= 100 trades on each
  sample, t < -2 on both, and long-short |t| > 2 same sign on both.
- **Measurement:** `scripts/direction_by_instrument.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path, commodity short block, three live trend
  strategies, 26 instruments, 52 instrument-sides, both samples
  (4,502 and 9,138 trades).
- **Result:** no cell qualifies. UK100 shorts are the only side negative
  at t < -2 on both samples (-0.298 / -0.165 R) but have 70 recent
  trades and a long-short difference of t = 1.31 there. The recent
  year's index shorts, EURAUD, AUDJPY and BTCUSD all flip between the
  samples with |t| > 2 on at least one side.
- **Decision:** nothing blocked; UK100 shorts recorded as a watch. No
  code change, no restart. Section 129. Direction is measured at class
  and instrument level.

## 2026-09-09 (fourteenth run) — does instrument expectancy transfer? (selector premise)

- **Lever:** pair selection — the nightly selector ranks by backtest
  expectancy; tested whether the prior sample's instrument ranking on
  the live path predicts the recent year, using run 55's trade dumps
  (no new venue load). Preregistered: drop the prior bottom quartile
  only if below the rest on the recent year at t < -2.
- **Measurement:** offline from `scripts/direction_by_instrument.py`
  dumps, 26 instruments, router-passed path, 2-ATR stop.

  | prior-sample selection | recent E[R] | rest | diff / t |
  |---|---|---|---|
  | bottom quartile (6) | -0.063 | -0.026 | -0.038 / -1.33 |
  | top quartile (6) | -0.056 | -0.025 | -0.031 / -0.99 |
  | prior-positive (15) vs prior-negative (11) | -0.031 | -0.036 | +0.006 / +0.24 |
  | Spearman prior → recent | rho -0.22 (p 0.28) | | |

  No transfer in either direction; GOLD the only instrument positive on
  both samples (run 17).
- **Decision:** nothing dropped, no code change, no restart. Section
  130. The selector's ranking is confirmed as non-predictive; it stays
  as the list of instruments that trade.

## 2026-09-09 (fifteenth run) — the expectancy by exit kind

- **Lever:** exit logic, diagnostic — where the book's E[R] comes from,
  split by target / stop / timeout on the live path, both samples
  (run 47's merged-timeline dumps; no venue load).
- **Measurement:**

  | exit | last 365 d: share / mean R / contribution | prior 730 d: same |
  |---|---|---|
  | target | 13.6 % / +1.47 / +0.200 | 13.1 % / +1.47 / +0.193 |
  | stop | 25.8 % / -1.03 / -0.264 | 23.0 % / -1.03 / -0.237 |
  | timeout | 60.7 % / **+0.055** (t 4.4) / +0.034 | 63.8 % / **+0.068** (t 7.6) / +0.044 |

  The barriers lose (-0.06 / -0.04 R per trade; the stop is hit twice
  as often as the target), the timeout drift wins on both samples at
  t > 4. The target (RR 1.0–3.0) and the stop (floor-pinned; 3 ATR
  flat) are already swept, so no parameter follows.
- **Decision:** nothing changed; recorded as the one structural
  statement that holds on both samples. No code change, no restart.
  Section 131. Live active list for the record: 69 combos on 30
  instruments (turtle 29, donchian 27, momentum 8, 4h variants 5),
  generated 2026-09-08 05:51 UTC.

## 2026-09-09 (sixteenth run) — time stop for losers

- **Lever:** exit logic — after run 59's split (stops cost 0.26 R a
  trade, the drift is positive), leave a trade under water at the close
  of bar K (6 / 12 / 18), keeping the hard stop and target.
  Preregistered: built in only if better than live at t > 2 on both
  samples.
- **Measurement:** `scripts/time_stop_losers.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path, commodity short block, three live trend
  strategies, 26 instruments. Last 365 days.

  | variant | net E[R] | Σ R | vs live, t |
  |---|---|---|---|
  | none (live) | -0.033 | -147.3 | — |
  | K = 6 | -0.043 | -219.2 | -0.010 / -0.64 |
  | K = 12 | -0.027 | -130.2 | +0.005 / +0.33 |
  | K = 18 | -0.032 | -145.4 | +0.001 / +0.06 |

  Flat to worse; the first sample misses the bar and the second run was
  stopped to spare the venue.
- **Decision:** not built in. No code change, no restart. Section 132.
  The exit side is swept in every form the book allows.

## 2026-09-09 (seventeenth run) — the gain figure against the broker

- **Lever:** accounting behind the gain figure — the journal's realised
  PnL reconciled per trade with the account's TRADE transactions (last
  30 days, matched by instrument and time; read-only).
- **Measurement:** `scripts/broker_reconciliation.py`.

  | | journal | broker | gap |
  |---|---|---|---|
  | 24 matched closes | -2.31 USD | -4.98 USD | +2.67 USD |
  | of which 4 pre-fix foreign-currency closes (AUDNZD, GBPAUD, GBPCAD, AU200) | | | +2.25 USD |
  | 20 other closes, incl. all since the USD booking fix | | | within ±0.04 USD each |

  The metric is right since 2026-09-07 23:01 UTC; the legacy currency
  rows (run 10) now have a broker-verified size; the overnight fee (run
  58) remains the one cost outside the journal.
- **Decision:** nothing changed in data or code; recorded. Section 133.

## 2026-09-09 (eighteenth run) — session of entry, third sample (journal)

- **Lever:** regime filter, time of day — run 2 dissolved on the second
  simulator sample; the live journal (238 trades) read as a third.
  Preregistered: a window blocked only at t < -2 here with the same
  sign on both simulator samples, which run 2 had already ruled out.
- **Measurement (journal, read-only):** realised R by UTC hour of the
  signal bar in four six-hour windows: -0.186 / -0.037 / -0.009 /
  +0.176 R, all |t| ≤ 1.4, different shape from run 2's first sample.
- **Decision:** nothing changed; the session dimension is closed on
  three samples. No code change, no restart. Section 134.

## 2026-09-09 (nineteenth run) — the venue floor re-read: 0.01 %, not 1 %

- **Lever:** stop logic / cost — the dealing rules' minStopOrProfitDistance
  is 0.01 in plain percent (max reads 100, guaranteed-stop minimum
  0.25); the code reads it as a fraction, so the 1.05 % floor that pins
  78 % of trades at 6.5 ATR is the project's own, and the journal holds
  broker-honoured stops at 0.18 % and 0.31 %. Preregistered: the venue's
  true floor with the designed 2-ATR stop is built in only if not worse
  than live on both samples.
- **Measurement:** `scripts/venue_floor_correction.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  live widening rule, gap-aware booking, router-passed path, commodity
  short block, three live trend strategies, 26 instruments; live floor
  vs true floor at 1 / 2 / 3 ATR, both samples.

  | floor / stop | last 365 d: net / cost / stop % / vs live, t | prior 730 d: same |
  |---|---|---|
  | 1.05 % / 2 ATR (live) | -0.033 / 0.017 / 26 / — | +0.016 / 0.018 / 22 / — |
  | 0.0105 % / 2 ATR | -0.065 / 0.034 / 51 / -0.033, t -1.64 | -0.018 / 0.036 / 51 / -0.034, t -2.45 |
  | 0.0105 % / 1 ATR | -0.072 / 0.059 / 60 / t -1.99 | -0.065 / 0.061 / 60 / t -5.92 |
  | 0.0105 % / 3 ATR | -0.031 / 0.023 / 36 / t +0.07 | +0.006 / 0.025 / 36 / t -0.78 |

  The accidental 1.05 % floor beats the designed stop on both samples:
  cost per R doubles and the positive timeout drift shrinks from 61 % of
  trades to 18 % when the stop is tightened to 2 ATR.
- **Decision:** the stop floor stays, now documented as the project's
  wide-stop setting rather than the venue's rule (docstrings in
  `strategy_parameters.py`, `capital_com.min_stop_distance`,
  `spot_backtest._venue_min_distance`); no behaviour change, tests
  unchanged and green. No restart. Section 135 corrects sections 19,
  23, 28, 123 and 124 on the cause.

## 2026-09-09 (twentieth run) — the stop floor as a parameter

- **Lever:** stop logic — with the 1.05 % floor established as the
  project's own setting (run 63), the floor itself swept for the first
  time: 1.5 / 2 / 3 % of price at 2 ATR. Preregistered: a wider floor
  built in only if better net on both samples, not worse gross on
  either, and t > 2 pooled; smallest passing floor chosen.
- **Measurement:** `scripts/stop_floor_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  live widening rule, gap-aware booking, router-passed path, commodity
  short block, three live trend strategies, 26 instruments, both samples.

  | floor | last 365 d: net / vs live, t | prior 730 d: net / vs live, t |
  |---|---|---|
  | 1.05 % (live) | -0.033 / — | +0.016 / — |
  | 1.5 % | -0.013 / +0.020, t 1.25 | +0.007 / -0.009, t -0.81 |
  | 2.0 % | -0.009 / +0.023, t 1.53 | +0.006 / -0.010, t -0.97 |
  | 3.0 % | -0.001 / +0.032, t 2.27 | +0.002 / -0.015, t -1.48 |

  Monotonic gain on the recent year, monotonic loss on the older; the
  first clause fails. With run 63 (narrower loses on both), 1.05 % is
  the value both samples accept.
- **Decision:** not built in — the floor stays at 1.05 %. No code
  change, no restart. Section 136.

## 2026-09-09 (twenty-first run) — the stop floor by asset class

- **Lever:** stop logic by class — run 64's floor sweep split by asset
  class from its dumps (no venue load). Preregistered: a class floor
  built in only if better net on both samples and t > 2 pooled.
- **Result:** the recent-year gain is the indices (3 %: +0.054 R,
  t 2.32) and reverses there on the older sample (-0.030 R, t -1.89),
  pooled t -0.21 — the index bull-year artefact of runs 39 and 46 once
  more. FX alone keeps the sign on both (+0.013 / +0.010 R at 3 %),
  pooled t 1.52; crypto and commodities are noise.
- **Decision:** not built in; the stop dimension is closed in every
  direction. No code change, no restart. Section 137.

## 2026-09-09 (twenty-second run) — pooled test of the wider floors, and the night's table

- **Lever:** stop floor, pooled clause of run 64 — floors 1.5 / 2 / 3 %
  against 1.05 % over both samples together (13,000 trades, from the
  sweep's dumps): +0.0006 / +0.0009 / +0.0009 R, t = 0.07 / 0.10 / 0.11.
- **Decision:** not built in; the stop dimension is closed. No code
  change, no restart. Section 138 tabulates the 25 levers of this night
  and what holds on both samples.

## 2026-09-09 (twenty-third run) — the unconditional drift after a router-passed signal

- **Lever:** exit logic, the barrier-less drift book implied by run 59 —
  read from run 64's 3 % floor variant (91 % timeouts) by class on both
  samples, no venue load. Preregistered: a lever only if positive at
  t > 2 on both samples.
- **Result:** all classes -0.001 / +0.002 R (t -0.09 / +0.32); FX
  negative on both at the size of its spread; indices and commodities
  flip; crypto +0.108 then +0.011 R. Section 131's timeout drift was
  survivorship.
- **Decision:** nothing changed. No code change, no restart. Section 139.

## 2026-09-09 (twenty-fourth run) — 3 ATR / 48 bars after the overnight fee

- **Lever:** the only candidate better net on both samples (run 51),
  re-read with run 58's financing (0.003 R a night; the 48-bar leash
  crosses a second rollover on timeouts).
- **Result:** difference after financing +0.007 R (recent) and
  +0.0015 R (prior), t 0.5 / 0.3 before financing; at the live book's
  two entries a day that is one to four cents a day.
- **Decision:** not built in; candidate closed. No code change, no
  restart. Section 140.

## 2026-09-09 (twenty-fifth run) — forward read of the built-in changes, and a currency mix in R

- **Lever:** forward reading of runs 11 (2-ATR stop) and 39 (commodity
  short block) from the journal — 9 trades opened since 2026-09-07, 6
  closed at -0.78 USD, no commodity short opened since the block; too
  few to read.
- **Found and fixed:** the forward report's R divided the USD-booked
  result by a quote-currency stop distance (yen trades 150× too small);
  the dashboard's projection return did the same over notional. Both
  now use price ratios, free of currency; test added; full suite green
  (270). Corrected forward expectancy since 2026-08-24: +0.012 R over 30
  closes. Veto and edge scaling were consistent already. Section 141.
- **Decision:** metric fix only; no trading change, no restart.

## 2026-09-09 (twenty-sixth run) — live spreads against the cost audit

- **Lever:** cost filter input — the heartbeat spread samples of run 38
  (554 samples, one day) against the static audit the cost filter and
  simulator use.
- **Result:** 24 of 27 audited instruments within 0.95–1.13 of the audit;
  DE40 1.34×, FR40 1.74×, UK100 2.06× at the sampled hours (off-hours
  widening, run 37). No filter decision changes (UK100's cost is 1.8 % of
  risk against a 10 % ceiling); CADJPY and USDJPY are unaudited.
- **Decision:** nothing changed; refresh the audit from a week of
  samples, not one day. No code change, no restart. Section 142.

## 2026-09-09 (twenty-seventh run) — exit decomposition on the live journal

- **Lever:** exit logic, forward read of run 59 on the 238 live 1h-trend
  trades (R from prices, no venue load).
- **Result:** targets 23.5 % at +1.27 R, stops 37.4 % at -1.04 R, stale
  exits 39.1 % at +0.065 R (t 0.92); barriers cost 0.09 R a trade, the
  stale exits carry — the simulator's shape. Forward since 2026-08-24:
  half the closes stale at +0.22 R, book +0.012 R over 30.
- **Decision:** nothing changed. No code change, no restart. Section 143.

## 2026-09-09 (twenty-eighth run) — target realisation on the journal's wins

- **Lever:** exit execution — live wins paid +1.27 R against the
  designed +1.5 R; measured on 52 journalled wins with full prices.
- **Result:** the broker closes at the target (88 % within 0.02 R); the
  gap is the entry slippage of the forming-bar era (+0.10 R on these
  wins), which puts the fill past the signal-anchored levels. Since the
  fix slippage is ≤ 0.017 R and forward wins average +1.44 R.
- **Decision:** nothing changed (re-anchoring levels to the fill would
  cost an order call per trade for a hundredth of an R). No code change,
  no restart. Section 144.

## 2026-09-09 (twenty-ninth run) — stop-exit slippage on the journal

- **Lever:** exit execution cost — 83 journalled stop exits, exit versus
  stop level in stop units.
- **Result:** median +0.004 R (at the stop); the +0.045 R mean is two
  gaps on ATOMUSD (1.77 R) and PALLADIUM (0.54 R), both instruments
  since blocked; without them +0.02 R. Two stop exits since the 2-ATR
  stop: +0.028 R.
- **Decision:** nothing changed. No code change, no restart. Section 145.

## 2026-09-09 (thirtieth run) — live expectancy per strategy

- **Lever:** strategy selection — realised R per strategy, currency-free,
  all closed trades (read-only).
- **Result:** donchian -0.045 (n 132, t -0.5), turtle -0.038 (n 68),
  momentum -0.035 (n 5); the retired mean-reversion names -0.17 to
  -0.42 R at t up to -2.9 carry the historical loss.
- **Decision:** nothing changed; no live strategy is significantly
  negative. No code change, no restart. Section 146.

## 2026-09-09 (thirty-first run) — direction by class in the live journal

- **Lever:** direction filter, forward read of runs 39 and 55 — 238 live
  1h-trend trades by side and class, R from prices (read-only).
- **Result:** commodity shorts -0.188 R (n 34, blocked since run 39),
  index shorts -0.193 R (n 15), nothing significant; book long-short
  difference t 0.09.
- **Decision:** nothing changed. No code change, no restart. Section 147.

## 2026-09-09 (thirty-second run) — the target on the live frequency

- **Lever:** position sizing — what the 50 EUR/day target requires at
  the live entry rate (2.05 a day since 2026-08-24, 2.44 USD risk at
  fill): 28.5 USD per trade, i.e. 950 USD of risk per trade at E[R]
  0.03 or 11.7 R per trade at the current risk.
- **Decision:** nothing changed — sizing up multiplies an expectancy of
  zero. No code change, no restart. Section 148.

## 2026-09-09 (thirty-third run) — the ADX slope on unselected samples

- **Lever:** regime filter — run 42's fading-ADX candidate (the last on
  the forward-test list) read on the excluded instruments and the live
  journal. Preregistered: veto built in only at t_diff < -2 on the
  excluded set with the journal agreeing in sign.
- **Measurement:** `scripts/adx_slope_second_look.py` — (A) 13 excluded
  instruments, three years, router-passed, costs charged without the
  ceiling skip; (B) 238 journal trades, R from prices.

  | sample | fading − rest / t |
  |---|---|
  | run 42, last 365 d / prior 730 d | -0.065 / -2.33 ; -0.042 / -2.19 |
  | excluded instruments | **+0.023 / +1.04** |
  | journal (forward) | -0.027 / -0.18 (+0.098 / +0.29) |

  Reversed on the excluded set; the first condition fails.
- **Decision:** not built in; the forward-test list is empty. No code
  change, no restart. Section 149.

## 2026-09-09 (thirty-fourth run) — GOLD on the journal

- **Lever:** pair selection — the one instrument positive on both
  walk-forward samples (runs 17 and 62), read on its 15 live trades.
- **Result:** -0.08 R a trade (donchian 2 at +0.43 R, turtle 13 at
  -1.66 R together) against +0.14 to +0.18 R simulated; t ≈ -0.3.
- **Decision:** nothing changed; GOLD stays as ranked, re-read at 50
  live trades. No code change, no restart. Section 150.

## 2026-09-09 (thirty-fifth run) — rollovers crossed per trade

- **Lever:** cost mechanism from run 58 — trades entered on the 21:00 UTC
  bar would pay two overnight fees in a 24-bar hold. Journal since
  2026-08-24, read-only.
- **Result:** 30 closes: 14 crossed no rollover, 9 one, none two by that
  mechanism (no entry on the 21:00 bar); seven crossed four to ten during
  the holiday pause and the 2026-08-30 outage, both since guarded.
- **Decision:** nothing changed. No code change, no restart. Section 151.

## 2026-09-09 (thirty-sixth run) — the power of the forward programme

- **Lever:** the forward tests the log defers to, sized — live R sd 0.96
  over 186 trend trades, 2.05 accepted entries a day (read-only).
- **Result:** t = 2 needs ≈ 970 trades for the router's +0.062 R (1.3
  years), ≈ 4,100 for a +0.03 R book expectancy (5.5 years), ≈ 370 for
  +0.10 R (half a year); 33 accepted trades exist since 2026-08-24.
- **Decision:** nothing changed; the forward thresholds stand as read
  points, not decision points. No code change, no restart. Section 152.

## 2026-09-09 (thirty-seventh run) — the router floor on the forward window

- **Lever:** regime filter — counterfactual floors 25 / 20 / none from
  run 59's replayed intents (no venue load), sixteen days since
  2026-08-24.
- **Result:** live floor 30: 1.9 entries a day at -0.115 R (-0.22 R a
  day); floor 25: 4.3 a day at -0.162 R (-0.70); floor 20: -1.18 R a
  day; no router: -2.64 R a day. The 25–30 band alone read -0.199 R
  over 39 intents.
- **Decision:** nothing changed — the floor stays at 30. No code change,
  no restart. Section 153.

## 2026-09-09 (thirty-eighth run) — stale-exit latency

- **Lever:** exit execution — trigger to confirmed close on the three
  stale exits of 2026-09-08: 71 / 73 / 71 s; about 0.02 % of price, a
  symmetric ±0.02 R jitter, not a cost. Open positions 23.0 / 21.0 /
  13.0 h, inside the leash.
- **Decision:** nothing changed. No code change, no restart. Section 154.

## 2026-09-09 (thirty-ninth run) — the projection tile's premise

- **Lever:** pair selection as the dashboard's projection assumes it —
  prior top-3 instruments by E[R] read on the recent year (run 62's
  dumps): GOLD +0.177, US100 -0.116, COPPER -0.158 R; pooled -0.054 R
  against the book's -0.033. The "119T" header is the 119-day span, not
  a defect.
- **Decision:** nothing changed; the tile stays labelled as an
  extrapolation. No code change, no restart. Section 155.

## 2026-09-09 (fortieth run) — book load at entry

- **Lever:** concurrent / cluster cap, forward face — R by positions
  open at entry on the 238 journal trades (read-only): 0–1 -0.046,
  2–3 +0.022, 4–5 -0.299 (t -2.0, t_diff -1.7, n 40), 6–7 +0.023.
- **Decision:** nothing changed; no ordering, one sample. No code
  change, no restart. Section 156.

## 2026-09-09 (forty-first run) — the nightly list's turnover

- **Lever:** pair selection — yesterday's active list against today's
  (05:30 UTC refresh): 69 → 68 combinations, 68 kept, one removed
  (GOLD / momentum), score Spearman 0.87 on the kept ones. The refresh
  caused 58 rate-limit errors on the bot's evaluations in 20 minutes.
- **Decision:** nothing changed; the refresh decides almost nothing and
  is not an operational blocker (one bar per hit at a quiet hour). A
  paced fetch in the selector is noted, not built. Section 157.

## 2026-09-09 (forty-second run) — the selector's score against live results

- **Lever:** pair selection — backtest expectancy per combination
  (today's list) against realised live R for the twelve combinations
  with ≥ 5 closed trades (read-only): Spearman +0.39 (p 0.21); BTCUSD
  agrees, GOLD and OIL_BRENT turtle reverse.
- **Decision:** nothing changed. No code change, no restart. Section 158.

## 2026-09-09 (forty-third run) — the leash on the live US30 close

- **Lever:** exit logic, live verification — US30 stale exit at 24.0 h,
  74 s to confirmation, +0.31 R (+0.71 USD); dashboard updated to
  +1.91 USD for the day, all-time -96.01 USD.
- **Decision:** nothing changed. No code change, no restart. Section 159.

## 2026-09-09 (forty-fourth run) — the pinned combinations

- **Lever:** pair selection — 29 pins read against backtest and
  journal: twenty negative in the backtest by design (cost-chosen
  universe), two negative live as well at n 8 (AUDUSD donchian -0.040,
  EURAUD turtle -0.131 R), none at the bar; the live veto already
  retires pins on realised results.
- **Decision:** nothing unpinned. No code change, no restart. Section 160.

## 2026-09-09 (forty-fifth run) — hours to the barriers, live

- **Lever:** exit logic, descriptive — stops hit at a median 3.0 h
  (50 % within 3 h, 95 % within 12 h), targets at 4.5 h (94 % within
  12 h), on 97 barrier exits since 2026-07-10 (read-only).
- **Decision:** nothing changed. No code change, no restart. Section 161.

## 2026-09-09 (forty-sixth run) — the live book by month

- **Lever:** stationarity check of the live book — E[R] per month May
  to September: +0.134 / +0.029 / -0.098 / -0.088 / -0.029 R, all
  |t| ≤ 1.04 (read-only). Keepalive and cron verified.
- **Decision:** nothing changed. No code change, no restart. Section 162.

## 2026-09-09 (forty-seventh run) — intent to trade conversion

- **Lever:** risk guards, in practice — since 2026-08-24: 310 intents,
  251 router-refused, 59 passed, 33 traded (56 %); the rest: 19
  duplicates, 3 stop floor, 2 concurrent cap, 2 minimum size (read-only).
- **Decision:** nothing changed — nothing refused is recoverable without
  touching a limit. No code change, no restart. Section 163.

## 2026-09-09 (forty-eighth run) — the structural refusals

- **Lever:** pair selection — the five non-duplicate refusals behind
  the router since 2026-08-24: three GOLD stop-floor refusals from
  before run 34's widening, two marginal minimum-size refusals (CHFJPY,
  HK50) that traded again since. No instrument is unreachable.
- **Decision:** nothing changed. No code change, no restart. Section 164.

## 2026-09-09 (forty-ninth run) — the daily figure's distribution

- **Lever:** the objective's metric itself — 62 calendar days since
  2026-07-10: -1.32 USD a day, sd 6.99, 32 % positive days, 95 % band
  ±13.7 USD; the 58.5 USD target is 8.6 daily standard deviations out
  (read-only).
- **Decision:** nothing changed. No code change, no restart. Section 165.

## 2026-09-09 (fiftieth run) — the notional cap on the forward window

- **Lever:** position sizing — fill risk 2.45 USD against 3.00, cap
  binding on 90 % of the 31 forward closes; rescaled to a uniform 3.00
  USD the window reads +2.02 USD instead of -1.96 (per-trade R +0.022,
  t ≈ 0.1); day sd would rise from 3.2 to 3.9 USD (read-only).
- **Decision:** cap stays — a limit is not loosened on thirty trades.
  No code change, no restart. Section 166.

## 2026-09-09 (fifty-first run) — the cap's tilt by class

- **Lever:** position sizing — fill risk per class on the 33 forward
  entries: FX 2.29, indices 2.37, commodities 2.52, crypto 2.69 USD; the
  cap binds on 75–100 % in every class, tilt 15 % (read-only).
- **Decision:** nothing changed. No code change, no restart. Section 167.

## 2026-09-09 (fifty-second run) — intents and trades by instrument

- **Lever:** pair selection, descriptive — 30 instruments with
  intents, 22 with a trade; top five carry 29 % of intents and 24 % of
  trades; router pass rate 0 % (USDCHF, J225, GBPUSD, DE40) to 53 %
  (COPPER) (read-only).
- **Decision:** nothing changed. No code change, no restart. Section 168.

## 2026-09-09 (fifty-third run) — ADX at the signal, all intents

- **Lever:** regime filter, descriptive — 308 intents: median ADX 21.0,
  45 % below 20, 19 % at or above 30; accepted median 36.4, rejected
  20.0; momentum's eight intents all under 30 (read-only).
- **Decision:** nothing changed. No code change, no restart. Section 169.

## 2026-09-09 (fifty-fourth run) — the rate limits and the signal window

- **Lever:** operational cost of the night's replays and the refresh —
  92 rate-limit hits, none in the five minutes after a bar close, 91
  mid-hour where the poll repeats (read-only).
- **Decision:** nothing changed. No code change, no restart. Section 170.

## 2026-09-09 (fifty-fifth run) — momentum's live contribution

- **Lever:** strategy selection — momentum: 13 intents since May, 5
  trades, none since 2026-08-24 (8 intents, all router-rejected at a
  median ADX of 17.9); seven ranked combinations and two 4h pins in
  the list; a third of the nightly refresh's load (read-only).
- **Decision:** nothing changed — run 3 keeps it on expectancy, and
  rarity is not a reason. No code change, no restart. Section 171.

## 2026-09-09 (fifty-sixth run) — entry slippage since the forming-bar fix, re-read

- **Lever:** cost side, live — all fills since 2026-09-08 04:00 UTC:
  still the five of run 27 (no entry since 2026-09-08 16:00 UTC), mean
  +0.004 R, median -0.001 R, orders 2–43 s after the close (median
  30 s). Section 118 stands unchanged.
- **Decision:** nothing changed. No code change, no restart.

## 2026-09-09 (fifty-seventh run) — volatility regime at the signal (ATR% rank)

- **Lever:** regime filter — the router gates on ADX only; the
  instrument's own volatility at the signal, relative to its last 30
  days, had never been read on the live book. Feature: percentile rank
  of ATR(14)/close at the signal bar against the 720 bars before it.
  Preregistered rule as in runs 40 to 44 (quartile bucket blocked only
  if t < -2 on both disjoint samples and |t| > 2 for the difference to
  the rest with the same sign on both; edges fixed on the recent year).
  After the recent year and before the older sample: "keep only the
  quietest quarter" (block rank >= 28.75) preregistered as a second
  candidate, same bar.
- **Measurement:** `scripts/atr_regime_gate.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, paced 35-day history pages
  (no 429 on the bot during either fetch). Last 365 days, then days
  366–1,095 as the independent check.
- **Result (E[R] net of costs):** recent year — quietest quarter
  +0.066 R (t = +2.62, t_diff = +4.52), third quarter -0.098 R
  (t = -3.86, t_diff = -2.89), upper three quarters -0.067 R
  (t = -4.39, t_diff = -4.52). Older sample — third quarter +0.027 R
  (t = +1.46), upper three quarters +0.017 R (t = +1.57), no bucket
  beyond 0.8 standard errors from the rest. Both candidates fail the
  first clause by a sign flip.
- **Decision:** not built in. No code change, no restart. Section 172.
  Volatility level joins the signal-bar features as a non-lever; the
  recent year's "quiet before the break" reading is the same regime
  signature as run 44's low 4h ADX, not an independent signal.

## 2026-09-09 (fifty-eighth run) — trend alignment at the signal (close vs EMA200)

- **Lever:** regime filter — the router gates on ADX strength, which
  is direction-free; whether the 1h breakout points the same way as
  the instrument's longer trend had never been read. Feature: signed
  distance of the signal close to the causal EMA(200) of 1h closes in
  ATR(14) units, in the signal's direction (negative = counter-trend).
  Preregistered before the data were seen: primary split aligned
  (>= 0) against counter-trend (< 0), quartile edges fixed on the
  recent year; a bucket is blocked only if t < -2 on both disjoint
  samples and |t| > 2 for the difference to the rest with the same
  sign on both (the bar of runs 40 to 44 and 57).
- **Measurement:** `scripts/htf_trend_alignment.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, paced 35-day history pages.
  Last 365 days (n = 4,378), then days 366–1,095 (n = 9,008) as the
  independent check; edges 2.47 / 5.12 / 7.54 ATR.
- **Result (E[R] net of costs):** counter-trend (11–12 % of trades)
  -0.048 R (t = -1.37, t_diff = -0.57) on the recent year and -0.029 R
  (t = -1.17, t_diff = -2.00) on the older sample; deep counter-trend
  (< -1 ATR) -0.065 R (t = -1.54) then -0.055 R (t = -1.91,
  t_diff = -2.63); lowest quartile -0.015 R (t_diff = +0.65) then
  -0.032 R (t_diff = -3.36), a sign flip. Extended breakouts (>= 3 ATR)
  -0.041 R (t = -2.67) on the recent year and +0.032 R (t = +3.10) on
  the older one — the early-versus-late regime signature of runs 44
  and 57 again. The candidate fails the first clause on both samples;
  no bucket qualifies.
- **Decision:** not built in. No code change, no restart. Section 173.
  Trend alignment joins the signal-bar features as a non-lever; the
  consistent small loss of the counter-trend trades (one in nine) is
  recorded for a later read on the live journal.

## 2026-09-09 (fifty-ninth run) — half out at +1 R (partial take-profit)

- **Lever:** exit logic — the live exit is all-or-nothing at the 1.5 R
  target; sections 131 and 143 (barriers lose, the drift wins) asked
  whether banking half of the position at +1 R and letting the rest
  run changes the expectancy. Two variants: A, remainder unchanged
  (stop and 1.5 R target); B, remainder's target at 2.5 R. Acceptance
  as in the exit series: better than the live rule at paired t > 2 on
  both disjoint samples.
- **Measurement:** `scripts/partial_exit.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, paced 35-day history
  pages. All three rules booked on the same bar path of every trade,
  differences paired per trade. Last 365 days (n = 4,501), then days
  366–1,095 (n = 9,153) as the independent check.
- **Result (E[R] net of costs, diff vs live, paired t):** recent
  year — A +0.0066 R (t = +2.24), B +0.0083 R (t = +2.03), uniform
  across the strategies; older sample — A -0.0066 R (t = -3.77),
  B -0.0058 R (t = -2.15), again on all three strategies. One trade in
  four reaches +1 R on either sample; the win rate rises about one
  point under either rule. A sign flip between the samples: the rule
  moves money from the trades that reach 1.5 R to the ones that turn
  back, and which group is larger depends on the year.
- **Decision:** not built in — fails the bar on the older sample. The
  exit stays all-or-nothing at 1.5 R. No code change, no restart.
  Section 174.

## 2026-09-09 (sixtieth run) — the weekday of the signal (calendar split)

- **Lever:** regime filter — the weekday of the 1h signal bar, the one
  calendar axis section 7 had refused to open and section 51 had not
  covered (session window). Opened exactly once, preregistered: a
  weekday is blocked only if t < -2 on both disjoint samples and
  |t| > 2 for the difference to the rest with the same sign on both.
- **Measurement:** `scripts/weekday_split.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, paced 35-day history
  pages. Last 365 days (n = 4,503), then days 366–1,095 (n = 9,160) as
  the independent check. Weekend buckets are the crypto pairs only.
- **Result (E[R] net of costs):** recent year — Sunday -0.268 R
  (t = -4.46, t_diff = -4.06, all three strategies), weekend -0.240 R
  (t = -4.23), Wednesday -0.066 R (t = -2.50), Thursday -0.063 R
  (t = -2.52), Tuesday +0.060 R (t_diff = +3.54). Older sample —
  Sunday +0.030 R (t = +0.44), Saturday +0.334 R (t = +2.94), Thursday
  +0.090 R (t = +4.58, all three strategies), Wednesday -0.007 R
  (t = -0.40), Monday the worst day (t_diff = -2.51). Every standout
  day flips or vanishes between the samples; no bucket passes the
  first clause on both.
- **Decision:** not built in — the calendar axis dissolves like the
  session window did. No code change, no restart. Section 175.
  Section 7's refusal to search calendar buckets stands as the rule.

## 2026-09-10 — the variance ratio of the prior month (return autocorrelation regime)

- **Lever:** regime filter — the router gates on ADX, a 14-bar
  directional statistic; the month-scale autocorrelation of the
  instrument's own returns had never been read. Feature: Lo-MacKinlay
  variance ratio VR(24) of the 1h log returns over the 720 bars
  before the signal (VR > 1 trending, VR < 1 mean-reverting).
  Preregistered: primary split VR < 1 against VR >= 1, quartile edges
  fixed on the recent year, block only at t < -2 on both disjoint
  samples and |t| > 2 for the difference to the rest with the same
  sign on both.
- **Measurement:** `scripts/variance_ratio_gate.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments. Last 365 days
  (n = 3,962), then days 366–1,095 (n = 8,618). Then, because the sign
  held on both, a third sample: `scripts/variance_ratio_journal.py`,
  the same VR reconstructed at the signal bar of the 236 closed live
  trades of the three strategies, realised R at the actual fill.
- **Result (E[R] net of costs):** history — signals after a
  mean-reverting month better than the rest on both samples, the
  first feature in the series with a stable sign: trending half
  -0.054 R (t = -2.34, t_diff = -0.93) then -0.014 R (t = -0.94,
  t_diff = -2.79); lowest quartile vs rest t_diff = +2.85 then +2.12.
  Fails the bar on both samples by opposite clauses; the only cut that
  clears the difference clause twice would drop three trades in four
  and the dropped ones are profitable on the older sample (+53 R).
  Live journal — the sign reverses: VR < 1 -0.129 R (154 trades),
  VR >= 1 +0.177 R (82 trades), t_diff = -2.23 against the history's
  direction; the 33 forward trades lean the same way.
- **Decision:** not built in. No code change, no restart. Section 176.
  Section 115's pattern again: two history samples agree, the live
  book disagrees. The variance ratio joins the non-levers.

## 2026-09-10 (second run) — spread mean reversion between related instruments

- **Lever:** new signal source — a relative-value family, never opened
  in section 7's structural test: z-score (240 bars) of the log ratio
  of two related instruments, entry on crossing |z| = 2, target z = 0,
  stop |z| = 3.5, leash 48 bars, nine spreads (Brent/WTI, gold/silver,
  US500/US100, US500/US30, DE40/EU50, DE40/FR40, EURUSD/GBPUSD,
  AUDUSD/NZDUSD, BTC/ETH). Both legs charged their audited round-trip
  spread, live 10 % cost ceiling applied. Preregistered acceptance:
  pooled E[R] > 0 at t > 2 on both disjoint samples.
- **Measurement:** `scripts/pairs_spread_mr.py` on the 1h history,
  capital_com, paced 35-day pages. Last 365 days (323 trades, 170
  cost-skipped signals), then days 366–1,095 (597 trades, 462
  cost-skipped).
- **Result (E[R] net of both legs' costs):** pooled -0.118 R
  (t = -1.69) on the recent year and -0.062 R (t = -1.23) on the older
  sample; 72–73 % of trades end at the leash; no spread positive on
  both samples (BTC/ETH +0.13 R then -0.35 R at t = -2.77; DE40/FR40
  -0.43 R at t = -2.08 then +0.09 R). A third to nearly half of the
  signals cannot pay two spreads inside the cost ceiling.
- **Decision:** not built in — negative on both samples. No code
  change, no restart. Section 177. The relative-value family joins
  section 7's structural sources as tested and negative.

## 2026-09-10 (third run) — market breadth at the signal, and the client-sentiment sampler — SAMPLER BUILT IN

- **Lever:** regime filter — directional market breadth at the 1h
  signal: the mean over the other 25 instruments of sign(close −
  EMA200), carried forward to the signal bar, in the signal's
  direction. Neither peer confirmation (same class, run 46) nor trend
  alignment (own instrument, run 58) had read the market as a whole.
  Preregistered: primary split breadth ≥ 0 against < 0, quartile edges
  fixed on the recent year, block only at t < -2 on both disjoint
  samples and |t| > 2 for the difference to the rest with the same
  sign on both.
- **Measurement:** `scripts/market_breadth_gate.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments. Last 365 days (n = 4,398),
  then days 366–1,095 (n = 9,049); edges -0.12 / 0.20 / 0.44.
- **Result (E[R] net of costs):** recent year — with the market
  (top quartile, breadth ≥ 0.44) -0.109 R (t = -4.91, t_diff = -4.39,
  all three strategies), against the market +0.023 R
  (t_diff = +2.97). Older sample — the same top quartile +0.056 R
  (t = +3.53, t_diff = +3.13, all three strategies), against the
  market -0.017 R (t_diff = -2.81). Every cut flips sign between the
  samples; no bucket passes the first clause on both.
- **Decision:** not built in. Section 178. The fourth regime feature
  to reverse between the samples (runs 44, 46, 57, 61).
- **Built in alongside (data collection, no trading change):** the
  price history's features are exhausted, so the heartbeat now records
  the venue's client positioning per instrument (long percentage from
  `GET /api/v1/clientsentiment`) into `data/sentiment_samples.jsonl`,
  next to the spread samples of run 38 — `_sample_sentiment` in
  `app/spot_trading/autotrade.py`, one batched request per heartbeat,
  fail-silent, covered by `tests/test_sentiment_sampler.py`. Nothing
  reads it yet; the split is to be measured on the journal once
  enough trades carry a sample. Hurz restarted for the sampler.

## 2026-09-10 (fourth run) — index entries priced at their hour (the open cost correction of run 37)

- **Lever:** cost filter — block off-hours index entries, now priced
  at the venue's real spread for the hour they fire, from the
  two-day spread-by-hour table the heartbeat sampler of run 38 has
  built (the correction run 37 left open). Preregistered: block only
  at t < -2 on both disjoint samples and |t| > 2 against the
  cash-hours rest with the same sign on both.
- **Measurement:** `scripts/index_hour_costs.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, three live trend strategies, the nine
  index instruments, every trade charged the median sampled
  half-spread of its signal hour. Last 365 days (n = 1,660), then
  days 366–1,095 (n = 3,416).
- **Result (E[R] net of the hour's cost):** the hour table raises the
  index book's cost from 1.0 % to 1.5 % of risk (0.0045 R per trade,
  8 R and 15 R on the two samples — half of run 37's one-snapshot
  estimate). Off-hours entries -0.109 R (t = -4.01) against cash-hours
  -0.119 R on the recent year (t_diff = +0.24) and +0.041 R
  (t = +2.16) against +0.031 R on the older (t_diff = +0.35): not the
  losing side on either sample, positive on the older one. The index
  book flips with the regime (run 20 / section 137), at any hour.
- **Decision:** not built in — the block fails the bar on the older
  sample. Section 179. Run 37's open correction is closed as
  measured: half a percent of risk, too small to change a ranking;
  the shared simulator keeps the daytime table. No code change, no
  restart.

## 2026-09-10 (fifth run) — close-confirmed stop against the live touch stop

- **Lever:** exit logic — the one stop variant never measured: close
  the position at the bar's close only if that close lies beyond the
  2-ATR level, instead of the broker-side touch stop. A keeps a
  broker-side catastrophe stop at 3 ATR on touch; B has no touch stop
  (upper bound). Target intrabar 1.5 R, leash 24 bars. Preregistered
  acceptance: better than the live rule at paired t > 2 on both
  disjoint samples; both variants weaken the 1-R loss limit, which was
  part of the reading from the start.
- **Measurement:** `scripts/close_based_stop.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, three rules booked on the
  same bar path, differences paired per trade. Last 365 days
  (n = 4,503), then days 366–1,095 (n = 9,156).
- **Result (E[R] net of costs, diff vs live, paired t):** A +0.0023 R
  (t = +0.53) then -0.0000 R (t = -0.01); B +0.0016 R (t = +0.34) then
  +0.0008 R (t = +0.27); strategies disagree on the sign in both
  samples. Stop-outs fall from 26 % to 21 % of trades and the win rate
  rises 1.5 points, but 12–14 % of trades then lose more than 1 R
  (worst -1.6 R under A, -4.4 R under B) against 0.1–0.4 % live.
- **Decision:** not built in — no expectancy gain and a weaker risk
  limit. The stop stays broker-side on touch at 2 ATR. No code change,
  no restart. Section 180.

## 2026-09-10 (sixth run) — two-bar confirmation (enter only if the next close still holds)

- **Lever:** entry timing — enter at the close of the bar after the
  signal, and only if that close still lies beyond the level the
  signal broke; otherwise no trade. Neither the pullback entry
  (run 4), the retest (run 6) nor the next-open timing (run 21 of
  2026-09-08) had asked this. Preregistered acceptance: better than
  the live rule at paired t > 2 on both disjoint samples.
- **Measurement:** `scripts/two_bar_confirmation.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware stop
  booking, router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments; every signal's live R set
  against the variant's R (zero where the variant does not trade) and
  compared paired. Last 365 days (n = 4,521), then days 366–1,095
  (n = 9,159). Third sample: `scripts/two_bar_confirmation_journal.py`
  on the 216 closed live trades, realised R at the actual fill.
- **Result:** the split is the strongest the series has measured —
  unconfirmed signals (29–30 % of the book) lose 0.291 R and 0.217 R
  at t = -14.6 and -15.1, on all three strategies, while the confirmed
  remainder makes +0.067 R and +0.113 R. The rule that acts on it
  keeps almost none: skipping the unconfirmed adds +354 R and +568 R,
  entering the confirmed a bar late costs -279 R and -482 R, leaving
  paired +0.0168 R (t = +1.95) and +0.0094 R (t = +1.54). Journal:
  +0.048 R paired at t = +0.76; the 31 forward trades -0.102 R.
- **Decision:** not built in — fails the bar on both samples, and
  pooling them (t = +2.38) is the test this log refuses. No code
  change, no restart. Section 181. The decomposition is kept: the
  loss sits in the one-bar-old failed breakout, and the next question
  is whether the confirmation works as an exit instead of an entry
  filter — measured separately, not assumed.

## 2026-09-10 (seventh run) — the failed-breakout exit (the confirmation as an exit)

- **Lever:** exit logic — the other half of run 66: keep the entry
  where the live rule has it and close the position at the close of
  the bar after the signal if that close lies back inside the level.
  A cuts every unconfirmed trade, B only a losing one. Preregistered
  acceptance: better than the live rule at paired t > 2 on both
  disjoint samples. Neither variant weakens the 1 R loss limit — both
  exit strictly earlier than the stop.
- **Measurement:** `scripts/failed_breakout_exit.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments, three rules on the same
  bar path, differences paired per trade. Last 365 days (n = 4,528),
  then days 366–1,095 (n = 9,156).
- **Result:** B is A — every unconfirmed close is a losing one, so the
  clause never fires. Paired +0.0172 R (t = +3.17) on the recent year
  and -0.0023 R (t = -0.55) on the older one. The cut books -0.209 R
  and -0.207 R, the same toll in both regimes on 28–29 % of the book;
  what it saves is +0.062 R per cut trade on the recent year and
  -0.008 R on the older. A third of the cut trades (32 % and 36 %)
  would have ended positive at +0.54 R and +0.62 R, and the win rate
  falls nine points on both samples.
- **Decision:** not built in — the sign flips and the bar fails on the
  older sample. No code change, no restart. Section 182. With run 66
  this closes the confirmation from both sides: the split is real and
  stable, no rule that acts on it survives two samples.

## 2026-09-10 (eighth run) — the ATR lookback behind the stop (the grid's last unswept axis)

- **Lever:** strategy parameter — the stop is 2 x ATR(14) and the 14
  had never been swept (run 9 did the same for the Donchian length).
  Swept at 7 / 14 / 28 / 56 for the stop distance only; signals
  unchanged, so the Keltner band keeps its own ATR(14). Preregistered
  acceptance: better than 14 at paired t > 2 on both disjoint samples.
- **Measurement:** `scripts/atr_period_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments; four variants on the same
  bar path, differences paired per trade. Last 365 days (n = 4,488),
  then days 366–1,095 (n = 9,138).
- **Result:** flat. Widest paired difference 0.0036 R at t = +0.90;
  period 56 is best on the recent year (+0.0036) and worst on the
  older (-0.0027), period 7 the reverse. The reason is mechanical: the
  1.05 % venue floor is wider than 2 ATR on 76–85 % of trades, so the
  ATR sets the stop on only a fifth of the book, and the share rises
  with the lookback.
- **Decision:** not built in — period 14 stays. No code change, no
  restart. Section 183. The grid's last unswept axis is closed; the
  finding to carry is that this system's stop is a fixed 1.05 % of
  price on most trades, not a volatility-adaptive distance.

## 2026-09-10 (ninth run) — removing the target (the barrier the RR sweep never reached)

- **Lever:** exit logic — section 131 measured the barrier pair as
  losing and the 24-bar drift as the only black component, then closed
  the target question by inference from the RR 1.0-3.0 sweep. The rule
  it actually implies is no target at all, and that variant had never
  been booked. Measured against 3.0 R and 6.0 R on the same path. The
  stop is untouched in all four, so the 1 R loss limit stands and no
  risk control is loosened. Preregistered acceptance: better than the
  live rule at paired t > 2 on both disjoint samples.
- **Measurement:** `scripts/no_target_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, stop
  2.0 ATR, venue minimum, live widening rule, gap-aware stop booking,
  router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments; occupancy on the live
  variant so the trade set is identical, differences paired per trade.
  Last 365 days (n = 4,524), then days 366–1,095 (n = 9,161).
- **Result:** the sign flips. No target reads -0.0092 R at t = -1.09 on
  the recent year and +0.0058 R at t = +0.95 on the older one, and per
  strategy all three lose on the first sample and all three gain on the
  second, no cell above t = 1.4. 6 R and none are the same rule — the
  barrier is reached on 0-1 % of trades — so the RR sweep had already
  priced the far end without naming it. The effect is confined to the
  13 % of trades that live exits at the target: about -0.07 R each on
  the recent year, +0.045 R each on the older. The stop share moves
  only 26→27 and 22→23 %, so a runner does not walk back into the stop,
  it walks into the 24-bar leash.
- **Decision:** not built in — the target stays. No code change, no
  restart. Section 184. Section 131's decomposition survives, but the
  drift being the black component does not make it worth harvesting
  past 1.5 R; the target question is now measured rather than inferred.

## 2026-09-10 (tenth run) — a volatility-anchored target (section 124's open sentence)

- **Lever:** exit logic — section 124 closed with the observation that
  the pinned trades (78 % of the book, mean stop 6.5 ATR) need a
  volatility-scaled distance the venue does not offer on the stop. It
  offers it on the target. Targets at 1.5 / 3.0 / 4.5 ATR against the
  live 1.5 x stop, clamped to the venue's minimum distance because a
  closer target is not placeable — the constraint section 124's
  group-wise RR sweep did not carry. The stop is untouched in all four
  variants, so the 1 R loss limit stands and the target can only move
  closer. Preregistered acceptance: better than the live rule at paired
  t > 2 on both disjoint samples.
- **Measurement:** `scripts/atr_target_sweep.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, stop
  2.0 ATR, venue minimum, live widening rule, gap-aware stop booking,
  router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments; occupancy on the live
  variant so the trade set is identical, differences paired per trade.
  Last 365 days (n = 4,521), then days 366–1,095 (n = 9,169).
- **Result:** the first variant in the series to clear t = 2 — in both
  directions. The 1.5 ATR target reads +0.0193 R at t = +2.93 on the
  recent year and -0.0114 R at t = -2.85 on the older one, and all
  three strategies gain on the first sample and lose on the second.
  The shape of the trade is unambiguous: target exits double from 13 to
  26 %, the win rate rises five points to just over 50, timeouts fall
  from 61 to 51 %, and the stop share barely moves (26 → 23 %) — win
  size traded for win frequency at a realised RR just under 1.0,
  because the venue floor clamps the target. The 4.5 ATR control lands
  within 0.0006 R of the live rule on both samples, as it should.
- **Decision:** not built in — the bar asks for both samples and the
  sign flips. No code change, no restart. Section 185. What this run
  hands the next one is a lever that is real but conditional: five
  points of hit rate on the target distance, paying in the trending
  sample and not in the recent one, which makes the regime router —
  not an unconditional target change — the place to test it.

## 2026-09-10 (eleventh run) — the near target by ADX (section 185's conditional candidate)

- **Lever:** regime filter on the exit — section 185 found the near
  target worth five points of win rate on both samples but positive
  expectancy on one only, and the candidate explanation was trend
  strength: a target at the venue floor caps runners, so it should pay
  where they do not run. Preregistered before the data were seen: the
  paired difference must show the same bucket gradient on both samples
  before a threshold rule is built; thresholds ADX < 35 / 40 / 45 then
  booked as their own variants at paired t > 2 on both samples. Stop
  untouched throughout, the 1 R loss limit stands.
- **Measurement:** `scripts/target_by_adx.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, stop
  2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments; live and near target on the
  same bar path, difference paired per trade, split by ADX at entry.
  Last 365 days (n = 4,530), then days 366–1,095 (n = 9,171).
- **Result:** the precondition fails outright. On the recent year all
  five buckets are positive (+0.003 to +0.053 R), on the older sample
  four of five are negative (-0.004 to -0.019 R). Within a sample the
  near target points the same way at every trend strength; the sample
  decides the direction. The threshold rules inherit the flip — ADX<45
  reads +0.0125 R at t = +2.11 and -0.0105 R at t = -3.03. Two
  findings against expectation: the recent year's advantage *grows*
  with ADX, the opposite of the capping story, and the reason sits in
  the live column — the live rule's expectancy collapses from -0.008 to
  -0.144 R as ADX rises on the recent year and shows no ADX ordering at
  all on the older one (+0.018 to -0.016 R).
- **Decision:** not built in — no threshold rule was booked, because
  the gate that had to open first stayed shut. No code change, no
  restart. Section 186. What section 185 read as a conditional target
  effect is a high-ADX entry problem confined to one sample; the next
  question is that entry, not the target.

## 2026-09-10 (twelfth run) — an upper ADX bound on trend entries

- **Lever:** regime filter — the router gates trend entries at ADX >= 30
  and has no ceiling; section 186 found the live expectancy falling
  monotonically with ADX on the recent year (-0.008 R below 35 to
  -0.144 R above 50). Ceilings at 35 / 40 / 45 / 50. The hypothesis
  came from data already seen, so the acceptance rule was written out
  first and made asymmetric — a filter removes trades rather than
  adding exposure, and one that helps in one regime and is neutral in
  the other cannot show t > 2 twice by construction. Required: (a)
  positive point estimate on both samples, (b) t > 2 on at least one,
  (c) no reading below t = -1 on either, (d) sign stable across the
  sweep, (e) no contradiction from the journal.
- **Measurement:** `scripts/adx_ceiling_filter.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path, commodity short block, three live trend
  strategies, all 26 tradeable instruments; every signal's live R
  against the variant's R, zero where it does not trade, paired per
  trade, occupancy on the live rule so a filtered trade does not free
  its slot. Last 365 days (n = 4,530), then days 366–1,095 (n = 9,169).
  Journal cross-read on the 34 closed trades carrying an entry ADX.
- **Result:** the strongest single-sample effect the series has
  produced, and one sample wide. Recent year: +0.0336 / +0.0253 /
  +0.0161 / +0.0108 R at t = +3.97 / +4.04 / +3.39 / +2.82, monotone in
  severity, cut trades worth -0.07 to -0.14 R each. Older sample: -0.0025
  / +0.0003 / -0.0001 / +0.0013 R, every |t| <= 0.46 — not harmful,
  simply nothing, with the sign decided by noise at two thresholds each
  way. Per strategy the aggregate's older zero is a cancellation, not
  homogeneity: turtle_breakout, the only strategy with positive older
  expectancy (+0.030 R), reads negative at every threshold and its cut
  trades are its good ones (+0.027 to +0.046 R). Journal: |t| <= 0.46
  at every threshold on n = 34, too small to count either way.
- **Decision:** not built in — (b), (c) and (e) pass, (a) and (d) fail
  together: choosing the two thresholds whose older sign happens to be
  positive is the cherry-pick (d) was written to forbid. No code
  change, no restart. Section 187. Measured rather than suspected:
  high-ADX trend entries were the recent year's worst segment by a wide
  margin and carried no penalty in the two years before it — a
  statement about the last twelve months, not about the system.

## 2026-09-10 (thirteenth run) — the ADX ceiling on a third sample — BUILT IN

- **Lever:** regime filter — section 187 refused the upper ADX bound
  because the middle sample's sign was noise, leaving the choice of
  threshold to a cherry-pick. The venue serves hourly history past
  three years, so days 1,096–1,825 are available as a third disjoint
  sample never read for this question. Decision rule fixed before the
  fetch: build only if the third sample is positive at every threshold,
  reaches t > 2 at least once, and the cut segment carries E[R] <= 0 on
  all three samples; ship the mildest threshold holding its sign on all
  three, not the best t.
- **Measurement:** `scripts/adx_ceiling_filter.py`, unchanged from run
  12, on days 1,096–1,825 (n = 9,500, live E[R] -0.0230).

  | ceiling | 365 d: cut E[R] / diff / t | 366–1,095 d | 1,096–1,825 d |
  |---|---|---|---|
  | ADX<35 | -0.0709 / +0.0336 / +3.97 | +0.0051 / -0.0025 / -0.41 | -0.0314 / +0.0151 / +2.42 |
  | ADX<40 | -0.1074 / +0.0253 / +4.04 | -0.0010 / +0.0003 / +0.06 | -0.0333 / +0.0083 / +1.83 |
  | ADX<45 | -0.1241 / +0.0161 / +3.39 | +0.0004 / -0.0001 / -0.02 | -0.0650 / +0.0087 / +2.58 |
  | **ADX<50** | -0.1431 / +0.0108 / +2.82 | -0.0158 / +0.0013 / +0.46 | -0.0348 / +0.0026 / +1.02 |

- **Result:** all four conditions met at ADX < 50, and only there and at
  ADX < 40. The third sample is positive throughout and clears t = 2
  twice; the cut-segment condition eliminates 35 and 45, whose middle
  sample cuts profitable trades (+0.0051 and +0.0004 R); of the two
  survivors 50 is the milder, refusing 7–8 % of signals against 25 %.
  R sums improve on every sample: -171.0 → -122.2 (365 d), +111.3 →
  +122.8 (730 d), -218.6 → -193.7 (730 d).
- **Decision:** built in. `HURZ_REGIME_ADX_TREND_MAX`, default 50,
  enforced in `regime.decide()` — the single function both
  `autotrade.py` and `spot_backtest.py` call, so live and simulator
  cannot diverge. Trend signals at or above the ceiling return blocked
  with regime `overextended`; mean-reversion, neutral strategies and the
  fail-closed path for a missing ADX are untouched, and no risk control
  is loosened since the rule only removes entries. Verified: 56/56 tests
  green plus four new ones, and on 140 days of live history across three
  instruments and three strategies the router now passes exactly
  30 <= ADX < 50 (375 signals, none at either side). Hurz restarted.
  Section 188. Honest limit: the recent year still reads -0.0270 R a
  trade with the ceiling against -0.0377 without — a losing year made
  less losing, not a winning one.

## 2026-09-10 (fourteenth run) — the pin degree as an exclusion

- **Lever:** entry filter on the stop geometry — a trade pinned at 10 ATR
  carries its 1.5 R target 15 ATR away, unreachable inside a 24-bar
  leash, so it should only stop out or time out near zero while paying
  full spread. Structural rather than regime-dependent, hence a filter
  candidate. Caps at 4 / 6 / 8 / 10 ATR. Acceptance identical to run 13:
  positive point estimate on all three disjoint samples, t > 2 on at
  least one, cut segment E[R] <= 0 on all three, ship the mildest
  qualifying threshold. The stop itself is untouched — the rule only
  removes entries.
- **Measurement:** `scripts/pin_degree_filter.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path (now including run 13's ADX ceiling), commodity
  short block, three live trend strategies, all 26 tradeable
  instruments; paired per trade, occupancy on the live rule. Samples:
  365 d (n = 4,254), 366–1,095 d (n = 8,541), 1,096–1,825 d (n = 8,885).
- **Result:** rejected on condition (c) alone — on both older samples
  every cut segment is positive (+0.004 to +0.053 R), so the rule would
  discard profitable trades. No threshold is positive on more than one
  sample and none reaches t > 2 positively. The bucket table explains
  why and points the opposite way: the worst segment on all three
  samples is the *least* pinned one, the 2–3 ATR band where the venue
  floor barely binds, at -0.060 / -0.011 / -0.078 R (t -2.16 / -0.47 /
  -4.22). A wide stop cannot reach its target, but noise cannot drag it
  to the stop either; what is left is the drift.
- **Decision:** not built in — no cap. No code change, no restart.
  Section 189. Side benefit: this is the first measurement against the
  post-run-13 system and it verifies the build — the recent year reads
  -122.4 R where run 13 predicted -122.2, the residual being occupancy
  the filtered trades hand back. The least-pinned band is the next
  run's question.

## 2026-09-10 (fifteenth run) — the least-pinned band — BUILT IN

- **Lever:** entry filter on stop geometry — run 14's bucket table
  showed the worst segment is the least pinned one, the 2–3 ATR band
  where volatility alone clears the venue's 1.05 % minimum. Those three
  samples produced the hypothesis, so a fourth was fetched for the first
  time (days 1,826–2,555; the venue serves hourly history past seven
  years). Acceptance fixed before the fetch, run 13's rule extended by
  one sample: positive on all four, t > 2 on at least one, cut segment
  E[R] <= 0 on all four, ship the mildest qualifying floor.
- **Measurement:** `scripts/pin_floor_filter.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path including run 13's ADX ceiling, commodity short
  block, three live trend strategies, all 26 tradeable instruments;
  paired per trade, occupancy on the live rule. Floors 2.5 / 3.0 / 3.5 /
  4.0 ATR on n = 4,249 / 8,534 / 8,884 / 8,895.

  | floor | 365 d | 366–1,095 d | 1,096–1,825 d | 1,826–2,555 d (unseen) |
  |---|---|---|---|---|
  | 2.5 | +0.0237 / +2.67 | +0.0067 / +1.17 | +0.0286 / +4.48 | **-0.0021** / -0.31 |
  | **3.0** | +0.0216 / +2.23 | +0.0031 / +0.48 | +0.0301 / +4.24 | +0.0019 / +0.26 |
  | 3.5 | +0.0173 / +1.69 | **-0.0045** / -0.66 | +0.0277 / +3.66 | -0.0016 / -0.20 |
  | 4.0 | +0.0196 / +1.83 | **-0.0060** / -0.83 | +0.0264 / +3.34 | -0.0018 / -0.23 |

- **Result:** exactly one threshold survives. At 3.0 ATR every sample is
  positive, two clear t = 2, and the cut segment is negative on all four
  (-0.062 / -0.011 / -0.079 / -0.005 R). The neighbours each fail on a
  different sample — 2.5 leaves part of the bad band in, 3.5 and 4.0 cut
  into the good one. The fourth sample is much weaker than the other
  three (+0.0019 at t = +0.26), so it establishes direction, not size.
  Concentration check: crypto supplies 34–47 % of the cut but dilutes
  rather than carries the effect — the filter is worth +0.049 / +0.005 /
  +0.043 / +0.010 R a trade excluding crypto and -0.022 / -0.001 /
  +0.028 / -0.038 on crypto alone; it ships unconditionally, since a
  crypto exemption would be a threshold chosen after seeing the split.
  R sums: -124.8 → -32.9, +124.3 → +150.5, -209.7 → +57.7, +11.7 → +28.9.
- **Decision:** built in. `HURZ_MIN_STOP_ATR_MULTIPLE`, default 3.0, in
  `evaluate_pair` beside the existing 1 % price floor and at the matching
  point in `spot_backtest._simulate_trades`, so live and simulator refuse
  the same signals. No risk control is loosened — the rule removes
  entries and never widens a stop. Verified: 57/57 tests green including
  seven new ones, and on 140 days of live history no signal under
  3.01 ATR survives the path. Four existing tests had 1×ATR fixtures
  that the floor would have caught; they now disable it explicitly,
  since gap booking, trailing, sizing and the price floor are their
  subjects, not this. Hurz restarted. Section 190. Honest cost: the
  floor refuses 29–38 % of router-passed signals, the largest cut in
  trade count the project has made. The 1,096–1,825 sample crosses from
  a heavy loss to a profit; the recent year only loses less.

## 2026-09-10 (sixteenth run) — extension at the breakout (RSI in the signal's direction)

- **Lever:** entry filter — RSI(14) sits beside the ADX and ATR in
  `add_indicators` and had never been read as an entry segment. Read in
  the signal's direction so "high" means extended (RSI for longs,
  100 - RSI for shorts); refusal at 70 / 75 / 80 / 85. Hypothesis a
  priori rather than mined from these samples, so all four count
  equally. Acceptance identical to runs 13 and 15: positive on all four,
  t > 2 on at least one, cut segment E[R] <= 0 on all four, ship the
  mildest qualifying threshold. Stop untouched; the rule only removes
  entries.
- **Measurement:** `scripts/rsi_extension_filter.py` — cost-charging
  walk-forward simulator, capital_com, 1h, 3 segments, hold 24, RR 1.5,
  stop 2.0 ATR, venue minimum, live widening rule, gap-aware booking,
  router-passed path including the ADX ceiling, the 3×ATR floor
  replicated from `_min_stop_atr_multiple()`, commodity short block,
  three live trend strategies, all 26 tradeable instruments; paired per
  trade, occupancy on the live rule. n = 2,869 / 6,258 / 5,777 / 5,637.
- **Result:** rejected at every threshold on both (a) and (c). Only the
  recent year is positive (+0.0238 / +0.0140 / +0.0143 / +0.0067); all
  three older samples are negative at every threshold, and every cut
  segment there is positive, so the rule would discard winners in three
  years out of four. The 80+ band — the most extended entries, ~30 % of
  the book — returns +0.013 / +0.017 / +0.034 R on the older samples and
  -0.045 R at t = -2.14 on the recent one. Buying an already-run
  breakout was the profitable half of this system for three years and
  its worst segment in the last one.
- **Decision:** not built in. No code change, no restart. Section 191.
  Side benefit, and the real value of this run: measured against the
  post-run-15 system, the live baseline is a clean out-of-sample read on
  what shipped today — E[R] moves from -0.0377 to -0.0216 (365 d),
  +0.0134 to +0.0229, -0.0230 to **+0.0118**, +0.0013 to +0.0098. The two
  builds improve every sample and flip 1,096–1,825 d positive; three of
  four samples now read positive. The recent year still does not, and
  four different entry characteristics have now shown the same shape —
  at some point the shape is the finding: the last twelve months are a
  different regime, not a weaker version of the three before them.

## 2026-09-10 (seventeenth run) — instrument consistency across three samples — BUILT IN

- **Lever:** pair selection — section 130 tested whether instrument
  expectancy transfers using one prior sample against one following it
  and found none (Spearman -0.22). With four samples available a
  stricter form is possible: three disjoint training samples must ALL
  read negative before an instrument is flagged, the most recent year
  held out entirely. Flagged three of 24: AUDUSD, GBPCAD, GBPUSD —
  exactly the chance count under no transfer (24 × 0.5³ = 3.0), so the
  acceptance rule was written to let the held-out year decide alone:
  paired t > 2 on the test sample, or nothing is blocked. Training
  agreement is negative by construction and was not counted.
- **Measurement:** `scripts/instrument_consistency_block.py` — flags
  derived offline from run 16's per-instrument tables, then the block
  booked on the test sample by the cost-charging walk-forward simulator
  against the current system (ADX ceiling in `gate()`, 3×ATR floor),
  every signal's live R against the variant's R, zero where it does not
  trade, paired per trade. n = 2,870.
- **Result:** paired **+0.0163 R at t = +5.22**. The three read -0.0963 R
  at t = -5.34 on data that did not select them, against -0.0063 at
  t = -0.45 for the other 21; each is significant alone (AUDUSD -0.155
  / t -3.77, GBPUSD -0.084 / t -3.23, GBPCAD -0.054 / t -2.16). They are
  17 % of the year's trades and 76 % of its loss: the year's sum goes
  from -61.8 R to -14.9 R, and the remaining 21 instruments are flat
  rather than losing. Method check — the same rule reversed in time and
  run middle-out points the same way (-0.006 vs +0.019 and -0.014 vs
  +0.032) and flags the same three plus others; only the three the
  preregistered forward rule produced are blocked. Live journal,
  outside the test: 23.67 USD of the book's 239.57 USD all-time loss
  sits in these three over 28 trades, AUDUSD alone -27.98 over 15.
- **Decision:** built in. Added to `EXPECTANCY_BLOCKED_PAIRS` beside
  AU200, consulted by the entry guard in `evaluate_pair`, the order
  guard in `execute_intent` and the nightly selector through
  `BLOCKED_PAIRS`. No open position affected — none of the three was
  open — and the guards refuse entries only. Verified: 58/58 tests green
  including four new ones. Hurz restarted. Section 192. Section 130's
  conclusion stands unamended: one prior sample does not predict the
  next; three agreeing ones do.

## 2026-09-10 (eighteenth run) — the consistency rule at combination granularity

- **Lever:** pair selection — run 17's three-sample consistency rule
  applied one level down, at instrument-strategy granularity, which is
  what the nightly selector actually ranks. Acceptance fixed before the
  test sample was read: paired t > 2 on the held-out recent year; the
  three instruments blocked in run 17 excluded from the universe so any
  effect is additional; minimum 30 trades per training sample; the same
  rule reversed in time must agree.
- **Measurement:** `scripts/combo_consistency_block.py` — per-trade
  (pair, strategy, direction, R) dumped for all four samples by the
  cost-charging walk-forward simulator against the current system (ADX
  ceiling, 3×ATR floor, run 17's block list), so flagging and test come
  from the same booking. n = 2,384 / 5,272 / 4,802 / 4,679.
- **Result:** rejected, and it points the wrong way. Of 51 combinations
  with 30+ trades per training sample, three read negative in all three
  — against a chance expectation of 6.4, so the rule finds fewer
  consistent losers than randomness does. On the held-out year those
  three return **+0.0134 R** against -0.0075 for the rest; paired
  **-0.0009 R at t = -0.42**. All three arrangements agree: forward
  -0.0009 / t -0.42, backward -0.0045 / t -1.57, middle-out +0.0003 /
  t +0.10, and in two of three the flagged combinations beat the rest.
  Combination expectancy is not positively autocorrelated across
  samples; an instrument losing in three regimes is plausibly a bad
  instrument, a strategy-on-instrument losing in three regimes carries
  no such information.
- **Decision:** not built in — expectancy blocks stay at instrument
  level, where run 17 put them. No code change, no restart. Section 193.
  Side benefit: these dumps are the first read of the system with all
  three of today's builds in place. E[R] moves from -0.0377 to -0.0062
  (365 d), +0.0134 to +0.0322 at t +3.36, -0.0230 to +0.0215 at t +2.02,
  +0.0013 to +0.0169 at t +1.75; R sums -171.0 → -14.8, +122.8 → +169.8,
  -218.6 → **+103.4**, +11.7 → +79.2. Every sample improves, two now
  clear t = 2 positive where none did this morning, and the recent year
  is within one standard error of zero instead of three. Cost: 47 % of
  the recent year's trades. Where that leaves the objective: three of
  four years profitable, the fourth about flat — at 3 USD risk per trade
  that is 2 to 4 USD a day on the older samples and roughly zero on the
  recent one. The remaining gap to 50 EUR is risk per trade against a
  positive expectancy, not another entry filter, and the expectancy has
  to hold forward first.

## 2026-09-11 — the 1 % price floor, revisited at a positive expectancy

- **Lever:** cost filter / frequency — the floor's own comment named the
  condition for reopening it ("revisit only once expectancy is positive —
  at that point the floor becomes the single largest constraint on
  frequency", removing it "multiplies volume roughly fifteenfold"). After
  runs 13, 15 and 17 that condition holds on three of four samples, and
  the daily objective is trades-per-day × E[R] × risk. This lever ADDS
  trades, so the acceptance rule gained a term: positive on all four,
  t > 2 on at least one, AND the added trades carrying E[R] >= 0 on all
  four, shipping the most conservative qualifying floor. Risk per trade
  unchanged — sizing targets a fixed dollar risk, so a narrower stop
  takes a larger position for the same loss at the stop.
- **Measurement:** `scripts/stop_floor_revisit.py` — floors 1.00 / 0.75 /
  0.50 / 0.25 % and off, occupancy resolved per floor so the frequency
  change is modelled rather than held fixed on the live variant; against
  the current system (ADX ceiling, 3×ATR floor, run 17's block list). All
  four samples, n = 2,385 / 5,272 / 4,803 / 4,679.
- **Result:** the same line five times. **Zero trades added at any level
  on any sample.** The floor cannot fire: `_min_stop_fraction()` is
  1.00 % of price, `VENUE_MIN_STOP_FRACTION` is 1.05 %, and the floor is
  checked after the venue expansion, so every stop reaching the check has
  already been widened past it. The comment's fifteenfold figure was the
  magnitude of the ordering bug fixed since (floor checked *before*
  expansion, see `StopFloorOrderTest`), not of the floor — a stale note
  promising a lever its own fix had removed.
- **Decision:** no lever here; the floor stays as the fail-closed
  backstop for a zero expansion distance, and its comment is corrected to
  say what it does. One regression test added (58/58 green). No
  behavioural change, so no restart. Section 194. Frequency on the recent
  year, in guard order: 4,530 → ~4,189 (ADX ceiling, -341) → 2,869 (3×ATR
  floor, -1,320) → 2,384 (instrument block, -485). The volatility floor is
  the largest cut by a factor of four and the one that bought a measured
  expectancy improvement on four samples. There is no free frequency to
  recover — every remaining cut was paid for with expectancy. Further
  daily gain has to come from risk per trade against the positive
  expectancy the three older samples now show, and that multiplication is
  only worth making once it holds forward on the live book.

## 2026-09-11 (second run) — what the risk budget can deliver

- **Lever:** position sizing — run 19 closed the frequency question, so
  risk per trade is what remains. `edge_scaling.py` governs it and states
  the premise itself: the daily target "needs roughly ten times" the 3 USD
  base, but size has to be earned. This run measures whether the gate can
  fire and what it would pay if it did. Read-only measurement; no risk
  limit was touched, and nothing here argues for touching one.
- **Measurement:** `assess_edge()` on the live out-of-sample book plus
  closed-form reachability against the expectancies runs 13–18 established
  (+0.032 / +0.022 / +0.017 R), and the account balance read from the
  broker (557.35 EUR).

  | | |
  |---|---|
  | live out-of-sample sample | 37 trades, mean +0.0421 R, sd 0.839, se 0.138 |
  | lower 2σ bound | **-0.234** — gate shut; needs mean > +0.276 at n=37 |
  | trades for a positive 2σ bound at +0.032 R | 2,750 (~917 days at 3/day) |
  | at +0.017 R | 9,858 (~3,286 days) |
  | 1 % equity cap at 557 EUR | 6.02 USD = **2.01x** base — the 10x multiple never binds |
  | target 50 EUR/day on 557 EUR equity | 8.97 % of equity per day |
  | risk needed at +0.032 R, 3 trades/day | 559 USD = 92.9 % of equity per trade |
  | ceiling of the current configuration | ~0.6 USD ≈ 0.54 EUR a day |

- **Result:** the gate cannot open on any relevant horizon — a
  two-to-three hundredth R edge against a 0.84 R per-trade deviation
  needs thousands of trades at two sigma — and behind it the equity cap
  binds at 2x, not 10x. The objective is a factor of ninety away from
  what the configuration can produce, and the factor is the product of
  account size, per-trade risk and edge, none of which an entry filter,
  exit rule or parameter sweep touches. Raising risk to close it would
  put 93 % of the account behind every position.
- **Decision:** nothing loosened. Two stale claims corrected in
  `edge_scaling.py`'s docstring — that ten times the base is what the
  target needs (the equity cap allows two) and the implicit suggestion
  that the gate is waiting on a few more trades (it is waiting on
  thousands) — with the measured figures in their place, plus one
  regression test that the equity cap binds before the 10x multiple.
  58/58 green. No behavioural change, no restart. Section 195. What today
  did achieve stands and is the precondition for any sizing decision:
  three of four disjoint samples now positive where none was this
  morning. It is not 50 EUR a day, and the three quantities that could
  become that are the balance, the edge and the trade count — in that
  order of leverage.

## 2026-09-11 (third run) — the consistency rule on the direction axis

- **Lever:** pair selection / direction — run 17's three-sample rule works
  on instruments and run 18 showed it fails at instrument-strategy
  granularity. The third axis is instrument × direction, where a
  structural story is easy to tell and the project already keeps a
  hand-built `SHORT_BLOCKED_PAIRS`. Acceptance as before: paired t > 2 on
  the held-out recent year, minimum 30 trades per training sample, and the
  reversed time direction must agree.
- **Measurement:** computed from run 18's per-trade dumps (pair,
  strategy, direction, R) — no new venue load, same booking, current
  system throughout. 34 (pair, direction) cells qualified.
- **Result:** rejected. Three cells flagged against a chance expectation
  of 4.2; on the held-out year they return **+0.0347 R** against -0.0104
  for the rest, paired **-0.0032 at t = -1.19**. All three arrangements
  agree: forward -0.0032 / t -1.19, backward +0.0002 / t +0.17,
  middle-out +0.0016 / t +0.63. Direction pooled is equally unusable —
  longs lead on three samples (+0.023, +0.045, +0.024) and shorts lead
  decisively on the third (+0.0429 at t +2.59), so no global rule holds.
- **Decision:** not built in; the existing commodity short block stands on
  its own evidence and is untouched. No code change, no restart. Section
  196. The mechanism is now explained rather than just observed: the three
  axes divide the same trades into cells of ~150-700 (instrument) versus
  ~30-80 (combination, direction) trades, and since a cell mean's standard
  error scales with 1/√n, only instrument cells are large enough for three
  samples to agree about anything but sampling error. Flag counts confirm
  it — at or below chance on both finer axes, above it only for
  instruments. The segmentation programme is closed.

## 2026-09-11 (fourth run) — the router's ADX floor, re-measured

- **Lever:** regime filter / frequency — the floor at 30 is the largest
  single constraint on trade count, and its last examination (section
  153) used sixteen days of replayed intents and predates all three of
  2026-09-10's builds. Runs 19 and 20 had narrowed the daily-gain
  arithmetic to the frequency term, so the floor was the remaining
  candidate. This lever ADDS trades, so acceptance carried the extra
  term: positive on all four samples, t > 2 on at least one, added
  trades at E[R] >= 0 on all four, ship the most conservative
  qualifying floor.
- **Measurement:** `scripts/router_floor_revisit.py` — floors 30 / 27.5 /
  25 / 22.5 / 20, both `HURZ_REGIME_ADX_TREND` and the core override set
  per variant, occupancy resolved per floor, ADX ceiling + volatility
  floor + block list all in force. n at the live floor = 2,386 / 5,271 /
  4,806 / 4,679.

  | floor | 365 d ΔΣR | 366–1,095 d | 1,096–1,825 d | 1,826–2,555 d |
  |---|---|---|---|---|
  | 27.5 | +10.9 | -41.0 | -30.3 | -33.7 |
  | 25 | +8.8 | -126.4 | -82.6 | -84.6 |
  | 22.5 | -20.2 | -142.9 | -149.3 | -80.2 |
  | 20 | +16.5 | -71.5 | -108.3 | +1.8 |

- **Result:** rejected at every level on both (a) and (c). On the three
  older samples 30 is the best floor and every reduction costs, growing
  monotonically to 22.5 before partly recovering at 20; the added trades
  read -0.002 to -0.019 R there. Only the recent year would gain, and
  that is the sample whose sign has reversed on every lever measured
  today. Section 153's conclusion confirmed on evidence forty-five times
  larger.
- **Decision:** not changed — the floor stays at 30. No code change, no
  restart. Section 197. This closes the frequency term: every guard
  between signal and order that has now been measured on four samples
  holds back trades that lose. There is no configuration of this system
  that trades more often *and* earns more per trade.
- **Operational note:** 13 rate-limit (429) responses appeared in the bot
  log during today's measurement runs, individual pairs skipped for one
  cycle each; the heartbeat kept scanning all 68 pairs throughout and no
  entry or exit was missed. Cause is this session's own history fetching,
  not a defect. Future measurement batches should raise PAGE_PAUSE or run
  fewer windows back-to-back while the bot is live.

## 2026-09-11 (fifth run) — the leash as a throughput lever (R per calendar day)

- **Lever:** exit logic / throughput — every earlier leash sweep measured
  E[R] per trade, but the objective is gain per day and a position holds
  its slot for the whole leash: at the cap of 8, 24 bars allows 8
  openings a day, 12 bars 16. A shorter leash at lower per-trade
  expectancy could still pay more per day. Last open term of the
  daily-gain arithmetic after runs 19, 20 and 22. Acceptance fixed
  first: sum R per calendar day higher than live on all four samples,
  paired daily difference at t > 2 on at least one, per-trade expectancy
  not negative on any sample, ship the leash closest to 24. Risk per
  trade and the stop unchanged.
- **Measurement:** `scripts/hold_throughput.py` — merged book as section
  132 requires: all three strategies on one chronological timeline, one
  open position per instrument, concurrent cap 8 enforced, ADX ceiling +
  3×ATR floor + block list in force; sum R keyed by exit date over the
  calendar span. Leashes 6 / 12 / 24 / 48.

  | leash | 365 d | 366–1,095 d | 1,096–1,825 d | 1,826–2,555 d |
  |---|---|---|---|---|
  | 6 | -0.0758 | -0.0091 | -0.0200 | -0.0480 |
  | 12 | -0.0339 | +0.0250 | -0.0171 | +0.0070 |
  | **24 (live)** | **-0.0302** | **+0.0718** | **+0.0347** | **+0.0382** |
  | 48 | +0.0391 | +0.0568 | +0.0514 | +0.0500 |

- **Result:** rejected. 6 bars loses on all four, 12 bars loses on all
  four, 48 gains on three and loses 0.0149 on the second sample; nothing
  near t = 2. The reason is section 131's finding: the only positive
  component is the drift of trades reaching neither barrier, and that
  drift accrues *during* the leash. Shortening it lifts frequency (2.8 →
  4.6 trades a day) but cuts the mechanism that makes a trade positive,
  and expectancy falls faster than count rises (+0.024 → -0.002 R on
  sample 2). Time is not overhead in this system; it is the product.
- **Decision:** not changed — 24 bars stays. No code change, no restart.
  Section 198. Two things this run establishes beyond the lever: the
  merged simulation opens 2.84 trades a day against 3.3 realised in the
  live journal, the first independent check that this project's per-day
  arithmetic is not fiction; and read in dollars at 3 USD risk, the three
  older samples give +0.22, +0.10 and +0.11 USD a day, the recent year
  -0.09 — the same ceiling section 195 derived from the account side,
  reached independently from the throughput side.

## 2026-09-11 (sixth run) — strategy priority on a contested bar

- **Lever:** strategy parameter / selection — the duplicate guard gives one
  position per instrument (section 132) and today the winner is decided by
  iteration order. Ranked over the three older samples turtle leads and
  keltner trails, so same-bar contests were re-resolved as
  turtle > donchian > keltner, with the recent year held out. Scope limit
  stated first: only same-bar contests can be re-ordered, since preferring
  a later signal needs information the moment lacks. Acceptance: sum R per
  calendar day higher on all four samples, paired daily t > 2 on at least
  one, recent year not made worse.
- **Measurement:** `scripts/strategy_priority.py` — merged book, one
  position per instrument, concurrent cap 8, ADX ceiling + 3×ATR floor +
  block list in force, sum R keyed by exit date.

  | sample | contested slots | current | ranked | diff / t |
  |---|---|---|---|---|
  | 365 d | 55.7 % | -0.0401 | -0.0457 | -0.0056 / -0.84 |
  | 366–1,095 d | 57.6 % | +0.0721 | +0.0744 | +0.0023 / +0.75 |
  | 1,096–1,825 d | 58.6 % | +0.0399 | +0.0374 | -0.0025 / -0.69 |
  | 1,826–2,555 d | 59.2 % | +0.0406 | +0.0392 | -0.0014 / -0.99 |

- **Result:** rejected — positive on one sample, nothing near t = 2. The
  finding is in the mix: under the live order donchian takes 716 of 1,039
  trades on the recent year and turtle sixteen, so turtle is crowded out
  despite ranking first on three samples. Reversing priority moves turtle
  to 526 and donchian to 207 — about half the book changes hands — and
  per-trade expectancy shifts by two thousandths of an R (-0.0141 →
  -0.0161; +0.0239 → +0.0246). Trades that interchangeable are the same
  trades: three channel-breakout rules on the same instruments at the same
  hour, differing mainly in which arrives first. The per-strategy
  expectancy gaps that motivated the run are composition artefacts.
- **Decision:** not changed — router order stays. No code change, no
  restart. Section 199. This explains two earlier results instead of just
  recording a third: section 193's combination cells failed because they
  are near-duplicates, not only because they are small, and section 132's
  "five signals in six would pyramid" is the same overlap seen from the
  entry side. The choice of which rule to trade is not a lever, because
  there is effectively one rule.

## 2026-09-11 (seventh run) — the scaling cutoff and a metric that can see the builds

- **Lever:** position sizing, correctness rather than gain — `edge_scaling`
  excludes pre-cutoff trades because the entry filters were calibrated on
  them. The cutoff stood at 2026-08-24, but the three filters that now
  trade were calibrated on four walk-forward samples whose most recent
  window is the last 365 days, which contains the whole live journal. The
  37 trades the gate had accumulated are therefore in-sample for the
  system that now trades. Stated plainly: this is not a gain lever, and it
  is recorded as bookkeeping.
- **Measurement:** `assess_edge()` under both cutoffs — 37 qualifying
  trades at 2026-08-24, 0 at 2026-09-10. Section 195 had already
  established the gate needs 2,750–9,858 trades to open, so the 37 were
  never near the threshold and nothing real is lost.
- **Decision:** cutoff moved to 2026-09-10, sample restarts at zero,
  removing the possibility of scaling risk on evidence generated by a
  different filter set. No behavioural change to trading, so no restart
  needed — but the running process reads the constant at import, so the
  restart below applies it.
- **Second item, dashboard:** the project rule requires the daily gain to
  be visible and no current figure can show these builds — the 7- and
  30-day rates average trading days from before them, all-time averages
  121 days, and with zero entries since (weekend) the 30-day rate of
  -0.88 USD/day is frozen describing the previous system. Added one card
  row keyed to `_FILTER_EPOCH`: realised PnL and per-day rate over entries
  accepted since the builds, showing "noch kein Trade" while that set is
  empty rather than a misleading 0.00. Verified: dashboard regenerated,
  row renders, 0644, 58/58 tests green plus four new ones.

## 2026-09-11 (eighth run) — ablation of today's three filters

- **Lever:** all three of today's builds, tested in reverse — each removed
  with the other two in force. Runs 13, 15 and 17 each measured an
  addition with occupancy fixed on the live variant, which understates an
  exclusion filter but cannot see whether an earlier filter still earns
  its place, or whether the trades it refuses would be replaced by better
  ones. Acceptance, deliberately asymmetric toward caution: a filter is
  REMOVED only if its ablation is at least as good on all four samples and
  significant on one; it STAYS if its ablation is worse on any sample;
  inconclusive leaves it in place.
- **Measurement:** `scripts/filter_ablation.py` — merged
  one-position-per-instrument book, cap 8, occupancy resolved per variant
  so a freed slot is available, router floor 30 and short blocks in force
  throughout. R per calendar day.

  | variant | 365 d | 366–1,095 d | 1,096–1,825 d | 1,826–2,555 d |
  |---|---|---|---|---|
  | **full** | **-0.0258** | **+0.0715** | **+0.0334** | **+0.0337** |
  | -ceiling | -0.016 | -0.027 | **+0.045 (t +3.27)** | +0.005 |
  | -floor | -0.039 | -0.010 | **-0.140 (t -2.08)** | -0.009 |
  | -block | -0.034 | -0.035 (t -1.95) | -0.017 | -0.022 |

- **Result:** the volatility floor and the instrument block are confirmed
  under the stricter accounting — removing either is worse on all four
  samples, and the floor costs 34 % of trades (2.84/day to 4.32) while
  still being the better rule everywhere. The ADX ceiling does not pass
  the same test: its removal is worse on the two newer samples and better
  on the two older, significantly so on the third (t +3.27). Section 188's
  case rested partly on fixed occupancy; with the slot freed, the trades
  it refuses are replaced by worse ones on the recent year and better ones
  on the older.
- **Decision:** nothing changed — all three stay, since the ceiling's
  ablation is worse on two samples and the rule does not withdraw a
  four-sample decision on an inconclusive retest. No code change, no
  restart. Section 201. Methodological point recorded for future runs:
  fixed-occupancy paired tests measure the filter, merged-book per-day
  tests measure the book; for a rule the first is fairer, for the
  objective the second counts, and they can disagree. The ceiling is
  flagged as the next question rather than quietly kept or dropped.

## 2026-09-11 (ninth run) — the ADX ceiling's value on the merged book

- **Lever:** regime filter — run 26 flagged the ceiling as the weakest of
  the three builds and identified the accounting as the reason: section
  188 chose 50 with occupancy fixed, but on the merged book a refused
  trade hands its slot onward. The value itself had never been swept
  there. Ceilings 45 / 50 / 55 / 60 / none. Acceptance: at least parity on
  all four samples, t > 2 on at least one, ship the qualifying value
  closest to 50; nothing qualifying leaves 50 in place.
- **Measurement:** `scripts/adx_ceiling_merged.py` — merged
  one-position-per-instrument book, cap 8, occupancy per variant, router
  floor 30, volatility floor and instrument block in force. R per calendar
  day, differences from the live 50.

  | ceiling | 365 d | 366–1,095 d | 1,096–1,825 d | 1,826–2,555 d |
  |---|---|---|---|---|
  | 45 | +0.017 | **-0.001** | +0.003 | +0.001 |
  | **50 (live)** | — | — | — | — |
  | 55 | -0.017 | -0.006 | +0.017 | +0.005 |
  | 60 | -0.011 | -0.013 | +0.035 (t +2.76) | +0.009 |
  | none | -0.013 | -0.027 | +0.045 (t +3.24) | +0.004 |

- **Result:** nothing qualifies. 55, 60 and none lose on both newer
  samples; 45 is better on three and misses parity on the second by
  0.0014 R a day at t = -0.10 — noise, and still a miss. The rule is not
  relaxed after the fact. The reason no value can be determined is that
  the slope reverses: tighter is best on the recent year, looser on
  1,096–1,825 (removal best at t +3.24), 50 optimal on 366–1,095, 60
  marginally ahead on the oldest — four samples, four preferred values,
  the extremes significant in opposite directions.
- **Decision:** not changed — 50 stays, but its justification is now "the
  value that loses least when the samples disagree" rather than "the value
  the evidence picked": never best, never far from best, worst case
  0.017 R a day against 0.045 for removal. That is a weaker claim than
  section 188 made and the accurate one. No code change, no restart.
  Section 202. Run 26's finding is confirmed, not overturned — this filter
  rests on a disagreement between regimes, and the right response is the
  middle value, not a fitted one.
- **Operational check this run:** no entry had been booked since the first
  build 13 hours earlier, so the entry path was tested directly rather
  than assumed — 18 of 20 live 1h combinations produced no fresh signal on
  the last bar, two were the existing GOLD short block, and none was
  refused by the new filters. Signal scarcity, not a filter defect. The
  instrument block correctly refuses 6 of 63 combinations still listed by
  the pre-build nightly selector.

## 2026-09-11 (tenth run) — crypto longs after overnight financing

- **Lever:** cost filter — section 126 found financing immaterial overall
  (0.003 R a trade) with one open exception: crypto longs at 0.051 R a
  night, more than the book's whole expectancy. It declined a block on the
  two samples then available (+0.01 / -0.03 R net). Re-asked on four
  samples against the current system. Charge applied: 0.051 × 0.7 R a
  trade, the 0.7 being the conservative side of section 131's exit mix
  (60.7 % time out across the 21:00 UTC rollover; barriers resolve at a
  3.0–4.5 h median and mostly do not).
- **Measurement:** offline from run 18's per-trade dumps, so no venue load.

  | sample | crypto longs | gross E[R] | net E[R] | net t |
  |---|---|---|---|---|
  | 365 d | 19 | -0.3266 | -0.3623 | -1.55 |
  | 366–1,095 d | 30 | +0.4256 | +0.3899 | +1.85 |
  | 1,096–1,825 d | 26 | -0.2287 | -0.2644 | -1.36 |
  | 1,826–2,555 d | 7 | +0.0128 | -0.0229 | -0.05 |

- **Result:** no block is possible and none is needed — the sample size is
  the finding. Crypto trades before the 3×ATR floor vs after: 527 → 30,
  1,178 → 49, 1,125 → 47, 1,299 → 15, i.e. **94–99 % removed**, at a mean
  pin of 2.04–2.14. The mechanism is exact: crypto's ATR is large relative
  to price, so 2 ATR already clears the 1.05 % venue minimum and the stop
  stays near 2 ATR — the least-pinned band section 190's floor refuses.
  The financing exposure section 126 flagged was closed by a filter built
  for an unrelated reason.
- **Decision:** nothing built. No code change, no restart. Section 203.
  Also settled: no financing term needs adding to the simulator — at
  0.003 R a trade on the surviving book it would change no decision in
  this log. This is the other half of section 190's concentration check,
  which had found crypto diluting rather than carrying the floor's effect.

## 2026-09-11 (eleventh run) — the winning side of the consistency rule

- **Lever:** pair selection — run 17 applied the three-sample consistency
  rule to the losing side only. Its mirror: concentrate the book on
  instruments positive in all three training samples, trade nothing else.
  Acceptance as before: better on the held-out recent year at t > 2,
  minimum 60 trades per training sample.
- **Measurement:** offline from run 18's per-trade dumps. Of 18 qualifying
  instruments, five are positive throughout training (chance expectation
  2.2, so the set is not merely noise-sized).

  | instrument | test n | test E[R] | t |
  |---|---|---|---|
  | FR40 | 97 | +0.1728 | +2.15 |
  | US100 | 115 | +0.0915 | +1.08 |
  | J225 | 42 | +0.0365 | +0.24 |
  | US30 | 190 | -0.0656 | -1.24 |
  | EU50 | 108 | **-0.2255** | **-2.89** |

  Concentrated book on the test sample: n = 552, E[R] -0.0145 against
  -0.0062 for trading everything and -0.0037 for the rest.
- **Result:** rejected, and not narrowly — the winners are worse than the
  full book, two of five are negative out of sample and one significantly,
  and the rule would remove 77 % of trades to get there. All five
  candidates are indices, i.e. the index bull-run artefact sections 137
  and 155 already caught twice in other disguises.
- **Decision:** nothing changed. No code change, no restart. Section 204.
  The asymmetry with run 17 is the finding: an instrument can be
  structurally bad in a persistent way (spread against volatility, a stop
  the venue floor distorts, poor fills), but structurally good would have
  to mean "tends to trend", and trend is precisely what does not persist
  across regimes. Consistency therefore identifies durable losers and
  transient winners, and works in one direction only. The instrument
  dimension is closed in both.

## 2026-09-11 (twelfth run) — the notional cap's expired condition

- **Lever:** position sizing — section 81 kept the 250 USD notional cap
  with a conditional argument: raising it trades a 20 % looser exposure
  limit for a proportional dollar change "whose sign is the book's
  expectancy — which every section of this document puts at zero". After
  runs 13, 15 and 17 that sign is positive on three of four samples
  (+0.0715 / +0.0334 / +0.0332 R a day, recent year -0.0258), so the
  condition has expired and the question is legitimately reopened.
- **Measurement:** live sizing path, 43 accepted entries since
  2026-08-01. Planned risk averages **2.41 USD against the configured
  3.00**, 39 of 43 below 2.90 — `raw_size = min(risk_size, notional_size)`
  and the notional term is smaller whenever the venue floor sets the stop
  (3 USD at a 1.05 % stop needs 286 USD of notional). 24 % of the intended
  dollar gain is forgone structurally. Per-day effect of closing the gap:
  +0.042 / +0.020 / +0.020 USD on the three positive samples, -0.015 on
  the recent year. Exposure at 8 positions would go from 3.2 % to 4.0 % of
  equity, per-trade 0.40 % to 0.50 %, both inside the 1 % per-trade cap.
- **Decision:** not raised, for three reasons that are not arithmetic.
  The regime the book trades in now is the recent year, where the cap is
  protective (+0.015 USD a day of loss avoided). The forward evidence for
  the positive samples is zero trades — run 25 reset the out-of-sample
  window to the build date exactly because nothing earlier is evidence for
  the current system, and raising exposure on backtest expectancy alone is
  that same error mirrored. And section 81's rule protects an exposure
  limit, which a four-sample backtest is the weakest evidence to open.
  No code change, no restart. Section 205.
- **Correction this run owes the log:** every dollar figure in runs 20 and
  23 was stated at 3 USD of risk; the risk actually taken is 2.41, so they
  were 24 % too high. Run 23's "+0.22 / +0.10 / +0.11 USD a day" should
  read +0.17 / +0.08 / +0.08. Run 20's ~0.6 USD/day ceiling stands, since
  it was computed at the 1 % equity cap of 6.02 USD — but the figure for
  the configuration as it actually runs is 0.17 USD a day at best.
- **Recorded for the next run:** the cap is the largest identified lever on
  dollar gain that needs no new edge (+24 % at unchanged expectancy), and
  its blocking condition is now named precisely — forward confirmation on
  entries after 2026-09-10.

## 2026-09-11 (thirteenth run) — the scaling gate was inert — FIXED

- **Lever:** position sizing, defect rather than trade-off — run 30 found
  the notional cap binding on every venue-pinned stop. Following that
  through: `edge_scaling` raises `risk_per_trade` only, and sizing takes
  `min(risk_size, notional_size)`. With the notional term already smaller
  at base risk, a raised budget changes nothing.
- **Measurement:** live sizing function, US30 at a 1.05 % stop, fine
  broker increment.

  | target risk | cap | notional | planned risk |
  |---|---|---|---|
  | 3.00 | 250 | 246.75 | 2.59 |
  | 4.50 | 250 | 246.75 | **2.59** |
  | 6.00 | 250 | 246.75 | **2.59** |
  | 6.00 | 500 | 498.75 | 5.24 |

  Three budgets, one outcome. Had the gate opened it would have logged
  "risk scaled $3.00 → $6.00" and placed identical orders — the confidence
  bound, the equity ceiling and the bounded steps all unreachable behind a
  constant the module does not know about.
- **Decision:** fixed. `evaluate_pair` scales the notional cap by the same
  factor as the risk budget, preserving the exposure-to-risk ratio the cap
  was set at instead of tightening it as the budget grows. While the gate
  is shut the factor is 1.0 and the cap stays at 250 — verified against the
  live journal state: no order changes today or until the gate opens on
  post-2026-09-10 entries. 60/60 tests green including three new ones
  pinning the defect. Hurz restarted. Section 206.
- **Explicitly not run 30's lever:** that one asked to raise the cap now on
  backtest expectancy and was refused, because an exposure limit should not
  open on four-sample evidence. This leaves today's exposure unchanged and
  repairs the path so the mechanism built for earning a larger size can
  deliver it once its own evidence bar is met. One loosens a limit; the
  other connects two limits that were talking past each other.

## 2026-09-11 (fourteenth run) — the stop floor in dollars

- **Lever:** stop logic, re-asked in the objective's unit — section 136
  swept the floor in R per trade and kept 1.05 %, but R is normalised by
  the very quantity being changed. Section 205 showed the cap binds at
  1.05 % (3 USD needs 286 USD of notional against a 250 cap, so the trade
  carries 2.62), while a wider floor needs less notional for the same
  dollar risk and frees the budget. Floors 1.05 / 1.2 / 1.5 / 2.0 %.
  Acceptance: USD/day higher on all four samples, paired daily t > 2 on at
  least one, planned risk never above the configured 3.00, ship the
  smallest qualifying floor. No risk limit is loosened — the dollar loss
  at the stop stays at its configured ceiling.
- **Measurement:** `scripts/stop_floor_dollars.py` — merged book, cap 8,
  every trade sized through the live `calculate_position_size` so cap,
  broker increment and resulting rejections are real; ADX ceiling, 3×ATR
  floor and block list in force; stop and target move together.

  | floor | mean risk | 365 d | 366–1,095 d | 1,096–1,825 d | 1,826–2,555 d |
  |---|---|---|---|---|---|
  | **1.05 %** | **2.62** | **-0.0783** | **+0.1873** | **+0.0890** | **+0.0867** |
  | **1.20 %** | **2.99** | +0.013 | +0.014 | +0.045 | +0.002 |
  | 1.50 % | 2.99 | -0.040 | -0.111 | -0.081 | +0.013 |
  | 2.00 % | 2.98 | +0.043 | -0.025 | -0.126 | -0.019 |

- **Result:** the mechanism works as predicted — at 1.20 % mean planned
  risk goes 2.62 → 2.99 USD, the configured budget is reached, and trade
  count rises 5 % because the wider floor lifts pin ratios past run 15's
  3×ATR threshold. **1.20 % is the only variant positive on all four
  samples.** 1.50 and 2.00 give the risk back but lose more expectancy
  than the extra dollars are worth — section 136's finding surviving in
  the new unit.
- **Decision:** not built in. (a), (c) and (d) pass; (b) fails everywhere,
  best reading **t = +0.49**. Named precisely because it is not the same
  as absence: a per-calendar-day paired test has little power here, since
  most days carry two or three trades and many none, so the series is
  dominated by single-trade variance. Four of four positive is p = 0.0625
  on a sign test, which was not the preregistered statistic and does not
  license a build. No code change, no restart. Section 207.
- **Recorded for the next run:** the 24 % of dollar gain the notional cap
  forgoes can be recovered at the stop floor rather than at the exposure
  limit, it shows on all four samples, and what remains is measurement
  power, not direction. Preregister a statistic with the power to settle
  it — paired per trade over the signals both variants take.

## 2026-09-11 (fifteenth run) — the stop floor, paired in dollars

- **Lever:** stop logic — run 32 found 1.20 % positive on all four samples
  in USD per day but missed t > 2 for lack of power. This supplies it:
  paired in USD on trades both floors take, with trades only one takes
  reported separately. Preregistered: paired diff positive on all four,
  t > 2 on at least one and never below -2, added trades at mean USD >= 0
  on all four, planned risk never above 3.00. Declared in advance as the
  last attempt at the floor.
- **Measurement:** `scripts/stop_floor_paired.py` — merged book, live
  `calculate_position_size` so cap and increment are real, occupancy per
  variant, trades matched by instrument and bar.

  | sample | shared | paired USD | t | only-1.20 % | their mean USD |
  |---|---|---|---|---|---|
  | 365 d | 935 | +0.0201 | +1.34 | 152 | +0.0454 |
  | 366–1,095 d | 2,013 | **-0.0110** | -1.09 | 287 | +0.0382 |
  | 1,096–1,825 d | 1,792 | +0.0106 | +0.92 | 358 | +0.0263 |
  | 1,826–2,555 d | 1,786 | +0.0157 | +1.58 | 262 | **-0.0737** |

- **Result:** rejected on three of four conditions — (a) negative on
  366–1,095, (b) best t +1.58, (c) added trades negative on the oldest
  sample; only (d) holds. The two measurements disagree because run 32's
  per-day figure mixes the cap effect with a composition effect: on
  366–1,095 the trades only the live floor takes return -0.1232 USD each,
  so dropping them lifts the daily number for reasons unrelated to the cap.
  Paired, the cap effect alone reads +0.020 / -0.011 / +0.011 / +0.016 —
  sign unstable. Mechanism: a wider floor buys 14 % more dollars per unit
  of R and widens the target by the same proportion, so it costs R; the
  two terms very nearly cancel. Section 136's conclusion reached again,
  independently, in the other unit.
- **Decision:** not built in — 1.05 % stays, and the question is closed.
  Asked in R (136), USD per day (207) and paired USD (208), three
  statistics, three times unsupported. No code change, no restart.
  Section 208. What remains unrecovered: the notional cap still forgoes
  24 % of the configured budget (run 30) and that loss cannot be taken
  back at the stop floor — only at the cap, which run 30 declined on
  forward-evidence grounds and run 31 repaired for the day it arrives.

## 2026-09-11 (sixteenth run) — the nightly refresh after an interrupted run — FIXED

- **Lever:** pair selection, operational defect — the active list on disk
  was 28 hours old and still carried six entries for the three instruments
  blocked in run 17. The bot log (local time) shows the refresh fired at
  07:30 CEST and a restart at 07:37 killed it seven minutes into a run that
  normally takes twenty-three, with nothing to repair it until the next
  day.
- **Cause:** the scheduler's start-up rule marks the day done whenever the
  process starts past 05:30 UTC — the clock, not the result. It cannot
  distinguish "already ran" from "interrupted" or "never started". Its own
  comment names the intent (a restart must not fire an immediate
  catch-up), which is sound; the implementation achieved it by assuming
  success.
- **Fix:** the scheduler checks whether the persisted list carries today's
  UTC date. Same protection — at most one fire per UTC day however often
  the process restarts, since a completed refresh stamps the file — while
  repairing an interrupted or missed run the same day. Missing, unreadable
  or undated counts as not written, so it repairs rather than assumes.
- **Verified in the running system:** after the fix and a restart the
  scheduler fired the catch-up unaided at 09:51 and completed 3/3
  backtests; the list went from 68 combinations dated 2026-09-10 with six
  blocked entries to **55 combinations dated 2026-09-11 with none**, and
  the heartbeat now scans 55 pairs. 61/61 tests green including five new
  ones. Section 209.
- **Severity, both directions:** nothing incorrect was traded —
  `BLOCKED_PAIRS` is consulted at the entry guard, not only at selection,
  so the retired instruments were refused regardless. But the failure
  compounds: every restart after 05:30 defers the refresh another day, and
  this session restarted the bot five times.
- **Also checked this run:** entries since the build are still zero. At
  07:46 UTC with Europe open, 26 of 26 tradeable 1h combinations had no
  fresh signal on the last bar — not one reached a guard. That matches the
  arithmetic: 2.84 trades a day across 69 combinations is one per
  combination every 25 days. Signal scarcity, not filter action.

## 2026-09-11 (seventeenth run) — reward:risk on the post-build book

- **Lever:** exit logic — the target was last swept on the pre-build book
  (sections 63, 124, 184, 185). The three filters trimmed it unevenly:
  94–99 % of crypto gone, every least-pinned trade gone, three FX pairs
  gone, the high-trend tail gone. What remains is almost entirely
  venue-pinned stops at 3+ ATR, and a target is a claim about how far
  price runs before turning. RR 1.0 / 1.5 / 2.0 / 2.5, measured in R per
  calendar day on the merged book because the RR changes both per-trade
  expectancy and occupancy. Acceptance: better on all four samples,
  paired daily t > 2 on at least one, ship the qualifying value closest
  to 1.5. Stop untouched, 1 R loss limit unmoved.
- **Measurement:** `scripts/rr_current_book.py`.

  | RR | 365 d | 366–1,095 d | 1,096–1,825 d | 1,826–2,555 d | all four? |
  |---|---|---|---|---|---|
  | 1.0 | -0.010 | -0.025 | +0.016 | -0.009 | no |
  | **1.5 (live)** | — | — | — | — | — |
  | 2.0 | -0.010 | -0.020 | +0.046 | +0.018 | no |
  | 2.5 | -0.022 | -0.027 | +0.042 | +0.025 | no |

- **Result:** no variant better on all four; (a) fails for each. The split
  is the familiar one in a new place — the two older samples prefer a
  wider target (2.0/2.5 add 0.02–0.05 R a day), the two newer prefer 1.5,
  and 1.0 is worse than 1.5 on three of four. Filtering the book did not
  change the answer the pre-build sweeps gave.
- **Defect in this run's measurement, recorded not hidden:** the script
  was derived from the leash sweep and its final comparison table still
  iterated the leash variable, raising a NameError after the per-variant
  figures printed. The per-day numbers are complete; the paired daily
  t-statistics were never produced. Fix committed. It does not change the
  decision: (a) is evaluated on point estimates and fails for every
  variant on at least two samples, and a t-statistic cannot turn a
  negative difference positive. Had any variant been positive on all four,
  the run would have had to be repeated before deciding.
- **Decision:** not changed — RR 1.5 stays. No code change to the trading
  system, no restart. Section 210.

## 2026-09-11 (eighteenth run) — decomposing the risk shortfall

- **Lever:** position sizing, correcting run 30's own attribution — the
  first live trade after the builds (EURUSD short, notional 231.9 against
  a 250 cap) showed planned risk at 2.44 while the cap was not binding at
  all. Run 30 had attributed the whole 2.41-vs-3.00 gap to the cap and
  called it a +24 % lever.
- **Measurement:** sizing arithmetic reproduced per trade over 44 accepted
  entries since 2026-08-01 — `risk_size = target/stop_distance`,
  `notional_size = cap/entry`, then the broker increment floor.

  | component | mean USD | share |
  |---|---|---|
  | total shortfall | 0.591 of 3.00 | 100 % |
  | notional cap | 0.355 | **60 %** |
  | broker increment rounding | 0.165 | **28 %** |
  | remainder (quote conversion, minimums) | 0.071 | 12 % |

  The cap binds on 39 of 44 trades. EURUSD is the clean case: risk_size
  245.7, notional_size 215.6, actual 200.0 — cap costs 0.366 USD, rounding
  a further 0.190, and 3.00 − 0.556 = 2.44 is exactly the journalled value.
- **Result:** run 30's cap lever is worth **+11.8 %** at unchanged
  expectancy, not +24 %. The rounding term is not recoverable: the
  increment is a broker constraint and rounding up would breach the risk
  target — at EURUSD's 100-unit step, 300 units is 3.65 USD against a 3.00
  budget. `ROUND_FLOOR` is correct and its cost is the price of a discrete
  instrument.
- **Decision:** nothing changed; run 30's decision stands unaffected (an
  exposure limit does not open on backtest evidence alone), but the prize
  behind it is halved, which matters when that decision is made on forward
  data. No code change, no restart. Section 211. What this corrects is a
  habit more than a number — run 30 inferred a cause from a correlation
  without decomposing it, and the first contradicting live trade was
  enough to expose that.

## 2026-09-11 (nineteenth run) — the cap lever against real broker increments

- **Lever:** position sizing — run 38 put the notional cap at 60 % of the
  risk shortfall, worth +11.8 %. This verifies that against the venue's
  actual size constraints rather than against assumed ones.
- **A wrong turn, recorded:** inferring each instrument's step as the GCD
  of its observed sizes gave +2.7 % — a figure that would have retired the
  lever. The inference is systematically too coarse: EURUSD's real step is
  100 against 200 inferred, COPPER's 1 against 36, DE40's 0.001 against
  0.008. A GCD over a handful of sizes measures the sizes, not the step.
- **Measurement:** `order_constraints` fetched per instrument, sizing
  arithmetic replayed over 44 accepted entries since 2026-07-01.

  | cap | mean realised risk | vs live | unchanged |
  |---|---|---|---|
  | **250 (live)** | **2.169 USD** | — | — |
  | 300 | 2.405 USD | **+10.9 %** | 22 of 44 |
  | 400 | 2.425 USD | +11.8 % | 22 of 44 |

- **Result:** run 38's figure confirmed — the cap is worth about +11 %, not
  run 30's +24 %. Raising to 400 adds 0.9 points over 300 because past 300
  the risk term binds first, so 300 is the whole available gain; and on
  half the trades a raise changes nothing because the increment is coarser
  than the difference. Even at 300 the realised risk is 2.405 against 3.00
  — a 19.8 % shortfall that is rounding and unrecoverable.
- **Decision:** nothing changed; run 30's refusal stands untouched on
  forward-evidence grounds, but the lever is now priced correctly. No code
  change, no restart. Section 212. Worth keeping as a record of method:
  run 30 inferred the cause from a correlation and overstated it at 24 %,
  run 38 decomposed it to 11.8 %, an inferred step size understated it at
  2.7 %, and the venue's own numbers settled it at 10.9 % — three of those
  four figures came from this session, and only the last asked the broker.

## 2026-09-11 (twentieth run) — dollar efficiency per instrument

- **Lever:** pair selection — runs 38 and 39 showed increment rounding
  costs ~20 % of the risk budget and cannot be recovered. This asks
  whether the cost is spread evenly across instruments. Computed
  analytically: current price, the venue's `order_constraints`, the 1.05 %
  stop, the live 3 USD / 250 USD configuration, with quote-currency
  conversion.
- **Measurement:** 25 tradeable instruments of the current active list.
  Mean efficiency 77.3 %, median 81.2 %, range **47 % (GBPJPY) to 87 %
  (SILVER, ETHUSD, COPPER)**. Five below 70 %. The driver is arithmetic:
  a raw size of 185 against a step of 100 loses 46 % to the floor; 0.0174
  against 0.01 loses 43 %.
- **Result:** at identical expectancy in R, GBPJPY produces 47 % of the
  dollars SILVER does. The selector ranks by expectancy in R, which
  section 130 showed does not transfer between samples; efficiency does
  persist, since it follows price level, step and minimum size — none of
  them regime-dependent. The one instrument property that demonstrably
  persists is the one the selector does not price, and the one it prices
  is the one that does not persist.
- **Decision:** nothing built. The claim that reweighting would raise the
  daily figure is not yet measured — it needs efficiency recomputed at
  historical prices across the four samples. That is the next run's work,
  not this one's assertion. No code change, no restart. Section 213.
- **A repeated error, recorded:** the first version of this calculation
  omitted quote-currency conversion and reported seven instruments (all
  JPY-quoted, plus J225) as untradeable at 0 % efficiency. With conversion
  they run at 47–86 %. Same mistake as run 30's reading of 33,580 JPY as
  dollars, repeated four runs later in the same session. The sizing path
  has handled quote currency correctly since sections 118/119; the ad-hoc
  checks around it keep forgetting.

## 2026-09-11 (twenty-first run) — refusing structurally inefficient entries

- **Lever:** cost filter / pair selection — section 213 showed realised
  risk ranges from 47 % to 87 % of the 3 USD budget purely through the
  broker's size increment. This measures whether refusing a signal whose
  realised risk falls below 60 / 70 / 80 % of target raises the dollars
  per calendar day. The rule only refuses; it never enlarges a position
  and moves no risk limit.
- **Measurement:** merged book (ADX router, 3.0-ATR floor, 8 concurrent,
  24-bar hold) over four samples spanning 2,555 days, 25 instruments,
  efficiency recomputed per trade at that trade's entry price with the
  venue's real `order_constraints` and `usd_per_quote`.

  | sample | live | ≥60 % | ≥70 % | ≥80 % |
  |---|---|---|---|---|
  | 0–365 d | −0.0263 | **+0.1181** (t +2.94) | **+0.1350** (t +2.02) | +0.0528 |
  | 366–1095 d | +0.1825 | +0.0023 | −0.0040 | −0.0117 |
  | 1096–1825 d | +0.0601 | −0.0174 | +0.0115 | −0.0356 |
  | 1826–2555 d | +0.0762 | −0.0038 | −0.0040 | −0.0241 |

- **Result:** the preregistered condition (a) — better on all four samples
  — fails at every threshold. The improvement lives entirely in the most
  recent year, the one sample where the live book is negative. The
  mechanism itself works: mean realised risk rises from 2.210 to 2.404 USD
  at 70 %. The dollars do not follow from that, they follow from dropping
  1,033 trades to 752, and those 281 removed trades were net losers only
  in that year.
- **Decision:** verworfen. No code change, no restart. The measurement
  script is kept. Section 214.

## 2026-09-11 (twenty-second run) — efficiency-weighted candidate ranking

- **Lever:** pair selection — the last standing version of the efficiency
  idea. Not a refusal (that was run 21, rejected) but a reordering: the
  live composite score `eR * log1p(n) * pf` multiplied by the
  combination's dollar efficiency. The active list keeps its size, so
  throughput is held constant by construction.
- **Measurement:** walk-forward over 2,555 days, rank on the trailing
  365 d, trade the next 90 d with the top ten, 24 out-of-sample blocks,
  23 instruments, 32,063 gated and sized signals, USD per calendar day.

  | sample | live rank | eff-weighted | diff | t |
  |---|---|---|---|---|
  | 0–365 d | +0.0833 | +0.0810 | −0.0023 | −0.09 |
  | 366–1095 d | +0.0094 | +0.0365 | +0.0271 | +1.37 |
  | 1096–1825 d | +0.0022 | +0.0111 | +0.0089 | +0.83 |
  | 1826–2555 d | +0.0684 | +0.0498 | −0.0185 | −1.44 |
  | pooled | +0.0349 | +0.0396 | +0.0047 | +0.56 |

- **Result:** (a) fails, two samples up and two down; (b) fails, best t
  +1.37; (c) holds at −1.0 % throughput. The reason is mechanical: the
  two top-ten lists overlap by **97 %** across the 24 blocks. The
  composite score already selects the well-stepped instruments, and the
  inefficient ones — GBPJPY at 47 %, UK100 at 50 % — rarely reach the top
  ten anyway. There is nothing for efficiency to reorder.
- **Decision:** verworfen, and the efficiency line is closed with it:
  run 20 measured the spread, run 21 refused on it, run 22 reranked on
  it. No code change, no restart. Section 215.

## 2026-09-11 (twenty-third run) — the length of the active list

- **Lever:** pair selection — the selector keeps the top N combinations,
  live N=40, and the length itself had never been measured. Both
  directions were arguable: a shorter list concentrates on the best
  scores, a longer one buys throughput, which section 198 showed is what
  the daily figure is bound by.
- **Measurement:** walk-forward as in run 22 on the same cached bars,
  24 out-of-sample blocks, 23 instruments, 32,063 signals, N in
  5 / 10 / 20 / 40 / all, scored in USD per calendar day.

  | N | pooled vs live 40 | t | trades |
  |---|---|---|---|
  | 5 | −0.1733 | −1.94 | 585 |
  | 10 | −0.1283 | −1.76 | 1,249 |
  | 20 | −0.0611 | −1.31 | 2,633 |
  | **40 (live)** | — | — | **3,976** |
  | all | ±0.0000 | — | 3,976 |

- **Result:** every shorter list is worse, monotonically, and N=40 equals
  N=all exactly — the eligible pool averages 31.1 per block, so the cut
  never binds. Confirmed against production: the live list holds 55
  combinations, 26 of them pins, leaving 29 ranked where the code asked
  for 40. The ranking orders the list but selects nothing.
- **Decision:** nothing changed — the parameter already sits at the best
  available value. Shortening costs throughput, raising it does nothing.
  No code change, no restart. Section 216. Left open for the next run:
  because no cut binds, the live list carries combinations with a
  negative score (down to −0.105). Whether removing those raises the
  daily figure is a separate lever and was not measured here.

## 2026-09-11 (twenty-fourth run) — the selector's eligibility thresholds

- **Lever:** pair selection — run 23 showed the top-40 cut selects
  nothing, which leaves the eligibility filter as what actually decides
  the active list. Since 2026-07-03 the scheduler asks for a widened one
  (`--min-pf 0.8`, `--min-er -0.2`, `--min-stability 0`) with a comment
  to narrow it back down once forward data arrives. 70 days have passed
  and four ranked combos now carry negative expectancy, so the lever is:
  does the strict filter earn more per day than the wide one?
- **Measurement:** walk-forward as in runs 22 and 23, same cached bars,
  24 out-of-sample blocks, cut held at 40, eligibility the only variable.

  | variant | pooled vs live | t | trades | all four better |
  |---|---|---|---|---|
  | **live 0.8/−0.2** | — | — | **4,848** | — |
  | 0.9/−0.1 | −0.0343 | −1.48 | 4,437 (−8.5 %) | NO |
  | 1.0/0.0 | −0.0421 | −1.23 | 3,976 (−18.0 %) | NO |
  | 1.1/+0.05 | −0.0737 | −1.32 | 2,588 (−46.6 %) | NO |

- **Result:** every tightening is worse on every one of the four samples
  — twelve comparisons, no exception — and the loss grows with the
  tightening. The reason is the combination of sections 130 and 198:
  tightening removes combos by in-sample expectancy, which does not
  transfer, and pays certain throughput for it. The 2026-07-03 widening
  was not a temporary expedient that expired; it is still correct.
- **Decision:** thresholds unchanged. The stale instruction to narrow
  them was removed from `scheduler.py` and replaced with the measured
  result, so the next reader does not make a measurably worse change.
  Comment-only edit, no behaviour change, no restart. Section 217.
- **A correction, recorded:** run 23 claimed the harness had reproduced
  the live pool size (31.1 against 29). It had not — the harness ran the
  strict filter, not the one the scheduler uses; under the live
  thresholds its pool is 39 of a possible 40. Run 23's finding that
  shortening the list costs throughput survives, but its N-sweep ran
  under an eligibility filter the bot does not use, and under the live
  one the cut very nearly binds. **Raising N above 40 is therefore
  untested and is the next run's lever.**

## 2026-09-11 (twenty-fifth run) — a longer active list, under the live filter

- **Lever:** pair selection — run 24 showed run 23's length sweep had run
  under the strict eligibility filter, so a list longer than the live 40
  had never been measured under the filter the bot uses. Throughput can
  only rise here, so run 21's confound cannot appear.
- **Measurement:** walk-forward as in runs 22–24, live filter
  (pf ≥ 0.8, eR ≥ −0.2), 24 out-of-sample blocks, N in 40 / 50 / 60 / all.

  | N | pooled vs live 40 | t | trades |
  |---|---|---|---|
  | **40 (live)** | — | — | **4,848** |
  | 50 | −0.0125 | −0.49 | 5,163 (+6.5 %) |
  | 60 | −0.0162 | −0.61 | 5,180 (+6.8 %) |
  | all | −0.0162 | −0.61 | 5,180 (+6.8 %) |

- **Result:** neither condition met, and the effect is flat rather than
  negative — one sample gains, three give a little back. The uncapped
  pool averages 43.3 per block and the cut binds in 15 of 24 blocks, so
  the room existed; the combinations at ranks 41–50 simply earn nothing.
  Throughput rises 6.5 % with no dollar effect, which qualifies the
  throughput story of runs 18 and 23: the concurrent cap of 8 and the
  one-position-per-pair rule bound the open book, so a longer list swaps
  marginal entries rather than adding them.
- **Decision:** verworfen. 40 sits on a plateau — worse below, flat above.
  With run 24 fixing the thresholds, both of the selector's size knobs
  are now measured and both stay. No code change, no restart. Section 218.
- **Next:** the list's other half is still unpriced — 26 of the 55 live
  entries are operator pins that bypass ranking, eligibility and cost
  filters entirely.

## 2026-09-11 (twenty-sixth run) — what the operator pins contribute

- **Lever:** pair selection — 26 of the 55 live combinations are pins
  that bypass every filter. Run 25 showed extra combinations mostly swap
  which signal takes a slot, so the question is whether the pinned half
  earns its place against the ranked half.
- **Measurement:** walk-forward as in runs 22–25 at the live
  configuration, three lists: live (ranked ∪ pins), ranked only, pins
  only. The harness holds 40 of the 59 pins; the 4h and momentum pins lie
  outside its universe.

  | variant | combos | trades | pooled vs live | t |
  |---|---|---|---|---|
  | **live (both)** | 56.3 | **5,908** | — | — |
  | ranked only | 39.0 | 4,848 (−17.9 %) | +0.0173 | +0.41 |
  | pins only | 40.0 | 4,963 (−16.0 %) | +0.0016 | +0.03 |

- **Result:** dropping the pins is nominally better but fails both
  conditions — t +0.41 and a loss on the 366–1095 d sample. The finding
  is the flatness: three genuinely different lists, differing by 18 % in
  trade count, land within 0.017 USD/day of each other.
- **Decision:** pins stay, nothing built. No code change, no restart.
  Section 219.
- **Axis closed.** Runs 21–26 measured efficiency as a refusal and as a
  reranking, the list length downward and upward, the eligibility
  thresholds and now the composition. Six null results with one cause:
  the concurrent cap of 8 and one-position-per-pair bound the book, and
  the marginal trade is worth about zero (section 85 measured it at
  −0.026 R from the other side). **The next lever must leave the
  selection axis.** The only measured change that moved dollars is the
  notional cap at +10.9 % (run 19), refused on forward-evidence grounds
  in run 30 — that refusal, not the selector, is what holds the figure
  down.

## 2026-09-11 (twenty-seventh run) — a longer list given free capacity

- **Lever:** pair selection under a lifted cap — runs 25 and 26 blamed
  their null results on a full book. That premise is testable, and the
  lever it implies is a longer list once capacity is free. Raising the
  list length loosens no risk limit; only the simulation's cap was lifted,
  to find out whether capacity was the cause.
- **Measurement:** occupancy first, from both sides. Live, hourly over
  30 days: mean 4.83 of 8, at capacity in 3.9 % of hours. Harness, at
  every candidate entry: 3.21 of 8, 208 refusals against 4,848 trades.
  Then the list sweep at cap 8 and cap 24.

  | | N=60 vs N=40 | t | refused by cap |
  |---|---|---|---|
  | cap 8 (live) | −0.0162 | −0.61 | 208 |
  | cap 24 | −0.0099 | −0.37 | 0 |

- **Result:** both conditions fail at both caps. Lifting the cap removes
  every refusal and changes the answer by a hundredth of a cent per day.
  The book was never full — not live, not in the harness — so the
  capacity explanation of runs 25 and 26 is refuted.
- **Decision:** nothing built, cap stays at 8. Lifting it buys 58 trades
  and no dollars while adding exposure, so this is an argument for
  leaving it alone. No code change, no restart. Section 220.
- **The correction matters more than the lever.** Runs 25 and 26 measured
  correctly but explained wrongly. The real constraint is that the
  marginal trade is worth about zero — section 85 measured the refused
  entries at −0.026 R, run 25 found 6.8 % more trades and no dollars,
  run 26 found three different lists within 0.017 USD/day. No
  rearrangement of which or how many trades can move a figure built from
  trades worth nothing at the margin. That rules out the capacity
  direction too, and leaves per-trade expectancy and per-trade dollars as
  the only axes that can still matter.

## 2026-09-11 (twenty-eighth run) — the cost axis, priced whole

- **Lever:** cost filters — run 27 left per-trade expectancy as the only
  live axis. Instead of testing another cost filter, this prices the
  entire axis by booking every trade twice, with the cost term and at
  zero.
- **Measurement:** walk-forward as in runs 22–27, 4,848 trades over
  24 out-of-sample blocks.

  | | R per trade | t | USD/day |
  |---|---|---|---|
  | gross | +0.0391 | +3.82 | +0.2109 |
  | cost | 0.0115 | — | −0.0608 |
  | **net** | **+0.0276** | **+2.70** | **+0.1501** |

- **Result:** the edge is real and survives costs, positive on all four
  samples. Costs take 30 % of the gross edge, so **+0.06 USD/day is the
  hard ceiling on every conceivable cost lever** — a free venue included.
  That retires the axis instead of inviting another filter on it.
- **The wall, stated in one line:** 0.0276 R × 2.2 USD risk × 2.7 trades
  a day ≈ 0.16 USD/day. The 50 EUR objective is ~300× that. Run 7 reached
  the same wall from the sizing side (target = 93× the account).
- **Live comparison:** since the current filter set completed on
  2026-08-24 the live book is −0.72 USD/day over 46 trades, about one
  standard error from the harness's +0.15 — not yet distinguishable. The
  July–August book at −0.31 USD/trade over 417 trades is more than three
  standard errors below it, and predates these filters.
- **Decision:** nothing built, no restart. Section 221. Any future lever
  must name which of expectancy, size or frequency it multiplies, and by
  how much; nothing in 221 sections has produced a factor above ~1.1.

## 2026-09-11 (twenty-ninth run) — the ceiling of the family, measured

- **Lever:** none left to test individually — this measures the bound on
  all three factors at once, so the question "which lever next" can be
  answered rather than repeated.
- **Measurement:** each factor pushed past any implementable setting.
  Frequency: every gated, sizeable signal, no cap, no per-pair rule, no
  list — 12.57/day against the book's 2.7. Expectancy: gross, costs at
  zero — +0.0236 R over 32,063 signals, t +6.26. Size: the full 3.00 USD
  budget, no rounding, no cap.

  | | book | bound |
  |---|---|---|
  | frequency | 2.7 /day | 12.57 /day |
  | expectancy | +0.0276 R | +0.0236 R gross |
  | size | ~2.20 USD | 3.00 USD |
  | **USD/day** | **+0.15** | **+0.889** |

- **Result:** the ceiling is 0.889 USD/day against an objective of 54.00
  USD/day — **61× short**, with every constraint removed and costs at
  zero. The objective would need E[R] = 1.43 R a trade, sixty times the
  measured gross edge, or an account roughly 200× larger at the same risk
  fraction. Run 7 reached the same wall from the sizing side at 93×.
- **A finding inside the finding:** over the last year the unselected
  signal is worth +0.0018 R gross — zero. Runs 21–28 were asking the
  selection machinery to order a population that currently has no edge to
  order.
- **Decision:** nothing built, no restart. Section 222. The search for a
  lever inside this strategy family is closed: the daily figure lives
  between 0.15 and at most 0.89 USD and cannot be tuned to 54. Moving it
  requires materially more capital, a different strategy family, or a
  lower objective — an operator decision, not a parameter here.

## 2026-09-11 (thirtieth run) — the duplicate-instrument guard — FIXED

- **Not a lever but a defect**, found while checking an anomaly from run
  27's occupancy work: the live book showed two positions on HK50 three
  seconds apart on 2026-09-10, `turtle_breakout_4h` and
  `donchian_breakout`, each sized for the full 3 USD budget.
- **Cause:** the one-position-per-pair guard reads `positions`, the
  cycle-start snapshot, which cannot contain what the cycle just opened;
  the bar-time dedup treats two resolutions as two distinct signals by
  design. Commit dc3afcf gave the cluster and concurrent caps an
  `opened_this_cycle` view in August and left this guard behind.
- **Fixed:** pairs opened in the cycle are tracked and a second entry on
  one is refused and journalled, beside the concurrent cap so the skip
  stays visible. Regression test reproduces the live case across two
  resolutions — two orders before, one after. Full suite 301 tests green;
  a stale assertion in the cluster-map test was corrected alongside.
- **Also verified:** the 89 books above eight positions all predate
  2026-08-24, when the concurrent cap stopped depending on an unset
  environment variable. The cap is working.
- **Effect on the daily figure:** not measurable — one occurrence in
  eighteen days. What it removes is the tail where one instrument moves
  against two positions at once. Section 223. Hurz restarted on the fix.

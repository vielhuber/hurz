# Hurz — canonical status

Updated 2026-10-22. Short and current; history lives in `levers.md` and
`EDGE_FINDINGS.md`.

## Probation period

2026-10-22 to 2026-11-21 (Europe/Berlin). Goal: a robustly shown,
economically relevant daily net gain at unchanged risk limits. Phase 1
repairs the measurement base; edge research (at most two pre-registered
strategy hypotheses) only after its acceptance. Checkpoints: 2026-10-29
(opening balance, unit fix, parity status), 2026-11-05 (measurement base
accepted or not), 2026-11-12 (research and forward phase), 2026-11-21
(final grade A/B/C). No autonomous research after 2026-11-21 without a
new order.

## Live configuration

- Demo account on Capital.com, one bot instance from `/host/data/hurz`
  (watchdog `scripts/_session_watchdog.sh`, host keepalive restarts it
  after a container restart).
- Strategies: momentum (1h) on the ranked list, plus five exclusive 4h
  pins. Active list of 2026-10-22 06:06 UTC: momentum on US30, DE40,
  US100, EU50, UK100 and ETHUSD (pinned); donchian_breakout_4h SILVER and
  NZDUSD, turtle_breakout_4h HK50, momentum_4h COPPER and CHFJPY.
- Selection: nightly refresh at 05:30 UTC over donchian_breakout,
  momentum and turtle_breakout, ranked on two years of history since
  2026-10-13 (commit 6fe17ae; section 401, the last adopted lever).
- Retired strategies: donchian_breakout (2026-09-25), turtle_breakout
  (2026-10-01 02:02 UTC) and the mean-reversion and keltner strategies.
  Since 2026-10-22 the strategy veto sums in USD; all seven earlier
  retirements are kept explicitly (section 437). Readmission is a
  separate, validated strategy change.
- Risk per trade 3 USD target, stop at least 3 ATR and the venue
  minimum, cost ceiling 10 % of risk, at most 8 open positions, three per
  cluster and direction, one per instrument, 24-hour wall-clock stale
  exit (4h strategies: 96 hours), waiting through closed markets.

## Reference (research data, not an untouched test)

Canonical since 2026-10-23 (sections 439, 440): section 401's book with
the bot's 4h entry rule (refused under 3 ATR) and the bot's wall-clock
stale exits (hourly 24 h, 4h pins 96 h after the order; the ranking still
reads bar-booked signals, as the selector's backtest does), on the bar
cache of 2026-10-12, OOS 2020-09-23 to 2026-09-20 (2,188 calendar days),
weekly re-ranking: 388 closes, +97.1406 USD, +0.044397 USD/day, daily SD
0.7678, worst day -5.1024 (momentum 323 closes +106.76 USD, 4h pins 65
closes -9.62 USD). Reproduced by `scripts/momentum_two_year_ranking.py`
(the 730-day arm). Earlier figures: +0.017465 USD/day (section 401, 4h
stops at the floor, bar leashes), +0.033727 (section 439). This history
has been searched over 430 times; it is research data.

## Journal (realised USD, opening balance of 2026-10-22, section 438)

One ledger (`app/spot_trading/journal_ledger.py`, `scripts/journal_balance.py`)
converts every accepted live close to USD: booked USD since 2026-09-07
23:05 UTC; older rows recomputed from fill and exit, USD-quoted as is, by
the journaled USD risk, or at a fixed rate of 2026-10-12 (56 legacy closes
in EUR, GBP, JPY, CHF, HKD, AUD, CAD instruments; 0 unconverted).

| cohort | closes | USD |
|---|---:|---:|
| current bot since the veto regime 2026-10-01 02:02 UTC | 3 | +1.37 |
| of it since the two-year ranking 2026-10-12 22:25 UTC | 3 | +1.37 |
| of it since the probation start 2026-10-22 | 0 | 0 |
| current strategies before 2026-10-01 (history) | 31 | +27.53 |
| retired strategies (history) | 548 | -487.87 |
| whole history Capital.com since 2026-05-08 | 582 | -458.96 |
| Kraken Futures research (retired) | 19 | -10.62 |

Open: NZDUSD donchian_breakout_4h long since 2026-10-21 20:00 UTC.
Financing (account SWAP entries) is not in the journal; broker account
movements are not reconciled yet — both open. The figure -196.67 USD given
earlier on 2026-10-22 read `realized_pnl` as booked (signal price, quote
units) and is superseded.

## Known deviations between replay, selector and bot

| item | bot | replay | status |
|---|---|---|---|
| strategy veto units | quote currency summed across instruments | follows the bot | fixed 2026-10-22 (section 437) |
| 4h signal below the 3-ATR floor | refused | stop set to the floor | fixed 2026-10-23 (section 439) |
| stale exit | 24 wall-clock hours (4h: 96) | 24 bars | fixed 2026-10-23 (section 440) |
| indicator warm-up | selector warm-up | cache warm-up | open (sections 404, 406) |
| selection rhythm | nightly | weekly | open |
| financing | broker SWAP | modelled per night | open (section 337) |
| journal vs dashboard totals | — | — | fixed 2026-10-22 (section 438) |

## Case check of the live decisions (2026-10-23, section 439)

`scripts/live_decision_check.py`, journal only (no broker access): 57
signals since 2026-10-01 02:02 UTC — 46 refused by the ADX gate, 7 by the
3-ATR floor (5 of them 4h pins the old replay would have traded), 4
entered. All four entries within tolerance on fill (|slip| <= 0.10 R),
size, stale exit and realised USD; planned risk of the US30 short of
2026-10-16 is 6.3 % above the replay's because the bot prices it at the
live quote 49 s after the cash close, not at the bar close (same size).
One refusal reads ADX 30.0 at a floor of 30: the reason prints one
decimal. After the restart of 2026-10-22 05:20 UTC three refusals were
journaled twice. Open: the replay's own signals for these weeks need bars
after 2026-09-20 (broker fetch, planned for the weekend when the bot is
idle).

## Test suite

`tests.test_runtime_guard_wiring.test_cluster_cap_journals_the_blocked_signal`
errors (TypeError) and `test_duplicate_instrument_entries`,
`test_reconcile_adoption`, `test_trade_cost_filter` and
`test_stale_exit_market_status` do not finish within 90 s — identical on
commit c7d172b, so not caused by the 2026-10-22 changes; open.

# Hurz — canonical status

Updated 2026-10-27. Short and current; history lives in `levers.md` and
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

## Phase 1 accepted (2026-10-27, section 455)

| criterion | evidence |
|---|---|
| unit correctness | strategy veto in USD, retirements kept (437); one USD ledger (438) rebooked at broker fills (452); current bot journal +0.91 USD against broker TRADE +0.93 USD |
| decision correctness | 53 of 55 journal signals the same, all four entries, 2 in the holiday pause (454); lists, pins and reservations identical on both archived nights (451); limits shared by code (test) |
| explained tolerances | ADX within 1.0 of the gate, price within 0.05 % (443, 454); spread +1.7 % of the result (450); financing within 5 % (448); fixed FX rates for 56 legacy closes (438) |
| reproducible reference | two runs from commit 4fea143 identical: 366 closes, +0.044042 USD/day |
| deployment and bot | runtime checkout on origin/main, one healthy instance |

Residuals, without trading consequence and listed rather than explained:
17 replay-only refusals on combinations whose list membership before
2026-10-24 was not recorded, one journal-only refusal (US100 2026-10-20
17:00), one refusal with a 0.08 % price gap (US30 2026-10-23 09:00), the
selector's 55-instrument universe against the replay's 27 (no list
differed). A new deviation reopens the acceptance.

Frozen reference (research data, searched over 430 times, not an
untouched test): code commit 4fea143; hourly bar cache of 2026-10-12, OOS
2020-09-23 to 2026-09-20; native 4h bars fetched 2026-10-24; spread
snapshot of 2026-09-04; overnight rates of 2026-09-29; today's vetoes and
pins. Forward evidence is the live bot: since 2026-10-13 (two-year
ranking) for the strategy, since 2026-10-22 for the probation.

## Checkpoint 2026-10-29 (written 2026-10-26)

- Opening balance: one USD ledger (section 438), rebooked at the broker's
  fills (section 452); table below. Current bot since 2026-10-01: 4 closes,
  +0.91 USD; since the probation start: none.
- Unit fix: the strategy veto sums in USD, all seven earlier retirements
  kept explicitly (section 437).
- Parity: decisions (sections 439, 443), 4h floor (439), wall-clock leash
  (440), warm-up (441), nightly rhythm (442, 445), native 4h bars (446),
  financing (448), spread (450), sizes (439), list, pins and reservations
  (451) checked, the bank-holiday pause modelled (453); the canonical
  reference reads 366 closes, +0.044042 USD/day (research data).
- Limits: never bound live; the bot's order path refuses the fourth
  same-direction index in the risk-on cluster (test), and the replay uses
  the bot's clusters, cap and concurrent limit (parity test, 2026-10-26).
- Open gaps: replay-only refusals on combinations whose list membership
  before 2026-10-24 is not recorded (section 454: 17, none would have
  traded), one journal-only refusal (US100 2026-10-20 17:00); the selector ranks 55 instruments against
  the replay's 27 (no list differed).

## Live configuration

- Demo account on Capital.com, one bot instance from `/host/data/hurz`
  (watchdog `scripts/_session_watchdog.sh`, host keepalive restarts it
  after a container restart).
- Strategies: momentum (1h) on the ranked list, plus five exclusive 4h
  pins. Active list of 2026-10-22 06:06 UTC: momentum on US30, DE40,
  US100, EU50, UK100 and ETHUSD (pinned); donchian_breakout_4h SILVER and
  NZDUSD, turtle_breakout_4h HK50, momentum_4h COPPER and CHFJPY.
- Selection: nightly refresh at 05:30 UTC over donchian_breakout,
  momentum and turtle_breakout (the retired two are dropped by the veto),
  ranked on two years of history since 2026-10-13 (commit 6fe17ae;
  section 401, the last adopted lever).
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

Canonical since 2026-10-26 (sections 439-442, 445, 446, 453): section
401's book with the bot's 4h entry rule (refused under 3 ATR) on the
broker's native 4h bars, the bot's wall-clock stale exits (hourly 24 h,
4h pins 96 h after the order), the bot's bank-holiday pause (no entries,
no stale exit and no nightly refresh on a DE, US, GB or CH-Zurich holiday,
Europe/Berlin date) and the selector's ranking (bar-booked trades,
indicators cold-started on the ranking window, re-ranked every night,
effective 06:05 UTC when the scheduler's refresh lands), on the bar cache
of 2026-10-12 (hourly) and the native 4h bars fetched 2026-10-24, OOS
2020-09-23 to 2026-09-20 (2,188 calendar days): 366 closes, +96.3649
USD, +0.044042 USD/day, daily SD 0.7433, worst day -4.7624 (momentum 306
closes +105.00 USD, 4h pins 60 closes -8.63 USD). Reproduced by
`scripts/momentum_two_year_ranking.py` (the 730-day arm). Earlier
figures: +0.017465 USD/day (section 401), +0.033727 (439), +0.044397
(440), +0.048009 (441), +0.045270 (442), +0.045478 (445), +0.044287
(446). These
corrections describe the bot's rules on searched history, not evidence of
an edge. This history has been searched over 430 times; it is research
data.

## Journal (realised USD, opening balance of 2026-10-22, section 438)

One ledger (`app/spot_trading/journal_ledger.py`, `scripts/journal_balance.py`)
converts every accepted live close to USD: booked USD since 2026-09-07
23:05 UTC; older rows recomputed from fill and exit, USD-quoted as is, by
the journaled USD risk, or at a fixed rate of 2026-10-12 (56 legacy closes
in EUR, GBP, JPY, CHF, HKD, AUD, CAD instruments; 0 unconverted).

| cohort | closes | USD |
|---|---:|---:|
| current bot since the veto regime 2026-10-01 02:02 UTC | 4 | +0.91 |
| of it since the two-year ranking 2026-10-12 22:25 UTC | 4 | +0.91 |
| of it since the probation start 2026-10-22 | 0 | 0 |
| current strategies before 2026-10-01 (history) | 31 | +26.10 |
| retired strategies (history) | 548 | -490.20 |
| whole history Capital.com since 2026-05-08 | 583 | -463.18 |
| Kraken Futures research (retired) | 19 | -10.62 |

Rebooked 2026-10-25 (section 452): 71 exits had been booked at the last
hourly bar's close because the close lookup missed positions held over a
day; they now carry the broker's fill and close time (before: current
bot +1.12 over 4, history -459.22 over 583). Three closes from June and
July are not in the broker's log and stay as booked.

Broker account against the journal (2026-10-24, section 444; EUR at
1.14866): since 2026-05-08 TRADE -505.16 USD, SWAP -20.92 USD, corporate
actions -0.28 USD, whole account -526.36 USD against the journal's
-458.96 (then). Current bot: journal +1.37 (+1.29 after section 452's
rebooking), broker TRADE +1.32, SWAP 0.00 USD (5 entries) — in line. The history differs by up to 41 USD per cohort and
is not reworked. Since 2026-10-24 the scheduler imports the account's
transactions nightly into `broker_transactions` (first import: 1,122
entries, TRADE -439.78 EUR, SWAP -18.21 EUR, corporate actions -0.24
EUR); the dashboard shows them beside the journal (section 447). Booked
since 2026-10-01 02:02 UTC: TRADE +2.07 EUR over 6 closes (3 of them
turtle positions opened before), SWAP -0.06 EUR over 8 entries.

Open: none. NZDUSD donchian_breakout_4h closed 2026-10-25 21:05 UTC at the
96-hour leash, -0.38 USD (five close orders rejected while the market
reopened, section 452). The figure -196.67 USD given
earlier on 2026-10-22 read `realized_pnl` as booked (signal price, quote
units) and is superseded.

## Known deviations between replay, selector and bot

| item | bot | replay | status |
|---|---|---|---|
| strategy veto units | quote currency summed across instruments | follows the bot | fixed 2026-10-22 (section 437) |
| 4h signal below the 3-ATR floor | refused | stop set to the floor | fixed 2026-10-23 (section 439) |
| stale exit | 24 wall-clock hours (4h: 96) | 24 bars | fixed 2026-10-23 (section 440) |
| indicator warm-up | selector cold-starts on its window | cache warm-up | fixed 2026-10-23 (section 441) |
| selection rhythm | nightly, list lands ~06:05 UTC | weekly | fixed 2026-10-23/24 (sections 442, 445) |
| financing | broker SWAP, imported nightly (section 447) | fixed rates of 2026-09-29 per rollover | checked 2026-10-25: since the rates broker -0.11 EUR, model -0.11 EUR, before -15.93 against -15.13 (section 448) |
| journal vs dashboard totals | — | — | fixed 2026-10-22 (section 438) |
| 4h bars | broker 4h bars | hourly bars resampled (ATR 0-2.5 % low) | fixed 2026-10-24: native 4h bars (section 446) |
| list history | overwritten nightly | — | archived since 2026-10-24 (section 443) |
| list, pins, reservations | selector over 55 instruments, PF uncapped in the score | `active_order` over 27, PF capped at 5 | checked 2026-10-25: both archived lists (2026-10-24, 2026-10-25) identical, trade counts identical (section 451) |
| limits | max 8 open, 3 per cluster and direction, 1 per instrument | the same caps | not reached live since 2026-10-01 (59 decisions, none refused by a cap) |
| spread | live quote at the order | snapshot of 2026-09-04 per instrument | checked 2026-10-25: the reference's 387 closes cost 10.12 USD, 11.80 at the medians of 16,934 live quotes (+1.68 USD, 1.7 % of the result; section 450) |
| size | fill risk at the live quote | planned at the bar close | checked 2026-10-23: four entries within tolerance (section 439) |
| bank holidays | idles the whole Berlin day (no entries, exits, refresh) | traded through | fixed 2026-10-26: modelled (section 453; explains ETHUSD 2026-10-12) |
| session log | truncated on every start | — | kept, last 60 runs, since 2026-10-25 (section 449) |

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
journaled twice.

Replay against bot, signal by signal (2026-10-26, section 454, on the
current replay rules: native 4h bars, holiday pause): of 55 journal
signals between 2026-10-01 03:00 and 2026-10-24 00:00 UTC, 53 have the
same decision and price (all four entries among them); 2 replay signals
fell on the bank-holiday pause of 2026-10-12. Unexplained and without
trading consequence: 17 replay-only refusals on combinations whose list
membership was not yet archived (13 before the two-year list, four on
FR40), one journal-only refusal (US100 2026-10-20 17:00) and one refusal
with the same decision but a 0.08 % price gap (US30 2026-10-23 09:00).
Section 443's NZDUSD 4h difference is gone on native 4h bars.

## Test suite

Checked 2026-10-26, each of the 180 modules on its own with a 120-second
limit: none hangs, 177 pass in the working checkout. The five modules
reported as hanging since 2026-10-22 finish in about a second; three of
them failed because the stop-out guard (fail-closed without a journal)
refused every order and a fixture pair (ARBUSD) is blocked since commit
bf29bed; both fixed in the tests. Two modules need the runtime files
(`tmp/`, the active list) and pass with them; one research fixture was
stale and is fixed.

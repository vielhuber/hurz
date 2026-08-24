# Measured findings, August 2026

Written after a full audit of the live journal (495 closed Capital.com
demo trades, 11 May – 24 Aug 2026) and an exhaustive walk-forward
search. It records what was *measured*, so later work starts from the
evidence instead of re-deriving it — several of the findings below
contradict what the code previously assumed.

## 1. There is no demonstrated edge

A walk-forward search over **154,560 parameter combinations** — 14
instruments, 9 strategy families, 1h/4h/1d, RR 1.0–3.5, stop 0.75–2.0
ATR, holds 6–48 bars, ADX off plus thresholds 15–40, history from
30 Nov 2023 — produced:

- 1,216 pooled-positive variants with >= 50 trades
- 93 variants with >= 4/5 positive segments
- **0 variants surviving Bonferroni correction**

No variant on the 1h timeframe, which is what the bot trades, passed
the stability bar at all. Every corrected lower confidence bound was
negative.

**Consequence:** parameter tuning on this data set is finished. Testing
more variants finds more noise, not more edge. A genuine improvement has
to come from a different signal source, validated out-of-sample.

## 2. Backtest statistics are not predictive here

`bollinger_rev` has the best backtest expectancy of any strategy
(+0.391 E[R] on DE40, +0.520 for `rsi_mr` on DE40) and the worst live
result: **-0.128 R over 121 trades, -120.57 USD**.

The obvious rescue — "it only lost on expensive instruments" — is false.
On *cheap* instruments it did worse:

| strategy | cheap instruments | expensive instruments |
|---|---:|---:|
| bollinger_rev | -0.179 R (n=76) | -0.042 R (n=45) |
| rsi_mr | -0.473 R (n=14) | -0.188 R (n=11) |
| stochastic_mr | -0.169 R (n=38) | -0.187 R (n=24) |

**Consequence:** selection must run on forward results. The live vetoes
in `pair_selector` retire proven losers; nothing promotes a combo on
backtest numbers alone.

## 3. Trading cost was the dominant loss

Over 372 trades (1 Jul – 24 Aug): realised **-118.52 USD**, estimated
spread **379.94 USD** — the spread is **320.6 %** of the net loss. The
same trades without cost would have returned **+261.42 USD**.

Measured execution confirms it: across 299 trades with a valid fill the
entry disadvantage was 169.51 USD, i.e. 52.1 % of the estimated
round-trip spread — almost exactly the expected half-spread. Over 82 %
of fills landed on the unfavourable side.

Cost as a share of risk decides tradeability. The venue minimum stop is
1.05 % of price, so a 0.5 % spread — ordinary for a crypto alt — burns
~48 % of the risk budget before the trade can work. APTUSD needs a
118.9 % hit rate to break even.

Expectancy by cost share, over 481 trades, is strictly monotonic:

| cost / risk | trades | mean R | PnL |
|---|---:|---:|---:|
| <= 5 % | 204 | -0.063 | **+31.20** |
| <= 10 % | 316 | **-0.047** | -12.88 |
| <= 20 % | 323 | -0.066 | -75.97 |
| <= 40 % | 418 | -0.104 | -140.00 |
| unfiltered | 481 | -0.111 | -195.60 |

Monotonic across nine thresholds is a mechanism, not a fitted cut. The
ceiling is set at 10 %.

## 4. Where the loss is NOT

- **Exits.** Bot-side exits returned **+46.68 USD**. Full winners
  (+968.07) and full stops (-907.29) nearly cancel; the loss comes from
  the ratio of stops to targets, i.e. signal quality.
- **Direction.** Long -0.074 R, short -0.163 R — no exploitable skew.
- **Asset class.** FX -0.143 R, indices/commodities -0.111 R, crypto
  -0.103 R — the same mild negative everywhere.
- **Stop placement.** Broker stops were present and correct; the one
  -13.57 USD outlier was a weekend gap on ATOMUSD, not a software fault.

## 5. What did look profitable

Trend-following on cost-viable instruments: **+0.016 R over 190 trades,
+117.56 USD**, against -0.296 R on expensive ones. BTCUSD alone returned
**+0.209 R over 52 trades**.

An open lead, deliberately not acted upon: signals raised by two
strategies on the same bar returned +0.108 R against -0.161 R for lone
signals. But the paired trades are the same position booked twice, so
they are perfectly correlated — collapsed to 44 independent events the
difference gives **t = 1.65**, and the 95 % interval spans
[-0.197, +0.414]. `scripts/forward_report.py` tracks it forward.

## 6. The daily-return target

At 3 USD risk and the observed 6.76 trades per calendar day, a working
system yields roughly **1–4 EUR per day**:

| expectancy | per day |
|---:|---:|
| 0.05 R | 0.87 EUR |
| 0.10 R | 1.73 EUR |
| 0.20 R | 3.47 EUR |
| 0.30 R | 5.20 EUR |

50 EUR (58.50 USD at 1.1699) would need **98–390 trades per day** at
this risk, or an expectancy of **2.88 R** at this frequency. Neither is
a realistic planning assumption. Reaching it requires both a
forward-proven edge and roughly ten times the risk capital — at 0.20 R
and current frequency, ~43 USD risk per trade, ~3,514 USD notional per
position and >= 4,325 USD of risk capital at 1 % per trade.

`app/spot_trading/edge_scaling.py` raises the budget automatically, but
only once >= 40 out-of-sample trades show a lower confidence bound above
zero. `app/spot_trading/risk_guard.py` stops new entries after 6 R of
daily loss, which matters most precisely when that scaling has kicked in.

## 7. Structurally different signal sources also failed

A preregistered experiment then tested signal structure rather than tuning
the existing indicators again. Capital.com one-hour mid-price history from
30 Nov 2023 through 23 Aug 2026 was split before evaluation:

- development: 30 Nov 2023 – 31 Jan 2026
- untouched holdout: 1 Feb – 23 Aug 2026
- 14 audited instruments (six FX majors and eight crypto instruments)
- one fixed execution profile: 1 ATR stop widened to the venue's buffered
  1.05% minimum, 1.5 R target, 24 bars maximum hold, shared 3 USD / 250 USD
  sizing and actual broker quantity constraints
- full audited spread charged; signals above the live 10% cost/risk ceiling
  counted as cost-skipped rather than trades

There were **18 distinct rule variants** and **22 phase evaluations**: all 18
on development, followed by exactly one frozen champion from each of four
families on the holdout. Development confidence bounds divide the one-sided
5% alpha by 18; holdout bounds divide it by four. Bonferroni was chosen over
Benjamini-Hochberg because selecting any apparent winner can allocate
capital, so family-wise error is the relevant risk. Standard errors are
clustered by ISO week to avoid treating simultaneous instruments as
independent observations.

Development results (LCB is the Bonferroni-corrected one-sided lower
confidence bound):

| variant | trades | E[R] | PnL USD | positive segments | LCB | cost-skipped |
|---|---:|---:|---:|---:|---:|---:|
| mtf_break20_4h_ema20 | 2,968 | -0.019 | -140.34 | 1/5 | -0.070 | 8,354 |
| mtf_break55_4h_ema20 | 2,245 | -0.016 | -87.10 | 0/5 | -0.075 | 5,796 |
| mtf_break20_1d_ema20 | 2,220 | -0.012 | -60.86 | 3/5 | -0.070 | 5,808 |
| mtf_break55_1d_ema20 | 1,545 | -0.003 | -12.33 | 2/5 | -0.072 | 3,713 |
| vol_atr_q25_break20 | 1,119 | -0.023 | -69.75 | 1/5 | -0.095 | 3,180 |
| vol_atr_q25_break55 | 559 | -0.056 | -87.51 | 1/5 | -0.159 | 1,278 |
| vol_atr_q75_break20 | 1,613 | -0.022 | -88.70 | 2/5 | -0.103 | 3,412 |
| vol_atr_q75_break55 | 1,118 | +0.001 | +7.19 | 3/5 | -0.090 | 2,332 |
| vol_squeeze_q10_break20 | 1,433 | -0.002 | -2.68 | 2/5 | -0.067 | 3,851 |
| vol_squeeze_q20_break20 | 2,130 | -0.008 | -40.50 | 1/5 | -0.065 | 5,627 |
| lead_btc_1h_z075 | 898 | -0.024 | -68.67 | 3/5 | -0.143 | 10,484 |
| lead_btc_1h_z125 | 478 | +0.044 | +50.40 | 5/5 | -0.110 | 4,986 |
| lead_btc_4h_z075 | 756 | +0.009 | +17.78 | 3/5 | -0.108 | 11,830 |
| lead_btc_4h_z125 | 376 | +0.088 | +90.32 | 3/5 | -0.084 | 5,800 |
| relative_24h_rebalance12h | 1,558 | -0.034 | -122.04 | 1/5 | -0.087 | 2,525 |
| relative_24h_rebalance24h | 957 | -0.067 | -144.94 | 1/5 | -0.132 | 1,270 |
| relative_72h_rebalance12h | 1,376 | -0.042 | -135.03 | 1/5 | -0.100 | 2,487 |
| relative_72h_rebalance24h | 903 | -0.050 | -97.36 | 2/5 | -0.113 | 1,245 |

No development rule had a corrected lower bound above zero. The positive
BTC-lead rows were the only plausible lead, but even `lead_btc_1h_z125`,
which was positive in all five development segments, still had a corrected
LCB of -0.110. Per the preregistration, family champions were selected by
the greatest corrected LCB rather than by the most attractive point estimate.

The one-time holdout results were:

| frozen family champion | trades | E[R] | PnL USD | positive segments | LCB | cost-skipped |
|---|---:|---:|---:|---:|---:|---:|
| mtf_break20_4h_ema20 | 730 | -0.078 | -133.74 | 0/5 | -0.165 | 2,015 |
| vol_squeeze_q20_break20 | 544 | -0.025 | -35.18 | 2/5 | -0.110 | 1,319 |
| lead_btc_4h_z125 | 67 | -0.004 | +0.90 | 3/5 | -0.354 | 1,186 |
| relative_24h_rebalance12h | 413 | -0.024 | -28.30 | 2/5 | -0.113 | 652 |

All four holdout samples exceeded the 50-trade floor, and **none passed**.
Multi-timeframe confirmation failed most clearly. Volatility filtering and
relative strength stayed mildly negative. BTC leadership's small positive
dollar result came with negative per-trade expectancy, only 3/5 positive
segments and a very wide negative corrected bound; it is not an edge.

Time/session/weekday variants were intentionally not opened: their arbitrary
boundaries add an especially overfit-prone search after both the 154,560-rule
indicator search and this structural test failed. No new live strategy is
justified, and `donchian_breakout_v3` remains disabled.

## 7. The account is the binding constraint

Measured 24 Aug 2026: the Capital.com demo account holds **474.43 EUR**
(~555 USD) of available funds.

Section 6 puts the requirement for 50 EUR/day at roughly 4,325 USD of
risk capital and 3,514 USD of notional per position — at seven
concurrent positions, about 28,000 USD gross notional. The account
covers **under an eighth** of the risk capital alone.

What this balance actually supports, at an aggressive 1 % risk per
trade (4.74 EUR ≈ 5.5 USD) and the observed 6.76 trades per day:

| expectancy | per day |
|---:|---:|
| 0.05 R | 1.86 USD / 1.59 EUR |
| 0.10 R | 3.72 USD / 3.18 EUR |
| 0.20 R | 7.44 USD / 6.36 EUR |
| 0.30 R | 11.15 USD / 9.53 EUR |

So even with an exceptional edge and 1 % risk per trade — which on this
balance means a 195 USD drawdown wipes out a third of the account — the
ceiling is under 10 EUR/day. The 50 EUR target needs a proven edge
**and** roughly eight times this capital. Neither is a software
question.

## 8. A trap for anyone running the analysis scripts

`scripts/spot_backtest.py` and any ad-hoc analysis fail with
`401 error.invalid.details` when the environment is loaded from the
shell (`set -a; . ./.env`). The cause is not a session limit or bad
credentials: bash expands `$` and other metacharacters inside the
quoted secret, so a password containing them arrives corrupted.

Load the environment the way the bot does instead:

```python
from app.utils.singletons import settings
settings.load_env()
```

This cost one aborted backtest run and one misdiagnosis — the failure
was first attributed to the running bot holding the only allowed broker
session, which was wrong.

## 9. Historical PnL understates the loss by ~216 USD

Until 21 Aug 2026 the journal computed `realized_pnl` from the SIGNAL
price rather than the actual fill. Commit `8dd3f30` changed it to the
recorded entry fill; every trade closed from 21 Aug 12:05 onward books
against the fill, and the transition is clean.

Of the 415 closed Capital.com trades whose fill differs from the signal
price, **360 are booked against the signal price**. Recomputing them
against the fill gives an additional **-216.05 USD**.

| basis | trades |
|---|---:|
| fill price (correct) | 55 |
| signal price (understates) | 360 |

By month, signal-priced closes: May 29, June 68, July 197, August 66 —
all August cases fall before the fix.

Two consequences for anything read out of this table:

- The realised result to 24 Aug is closer to **-450 USD** than to the
  -234.57 USD the column sums to. Entry slippage was simply never
  booked, which is also why the independent spread estimate of 379.94
  USD looked so large next to the recorded loss.
- Every expectancy derived from pre-21-Aug rows is optimistic, this
  document included. The vetoes calibrated on them are therefore too
  lenient rather than too strict — combos were retired on understated
  losses, so none of them was retired unfairly.

The 360 rows were left untouched: correcting them means a mass update
of production data on a derived column, which is an owner decision, and
the forward window that decides anything from here on is already
booking against the fill.

## 10. Corrected expectancy: the filters do not reach break-even

Recomputing every close against its actual fill (section 9) changes the
headline numbers this document reported earlier. The corrected column is
the one to use.

| configuration | trades | as booked | corrected |
|---|---:|---:|---:|
| all trades | 495 | -234.57 (-0.1158 R) | **-450.84 (-0.1817 R)** |
| minus retired strategies | 220 | +63.59 (-0.0549 R) | **-26.61 (-0.1036 R)** |
| minus cost-blocked pairs too | 191 | +101.77 (-0.0054 R) | **+28.02 (-0.0378 R)** |

The filters are worth **0.144 R per trade** — a real improvement, and
the largest single effect measured in this project. They do not reach
break-even: the best configuration still expects **-0.0378 R**.

The dollar column turning positive at +28.02 while expectancy stays
negative is a size artefact, not an edge: a handful of large winners
outweigh many small losers. Under the risk-based sizing now in force
every trade carries the same risk, so the R figure is the one that will
govern from here.

Earlier revisions of this document, and several progress reports, cited
**+0.0134 R** for the filtered configuration. That number came from
signal-priced rows and was too optimistic by roughly 0.05 R.

## 11. The cost-threshold monotonicity was an artefact

Section 3 justified the 10 % cost ceiling with a strictly monotonic
relationship between cost share and expectancy across nine thresholds —
"a mechanism, not a fitted cut". Recomputed against actual fills, that
monotonicity disappears.

Trend-following trades with audited spreads, retired strategies already
excluded, PnL booked against the fill:

| cost ceiling | trades | share | mean R | PnL |
|---|---:|---:|---:|---:|
| <= 2 % | 51 | 23.3 % | -0.0558 | +1.81 |
| <= 3 % | 70 | 32.0 % | **-0.1744** | -53.83 |
| <= 5 % | 126 | 57.5 % | -0.0530 | +5.31 |
| <= 8 % | 175 | 79.9 % | -0.0502 | +45.80 |
| <= 10 % | 187 | 85.4 % | -0.0375 | +45.39 |
| <= 15 % | 190 | 86.8 % | **-0.0328** | +47.39 |
| unfiltered | 219 | 100 % | -0.0995 | -7.24 |

The <=3 % band sitting well below both its neighbours is the tell: a
real cost mechanism cannot make a trade worse by being cheaper. That
band is noise, and so was the clean ordering it used to sit in.

What survives is the coarse effect, and it is substantial: filtering at
all lifts expectancy from **-0.0995 R to about -0.033 R**, worth roughly
0.067 R per trade. What does not survive is the precision — anywhere
between 8 % and 15 % performs within 0.02 R, which is inside the noise
of a 190-trade sample.

The ceiling stays at 10 %. It sits near the flat optimum, it is the more
conservative end of the plateau, and moving it to 15 % to capture three
more trades and 0.005 R would be exactly the kind of fitting this
document keeps warning about.

## 12. Where the running configuration stands

All figures below book each close against its actual fill (section 9)
and exclude abandoned rows.

| configuration | trades | mean R | PnL |
|---|---:|---:|---:|
| whole history | 495 | -0.1817 | -450.84 |
| minus retired strategies | 220 | -0.1036 | -26.61 |
| minus retired combos too | 171 | -0.0688 | +16.46 |
| **combos actually live today** | **132** | **+0.0283** | **+90.35** |

**Read the last row with care.** It is not a forecast. Those combos are
on the list precisely because they did not look bad in this same data,
so the number partly reflects a selection made after the fact. Quoting
it as the system's expectancy would repeat the mistake that filled the
original pinned list with losers.

What the table does support is the ordering: every filter step improves
expectancy, monotonically, and the gap between -0.1817 and +0.0283 R is
what all the changes in this project add up to.

Only the forward window settles it. And even if +0.0283 R survives
out-of-sample, at 3 USD of risk and roughly five trades a day it is
**about 0.42 USD per day** — two orders of magnitude below the 50 EUR
target, which sections 6 and 7 show to be out of reach on this account
regardless.

## 13. The backtest was pricing the untradeable instruments as cheap

`_fee_for` charged a flat **0.05 % per side** to any Capital.com crypto
pair without an entry in the 14-instrument spread audit. The broker
actually quotes about **0.25 % per side** on the alts and **2.50 %** on
APTUSD — an understatement of ten- to fiftyfold, concentrated precisely
on the instruments section 3 shows to be unhandelable.

This closes a loop that ran through the whole system:

1. the backtest made alts look cheap, so they scored well;
2. the pair selector picked them up;
3. the live path traded them and lost to the spread;
4. the PnL column booked against the signal price and hid the slippage;
5. the vetoes read that column and retired them too slowly;
6. the dashboard summed the same column and showed a profit.

Every step biased the same way — toward expensive instruments.

The percentage audit covering all 38 traded instruments is now consulted
before the flat defaults. A 30-day run on 1h bars after the change:

| pair | trades | E[R] |
|---|---:|---:|
| BTCUSD | 7 | +0.449 |
| ETHUSD | 8 | +0.187 |
| ADAUSD | **0** | — |
| DOTUSD | **0** | — |
| DE40 | 8 | -0.404 |

ADAUSD and DOTUSD now produce **no tradeable signals at all**: the cost
filter rejects every one. Under the old fee they would have produced a
full set of results and gone straight into the candidate pool.

Note that `data/spot_backtest_results.json` was generated under the old
fee and is therefore optimistic for every alt in it. The nightly refresh
regenerates it with the corrected costs.

## 14. There is no gross edge either — retracting section 3's headline

Section 3 concluded that the strategies carry a positive gross edge of
**+261.42 USD** which trading costs then consume. That figure was
computed against the booked PnL of -118.52 USD, and section 9 shows that
column understates the loss by entry slippage.

Against actual fills:

| | USD |
|---|---:|
| realised, booked against fill | **-450.84** |
| estimated spread paid | 379.94 |
| hypothetically cost-free | **-70.90** |

**Even with zero trading costs the system loses.** The gross edge does
not exist; it was an artefact of the same signal-price booking that
inflated every other figure in this project.

The break-even arithmetic says the same thing independently. With
`p_BE = (1 + cost/R) / (1 + RR)` at the realised RR of 1.5:

| venue and instrument | cost/R | break-even hit rate |
|---|---:|---:|
| Capital, crypto alt (0.5 % / 1.05 % stop) | 47.6 % | 59.0 % |
| Capital, alt with stop widened 2x | 23.8 % | 49.5 % |
| Capital, BTC/ETH | 5.7 % | 42.3 % |
| Capital, index | 0.6 % | 40.2 % |
| Kraken Futures (0.1 % RT, 1 % stop) | 10.0 % | 44.0 % |
| **cost-free, any venue** | **0 %** | **40.0 %** |

The system achieves **33.0 %**. It misses break-even by seven points
*before any cost is charged*. Moving to a venue ten times cheaper buys
two points of the seven.

To break even at 33 % the strategies would need an RR of **2.03 even
cost-free**, and 2.33 on Kraken Futures — against the 1.5 they actually
realise.

**So the venue is not the problem and never was.** Cheaper execution
would have reduced the loss, not removed it. What is missing is
predictive value in the signals, which sections 1 and 5 already showed
by exhaustive search. The cost work in this project was still worth
doing — it removed a real and large drag, and it stopped the system
trading instruments it could never win on — but it was never going to
be sufficient on its own.

## 15. The risk-reward ratio is not the missing lever either

Section 14 showed the strategies need RR 2.03 to break even at their
33 % hit rate, against the 1.5 they trade. That makes RR the last
untested structural knob, so it was swept over six values on the three
surviving candidates plus GOLD, BTCUSD and ETHUSD (capital_com, 1h,
30 days).

The sweep ran on `donchian_breakout`, which carries no table entry, so
its RR varied correctly. For the three strategies that *do* carry one
(`donchian_breakout_v2`, `_v3`, `donchian_trail`) the flag was silently
discarded, and a sweep there would have reported six identical numbers
as if they were six experiments.

That override was deliberate, though: the results file feeds the pair
selector, so a persisted run at an off-live RR would rank pairs on an
exit target nothing executes. Both concerns now hold — `--rr` is
honoured, and a run that departs from the live value refuses to persist.

| RR | n | win% | E[R] | SE | t | p (1-sided) |
|---:|---:|---:|---:|---:|---:|---:|
| 1.0 | 57 | 59.6 | +0.153 | 0.134 | 1.14 | 0.127 |
| 1.5 | 50 | 48.0 | +0.220 | 0.180 | 1.23 | 0.110 |
| 2.0 | 49 | 42.9 | +0.307 | 0.215 | 1.43 | 0.076 |
| 2.5 | 45 | 31.1 | +0.115 | 0.247 | 0.47 | 0.321 |
| 3.0 | 44 | 27.3 | +0.131 | 0.273 | 0.48 | 0.316 |
| 4.0 | 41 | 26.8 | +0.423 | 0.348 | 1.22 | 0.112 |

Best t is **1.43** at RR 2.0. Six values tested means Bonferroni
`alpha = 0.0083`, so t would have to exceed **2.64**. Nothing comes
close, and the widest apparent improvement (+0.423 at RR 4.0) carries
the largest standard error of all — it is the noisiest cell in the
table, not the best one.

The shape settles it independently. A real mechanism would trend: hit
rate falls as the target widens, and expectancy either rises or falls
with it. Instead the differences run **+0.067, +0.087, -0.192, +0.016,
+0.292** — two sign changes across five steps. The 2.5 and 3.0 cells
collapse and 4.0 jumps back up, which no exit-target mechanism produces.

**RR was left at its configured values.** The sweep is recorded here so
the next person does not rerun it and stop at the first ✅.

## 16. Stale backtest blocks rank systematically too high

The results file the pair selector ranks from still held blocks
generated on **2026-05-15**, three months old, alongside `capital_com`
blocks from 2026-07-10. Everything predating 2026-08-24 was priced on
the old fee table, which section 9 shows undercharged some instruments
by an order of magnitude.

That makes staleness here worse than ordinary staleness. An out-of-date
block is not merely imprecise — it is biased in one direction, because
the instruments priced most wrongly are exactly the expensive ones that
then rank highest. It is the same distortion this project has been
unwinding, preserved in a file that no longer gets fully rewritten.

`rank_pairs` now drops blocks older than `max_age_days` (30). A block
whose timestamp is missing or unparsable counts as stale: after the fee
correction, "unknown age" cannot be distinguished from "priced on the
old table". `max_age_days=None` disables the guard for analysis.

The guard changes nothing about the current selection — that ranking was
already empty, because the refreshed backtests yield around 15 trades per
combination against a `min_trades` floor of 30, and the live basket runs
off pins rather than the ranking. It matters the moment those filters are
loosened, which is precisely when a three-month-old block priced on the
wrong fees would otherwise come back to the top.

## 17. The backtest measured a different holding leash than the one traded

`donchian_trail` runs a **240-bar** leash live — a deliberate multi-day
allowance so the ATR trail can develop. The backtest carried its own
copy of the 24-bar default and never consulted the strategy table, so
every result for that strategy described a leash ten times shorter than
the one actually being traded. Two of the eight open positions
(EURUSD, US500) run this strategy, so it is not a dormant path.

This is the same class of defect as the `--rr` override in section 15
and the fee tables in section 9: live and backtest silently disagreeing,
with the backtest reading the friendlier of the two.

Measured at both leashes (capital_com, 1h, 30 days, five instruments):

| leash | n | win% | E[R] | SE | t | p |
|---|---:|---:|---:|---:|---:|---:|
| 24 bars (what was measured) | 36 | 16.7 | +0.227 | 0.376 | 0.60 | 0.273 |
| **240 bars (what is traded)** | 31 | 19.4 | **+0.175** | 0.431 | 0.41 | 0.342 |

The traded configuration is the **weaker** of the two, so the persisted
number for this strategy was optimistic — the direction this project
keeps finding. Both are far below significance (t of 0.41 against the
2.0 a single test would need), so nothing here is an edge either way.

The backtest now imports `_DEFAULT_MAX_HOLD_BARS` from the shared module
instead of redeclaring it, reads `max_hold_bars_for()` for the live
value, and refuses to persist a run at an off-live leash. A test asserts
that `max_hold_bars_for()` and `stale_exit_after_seconds()` agree, so the
two paths cannot drift apart again.

## 18. Nine in ten backtest trades are signals live never opens

The live loop drops a signal outright when its stop sits inside the 1 %
floor (`evaluate_pair` returns None). The backtest had no such step: it
went straight to widening the stop to the venue minimum and traded the
signal. So the two paths did not disagree on a parameter — they
disagreed on *whether the trade happens at all*.

Measured on donchian_breakout across seven instruments, capital_com 1h,
30 days:

| | trades |
|---|---:|
| backtest without the floor | 93 |
| **surviving the floor, i.e. live-tradeable** | **6** |

**87 of 93 signals — 93 % — never open live.** Expectancy barely moves
(+0.197 → +0.194), so this is not a quality filter in disguise; it is a
volume finding. Every backtest in this project measured roughly fifteen
times more trades than the configuration can actually take.

That resolves several loose ends at once:

- The `min_trades = 30` selection floor was being cleared by samples
  made up almost entirely of trades that cannot occur. A combination
  "proven" on 40 backtest trades rests on about three real ones.
- The ranking going empty after the fee fix was not a bug. It is what
  an honest sample size looks like.
- The frequency gap is far wider than section 11 estimated.

Rescaling the target arithmetic on live-tradeable volume:

| | |
|---|---:|
| live-tradeable trades | 0.20/day on 7 instruments |
| scaled to the 59 traded instruments | **1.69/day** |
| at E[R] +0.194 and 3 USD risk | 0.58 USD/trade |
| **achievable** | **0.98 USD/day** |
| target (50 EUR) | 58.49 USD/day |
| **gap** | **60x** |

Reaching 50 EUR/day would need **101 trades/day** against the ~1.7 the
strategies actually generate — and that calculation still grants a
positive expectancy that sections 5 and 14 show does not survive
correction. The frequency ceiling is structural: it comes from how often
these signal rules fire with a stop wide enough to be worth trading, not
from any setting that can be turned up.

The backtest now applies the floor before widening and prints per-pair
how many signals it removed, so the drop can never again read as "this
pair produced few signals".

## 19. The stop floor removes the better half, not the worse one

Section 18 makes the 1 % stop floor the binding constraint on trade
frequency — it removes about fifteen signals in sixteen. Its
justification, recorded in `autotrade.py`, was that narrow stops lose
more because spread consumes a larger share of a small risk budget:
"-0.31R under 1 %, -0.09R at or above".

Rebooked against actual fills, that reverses:

| stop distance | n | E[R] booked | **E[R] against fill** | t |
|---|---:|---:|---:|---:|
| < 1 % (rejected) | 56 | -0.208 | **-0.058** | -0.33 |
| ≥ 1 % (traded) | 379 | -0.322 | **-0.432** | -3.85 |

Difference, narrow minus wide: **+0.374 R** (SE 0.210, t 1.78).

The original finding was an artefact of the same signal-price booking
that distorted everything else — and it is self-reinforcing in the worst
way, because slippage is a *larger* share of a small R, so the very
column that hides slippage makes narrow stops look worst exactly where
they are hurt most by the error.

By band, against fills:

| band | n | E[R] | t | USD |
|---|---:|---:|---:|---:|
| 0.00–0.50 % | 10 | -0.143 | -0.38 | -0.86 |
| 0.50–0.75 % | 13 | -0.308 | -0.72 | -34.86 |
| 0.75–1.00 % | 33 | +0.066 | 0.29 | +74.89 |
| **1.00–1.50 %** | **324** | **-0.313** | **-3.41** | **-394.73** |
| 1.50–2.50 % | 44 | -1.262 | -1.85 | +48.25 |
| > 2.50 % | 11 | -0.611 | -2.91 | -113.34 |

The one positive band carries t = 0.29 across 33 trades, so it is not an
edge — but the band the floor steers everything into, holding 324 of 435
trades, loses at t = -3.41.

**The floor stays on.** Not for its original reason, which is wrong, but
because expectancy is negative on both sides. Removing it improves
blended expectancy from -0.432 to -0.384 while multiplying volume about
fifteenfold, which increases the dollar loss. Trading more of a negative
edge loses faster, however the edge is sliced.

It should be revisited the moment expectancy turns positive, because at
that point it becomes the single largest constraint on frequency — and
section 18 shows frequency is 60x short of the target.

## 20. R-multiples were distorted by near-zero risk denominators

Chasing why the 1.00–1.50 % stop band loses turned up a measurement
fault in this project's own statistics rather than a property of the
strategies.

The trail: widened stops were suspected of harming results, but trades
pinned at the venue minimum return -0.035 R against -0.686 R for
naturally-wide stops — the opposite of the hypothesis. That gap then
turned out to be exit composition, not stop width: the narrow class is
44 % manual exits (timeouts near zero R), the wide class only 8 %.
Holding exit type constant shrinks the difference to +0.184 R at t 1.38,
which is nothing. **No stop-ceiling filter is warranted.**

But the worst cell — manual exits on wide stops, E[R] -4.335 across 19
trades — totals **-6.12 USD**, about 1 % of the loss. Its R values reach
-19 and -22 because those May trades risked fractions of a cent:

| risk denominator | USD |
|---|---:|
| P0 | 0.0008 |
| P5 | 0.83 |
| P50 | 3.25 |
| P100 | 39.72 |

Seventeen trades (3.9 %) risk under 0.30 USD, and they move the headline:

| calculation | E[R] | t |
|---|---:|---:|
| all trades, unweighted mean of per-trade R | -0.384 | -3.82 |
| excluding risk < 0.30 USD | -0.226 | -3.31 |
| excluding risk < 1.00 USD | -0.184 | -3.64 |
| **capital-weighted, SUM(pnl)/SUM(risk)** | **-0.158** | — |

The sign never changes and significance holds throughout, so no earlier
conclusion reverses — the system still loses, and every negative verdict
in this document stands. But the magnitude was overstated by more than
a factor of two, and the capital-weighted -0.158 R is the figure an
account actually experiences.

`_realized_expectancy` computed `SUM(pnl/risk)/n`, so both retirement
vetoes ran on the distorted figure. They now compute
`SUM(pnl)/SUM(risk)`. One combination changes verdict:
`donchian_breakout/ETHUSD` reads -0.254 unweighted against -0.092
weighted, so it was being retired over an artefact.

Current vetoes on the corrected basis: seven combinations and five
strategies retired.

## 21. The two risk controls read the distorted figure too

Section 20 fixed the retirement vetoes. The same two faults — booking
against `realized_pnl`, and dividing by the risk each trade happened to
take — were also present in both controls that decide how much risk the
system runs.

**Daily loss guard.** It summed per-trade R against taken risk, so an
oversized position reported roughly -1R however much it cost. A trade
risking 39 USD against the 3 USD budget consumed thirteen budget units
and read as one; conversely a micro position losing two cents read as
-20R and could block a day's entries by itself. Both failure directions
defeat the control. It now sums the fill-based PnL and divides by the
budgeted risk, so "6R" means six budget units — what the limit was
always meant to express. Across the journal the booked column understated
the total by 36 % (-57.3R against -89.9R).

**Edge scaling.** This one decides whether to *increase* risk, so a
distorted input is worst here. It now books against fills and ignores
positions sized below 10 % of the budget: their near-zero denominator
both shifts the mean and inflates the variance the confidence bound is
built from, and an inflated variance widens the bound in a way that can
cut either direction. Current state is unchanged in practice — 1 of the
40 required out-of-sample trades, so risk stays at base regardless.

Neither fix changes the verdict on profitability. They matter because
they are the two places where a wrong number does damage rather than
merely misinform.

## 22. Tightening the selection filters is not supported either

The nightly scheduler has run in a deliberately relaxed configuration
since 2026-07-01: `--min-pf 0.8`, `--min-er -0.2`, stability gate off,
`min_trades 10`. The recorded reasoning was that backtest statistics
proved non-predictive, so the system should cast a wide net and let live
results decide. That admits combinations with negative in-sample
expectancy by design, which sits oddly against a profitability goal —
so it is worth asking whether the relaxation cost anything.

Splitting live results by whether a combination would also have passed
the strict filters (`pf >= 1.0`, `E[R] >= 0`):

| weighting | passed strict | admitted only by relaxation | implies |
|---|---:|---:|---|
| capital-weighted | -0.364 R | -0.047 R | relaxation better |
| unweighted mean | -0.294 R | -0.385 R | strict better |

**The two weightings disagree on the sign**, and the unweighted
difference is +0.091 R at t = 0.49. A real effect does not flip
direction with the choice of weighting; this one is noise in both
readings.

So there is no evidence that tightening the filters would improve
results, and none that the relaxation harmed them. **The configuration
was left alone.** Changing it would be a coin-flip dressed as a
decision, and the coin has already been flipped twice with opposite
outcomes.

This closes the last operational knob that looked like it might matter.
Every remaining lever — venue (section 14), risk-reward (15), holding
leash (17), stop floor (19), selection filters (22) — has now been
measured and none moves the result. What is missing is upstream of all
of them.

## 23. The structural vise: cheap instruments cannot clear the venue's minimum stop

This is the deepest explanation the project has reached, and it makes
sections 18–19 follow from one fact rather than several.

Capital.com enforces a **minimum stop distance of 1.05 %** of price
(including the 5 % buffer). The live floor of 1 % is not an arbitrary
setting — it is that venue rule. Now put it against the cost table:

| instrument | spread | stop needed for a 10 % cost share | ATR stop the strategies produce (1h) |
|---|---:|---:|---|
| US30 | 0.004 % | 0.04 % | ~0.2–0.5 % |
| US500 | 0.008 % | 0.08 % | ~0.3 % |
| EURUSD | 0.006 % | 0.06 % | ~0.2 % |
| GOLD | 0.006 % | 0.06 % | ~0.4 % |
| DOTUSD | 0.51 % | 5.1 % | ~1.5 % |
| AAVEUSD | 1.00 % | 10 % | ~2 % |
| APTUSD | 5.00 % | 50 % | ~2 % |

**The two constraints select disjoint sets:**

- Instruments cheap enough to trade (indices, FX) produce ATR stops
  *below* the venue minimum. Every signal is either rejected or widened
  to 1.05 %, at which point it is no longer the stop the strategy asked
  for.
- Instruments volatile enough to clear the venue minimum naturally
  (crypto, some commodities) carry spreads 60–600x wider, so the cost
  share is far past any sensible ceiling.

Measured: `donchian_breakout` on six cheap instruments over 30 days
produces **zero live-tradeable signals** at 1h — 93 signals, all below
the floor. At 4h the rejection count drops (2–6 per instrument instead
of 9–25) but the surviving count is still **zero**: even four-hour ATR
stops on indices sit under 1 %.

This explains, from a single mechanism, why:

- frequency is 60x short of the target (section 18) — the venue rule
  removes almost every signal on the only instruments worth trading;
- the traded book lost money (sections 9, 19) — what *did* clear the
  minimum naturally was the expensive half, and 177 such trades account
  for -308.59 USD, 68 % of the total loss;
- no parameter helps (sections 14–17, 22) — RR, leash, filters and
  venue-of-execution all operate inside a set that is nearly empty.

Dropping the floor to 0.2 % in backtest does produce trades on cheap
instruments — 33 of them, 42.4 % win rate, **E[R] +0.041** — but those
are trades whose stop was widened to the venue minimum, so they are not
what the strategy signalled, and t = 0.19 makes the figure
indistinguishable from zero anyway. **The floor was left in place.**

The only structural escape is a venue without a percentage-based
minimum stop, which would allow tight stops on the cheap instruments.
Section 14 shows that even then the hit rate would have to rise, so this
is a necessary condition, not a sufficient one.

## 24. The cost ceiling explains most of the loss — and it was leaking

Splitting every closed trade by whether its cost share cleared the 10 %
ceiling separates the book almost completely:

| | n | win% | E[R] | capital-weighted | t | USD |
|---|---:|---:|---:|---:|---:|---:|
| cost/R <= 10 % (tradeable) | 322 | 39.4 | -0.069 | **-0.052** | -1.25 | -113.58 |
| cost/R > 10 % (too expensive) | 185 | 29.7 | -0.801 | **-0.475** | -3.67 | **-346.70** |

The expensive side loses at t = -3.67 and accounts for **75 % of the
total loss**. The tradeable side sits at -0.052 R with t = -1.25 —
still negative, but no longer distinguishable from zero. The system's
losses are, to first order, a cost problem on instruments it should
never have opened.

The entry-time filter is correctly built: it widens the stop, re-checks,
and skips the trade if the share still exceeds the ceiling. But it reads
a **broker quote**, and a missing quote scored as zero cost — an open
gate. The audited-spread fallback fixed that, yet the damage is visible
in the record: **33 August trades on ATOMUSD, DOTUSD, AAVEUSD and
PALLADIUM, losing 74.55 USD**, the most recent on 2026-08-22.

All four are on the instrument block list, which until now existed only
as a project rule enforced through that one dynamic filter. It is now
also a named set in `autotrade`, checked at both entry boundaries —
`evaluate_pair` and `execute_intent` — so absent data cannot reopen it.
The dynamic filter remains primary; the list is the fail-closed backstop.

Current active list contains none of these eleven instruments, so this
changes nothing today. It closes the path by which they returned.

## 25. Three quarters of the active list never fires

Of the 55 combinations in the active list, **41 (75 %) produced no
signal at all in 30 days**. The dead ones are precisely the cheap
indices and FX pairs on `donchian_breakout` — DE40, EURUSD, GBPUSD,
US100, FR40, J225, AU200, CHFJPY, NZDUSD, COPPER, SILVER, HK50,
NATURALGAS — exactly the set section 23 predicts, because their ATR
stops sit under the venue minimum.

The 14 that do fire cluster on instruments whose volatility clears
1.05 % naturally: BTCUSD (8), OIL_BRENT (7), SILVER 4h (7), EURAUD (6),
US500 on `donchian_trail` (6), AUDUSD (4).

The obvious remedy is a wider ATR multiple, since `stop_atr` is 1.0 for
every strategy and a wider stop costs nothing on an instrument quoting
0.008 %. Swept on seven cheap instruments:

| stop_atr | 1h / 30d trades | E[R] | 4h / 120d trades | E[R] |
|---:|---:|---:|---:|---:|
| 1.0 | 0 | — | 4 | -1.005 |
| 1.5 | 0 | — | — | — |
| 2.0 | 3 | -1.005 | 13 | -0.288 |
| 2.5 | 4 | -0.614 | — | — |
| 3.0 | 6 | +0.284 | 22 | -0.352 |

It does not work. At 1h even a tripled stop yields six trades in thirty
days, because an ATR of 0.3 % still leaves 3x under the 1 % floor. At 4h
the count rises to 22 over 120 days — 0.18 trades/day — but expectancy
stays negative and the **hit rate collapses from 23.1 % to 9.1 %**:
widening the stop moves the RR 1.5 target proportionally further away,
so it is reached far less often. The one positive cell (+0.284 at 1h,
stop_atr 3.0) rests on six trades and is noise.

**`stop_atr` was left at 1.0.** This was the last mechanism that could
have widened the tradeable set from inside the strategy configuration.

The dead combinations are left in place: they consume scan time but no
slots, and removing them would only hide the finding that the venue rule,
not the selection, is what silences them.

## 26. What the current configuration delivers, and why the number flatters

The active list, measured over its own trading history:

| set | n | win% | E[R] | capital-weighted | t | USD |
|---|---:|---:|---:|---:|---:|---:|
| active combinations only | 133 | 42.1 | +0.020 | **+0.126** | 0.23 | +87.23 |
| every combination ever traded | 513 | 36.1 | -0.335 | -0.158 | -3.84 | -462.96 |

The active set looks profitable. **It is not evidence of anything.** That
list was produced by vetoes that retire combinations on realised losses,
so the surviving set is positive by construction — the selection rule and
the measurement read the same data. t = 0.23 says as much on its own.

The unbiased estimate is the forward test, which has produced **one**
closed trade since the cutoff.

Taking the flattered number at face value anyway, as a best case:

| | |
|---|---:|
| closes per day | 1.27 |
| per trade at 3 USD risk | +0.378 USD |
| **best-case daily** | **+0.48 USD** |
| target | +58.49 USD |
| **gap** | **122x** |

So even the in-sample, selection-biased, statistically insignificant
reading of the best configuration this project has produced falls short
of the target by two orders of magnitude. The honest reading is not
positive at all.

### Closing balance

Twenty-six sections of measurement have found: nine accounting and parity
defects (7–13, 15, 17, 18, 20, 21, 24), all of which flattered results in
the same direction; zero statistically significant edges across 154,560
parameter combinations, 18 structural signal rules, and 1,440 Kraken
variants; and one structural constraint (23) that explains the rest —
Capital.com's 1.05 % minimum stop and instrument spreads select disjoint
sets, leaving almost nothing tradeable.

Every operational lever has been tested and none moves the result: venue
(14), risk-reward (15), holding leash (17), stop floor (19), selection
filters (22), ATR multiple (25).

What is missing is predictive value in the signals. No configuration
change can supply it.

## 27. The backtest never modelled the trailing exit — and the strategy is a loser

`donchian_trail` carries a fixed RR of 5.0 as a "far backstop", because
live it exits on a trailing stop. The backtest had **no trailing logic at
all**: it walked bars checking SL and TP only. So every result for this
strategy described something that targets +5R and stops at -1R, which is
not the exit it runs. This is the third live/backtest divergence found
after `--rr` (15) and the holding leash (17), and the largest.

The trail is now modelled in `_simulate_trades`, reading the same
`trail_config_for()` the live parameters come from, including the
never-give-back-below-break-even rule.

Measured with its real parameters (arm at 1.0R, ride 2.0xATR), 4h,
150 days, 14 tradeable instruments:

| activation | ATR multiple | n | E[R] | capital-weighted | best trade |
|---:|---:|---:|---:|---:|---:|
| **1.0 R** | **2.0 (live)** | 62 | **-0.648** | -0.862 | +1.64 |
| 2.0 R | 3.0 | 59 | -0.591 | -0.482 | +4.97 |
| 2.0 R | 4.0 | 57 | -0.453 | -0.266 | +4.97 |
| 2.0 R | 6.0 | 57 | -0.828 | -0.751 | +4.96 |

The live configuration returns **-0.648R at a profit factor of 0.19**,
t ~ -5.7. The live journal agrees in sign: -0.136R capital-weighted over
10 closed trades.

The mechanism is visible in the "best trade" column. At 2xATR the trail
arms at +1R and sits two ATR back, so on a 1xATR stop it closes at
roughly break-even on any ordinary pullback — it **caps winners**, the
one thing a trend follower must not do. The best trade it ever produced
is +1.64R, below what the fixed-target strategies reach (+2.06R).

Widening to 4xATR does what theory says: winners of **+4.97R** appear,
the first genuine right tail in this project. It still loses, because
the hit rate needed at that payoff is 18 % against the 8.8 % achieved:

| winner size | break-even hit rate |
|---:|---:|
| +1.64 R | 39.9 % |
| +4.97 R | 18.0 % |
| +10.00 R | 9.8 % |

**`donchian_trail` is now blocked for entries**, alongside v3. Open
positions keep their exit path, trail included — the block applies at
`evaluate_pair` and `execute_intent` only. Two positions (US500, EURUSD)
were open at the time and will close on their own logic.

This is a real reduction in expected loss, not an edge. It removes a
strategy that measurably loses; it does not make the remainder win.

## 28. RETRACTION: sections 18, 23 and 25 rest on a fault I introduced

Section 18 changed the backtest to apply the stop floor **before**
expanding to the venue minimum, on the reasoning that the live loop
"drops a signal outright and only widens afterwards". That reading of
the live code was wrong, and two independent checks contradict it.

**The live order is the reverse.** `evaluate_pair` is called with
`apply_venue_min=True`; expansion happens at line 382 and the floor is
applied at line 433. Since the venue minimum (1.05 %) exceeds the floor
(1 %), a signal that has been expanded always clears it. The floor
therefore almost never fires live — and the journal proves it: **zero
entries** carrying "stop distance below" have ever been written.

**A second fault compounded it.** `_venue_min_distance` returns 0 when
the instrument is absent from `data/capital_min_distances.json`, which
covers only 14 instruments — **US500, US30, GOLD and DE40 are not among
them**. Zero meant "no expansion", so exactly the instruments discussed
in sections 18 and 23 kept their narrow ATR stop, failed the floor, and
vanished. `pair_selector` already defaults to 1.05 % in this case; the
backtest now does the same.

Re-measured with the correct order and the default in place —
donchian_breakout, seven cheap instruments, 1h, 30 days:

| | trades | E[R] |
|---|---:|---:|
| with my fault | **0** | — |
| corrected | **40** | -0.028 |

**What this retracts:**

- Section 18's headline ("93 % of signals never open live") is wrong.
  They do open, at the expanded stop.
- Section 23's claim of "zero live-tradeable signals" on cheap
  instruments is wrong, and with it the vise as stated. Expansion is
  the mechanism that resolves it: a cheap instrument expanded to 1.05 %
  still carries a cost share under 2 %.
- Section 25's `stop_atr` sweep was run through the faulty path and its
  trade counts are meaningless.

The live record contradicted me the whole time and I did not check it:
**125 closed trades in 30 days, 4.17 per day across 21 instruments**,
against the 1.69 I projected from the broken backtest.

### The target gap, on measured frequency

| | |
|---|---:|
| measured closes per day | 4.17 |
| risk per trade | 3.00 USD |
| **required expectancy** | **+4.68 R** |
| best systems achieve | +0.10 to +0.30 R |

| at E[R] | USD/day | short by |
|---:|---:|---:|
| +0.05 | 0.63 | 94x |
| +0.10 | 1.25 | 47x |
| +0.20 | 2.50 | 23x |
| +0.30 | 3.75 | 16x |

At a realistic +0.20 R the target needs **97 closes per day** against
the 4.17 achieved. The conclusion of section 26 is unchanged and the
arithmetic is now sound: the gap is one to two orders of magnitude,
and it is a frequency-times-capital problem, not one the stop floor
explains.

## 29. The 15m timeframe raises frequency but trades noise

With section 28 establishing frequency as the binding constraint and the
cheap instruments confirmed tradeable, the shortest untested lever is
resolution. 15m carries four times the bars of 1h.

Eighteen cheap instruments, 10 days (the broker's ~1000-bar limit caps
the window at 15m):

| resolution | trades | win% | PF | E[R] | capital-weighted |
|---|---:|---:|---:|---:|---:|
| 15m | 74 | 1.4 | 1.66 | +0.048 | +0.157 |
| 1h | 17 | 5.9 | 0.69 | -0.112 | -0.154 |

Frequency roughly doubles per unit time and the sign flips positive. It
does not survive inspection.

**The per-instrument extremes give it away.** Best and worst trades are
+0.57/+0.02 on US30, +0.13/-0.15 on DE40, +0.07/-0.18 on NZDUSD — a
fraction of a stop in either direction. Only GOLD reaches a target
(+1.49). The overall hit rate is **1.4 %**.

These are not trades in any strategic sense. The stop is expanded to
1.05 % and the target to 1.575 %, while the holding leash is 24 bars —
**six hours** at 15m. An index rarely travels a full percent in six
hours, so almost every position exits on timeout, somewhere near entry.
The positive expectancy is the mean of that drift, not of the strategy.

Statistically it is nothing: Sharpe 0.157 per trade over 74 trades gives
**t = 1.35** (p ~ 0.088), against 2.0 for a single test and 2.4 for the
three resolutions tried. Eleven of eighteen instruments are positive,
five negative, three produce nothing.

And taken at face value it would still not matter: 7.4 trades/day at
+0.048 R and 3 USD risk is **1.07 USD/day**, short of the target by a
factor of 55.

**Resolution was left at 1h.** Trading a shorter timeframe against an
expanded stop converts a breakout strategy into a six-hour random
position, which is a way to accumulate cost, not edge.

## 30. The signals do not beat random entries

Section 28 establishes that on cheap instruments the stop is always
expanded to the venue minimum. Stop and target therefore come from broker
mechanics, not from the strategy — which leaves the signal responsible
for exactly two things: **when** to enter and in **which direction**.

That is directly testable. Same bars, same simulator, same expansion,
same costs, same holding leash, same number of entries — only the choice
of bar and direction differs. Random entries were drawn uniformly over
the usable bar range with a coin-flip direction, 20 draws per instrument
to average out luck, across 14 cheap instruments, 1h, 30 days.

| | n | E[R] | t |
|---|---:|---:|---:|
| strategy signals | 75 | **-0.0199** | -0.26 |
| random entries | 1,378 | **+0.0006** | 0.04 |

Difference, signal minus random: **-0.0206 R** (SE 0.0779, t = -0.26).

**The signals do not beat random entry. They are marginally worse.**

Two things follow.

First, random trading on these instruments is break-even after costs
(+0.0006 R). That confirms section 24 from the other direction: where
the cost share is small, execution is not what loses the money.

Second, the missing edge is in the signals themselves, not in sizing,
exits, filters, venue or timing. Every mechanism this project spent
twenty-nine sections measuring sits downstream of a directional call that
carries no information.

**On the test's power:** with SE 0.0779 it resolves differences from
about **0.156 R** upward. The target requires +4.68 R per trade
(section 28), thirty times that threshold. An edge large enough to
matter here would be unmissable. What was measured is -0.0206 R.

This is the cleanest statement of the project's central finding, and it
is the one that should have been made first — before the parameter
sweeps, the venue comparison, and the exit modelling. Those were all
searches for a better way to act on a signal that says nothing.

### 30b. The same holds for every active strategy

Section 30 tested `donchian_breakout`. Repeating it for the whole active
set, identical method:

| strategy | n | signal E[R] | random E[R] | difference | t |
|---|---:|---:|---:|---:|---:|
| donchian_breakout | 75 | -0.0199 | +0.0006 | -0.0206 | -0.26 |
| turtle_breakout | 54 | +0.0562 | -0.0250 | **+0.0812** | +0.84 |
| momentum | 7 | -0.2778 | -0.0476 | -0.2302 | -1.12 |
| keltner_breakout | 69 | -0.0448 | -0.0336 | -0.0112 | -0.15 |

**Not one beats random entry.** Every |t| is below 1.2, against 2.0 for a
single test and roughly 2.5 once the four comparisons are corrected for.
Three of the four are worse than a coin flip.

`turtle_breakout` is the only one above random (+0.0812 R) and it is the
one worth naming explicitly as *not* a finding: t = 0.84 over 54 trades,
which is exactly the kind of number this project has repeatedly chased
and repeatedly had to retract. `momentum` produced 7 signals in 30 days
across 14 instruments, too few to say anything at all.

This closes the question the project set out to answer. The strategies
select entries that are statistically indistinguishable from picking a
bar at random and flipping a coin — and on the cheap instruments, that
coin flip is break-even after costs. There is no edge to size up, filter,
re-time, re-venue or re-exit.

## 31. Cross-sectional relative strength: tested, not supported, underpowered

The preregistration excluded time-of-day rules for having too many
arbitrary boundaries, and its cross-asset work tested **lead-lag** ("does
A predict B"). Cross-sectional relative strength — rank the universe,
buy the strongest, sell the weakest — is a structurally different
anomaly and was never tried.

Specified before running, single specification, no sweep:

- universe: the 14 cheap instruments
- ranking: trailing 252-hour return (conventional intermediate horizon)
- rebalance: every 120 hours; long top 3, short bottom 3
- execution: unchanged — venue-minimum stop, RR 1.5, 24-bar leash, real
  spreads
- decision rule: accept only at t > 2.0 against the section 30 random
  baseline

Result: **n = 15, E[R] +0.0609, difference against random +0.0603 R at
t = +0.30. Not accepted.**

**The honest caveat is the sample, not the sign.** Fifteen trades cannot
distinguish a real effect from nothing, and this is a limit of the data
rather than of the design. Capital.com serves roughly 1,000 hourly bars:

| window | lookback | rebalance | rebalance points | signals |
|---:|---:|---:|---:|---:|
| 40 days | 252 h | 120 h | 5 | 30 |
| 40 days | 252 h | 24 h | 28 | 168 |
| 40 days | 120 h | 24 h | 33 | 198 |

A 252-hour lookback consumes a quarter of the available history before
the first ranking exists. Shortening the lookback or rebalancing daily
would raise the count, but choosing those numbers *after* seeing this
result is exactly the search that sections 15, 19 and 22 had to retract.

So this is recorded as **untested in any conclusive sense**, not as
refuted. Testing it properly needs a longer history than this venue
exposes — the same constraint that limits every other analysis here.
It does not change the standing of section 30: whatever ranking might
add, the entry signals currently in use carry no directional information.

## 32. History paging was broken — and the year-long benchmark confirms section 30

Section 31 recorded the ~1000-bar ceiling as a limit of the venue. It was
a defect in our own adapter.

`fetch_history` paginates by advancing `from` while leaving `to` pinned
at the requested end. Capital.com counts the from/to span in **calendar
time**, not in bars returned, and refuses a request whose span exceeds
what `max` bars nominally cover — with a bare HTTP 400. So any range
wider than about forty days returned nothing at all, and every backtest
in this project silently ran on ~660 bars.

The window now moves as a whole, at 90 % of the nominal span (a request
covering exactly 1000 x 1h = 41.7 days is refused; 40 days succeeds and
yields ~665 bars, because markets are shut for part of it).

| requested | before | after |
|---|---:|---:|
| 90 days | HTTP 400 | 1,521 bars |
| 180 days | HTTP 400 | 3,036 bars |
| 365 days | HTTP 400 | **6,140 bars** |

**Nine times the data.** Section 30's benchmark was rerun on a full year,
16 instruments:

| strategy | signal n | difference vs random | t | same test at 30 days |
|---|---:|---:|---:|---:|
| donchian_breakout | 1,131 | -0.0220 | -0.83 | -0.0206 |
| turtle_breakout | 924 | **-0.0441** | -1.44 | **+0.0812** |
| keltner_breakout | 1,023 | -0.0100 | -0.34 | -0.0112 |

All three sit below random entry and none is significant. Section 30's
conclusion holds on roughly a thousand trades per strategy instead of
seventy-five.

**`turtle_breakout` is the lesson.** At 30 days it measured +0.0812 R
above random, the single most encouraging number this project produced.
On a full year it is **-0.0441 R** — the sign reversed. It was noise, as
section 30b already flagged it might be, and the small sample is exactly
why. Every positive figure in this document that rests on tens of trades
deserves the same suspicion.

The benchmark now resolves differences from about **0.06 R** upward,
against 0.156 R before. The target needs +4.68 R per trade — seventy-eight
times that threshold.

## 33. Cross-sectional relative strength: now properly powered, and negative

Section 31 left this open rather than refuted, because 15 trades could
not distinguish anything and the history was thought to be capped. The
paging fix (32) removed that cap, so the **same preregistered
specification** was rerun — 252h lookback, 120h rebalance, long top 3 /
short bottom 3, unchanged execution — on a full year.

| basis | instruments | n | difference vs random | t |
|---|---:|---:|---:|---:|
| section 31 | 14 (6 usable) | 15 | +0.0603 | +0.30 |
| year, partial fetch | 6 | 235 | +0.0406 | +0.91 |
| **year, full universe** | **14** | **215** | **+0.0191** | **+0.36** |

**Not accepted.** The cross-sectional portfolio itself returns
**+0.0002 R** — zero to four decimal places — against -0.0189 R for the
matched random control.

The middle row is worth keeping visible. With only six instruments
reaching the simulator, "top 3 versus bottom 3" is merely the upper half
against the lower half, and it measured +0.0406 R at t = 0.91. Restoring
the full fourteen — a genuine ranking — cuts it to +0.0191 R at t = 0.36.
The apparent effect shrank as the basis improved, exactly as
`turtle_breakout` did in section 32 when its sample grew.

Those six instruments were missing for a mundane reason: fetching a year
for fourteen instruments issues around 126 chunked requests, and several
died on transient transport errors. The fetch now retries and caches, so
this is measurement plumbing rather than a finding — but a partial fetch
that silently proceeds with whatever arrived is how the 6-instrument
number got produced in the first place.

This closes the last open signal class. Time-series breakouts (30, 32),
mean reversion (retired by veto), lead-lag (preregistration), and now
cross-sectional ranking have all been measured against a random control
on a year of data. None beats it.

## 34. The default backtest window was a workaround for the paging bug

`_DEFAULT_DAYS = 30` was never a considered choice: the adapter failed
with HTTP 400 on any range wider than ~40 days (32), so 30 was simply
what worked. It is now **180**, giving ~3,000 hourly bars per instrument
against ~660. Not 365, to keep the nightly run's request count sane.

`_fetch` now retries transient transport failures. A year for fourteen
instruments issues ~126 chunked requests and several reliably die; the
caller previously proceeded with whichever instruments survived, which
is precisely how section 33's cross-sectional test silently became a
6-instrument test and reported a different number.

Measured on the longer window, eight tradeable instruments, 1h:

| strategy | n | win% | PF | E[R] |
|---|---:|---:|---:|---:|
| donchian_breakout | 373 | 29.5 | 1.04 | **+0.018** |
| turtle_breakout | 286 | 28.7 | 1.01 | **+0.003** |

Both are zero to within noise (t ~ 0.29 for the first). Consistent with
the random benchmark: profit factors of 1.04 and 1.01 are what a coin
flip produces when costs are small.

The same strategy across this project's measurements:

| window | instruments | n | E[R] |
|---|---:|---:|---:|
| 30 days | 6 | 17 | -0.112 |
| 30 days | 7 | 40 | -0.028 |
| **180 days** | **8** | **373** | **+0.018** |

**The spread between these readings exceeds any of them.** That is the
methodological finding of this whole exercise: on samples of tens of
trades, the measurement noise dominates the quantity being measured, and
every conclusion drawn from such a sample — including several of mine
that had to be retracted — is a coin flip dressed as evidence.

On the best sample available: 2.07 trades/day at +0.018 R and 3 USD risk
is **0.11 USD/day**, short of the 50 EUR target by a factor of **523**.

## 35. The selector finally has real samples — and still nothing significant

With the 180-day window the persisted results were regenerated. Sample
sizes are no longer decorative:

| strategy | n | win% | PF | E[R] |
|---|---:|---:|---:|---:|
| donchian_breakout | 1,054 | 17.9 | 0.96 | -0.013 |
| turtle_breakout | 827 | 17.3 | 0.95 | -0.017 |
| momentum | 134 | 23.1 | 1.03 | +0.015 |

All three are zero within noise across the full instrument list. The
earlier +0.018 for donchian on eight cheap instruments versus -0.013
here is the cost effect of section 24 reappearing: the wider list
includes instruments where the cost share bites.

Ranking now produces a real table. It also demonstrates the trap in one
line: **top of the list is `momentum/ETHUSD`, n = 11, E[R] +0.702** —
while momentum overall sits at +0.015 across 134 trades. That single
cell is the luckiest of ~50 combinations, nothing more.

Turning on the stability gate (min-pf 1.0, min-er 0.0, min-stability
0.66) leaves four:

| combination | n | PF | E[R] | t |
|---|---:|---:|---:|---:|
| turtle_breakout / GOLD | 30 | 1.89 | +0.332 | ~1.44 |
| donchian_breakout / GOLD | 39 | 1.49 | +0.210 | ~1.0 |
| donchian_breakout / OIL_BRENT | 46 | 1.31 | +0.154 | ~0.8 |
| donchian_breakout / DE40 | 37 | 1.37 | +0.141 | ~0.7 |

GOLD surviving under two independent strategies is the most interesting
pattern here, and it still is not evidence: GOLD quotes a 0.006 % spread,
so its trades sit closest to the zero that section 30 shows random entry
produces on cheap instruments. Instrument economics, not signal quality.

The best candidate reaches **t ~ 1.44**, selected from roughly fifty
combinations, where Bonferroni would demand t > 3. **The active list was
not changed.** Section 34 is exactly about why: a 30-trade cell moving
0.3 R is what noise looks like at this sample size, and this project has
already retracted several such findings.

What did improve is the basis for future decisions. `min_trades = 10`
now selects on genuine samples rather than on tens of trades, and the
stability gate has enough history to mean something. Neither creates an
edge; both make the absence of one harder to mistake for a finding.

## 36. The one candidate above threshold — and why it changes nothing

Section 35 flagged GOLD surviving under two strategies and dismissed it
as instrument economics without testing it. Tested directly against a
random control on GOLD alone, 365 days:

| strategy | signal n | signal E[R] | random E[R] | difference | t |
|---|---:|---:|---:|---:|---:|
| **turtle_breakout** | 68 | +0.266 | -0.032 | **+0.298** | **+2.16** |
| donchian_breakout | 80 | +0.135 | -0.005 | +0.141 | +1.09 |

**t = 2.16 is the first value this project has produced above the
single-test threshold.** It deserves a careful reading, not a
celebration.

Split in half by time, each against its own random control:

| half | n | difference | t |
|---|---:|---:|---:|
| first | 36 | +0.199 | +1.11 |
| second | 32 | +0.287 | +1.39 |

Both halves are positive and of similar size, so it is not one regime
carrying the whole. But neither half is significant on its own, and
three problems remain:

1. **It was selected post hoc.** GOLD was tested *because* it stood out
   in the ranking of ~50 combinations. Corrected for that search,
   t = 2.16 falls well short of the ~3 required.
2. **Live disagrees.** `turtle_breakout/GOLD` sits on the retirement
   veto: 13 closed trades at **-0.129 R**. That sample is far too small
   to refute the backtest, but it is not support either.
3. **The size is irrelevant to the goal.** 68 trades in 365 days is
   0.19 per day. At +0.266 R and 3 USD risk that is **0.15 USD/day** —
   the target is 393 times larger. Reaching 50 EUR/day from this
   combination alone would need **1,180 USD of risk per trade** on a
   559 EUR account.

**The veto stays in place and the active list is unchanged.** Point 3 is
why this is not a close call: even granting the finding entirely, at
face value, with no correction for selection, it moves the daily result
from roughly zero to roughly zero. It is recorded as a preregistered
forward-test candidate, nothing more.

## 37. What reaching the target would actually require

The daily result is a product of three terms:

    daily = trades_per_day x E[R] x risk_per_trade

Two are measured and near zero; the third is bounded by the account and
by the project's own risk rule. Required risk per trade for 58.49 USD/day
(50 EUR), on a 654 USD account:

| E[R] | 2 trades/day | 5 | 10 | 20 | 50 |
|---:|---:|---:|---:|---:|---:|
| +0.05 | 585 | 234 | 117 | 58 | 23 |
| +0.10 | 292 | 117 | 58 | 29 | 12 |
| +0.20 | 146 | 58 | 29 | 15 | 6 |
| +0.30 | 97 | 39 | 19 | 10 | 4 |
| +0.50 | 58 | 23 | 12 | 6 | 2 |

Measured: **4.17 trades/day, E[R] between 0.00 and +0.02, 3 USD risk** —
which is 0.46 % of the account, deliberately conservative and fixed by
the project's 3 USD / 250 USD notional rule.

Even suspending that rule and risking 2 % of the account (13 USD) at the
current frequency:

| E[R] | USD/day | short by |
|---:|---:|---:|
| +0.10 | 5.42 | 11x |
| +0.20 | 10.84 | 5x |
| +0.30 | 16.26 | 4x |

At a genuine +0.20 R — better than the measured value by an amount no
test in this project could establish — the target still needs an account
of about **3,500 USD**, five times the current one.

So the target requires all three simultaneously:

1. **an edge of roughly +0.20 R**, where thirty-six sections of
   measurement against random controls find approximately zero;
2. **about five times the capital**, which only the account holder can
   provide;
3. **a relaxation of the 3 USD risk rule**, which is a standing project
   constraint and not mine to change.

None of the three is a code problem, and the first is the one that
matters: with E[R] at zero, the other two multiply zero.

### Current state

Forward test since the cutoff: 30 trades, 3 closed, **-2.53 USD**.
Seven positions open. The bot is running on the Capital demo account
with the corrected risk controls, the cost blocklist, `donchian_trail`
and `donchian_breakout_v3` blocked for entries, and backtests now
measuring 180 days instead of 30.

## 38. Reconciliation is clean; the equity basis was not

A full broker-versus-journal reconciliation, never run end-to-end in this
session:

| | |
|---|---|
| broker open positions | 7 — UK100, US500, GBPCAD, NZDUSD, US30, SILVER, EURUSD |
| journal open positions | 7 — identical set |
| only at broker / only in journal | none |
| count mismatches | none |

The accounting corrections of sections 7–13 and 20–21 hold up: the two
sides agree exactly.

The balance read did surface one defect. `account_balance()` returned
**485.96 EUR** where the account's actual balance is **559.77 EUR** —
it prefers `available`, which is what remains after margin on open
positions:

| field | value |
|---|---:|
| balance | 559.77 |
| deposit | 558.81 |
| profitLoss (open) | +0.96 |
| available | 485.96 |

Its only live consumer is the risk-scaling equity ceiling, which caps
risk at a fraction of account equity. Reading `available` tied that
ceiling to book utilisation: seven open positions shrank the perceived
account by 13 %, and an empty book would have inflated it again. Nothing
in the design intends that. It now reads `balance`, falling back to
`available` only when the broker omits it.

Practically this changes nothing today — risk sits at base because only
3 of the 40 required out-of-sample trades exist — but it is the kind of
coupling that misbehaves precisely when the book is fullest.

Worth recording separately: the account balance is **559.77 EUR against
558.81 EUR deposited**, while the journal records -450.84 USD of
realised losses across 495 trades. The demo account has evidently been
topped up during its history, so its balance is not a running P&L and
must not be read as one.

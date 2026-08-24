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

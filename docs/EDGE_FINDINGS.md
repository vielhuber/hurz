# Measured findings, August 2026

Written after a full audit of the live journal (495 closed Capital.com
demo trades, 11 May – 24 Aug 2026) and an exhaustive walk-forward
search. It records what was *measured*, so later work starts from the
evidence instead of re-deriving it — several of the findings below
contradict what the code previously assumed.

## Read this first

Sections are appended chronologically, so **later sections correct
earlier ones**. Three are retracted outright — 18, 23 and 25, by section
28 — and several figures were revised once samples grew. Start here.

### The finding

The entry signals carry no directional information. Measured against
random entries through the identical simulator — same bars, same stop
expansion, same costs, same holding leash, same number of entries, only
the choice of bar and direction differing (30, 32, 45):

| strategy | signal n | difference vs random | t |
|---|---:|---:|---:|
| donchian_breakout, 1 year | 1,131 | -0.0220 | -0.83 |
| donchian_breakout, 3 years | 810 | **+0.0302** | +1.12 |
| turtle_breakout | 924 | -0.0441 | -1.44 |
| keltner_breakout | 1,023 | -0.0100 | -0.34 |

**The sign reverses between the two largest samples**, both far from
significance. That is stronger than an absence of significance: the
quantity will not hold a direction across independent samples of ~1,000
trades. Mean reversion, lead-lag, cross-sectional ranking (33) and both
uses of volume (41, 41b) were tested the same way, with the same result.
On cheap instruments random entry is break-even after costs, so the
shortfall is in the signals, not in execution.

### The target

`daily = trades_per_day x E[R] x risk_per_trade`. Measured: ~4 trades per
day, E[R] indistinguishable from zero, 3 USD risk — about **0.11 USD per
day** against the 58.49 USD (50 EUR) required (34, 37).

Reaching it needs three things at once: an edge near +0.20 R where
measurement finds zero, roughly five times the capital, and a relaxation
of the 3 USD risk rule. With E[R] at zero the other two multiply zero.
Worse, section 40 shows a candidate of the size ever measured here could
not be *validated* inside a decade at this trade frequency.

### The methodological finding

Four times a near-threshold value dissolved under a larger sample: the
cost-threshold band (19), `turtle_breakout` (32), the cross-sectional
effect (33), and the regime router (46, 46b — from t = -1.98 to t =
-0.34). None was acted on. Section 34 shows the same strategy measuring
-0.112, -0.028 and +0.018 R depending on sample: **the spread between
readings exceeded any of them.**

### What was wrong with the measurements

Sixteen defects, all flattering results in the same direction — PnL
booked against signal price (9), fee tables understating crypto spreads
tenfold (9), the cost filter opening on a missing quote (24), `--rr` and
the holding leash diverging between live and backtest (15, 17), the
trailing exit never modelled (27), R-multiples distorted by near-zero
risk denominators (20), both risk controls reading the flattering column
(21), history paging capping every backtest at ~660 bars of an available
18,000 (32, 45), account equity read as free margin (38), volume dropped
from the strategy frame despite the documented contract (41), and pinned
combinations bypassing both static blocks (44, 44b).

### Current state

Broker and journal reconcile exactly. `donchian_trail` (27) and
`donchian_breakout_v3` are blocked for entries; thirteen high-cost
instruments are blocked by name (24, 42); backtests measure 365 days of
the three years available. Running cost is **0.24 USD/day** (43) — the
system no longer bleeds, it simply does not earn. Forward test since the
2026-08-24 cutoff: 5 positions opened, 3 closed, -2.53 USD.

---

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

## 39. Operational hardening for the longer backtest window

Raising the backtest window to 180 days (34) made the nightly refresh
about six times slower. Measured: **305 seconds for one strategy over the
full instrument list**, so roughly fifteen minutes for the three the
scheduler runs. That is acceptable — the subprocess is awaited
asynchronously, so trading continues throughout.

The risk it introduced is the unbounded wait. `_run_one_backtest` awaited
`communicate()` with no timeout, so a hung fetch would stall the nightly
refresh until the process was restarted, leaving the active list frozen
with no visible symptom. Longer runs make that more likely, not less.

A 1800-second timeout now bounds it: the subprocess is killed and the
failure reported through the existing diagnostic path, so the run fails
loudly and the next night retries.

This changes nothing about profitability. It is here because the window
change was mine, and an operational regression introduced while chasing
a measurement improvement is still a regression.

## 40. The system cannot validate its own candidates

Section 36 recorded `turtle_breakout/GOLD` (t = 2.16) as a preregistered
forward-test candidate without stating what would settle it. Working that
out turns the candidate into a finding about the whole approach.

The measured effect is +0.298 R against random, with an outcome spread of
about 1.3 R. Sample size needed to confirm it forward:

| criterion | power | trades needed | at 68 trades/year |
|---|---:|---:|---:|
| t > 1.96, uncorrected | 80 % | 247 | **3.6 years** |
| t > 3.0, corrected for the ~50-combination search | 80 % | 411 | **6.0 years** |
| t > 3.0, corrected | 95 % | 573 | **8.4 years** |

**The single most promising candidate this project produced cannot be
validated inside a decade.** That is not a statement about GOLD; it
follows from the frequency. A combination firing 0.19 times a day cannot
accumulate evidence faster than the market changes underneath it.

The same arithmetic governs everything here. The whole system trades
4.17 times a day; resolving an effect of +0.05 R — a realistic edge
rather than an implausible one — would need thousands of trades, which is
years. By then the instrument mix, the spread structure and the venue
rules will all have moved.

Two consequences follow, and they are more useful than another parameter
sweep:

1. **A backtest result on this venue can never be confirmed forward
   within a useful horizon.** Deployment decisions here are therefore
   always bets on in-sample evidence, which sections 2 and 22 already
   show is not predictive. That is the real reason the data-generation
   mode has run for months without converging.
2. **Any viable approach must trade far more often** — not to earn more
   per day, but so that evidence accumulates fast enough to act on.
   Frequency is a prerequisite for *knowing*, before it is a lever for
   earning.

This closes the investigation honestly. The signals carry no directional
information (30, 32); the target needs an edge, five times the capital
and a relaxed risk rule simultaneously (37); and even a genuine edge of
the size measured could not be established here before it decayed.

## 41. Volume was never available to any strategy

`app/strategies/base.py` documents the frame handed to a strategy as
carrying *open, high, low, close, volume*. Both converters —
`spot_backtest._bars_to_df` and `autotrade._bars_to_df` — built the frame
without the volume column. Any strategy relying on the documented
contract would have raised `KeyError`, so none was ever written, and the
one price-independent data source Capital.com provides went unused for
the project's lifetime.

Capital.com populates it on every bar: 100 % non-null across US500, GOLD,
EURUSD and BTCUSD, with plausible distributions (US500 median 2,209,
GOLD median 16,933). Both converters now keep it, and a test asserts they
agree on columns.

With that fixed, the standard hypothesis was preregistered and tested:
**breakouts on above-median volume hold; those on below-median volume are
false breaks.** Filter is the signal bar's volume against the median of
the preceding 100 bars. One specification, acceptance at t > 2.0 for the
filtered set over the unfiltered one.

| group | n | E[R] |
|---|---:|---:|
| high volume | 717 | **-0.0609** |
| low volume | 382 | -0.0386 |
| all signals | 870 | -0.0483 |
| random control | 4,384 | -0.0250 |

**Not accepted.** High-volume breakouts perform *worse* than low-volume
ones (-0.0224 R, t = -0.51), the opposite of the hypothesis and not
significant in either direction. Every signal group sits below the random
control.

The fix outlasts the hypothesis: volume is now available to any future
strategy, and the contract in `base.py` is no longer a false promise. The
particular rule tested is dead, which is what a preregistered test is
for.

### 41b. Volume as a signal, not as a filter

Section 41 tested volume as a *filter* on price breakouts. That test was
weaker than it looked: sections 30 and 32 show the underlying signal
carries no information, and no subset of a zero-information signal can
carry any either. A filter can only concentrate an edge that exists.

Volume as the **primary trigger** is a different hypothesis, and it only
became testable once the frame carried the column. Preregistered, single
specification: a bar whose volume exceeds **3x** the median of the
preceding 100 bars, direction taken from that bar's own close-minus-open,
regime filter off. No variants — 3x fails means the hypothesis fails,
not that 2x gets tried next.

| | n | E[R] |
|---|---:|---:|
| volume climax | 1,058 | +0.0011 |
| random control | 14,038 | -0.0058 |
| difference | | +0.0069 R, t = +0.32 |

**Not accepted**, on a sample large enough to matter: with SE 0.0216 the
test resolves differences from about 0.043 R, and the climax entry
returns **+0.0011 R** — zero to three decimal places.

Volume is now available for future work and two of its standard uses are
recorded as measured and dead. That is the useful state: the column
exists, the obvious hypotheses are closed, and nobody has to re-derive
them.

## 42. Two more instruments fail the cost audit

With expectancy at zero, cost is the only systematic drag left, so it is
the only lever that still moves the result — from slightly negative
toward zero. Auditing the 55 active combinations by cost share against
their measured live stops:

| cost share of risk | combinations |
|---|---:|
| under 2 % | 35 |
| 2–5 % | 10 |
| 5–10 % | 7 |
| **over 10 %** | **3** |

The three over the ceiling, audited per instrument as the block-list rule
requires:

| instrument | spread | mean live stop | cost/R mean | worst | verdict |
|---|---:|---:|---:|---:|---|
| CORN | 0.135 % | 1.14 % | **11.8 %** | 12.9 % | blocked |
| NATURALGAS | 0.174 % | — | **16.6 %** | — | blocked |
| WHEAT | 0.113 % | 1.32 % | 8.5 % | 10.0 % | kept |

CORN never cleared the 10 % ceiling in any of its four fills, and it was
still trading on 2026-08-24. NATURALGAS has produced no fills, so its
figure is structural — 0.174 % against the 1.05 % venue minimum — but
both terms are measured rather than assumed. WHEAT was examined at the
same time and kept.

The reasoning is structural, not performance-based: spread and venue
minimum are both measured quantities, and their ratio breaches the
ceiling regardless of how the trades happened to turn out. CORN's E[R] of
-0.104 across four trades is far too small a sample to justify anything
on its own.

The dynamic filter should have caught CORN at order time. It did not,
because widening the stop pulls the share back under the ceiling — the
widened stop is then no longer the one the strategy asked for. The named
list does not have that loophole.

## 43. What the forward test costs to run

With expectancy at zero, the running cost *is* the expected loss. After
the block list removed the expensive instruments (24, 42), the current
configuration:

| | |
|---|---:|
| tradeable combinations | 52 of 55 |
| cost share per trade | 1.32 % median, 1.94 % mean of risk |
| in dollars | 0.058 of the 3.00 USD risked |
| **at 4.17 trades/day** | **0.24 USD/day** |
| per month | 7.27 USD |

Against a 559 EUR account that is about **1.2 % per year** — a tolerable
price for keeping a forward test alive, and a different regime from the
history: the expensive half of the book ran at roughly 0.475 R per trade,
1.42 USD on the same 3 USD risk, twenty-four times the current figure.

That reframes the state of the system honestly. It does not earn, and no
measurement in this document suggests it will. But it no longer bleeds
either: the -0.158 R capital-weighted expectancy of the historical
record (20) was dominated by instruments that are now blocked by name,
and what remains carries a drag of under 2 % of risk per trade.

So the operating question is no longer "how much is this losing" but
"is 0.24 USD/day a fair price for the data". At the current rate the
forward test reaches a verdict-grade 40 closes in roughly ten days of
trading, for about 2.40 USD. That is the only remaining open item in
this investigation, and it costs almost nothing to let it finish.

## 44. The nightly pipeline verified, and a pin loophole closed

The nightly refresh had never run with the 180-day window or the timeout,
so it was exercised directly rather than left to fire unattended at
05:30 UTC:

| step | result |
|---|---|
| one strategy backtest | ok, **321 s** |
| `_refresh_pairs` | ok, 4 s |

Three strategies is therefore about sixteen minutes, comfortably inside
the 1800 s bound.

The run surfaced a loophole. **CORN reappeared in the refreshed active
list under two strategies**, one day after being blocked, because a pin
bypasses the ranking's cost filter. The entry guard still refused it, so
no trade could occur — but the file no longer described what was
tradeable, and every cycle re-checked a name that could never open.

The block list now lives in `app/spot_trading/instrument_blocks.py`,
which both the selector and the trader read. Putting it there rather than
importing the trader from the selector matters: the first attempt did
exactly that and perturbed unrelated tests, which is the dependency
direction telling the truth about itself.

Two tests were fragile in the same area and are now deterministic. One
left `strategy_expectancy_veto` unstubbed, so it reached for a real
database and passed or failed depending on whether an earlier test had
opened a connection. The other used PALLADIUM — now cost-blocked — as an
arbitrary instrument while testing something else entirely.

After the fix the refreshed list holds 60 combinations and **no blocked
instrument**. `turtle_breakout/GOLD` returned to it legitimately: the
capital-weighted correction (20) moved its live figure from -0.271 R to
-0.129 R, above the -0.15 veto threshold. That is the selection working
as designed on corrected data, not a decision of mine — and section 36
still applies to what it is worth.

### 44b. The same loophole applied to blocked strategies

`donchian_trail` was retired for entries in section 27, yet two pinned
combos — US500 and EURUSD — remained in the active list. Identical cause
to CORN: a pin bypasses the selector's filters, and the strategy block
lived in the trader where the selector could not consult it.

Both blocks now sit in `app/spot_trading/trading_blocks.py`, which the
selector and the trader read independently. Regenerating the list drops
it from 60 combinations to **56, with nothing blocked remaining**.

Removing them from the list does not strand the two open positions.
Positions to manage come from `platform.list_positions()` — the broker —
not from the active list, and the strategy guards sit only in
`evaluate_pair` and `execute_intent`. Both keep their exit path, trailing
stop included. A test asserts that sourcing, so a future refactor cannot
quietly couple exits to the active list.

Neither the instrument nor the strategy loophole ever permitted a trade;
both entry guards held throughout. What they corrupted was the file that
describes what the system trades — which is the artefact every later
analysis reads.

### 44c. The loophole class is closed

Both leaks had one shape: a guard living in the trader that the selector
could not consult, so a pin walked past it. Enumerating every early
return in the two entry paths settles whether more exist.

`evaluate_pair`:

| guard | kind | in the selector? |
|---|---|---|
| `DISABLED_LIVE_STRATEGIES` | static | yes, now |
| `COST_BLOCKED_PAIRS` | static | yes, now |
| fewer than 50 bars | data-dependent | not applicable |
| no signals from the strategy | data-dependent | not applicable |
| ATR missing or non-finite | data-dependent | not applicable |
| regime router (ADX) | runtime | not applicable |
| stop below the floor | runtime | not applicable |

`execute_intent` carries the same two static guards plus platform error
handling.

**There are exactly two static blocks, and the selector reads both.**
Everything else depends on the bar being evaluated and cannot be
precomputed into a list — a combination blocked by ADX this hour is
tradeable the next, which is the router working, not a leak.

So the class is closed rather than merely two instances of it patched.
Any future static block belongs in `trading_blocks.py`, where both sides
see it by construction.

## 45. Three years are available, and the core finding survives the wider test

The paging fix (32) understated what the venue serves. Probing further:

| requested | bars on US500 | earliest |
|---|---:|---|
| 365 days | 6,140 | 2025-08-25 |
| 730 days | 12,100 | 2024-08-25 |
| 1,095 days | **18,007** | 2023-08-27 |

Capital.com holds three years of hourly data. Every measurement in this
document before section 32 ran on ~660 bars of it.

The random-entry benchmark was rerun on the full three years. Comparing
the two largest independent samples of the same strategy:

| basis | signal n | difference vs random | t |
|---|---:|---:|---:|
| 1 year, 16 instruments | 1,131 | **-0.0220** | -0.83 |
| 3 years, 4 instruments | 810 | **+0.0302** | +1.12 |

**The sign reverses.** Neither is significant, and the second test
resolves differences from 0.054 R upward, so both readings sit inside
the noise band. A real effect does not change direction between samples
of eight hundred and eleven hundred trades — this is the same lesson
section 34 drew from three conflicting readings of the same strategy,
now on samples an order of magnitude larger.

That makes the finding stronger, not weaker. "No edge" is no longer just
an absence of significance; it is a quantity that will not hold a sign
across independent large samples.

The backtest default moves from 180 to **365 days**. Not the full three
years: the nightly run would take about half an hour per strategy, and
2023 data describes a regime two years gone. A year is enough for
walk-forward segments and recent enough to resemble what the bot trades
now.

## 46. The regime router discards the better half

Breaking down what the live loop rejects: of 31 signals refused since the
cutoff, **25 were stopped by the ADX regime router** — 81 %, far ahead of
the duplicate lock (3), the stop floor (2) and the position cap (1). It
is by a wide margin the system's strongest throttle, and its value had
never been measured against a random control.

Preregistered: the passing set must beat the unfiltered set at t > 2.0.
A filter discarding four signals in five without improving expectancy is
pure throughput loss — and section 40 shows throughput is exactly what
this system lacks to validate anything.

Ten cheap instruments, 1h, 365 days, router applied by hand so both sides
are visible:

| group | n | E[R] | t |
|---|---:|---:|---:|
| **passed by the router** | 675 | **-0.0518** | -1.93 |
| **rejected by the router** | 1,445 | **+0.0116** | +0.66 |
| all signals | 2,120 | -0.0086 | -0.58 |
| random control | 5,203 | -0.0050 | -0.55 |

| comparison | difference | t |
|---|---:|---:|
| passed minus all | -0.0433 | -1.41 |
| **passed minus rejected** | **-0.0635** | **-1.98** |

**Not accepted** — and the direction is the opposite of its purpose. The
router keeps the worse half and throws away the better one, while cutting
signal count by 68 %.

t = -1.98 sits just under the threshold, so **the router was left on**.
This document has retracted several findings built on values like that,
and acting on one now would repeat the error the whole exercise has been
about. The passing set's own t of -1.93 against zero is likewise not
significant.

But it is recorded as the strongest filter effect measured here, and the
only one whose point estimate is materially negative. Two things would
settle it: the same test on the three-year history (32, 45), and a
forward comparison — which section 40 shows needs throughput the router
is itself suppressing. If a later test reaches t < -2.0 on an independent
sample, turning the router off is the indicated change, and it would
roughly triple the signal rate at no cost in expectancy.

### 46b. The router question, settled on three years

Section 46 measured the router at t = -1.98 against its rejected half and
left it on, because acting on a value just under threshold is the error
this document keeps retracting. The three-year history settles it.

Same specification, no parameter changes, only more data — five
instruments, ~18,000 bars each:

| basis | passed | rejected | difference | t |
|---|---:|---:|---:|---:|
| 1 year, 10 instruments | -0.0518 | +0.0116 | -0.0635 | **-1.98** |
| **3 years, 5 instruments** | **+0.0073** | **+0.0164** | **-0.0091** | **-0.34** |

The effect shrinks sevenfold and the sample nearly triples per side
(1,038 passed, 2,078 rejected). **The one-year reading was noise.**

The standing rule — keep the router until an independent test reaches
t < -2.0 — was correct, and the independent test has now come back at
t = -0.34. **The router stays on**, and this is the resolution rather
than another deferral.

What remains true is narrower than section 46 suggested: the router
suppresses 67 % of signals for no measurable gain *and* no measurable
harm. That matters only for section 40's validation problem — tripling
throughput at neutral expectancy would let forward evidence accumulate
three times faster. It is not a profitability lever, and nothing here
justifies changing it.

Fourth time in this document that a near-threshold value dissolved under
a larger sample: the cost-threshold band (19), turtle_breakout (32), the
cross-sectional effect (33), and now the router. The pattern is the
finding.

## 47. The exit paths audited, and one more denominator trap

The entry guards were audited in 44c; the exit paths only for existence.
A defect there costs money directly, so:

| outcome | n | mean R | USD |
|---|---:|---:|---:|
| loss | 234 | **-1.029** | -1,347.83 |
| manual | 167 | -0.647 | +117.67 |
| win | 115 | +1.221 | +805.41 |

**The stops work.** Losses average -1.029 R against a designed -1.0, and
only 3 of 221 stop exits run past -1.5 R — those are gaps, not a
mechanism failure. Wins average +1.221 R against a 1.5 target, the
shortfall being costs and early exits, which is expected.

The `manual` row is contradictory: a mean of **-0.647 R** alongside a
**positive** dollar total. That is the denominator trap of section 20
again, and this instance shows how violent it is:

| basis | n | E[R] | t |
|---|---:|---:|---:|
| all manual exits | 111 | **-0.647** | -1.84 |
| excluding risk under 1 USD | 106 | **+0.118** | +1.76 |
| capital-weighted | 111 | +0.180 | — |

**Five trades out of 111 flip the sign**, and they flip it from
significantly-looking-negative to positive. Both t values are near
threshold in opposite directions, from the same data.

This is not a new code defect — the vetoes and both risk controls were
corrected in 20 and 21. It is a defect in *analysis practice*: ad-hoc
queries throughout this investigation used unweighted R means, and this
one would have supported either conclusion depending on a filter nobody
would think to mention.

**No action taken on the +0.118.** Time-based exits (stale, flip, trail)
outperforming stop/target exits would be an interesting claim, but t =
1.76 selected after seeing the data is precisely the pattern that
dissolved four times already (46b). Recorded, not acted on.

The rule for future work: **report capital-weighted expectancy, or state
the risk floor applied.** An unweighted R mean over trades of unequal
size is not a summary of anything.

## 48. The dashboard showed a fifth of the loss

The dashboard's stat views exclude retired mean-reversion strategies and
Kraken — deliberately, and documented: they describe the active
trend-only book. But the figure was labelled **"All-time"**:

| | trades | PnL |
|---|---:|---:|
| shown as "all-time" | 292 | **-92.95 USD** |
| excluded (mean reversion, Kraken) | 227 | **-372.60 USD** |
| **actual total** | **519** | **-465.56 USD** |

**The dashboard reported a fifth of the realised loss** under a label
meaning the opposite. The excluded trades are not hypothetical — they
were opened, closed and settled on the account.

This is the same shape as every accounting defect in this document: a
view that flatters in one direction, with a plausible reason attached.
The reason here is genuinely good — a chart of a book that no longer
trades tells you nothing about what the bot does now — which is exactly
why it survived unexamined.

The filtering is unchanged, because the intent behind it is right. What
changed is that the excluded total is now on the card:

- `All-time (aktives Buch)` — was just "All-time"
- `stillgelegt (227 Trades)` — the excluded book
- `Gesamt inkl. stillgelegt` — the two summed

Five tests cover the rows, including the empty-retired case, so the
excluded total cannot silently vanish again.

Worth stating plainly: I have spent this investigation correcting
measurements that flattered results, and the operator-facing summary —
the one artefact a human actually looks at — was the last and largest of
them. It was showing -93 USD where the truth is -466.

## 49. Three levers tested, three levers dead

Four days of downtime (the `@reboot` cron entry had gone missing) ended
with the book restarted and three candidate levers queued. All three
were measured and none survived. Recording them so the next run does not
re-test the same ideas.

### 49a. The book stopped losing when the blocklists were applied

The -77 USD since the router era looked like an active bleed. Recomputing
the same window under the configuration that is actually live today —
`COST_BLOCKED_PAIRS` removed, `donchian_breakout_v3` and `donchian_trail`
removed, mean reversion removed — leaves 161 of 266 trades:

| basis | n | PnL | mean/trade | t | p |
|---|---:|---:|---:|---:|---:|
| everything closed since 2026-07-10 | 266 | -77.25 | -0.290 | | |
| **only what today's config would trade** | **161** | **-1.76** | **-0.011** | **-0.05** | **0.96** |

The loss was produced almost entirely by instruments and strategies that
have since been blocked. The remaining book is not losing; it is flat and
statistically indistinguishable from zero. That reframes the problem: not
a bleed to stop, an edge to find.

Nothing survives correction. Every strategy (6 tested) and every pair
(12 tested, n>=5) has a Bonferroni-corrected p of 1.000. The largest
remaining single position is OIL_CRUDE at -16.10 over 29 trades, raw
p = 0.35.

### 49b. OIL_CRUDE is not a cost problem — not blocked

OIL_CRUDE was the obvious block candidate, and OIL_BRENT earning +5.70
over 25 trades in the same window made a spread difference the obvious
suspect. Fresh instrument-level audit against measured live stops:

| pair | %/side | mean live stop | cost/risk | at tightest stop |
|---|---:|---:|---:|---:|
| OIL_CRUDE | 0.0225 % | 1.161 % | **3.9 %** | 4.3 % |
| OIL_BRENT | 0.0213 % | 1.248 % | 3.4 % | 4.1 % |
| WHEAT | 0.0531 % | 1.289 % | 8.2 % | 9.4 % |
| AAVEUSD (blocked) | — | 1.128 % | 88.6 % | 95.2 % |
| DOTUSD (blocked) | — | 1.055 % | 48.3 % | 48.6 % |

The two oils cost the same to trade. OIL_CRUDE sits at 3.9 % of risk
against a 10 % ceiling. **No block** — the difference between them is
noise, and the table also confirms the existing blocklist was right by an
order of magnitude.

### 49c. The walk-forward simulator charged no costs

Before the reward:risk sweep could mean anything, the tool had to be
fixed. `_simulate` scored a win as exactly `+rr` and a loss as `-1.0`:
no spread, no venue minimum stop. That is not a rounding issue for an
`--rr` comparison — a wider target scales the win leg while the cost leg
stays invisible, so the sweep was rigged toward whatever target was
largest. Costs are charged on the entry price but measured against the
stop distance, which is exactly why the live book realises a 1.41 payoff
against a planned 1.5.

Now reusing the resolvers `spot_backtest.py` already owns.

### 49d. A wider target does not pay — 1.5 stays

`donchian_breakout`, 10 instruments, 60 days, 3 segments, net of costs:

| rr | mean E[R] across pairs |
|---|---:|
| 1.0 | -0.0216 |
| **1.5 (current)** | **-0.0027** |
| 2.0 | -0.0213 |
| 2.5 | -0.0228 |
| 3.0 | -0.0028 |

No monotonic gain, no ordering worth acting on, and the current setting
is among the best. **The reward:risk lever is dead.**

The reason is visible once the resolution mix is reported, which it now
is: **EURUSD and GBPUSD resolve 100 % by timeout.** Not one trade in 60
days touches stop or target inside the 24h limit — Capital's 1.05 %
minimum stop is far wider than hourly FX movement, so both barriers are
decorative and the trade is whatever the close gives after 24 hours. That
also explains the live book's 116 of 266 time-based exits.

Tempting conclusion: the venue floor is destroying the edge. It is not
supported. Splitting live trades by whether the stop sat on the floor:

| | n | PnL | mean | t | p |
|---|---:|---:|---:|---:|---:|
| stop pinned to venue minimum | 108 | +20.86 | +0.193 | +0.85 | 0.39 |
| stop set by ATR | 53 | -22.63 | -0.427 | -0.92 | 0.36 |

The pinned trades did *better*, and neither side is significant. The
100 %-timeout mechanic is real but does not translate into worse results.

### 49e. The router, settled a second time — now forward

Section 46 asked for a forward comparison the backtests could not supply.
The journal now supplies it: every regime-vetoed signal is written with
its full entry, stop and target, so the bars that followed decide what it
would have returned. 170 vetoed and 18 taken signals since instrumentation
began, replayed net of spread (`scripts/regime_counterfactual.py`):

| group | n | E[R] | win | t |
|---|---:|---:|---:|---:|
| passed by the router (ADX>=30) | 15 | -0.177 | 40.0 % | -0.81 |
| rejected by the router (ADX<30) | 155 | -0.097 | 43.2 % | -1.69 |
| **difference** | | **-0.079** | | **-0.35** |

The forward reading lands on **t = -0.35** against 46b's three-year
backtest value of **t = -0.34**. Two independent samples, one historical
and one live, agreeing to the second decimal. **The router stays on**, and
the question is now settled from both directions.

The decisive part is not the difference but the levels: *both* groups are
negative. Switching the router off would not have bought profit, it would
have bought more losing trades. The suppressed signals are not the better
half — they are the same flat nothing, in greater quantity.

Below ADX 20 the suppressed signals win 51.2 % of the time at E[R]
-0.121: frequent small wins against rare large losses, which is the
mean-reversion payoff shape that was retired in July for losing money.

### What this run establishes

The active book is flat, not bleeding. Blocklists, reward:risk, and the
regime router have each been measured and none of them is the missing
piece. At 3.5 trades a day and this dispersion, no per-pair or
per-strategy effect can reach significance in any reasonable time — the
constraint is not analysis quality, it is that a flat book cannot be
tuned into a 50 EUR/day book by adjusting exit levels on the strategies
it already runs.

## 50. The holding leash is not the lever either

Section 47 left one exit-side hint standing: time-based exits looked
better than stop/target exits after the risk floor. The leash length was
the one exit parameter never measured on the cost-charging simulator
(49c), so it was swept: 6 / 12 / 24 / 48 / 96 bars, 1h, 365 days, 3
segments, 10 unblocked instruments, the three live trend strategies
(`scripts/max_hold_sweep.py`).

| hold | n | E[R] | timeout % | diff vs 24 | t |
|---:|---:|---:|---:|---:|---:|
| 6 | 8,336 | -0.0143 | 72 | -0.0195 | -1.27 |
| 12 | 7,062 | -0.0067 | 57 | -0.0119 | -0.72 |
| **24 (live)** | 5,832 | **+0.0052** | 38 | — | — |
| 48 | 5,001 | -0.0105 | 20 | -0.0157 | -0.77 |
| 96 | 4,533 | -0.0241 | 9 | -0.0293 | -1.34 |

**The live setting is the maximum**, in the pooled reading and in each
strategy on its own. Shorter leashes lose because the trade is cut before
either barrier is reached and the round-trip cost is paid on a random
close; longer ones lose because the timeout leg drifts negative once the
breakout has failed. Nothing is significant, nothing is better. The
leash stays at 24 bars. Full run log in `docs/levers.md`.

## 51. Time of day, opened once, and it dissolved on the second sample

Section 7 refused to open time/session/weekday variants because a free
search over window boundaries is an overfit machine. One window was
opened here under preregistration: signal bar inside [07:00, 20:00) UTC
against the rest, hold 24, costs charged, three live trend strategies,
ten unblocked instruments (`scripts/session_split.py`). Acceptance
required t > 2.0 for the session side.

| sample | E[R] in session | E[R] outside | difference | t |
|---|---:|---:|---:|---:|
| last 365 days (n = 5,829) | -0.0130 | **+0.0498** | -0.0628 | **-2.17** |
| prior 730 days (n = 11,786) | -0.0101 | **-0.0487** | +0.0386 | **+1.89** |

The preregistered direction failed. The opposite reading on the recent
year cleared |t| = 2 — and had it been taken at face value the bot would
now trade only overnight. On the two years before, disjoint by
construction, the overnight side is the *worse* one by nearly the same
margin. The 3-hour buckets reverse individually as well: 00–09 UTC reads
+0.07 to +0.11 on the recent year and -0.02 to -0.05 on the prior two.

Fifth dissolving near-threshold value (19, 32, 33, 46b, now this).
Nothing changes, and the reason section 7 gave for not opening this
family stands: a single preregistered window produced a |t| > 2 artefact
on its first try, which is what a free search would have produced by the
dozen.

## 52. A break-even stop trades losses for scratches, one for one

The ATR trail (27) lost because it capped winners. The mildest version
of stop management — move the stop to entry once the trade has earned
0.5, 0.75 or 1.0 R, and nothing further — was measured on the
cost-charging simulator, 365 days, ten instruments, three live trend
strategies (`scripts/breakeven_sweep.py`):

| activation | E[R] | loss % | scratch % | win % | diff vs live | t |
|---:|---:|---:|---:|---:|---:|---:|
| none (live) | +0.0063 | 36.9 | 0.0 | 24.3 | — | — |
| 0.5 R | +0.0110 | 25.2 | 30.0 | 17.4 | +0.0046 | +0.28 |
| 0.75 R | +0.0048 | 30.2 | 17.4 | 20.1 | -0.0015 | -0.09 |
| 1.0 R | +0.0010 | 33.5 | 9.6 | 21.9 | -0.0054 | -0.30 |

Every full stop the move avoids is paid for by a full target it also
avoids: at 0.5 R the loss share falls by twelve points and the win share
by seven, and the expectancy difference is 0.005 R. On a random-walk
path that is exactly what should happen — a stop at entry removes
symmetric mass from both tails — and the measurement is consistent with
the entries being random (30, 45). The live fixed stop stays.

## 53. Entering on the pullback instead of the breakout changes nothing

First candidate from the "different signal source" list: keep the
breakout as the trigger, but enter with a limit at the level that was
broken and only if price returns to it within K bars
(`scripts/pullback_entry.py`; 365 days, ten instruments, three live
trend strategies, costs charged on the fill, hold counted from the fill).

| K | n | fill % | E[R] | diff vs market entry | t |
|---:|---:|---:|---:|---:|---:|
| 0 (live) | 5,830 | 100 | +0.0063 | — | — |
| 3 | 4,499 | 67 | -0.0058 | -0.0121 | -0.61 |
| 6 | 4,789 | 74 | +0.0015 | -0.0048 | -0.25 |
| 12 | 4,941 | 80 | +0.0193 | +0.0130 | +0.67 |

A fifth to a third of the signals never retrace and are lost; those are
by construction the moves that ran, and what remains starts nearer its
stop. Per strategy the sign is not even stable (turtle negative at every
K, keltner positive at every K, donchian both). Consistent with random
entries (30, 45): shifting the entry price by a fraction of an ATR on a
signal that carries no information shifts nothing. Not adopted.

## 54. Fading a failed breakout loses, significantly

Second candidate from the different-signal-source list: when the close
returns inside the level a breakout had crossed, within K bars, trade
against the breakout at that close (`scripts/failed_breakout.py`; 365
days, ten instruments, three live trend strategies as the trigger, hold
24, costs charged, count-matched random entries as the control).

| K | n | E[R] | t vs 0 | vs random | t | vs live breakout | t |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 3,904 | **-0.0632** | **-4.14** | -0.0402 | **-2.38** | -0.0695 | **-3.45** |
| 6 | 4,584 | -0.0473 | -3.30 | -0.0196 | -1.24 | -0.0536 | -2.76 |

Every strategy, every K, negative — and at K = 3 the signal is
significantly worse than random through the same simulator. That is
new: every earlier signal measured here was *indistinguishable* from
random. The shape is the one section 2 retired live and section 49e
found again among the router's suppressed signals: frequent small wins
against rare large losses. Not adopted.

The obvious next thought — trade *with* the breakout on that same
close, since its fade loses — is not implied by this table. Reversing a
trade negates the gross leg but pays the cost leg twice and inverts the
1.5:1 payoff, so the continuation version is a separate, post-hoc
hypothesis that would need its own preregistered run.

## 55. The retest entry is random too

Section 54 closed with the warning that a losing fade does not imply a
winning continuation. Measured as its own preregistered run
(`scripts/retest_continuation.py`, same setup, direction with the
breakout):

| K | n | E[R] | vs random | t | vs live breakout | t |
|---:|---:|---:|---:|---:|---:|---:|
| 3 | 3,776 | -0.0082 | +0.0147 | +0.85 | -0.0145 | -0.71 |
| 6 | 4,420 | -0.0201 | +0.0077 | +0.47 | -0.0264 | -1.34 |

Random. The fade lost 0.063 R, the continuation loses 0.008 R, and the
gap between them is the cost paid twice plus the inverted 1.5:1 payoff.
Both retest variants are closed.

## 56. The opening-range breakout: a textbook dissolve

First trigger outside the channel family: the cash-open hour of the
four indices as the range, first close beyond it the same day as the
entry (`scripts/orb_breakout.py`; hold 24, costs charged, random
control, acceptance preregistered at t > 2.0 vs random on both a recent
and a disjoint older sample).

| sample | n | E[R] | t vs 0 | vs random | t |
|---|---:|---:|---:|---:|---:|
| last 365 days | 609 | **+0.0843** | **+2.46** | +0.0792 | **+2.09** |
| prior 730 days | 1,166 | +0.0096 | +0.41 | +0.0111 | +0.43 |

Had the recent year been the only sample, this would have been the
first accepted signal in the document: positive, significant against
zero, significant against random, above the live entry. The disjoint
two years, with twice the trades, return zero on every column. That is
the sixth time (19, 32, 33, 46b, 51, now this), and it is the strongest
case yet for the rule that nothing is accepted on one sample — the
recent-year reading was not near the threshold, it was clearly past it.

## 57. Previous-day range on the commodities: zero on both samples

Companion to 56 for GOLD, OIL_CRUDE and OIL_BRENT: the first hourly
close outside the previous day's range enters in that direction, once
per day (`scripts/prev_day_range_breakout.py`, same setup and
preregistration as the opening-range run).

| sample | n | E[R] | vs random | t | vs donchian | t |
|---|---:|---:|---:|---:|---:|---:|
| last 365 days | 524 | +0.0071 | +0.0464 | +0.84 | -0.0588 | -0.89 |
| prior 730 days | 976 | -0.0363 | +0.0105 | +0.29 | +0.0322 | +0.74 |

Nothing to dissolve this time — it never rose. GOLD alone is positive
on both samples (t = 1.6, 1.3), which is one instrument of three read
after the fact, and both oils are negative on both. Not adopted.

## 58. The channel length was the unswept grid axis, and it is flat too

Section 1's grid varied RR, stop, hold and ADX but never the Donchian
lookback. Swept here at 10 / 20 / 40 / 80 bars on both samples
(`scripts/donchian_period_sweep.py`, ten instruments, costs charged):

| period | last 365 d diff vs 20 | t | prior 730 d diff vs 20 | t |
|---:|---:|---:|---:|---:|
| 10 | -0.0292 | -1.03 | +0.0024 | +0.12 |
| 40 | -0.0472 | -1.48 | +0.0168 | +0.77 |
| 80 | +0.0106 | +0.30 | +0.0311 | +1.29 |

Nothing reaches threshold on either sample, 40 changes sign between
them, and the only consistent direction (80) buys 0.01–0.03 R with half
the trades. The lookback is not the lever; the grid's conclusion holds
on its missing axis as well.

## 59. The Keltner band width is flat as well

Companion to 58 for the other live channel: band multiples 1.0 / 1.5 /
3.0 against the live 2.0 (`scripts/keltner_width_sweep.py`, both
samples, ten instruments, costs charged):

| atr_mult | last 365 d diff vs 2.0 | t | prior 730 d diff vs 2.0 | t |
|---:|---:|---:|---:|---:|
| 1.0 | -0.0110 | -0.38 | -0.0301 | -1.50 |
| 1.5 | -0.0005 | -0.02 | -0.0177 | -0.87 |
| 3.0 | -0.0276 | -0.77 | +0.0082 | +0.34 |

Tightening the band buys more trades of the same nothing, widening it
changes sign between samples. Both live channel parameters (58, 59)
now measured on their own axis; neither is the lever.

## 60. The stop width — the first lever whose sign survived the second sample

Section 25 swept `stop_atr` through the path section 28 retracted, so
the live 1.0 had never been measured on the cost-charging simulator.
Rerun at 1.5 / 2.0 / 3.0 on both samples, three live trend strategies
pooled, net result split into gross and cost (`scripts/stop_width_sweep.py`):

| stop_atr | last 365 d net diff | t | gross diff | prior 730 d net diff | t | gross diff |
|---:|---:|---:|---:|---:|---:|---:|
| 1.5 | +0.0025 | +0.13 | -0.0011 | +0.0037 | +0.29 | +0.0008 |
| **2.0** | **+0.0223** | +1.21 | +0.0150 | **+0.0082** | +0.66 | +0.0019 |
| 3.0 | +0.0228 | +1.29 | +0.0101 | +0.0121 | +0.99 | -0.0000 |

Neither t clears 2.0, and by the rule this document has applied to
every signal claim that would end the matter. It does not, because the
effect is not a signal claim. The round-trip spread is charged on the
entry price and R is measured against the stop (49c), so the cost in R
falls as 1/stop: 0.030 R at 1.0, 0.023 R at 2.0, 0.017 R at 3.0, on
both samples, to the third decimal. That part is arithmetic. What
needed measuring was whether a wider stop *loses* gross expectancy —
fewer stop-outs but smaller moves per R — and it does not: +0.015 and
+0.002 R on the two samples, both non-negative, neither significant.

**Built in at 2.0.** Not 3.0: at 3.0 the target sits 4.5 ATR away and
61 % of trades run to the 24-bar timeout, which is no longer a
stop-and-target strategy. At 2.0 the stop still resolves half the
trades. Risk per trade is unchanged — the 3 USD is divided by a wider
distance, positions halve, and the minimum-size guard skips rather than
enlarges. The expected gain is modest: +0.007 R per trade from cost
alone, about +0.08 USD per day at the current rate, +0.02 R if the
recent year's gross reading holds. It is the first lever in sixty
sections to point the same way on two independent samples, and it does
so because most of it was never a bet.

What this does *not* say: that the entries have an edge. Gross stays
where 30, 45 and every section since left it.

## 61. Reward:risk at the 2-ATR stop — still 1.5

With the stop widened (60), the target was re-swept at the live
configuration (`scripts/rr_at_stop_sweep.py`, both samples, gross and
cost split):

| rr | last 365 d net diff | t | prior 730 d net diff | t | timeout % |
|---:|---:|---:|---:|---:|---:|
| 1.0 | -0.0185 | -1.09 | +0.0001 | +0.01 | 35 / 38 |
| 2.0 | +0.0095 | +0.50 | +0.0009 | +0.07 | 54 / 55 |
| 3.0 | +0.0157 | +0.78 | +0.0125 | +0.92 | 61 / 62 |

The cost column does not move with rr, so this is entirely a gross
claim, and it does not reach t = 1 on either sample. The wider targets
buy their 0.01 R by never being hit: at 3.0 the trade is a 24-bar hold
with a decorative target. 1.5 stays, as 15 and 49d concluded.

## 62. The leash at the 2-ATR stop — still 24 bars

The wider stop (60) leaves more trades to the timeout, so the leash was
re-swept at the live configuration (`scripts/max_hold_at_stop_sweep.py`,
both samples):

| hold | last 365 d net diff | t | prior 730 d net diff | t |
|---:|---:|---:|---:|---:|
| 12 | -0.0292 | -1.82 | +0.0001 | +0.01 |
| 48 | -0.0300 | -1.45 | +0.0050 | +0.35 |
| 96 | -0.0411 | -1.81 | +0.0169 | +1.06 |

Same answer as the first sweep (50): the live leash is the best value
on the recent year, and the only alternative that looks better on the
older sample is the one that looks worst on the recent one. 24 stays.

## 63. The turtle channel length is flat too

Companion to 58 for the slow channel, measured at the 2-ATR stop
(`scripts/turtle_period_sweep.py`, both samples):

| period | last 365 d diff vs 55 | t | prior 730 d diff vs 55 | t |
|---:|---:|---:|---:|---:|
| 20 | +0.0238 | +0.72 | -0.0048 | -0.21 |
| 110 | +0.0206 | +0.51 | +0.0251 | +0.93 |

The longer channel points the same way twice, which by now is the
minimum and not the bar: at t = 0.5 and 0.9 with 30 % fewer trades and
no arithmetic component it is noise with a consistent sign, not a
lever. 55 stays. All three live channel parameters (58, 59, 63) are
now measured on their own axis.

## 64. The halved position clears the broker minimum

Follow-up to 60: at the 2-ATR stop the position is half its former
size, and the sizing guard refuses rather than enlarges anything below
the broker minimum. Replayed with the live constraints and the live
sizing function (`scripts/min_size_skips.py`, 365 days, ten instruments):

| stop | skipped | on | planned risk (kept) | USD per kept trade |
|---:|---:|---|---:|---:|
| 1.0 ATR | 0.1 % | — | 2.52 | +0.015 |
| 2.0 ATR | 1.4 % | OIL_CRUDE 6 %, OIL_BRENT 8 % | 2.59 | +0.078 |

Only the oils' 1-lot minimum is coarse enough to matter, and the
refused trades read -0.11 R on this sample. Planned risk edges toward
the 3 USD target because the wider stop needs less notional. Two things
worth stating: the stop change is inert on indices and FX, which the
1.05 % venue floor pins at either width, so its whole effect lives on
BTCUSD, ETHUSD, GOLD and the oils; and the guard needs no adjustment.

## 65. Pinned and ATR-bound instruments — no split, one candidate

Whether the book should keep only the instruments where the 2-ATR
stop actually binds (`scripts/pinned_groups.py`, both samples, per
group against random entries):

| sample | ATR-bound vs random | t | pinned vs random | t | group diff | t |
|---|---:|---:|---:|---:|---:|---:|
| last 365 d | +0.0915 | +4.04 | +0.0206 | +1.29 | +0.0365 | +1.51 |
| prior 730 d | +0.0117 | +0.77 | +0.0065 | +0.59 | -0.0374 | -2.28 |

The recent year's t = 4.04 for the ATR-bound group would have been the
headline; the older sample returns it to noise, driven by both oils
turning significantly negative there. The groups are not different in
any stable way.

One instrument survives both samples on its own: GOLD, +0.152 R over
random at t = 2.71 on the recent year and +0.100 R at t = 3.24 on the
prior two, from 498 and 917 trades. That is a post-hoc pick from ten,
and section 36 recorded `turtle_breakout/GOLD` at t = 2.16 once before
with live disagreeing. It is therefore not acted on here but
preregistered: per strategy, router applied, both samples, t > 2.0
against random per strategy, before GOLD is pinned to the other two
trend strategies.

## 66. GOLD, per strategy and after the router — consistent, not significant

The preregistered follow-up to 65 (`scripts/gold_strategies.py`; router
applied, control drawn from router-passing bars, both samples):

| strategy | last 365 d vs random | t | prior 730 d vs random | t |
|---|---:|---:|---:|---:|
| donchian_breakout | +0.101 | +0.75 | +0.108 | +1.49 |
| turtle_breakout | +0.228 | +1.55 | +0.095 | +1.24 |
| keltner_breakout | +0.148 | +1.02 | +0.073 | +0.95 |
| momentum | -0.099 | -0.25 | -0.039 | -0.15 |

Six positive cells out of six for the trend strategies, none above
t = 1.6. The pooled t of section 65 was those three added together
before the router took six signals in ten. This is the most consistent
instrument in the book and still not a lever by the rule; it is exactly
what section 40 predicted — an effect of this size cannot be validated
at this trade count. GOLD keeps its two ranked slots and gets no third.

## 67. The router at the 2-ATR stop — a third sign flip

Re-measured at the live configuration (`scripts/router_at_stop.py`,
both samples, random control):

| sample | passed − rejected | t | all − random | t | passed − random | t |
|---|---:|---:|---:|---:|---:|---:|
| last 365 d | -0.0371 | -1.47 | +0.0396 | +2.67 | +0.0075 | +0.35 |
| prior 730 d | +0.0333 | +1.94 | +0.0100 | +1.00 | +0.0317 | +2.13 |

Section 46 read the router at t = -1.98, 46b at -0.34, 49e forward at
-0.35; now -1.47 and +1.94 on two samples of the same configuration.
Whatever the router does, it does not do it consistently, and the rule
that it comes off only on a forward reading below t = -2.0 stands. The
signal-versus-random line repeats section 45 at the wider stop: the
recent year says yes at t = 2.67, the two years before say t = 1.00.

## 68. The cost ceiling at the 2-ATR stop — a backstop, not a lever

At the wider stop every unblocked instrument sits below 10 % of risk,
so the ceiling was re-read by bucketing trades on their own cost
(`scripts/cost_buckets.py`, both samples):

| ceiling | last 365 d Σ R | prior 730 d Σ R |
|---:|---:|---:|
| 10 % (live) | +151.5 | -135.7 |
| 5 % | +143.5 | -160.1 |
| 3 % | +72.5 | -25.6 |

The 5–10 % bucket reads +0.012 and +0.016 R net on the two samples,
better than the middle bucket on the older one — cost share does not
order expectancy once the untradeable instruments are gone, which is
what section 11 said of the live journal. Tightening the ceiling only
removes trades. It stays at 10 %.

## 69. Exiting when the regime fades loses

An ADX-based exit — close when ADX(14) falls below 15, 20 or 25 —
against the fixed 24-bar leash at the live configuration
(`scripts/regime_exit.py`, both samples):

| ADX exit below | last 365 d diff vs live | t | prior 730 d diff vs live | t |
|---:|---:|---:|---:|---:|
| 15 | -0.0196 | -1.13 | +0.0026 | +0.22 |
| 20 | -0.0304 | -1.93 | -0.0059 | -0.55 |
| 25 | -0.0435 | -2.96 | -0.0058 | -0.58 |

Monotonically worse on the recent year and no better on the older one.
Cutting a trade on a regime reading truncates it at a random point of
its path with the spread already paid; the leash does the same but
later, after the barriers had their chance. The exit side has now been
measured on hold (50, 62), trail (27), break-even (52), target (49d,
61) and regime (this); none beats the fixed stop, target and 24-bar
leash the live loop runs.

## 70. The instruments nobody measured, and one that loses everywhere

The active list carries 17 instruments outside the ten every sweep in
this document used. Measured the same way (`scripts/other_instruments.py`;
both samples, random control, and the router-passed subset that live
actually trades):

| | last 365 d | prior 730 d |
|---|---:|---:|
| group, all signals, E[R] / t | -0.0272 / -3.19 | -0.0290 / -4.91 |
| group, router-passed, E[R] / t | -0.0885 / -6.43 | -0.0074 / -0.75 |
| AU200, router-passed, E[R] / t | -0.301 / -5.48 | -0.114 / -2.58 |
| AU200, random entries | -0.079 | -0.041 |

The group is a cost sink before the router on both samples and, on the
live path, a heavy loser on the recent year that flattens on the older
one — the by-now familiar shape, and not enough for a group block. AU200
is different: significantly negative on the live path on both samples,
negative for random entries too, and 5 of 5 live trades lost. That is
the instrument rather than the signal. **AU200 is blocked for entries**,
on a separate expectancy list so the cost audit's criterion stays
clean. SILVER (t = -1.84 / -2.83) is a watch. This is the second lever
in the session to hold on two samples, and like the first (60) it is a
removal of a measured loss, not the discovery of an edge.

## 71. The router's threshold is not the lever either

The trend floor at 20 / 25 / 35 against the live 30, router-passed
expectancy at the 2-ATR stop (`scripts/adx_threshold.py`, both samples):

| ADX ≥ | last 365 d diff vs 30 | t | prior 730 d diff vs 30 | t |
|---:|---:|---:|---:|---:|
| 20 | -0.0060 | -0.24 | -0.0272 | -1.58 |
| 25 | +0.0097 | +0.37 | -0.0219 | -1.21 |
| 35 | -0.0431 | -1.34 | -0.0064 | -0.29 |

Lowering the floor adds trades at lower expectancy on the older sample
and does nothing on the recent one; raising it removes trades without
improving what remains. With 46, 46b, 49e, 67 and this, the router has
been measured on and off, forward and back, and at every plausible
level: it stays at 30 because nothing else is better twice.

## 72. The 4h variants are random too

The two 4h strategy names in the live rotation, measured on 4h bars at
the 2-ATR stop (`scripts/h4_variants.py`, both samples, random control):

| sample | E[R] all | vs random | t | E[R] router-passed | vs random | t |
|---|---:|---:|---:|---:|---:|---:|
| last 365 d | -0.0401 | -0.0430 | -1.09 | -0.0181 | -0.0211 | -0.35 |
| prior 730 d | -0.0065 | +0.0243 | +0.89 | +0.0048 | +0.0357 | +0.91 |

The slower timeframe changes the trade count and nothing else. The
4h pins stay as they are — there is no reading that would justify
removing or expanding them.

## 73. Momentum — positive twice, significant never

The strategy the selector ranks highest on the indices, measured
against random on twelve instruments (`scripts/momentum_check.py`,
both samples):

| sample | vs random, all | t | vs random, router-passed | t | passed / all |
|---|---:|---:|---:|---:|---:|
| last 365 d | +0.0514 | +1.41 | +0.0428 | +0.53 | 148 / 1,042 |
| prior 730 d | +0.0271 | +1.06 | +0.0725 | +1.32 | 298 / 1,941 |

Same sign on both samples and on both sides of the router, and nothing
near threshold. The router discards six momentum signals in seven,
which section 46b already measured as neither help nor harm. Recorded
with GOLD (66) as the two consistently-signed readings in the book;
neither clears the bar, and at these trade counts neither can (40).

## 74. The wider stop drops HK50 at the broker minimum

First live intent under the 2-ATR stop: HK50, refused because the
halved size (0.0099) sits under the 0.01 minimum — at the 1.05 % floor
the same signal sized to 0.0112 and traded. The remaining active
instruments were replayed with the live sizing function
(`scripts/min_size_skips.py`): USD-quoted ones skip at 0 % (SILVER
5.8 %); the JPY- and HKD-quoted rows of that replay are not live
figures because the script does not convert quote currencies, which
is now on record.

HK50 measured -0.0625 and -0.0353 R on the two samples (70) and its
random control was positive, so the guard is removing an instrument
that lost. Nothing to change; the sizing guard remains fail-closed and
the stop stays at 2 ATR.

## 75. Weekend gaps are real, rare, and not a lever

Friday-afternoon entries against the rest, with a simulator that books
the open when a bar gaps through the stop (`scripts/weekend_entries.py`,
both samples):

| sample | E[R] Friday | E[R] rest | diff | t | gapped stops, mean R |
|---|---:|---:|---:|---:|---:|
| last 365 d | +0.0067 | +0.0254 | -0.0187 | -0.41 | -1.75 |
| prior 730 d | -0.0479 | -0.0111 | -0.0369 | -1.28 | -1.78 |

A gap through the stop costs 1.75 R, not 1 — the shared simulator
understates that — but it happens on 0.1–0.2 % of trades, and Friday
entries as a class sit inside the noise on both samples. The oils are
the one consistent Friday loser (t = -1.5, -1.2) and stay on watch.
Nothing changes.

## 76. The simulator now pays for gaps

Follow-up to 75: `_simulate_trades` books the open when a bar opens
beyond the stop. Weekend gaps cost the oils 1.75 R a stop rather than
1, on one Friday entry in seven to ten; across all trades the
correction is 0.002–0.005 R, so no ranking moves on it except where it
should — the two oils. Seventeenth measurement defect corrected in
this document, and like the others it read in the flattering direction.

## 77. The risk budget was in the wrong currency

`calculate_position_size` divides a USD budget by a stop distance in
the instrument's quote currency. The broker prices DE40 in EUR, UK100
in GBP, HK50 in HKD and the yen crosses in JPY, and the account is in
EUR. The journal confirms it: size × stop is 1.9–2.7 quote units on
every instrument, meaning 4.06 USD of risk on UK100 and 0.02 USD on
AUDJPY against a 3 USD cap — and the JPY/HKD instruments were being
refused at the broker minimum because 3 JPY buys no contract.

`prepare_order` now reports the USD value of the quote currency from
the venue's FX mid and the loop sizes in quote units of a USD budget;
no rate, no trade. Eighteenth measurement defect, and the first one
that ran in *both* directions — over the cap on the European indices,
under it everywhere east. Section 20's near-zero risk denominators
were partly this.

## 78. The backtest had the same currency blind spot

Companion to 77: `spot_backtest.py` sized in the quote currency too,
so the persisted ranking carried n = 0 for J225, AUDJPY and CHFJPY and
12 trades a year for HK50 — not because the signals were rare but
because 3 JPY buys no contract. Sized in USD they produce 16–21 trades
per 120 days, on par with the rest. The ranking file the selector
reads is therefore the first place where yen and Hong Kong instruments
will be judged on expectancy at all; section 70 says that expectancy
is random, and the selector's gates now get to see it.

## 79. Realised PnL was quote-currency arithmetic

The closure resolver prices (exit - fill) × size in the instrument's
quote currency, and the journal stored that as USD. Full stop-outs
show |PnL| / fill risk of 1.00–1.03, so it was never the broker's
EUR-account figure either. With sizing in USD (77) a HK50 stop would
have booked 21 HKD as -21 USD. The resolver now applies the venue
rate at close; USD instruments are unchanged, the European indices
and the yen and Hong Kong instruments are booked in dollars for the
first time, and an unknown rate books nothing rather than something
wrong. Nineteenth defect; the dashboard's "USD" was a mixed-currency
sum for roughly a tenth of the trades.

## 80. What the currency mix did to the historical sums

Sixty-two of 523 closed trades were on instruments quoted in EUR, GBP,
HKD, JPY, CAD, NZD, CHF or AUD and booked in those units as dollars.
At today's rates the all-time total moves from -241.66 to -250.82 USD,
the forward test since 24 August from -8.11 to -10.24. Small, and in
the flattering direction like every other defect here. The journal is
not rewritten; new closures are booked in USD (79), and these deltas
are the footnote for the older sums.

## 81. The notional cap is a risk limit, not a lever

At the venue floor a 3 USD risk needs 286 USD of notional, so the
250 USD cap trims planned risk to 2.2–2.5 USD on the pinned
instruments (`scripts/notional_cap_sweep.py`): 2.59 USD average at
250, 2.82 at 300, no further change at 400 because size increments
bind next. R-multiples are unaffected, dollars scale with risk taken,
and the sign of that scaling is the book's expectancy. Loosening a
hard exposure limit to lever a zero is not a trade this document will
make. The cap stays.

## 82. Seven active instruments sat outside the cluster cap

The same-direction cap counts only mapped instruments, and EU50,
COPPER, AUDJPY, CHFJPY, AUDNZD, EURAUD and GBPCAD were unmapped —
noticed because three shorts were open on HK50, CHFJPY and AUDJPY at
once. On a year of hourly returns (`scripts/cluster_correlations.py`)
EU50 sits at 0.93 with DE40, COPPER at 0.58 with the metals and the two
yen crosses at 0.60 with each other; the three remaining crosses are
below 0.4 against everything and stay singletons. Mapped accordingly,
with a test that every active instrument is either mapped or one of
those three. A guard gap, not a lever.

## 83. One position per instrument absorbs five signals in six

The three live trend strategies fire 11,325 signals a year on the ten
core instruments; 83 % arrive while a position is already open there,
and 80 % of those point the same way (`scripts/strategy_overlap.py`).
The live loop's one-position-per-instrument rule refuses them all, so
the book holds one bet per instrument regardless of how many channels
break. Nothing to change — but the per-strategy backtests in this
document each carry their own open-trade state, so their trade counts
add up to roughly six times what live can open. Expectancies stand;
throughput figures across strategies do not sum.

## 84. The daily-loss limit, and a trap in book-level replays

Replaying the 6 R daily limit on the pooled three-strategy backtest
book said that trades after a -3 R day lose 0.05–0.08 R at t of 2 to
5 on both samples. Replaying it on the merged one-position-per-
instrument timeline — what live can actually open — says +0.008 and
-0.032 R, a sign flip, and at 6 R the guard touches nine to thirteen
days a year for nothing (`scripts/daily_loss_replay.py`). The stacked
book counted the same instrument's loss three times on the same day,
so its bad days predicted themselves. The limit stays at 6 R, and any
future measurement of anything day-shaped uses the merged timeline.

## 85. The concurrent-position cap costs nothing measurable

On the merged timeline the cap of 8 refuses 6.4 % of entries whose
expectancy reads -0.026 and -0.034 R on the two samples, both inside
noise (`scripts/concurrent_cap_replay.py`). Tightening to 6 gains 38 R
on the recent year and loses 12 R on the older one — the familiar
reversal — and 4 halves the book. The cap stays at 8: it bounds
exposure and the trades it turns away are not the good ones.

## 86. Re-entering right after a stop-out re-buys the failed move

On the merged timeline an entry within six hours of a stop-out on the
same instrument reads -0.135 R against the rest on the recent year
(t = -2.11) and -0.064 R on the two years before (t = -1.50)
(`scripts/reentry_after_stop.py`); the live journal's 32 such
re-entries since July are flat. Not a two-sample pass by this
document's signal standard, but this is the stop-out cooldown the risk
rules already call for, and both samples say it costs nothing to have.
Built in at six hours, reading the journal's last stop-out per
instrument, fail-closed. One entry in eight on the core book falls
under it.

## 87. The entry cap is a circuit breaker and never fires

The 100-per-24h cap never binds on the merged timeline — peak issuance
is under twenty — and tighter caps (`scripts/entry_cap_replay.py`)
refuse entries that are not significantly worse than zero on either
sample. It stays, as the breaker it was written to be. With this every
entry-side guard has been measured at the live configuration: none of
them is a lever, and none of them is costing the book anything it
would want back.

## 88. The cost filter's stop widening happens only off the audited tape

Against the audited spread table no trade on any active instrument
exceeds the 10 % cost share at the 2-ATR stop, on either sample
(`scripts/cost_widening_check.py`), so the live widening rule fires
only when the quote is wider than the audit — the HK50 entry at 23:55
UTC widened 265 → 300 HKD from 11.3 % to 10.0 %. The backtest skips
what the live loop widens, and neither the bars nor the journal record
the spread that decided it. Not measurable today; recorded as the one
live/backtest divergence in this document that no history can settle
without a journal flag.

## 89. Positions carried through holidays come back fine

Twenty positions held through a pause of more than sixty hours since
July closed at +0.118 R, against +0.016 R for ordinary stale exits and
-0.196 R for stops and targets; the shutdown closes sum to +5.32 USD.
The holiday guard idles the whole book on 18 weekdays a year — 6.9 % of
weekdays, crypto and open markets included — for no measured cost and
no measured gain. It stays as written.

## 90. Stops fill where they sit; entries do not

Across 235 stop-outs the fill sits 0.027 R beyond the stored stop on
average and 0.003 R at the median — the venue executes stops where
they are placed, and the tail is the weekend gap section 76 now
charges. Targets fill at the target. The entry is the leg that costs:
202 fills since July sit 0.128 R behind the signal-bar close the
simulator enters at. Half of that is the spread the simulator does
charge; the other half is the price moving on in the breakout's
direction between the close and the order, which no section here has
modelled. Section 3 measured the same thing in dollars. Next: entry at
the next open versus a limit at the close, both samples.

## 91. Entry timing: the gap is below the bar

Entering at the next bar's open instead of the signal close costs
0.009–0.015 R on both samples (`scripts/entry_timing.py`); the live
fill gap is 0.128 R. So roughly a tenth of it is the hour the
simulator skips and the rest is spread and latency at the moment of
the order. A limit at the signal close fills 97 % of the time and
earns nothing back — the fills that come to the limit are the moves
that already ended. Market entry stays; the gap is recorded as the
book's largest unmodelled cost, and one that hourly data cannot
measure further.

## 92. Live was trading the forming bar

Orders left a median 31 minutes before the signal bar closed. The
venue's history ends with the current candle, and the loop treated it
as the just-closed bar, so live entered on intrabar crossings of the
channel while every backtest here enters on the confirmed close.
Measured on both samples (`scripts/intrabar_vs_close.py`), the intrabar
entry is worse by -0.052 R (t = -2.98) and -0.023 R (t = -1.88), with
yearly sums of -164 R against +149 R and -478 R against -148 R — the
extra fifth of entries are the breakouts the close takes back.

This is the largest live/backtest divergence in the document, larger
than the leash (17) or the trail (27), and it ran in the direction
that made live worse than measured rather than better. The loop now
discards the forming bar (`_completed_bars`) and enters on the close
the backtests price. Twentieth defect; the first whose correction
should raise live expectancy toward the measured figure rather than
lower the measured figure toward live.

## 93. Keltner is the best of three randoms

On the close-confirmed simulator `keltner_breakout` edges the other
two trend strategies on both samples (+0.035 and +0.005 R) and beats
its random control at t = 1.87 and 1.17 — after the router at 0.08
and 0.51. It stays out of the nightly rotation: the margin is inside
the noise on both samples and the scheduler's reason for excluding it,
that it would take entries from instruments the book already holds,
is exactly what run 13 measured. Its persisted backtest is from July
and stale.

## 94. The corrected simulator reorders the ranking without moving the mean

Re-persisting `donchian_breakout` on the corrected simulator leaves
the pooled expectancy at -0.0399 to four decimals and replaces six of
the ten top instruments: the yen crosses, J225 and WHEAT size for the
first time and five yen crosses enter the top ten. That is section 78
arriving in the selector, not an edge — AUDJPY and CHFJPY measured
random in 70 and the others are unmeasured. The nightly run will do
the same for the other strategies; the next active list should be read
with that in mind.

## 95. The yen crosses are one cluster

EURJPY, GBPJPY and CADJPY correlate 0.67–0.84 with each other and with
AUDJPY and CHFJPY on a year of hourly returns and below 0.25 with
anything else (`scripts/yen_cluster_correlations.py`); they join the
`jpy_crosses` cluster before the corrected ranking (94) puts them in
the active list. USDJPY reads 0.50 with the crosses and 0.46 with the
USD majors and stays where it is. A guard kept true ahead of the
instruments it will need to count.

## 96. The yen instruments, measured before they trade

EURJPY, GBPJPY, CADJPY, J225 and USDJPY against random on both
samples (`scripts/yen_instruments.py`): +0.012 R at t = 0.73 and 1.06
as a group, -0.014 and -0.023 R after the router, no instrument
significantly negative twice. Random, like the rest of the book, and
now counted by the cluster cap (95) and sized in dollars (77) before
the selector puts them in play. Nothing to block, nothing to promote.

## 97. Instrument rankings do not persist year to year

Across three disjoint yearly windows the rank correlation of
instrument expectancy is +0.02, +0.10 and +0.32 on all signals — the
first transition puts last year's winners below its losers — and
+0.51, +0.32, +0.62 on the router-passed path with a few hundred
trades per instrument (`scripts/all_instruments_check.py`). The
selector ranks by backtest as if the ranking meant something; on the
whole-signal basis it does not, and on the router path it is the size
of effect this document has already watched dissolve. Nothing is
added. The names that keep landing at the bottom (UK100, FR40, AU200)
are the structural cases of section 70.

## 98. The live-expectancy veto is a coin toss on eight trades

Replaying the selector's retire rule (mean R ≤ -0.15 over ≥ 8 trades)
per combo on both samples (`scripts/veto_replay.py`): the retired
combos go on to return +0.061 R on the recent year (t = +2.87) and
-0.034 R on the two years before (t = -2.82). Two significant readings
in opposite directions from the same rule is section 34's finding in
its purest form — eight trades of a zero-expectancy process rank
noise. The rule stays as written, because a retire-only rule cannot
promote a loser, but it should not be read as finding anything.

## 99. The strategy veto would have switched the book off on any sample

Replayed per strategy (`scripts/strategy_veto_replay.py`), the rule
that retires a strategy at mean R ≤ -0.10 over 25 trades retires all
three trend strategies on both samples inside their first 25–140
trades: -160 R forgone on the recent year, +131 R saved on the two
before. A running mean over a few dozen trades of a zero-expectancy
process visits -0.10 as a matter of course, so the rule decides when
the book stops, not which strategy is bad. Live it has never touched a
rotation strategy and its keltner entry is forming-bar-era evidence.
It stays, as a retire-only backstop, and is not read as a finding.

## 100. The trade-count floor binds only on momentum

Every trend-strategy combo on the measured instruments clears the
selector's floor on its own — 59 router-passed trades a year at the
median, none under 30 (`scripts/combo_counts.py`) — so the nightly
floor of 10 exists for momentum, which the router lets through once
in seven signals and which is ranked first on the indices on 10–21
trades. Those rows have traded twice since July. Raising the floor
would delete them without changing the book; the floor stays, and the
momentum ranks are read as section 40 says samples that size must be.

## 101. The wall-clock leash is a small, conservative divergence

Live holds 24 hours, the backtests 24 bars; where sessions break the
live leash is the shorter one. Simulated (`scripts/wallclock_leash.py`)
the wall-clock leash trades 5 % more often, times out 15 % more often
and gives back 0.002–0.008 R per trade on the two samples, t of -0.2
and -0.4. Same sign twice, inside the noise, conservative. Left as is;
the calendar machinery to align them is not worth 0.005 R.

## 102. The nightly refresh runs whenever the bot does

Log markers show the selector fired on 49 of 50 days from 8 July to
26 August, on none of the nine reboot-outage days (49) and not on the
Labor Day the holiday guard idled the loop. The ranking is stale only
when the bot is, plus holidays, where the guard also idles a backtest
that needs no market. Given 97, a two-day-old list costs nothing
measurable; noted, not changed.

## 103. Which guards actually fire

Since July the journal refused 202 signals for the router, 20 for
broker errors (18 of them on crypto now cost-blocked, 2 OIL_CRUDE
orders in its daily break), 15 duplicates, 3 below the stop floor,
2 for the concurrent cap — and none for the cluster cap, the daily
loss limit or, being new, the cooldown. The book is throttled by the
router and the one-position rule; everything else is a backstop that
has not been reached. Nothing to replay, nothing to change.

## 104. The stop floor was a GOLD block

GOLD's venue minimum is 0.1 % of price, every other instrument's 1 %.
The live loop widened stops only to what the venue demanded, so GOLD's
2-ATR stop (about 0.9 %) reached the 1 % cost floor of 2026-08-24 and
was refused — three refusals since, thirteen accepted trades before at
0.3–0.5 % stops, none after. The backtests, with GOLD missing from
the minimum-distance cache, widened it to 1.05 % and measured it there:
+0.130 R (t = 2.64) and +0.073 R (t = 2.70) on the two samples
(`scripts/gold_stop_treatment.py`), against +0.056 and -0.060 R for
what the floor let through. The loop now widens every stop to the
shared 1.05 % minimum before the floor, which makes live GOLD the
instrument the measurements describe — the one instrument in the book
that beats random on both samples (66). Twenty-first defect, and the
one with the clearest expected effect on the daily figure.

## 105. The minimum-distance cache carries no information

All 35 instruments checked against the broker's dealing rules carry a
1 % venue minimum — 1.05 % with the buffer, the backtest's default —
except GOLD at 0.1 %, which section 104 widened live to the same
1.05 %. The 14 cached entries all say 1 %. Live and backtest now place
the same minimum stop everywhere; the cache is redundant and harmless.

## 106. GOLD's spread is right; its fills were not

The audited spread for GOLD (0.0056 % a side) matches the venue's
quote to the fourth decimal, as it does for SILVER, BTCUSD and US500.
The twelve live GOLD fills, however, sit a median 0.028 % from the mid
they were sized on — five half-spreads, about 5 % of R at the widened
stop, all from the forming-bar era. Execution, not the table. The
first close-confirmed GOLD trades will show whether that cost survives
entering on a closed bar; until then the table stands.

## 107. The spread table is a daytime table

Before the European open the venue quotes FR40 at thirteen times its
audited spread, HK50 at six, UK100 at three, DE40 at nearly three; half
of every index strategy's signals fire in those hours. Charging the
off-hours spread where it applies (`scripts/index_offhours_costs.py`,
one snapshot) doubles the cost per R on the five European and Asian
indices and takes their expectancy from -0.048 / -0.039 R to -0.057 /
-0.052 R on the two samples. Live sees the real quote and widens or
refuses; the backtest that ranks them does not. The correction needs a
spread-by-hour table the project does not have yet; recorded as the
open cost item, alongside 91's sub-bar execution.

## 108. Collecting the spread-by-hour table

The heartbeat now writes the venue's bid and offer for every active
instrument once an hour to `data/spread_samples.jsonl`, from the
session the loop already holds. In a week that is the table section
107 lacked; until then the simulator keeps charging the daytime spread
and the live cost filter keeps charging the real one.

## 109. Shorts on the commodities lose on both samples; index shorts only on the bull year

Section 4 read direction off the early live journal (long -0.074 R,
short -0.163 R) and called it no skew. It was never measured on the
simulator at the current configuration. `scripts/direction_split.py`
replays the three live 1h trend strategies on the router-passed path at
the 2-ATR stop, venue minimum, live widening rule and gap-aware stop
booking over all 26 tradeable instruments, and splits every trade by
direction within its asset class. Preregistered rule: a class's short
(or long) side is blocked only if it is significantly negative at
t < -2 on both disjoint samples and the long-short difference holds at
|t| > 2 with the same sign on both.

| class, side | last 365 d: n / E[R] / t | prior 730 d: n / E[R] / t |
|---|---|---|
| all longs | 2,511 / -0.0005 / -0.03 | 5,212 / +0.0124 / +1.10 |
| all shorts | 2,467 / **-0.0910** / **-5.10** | 4,865 / -0.0063 / -0.51 |
| index longs | 810 / -0.0298 / -1.08 | 1,773 / +0.0633 / +3.48 |
| index shorts | 852 / **-0.1992** / **-6.26** | 1,632 / +0.0174 / +0.77 |
| commodity longs | 559 / +0.0348 / +0.76 | 1,022 / +0.0104 / +0.33 |
| **commodity shorts** | 491 / **-0.1604** / **-3.32** | 1,004 / **-0.1015** / **-3.17** |
| commodity long − short | +0.195 / t +2.92 | +0.112 / t +2.49 |
| fx shorts | 784 / -0.0271 / -1.58 | 1,639 / +0.0026 / +0.21 |
| crypto shorts | 340 / +0.1332 / +2.19 | 590 / +0.0654 / +1.36 |

Index shorts are the largest single reading in this log on the recent
year and dissolve entirely on the two years before it: that was the
bull market, not a lever. Commodity shorts are the first split that
holds on both samples under the preregistered rule. Per instrument,
SILVER shorts carry most of it (-0.33 R at t = -3.5, -0.26 R at
t = -3.9) with SILVER longs flat (+0.10, -0.02); the oils' shorts are
significantly negative on the recent year (t = -2.0, -2.2) and mildly
so on the older one; GOLD and COPPER shorts are flat. The live journal
reads the same way: 29 commodity shorts returned -20.04 USD, 58 longs
-6.86 USD. Removing the class's shorts would have added about 79 R over
the last year and 51 R a year over the two before on the simulator,
before the one-position rule and the caps merge signals.

The short side of the five commodities is refused for entries in the
live loop, the shared simulator and the walk-forward stability check;
the long side and the instruments stay active. Section 4's live reading
was not wrong, it was pooled: the skew lives in one class.

## 110. Late breakouts are not worse; weak ones are, but not reliably

The entry features of the breakout bar had never been measured: every
signal was taken as it fired. The one preregistered feature here is
the extension of the signal close from the 20-bar EMA in ATR(14)
units, signed by direction, on the idea that a close already far from
its mean is a late entry that mean-reverts into the stop.
`scripts/late_breakout_filter.py` replays the three live 1h trend
strategies on the router-passed path at the 2-ATR stop, venue minimum,
live widening rule, gap-aware stop booking and the commodity short
block over all 26 tradeable instruments, and buckets every trade by
that extension. The quartile edges were fixed on the recent year
(1.97 / 2.35 / 2.92 ATR) and applied unchanged to the two years
before. Preregistered rule: a bucket is blocked only if it is
significantly negative at t < -2 on both disjoint samples and its
difference to the rest holds at |t| > 2 with the same sign on both.

| extension bucket | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| below 1.97 ATR (weak) | 1,126 / **-0.080** / **-3.44** / -0.062 / **-2.28** | 2,237 / -0.009 / -0.54 / -0.033 / -1.70 |
| 1.97–2.35 | 1,125 / -0.002 / -0.06 / +0.042 / +1.52 | 2,316 / -0.003 / -0.16 / -0.025 / -1.27 |
| 2.35–2.92 | 1,126 / -0.029 / -1.19 / +0.006 / +0.20 | 2,273 / +0.046 / +2.68 / +0.041 / +2.05 |
| above 2.92 ATR (late) | 1,126 / -0.022 / -0.86 / +0.014 / +0.49 | 2,287 / +0.028 / +1.56 / +0.016 / +0.80 |

The late quarter is not worse on either sample: the hypothesis is
dead as stated. What the recent year shows instead is the opposite
end — closes that clear the channel while still within two ATR of
their mean read -0.080 R at t = -3.44, the familiar marginal break
that reverses into the stop, and the same bucket is the worst of the
four on the older sample too. But there it is -0.009 R at t = -0.54
and the difference to the rest is t = -1.70: the sign persists, the
size does not, and the preregistered bar is missed on both counts.
Per strategy the shape repeats without reaching significance
anywhere on the older sample. This is the buffered-breakout idea of
`donchian_atr` (rejected 2026-07-08) measured as a feature of the live
signals rather than as a new strategy, and it lands where that did.
No filter is built in.

## 111. Wide breakout bars win on the recent year only

Section 110 measured the close's extension from its mean; the other
feature of the signal bar is the bar itself, its high-low range in
ATR(14). `scripts/breakout_bar_range.py` replays the three live 1h
trend strategies on the router-passed path at the 2-ATR stop, venue
minimum, live widening rule, gap-aware stop booking and the commodity
short block over all 26 tradeable instruments, and buckets every trade
by that range. Quartile edges were fixed on the recent year (1.00 /
1.45 / 2.09 ATR) and applied unchanged to the two years before.
Preregistered rule as in section 110: a bucket is blocked only if it is
significantly negative at t < -2 on both disjoint samples and its
difference to the rest holds at |t| > 2 with the same sign on both.

| signal bar range | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| below 1.00 ATR | 1,126 / -0.054 / -2.39 / -0.028 / -1.05 | 2,089 / -0.005 / -0.31 / -0.027 / -1.38 |
| 1.00–1.45 | 1,125 / -0.059 / -2.45 / -0.034 / -1.23 | 2,237 / -0.004 / -0.23 / -0.026 / -1.31 |
| 1.45–2.09 | 1,125 / -0.061 / -2.51 / -0.037 / -1.32 | 2,438 / **+0.034** / **+2.09** / +0.026 / +1.35 |
| above 2.09 ATR | 1,127 / **+0.042** / +1.58 / **+0.100** / **+3.36** | 2,351 / +0.032 / +1.81 / +0.023 / +1.12 |

The recent year reads exactly as the momentum-bar hypothesis predicts:
the widest quarter of signal bars is the only positive bucket, beats
the rest by 0.100 R at t = +3.36, and every narrower quarter loses
about 0.06 R at t ≈ -2.4, in all three strategies alike. The older
sample keeps the sign of the wide quarter (+0.032 R) but not the size
(t = 1.81, difference t = +1.12), no narrower bucket is negative there,
and the 1.45–2.09 quarter that any minimum-range filter would remove is
the best of the four on that sample at +0.034 R, t = +2.09. This is the
pattern of section 109's index shorts — a strong reading confined to
the recent year that the independent sample does not carry — and the
preregistered bar is missed on both counts there. No filter is built
in; the signal bar's own features are now measured on both counts
(extension, range) and neither is a lever.

## 112. A fading ADX at the signal bar is worse than the rest, twice — and still not a block

Sections 110 and 111 measured the signal bar's price features; the
router's own input had never been read as a direction. The router
requires ADX(14) >= 30 at the signal, which says how strong the trend
is, not whether it is building or fading. `scripts/adx_slope_filter.py`
replays the three live 1h trend strategies on the router-passed path at
the 2-ATR stop, venue minimum, live widening rule, gap-aware stop
booking and the commodity short block over all 26 tradeable
instruments, and buckets every trade by the change of ADX over the
three bars before the signal. Quartile edges were fixed on the recent
year (+0.22 / +2.58 / +4.43 ADX points) and applied unchanged to the
two years before. Preregistered rule as in sections 110 and 111: a
bucket is blocked only if it is significantly negative at t < -2 on
both disjoint samples and its difference to the rest holds at |t| > 2
with the same sign on both.

| ADX change over 3 bars | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| below +0.22 (fading) | 1,126 / **-0.081** / **-3.34** / -0.065 / **-2.33** | 2,519 / -0.017 / -1.07 / -0.042 / **-2.19** |
| +0.22 to +2.58 | 1,125 / -0.004 / -0.17 / +0.037 / +1.31 | 2,432 / **+0.035** / **+2.13** / +0.030 / +1.55 |
| +2.58 to +4.43 | 1,126 / -0.037 / -1.54 / -0.007 / -0.25 | 2,057 / +0.009 / +0.51 / -0.005 / -0.25 |
| above +4.43 | 1,127 / -0.006 / -0.23 / +0.035 / +1.24 | 2,093 / +0.028 / +1.55 / +0.020 / +0.95 |
| falling (< 0) vs rising | 1,038 / -0.068 / -2.68 / -0.047 / -1.62 | 2,345 / -0.017 / -1.02 / -0.041 / -2.08 |

Three quarters of router-passed signals fire while ADX is still rising;
the quarter that fires as it flattens or turns is the worst bucket on
both samples, and its gap to the rest holds at t = -2.33 and t = -2.19
with the same sign. That is the first signal-bar feature in this log
whose difference survived the independent sample. What it does not do
is lose on its own there: -0.017 R at t = -1.07 against the required
t < -2, so the first half of the preregistered rule is missed. Per
strategy the shape is the same everywhere and significant nowhere on
the older sample; on the recent year it is carried by turtle_breakout
(-0.155 R, t = -2.63 in the fading quarter, n = 174). The binary split
(falling vs rising) reads t = -1.62 then -2.08 for the difference and
misses on the recent year.

Read as a filter, the arithmetic is one-directional on both samples —
dropping the fading quarter lifts the remaining book from -0.032 to
-0.016 R on the recent year and from +0.013 to +0.025 R on the older
one — but the older sample's gain comes from removing trades that lose
0.017 R each, not significantly different from zero, and the rule was
written before the data were seen. No filter is built in. ADX at entry
is already journalled (`entry_adx`), so a slope reading can be added to
the forward test without changing what trades.

## 113. How old the broken level is does not matter

Sections 110 to 112 read the signal bar itself; the level it clears had
never been read. A donchian or turtle entry breaks the extreme of its
channel window, and that extreme was either printed a bar or two ago
(a trend already running, the channel rising under the price) or near
the far end of the window (a base the breakout resolves). The classic
claim is that the second kind carries further. `scripts/breakout_base_age.py`
replays the three live 1h trend strategies on the router-passed path at
the 2-ATR stop, venue minimum, live widening rule, gap-aware stop
booking and the commodity short block over all 26 tradeable
instruments, and buckets every trade by the age of the channel extreme
it broke (argmax of the highs or argmin of the lows over the strategy's
own window: 20 bars for donchian and keltner, 55 for turtle), expressed
as a fraction of that window. Quartile edges were fixed on the recent
year (0.050 / 0.145 / 0.350) and applied unchanged to the two years
before. Preregistered rule as in sections 110 to 112: a bucket is
blocked only if significantly negative at t < -2 on both disjoint
samples and its difference to the rest holds at |t| > 2 with the same
sign on both.

| age of the broken level / window | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| below 0.05 (fresh) | 606 / -0.041 / -1.22 / -0.011 / -0.30 | 1,178 / +0.011 / +0.47 / -0.006 / -0.22 |
| 0.05–0.145 | 1,618 / -0.028 / -1.41 / +0.005 / +0.21 | 3,234 / +0.010 / +0.73 / -0.008 / -0.48 |
| 0.145–0.35 | 1,145 / -0.024 / -0.98 / +0.011 / +0.38 | 2,301 / +0.031 / +1.78 / +0.020 / +1.01 |
| above 0.35 (base) | 1,125 / -0.040 / -1.57 / -0.011 / -0.37 | 2,416 / +0.011 / +0.65 / -0.006 / -0.32 |
| age > 0.5 (deep base) | 718 / -0.044 / -1.41 / -0.015 / -0.44 | 1,589 / +0.017 / +0.79 / +0.001 / +0.04 |

Nothing moves. On the recent year every bucket sits within 0.011 R of
the rest and no difference reaches |t| = 0.6; on the older sample the
widest gap is the third quarter at +0.020 R, t = +1.01. The half of the
signals that break a level set in the last 15 % of the window and the
quarter that break a base older than half the window read the same as
everything else on both samples. Per strategy the shape is noise as
well: turtle's second bucket is +0.073 R at t = +2.55 on the older
sample and -0.025 R on the recent year, the sign flip of a chance
reading. The median age is 0.15 of the window, three bars for the 20-bar
channel: most router-passed breakouts clear a level printed within the
previous few bars, which is what an ADX >= 30 gate selects for. The
first half of the preregistered rule is missed already on the recent
year, so the independent sample could not have rescued it. No filter is
built in; the level's age joins extension, range and ADX slope as a
measured non-feature of the entry.

## 114. A trending 4h chart is where the 1h breakouts lose — twice in sign, once in size

The router reads trend strength from the 1h bars only. Whether the
4h chart is trending at the moment a 1h breakout fires had never been
read; the hypothesis going in was the usual one, that a breakout with
the higher timeframe already trending has the wind behind it.
`scripts/htf_adx_gate.py` replays the three live 1h trend strategies on
the router-passed path at the 2-ATR stop, venue minimum, live widening
rule, gap-aware stop booking and the commodity short block over all 26
tradeable instruments, and buckets every trade by ADX(14) of the 4h
bars (resampled from the same 1h history, read at the last 4h bar
completed before the signal, so nothing after the signal is seen).
Quartile edges were fixed on the recent year (19.8 / 25.5 / 33.2) and
applied unchanged to the two years before. Preregistered rule as in
sections 110 to 113: a bucket is blocked only if significantly negative
at t < -2 on both disjoint samples and its difference to the rest holds
at |t| > 2 with the same sign on both. After the recent year had been
seen and before the older sample ran, the median split (4h ADX >= 25.5)
was preregistered as a second candidate under the same bar.

| 4h ADX at the 1h signal | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| below 19.8 | 1,106 / +0.019 / +0.80 / +0.063 / +2.22 | 2,140 / +0.051 / +2.94 / +0.047 / +2.37 |
| 19.8–25.5 | 1,107 / +0.005 / +0.20 / +0.043 / +1.54 | 2,112 / +0.055 / +3.11 / +0.053 / +2.60 |
| 25.5–33.2 | 1,105 / **-0.073** / **-3.01** / -0.061 / **-2.15** | 2,176 / +0.031 / +1.78 / +0.021 / +1.05 |
| above 33.2 | 1,108 / **-0.062** / **-2.43** / -0.046 / -1.56 | 2,637 / **-0.060** / **-3.73** / -0.105 / **-5.57** |
| upper half (>= 25.5) | 2,213 / **-0.068** / **-3.83** / -0.080 / **-3.23** | 4,813 / -0.019 / -1.60 / -0.072 / **-4.21** |
| book without the upper half | 2,213 / +0.012 | 4,252 / +0.053 |

The hypothesis is reversed and the reversal is the most consistent
reading this log has recorded. On both samples the lower half — 1h
breakouts that fire while the 4h chart is not yet trending — is the
profitable side, and the difference to the upper half holds at
t = -3.23 and t = -4.21. The widest quarter (4h ADX above 33) is
significantly negative on both samples on its own, -0.062 R and
-0.060 R, and it is the worst bucket in all three strategies on the
older sample. Read as an early-trend entry: the 1h channel breaks
before the 4h ADX has built, and by the time the 4h chart reads as a
strong trend the 1h breakouts are late.

The preregistered bar is still missed, twice, and by the same clause
each time. The quartile that qualified on the recent year (25.5–33.2)
turns positive on the older sample, +0.031 R at t = +1.78 — the losing
band moved up a quarter between the samples. The median split holds its
difference on both samples but the upper half is not significantly
negative on its own on the older one: -0.019 R at t = -1.60 against the
required t < -2. The top quarter would pass the first clause on both
samples and fails the second on the recent year, where its difference
to the rest is t = -1.56; it was not preregistered. Section 112's ADX
slope failed on exactly the same clause, and the two features are
related (a high 4h ADX is a trend that has been building for a day or
more), so this is probably the same thing seen from a second angle
rather than an independent confirmation.

No filter is built in. Measured as if it were: dropping the upper half
lifts the book from -0.028 to +0.012 R on the recent year and from
+0.015 to +0.053 R on the older one, at half the entries. That is the
size of effect the target requires and the first time both samples
agree on the sign of a filter's gain, but the rule that protects this
log from its own bull-year readings was written before the data and it
says not yet. The 4h ADX at entry can be reconstructed for every
journalled trade from its `bar_time` and the 1h history, so the
forward test needs no change to what trades or what is journalled.

## 115. The 4h-ADX split does not travel: flat on the excluded instruments, reversed in the journal

Section 114 left the median split of the 4h ADX (>= 25.5 at the 1h
signal) as the strongest candidate on record: the same sign on both
walk-forward samples at t = -3.23 and -4.21 for the difference, the
block bar missed only on the upper half's own significance. A split
found on 26 instruments and then re-read on the same 26 is still a
selection, so `scripts/htf_adx_second_look.py` reads it, fixed at
25.532, on two samples no earlier run touched. (A) The instruments the
live book excludes — the eleven cost-blocked crypto and metal
instruments, CORN, NATURALGAS and AU200; APTUSD returns no history, so
thirteen — over three years, router-passed, 2-ATR stop, venue minimum
and live widening rule, costs charged at their actual spread but
without the 10 % ceiling skip so that trades exist. (B) The live
journal of the 1h trend strategies, 231 closed Capital.com trades from
May to September 2026 with realised R at the actual fill, the 4h ADX at
each signal bar reconstructed from the 1h history. Preregistered before
either ran: the ceiling would be built in only if the upper half were
worse than the lower on (A) at t < -2 and the journal agreed in sign.

| sample | n | lower half E[R] | upper half E[R] | upper − lower | t |
|---|---|---|---|---|---|
| (A) excluded instruments, net | 9,257 | -0.131 | -0.125 | +0.006 | +0.33 |
| (A) excluded instruments, gross of cost | 9,257 | +0.010 | +0.002 | -0.008 | -0.42 |
| (B) journal, all closed | 231 | -0.083 | +0.013 | +0.096 | +0.72 |
| (B) journal, since 2026-08-24 | 27 | -0.138 | +0.073 | +0.211 | +0.52 |
| for reference, section 114: last 365 d | 4,426 | +0.012 | -0.068 | -0.080 | -3.23 |
| for reference, section 114: prior 730 d | 9,065 | +0.053 | -0.019 | -0.072 | -4.21 |

Nothing of section 114 survives the move. On the excluded instruments
the two halves are the same to within 0.006 R over nine thousand
trades, net and gross, in all three strategies. In the journal the
sign is reversed: the trades taken while the 4h chart was trending are
the better half, +0.013 R against -0.083 R, in donchian, turtle and
keltner alike and in the forward window since the filters went live —
the journal is too small for the t to mean much (0.72), but a real
effect of the size section 114 measured would have read the other way
with better than even odds, and it reads the other way in every slice.
The excluded set is mostly altcoins and thin commodities, structurally
unlike the live book, and the journal was traded at the 1-ATR stop
until 2026-09-07 rather than the 2-ATR stop of the replay; both are
reasons the readings could differ, neither is a reason to believe the
replay over them. A feature that holds on the instruments it was found
on, at two time windows, and on nothing else is the signature of a
property of those instruments over those years, not of the signal.

No filter is built in. The 4h ADX is struck from the forward-test
list; the ADX slope of section 112 stays as the only candidate there,
and it shares the same weakness (never read outside the 26). The
methodological point is worth keeping: for this book the independent
sample in time (sections 110 to 114) has been necessary but not
sufficient — a candidate that survives it has to be read on
instruments and on trades that were not part of the selection before
it is believed.

## 116. Peer confirmation flips sign between the samples

The entry features of a single instrument are measured out (sections
110 to 115); the first cross-instrument reading asks whether a breakout
that is part of a move across its class carries further than a lone
break. `scripts/peer_breakout_breadth.py` replays the three live 1h
trend strategies on the router-passed path at the 2-ATR stop, venue
minimum, live widening rule, gap-aware stop booking and the commodity
short block over all 26 tradeable instruments, and buckets every trade
by the number of *other* instruments of the same class (fx / index /
crypto / commodity) that produced a router-passed signal of any live
strategy in the same direction during the 24 bars up to the signal,
counted whether or not the simulator could take them. The buckets were
fixed a priori at 0, 1, 2 and >= 3 peers, so no edge was fitted on the
recent year. Preregistered rule as in sections 110 to 114: a bucket is
blocked only if significantly negative at t < -2 on both disjoint
samples and its difference to the rest holds at |t| > 2 with the same
sign on both.

| same-direction peers in 24 bars | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| 0 (lone break) | 922 / +0.022 / +0.77 / +0.069 / +2.19 | 1,974 / +0.034 / +1.71 / +0.025 / +1.12 |
| 1 | 1,210 / +0.027 / +1.04 / +0.082 / +2.80 | 2,503 / -0.002 / -0.10 / -0.023 / -1.14 |
| 2 | 743 / **-0.073** / **-2.64** / -0.048 / -1.55 | 1,553 / **-0.055** / **-3.04** / -0.084 / **-4.11** |
| >= 3 (crowded) | 1,619 / **-0.091** / **-4.78** / -0.090 / **-3.65** | 3,105 / **+0.051** / **+3.74** / +0.054 / **+3.12** |

On the recent year the reading is the reverse of the hypothesis and
strong: the crowded quarter loses 0.091 R at t = -4.78 and the lone and
single-peer breaks are the only positive buckets, in all three
strategies. On the older sample the same bucket is the *best* one,
+0.051 R at t = +3.74, and the difference to the rest holds at
t = +3.12 with the opposite sign. This is a clean sign flip on a bucket
that qualified under the rule on the first sample, the sharpest yet in
this log, and the class tables say why: the crowded bucket is 60 %
index trades, and a class-wide index breakout in the recent year was
mostly a short into a bull-market dip (section 109's index shorts,
-0.199 R), while in the two years before it was a long in a rising
market. Peer confirmation is reading the index regime of the sample,
not the signal. The two-peer bucket is negative on both samples, but a
feature that is bad at two peers and good at three is not a mechanism,
and it was not preregistered as a split.

No filter is built in. Peer confirmation joins the entry features as
measured and dead; the cross-sectional information in this book has now
been read as relative strength (sections 31, 33), as BTC leadership
(section 7) and as class breadth, and none of the three carries.

## 117. A passive limit at the signal close does not recover its half-spread

Runs 4 and 21 (sections 53 and the entry-timing note) measured limit
entries and found nothing, but both charged the full round-trip spread
on the passive fill and counted a fill when the mid price touched the
limit. A resting buy order at price P fills when the *ask* reaches P,
i.e. once the mid has moved a half-spread below the close, and the
position then pays the spread once, on the exit. That saving — half
the round trip, 0.02–0.05 R on this book — had never been modelled, and
it is the size of effect the book's expectancy turns on.
`scripts/passive_limit_entry.py` replays the three live 1h trend
strategies on the router-passed path at the 2-ATR stop, venue minimum,
live widening rule, gap-aware stop booking and the commodity short
block over all 26 tradeable instruments, and compares the market entry
at the signal close (cost 2h) with a limit at the signal close valid
one or three bars: the fill requires the mid to reach close − h (long)
or close + h (short), a gap through the limit fills at the open's ask
or bid, the cost is h, the 24-bar hold counts from the fill bar, and a
stop hit inside the fill bar is booked as a loss because the order of
events inside the bar is unknown — the limit is measured
pessimistically on that point and optimistically on none. Preregistered
rule: the limit is built in only if the same validity beats the market
entry on both disjoint samples in E[R] per trade at t > 2 and in total
R per sample, which charges it for the signals it never fills.

| entry, last 365 d | signals | filled | fill % | E[R] | t | Σ R | R per signal | vs market / t |
|---|---|---|---|---|---|---|---|---|
| market at close | 4,560 | 4,499 | 98.7 | -0.033 | -2.71 | -148.9 | -0.033 | — |
| limit, 1 bar | 4,688 | 4,353 | 92.9 | -0.039 | -3.16 | -169.0 | -0.036 | -0.006 / -0.33 |
| limit, 3 bars | 4,635 | 4,411 | 95.2 | -0.039 | -3.22 | -173.7 | -0.038 | -0.006 / -0.36 |

The limit fills nine times in ten and still comes out behind on every
count, in all three strategies alike (differences of -0.004 to -0.009
R, |t| < 0.35). The saving is real and it is paid twice over: the one
signal in fourteen that never comes back to its close is the one that
ran, and the filled trade starts a bar later and half a spread closer to
its stop. Per instrument the sign is mixed, eleven better and fifteen
worse, with nothing outside noise. The rule is conjunctive and the
first sample fails it by a wide margin, so the older sample was not
run; the market entry's figures there are on record in sections 114
to 116 (+0.015 R over 9,065 trades) and the limit could not have
passed on one sample alone.

No change. This closes the entry-cost side as far as hourly bars can
see it: market at the confirmed close is the entry, the 0.128 R live
fill gap of section 100 remains the open item, and it lives below the
bar where a limit order cannot reach it either. A limit at a better
price than the close is section 53's pullback entry, already dead.

## 118. The live fill gap is gone: entry slippage is one half-spread

Section 100 measured live fills 0.128 R behind the signal close, and
run 22 traced most of it to orders leaving on the forming bar. Whether
the gap survived that fix had not been read. From the journal
(read-only): fill price against the journalled signal price, in units
of the trade's stop distance, positive when the fill is worse for the
trade, for every accepted Capital.com entry since the filters went live
on 2026-08-24.

| entries | n | mean slip | median | sd | s.e. |
|---|---|---|---|---|---|
| 2026-08-24 to the forming-bar fix (2026-09-08 04:00 UTC) | 28 | +0.017 R | +0.011 R | 0.038 | 0.007 |
| after the fix | 5 | +0.004 R | -0.001 R | 0.045 | 0.020 |

The orders now leave two to forty seconds after the bar closes and the
slippage that remains is the half-spread the simulator already charges
on entry, +0.017 R before the fix and within noise of zero after it.
The 0.128 R of section 100 was the provisional close, not execution.
Slippage is nearly deterministic per trade, so 33 entries settle it
to ±0.007 R; the entry-cost side is closed on the live book as well as
in the simulator (section 117).

## 119. A timed-out instrument re-breaks as well as any other

Run 16 built in a cooldown after a stop-out; the other exit that leaves
an instrument's regime in doubt is the timeout, a break that went
nowhere in 24 bars. `scripts/reentry_after_timeout.py` replays the
merged one-position-per-instrument timeline of the three live 1h trend
strategies, router-passed, commodity short block, 2-ATR stop, venue
minimum, live widening rule, gap-aware stop booking and the live 6-hour
stop-out cooldown, over all 26 tradeable instruments, and classes every
entry by the previous exit on the instrument and the hours since it.
Preregistered: a re-entry within 6 or 24 hours of a timeout is blocked
only if negative at t < -2 on both disjoint samples and worse than the
rest at |t| > 2 on both.

| previous exit, window | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| timeout, <= 6 h | 214 / +0.019 / +0.38 / +0.056 / +1.06 | 503 / +0.007 / +0.25 / +0.008 / +0.25 |
| timeout, <= 24 h | 448 / +0.014 / +0.41 / +0.059 / +1.45 | 941 / +0.020 / +0.92 / +0.026 / +0.98 |
| target, <= 6 h | 115 / -0.164 / -1.68 / -0.141 / -1.43 | 207 / -0.116 / -1.57 / -0.123 / -1.64 |
| target, <= 24 h | 184 / -0.144 / -1.89 / -0.126 / -1.60 | 353 / -0.086 / -1.53 / -0.095 / -1.65 |
| stop, <= 24 h (after the 6 h cooldown) | 122 / +0.085 / +0.97 / +0.124 / +1.38 | 227 / -0.004 / -0.06 / -0.005 / -0.07 |
| no exit in the prior 24 h | 1,140 / -0.043 / -1.78 / -0.029 / -0.75 | 2,259 / +0.006 / +0.35 / +0.014 / +0.52 |

Re-entries after a timeout are, if anything, slightly better than the
rest on both samples, never significantly: the hypothesis is dead. Six
in ten entries on this book follow a timeout, so a cooldown there would
also have removed the majority of the trades for nothing. The one row
with the same sign twice is not the one preregistered: a re-entry
within a day of a *target* exit reads -0.144 R and -0.086 R, below the
rest by 0.13 and 0.09 R at t = -1.6 on each sample — the instrument
that just paid out re-breaks into the pullback. It is 7–10 % of entries
and it misses the bar on both clauses on both samples; recorded, not
acted on, and not to be re-run on the same data. The stop-out row is
the book after the live cooldown and shows nothing left to remove
there.

## 120. The dashboard was still summing quote currency as dollars

Section 98 moved the journal's realised PnL to USD on 2026-09-07 23:01
UTC. The dashboard never read that column for closed trades: since
2026-08-21 it recomputes the result from exit and fill price, to correct
the legacy rows booked against the signal price, and that recomputation
is in the instrument's quote currency. The first evening with yen and
HK50 stale exits after the switch showed it — three closes worth
-0.06, +0.66 and +0.60 USD in the journal rendered as +193.59 USD
"today" and turned the all-time figure positive:

| trade | journal (USD) | dashboard recomputation | ratio |
|---|---|---|---|
| HK50 short, 0.07 | -0.059 | -0.462 HKD | 7.8 |
| CHFJPY short, 200 | +0.662 | +101.80 JPY | 153.7 |
| AUDJPY short, 300 | +0.600 | +92.25 JPY | 153.6 |

Every USD-quoted close since the switch matches the column exactly. The
dashboard now reads `realized_pnl` for rows closed from 2026-09-07
23:05 UTC and keeps the price recomputation for the legacy rows, whose
residual currency distortion is the under-4 % footnote of section 99.
Today's line reads +1.20 USD and all-time -96.72 USD after the fix. A
test pins both branches and the fallback. The lesson for the gain
figure this whole document is measured by: a dashboard that computes
its own result from prices has to convert currency too, or read the
book that does.

## 121. A class-level risk tilt has no support in the journal

Sections 114 to 116 printed the book by asset class on both walk-forward
samples, and two classes kept their sign on both: crypto +0.094 and
+0.032 R, commodities +0.033 and +0.011 R, while FX read -0.021 and
-0.018 R (indices flipped, -0.115 then +0.041). The obvious sizing
lever is a tilt — halve the FX risk, leave the rest — which loosens no
limit and would raise the dollar result if the class signs are a
property of the book rather than of the samples. Preregistered before
the journal was read: the tilt is built in only if FX is negative in
the live journal as well and below the rest at t < -2 on both
walk-forward samples. The journal, 238 closed 1h-trend trades with
realised R at the fill:

| class | n | E[R] | t | vs rest, t | forward since 2026-08-24: n / E[R] |
|---|---|---|---|---|---|
| fx | 26 | **+0.085** | +0.90 | +1.01 | 8 / +0.437 |
| crypto | 86 | +0.032 | +0.27 | +0.59 | 4 / -0.410 |
| index | 26 | -0.019 | -0.15 | +0.02 | 6 / +0.077 |
| commodity | 100 | **-0.096** | -0.87 | -0.94 | 12 / -0.205 |

The journal ranks the classes the other way round: FX is its best class
and the commodities its worst, in the whole sample and in the forward
window alike. Nothing here is significant on either side (the FX
readings on the walk-forward were t ≈ -1 to begin with), which is the
point — the class signs are noise on 26 instruments over three years
and noise on 238 live trades, and they disagree. No tilt is built in;
the class dimension joins the instrument ranking (section 27's
successor, run 27) as measured and unpredictive.

## 122. The target re-entry does not travel either

Section 119 left one unpreregistered reading with the same sign on both
walk-forward samples: a re-entry within a day of a target exit on the
same instrument, -0.144 and -0.086 R, below the rest at t = -1.6 on
each. Following section 115's rule — a candidate that survives the time
sample is read on the excluded instruments and on the journal before it
is believed — `scripts/target_reentry_second_look.py` reads the same
split, preregistered before either ran: a 24-hour target-exit cooldown
would be built in only if the bucket were below the rest at t < -2 on
the excluded instruments and the journal agreed in sign.

| sample | n after target ≤ 24 h | E[R] | rest E[R] | diff | t |
|---|---|---|---|---|---|
| section 119, last 365 d (26 live instruments) | 184 | -0.144 | -0.019 | -0.126 | -1.60 |
| section 119, prior 730 d | 353 | -0.086 | +0.009 | -0.095 | -1.65 |
| (B) live journal, 238 closed trades | 41 | -0.139 | +0.003 | -0.142 | -0.73 |
| (A) excluded instruments, 3 years, merged timeline | 469 | -0.066 | -0.130 | **+0.063** | **+1.30** |

The journal agrees in sign, as weakly as its 41 trades allow. The
excluded instruments do not: there the re-entry after a target is the
*better* trade by 0.063 R, in a sample of 3,836 with the same router,
stop and cooldown. Three readings against one, none of the four
significant, and the one independent instrument set points the other
way — that is the profile of noise with a lucky sign, not of a
mechanism. No cooldown is built in; the exit-kind dimension (stop,
timeout, target) is now measured in full, and only the stop-out
cooldown of section 86 stands.

## 123. A 3-ATR stop with a 48-bar leash: slightly better, weakly, and the stop is not the lever

Section 60 chose the 2-ATR stop over 3 ATR because at the 24-bar leash
three trades in five timed out, and section 89 found no better leash at
2 ATR. The combination had not been read: a wider stop cuts the cost per
unit of risk and a longer leash gives it the time it needs.
`scripts/stop_hold_combo.py` replays the three live 1h trend strategies
on the router-passed path, venue minimum, live widening rule, gap-aware
stop booking and the commodity short block over all 26 tradeable
instruments for the live 2.0 / 24 and for 3.0 / 48, 3.0 / 36 and
2.5 / 36. Preregistered: 3.0 / 48 is built in if its net E[R] beats the
live setting on both disjoint samples, its gross E[R] is not worse on
either, and its timeout share is under 50 %.

| stop / hold | last 365 d: n / net / cost / gross / timeout % / Σ R / vs live, t | prior 730 d: same |
|---|---|---|
| 2.0 / 24 (live) | 4,505 / -0.033 / 0.017 / -0.016 / 61.1 / -147.3 / — | 9,139 / +0.016 / 0.018 / +0.034 / 64.1 / +142.9 / — |
| **3.0 / 48** | 3,800 / -0.024 / 0.014 / -0.009 / **51.1** / -89.2 / +0.009, t +0.48 | 7,597 / +0.019 / 0.016 / +0.035 / **50.5** / +145.7 / +0.004, t +0.26 |
| 3.0 / 36 | 4,017 / -0.031 / 0.014 / -0.017 / 58.2 / -124.1 / +0.002, t +0.10 | 8,031 / +0.014 / 0.015 / +0.030 / 59.7 / +115.2 / -0.001, t -0.10 |
| 2.5 / 36 | 4,096 / -0.043 / 0.016 / -0.028 / 54.3 / -177.5 / -0.011, t -0.58 | 8,147 / +0.018 / 0.017 / +0.035 / 56.1 / +144.8 / +0.002, t +0.17 |

Two of the three clauses hold, weakly: 3.0 / 48 is better net on both
samples by 0.009 and 0.004 R at t = 0.5 and 0.3, and not worse gross.
The third misses by a point on each sample, 51.1 % and 50.5 % timeouts
against the 50 % written down — a clause that, it turns out, the live
setting itself fails by a wider margin (61 % and 64 %), so it guarded
against a state the book is already in. The result is not built in; it
is not evidence of anything at those t-values, and the total R of the
older sample is the same to within two R at 17 % fewer trades.

The more useful reading is in the cost and stop-width columns. Tripling
the ATR multiple from 1 to 3 should cut the cost per R to a third; here
the cost falls only from 0.017 to 0.014 R, because the stop sits at the
venue's 1.05 % minimum on most trades whatever the multiple — the mean
stop is 1.2 % of price at 2 ATR and 1.4 % at 3 ATR. The venue floor,
not the ATR multiple, sets the cost of this book (section 23's vise),
and the stop-width lever was exhausted by the move to 2 ATR. The
timeout share of 61–64 % on the live path says the same thing from the
other side: at a 1.05 % stop and a 1.6 % target, most hourly breakouts
reach neither in a day.

## 124. The target has no ordering on the pinned trades either — and 78 % of trades are pinned

Section 123 showed the stop sitting on the venue's 1.05 % minimum on
most trades. On such a trade the 1.5 R target is far further away in
volatility units than the 2-ATR design assumed, so the reward:risk
setting might want to differ there. `scripts/rr_by_pin_status.py`
replays the three live 1h trend strategies on the router-passed path,
venue minimum, live widening rule, gap-aware stop booking and the
commodity short block over all 26 tradeable instruments at RR 1.0 /
1.5 / 2.0 / 2.5, separately for trades whose stop was widened to the
floor and for trades whose 2-ATR stop stood. Preregistered: a
different RR for the pinned trades only is built in if better than 1.5
at t > 2 on both disjoint samples.

| last 365 d | share | mean stop | RR 1.0 | RR 1.5 (live) | RR 2.0 | RR 2.5 |
|---|---|---|---|---|---|---|
| pinned to the floor | 78 % | 6.5 ATR | -0.023 (+0.012, t +0.70) | -0.035 | -0.030 (+0.005, t +0.28) | -0.027 (+0.008, t +0.44) |
| ATR-bound | 22 % | 2.0 ATR | -0.001 (+0.024, t +0.54) | -0.026 | -0.019 (+0.007, t +0.13) | -0.010 (+0.016, t +0.29) |

No ordering: on the pinned trades 1.5 is the worst of four and 1.0 and
2.5 are both "better" by a hundredth of an R at t < 0.7, a U-shape that
is noise; the ATR-bound quarter reads the same. Nothing approaches the
bar on the first sample, so the second was not run. Section 63's
conclusion — the target is not the lever — holds on both halves of the
book separately.

The number to keep is the share. Seventy-eight per cent of
router-passed trades have their stop widened to the venue floor, and
on those the mean stop is 6.5 ATR(14): every FX trade, most index and
metal trades. The "2-ATR stop" of the design is what one trade in five
actually gets. The book as traded is a 1.05 %-of-price stop with a
1.6 % target on hourly bars, held a day; on a major FX pair that is
six and nine hours of typical range, which is why three trades in five
reach neither level (section 123). The venue floor is not a cost
detail of this book, it is its execution model, and the levers this
document has swept — stop multiple, target, leash, signal features —
were all swept on top of it.

## 125. More time does not help the pinned trades

Section 124 left the obvious follow-up: a stop of 6.5 ATR needs more
than 24 hourly bars to resolve, a 2-ATR stop does not, so the holding
leash might want to differ by stop status. `scripts/hold_by_pin_status.py`
replays the three live 1h trend strategies on the router-passed path,
venue minimum, live widening rule, gap-aware stop booking and the
commodity short block over all 26 tradeable instruments at holds 24 /
48 / 72 / 96, separately for pinned and ATR-bound trades. Preregistered:
a 48-bar leash for pinned trades only is built in if better than 24 on
that subset at t > 2 on both disjoint samples.

| last 365 d | hold 24 (live) | 48 | 72 | 96 |
|---|---|---|---|---|
| pinned (78 %, 6.5 ATR): E[R] / timeout % / vs 24, t | -0.035 / 72 / — | -0.031 / 55 / +0.004, t +0.21 | -0.031 / 45 / +0.004, t +0.17 | -0.027 / 36 / +0.008, t +0.33 |
| ATR-bound (22 %, 2.0 ATR): same | -0.026 / 23 / — | -0.072 / 10 / -0.046, t -0.90 | -0.073 / 4 / -0.047, t -0.91 | -0.069 / 1 / -0.043, t -0.83 |

The pinned trades time out seven times in ten at 24 bars and still
four times in ten at 72; giving them up to four days moves their
expectancy by less than a hundredth of an R at t ≈ 0.3. The ATR-bound
quarter, where the stop is where the design put it, gets worse with
every extension — its breakouts that have not paid in a day do not pay
in four, and holding them turns timeouts into stops. Nothing meets the
bar on the first sample; the second was not run. The leash was already
measured out pooled (sections 62 and 89); split by stop status it is
measured out too. What the pinned trades need is not time but a stop
scaled to their volatility, which the venue does not sell below 1.05 %
of price — the vise of section 23, now measured from the exit side.

## 126. The overnight fee: real, invisible to the journal, and small on this book

Every gain figure in this document comes from the bot's own price
arithmetic. The venue charges an overnight fee on each open position at
21:00 UTC, and nothing in the journal, the dashboard or the simulator
has ever seen it. `scripts/overnight_fee_audit.py` reads the account's
transaction history and the current per-instrument rates (read-only).

Last 30 days of the demo account (the endpoint returned exactly 100
entries, so the list may be capped; the earliest is 2026-08-25):

| type | entries | sum |
|---|---|---|
| TRADE (closes) | 24 | -4.26 EUR |
| SWAP (overnight fee) | 70 position-nights | -0.51 EUR |
| CORPORATE_ACTION | 6 | +0.01 EUR |

The fee ran at 12 % of the realised trade result over the month, a
mean of 0.007 EUR per position-night — 0.003 R at the 3 USD risk —
because the book held mostly FX and short index positions, whose rates
are tiny or even positive: crypto and metal shorts *receive* (BTCUSD
and ETHUSD shorts +0.03 EUR a night). The rates say where it would
matter: at the 250 USD notional a crypto long pays 0.051 R a night,
an index long 0.014–0.018 R, a metal long 0.013 R, while shorts on
those pay 0.0005–0.011 R or are credited. For comparison the round-trip
spread costs 0.017 R per trade (section 123), so a crypto long that
times out has paid three spreads in financing, and an index long one.

Measured as a lever: only the crypto longs are material. Read off
section 109's direction table, crypto longs run about +0.05 R and
0.00 R on the two samples before financing and about +0.01 and -0.03 R
after one night of it — negative on one sample, not significantly on
either, so no block follows; a financing-aware cost ceiling would
refuse nothing, since even the crypto long's spread plus one night
stays under 10 % of risk. What does follow is accounting: the book's
true result is about 0.003 R per trade below every figure here, and
the dashboard's daily gain omits roughly half a euro a month. Not
corrected — the journal is price-based by design and the account is
topped up (section 38), so the fee would have to be pulled from the
transaction history per position; recorded as the second known
understatement after section 99's currency mix.

## 127. The router's forward test: the trades it kept out lost 35 R

Sections 46, 46b and 49e left the ADX router on with a standing rule:
it comes off only when an independent forward test reads passed minus
rejected below t = -2. Since 2026-08-24 the live loop journals every
intent the router refuses with its pair, direction, signal price, stop
and target, and since then it has refused 246 intents against 33
accepted trades — seven in eight signals the strategies raise are
rejected on the 1h ADX. `scripts/router_forward_test.py` replays both
sides the same way from the 1h history that followed each signal bar
(24-bar hold, gap-aware stop, audited spread; 197 rejected and 30
accepted intents had complete history):

| intents since 2026-08-24 | n | E[R] | t | win % | Σ R |
|---|---|---|---|---|---|
| accepted by the router (traded) | 30 | -0.115 | -0.74 | 46.7 | -3.4 |
| rejected by the router | 197 | **-0.177** | **-3.28** | 38.6 | **-34.9** |
| passed − rejected | | +0.062 | +0.38 | | |

The rule is not met — the difference is t = +0.38, and the router stays
on — but this is the first forward reading in which the router's sign
is right: the signals it kept out would have lost 35 R in sixteen days,
significantly, while the book it let through lost 3.4 R. Per strategy
the rejected donchian signals are the loss (-0.221 R, t = -3.46, n =
122), turtle's -0.152 R at t = -1.59; the seven rejected momentum
signals would have won. By ADX band nothing is monotonic: the rejected
signals lose about the same below 20 as at 25–30, and the nine at ADX
above 40 that fell to the core-floor logic are flat. Two cautions. The
accepted side is 30 trades, so the difference has no power at all.
And the sixteen days are one regime — the same fortnight in which the
book itself lost — so the reading says the router filtered a bad
fortnight well, not that ADX predicts. It is recorded as the forward
test the rule asked for; the rule is not triggered and nothing changes.
The remaining guards, for the record, barely act: since 2026-08-24 the
concurrent cap refused 2 entries, the size floor 2, the stop floor 3,
the duplicate-instrument guard 19.

## 128. Strategy agreement on the signal bar carries nothing

Section 5 noted, on 44 live trades, that entries raised by two strategies
on the same bar returned +0.108 R against -0.161 R for lone signals
(t = 1.65), and the live loop's duplicate guard makes exactly that
distinction every hour: when donchian, turtle and keltner fire together
it takes one entry and drops the rest. `scripts/strategy_agreement.py`
replays the merged one-position-per-instrument timeline on the
router-passed path, live 2-ATR stop, venue minimum, live widening rule,
gap-aware stop booking, commodity short block and the live 6-hour
stop-out cooldown over all 26 tradeable instruments, and buckets every
entry by the number of live strategies that fired on its bar in its
direction. Preregistered: the lone bucket is blocked only if
significantly negative at t < -2 on both disjoint samples and below the
rest at |t| > 2 on both.

| strategies on the bar, last 365 d | share | n | E[R] | t | vs rest | t |
|---|---|---|---|---|---|---|
| 1 (lone) | 42 % | 795 | -0.035 | -1.24 | -0.010 | -0.26 |
| 2 | 37 % | 708 | -0.043 | -1.37 | -0.022 | -0.56 |
| 3 (all three) | 21 % | 393 | +0.007 | +0.17 | +0.046 | +1.00 |
| >= 2 (confirmed) | 58 % | 1,101 | -0.025 | -1.01 | +0.010 | +0.26 |

Nothing: a lone signal reads the same as a confirmed one to within a
hundredth of an R, and the three-way agreement's +0.046 R over the rest
is t = 1.0 on 393 trades. The 44-trade reading of section 5 was the
noise it looked like. The first sample misses the bar, so the second was
not run. Three breakout definitions on one price series agree most of
the time by construction — the channel high, the 55-bar high and the
Keltner band are crossed within the same few bars in any trend — so
their agreement is not a second opinion, it is the same opinion three
times. The duplicate guard is right to keep one entry; which of the
three it keeps does not matter.

## 129. Direction by instrument: no cell qualifies, UK100 shorts recorded

Section 109 read direction by asset class and blocked the commodity
shorts, the one filter of this series that passed on both samples and
off-sample. The instrument level was the remaining cut.
`scripts/direction_by_instrument.py` replays the three live 1h trend
strategies on the router-passed path, live 2-ATR stop, venue minimum,
live widening rule, gap-aware stop booking and the commodity short block
over all 26 tradeable instruments on both disjoint samples and dumps
every trade with its instrument and direction; 52 instrument-sides
(the five commodities have no short side). Preregistered, with a size
floor added for the number of cells: a side is blocked only if it has
at least 100 trades on each sample, is negative at t < -2 on both, and
the long-short difference holds at |t| > 2 with the same sign on both.

No cell qualifies. The one side negative at t < -2 on both samples:

| instrument, side | last 365 d: n / E[R] / t | prior 730 d: n / E[R] / t | long − short, t (recent / prior) |
|---|---|---|---|
| UK100 short | 70 / -0.298 / -3.28 | 149 / -0.165 / -2.76 | +1.31 / +2.97 |

UK100's shorts miss on two counts — 70 trades on the recent year and a
long-short difference of t = 1.31 there — and UK100 has been at the
bottom of every instrument ranking (sections 70 and run 27) without its
longs being significantly better. Everything else in the table is the
by-now familiar sign flip: the recent year's index shorts (US500
-0.332 at t -4.26, US30 -0.235, EU50 -0.338, J225 -0.275, US100
-0.202) all read flat or positive on the two years before; EURAUD and
AUDJPY flip side between the samples with t beyond 3 on each; the
recent year's standout BTCUSD (+0.23 R both sides, t 2.6 and 2.4) is
+0.01 on the older sample. With 52 cells, one at t < -2 on both samples
is at the level chance produces. Nothing is blocked; UK100 shorts are
recorded as a watch, as SILVER was before section 109, and the
direction dimension is now measured at class and instrument level.

## 130. Instrument expectancy does not transfer between the samples

The nightly selector ranks instrument-strategy combinations by their
backtest expectancy and trades the top of the list, which assumes that
an instrument's past expectancy predicts its next. Section 129's trade
dumps allow the cleanest version of that test on the live path: rank
the 26 instruments by their router-passed E[R] over days 366–1,095 and
read the same instruments over the last 365 days. Preregistered: the
prior sample's bottom quartile is dropped from the book only if it is
below the rest on the recent year at t < -2.

| selection made on the prior 730 d | instruments | recent-year n / E[R] | rest of the book | diff / t |
|---|---|---|---|---|
| bottom quartile | OIL_BRENT, OIL_CRUDE, AUDUSD, UK100, EURAUD, EURUSD | 861 / -0.063 | -0.026 | -0.038 / -1.33 |
| top quartile | HK50, ETHUSD, US30, COPPER, US100, GOLD | 1,189 / -0.056 | -0.025 | -0.031 / -0.99 |
| all instruments positive on the prior sample (15) | | 2,855 / -0.031 | -0.036 (the 11 negative ones) | +0.006 / +0.24 |
| Spearman rank correlation, prior → recent | 26 | rho = -0.22, p = 0.28 | | |

Nothing transfers. The prior sample's winners and losers are both worse
than the rest on the recent year; an instrument that was positive
before reads the same as one that was negative, to within 0.006 R; the
rank correlation is negative and insignificant. Of the six top-quartile
names, four (US100, COPPER, US30, HK50) are among the recent year's
worst, and the recent year's best (BTCUSD +0.22 R) was +0.01 before.
GOLD is the one instrument positive on both samples, as section 70
already recorded. The condition is not met and nothing is dropped. What
follows is not a new filter but a reading of the selector: its ranking
by past expectancy is, as run 27 put it, a list of instruments that
trade, not a forecast — and a list of all tradeable instruments would
have done the same.

## 131. Where the expectancy goes: the barriers lose, the drift wins

Section 119's merged-timeline dumps carry every trade's result on both
samples, which allows the book's expectancy to be split by how each
trade ended — target, stop, or the 24-bar timeout — on the live path
(router, 2-ATR stop widened to the venue floor, RR 1.5, commodity short
block, stop-out cooldown), all 26 tradeable instruments.

| exit | last 365 d: share / mean R / contribution to E[R] | prior 730 d: same |
|---|---|---|
| target (+1.5 R less cost) | 13.6 % / +1.471 / +0.200 | 13.1 % / +1.470 / +0.193 |
| stop (-1 R less cost, gaps included) | 25.8 % / -1.025 / -0.264 | 23.0 % / -1.028 / -0.237 |
| timeout (24-bar drift less cost) | 60.7 % / **+0.055** (t +4.37) / +0.034 | 63.8 % / **+0.068** (t +7.64) / +0.044 |
| book | -0.031 | | +0.000 |

The shape is the same on both samples and it is not what the design
assumes. The barrier pair loses: at a 1.5 R target the stop is hit
nearly twice as often as the target on both samples (26 % against 14 %,
23 % against 13 %), so the two barriers together book -0.06 R and
-0.04 R per trade. The timeout, which the design treats as the failure
mode, is the only component in the black: the three trades in five that
reach neither level drift the right way by 0.055 and 0.068 R net of
costs, significantly on both samples (t = 4.4 and 7.6), with 52–53 %
of them positive. Read against the structure of sections 123 and 124:
on a stop pinned at 1.05 % of price, a 1.6 % target is reached by one
trade in seven and the stop by one in four within a day, while the
median trade just carries a small positive drift to the leash.

What this does and does not open. It does not reopen the target: RR
1.0 to 3.0 was flat pooled (sections 63 and 124), so letting the
target-hitters run yields no more than their 1.47 R on average. It does
not reopen the stop: the stop is the venue's floor on 78 % of trades and
cannot be moved down, and moving it up (3 ATR) changed nothing
(section 123). It does say that the book's small positive drift after a
router-passed breakout is real and repeatable, and that every barrier
placed on it has so far cost more than it caught — the one structural
statement of this series that holds on both samples with t above 4.
Recorded; no parameter follows from it that the log has not already
swept.

## 132. A time stop for losers does not replace the late stop

Section 131 found the stop, pinned six ATR away on most trades, hit
twice as often as the target and costing 0.26 R a trade, while the
24-bar drift was positive. A stop that far away protects late, so the
natural variant leaves a trade that is below its entry at the close of
bar K and books that close, keeping the hard stop and target otherwise.
`scripts/time_stop_losers.py` replays the three live 1h trend
strategies on the router-passed path, venue minimum, live widening rule,
gap-aware stop booking and the commodity short block over all 26
tradeable instruments for K = 6, 12 and 18 against the live setting.
Preregistered: a variant is built in only if better than the live
setting on both disjoint samples at t > 2.

| last 365 d | n | net E[R] | gross | early exits | Σ R | vs live, t |
|---|---|---|---|---|---|---|
| none (live) | 4,502 | -0.033 | -0.016 | — | -147.3 | — |
| K = 6 | 5,134 | -0.043 | -0.026 | 76.5 % | -219.2 | -0.010, t -0.64 |
| K = 12 | 4,770 | -0.027 | -0.010 | 70.9 % | -130.2 | +0.005, t +0.33 |
| K = 18 | 4,594 | -0.032 | -0.015 | 65.7 % | -145.4 | +0.001, t +0.06 |

Nothing: leaving losers at six bars is worse (three trades in four are
under water at some point in their first six hours and many of them
recover), at twelve it is a hundredth of an R better at t = 0.3, at
eighteen it is the live setting. The first sample misses the bar by a
wide margin and the second was stopped to spare the venue. The stop
losses of section 131 are not the slow bleed of trades that were losing
from the start; they are trades that moved 1.05 % against within a day,
and no earlier reading of the price warns of them. The exit side is now
swept in every form this book allows — target, stop width, leash,
break-even, trailing, regime, time-of-day, time stop — and the result is
the same each time: the barriers cost what they cost, and the drift is
what it is.

## 133. The gain figure reconciled against the broker: right since the fix, the legacy rows as known

Every daily-gain figure comes from the journal's price arithmetic; the
account's transaction history is the broker's own statement of each
close. `scripts/broker_reconciliation.py` matches the last 30 days of
journal closes to the account's TRADE transactions by instrument and
time (the broker's dealId is the position's, not the journalled opening
reference, so ids do not match) and compares them at 1.1699 USD per EUR.

| | |
|---|---|
| matched closes | 24 of 25 (one predates the transaction window) |
| journal | -2.31 USD |
| broker | -4.26 EUR = -4.98 USD |
| journal minus broker | +2.67 USD, +0.11 USD per trade |

The gap is not spread across the book. Twenty closes agree to within
±0.04 USD — every USD-quoted instrument and every close since the USD
booking of section 98 went live (HK50, CHFJPY, AUDJPY on 2026-09-08 at
+0.12, -0.04 and -0.01 USD). Four closes carry it: AUDNZD +1.56 USD,
GBPAUD +0.52, GBPCAD +0.51 and AU200 -0.34, all quoted in NZD, AUD or
CAD and all closed on 2026-09-02 and 2026-09-04, before the fix, when
the journal summed quote currency as dollars; the ratios of journal to
broker (1.70, 1.40, 1.37, 1.35) are the currency rates. That is section
99's legacy distortion measured on the broker's numbers instead of
estimated: +2.25 USD of the +2.67. The remaining +0.20 USD sits on two
crypto stale exits (ETHUSD +0.12, BTCUSD +0.08, 0.03–0.05 % of
notional) and is the size of the mid-versus-bid difference on a close
booked at the bar price, as section 118 measured on the entry side.

So the metric is sound going forward: since 2026-09-07 23:01 UTC the
journal and the broker agree to the cent on the trade result, and the
two known understatements are the overnight fee (section 126, about
0.003 R a trade, never in the journal) and the legacy currency rows,
which now have a broker-verified size. Neither is corrected in the
data — production rows are read-only by rule — and the dashboard's
all-time figure therefore reads about 2 USD better than the account on
the last month's closes, and section 99's estimate on the older ones.

## 134. Session of entry in the journal: the third sample is flat too

Section 51 opened the time-of-day filter and saw it dissolve on the
second simulator sample. The live journal is a third sample on
different trades: 238 closed 1h-trend trades since May, realised R at
the fill, by the UTC hour of the signal bar.

| entry window (UTC) | n | E[R] | t | rest | t_diff |
|---|---|---|---|---|---|
| 00–06 | 49 | -0.186 | -1.21 | +0.021 | -1.22 |
| 06–12 | 75 | -0.037 | -0.32 | -0.015 | -0.16 |
| 12–18 | 72 | -0.009 | -0.08 | -0.027 | +0.13 |
| 18–24 | 42 | +0.176 | +1.10 | -0.064 | +1.37 |

Nothing above |t| = 1.4, and the two ends of the day that read largest
here (Asia worst, late US best) are not the ones section 51's first
sample flagged. Three samples, three different shapes: the session is
not a lever, on the simulator or live.

## 135. The "venue floor" is the project's own — and it beats the designed stop

Every stop measurement in this document rests on a floor of 1.05 % of
price described as the venue's minimum. It is not. The dealing rules
give `minStopOrProfitDistance` as `{unit: PERCENTAGE, value: 0.01}` next
to `maxStopOrProfitDistance` `{PERCENTAGE, 100}` and
`minGuaranteedStopDistance` `{PERCENTAGE, 0.25}`: the unit is plain
percent, the minimum is 0.01 % of price (GOLD 0.001 %), and the code
reads the value as a fraction, so 0.01 became 1 % and, with the 5 %
buffer, 1.05 % — a hundred times the venue's requirement. The journal
confirms it: the broker accepted and honoured stops at 0.176 % (AUDUSD),
0.310 % (ETHUSD) and 0.53–0.88 % on a dozen other instruments, each
loss exit landing exactly at the journalled stop; the `stoploss`
rejections on record (ARBUSD, APTUSD, July) carried stops of 1.05–2.3 %
and were not minimum-distance rejects. Sections 23, 28, 123 and 124
therefore describe a vise the project built for itself.

Whether to remove it is a measurement, not a correction, and it was
preregistered as one: the true floor with the designed 2-ATR stop is
built in only if not worse than the live setting on both samples.
`scripts/venue_floor_correction.py`, router-passed path, live widening
rule, gap-aware booking, commodity short block, 26 instruments:

| floor / stop | last 365 d: n / net E[R] / cost R / stop % / target % / timeout % / mean stop / vs live, t | prior 730 d: same |
|---|---|---|
| **1.05 % / 2 ATR (live)** | 4,502 / -0.033 / 0.017 / 26 / 13 / 61 / 1.22 % / — | 9,137 / +0.016 / 0.018 / 22 / 14 / 64 / 1.18 % / — |
| 0.0105 % / 2 ATR | 5,210 / -0.065 / 0.034 / 51 / 31 / 18 / 0.70 % / -0.033, t -1.64 | 10,634 / -0.018 / 0.036 / 51 / 33 / 16 / 0.64 % / **-0.034, t -2.45** |
| 0.0105 % / 1 ATR | 6,354 / -0.072 / 0.059 / 60 / 39 / 1 / 0.37 % / -0.039, t -1.99 | 12,981 / -0.065 / 0.061 / 60 / 40 / 1 / 0.34 % / -0.081, t -5.92 |
| 0.0105 % / 3 ATR | 4,644 / -0.031 / 0.023 / 36 / 20 / 44 / 1.05 % / +0.001, t +0.07 | 9,476 / +0.006 / 0.025 / 36 / 22 / 42 / 0.94 % / -0.010, t -0.78 |

The designed stop loses to the accidental one on both samples, by
0.033 R and 0.034 R, significantly on the older. At 2 ATR the stop is
0.7 % of price instead of 1.2 %, the cost per R doubles from 0.017 to
0.035 R, half the trades stop out instead of a quarter, and the timeout
drift that section 131 identified as the book's only positive component
is cut from three trades in five to one in six. At 1 ATR it is worse
again; at 3 ATR the mean stop lands at 1.0 % and the result at the live
figure — which is the finding of section 60 (2 ATR over 1 ATR) and of
section 123 (3 ATR no better than the floor) seen from the other side:
on this book the stop wants to be about 1 % of price, whatever the
volatility, because the barrier costs more than it saves and the drift
needs room. The floor stays, now as what it is: the project's wide-stop
setting, not the venue's rule. The three docstrings that called it the
venue's minimum now say so; no behaviour changed and no test moved.
Sections 19 and 23 remain correct about the numbers and wrong about the
cause, and this section is their correction.

## 136. The floor is a parameter — and 1.05 % is where both samples want it

Section 135 made the 1.05 % stop floor the project's own setting, so it
can be varied, and no sweep had ever varied it: every earlier stop sweep
moved the ATR multiple above a fixed floor. `scripts/stop_floor_sweep.py`
replays the three live 1h trend strategies on the router-passed path,
live widening rule, gap-aware stop booking and the commodity short block
over all 26 tradeable instruments at 2 ATR under floors of 1.05 % (live),
1.5 %, 2 % and 3 % of price. Preregistered: a wider floor is built in
only if better net on both disjoint samples, not worse gross on either,
and better at t > 2 pooled; after the first sample and before the
second, the choice among passing floors was fixed as the smallest.

| floor | last 365 d: net E[R] / cost R / gross / stop % / timeout % / vs live, t | prior 730 d: same |
|---|---|---|
| **1.05 % (live)** | -0.033 / 0.017 / -0.016 / 26 / 61 / — | +0.016 / 0.018 / +0.034 / 22 / 64 / — |
| 1.5 % | -0.013 / 0.013 / +0.001 / 17 / 74 / +0.020, t +1.25 | +0.007 / 0.014 / +0.021 / 15 / 77 / -0.009, t -0.81 |
| 2.0 % | -0.009 / 0.011 / +0.001 / 11 / 82 / +0.023, t +1.53 | +0.006 / 0.011 / +0.017 / 10 / 84 / -0.010, t -0.97 |
| 3.0 % | -0.001 / 0.007 / +0.007 / 5 / 91 / **+0.032, t +2.27** | +0.002 / 0.007 / +0.009 / 6 / 91 / -0.015, t -1.48 |

On the recent year the reading is monotonic and, at 3 %, significant:
the wider the floor, the less the barriers cost and the more of the
positive drift the trade keeps, until at 3 % nine trades in ten are
plain 24-bar holds and the book is flat instead of -0.033 R. On the two
years before every wider floor is worse, net and gross alike, and the
ordering is reversed: there the barriers earned their keep, the target
at 1.05 % paying more than the drift it forfeits. The first clause
fails and nothing is built in. Read together with section 135 (the
designed 2-ATR stop, 0.7 % of price, loses on both samples), the floor
sits at a value both samples accept — narrower loses twice, wider loses
once — which is as much as this book can say about its stop. Position
sizing is untouched; the wider floors would also have cut sizes by a
third to two thirds and multiplied the minimum-size skips.

## 137. The floor's recent-year gain was the indices — the same artefact again

Section 136's sweep, split by asset class from its dumps (no new
history), with the same rule per class: a class-specific floor is built
in only if better net on both samples and t > 2 pooled.

| class, floor vs 1.05 % | last 365 d: diff / t | prior 730 d: diff / t | pooled t |
|---|---|---|---|
| index, 3 % | **+0.054 / +2.32** | **-0.030 / -1.89** | -0.21 |
| index, 1.5 % | +0.032 / +1.17 | -0.017 / -0.91 | -0.05 |
| fx, 3 % | +0.013 / +1.12 | +0.010 / +1.09 | +1.52 |
| fx, 2 % | +0.009 / +0.73 | +0.004 / +0.44 | +0.76 |
| crypto, 1.5–3 % | +0.02 to +0.04 / ≤ 0.61 | -0.02 to +0.01 / ≤ 0.51 | ≤ 0.47 |
| commodity, 1.5–3 % | +0.01 to +0.05 / ≤ 0.90 | -0.02 to -0.03 / ≤ -0.93 | ≤ -0.07 |

The recent year's monotonic gain from a wider floor is the indices,
+0.054 R at t = 2.3 for the 3 % floor, and on the two years before the
same class loses 0.030 R with it at t = -1.9 — section 109's index
shorts and section 116's crowded breakouts in a third disguise: a wide
stop on an index short in a bull year is a stop that is not hit before
the dip reverses. FX is the one class where a wider floor reads the
same sign on both samples, +0.013 and +0.010 R at 3 %, and it pools to
t = 1.5; crypto and commodities are noise. Nothing qualifies and no
class-specific floor is built in. The stop dimension is now measured in
every direction the book allows — multiple, floor, class, leash, time
stop — and closed.

## 138. The night of 2026-09-08/09 in one table

Twenty-five levers were measured between sections 113 and 137, with the
two disjoint walk-forward samples as the bar and, where a candidate
survived them, the excluded instruments and the live journal as the
off-sample read. The pooled clause of section 136 closes the last one:
floors of 1.5, 2 and 3 % pool to +0.0006, +0.0009 and +0.0009 R against
the live floor over 13,000 trades, t = 0.07 to 0.11.

| lever | recent year | prior two years | off-sample | verdict |
|---|---|---|---|---|
| broken level's age (113) | flat | flat | — | dead |
| 4h ADX at the signal (114, 115) | upper half -0.068, t_diff -3.2 | -0.019, t_diff -4.2 | excluded flat, journal reversed | dead |
| peer confirmation (116) | crowded -0.091, t -4.8 | +0.051, t +3.7 | — | sign flip |
| passive limit entry (117) | worse on every count | not run | — | dead |
| live slippage (118) | +0.004 R after the fix | — | — | closed |
| timeout re-entry (119) | flat | flat | — | dead |
| target re-entry (119, 122) | -0.144, t -1.9 | -0.086, t -1.5 | journal agrees, excluded reversed | dead |
| dashboard quote-currency sum (120) | +193.59 shown for +1.20 | — | — | fixed |
| class risk tilt (121) | fx negative | fx negative | journal: fx best class | dead |
| 3 ATR / 48 bars (123) | +0.009, t 0.5 | +0.004, t 0.3 | — | flat, floor-bound |
| RR by stop status (124) | no ordering | not run | — | dead |
| leash by stop status (125) | flat / worse | not run | — | dead |
| overnight fee (126) | 0.003 R a night on this book | — | broker statement | recorded |
| router forward test (127) | rejected -0.177 (n 197) vs passed -0.115 (n 30) | — | — | router stays |
| strategy agreement (128) | flat | not run | — | dead |
| direction by instrument (129) | index shorts -0.33, t -4.3 | flat / positive | — | sign flip |
| instrument transfer (130) | rho -0.22 | — | — | none |
| expectancy by exit (131) | barriers -0.06, drift +0.055 (t 4.4) | barriers -0.04, drift +0.068 (t 7.6) | — | structural |
| time stop for losers (132) | flat / worse | stopped | — | dead |
| broker reconciliation (133) | journal = broker ± 0.04 since the fix | — | legacy rows +2.25 | metric sound |
| session (134) | — | — | journal flat | dead on 3 samples |
| venue floor re-read (135) | true floor -0.033, t -1.6 | -0.034, t -2.5 | — | floor kept, cause corrected |
| floor as a parameter (136, 137) | 3 %: +0.032, t 2.3 (indices) | -0.015, t -1.5 | pooled t 0.1 | sign flip |

What holds on both samples: the router-passed book carries a small
positive 24-bar drift (sections 131) that the barriers cost more than
they catch; the stop wants to be about 1 % of price, narrower loses
twice and wider loses once (135, 136); the commodity shorts lose (109);
nothing about the signal bar, the level it breaks, its peers, its
session, its class or its instrument predicts the next trade; and every
reading that was strong on the recent year alone was the bull-year
index regime. What follows for the objective is section 37's
arithmetic, unchanged: at 3 USD of risk and an expectancy of zero
within ±0.03 R, the daily gain is noise around zero, and no lever this
book allows has moved it.

## 139. The drift is survivorship: unconditional, the router book is zero

Section 131's positive timeout drift is conditional on a trade reaching
neither barrier. Section 136's 3 % floor, at which 91 % of trades run
to the 24-bar leash, is as close to the unconditional drift after a
router-passed signal as this book allows, net of costs, from the
sweep's dumps:

| class | last 365 d: n / E[R] / t | prior 730 d: n / E[R] / t |
|---|---|---|
| crypto | 537 / +0.108 / +2.89 | 1,120 / +0.011 / +0.40 |
| fx | 1,637 / -0.008 / -2.09 | 3,283 / -0.008 / -2.48 |
| index | 1,557 / -0.058 / -6.02 | 3,239 / +0.013 / +1.90 |
| commodity | 525 / +0.079 / +2.62 | 978 / -0.015 / -0.98 |
| **all** | 4,256 / **-0.001** / -0.09 | 8,620 / **+0.002** / +0.32 |

Unconditionally the book drifts nowhere: -0.001 and +0.002 R on the two
samples, with R here a 3 % move. The positive drift of the timeouts was
the drift of the trades that had not already gone 1 % against — the
survivors — and the barriers' cost was the price of selecting them. FX
is the one class negative on both samples (t -2.1 and -2.5), and its
size is the cost: -0.008 R of a 3 % stop is -0.02 % of price per trade,
the spread. Indices and commodities flip sign between the samples once
more; crypto is positive twice but at t = 0.4 on the older. Read as the
preregistered test of a barrier-less drift book — positive at t > 2 on
both samples — it fails on both, and with it the last mechanism this
book had left: the signals do not drift, the barriers do not select,
and the costs are what remains.

## 140. The one two-sample "better" candidate, after financing

Of the night's levers, only the 3-ATR stop with a 48-bar leash (section
123) was better net on both samples: +0.009 and +0.004 R at t = 0.5 and
0.3, with the preregistered timeout clause missed by a point. Section
126 has since priced the overnight fee at about 0.003 R a position-night,
and the longer leash crosses a second rollover on the trades that time
out. Charging it — one night for a live timeout, two for a 48-bar one,
half and one for barrier exits — the difference shrinks to +0.007 R on
the recent year and +0.0015 R on the older. At the live book's two
entries a day and 3 USD of risk that is four cents and one cent a day,
before the 17 % fewer entries the longer leash allows; at the
simulator's independent per-strategy count it would be a quarter of a
dollar. Neither is distinguishable from zero, and neither justifies a
change to the stop, the leash, the sizing and a restart. Not built in;
the candidate is closed.

## 141. R in the forward report was mixing currencies since the USD booking

The forward read of the trades opened since the 2-ATR stop went live
showed the three yen and HK50 stale exits at R = 0.00 beside +0.66 and
+0.60 USD. The forward report divides `realized_pnl` — in USD since
2026-09-07 23:01 UTC (section 98) — by the stop distance times size,
which is in the instrument's quote currency, so a yen trade's R read
150-fold too small; the dashboard's projection divided the same USD
result by a quote-currency notional. The live-expectancy veto and the
edge scaling were not affected: both recompute the result from prices
and divide by a price-based risk, quote currency over quote currency.

Both readers now cancel the currency with prices: R is exit against fill
over the stop distance, the return fraction is the same over the fill
price; rows without an exit price keep the old ratio, which for the
legacy rows is quote over quote and consistent. A test pins the return
expression on the CHFJPY close. Forward since 2026-08-24, corrected: 30
closes, +0.012 R, -2.67 USD. The journal analyses of sections 115, 119,
121, 122 and 134 used the old ratio; only the three post-switch
non-USD closes were affected, three rows in 238, and no reading moves.

## 142. The live spreads against the audit: right within a tenth, except three indices

Section 106 put a spread sampler on the heartbeat so the static spread
audit the cost filter and the simulator use (2026-08-24 and 2026-09-04)
could be checked against the venue's live quotes. After one day, 554
samples on 31 instruments, 14–21 per instrument, taken at the
heartbeat's minute (about :47) each hour:

| instrument | samples | median live spread | audit | ratio |
|---|---|---|---|---|
| 24 instruments (crypto, FX, US indices, metals, oils) | 14–21 each | — | — | 0.95–1.13 |
| DE40 | 21 | 0.0077 % | 0.0057 % | 1.34 |
| FR40 | 19 | 0.0156 % | 0.0090 % | 1.74 |
| UK100 | 19 | 0.0185 % | 0.0090 % | 2.06 |
| CADJPY, USDJPY | 20, 15 | 0.0063 %, 0.0078 % | not audited | — |

The audit holds to within a tenth for everything the book trades most,
crypto and the oils to within 3 %. The three European indices trade at
1.3–2.1 times their audited spread at the sampled hours — the off-hours
widening of section 105 read live — and the FX crosses show occasional
outliers (AUDJPY 0.18 %, USDCHF 0.24 % once each) that the medians
absorb. In R the understatement is small: UK100's round trip at the live
median is 1.8 % of a 1.05 % stop instead of 0.9 %, far under the 10 %
ceiling, so no filter decision changes, and UK100 and FR40 are already
the book's structural losers on other grounds (sections 70, 129). The
audit is not refreshed from one day of samples taken at one minute of
the hour; when a week is in, `capital_spread_audit.py` can take the
medians, and the two unaudited yen crosses should be added then.

## 143. The journal shows the same shape: barriers lose, stale exits carry

Section 131's split of the simulated book by exit kind, read on the 238
live 1h-trend trades of the journal (R from prices, free of currency;
outcomes as the journal labels them — `win` target, `loss` stop,
`manual` the 24-hour stale exit):

| exit | n | share | mean R | t | contribution to E[R] |
|---|---|---|---|---|---|
| target | 56 | 23.5 % | +1.273 | +25.5 | +0.299 |
| stop | 89 | 37.4 % | -1.042 | -50.1 | -0.390 |
| stale exit | 93 | 39.1 % | +0.065 | +0.92 | +0.026 |
| all | 238 | | -0.065 | -0.99 | |

The live book reads as the simulator does: the stop is hit more often
than the target and the two barriers together cost 0.09 R a trade,
while the trades that reach neither drift the right way by +0.065 R —
the same +0.055 / +0.068 R the two simulator samples gave. The barrier
shares are higher live (61 % against 39 %) because most journal trades
date from the 1-ATR stop; in the forward window since 2026-08-24, mostly
at the 2-ATR stop, half the closes are stale exits at +0.22 R, a sixth
targets, a third stops, and the book reads +0.012 R over 30 trades.
Section 139 has already shown that the drift is that of the survivors
and is zero unconditionally; the journal adds that live execution does
not change the shape.

## 144. Why live targets paid 1.27 R: the levels are anchored to the signal, the fill was not

Section 143's live wins average +1.27 R against the +1.47 R the simulator
books. On the 52 journalled wins with complete prices the broker is not
the reason — 88 % closed within 0.02 R of the target, mean exit minus
target +0.007 R — and the design is not either: target over stop,
both measured from the signal close, is 1.499. The gap is the fill.
Stop and target are derived from the signal close and sent with the
order; the fill lands past the signal by the entry slippage, so measured
from the fill the stop is wider and the target nearer: on these wins the
slippage was +0.10 R (the forming-bar era of section 100) and the
target stood 1.25 R from the fill, 1.15 R on the wins with the 1 %-plus
stop. Losses show the mirror image, exiting 0.045 R beyond the stop.
Since section 101 fixed the forming-bar entry the slippage is +0.004 to
+0.017 R (section 118), so the same anchoring now costs a hundredth of an
R on a win and gains it back on a loss; re-anchoring the levels to the
fill would need a second order call per trade for that. Not changed;
the 1.27 R is a legacy figure, and the forward wins since 2026-08-24
average +1.44 R.

## 145. Stop-exit slippage: at the stop, with a fat tail that is already blocked

Section 144's losses exited 0.045 R beyond the stop on average, which at
a 37 % stop share would be a cost the size of the spread. On the 83
journalled stop exits with full prices:

| stop exits | n | mean beyond stop | median | share beyond 0.10 R |
|---|---|---|---|---|
| all | 83 | +0.045 R (s.e. 0.022) | +0.004 R | 5 % |
| stop under 1 % (1-ATR era) | 12 | +0.030 | +0.000 | 17 % |
| stop at the floor or wider | 71 | +0.048 | +0.005 | 3 % |
| since the 2-ATR stop (2026-09-07) | 2 | +0.028 | | 0 % |

The median stop exit is at the stop: +0.004 R, half a hundredth of the
distance. The mean is two trades — ATOMUSD, a gap of 1.77 R, and
PALLADIUM, 0.54 R — both on instruments the cost blocklist has since
removed; without them the mean is +0.02 R, and the two Monday-morning
exits that could carry a weekend gap read +0.04 R. Stop execution is
not a cost of this book; the gap risk that remains is the one section
75 priced (a gapped stop costs 1.75 R, one Friday trade in twenty to
fifty), and it lives in instruments no longer traded. Nothing changes.

## 146. Live expectancy per strategy, currency-free

The strategy-level veto (section 91) reads realised results per
strategy; the same read with R taken from prices, so no currency enters,
over every closed Capital.com trade:

| strategy | n | E[R] | t | win % | forward since 2026-08-24: n / E[R] |
|---|---|---|---|---|---|
| donchian_breakout | 132 | -0.045 | -0.50 | 43.9 | 22 / +0.056 |
| turtle_breakout | 68 | -0.038 | -0.30 | 39.7 | 8 / -0.110 |
| momentum | 5 | -0.035 | -0.06 | 40.0 | 0 |
| keltner_breakout (not in the rotation) | 33 | -0.205 | -1.36 | 39.4 | 0 |
| retired mean reversion (bollinger, stochastic, rsi) | 208 | -0.165 to -0.416 | -1.8 to -2.9 | 28–41 | 0 |
| disabled variants (v2, v3, trail, 4h) | 82 | -0.05 to -0.73 | | | 0 |

The three live trend strategies are indistinguishable from zero and from
each other, live as in the simulator; the retired and disabled names
carry the loss the dashboard's "Gesamt inkl. stillgelegt" line shows.
Nothing in the live rotation is significantly negative and nothing
follows for the veto.

## 147. Direction in the live journal: the same signs, none significant

Section 109 blocked the commodity shorts on the simulator (both samples,
t -3.3 and -3.2) with the journal agreeing in sign; section 129 found
the index shorts flipping between the samples. The live journal's 238
1h-trend trades by side and class, R from prices:

| class | long: n / E[R] / t | short: n / E[R] / t | long − short, t |
|---|---|---|---|
| fx | 13 / +0.070 / +0.46 | 13 / +0.134 / +1.16 | -0.33 |
| index | 11 / +0.212 / +0.98 | 15 / -0.193 / -1.21 | +1.51 |
| crypto | 49 / -0.079 / -0.52 | 37 / +0.012 / +0.07 | -0.38 |
| commodity | 66 / -0.116 / -0.84 | 34 / -0.188 / -1.01 | +0.31 |
| all | 139 / -0.060 / -0.68 | 99 / -0.072 / -0.73 | +0.09 |

Live, the commodity shorts are the worst side (-0.188 R, 34 trades, now
blocked) and the index shorts the next (-0.193 R, 15 trades), the two
readings of sections 109 and 129 in sign; nothing reaches significance
on 238 trades and the long-short difference is t = 0.09 over the book.
Nothing follows: the block already stands where the evidence met the
bar, and the index side did not meet it on the simulator's second
sample.

## 148. The target on the live frequency

Section 37 priced the 50 EUR/day target on the frequency of July. On
the live book as it trades now — 33 entries in the 16 days since the
filters went live, 2.05 a day, 2.44 USD of risk at fill against 3.00
planned (the 250 USD notional cap binds on the pinned trades):

| what the target needs | value |
|---|---|
| 58.5 USD a day at 2.05 entries a day | 28.5 USD per trade |
| at E[R] = 0.03 (the book's ±noise) | 950 USD of risk per trade, 390 times the current |
| at E[R] = 0.10 | 285 USD per trade, 117 times |
| at E[R] = 0.30 | 95 USD per trade, 39 times |
| at the current risk and frequency | 11.7 R per trade, or 800 trades a day at 0.03 R |

No lever measured in this document moves E[R] by more than a few
hundredths of an R on both samples, and none moves the entry rate by
more than a factor of two without letting through what the router
refuses (section 127: -0.177 R). The target is two orders of magnitude
away in risk per trade, and risk per trade is the one parameter this
document has not touched, because at an expectancy of zero within
±0.03 R it multiplies noise and, on the recent year, loss. Recorded so
the daily-gain line is read against what it can show.

## 149. The ADX slope does not travel either — the candidate list is empty

Section 112 left the fading ADX (change over three bars below +0.22)
as the one forward candidate: worse than the rest on both walk-forward
samples at |t| > 2, the bucket itself missing t < -2 on the older one.
Following section 115's rule, `scripts/adx_slope_second_look.py` reads
the same split, fixed at +0.22, on the excluded instruments (three
years, router-passed, costs charged without the ceiling skip) and on the
live journal (238 closed 1h-trend trades, R from prices). Preregistered:
a fading-ADX veto is built in only if the bucket is below the rest at
t < -2 on the excluded instruments and the journal agrees in sign.

| sample | n fading | E[R] fading | rest | fading − rest | t |
|---|---|---|---|---|---|
| section 112, last 365 d | 1,126 | -0.081 | -0.016 | -0.065 | -2.33 |
| section 112, prior 730 d | 2,519 | -0.017 | +0.025 | -0.042 | -2.19 |
| (A) excluded instruments, net | 2,067 | -0.111 | -0.134 | **+0.023** | **+1.04** |
| (A) excluded instruments, gross | 2,067 | +0.017 | +0.000 | +0.017 | +0.77 |
| (B) journal, all closed | 65 | -0.084 | -0.057 | -0.027 | -0.18 |
| (B) journal, forward since 2026-08-24 | 8 | +0.084 | -0.014 | +0.098 | +0.29 |

On the instruments that were not part of the selection the fading
quarter is the better one, in two of the three strategies, net and
gross; the journal agrees in sign on the whole sample at t = -0.18 and
reverses in the forward window on eight trades. The first condition
fails and nothing is built in. That closes the forward-test list: of
the 4h ADX (section 115), the target re-entry (122) and the ADX slope,
none carried beyond the 26 instruments and three years they were found
on. The rule that every candidate surviving the time split must also
survive the instrument split and the journal has now retired all three
that reached it, which is what the rule was for.

## 150. GOLD, the one two-sample instrument, read on the journal

Section 130 left GOLD as the only instrument positive on both
walk-forward samples on the live path (+0.177 R over 108 and +0.138 R
over 260 router-passed trades), section 66 had pooled it to t = 2.7 and
3.2 across strategies, and section 67 measured it per strategy as
positive against random on both samples and significant on neither.
GOLD is active for donchian, turtle and momentum. The journal is the
independent read: 15 closed GOLD trades since July, R from prices —
donchian 2 at +0.43 R together, turtle 13 at -1.66 R together, -0.08 R
a trade over all 15 against the simulator's +0.14 to +0.18. Fifteen
trades settle nothing (t about -0.3), but they do not confirm the
instrument either, and the same widening of a shared prior — a
positive reading on the instruments and years it was found on, flat or
reversed on the trades that followed — is what every other candidate of
this document has shown. Nothing changes: GOLD stays where the ranking
puts it, and the next read is the journal at 50 GOLD trades.

## 151. Rollovers crossed per trade: none from the 21:00 entry, all from the outages

Section 126 priced the overnight fee; the one way a 24-bar trade could
pay it twice is an entry in the hour before 21:00 UTC that is still open
at the next day's charge. From the journal, the 30 closes since
2026-08-24: 14 crossed no rollover, 9 crossed one, none crossed two by
that mechanism — no entry fell on the 21:00 bar. The seven that crossed
four to ten were held 81–235 hours: the three US indices through the
2026-09-04 holiday pause (section 39's guard) and four FX positions
through the five days the bot was down after the 2026-08-30 reboot,
which the boot script and keepalive now prevent. Nothing follows for the
entry rule; the fee is one night per timeout on this book, as section
126 assumed.

## 152. What the forward programme can decide, and when

The forward tests this document defers to — the router at 100 accepted
trades, GOLD at 50, the ADX slope — have a power that follows from two
numbers: the live book's R has a standard deviation of 0.96 (186 trend
trades since 2026-07-10, R from prices) and the book accepts 2.05
entries a day (section 148). For a difference to reach t = 2:

| question | difference | trades needed | at 2.05 a day |
|---|---|---|---|
| router, passed − rejected (section 127) | +0.062 R | ≈ 970 | 1.3 years |
| book expectancy against zero, if it were | +0.03 R | ≈ 4,100 | 5.5 years |
| book expectancy against zero, if it were | +0.10 R | ≈ 370 | half a year |

Thirty-three accepted trades exist since 2026-08-24. The forward
programme can therefore settle the router in a little over a year and
an expectancy of the size the walk-forward samples disagree about
(±0.03 R) never, at this frequency; only an edge three times larger than
anything measured on this book would show inside a year. This is the
arithmetic behind section 148 seen from the evidence side: the live
frequency is too low not only to reach the target but to learn whether
the book has an edge at all, and the router that keeps seven signals in
eight out is what sets that frequency. Nothing changes here; the
100-trade and 50-trade thresholds stand as the points at which the
forward reads are worth taking, not as points at which they decide.

## 153. The router floor forward: every lower floor loses more per day

Section 152 named the router as what sets the live frequency; section
127 replayed the intents it refused. Combining the two into the
counterfactual floors, on the sixteen days since 2026-08-24 (R net of
the audited spread, rejected intents replayed with their journalled
levels):

| router floor | entries | E[R] | entries a day | R a day |
|---|---|---|---|---|
| 30 (live) | 30 | -0.115 | 1.9 | -0.22 |
| 25 | 69 | -0.162 | 4.3 | -0.70 |
| 20 | 117 | -0.161 | 7.3 | -1.18 |
| none | 255 | -0.166 | 15.9 | -2.64 |

Every lower floor buys frequency at a worse expectancy and loses more
per day; the band the first step would add (ADX 25–30) read -0.199 R
on 39 intents. On the simulator the floor was flat between 15 and 40
(sections 46b and run 18), so this is one bad fortnight read forward,
not a new setting — but it is the direction the fortnight points, and
it is the same direction as section 127. The floor stays at 30.

## 154. Stale-exit latency: 71 seconds, symmetric

The three stale exits of 2026-09-08 in the session log went from the
leash trigger to the confirmed close in 71, 73 and 71 seconds — the
close request and its confirmation poll. At an hourly range of about
0.15 % the price moves about 0.02 % in that time, 2 % of the 1.05 %
stop, with no sign: a ±0.02 R jitter on two closes in five, not a
cost. The three open positions at the time of reading were 23.0, 21.0
and 13.0 hours old, inside the leash. Nothing to change.

## 155. The projection tile's premise does not hold out of sample

The dashboard's "Projected Gains" tile extrapolates the three most
profitable live combinations of net-positive strategies to a EUR-per-day
figure at a 1,000 EUR stake with a 50 % haircut ("Netto 119T" is the
net over the 119-day span). Its premise — that the best combinations so
far predict the next period — is section 130's question, and run 62's
dumps answer it for instruments: the prior sample's top three by E[R]
(GOLD +0.138, US100 +0.111, COPPER +0.075 R) read +0.177, -0.116 and
-0.158 R on the recent year, -0.054 R pooled against the book's -0.033.
Two of three reversed, the pooled selection did worse than no
selection. The tile is labelled as an extrapolation and haircut, and it
is not the daily-gain figure the objective is measured by, so it stays;
it should be read as what the last period's winners would have made
had they continued, which section 130 shows they do not.

## 156. Book load at entry, on the journal

Whether a trade opened into a full book fares worse than one opened
into an empty one is the forward face of the concurrent cap (section
91) and the cluster cap. On the 238 closed 1h-trend trades, R from
prices, by positions already open at entry:

| open at entry | n | E[R] | t | rest | t_diff |
|---|---|---|---|---|---|
| 0–1 | 114 | -0.046 | -0.49 | -0.082 | +0.27 |
| 2–3 | 78 | +0.022 | +0.19 | -0.107 | +0.91 |
| 4–5 | 40 | -0.299 | -2.01 | -0.018 | -1.70 |
| 6–7 | 6 | +0.023 | +0.05 | -0.067 | +0.18 |

The 4–5 band reads -0.30 R on 40 trades, the band above it +0.02 on
six: no ordering, one journal sample, nothing at the bar. The cap of 8
has refused two entries since 2026-08-24 (section 127) and the cluster
cap none; neither is tightened on this.

## 157. The nightly list turns over one combination a day, and costs the bot an hour of rate limits

The nightly selector re-ranks the book at 05:30 UTC. Yesterday's list
against today's: 69 combinations before, 68 after; 68 kept, none
added, one removed (GOLD / momentum); the Spearman correlation of the
kept combinations' scores is 0.87. The list is, as sections 130 and 155
say, a list of what trades, and it barely moves from one day to the
next — the daily re-ranking decides almost nothing. What it does do is
run three backtests over the whole universe inside the venue's 10
requests-a-second budget shared with the bot: the session log shows 58
rate-limit errors on the bot's own evaluations between 05:30 and
05:50 UTC while the refresh ran, against 34 in the preceding day from
all of this night's replays together. A skipped evaluation costs one
bar on one instrument at a quiet hour, so this is not an operational
blocker, but it is the refresh's only measurable effect: a daily
one-combination turnover bought with an hour of the bot's evaluations
missing. Left as is; a paced fetch in the selector, as the replays use,
would remove it.

## 158. The selector's score against the live result, per combination

Today's list carries the selector's backtest expectancy and score per
combination; twelve of its combinations have five or more closed live
trades (R from prices):

| combination | backtest E[R] | live E[R] / n |
|---|---|---|
| BTCUSD / donchian | +0.206 | +0.080 / 42 |
| BTCUSD / turtle | +0.197 | +0.163 / 12 |
| GOLD / turtle | +0.186 | -0.128 / 13 |
| OIL_BRENT / turtle | +0.115 | -0.003 / 19 |
| OIL_CRUDE / turtle | +0.076 | +0.001 / 10 |
| OIL_BRENT / donchian | +0.016 | +0.008 / 15 |
| GBPCAD / donchian | -0.040 | +0.235 / 5 |
| ETHUSD / donchian | -0.044 | -0.286 / 11 |
| EURAUD / turtle | -0.083 | -0.131 / 8 |
| AUDUSD / donchian | -0.120 | -0.040 / 8 |

Spearman of backtest against live expectancy +0.39 (p = 0.21), of
score against live +0.41 (p = 0.19): the right sign, twelve points,
nothing at the bar, and the two combinations the backtest likes most
after BTCUSD (GOLD and OIL_BRENT turtle) read flat or negative live.
The list's order carries about as much as sections 130 and 155 found
for instruments — a weak positive rank correlation that is not
distinguishable from none. Nothing changes.

## 159. The leash live: US30 closed at 24.0 h, +0.31 R

The stale-exit path read as it ran: the US30 short opened 2026-09-08
06:00 UTC triggered at 06:00:27 the next day ("closed after 24.0h"),
was confirmed closed 74 seconds later at 52,820.2 against a 52,994.1
fill, +0.71 USD, +0.31 R against the 1.05 % stop. The dashboard picked
it up on its next pass: four trades today, +1.91 USD, all-time
-96.01 USD. Section 154's latency (71–74 s) and section 131's shape
(a timeout carrying a small positive drift) both as measured. Nothing
to change; the day's figure moved because a trade closed, which is the
book working, not a lever.

## 160. The pins: a cost-chosen universe, not a list of winners

Twenty-nine of today's 68 combinations are operator pins that bypass
the ranking. Read against the backtest and the journal: twenty carry a
negative backtest expectancy (UK100 donchian -0.261, UK100 turtle
-0.236, US100 turtle -0.162 and so on), which is by design — the pin
file's own note chooses the universe on cost per unit of risk, because
selecting pins by past results "filled this list with losers" and the
backtest is not predictive either (sections 130, 158). Live, only two
pinned combinations are negative on both counts with five or more
trades, AUDUSD donchian (-0.040 R, n 8) and EURAUD turtle (-0.131 R,
n 8), neither near the bar; the live-expectancy veto retires pins on
realised results before the pins are appended, so the mechanism to
drop a losing pin already exists and has its own bar. The five 4h pins
have no backtest figure and 0–7 live trades. Nothing is unpinned.

## 161. Barriers resolve in hours: stops at a median of 3, targets at 4.5

On the 97 live trend trades since 2026-07-10 that ended at a barrier,
hours from entry to exit:

| exit | n | median | mean | within 3 h | within 6 h | within 12 h | beyond 24 h |
|---|---|---|---|---|---|---|---|
| stop | 62 | 3.0 | 4.4 | 50 % | 73 % | 95 % | 2 % |
| target | 35 | 4.5 | 9.5 | 40 % | 66 % | 94 % | 3 % |

Half the stops are hit inside three hours of a signal that fired on a
breakout — the immediate reversal that sections 132 and 119 could not
anticipate from any earlier bar — and the targets are not much slower.
Both barriers are decided inside the first half-day; the 24-bar leash
governs only the trades that reach neither, which is the drift book of
section 131. Nothing to change; the timing says the same as the
expectancy split, from the clock's side.

## 162. The live book by month

Whether the built-in changes moved the live book is a question the
journal answers only coarsely, but it is worth one table (R from
prices, trend strategies):

| month | n | E[R] | t | win % | USD |
|---|---|---|---|---|---|
| 2026-05 | 15 | +0.134 | +0.41 | 47 | +45.19 |
| 2026-06 | 24 | +0.029 | +0.13 | 50 | +80.06 |
| 2026-07 | 105 | -0.098 | -1.04 | 37 | -15.10 |
| 2026-08 | 81 | -0.088 | -0.77 | 43 | -15.73 |
| 2026-09 (to the 9th) | 14 | -0.029 | -0.14 | 57 | -2.64 |

No month is distinguishable from zero and no month from another; the
dollar column is the sizing of the time (larger positions in May and
June) more than the expectancy. The keepalive lock was touched at
08:05:07, the cron entries are in place.

## 163. From intent to trade since the filters went live

Of the 310 intents the strategies raised since 2026-08-24, the router
refused 251; of the 59 that passed, 33 became trades (56 %). The other
26 fell to the guards behind the router: 19 to the duplicate-instrument
guard (a second strategy firing on a bar already taken — by design one
entry per instrument and bar), 3 to the stop floor, 2 to the concurrent
cap and 2 to the broker's minimum size. Nothing is refused for a reason
that a change could recover without touching a limit: the duplicates
are the same trade twice, and the four size and floor refusals are
instruments the sizing cannot reach at the 1.05 % stop. The frequency
of the live book is the router's, as section 152 says, and behind it
the guards take four passed signals in ten, most of them duplicates.

## 164. The five structural refusals, resolved or marginal

Section 163's five refusals behind the router that were not duplicates:
three GOLD signals refused under the 1 % floor on 2026-08-25 and
2026-09-04 with 0.44–0.45 % stops — the case section 104 closed by
widening GOLD to the shared floor on 2026-09-08 — and two size refusals,
CHFJPY on 2026-09-04 and HK50 on 2026-09-07, where the size the 3 USD
risk allowed fell a hair under the broker's minimum (1.30 against 100
units, 0.0099 against 0.01) and raising it would have breached the
notional cap. Both instruments traded again within days. No instrument
in the list is structurally unreachable at the floor; the refusals are
the fail-closed sizing doing what section 82 built it to do at the
margin, on two signals in sixteen days.

## 165. The daily figure's own distribution

The objective is measured on calendar days, so the days are worth a
table. Active book, closes since 2026-07-10, 62 calendar days:

| | |
|---|---|
| mean per day | -1.32 USD |
| standard deviation of a day | 6.99 USD |
| positive / zero / negative days | 32 % / 18 % / 50 % |
| best / worst day | +32.21 / -24.84 USD |
| 95 % band of one day | ±13.7 USD |
| target 58.5 USD a day | 8.6 daily standard deviations above the mean |

A single day's figure is noise of ±14 USD around a mean a dollar
below zero; the target sits nine standard deviations out. Whether the
daily gain "rose" on any one day is therefore not readable from that
day — today's +1.91 USD is inside the band of every day this summer —
and the only figures that can move are the mean, which every section
of this document has failed to move at t > 2, and the sizing, which
multiplies the mean and the band alike (section 148).

## 166. Sizing on the forward window: the cap binds on nine trades in ten

Since 2026-08-24 the fill risk averaged 2.45 USD against the 3.00 USD
budget, and on 90 % of the 31 closed trades it sat under 2.90 USD — the
250 USD notional cap binds whenever the stop is the 1.05 % floor
(3.00 / 0.0105 = 286 USD of notional needed). The cap therefore sizes
the pinned trades (FX, indices) down and the ATR-bound ones (crypto,
commodities) up to the full budget, which is a tilt nothing intended.
Rescaled to a uniform 3.00 USD of risk the window reads +2.02 USD
instead of the realised -1.96, and +4.04 at 6.00 USD; the per-trade R
of +0.022 behind those figures is t ≈ 0.1 on 31 trades, so the sign is
noise and the tilt has moved four dollars in sixteen days. Section 78
kept the cap as an exposure limit; the day's standard deviation would
go from 3.2 to 3.9 USD at the full budget and to 7.7 at double. Nothing
changes: the cap is a limit, and a limit is not loosened on a sign that
thirty trades cannot establish.

## 167. The cap's tilt by class: fifteen per cent, not a class split

Section 166's tilt, by class on the 33 forward entries: fill risk
2.29 USD on FX (100 % capped), 2.37 on the indices (100 %), 2.52 on the
commodities (75 %), 2.69 on crypto (100 % — a BTCUSD stop of 1.1 %
needs 270 USD of notional, over the cap too). The cap binds almost
everywhere, so the tilt between the smallest and largest average risk
is 15 %, not a clean pinned-versus-unpinned split; the window's sign
by class (FX +0.50 R on 8 closes, commodities -0.21 on 12, crypto -0.41
on 4) is sample noise at those counts. Nothing changes; section 166's
figures stand with the tilt narrower than stated there.

## 168. Where the frequency comes from: everywhere, thinly

Intents since 2026-08-24 by instrument: 30 instruments raised them,
22 traded at least once, and the five busiest (US100, AUDNZD, BTCUSD,
AUDUSD, ETHUSD, 17–20 intents each) carry 29 % of the intents and
24 % of the trades. The router's pass rate by instrument runs from
zero (USDCHF, J225, GBPUSD, DE40: 12–14 intents each, none passed) to
about a half (COPPER 53 %, GOLD 45 %, CHFJPY 36 %), with the crypto and
index names at 10–17 %. No instrument carries the book and none is
idle by construction: the frequency is spread across the universe and
thinned uniformly by the router. Nothing changes.

## 169. Where the signals fire on the ADX scale

The journalled ADX at the signal bar for the 308 intents since
2026-08-24: percentiles 10 / 25 / 50 / 75 / 90 at 13.7 / 16.8 / 21.0 /
27.4 / 36.1; 45 % below 20, 16 % in the 25–30 band, 19 % at or above the
router's 30. Accepted entries sit at a median ADX of 36.4, rejected at
20.0; donchian's signals have a median of 20.4 and turtle's 22.6, the
eight momentum intents 17.9 with none over 30. A breakout of a 20- or
55-bar channel typically fires while the 1h ADX is still in its
teens or low twenties — the trend has not registered on the indicator
when the level breaks — so the router, by construction, keeps the
first four fifths of every breakout out and admits the ones that come
after ADX has built. That is the mechanism behind section 152's
frequency and section 153's forward reading; it is not changed here.

## 170. The rate limits cost no entries

Ninety-two rate-limit errors hit the bot's evaluations between
2026-09-08 04:00 and 2026-09-09 08:00 UTC — the night's replays and the
05:30 refresh (58 of them). By minute of the hour: none in the first
five minutes after a bar close, where the just-closed bar's signal is
evaluated and an order would leave; one in the last five minutes
before; 91 mid-hour, where an evaluation that fails is repeated on the
next poll with the same closed bar. The replays' pacing and the loop's
60-second poll kept the venue's budget clear at the one moment it
matters. Nothing to change; the replays stay paced and the refresh's
fetches remain unpaced, as section 157 noted.

## 171. Momentum in the rotation: thirteen intents since May, none past the router since August

The momentum strategy holds seven ranked combinations in today's list
(US30, DE40, US100, EU50, UK100, ETHUSD, FR40) and two 4h pins. Its
live record: 13 intents since May, 5 traded (2 since July, none since
2026-08-24, when all 8 intents fell to the router at a median ADX of
17.9). It is one of the three strategies the nightly refresh backtests
over the whole universe, so it accounts for a third of the refresh's
venue load (section 157) and, in the live book, for nothing. Section
57 measured it positive against random on both samples at t ≤ 1.4 and
kept it; that stands — a strategy is not dropped for trading rarely —
and the load it adds is the refresh's, not the bot's. Nothing changes.

## 172. Volatility at the signal: the quiet quarter wins one year and nothing the next

How volatile an instrument is at the moment a 1h breakout fires,
relative to its own recent history, had never been read on the live
book; the old `vol_squeeze` strategies of section 7 asked a different
question with a different simulator. `scripts/atr_regime_gate.py`
replays the three live 1h trend strategies on the router-passed path
at the 2-ATR stop, venue minimum, live widening rule, gap-aware stop
booking and the commodity short block over all 26 tradeable
instruments, and buckets every trade by the percentile rank of
ATR(14)/close at the signal bar against the 720 bars (30 days) before
it, so nothing after the signal is seen and the first month of each
window carries no feature. Quartile edges were fixed on the recent
year (28.75 / 57.50 / 81.94) and applied unchanged to the two years
before. Preregistered rule as in sections 110 to 114: a bucket is
blocked only if significantly negative at t < -2 on both disjoint
samples and its difference to the rest holds at |t| > 2 with the same
sign on both. After the recent year had been seen and before the older
sample ran, a second candidate was preregistered under the same bar:
keep only the quietest quarter (rank below 28.75), i.e. block the
upper three quarters as one bucket.

| ATR% rank at the 1h signal | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| below 28.75 (quiet) | 994 / **+0.066** / **+2.62** / +0.133 / **+4.52** | 2,305 / +0.027 / +1.76 / +0.011 / +0.57 |
| 28.75–57.50 | 997 / **-0.083** / **-3.16** / -0.065 / **-2.15** | 2,392 / +0.008 / +0.51 / -0.016 / -0.80 |
| 57.50–81.94 | 986 / **-0.098** / **-3.86** / -0.086 / **-2.89** | 1,914 / +0.027 / +1.46 / +0.010 / +0.47 |
| above 81.94 (loud) | 995 / -0.021 / -0.75 / +0.017 / +0.55 | 1,989 / +0.017 / +0.81 / -0.004 / -0.17 |
| upper three quarters (>= 28.75) | 2,978 / **-0.067** / **-4.39** / -0.133 / **-4.52** | 6,295 / +0.017 / +1.57 / -0.011 / -0.57 |
| book without 57.50–81.94 | 2,986 / -0.013 | 6,686 / +0.017 |
| book, whole | 3,972 / -0.034 / -2.58 | 8,600 / +0.020 / +2.21 |

On the recent year the reading is as clean as section 114's: the
quietest quarter is the only profitable one, +0.066 R at t = +2.62,
its gap to the rest holds at t = +4.52, and the two middle quarters are
significantly negative on their own (t = -3.16 and -3.86), the third
qualifying for the first clause of the bar with t_diff = -2.89. All
three strategies share the shape; donchian_breakout carries the third
quarter (-0.101 R, t = -2.47) and keltner_breakout the quiet one
(+0.069 R, t_diff = +3.27).

On the two years before, nothing of it survives. The third quarter
turns to +0.027 R at t = +1.46, the quiet quarter's advantage shrinks
to +0.011 R at t = +0.57, and the upper three quarters as one bucket
read +0.017 R at t = +1.57 — positive, against the t < -2 the rule
requires. No bucket is farther than 0.8 standard errors from the rest
on the older sample, in any of the three strategies. Both candidates
fail the first clause outright; this is not the near miss of sections
112 and 114 but a sign flip, of the kind sections 110, 111, 113 and
116 recorded for the signal-bar features.

Read together with section 114: the recent year rewarded breakouts
that fired before anything had built — 4h ADX still low, ATR still
low — and the two years before did not. That is one regime's
signature, not a filter. No filter is built in; the feature is
recorded as a non-lever. Nothing changes.

## 173. Trend alignment at the signal: counter-trend breakouts lose a little, everywhere, never enough

Whether a 1h breakout fires with or against the instrument's longer
trend had never been read on the live book; the router gates on ADX
strength, which is direction-free. `scripts/htf_trend_alignment.py`
replays the three live 1h trend strategies on the router-passed path at
the 2-ATR stop, venue minimum, live widening rule, gap-aware stop
booking and the commodity short block over all 26 tradeable
instruments, and tags every trade with the signed distance of the
signal close to the causal EMA(200) of 1h closes (about eight trading
days), in ATR(14) units and in the signal's direction — negative means
the breakout fires against the longer trend. Quartile edges were fixed
on the recent year (2.47 / 5.12 / 7.54 ATR) and applied unchanged to
the two years before. Preregistered before the data were seen: the
primary split is aligned (>= 0) against counter-trend (< 0), and a
bucket is blocked only if significantly negative at t < -2 on both
disjoint samples and its difference to the rest holds at |t| > 2 with
the same sign on both (the bar of sections 110 to 114 and 172).

| close − EMA200 at the 1h signal | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| counter-trend (< 0) | 501 / -0.048 / -1.37 / -0.021 / -0.57 | 1,056 / -0.029 / -1.17 / -0.053 / -2.00 |
| deep counter-trend (< -1 ATR) | 351 / -0.065 / -1.54 / -0.040 / -0.89 | 780 / -0.055 / -1.91 / -0.079 / **-2.63** |
| below 2.47 ATR | 1,094 / -0.015 / -0.63 / +0.018 / +0.65 | 2,233 / -0.032 / -1.89 / -0.066 / **-3.36** |
| 2.47–5.12 | 1,095 / -0.023 / -0.94 / +0.007 / +0.25 | 1,991 / **+0.050** / **+2.76** / +0.042 / **+2.04** |
| 5.12–7.54 | 1,093 / -0.027 / -1.05 / +0.002 / +0.06 | 2,082 / +0.020 / +1.08 / +0.003 / +0.14 |
| above 7.54 ATR | 1,096 / -0.049 / -1.91 / -0.027 / -0.92 | 2,702 / **+0.033** / **+2.09** / +0.022 / +1.15 |
| extended (>= 3 ATR) | 3,061 / **-0.041** / **-2.67** / -0.040 / -1.50 | 6,410 / **+0.032** / **+3.10** / +0.050 / **+2.64** |
| book, whole | 4,378 / -0.029 / -2.11 | 9,008 / +0.018 / +1.90 |

The breakouts fire, as they must, mostly with the trend: the median
signal sits five ATR beyond its EMA(200) and only 11–12 % of trades
are counter-trend on either sample. Those counter-trend trades lose on
both samples — -0.048 R and -0.029 R, the deep ones -0.065 R and
-0.055 R — with the same sign on all three strategies except
keltner_breakout on the older sample. That consistency is the most a
signal-bar feature has shown in this series, and it is still short of
the bar on every count: no bucket reaches t < -2 on either sample
(the closest is the deep counter-trend at -1.91 on the older one), the
difference to the rest holds at |t| > 2 on the older sample only, and
the lowest quartile's difference flips sign between the samples (+0.65
then -3.36).

The rest of the table is the regime signature of sections 114 and 172
once more: on the recent year the extended breakouts (three ATR or
more beyond the EMA) were the losers, -0.041 R at t = -2.67, and on
the two years before they were the winners, +0.032 R at t = +3.10 —
the same "early versus late" reading as the low 4h ADX and the quiet
ATR quarter, with the sign flipping between the samples.

Even had the counter-trend block passed, it would touch one trade in
nine and move the book by about 0.005 R per trade. No filter is built
in; trend alignment joins the signal-bar features as a non-lever. The
consistent small loss of the counter-trend trades is recorded for a
later read on the live journal once it holds enough of them. Nothing
changes.

## 174. Half out at +1 R: the recent year likes it, the two years before pay for it

The live exit is all-or-nothing: the whole position runs to the 1.5 R
target, the stop or the 24-bar leash. Sections 131 and 143 read that
the barriers lose and the drift wins, which raised the question
whether banking half of the position once it has travelled one stop
distance in favour, and letting the rest run, changes the expectancy.
`scripts/partial_exit.py` replays the three live 1h trend strategies
on the router-passed path at the 2-ATR stop, venue minimum, live
widening rule, gap-aware stop booking and the commodity short block
over all 26 tradeable instruments and books, on the same bar path of
every trade, three rules: the live rule; A, half out at +1 R with the
remainder unchanged (stop and 1.5 R target as before); B, half out at
+1 R with the remainder's target moved to 2.5 R. The trade set is
fixed by the live rule, so every difference is paired per trade and
the paired t is the test. When the partial level and a barrier fall in
the same bar, the stop is booked first and the partial is counted
only if the target was not hit on that bar either — the conservative
order. Acceptance was the bar of the exit series (sections 123, 125,
132): better than the live rule at paired t > 2 on both disjoint
samples.

| rule | last 365 d (n = 4,501): E[R] / diff vs live / paired t / win % | prior 730 d (n = 9,153): same |
|---|---|---|
| live (all-or-nothing, 1.5 R) | -0.031 / — / — / 45.5 | +0.014 / — / — / 47.7 |
| A: half out at +1 R, rest to 1.5 R | -0.025 / **+0.007** / **+2.24** / 46.9 | +0.007 / **-0.007** / **-3.77** / 48.9 |
| B: half out at +1 R, rest to 2.5 R | -0.023 / **+0.008** / **+2.03** / 46.2 | +0.008 / -0.006 / **-2.15** / 48.0 |

One trade in four reaches +1 R on either sample, so the rules differ
on a quarter of the book. On the recent year both partial rules beat
the live rule by 0.007–0.008 R per trade at paired t just above 2,
uniformly across the three strategies (turtle_breakout the strongest,
+0.014 R under B). On the two years before, both lose by the same
0.006–0.007 R per trade at paired t = -3.77 and -2.15, again on all
three strategies, turtle_breakout the most (-0.008 R under A at
t = -2.63). The win rate rises by about one point under either rule on
both samples, as banking half at +1 R must; what the recent year gave
back on the runners was less than the half taken, and the older sample
gave back more.

That is section 131 in a different guise: the rule takes money from
the trades that would have reached 1.5 R and gives it to the trades
that would have turned back, and which of those groups is larger
depends on the year. The recent year has been the poorer one for the
barriers, so the partial looked better there — the same "this year"
reading the exit series has produced before. The rule fails the bar
by a sign flip; neither variant is built in. The exit stays
all-or-nothing at 1.5 R. Nothing changes.

## 175. The weekday, opened once: every day that stands out in one year stands out the other way in the next

Section 7 refused to open time, session and weekday variants because a
free search over calendar buckets overfits, and section 51 opened the
session window exactly once, preregistered, and watched it dissolve on
the second sample. The weekday was the remaining calendar axis and
`scripts/weekday_split.py` opens it the same way, once: the three live
1h trend strategies on the router-passed path at the 2-ATR stop, venue
minimum, live widening rule, gap-aware stop booking and the commodity
short block over all 26 tradeable instruments, every trade bucketed
by the UTC weekday of its signal bar. Saturday and Sunday exist only
on the two crypto pairs. The rule was fixed before the data were seen:
a weekday is blocked only if its E[R] is significantly negative at
t < -2 on both disjoint samples and its difference to the rest holds
at |t| > 2 with the same sign on both.

| weekday of the 1h signal (UTC) | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| Monday | 710 / -0.008 / -0.27 / +0.027 / +0.80 | 1,555 / -0.033 / -1.58 / -0.058 / **-2.51** |
| Tuesday | 832 / **+0.060** / **+2.12** / +0.111 / **+3.54** | 1,740 / +0.009 / +0.51 / -0.007 / -0.33 |
| Wednesday | 985 / **-0.066** / **-2.50** / -0.046 / -1.53 | 1,939 / -0.007 / -0.40 / -0.028 / -1.37 |
| Thursday | 918 / **-0.063** / **-2.52** / -0.041 / -1.43 | 1,823 / **+0.090** / **+4.58** / +0.093 / **+4.30** |
| Friday | 793 / +0.007 / +0.22 / +0.045 / +1.39 | 1,747 / -0.010 / -0.50 / -0.030 / -1.42 |
| Saturday (crypto) | 51 / -0.123 / -0.80 / -0.094 / -0.61 | 91 / **+0.334** / **+2.94** / +0.322 / **+2.83** |
| Sunday (crypto) | 214 / **-0.268** / **-4.46** / -0.249 / **-4.06** | 265 / +0.030 / +0.44 / +0.015 / +0.22 |
| weekend (crypto) | 265 / **-0.240** / **-4.23** / -0.222 / **-3.83** | 356 / +0.107 / +1.84 / +0.096 / +1.63 |
| book, whole | 4,503 / -0.031 / -2.29 | 9,160 / +0.015 / +1.72 |

The recent year, read alone, would have been persuasive: Sunday
signals on the crypto pairs lost 0.27 R per trade at t = -4.46, their
gap to the rest held at t = -4.06, and all three strategies agreed
(donchian_breakout -0.31 R at t = -3.15, turtle_breakout -0.31 R at
t = -2.90). Tuesday was the only profitable day at +0.060 R and
t_diff = +3.54; Wednesday and Thursday were both significantly
negative on their own. On the two years before, none of it holds:
Sunday is +0.030 R, Saturday the best bucket of the whole table at
+0.334 R and t = +2.94, Thursday the one significantly good day at
+0.090 R and t = +4.58 with all three strategies agreeing, Tuesday
flat, and Monday now the worst day at t_diff = -2.51. Every day that
stands out on one sample stands out the other way, or not at all, on
the next. No bucket passes the first clause on both samples; the
nearest, Wednesday, reads -0.066 R then -0.007 R.

The weekend reading deserves one more word because it had a story
ready — thin crypto liquidity, wider effective spreads, the bot's own
audit table being a weekday table (section 107). The older sample
refuses the story: the same thin Sunday book was profitable for two
years. With 51 and 91 Saturday trades the sign is not even stable
within a sample. This is section 51's dissolve on the last calendar
axis; the weekday is a non-lever, section 7's refusal stands as the
rule, and no calendar filter is built in. Nothing changes.

## 176. The variance ratio of the month before: the first stable sign in the series, reversed on the live book

The router gates on ADX, a 14-bar directional statistic; whether the
instrument's own returns had been trending or mean-reverting on the
month scale had never been read. `scripts/variance_ratio_gate.py`
replays the three live 1h trend strategies on the router-passed path
at the 2-ATR stop, venue minimum, live widening rule, gap-aware stop
booking and the commodity short block over all 26 tradeable
instruments and tags every trade with the Lo-MacKinlay variance ratio
at q = 24 of the 1h log returns over the 720 bars before the signal
bar: the variance of overlapping 24-bar return sums over 24 times the
variance of 1-bar returns. VR above 1 means positively autocorrelated
(trending) returns, below 1 mean-reverting ones; nothing after the
signal is seen and the first month of each window carries no feature.
Preregistered: the primary split is VR < 1 against VR >= 1, quartile
edges fixed on the recent year (0.805 / 0.914 / 1.046) and applied
unchanged to the two years before, and a bucket is blocked only if
significantly negative at t < -2 on both disjoint samples and its
difference to the rest holds at |t| > 2 with the same sign on both.

| VR(24) of the prior month at the 1h signal | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| mean-reverting (VR < 1) | 2,679 / -0.028 / -1.72 / +0.026 / +0.93 | 5,535 / **+0.038** / **+3.52** / +0.052 / **+2.79** |
| trending (VR >= 1) | 1,283 / **-0.054** / **-2.34** / -0.026 / -0.93 | 3,083 / -0.014 / -0.94 / -0.052 / **-2.79** |
| strongly mean-reverting (VR < 0.8) | 954 / +0.019 / +0.70 / +0.072 / **+2.35** | 2,193 / **+0.050** / **+3.04** / +0.041 / **+2.11** |
| strongly trending (VR >= 1.2) | 301 / -0.078 / -1.80 / -0.046 / -1.00 | 1,036 / -0.040 / -1.57 / -0.067 / **-2.48** |
| below 0.805 | 991 / +0.029 / +1.10 / +0.087 / **+2.85** | 2,282 / **+0.049** / **+3.06** / +0.041 / **+2.12** |
| 0.805–0.914 | 989 / **-0.078** / **-2.91** / -0.056 / -1.81 | 1,885 / **+0.067** / **+3.54** / +0.061 / **+2.85** |
| 0.914–1.046 | 991 / **-0.064** / **-2.48** / -0.037 / -1.23 | 1,974 / -0.019 / -1.01 / -0.050 / **-2.33** |
| above 1.046 | 991 / -0.032 / -1.21 / +0.006 / +0.19 | 2,477 / -0.014 / -0.82 / -0.047 / **-2.33** |
| upper half (VR >= 0.914) | 1,985 / **-0.048** / **-2.60** / -0.023 / -0.89 | 4,451 / -0.016 / -1.29 / -0.073 / **-4.16** |
| book, whole | 3,962 / -0.036 / -2.74 | 8,618 / +0.019 / +2.16 |

Two thirds of the signals fire after a mean-reverting month on either
sample, and those signals do better than the rest on both: the
difference has the same sign on both samples for every cut in the
table — the primary split, the strong tails, the quartiles, the upper
half — which no signal-bar feature in sections 110 to 116 and 172 to
175 had managed. On the two years before, the mean-reverting half is
+0.038 R at t = +3.52 and the trending half loses 0.052 R more per
trade at t = -2.79; on the recent year the same half of the book
holds up at -0.028 R against -0.054 R for the trending one. In total
R the trending half cost 69 R on the recent year and 44 R on the
older sample; a block would have moved the book from -143 R to -74 R
and from +165 R to +209 R.

And it still fails the bar, on both samples, by opposite clauses. On
the recent year the trending half is significantly negative on its
own (t = -2.34) but so is the whole book, and its gap to the rest is
0.9 standard errors; on the older sample the gap is clear (t = -2.79)
but the bucket itself is only -0.014 R at t = -0.94. The upper half
reads the same way (t = -2.60 then -1.29; t_diff = -0.89 then -4.16).
The only cut that clears the difference clause on both samples, the
lowest quartile against the rest (+2.85 and +2.12), asks to keep one
trade in four and drop the other three, and the dropped three quarters
are profitable on the older sample (+0.008 R, +53 R in total): the
book would have gone from +165 R to +112 R there. The bar's first
clause exists for that case.

Because the sign was stable, the feature was read on a third,
independent sample before it was put down: `scripts/variance_ratio_journal.py`
reconstructs the same VR at the signal bar of every closed live trade
of the three 1h trend strategies from the 1h history and takes the
realised R at the actual fill against the booked stop, as section 115
did for the 4h ADX.

| VR(24) at the signal, live journal | n / E[R] / t / diff vs rest / t |
|---|---|
| mean-reverting (VR < 1) | 154 / **-0.129** / -1.65 / -0.306 / **-2.23** |
| trending (VR >= 1) | 82 / **+0.177** / — / +0.306 / **+2.23** |
| above 1.046 | 70 / +0.240 / +1.94 / +0.374 / **+2.59** |
| since 2026-08-24 (forward) | 33 / +0.006; VR < 1: 16 / -0.098; VR >= 1: 17 / +0.104 |
| all closed | 236 / -0.023 / -0.35 |

The live book says the opposite. Its 154 signals after a
mean-reverting month lost 0.13 R each and its 82 after a trending
month made 0.18 R, a gap of 0.31 R at t = -2.23 against the history's
direction, on donchian_breakout (t = -2.08) and turtle_breakout
(t = -1.39) alike; the 33 forward trades since the router floor lean
the same way. Three samples, two signs: the stable sign of the two
history samples was the recent regime and its predecessor agreeing,
not a property of the signal. This is section 115 once more — the
4h-ADX split also travelled through two history samples and reversed
in the journal. No filter is built in. The variance ratio joins the
signal-bar features as a non-lever; nothing changes.

## 177. Spread mean reversion between related instruments: negative on both samples, and half the signals cannot pay two spreads

Section 7 tested multi-timeframe confirmation, volatility filters, a
BTC lead and cross-sectional relative strength as structurally
different signal sources; a relative-value bet between two related
instruments was never among them, and the retired single-instrument
mean-reversion family of section 2 asked a different question.
`scripts/pairs_spread_mr.py` opens the family once, with the rule
fixed before the data were seen: nine spreads (Brent/WTI, gold/silver,
US500/US100, US500/US30, DE40/EU50, DE40/FR40, EURUSD/GBPUSD,
AUDUSD/NZDUSD, BTC/ETH); z-score of the log ratio of the closes
against its causal 240-bar mean and standard deviation; entry when the
close crosses |z| = 2, one position per spread; exit at the close when
z crosses back through zero (target), when |z| reaches 3.5 (stop) or
after 48 bars (leash). Risk is the log-ratio distance from the entry
to the stop level, both legs are charged their audited round-trip
spread, and a signal whose cost exceeds the live 10 % ceiling is
cost-skipped as the bot would skip it. Acceptance: pooled E[R] > 0 at
t > 2 on both disjoint samples.

| spread | last 365 d: n / E[R] / t / timeout % / cost-skipped | prior 730 d: same |
|---|---|---|
| OIL_BRENT / OIL_CRUDE | 13 / -0.293 / -0.80 / 62 / 69 | 0 / — / — / — / 224 |
| GOLD / SILVER | 41 / -0.361 / -1.68 / 66 / 1 | 76 / +0.065 / +0.55 / 68 / 14 |
| US500 / US100 | 43 / -0.080 / -0.44 / 81 / 0 | 78 / -0.067 / -0.53 / 77 / 2 |
| US500 / US30 | 42 / +0.087 / +0.37 / 52 / 1 | 77 / +0.025 / +0.14 / 62 / 2 |
| DE40 / EU50 | 32 / -0.231 / -1.09 / 72 / 16 | 64 / -0.054 / -0.35 / 78 / 56 |
| DE40 / FR40 | 35 / **-0.427** / **-2.08** / 91 / 0 | 79 / +0.090 / +0.59 / 71 / 2 |
| EURUSD / GBPUSD | 37 / +0.000 / +0.00 / 73 / 9 | 74 / -0.018 / -0.12 / 70 / 19 |
| AUDUSD / NZDUSD | 41 / -0.065 / -0.39 / 80 / 27 | 55 / -0.148 / -1.38 / 89 / 98 |
| BTCUSD / ETHUSD | 39 / +0.132 / +0.67 / 69 / 47 | 94 / **-0.348** / **-2.77** / 74 / 45 |
| pooled | 323 / **-0.118** / -1.69 / 72 / 170 | 597 / **-0.062** / -1.23 / 73 / 462 |

The family loses on both samples, 0.12 R per trade on the recent year
and 0.06 R on the two years before, and no spread is positive on both
with any weight: the two that look best on one sample (BTC/ETH at
+0.13 R, DE40/FR40 at +0.09 R) are the two significantly negative
ones on the other. Three in four trades end at the leash without the
ratio having come back to its mean, so the bet is mostly a 48-bar
hold of a random spread paying two round trips. And a third of the
recent year's signals and 44 % of the older sample's never trade at
all because two audited spreads against a 1.5-sigma risk exceed the
10 % cost ceiling — Brent/WTI on the older sample fires 224 times and
trades never, the FX cross AUDUSD/NZDUSD skips 98 of 153. The vise of
section 23 closes twice as hard on a two-legged trade.

No spread strategy is built in. The relative-value family joins the
structural sources of section 7 as tested and negative; nothing
changes.

## 178. Market breadth at the signal: with the market was the losing side one year and the winning side the next

Peer confirmation (section 116) asked whether same-class instruments
broke out together and trend alignment (section 173) whether the
instrument sat beyond its own EMA(200); neither read the market as a
whole. `scripts/market_breadth_gate.py` replays the three live 1h
trend strategies on the router-passed path at the 2-ATR stop, venue
minimum, live widening rule, gap-aware stop booking and the commodity
short block over all 26 tradeable instruments and tags every trade
with the directional breadth at its signal bar: the mean over the
other 25 instruments of sign(close − EMA200 of 1h closes), each
carried forward from its latest bar at or before the signal,
multiplied by the signal's direction, so +1 means the whole market
already leans the signal's way and −1 that all of it leans against.
Preregistered: the primary split is breadth ≥ 0 against < 0, quartile
edges fixed on the recent year (−0.12 / 0.20 / 0.44) and applied
unchanged to the two years before, block only at t < −2 on both
disjoint samples with |t| > 2 for the difference to the rest and the
same sign on both.

| directional breadth at the 1h signal | last 365 d: n / E[R] / t / diff vs rest / t | prior 730 d: same |
|---|---|---|
| against the market (< 0) | 1,418 / +0.023 / +1.12 / +0.077 / **+2.97** | 3,001 / -0.017 / -1.19 / -0.050 / **-2.81** |
| strongly against (< −0.5) | 323 / -0.015 / -0.36 / +0.015 / +0.36 | 691 / -0.017 / -0.58 / -0.037 / -1.17 |
| strongly with (≥ 0.5) | 1,033 / **-0.108** / **-4.22** / -0.103 / **-3.53** | 2,246 / +0.035 / +1.91 / +0.025 / +1.19 |
| below −0.12 | 990 / +0.028 / +1.14 / +0.074 / **+2.57** | 2,098 / -0.020 / -1.16 / -0.047 / **-2.40** |
| −0.12 to 0.20 | 916 / +0.012 / +0.46 / +0.052 / +1.72 | 2,001 / +0.005 / +0.27 / -0.015 / -0.74 |
| 0.20 to 0.44 | 1,097 / -0.013 / -0.49 / +0.022 / +0.74 | 1,976 / +0.007 / +0.38 / -0.012 / -0.60 |
| above 0.44 | 1,395 / **-0.109** / **-4.91** / -0.118 / **-4.39** | 2,974 / **+0.056** / **+3.53** / +0.059 / **+3.13** |
| book, whole | 4,398 / -0.029 / -2.15 | 9,049 / +0.016 / +1.83 |

Two thirds of the signals fire with the market already leaning their
way, and on the recent year those were the losers: the top quartile
lost 0.109 R per trade at t = −4.91, its gap to the rest held at
t = −4.39, all three strategies agreed (donchian_breakout −0.126 R at
t = −3.43, turtle_breakout −0.114 R at t = −2.87, keltner_breakout
−0.086 R at t = −2.17), and the signals against the market were the
only profitable ones at +0.023 R with t_diff = +2.97. Read alone, that
is a late-entry story with a mechanism attached — when everything has
already moved, the breakout is the last leg. On the two years before
the same top quartile is the best bucket of the table, +0.056 R at
t = +3.53 and t_diff = +3.13, again on all three strategies, and the
signals against the market are the losers at t_diff = −2.81. Every
cut flips; no bucket passes the first clause on both samples.

This is the fourth cross-sectional or regime feature to reverse
between the samples (sections 114, 116, 172, 176), and the shape is
the one the series keeps finding: the recent year punished breakouts
that fired late into a move the market had already made, and the two
years before rewarded them. Which regime the next year brings is not
something the signal bar can tell. No breadth filter is built in;
market breadth joins the non-levers, and nothing changes in the
trading rules.

Because the price history's own features have now all dissolved on a
second sample, the run also opened the one signal source the history
cannot supply. The venue reports its clients' positioning per market
(`GET /api/v1/clientsentiment`, long percentage per instrument); the
bot now records it on every heartbeat, one line per instrument in
`data/sentiment_samples.jsonl`, a minute or two before each hourly
signal bar, next to the spread samples of section 108. Nothing reads
it yet; once the journal holds enough trades with a sample beside
them, the split will be read the same way as the features above. That
is a data collection, not a lever, and it changes no trading decision.

## 179. Index entries at the hour they fire: the off-hours spread costs half a percent of risk, and the off-hours trades are not the bad ones

Section 107 charged a single 04:36 UTC snapshot for every off-hours
index entry and left one cost correction open: sample the venue's
spreads by hour, then charge them. The heartbeat sampler of section
108 has since recorded two full days, one to three quotes per
instrument and hour. The median half-spread by UTC hour, in thousandths
of a percent of mid, reads DE40 3 by day and 8–16 by night, FR40 5
and 27–60, UK100 5 and 14, EU50 12 and 16–24, HK50 10 inside its
session and 59–60 outside, while the three US indices and J225 barely
move (US30 2 at every hour, J225 8). `scripts/index_hour_costs.py`
replays the three live 1h trend strategies on the router-passed path
at the 2-ATR stop, venue minimum, live widening rule, gap-aware stop
booking and the 10 % ceiling over the nine index instruments, charges
every trade the median sampled half-spread of its signal hour, and
tags it cash-hours or off-hours by the underlying's session in UTC
(Europe 07–16, US 14–20, HK50 02–08, J225 00–06). Preregistered: the
off-hours bucket is blocked only if significantly negative at t < −2
on both disjoint samples and |t| > 2 against the cash-hours rest with
the same sign on both.

| index book, nine instruments | last 365 d: n / E[R] / cost R / Σ R | prior 730 d: same |
|---|---|---|
| audited table (what the backtest charges) | 1,665 / -0.107 / 0.0101 / -179 | 3,422 / +0.041 / 0.0104 / +140 |
| sampled hour table | 1,660 / -0.113 / 0.0147 / -187 | 3,416 / +0.037 / 0.0142 / +125 |
| of which off-hours entries | 1,033 / -0.109 (t = -4.01) / 0.0172 | 2,019 / +0.041 (t = +2.16) / 0.0167 |
| of which cash-hours entries | 627 / -0.119 (t = -3.46) / 0.0106 | 1,397 / +0.031 (t = +1.38) / 0.0106 |
| off-hours − cash-hours | +0.011 R, t = +0.24 | +0.010 R, t = +0.35 |

The correction is real and small. Charging the hour the signal fires
instead of the daytime table raises the index book's cost from 1.0 %
to 1.5 % of risk per trade, 0.0045 R, and costs it 8 R on the recent
year and 15 R on the older one — the number section 107 estimated from
its one snapshot (0.010–0.013 R) was about twice too large, because
the snapshot fell on the widest hour. Two thirds of the index entries
fire outside cash hours on either sample, and at their true cost they
are not the losing side: on the recent year they lose 0.109 R against
0.119 R for the cash-hours entries, on the older sample they make
0.041 R against 0.031 R, and neither gap is a quarter of a standard
error. Per instrument the picture is noise in both directions: FR40's
off-hours entries are its better half on both samples (+0.082 R and
+0.090 R) despite paying 3.6–5 % of risk in spread, EU50's are its
worse half on both, and the significant cells are single strategies
on single samples (keltner_breakout off-hours -0.19 R on the recent
year, +0.07 R on the older).

What the table does show is section 137 again: the index book as a
whole loses 0.11 R per trade on the recent year at t = −4.5 and makes
0.04 R on the two years before at t = +2.9, with every one of the nine
instruments on the same side of zero as the book — the bull years paid
the index breakouts and the last year took it back, at any hour and
under either cost model. No hour filter is built in; the off-hours
block fails the bar on the older sample outright. The one open cost
correction of section 107 is closed as measured: the shared simulator
keeps the daytime table, which understates the index book's cost by
half a percent of risk, too little to move any ranking the selector
has produced. Nothing changes.

## 180. A close-confirmed stop: the same expectancy, one trade in eight losing more than its risk

The live stop sits at the broker two ATR from the entry and fills on
a touch; section 131 read that the barriers lose and the drift wins,
and the one stop variant never measured was a close-confirmed stop —
the position is closed at the bar's close only if that close lies
beyond the 2-ATR level, so a wick through the level that closes back
inside no longer stops the trade out. `scripts/close_based_stop.py`
books three rules on the same bar path of every trade of the three
live 1h trend strategies on the router-passed path over all 26
tradeable instruments (venue minimum, live widening rule, gap-aware
booking, commodity short block): the live touch stop; A, the
close-confirmed 2-ATR stop behind a broker-side catastrophe stop at
3 ATR on touch; B, the close-confirmed stop alone, an upper bound no
bot should run. The target stays intrabar at 1.5 R and the leash at
24 bars. The trade set is fixed by the live rule, so every difference
is paired per trade. Acceptance, fixed before the data were seen:
better than the live rule at paired t > 2 on both disjoint samples.

| rule | last 365 d (n = 4,503): E[R] / diff vs live / paired t / stop % / trades below −1 R | prior 730 d (n = 9,156): same |
|---|---|---|
| live: touch stop at 2 ATR | -0.031 / — / — / 26 / 0.4 % | +0.014 / — / — / 22 / 0.1 % |
| A: close-confirmed 2 ATR, touch at 3 ATR | -0.029 / +0.002 / +0.53 / 21 / **13.7 %** | +0.014 / -0.000 / -0.01 / 18 / **12.2 %** |
| B: close-confirmed 2 ATR only | -0.030 / +0.002 / +0.34 / 20 / **13.4 %** (worst -3.4 R) | +0.015 / +0.001 / +0.27 / 18 / **11.9 %** (worst -4.4 R) |

Confirming the stop on the close takes a fifth of the stop-outs away
— 26 % of trades stop under the live rule, 21 % under A — and the win
rate rises a point and a half, and none of it reaches the expectancy:
+0.002 R on the recent year at paired t = +0.53, nothing on the two
years before, and the three strategies disagree on the sign in both
samples (keltner_breakout +0.009 R then -0.005 R, turtle_breakout
+0.001 R then +0.004 R). The wicks that the touch stop pays for and
the close stop forgives are worth exactly what the bars that close
through the level and keep going cost: the stop-outs saved come back
as losses booked at the close, deeper than one R. That is where the
rule's price sits. Under the live rule one trade in 250 loses more
than its risk, and only by a gap; under either close-confirmed
variant it is one in eight, at up to 1.6 R behind the 3-ATR
catastrophe stop and up to 4.4 R without it.

A rule that leaves the mean where it was and turns the 1-R loss
ceiling into a distribution with a fat left tail is not a lever but a
weaker risk limit. Not built in; the stop stays at the broker, on
touch, at 2 ATR. Nothing changes.

## 181. Two-bar confirmation: the strongest split the series has found, and the rule that acts on it keeps almost none of it

The pullback entry (section 53), the retest (55) and the next-open
timing (91) each moved where the entry sits; none asked whether a
breakout that is still beyond its level one bar later is a different
trade from one that has already slipped back inside.
`scripts/two_bar_confirmation.py` replays the three live 1h trend
strategies on the router-passed path at the 2-ATR stop, venue minimum,
live widening rule, gap-aware stop booking and the commodity short
block over all 26 tradeable instruments, and tags every live trade by
whether the close of the bar after the signal still lies beyond the
level the signal broke (the prior 20/55-bar extreme, or the Keltner
band). Two readings: the live book split confirmed against
unconfirmed, and the variant that enters at that later close with its
own 2-ATR stop, compared paired per signal against the live rule
(a signal the variant does not take contributes zero).

| | last 365 d | prior 730 d |
|---|---|---|
| live book | 4,521 / -0.035 R / Σ -158 | 9,159 / +0.014 R / Σ +126 |
| live, unconfirmed next close (29–30 %) | 1,290 / **-0.291** / **t = -14.6** / Σ -375 | 2,747 / **-0.217** / **t = -15.1** / Σ -597 |
| live, confirmed next close (70–71 %) | 3,231 / **+0.067** / **t = +4.6** / Σ +217 | 6,412 / **+0.113** / **t = +11.0** / Σ +723 |
| unconfirmed − confirmed | -0.358 / **t = -14.5** | -0.330 / **t = -18.7** |
| variant: enter at the next close | 3,791 / -0.035 / Σ -133 | 7,523 / +0.032 / Σ +238 |
| **variant − live, paired per signal** | **+0.0168 / t = +1.95** | **+0.0094 / t = +1.54** |

The split itself is the largest and most consistent this project has
measured. Where every signal-bar feature of sections 110 to 180 either
reversed between the samples or sat inside two standard errors, this
one holds its sign and its size on both: a breakout whose next bar
closes back inside the level loses 0.22–0.29 R, at t beyond 14, on all
three strategies (donchian_breakout -0.284 and -0.207, turtle_breakout
-0.297 and -0.197, keltner_breakout -0.289 and -0.248), while the
confirmed remainder makes 0.07–0.11 R. It is not a regime artefact and
it is not a selection: the whole live book's loss on the recent year
is the unconfirmed third, and the confirmed two thirds are profitable
on both samples.

None of that is available. The confirmation exists only one bar after
the signal, so the only rule that can use it enters at that later
close — and the price of the delay eats the benefit almost exactly.
Decomposed per signal: skipping the unconfirmed signals adds +354 R on
the recent year and +568 R on the older one, and entering the
confirmed ones a bar late costs -279 R and -482 R (the delayed entries
go from +0.064 R to -0.027 R and from +0.114 R to +0.035 R, at
t = -11.5 and -14.5). What remains is +0.017 R and +0.009 R per
signal, at paired t = +1.95 and +1.54 — the same sign twice, and short
of the preregistered t > 2 on either sample. Pooled over both samples
it reads +0.012 R at t = +2.38, which is the wrong test: pooling two
regimes is what sections 114 and 176 were designed to refuse.

The live journal, read as the third sample
(`scripts/two_bar_confirmation_journal.py`, the level and the next
close reconstructed for each of 216 closed trades, realised R at the
actual fill against the simulated variant): the same shape, no power.
The 110 signals whose next bar closed back inside lost 0.457 R each,
the variant's 106 entries made +0.022 R, and paired the variant is
+0.048 R per signal at t = +0.76. The 31 forward trades since the
router floor go the other way (-0.102 R, t = -0.66).

Not built in: the rule fails the preregistered bar on both samples.
What is worth keeping is the decomposition, because it is the first
time the series has located where the loss sits rather than how it
splits — the failed breakout, one bar old. Acting on it by delaying
every entry pays for the information with the very move the confirmed
breakouts make in that bar. A rule that keeps the entry where it is
and uses the confirmation as an exit instead is the obvious next
question, and it is not this one. Nothing changes.

## 182. The failed-breakout exit: the price of cutting is a constant, what the cut saves is not

Section 181 located the loss and showed that acting on it as an entry
filter pays for the information with a delayed entry. This asks the
other half: keep the entry where the live rule has it and use the same
confirmation as an exit. `scripts/failed_breakout_exit.py` books three
rules on the same bar path of every live trade of the three 1h trend
strategies on the router-passed path over all 26 tradeable instruments
(2-ATR stop, venue minimum, live widening rule, gap-aware booking,
commodity short block), so the trade set is identical and every
difference is paired per trade: the live rule; A, close the position
at the close of the bar after the signal if that close lies back
inside the level; B, the same but only if the position is at a loss
there. Neither variant can lose more than the live rule's 1 R —
it exits strictly earlier, at a close the stop has not reached.

B is A. Every unconfirmed close is also a losing one, because the
entry sits at the signal close, which was beyond the level by
construction; the two rules book identically on all 13,684 trades of
both samples, and the "leave a profitable slip alone" clause never
fires.

| | last 365 d (n = 4,528) | prior 730 d (n = 9,156) |
|---|---|---|
| live | -0.038 R / Σ -171 / 45.0 % wins | +0.014 R / Σ +124 / 47.7 % wins |
| A = B: cut on the unconfirmed close | -0.021 R / Σ -93 / 36.1 % wins | +0.011 R / Σ +103 / 37.2 % wins |
| **paired difference** | **+0.0172 / t = +3.17** | **-0.0023 / t = -0.55** |
| trades cut | 1,256 (28 %) | 2,686 (29 %) |
| booked at the cut | **-0.209 R** | **-0.207 R** |
| what those trades would have become | -0.271 R | -0.199 R |
| of the cut trades, share that would have ended positive | 32 %, at +0.544 R | 36 %, at +0.621 R |
| the trades never touched | 3,272 at +0.052 R | 6,470 at +0.102 R |

The price of the rule is a constant. Cutting a failed breakout at the
next close books -0.209 R on the recent year and -0.207 R on the two
years before — a fifth of the risk, paid on 28-29 % of the book, in
both regimes, which is what a one-bar adverse move against a 2-ATR
stop mechanically costs. What the cut buys is not constant at all. On
the recent year those trades went on to -0.271 R, so cutting saved
0.062 R each and 78 R in total, at paired t = +3.17. On the two years
before they went on to -0.199 R — slightly less bad than the cut
itself — so cutting cost 0.008 R each and 21 R, at t = -0.55. The sign
flips, and the preregistered bar (paired t > 2 on both samples) fails
on the second one.

What the rule does in both regimes is turn a third of its cut trades
into certain small losses: 32-36 % of them would have ended positive,
at +0.54 R and +0.62 R, and the win rate falls nine points under
either sample. That is the shape of every exit rule this log has
measured (sections 52, 132, 174, 180) — the barrier is moved, the
distribution narrows, and whether the mean improves depends on the
year. The recent year kept punishing the failed breakout after the
first bar; the two before let it recover just enough.

Not built in. The exit stays as it is. Sections 181 and 182 together
close the confirmation question from both sides: the split is real,
large and stable, and neither the entry nor the exit that acts on it
survives a second sample. What is stable is the cut price, -0.21 R,
which is the toll any one-bar reaction rule pays here.

## 183. The ATR lookback behind the stop: flat, because the venue floor overrides it on four trades in five

Section 1's grid varied the stop multiple, the reward:risk, the hold
and the ADX threshold; section 58 later found the Donchian lookback
had been left out of it, and the ATR period behind the stop distance
is the same omission. The stop is 2 x ATR(14) and the 14 had never
been moved. `scripts/atr_period_sweep.py` sweeps it at 7 / 14 / 28 /
56 bars for the stop distance only — the signals are generated exactly
as live, so the Keltner band keeps its own ATR(14) and nothing but the
stop, the target derived from it and the resulting size changes. All
four variants are booked on the same bar path of every signal,
occupancy follows the live variant, and a signal counts only where
every variant prices inside the 10 % cost ceiling (144 and 82 signals
dropped).

| ATR period | last 365 d (n = 4,488): E[R] / diff vs 14 / paired t / floor binds | prior 730 d (n = 9,138): same |
|---|---|---|
| 7 | -0.0357 / +0.0015 / +0.39 / 76 % | +0.0164 / +0.0022 / +0.89 / 80 % |
| **14 (live)** | **-0.0372** / — / — / 77 % | **+0.0142** / — / — / 82 % |
| 28 | -0.0356 / +0.0015 / +0.47 / 79 % | +0.0145 / +0.0003 / +0.15 / 83 % |
| 56 | -0.0336 / +0.0036 / +0.90 / 81 % | +0.0115 / -0.0027 / -1.04 / 85 % |

Nothing moves. The widest paired difference in the pooled table is
0.0036 R at t = +0.90, the two candidates disagree between the samples
(56 is the best on the recent year and the worst on the older one, 7
the reverse), and per strategy the only cell above one standard error
is keltner_breakout at period 7 on the recent year (+0.011 R,
t = +1.89) which reads +0.002 R at t = +0.59 on the older sample. The
win rate is flat to a tenth of a point across every variant.

The reason is in the last column. The venue's minimum stop distance —
the project's own 1.05 % floor of section 135 — is wider than 2 ATR on
76 to 85 % of the trades, so on four fifths of the book the ATR does
not set the stop at all and the period behind it is inert by
construction. What the sweep really measures is the fifth of the book
where the ATR still binds, and there it is noise. The share rises with
the lookback (76 % to 81 %, 80 % to 85 %) because a longer average is
smoother and dips under the floor more often, which is why period 56
converges towards the pure floor rule.

Period 14 stays. The grid's last unswept axis is swept and flat, and
the finding worth carrying is the mechanical one: this system's stop
is a fixed 1.05 % of price on most trades, not a volatility-adaptive
distance, and no ATR parameter can change that while the floor stands.
Nothing changes.

## 184. Removing the target: the barrier the RR sweep never reached, and the drift that does not pay for it

Section 131 measured the barrier pair and found it losing: at a 1.5 R
target the stop is hit nearly twice as often on both samples, so the
two together book -0.06 R and -0.04 R per trade, while the 24-bar
drift of the trades that reach neither barrier is the only component
in the black (+0.055 and +0.068 R at t = 4.4 and 7.6). It then closed
the target question by inference from the RR 1.0-3.0 sweep of sections
63 and 124. Inference is not measurement: RR 3.0 still places a
barrier, and the rule section 131 actually implies is no target at
all, the stop and the 24-bar leash alone. That variant had never been
booked.

`scripts/no_target_sweep.py` books four rules on the same bar path of
every signal of the three live 1h trend strategies — the live 1.5 R
target, 3.0 R, 6.0 R and none — with occupancy on the live variant so
the trade set is identical and every difference is paired per trade.
The stop is untouched in all four, so the 1 R loss limit stands and
only the upside barrier moves.

| target | last 365 d (n = 4,524): E[R] / diff vs 1.5 / paired t | prior 730 d (n = 9,161): same | target % | timeout % | win % |
|---|---|---|---|---|---|
| **1.5 R (live)** | **-0.0374** / — / — | **+0.0132** / — / — | 13 / 13 | 61 / 64 | 45.1 / 47.7 |
| 3 R | -0.0385 / -0.0011 / -0.17 | +0.0128 / -0.0004 / -0.08 | 4 / 3 | 70 / 73 | 43.6 / 46.1 |
| 6 R | -0.0477 / -0.0103 / -1.35 | +0.0228 / +0.0096 / +1.58 | 0 / 1 | 73 / 76 | 43.3 / 45.9 |
| none | -0.0466 / -0.0092 / -1.09 | +0.0190 / +0.0058 / +0.95 | 0 / 0 | 73 / 77 | 43.3 / 45.9 |

The sign flips between the samples and neither reading comes near the
bar: removing the target costs 0.009 R on the recent year and gains
0.006 R on the older one. Per strategy the same disagreement — on the
recent year all three lose (donchian -0.009, turtle -0.006, keltner
-0.012 R), on the older all three gain (+0.002, +0.003, +0.012 R), and
no cell exceeds t = 1.4.

Two things the table settles. First, 6 R and none are the same rule:
at that distance the barrier is reached on 0 to 1 % of trades, so the
RR sweep of section 63 had in fact already priced the barrier-less
variant at its far end without naming it. Second, the change is
confined to the 13 % of trades that live exits at the target — every
other exit is bit-identical — so the whole effect is what those trades
do afterwards, and it is worth about -0.07 R each on the recent year
and +0.045 R each on the older. The stop share barely moves (26 to 27
and 22 to 23 %): letting a winner run does not walk it back into the
stop, it walks it into the leash, where the 24-bar close pays a little
less than 1.5 R in one regime and a little more in the other.

Section 131's decomposition survives — the drift is the black
component — but it does not follow that the drift is worth harvesting
past 1.5 R. The target stays. Nothing changes.

## 185. A volatility-anchored target: five points of win rate, and the sign of the trade flips between the samples

Section 124 read the reward:risk separately for pinned and ATR-bound
trades and closed with a sentence that was never followed up: the
pinned trades — 78 % of the book at a mean stop of 6.5 ATR — need a
volatility-scaled distance the venue does not offer. It does not offer
it on the stop, where its 1.05 % floor overrides the ATR on four
trades in five (section 183). It offers it on the target. The live
target sits at 1.5 x the stop distance, so on a pinned trade it asks
for roughly 10 ATR of travel inside 24 bars, which is the mechanical
reason 61 % of the book times out.

`scripts/atr_target_sweep.py` anchors the target to the ATR instead —
1.5, 3.0 and 4.5 ATR against the live rule — on the same bar path of
every signal, occupancy on the live variant, differences paired per
trade. One constraint section 124's group-wise RR sweep did not carry:
the venue's minimum distance applies to the target as it does to the
stop, so an ATR target closer than the floor is not placeable and is
clamped to it. On a pinned trade, where the stop *is* the floor, that
caps the variant at RR 1.0 — which is why the 1.5 ATR column below
reports a mean realised reward:risk of 0.95, not 0.75.

| target | last 365 d (n = 4,521): E[R] / diff / paired t | prior 730 d (n = 9,169): same | mean rr | target % | win % |
|---|---|---|---|---|---|
| **live 1.5 R** | **-0.0378** / — / — | **+0.0134** / — / — | 1.50 | 13 / 13 | 45.2 / 47.7 |
| 1.5 ATR | -0.0184 / +0.0193 / **+2.93** | +0.0020 / -0.0114 / **-2.85** | 0.95 / 0.97 | 26 / 24 | 50.1 / 51.1 |
| 3 ATR | -0.0369 / +0.0009 / +0.30 | +0.0072 / -0.0062 / -2.97 | 1.15 / 1.12 | 19 / 20 | 46.0 / 48.5 |
| 4.5 ATR | -0.0384 / -0.0006 / -0.13 | +0.0140 / +0.0006 / +0.18 | 1.44 / 1.36 | 13 / 15 | 44.3 / 47.0 |

This is the first variant in the series to clear t = 2 on a sample —
and it clears it in both directions. The near target is worth +0.019 R
at t = +2.93 on the recent year and -0.011 R at t = -2.85 on the older
one, and the disagreement is not an artefact of one strategy: all
three gain on the first sample (+0.021, +0.019, +0.018 R) and all
three lose on the second (-0.010, -0.016, -0.009 R). The preregistered
bar asks for t > 2 on both. It fails.

What the table does establish is the shape of the trade. Pulling the
target in from 1.5 R to the venue floor doubles the target exits from
13 to 26 %, lifts the win rate by five points to just over 50, and
cuts the timeout share from 61 to 51 % — the leash stops being the
dominant exit. The stop share barely moves (26 to 23 %), so this is
not risk being traded away; it is win size being traded for win
frequency at a realised reward:risk just under 1.0. Whether that trade
pays depends entirely on how far the winners run, and that is the one
thing this book's regime decides: in the older, trending sample the
runners paid for the misses, in the recent year they did not.

The 4.5 ATR column is the control that confirms the reading — it lands
within 0.0006 R of the live rule on both samples, because at that
distance the clamp rarely binds and the rule nearly is the live rule.

The live target stays. The finding to carry is that this system has a
real, measurable win-rate lever on the target distance, worth five
points of hit rate, and that its expectancy sign is regime-dependent
rather than structural — which makes it a candidate for a regime-
conditional rule, not for an unconditional one. Nothing changes.

## 186. The near target by ADX: the gradient runs the wrong way on one sample and vanishes on the other

Section 185 left a conditional candidate: the near target buys five
points of win rate on both samples but pays only on one, and the
obvious suspect was trend strength — a target at the venue floor caps
the runners, so it should pay where they do not run and cost where
they do. The regime router gates trend entries at ADX >= 30, but not
at the same ADX, so the book carries the variation needed to test it.

`scripts/target_by_adx.py` books the live target and the 1.5 ATR
target on the same bar path and reads the paired difference by ADX
bucket at the entry bar. The rule preregistered before the data were
seen: the bucket gradient must carry the same sign on both samples
before any threshold rule is built.

| ADX at entry | last 365 d: n / live E[R] / paired diff / t | prior 730 d: n / live E[R] / paired diff / t |
|---|---|---|
| 30-35 | 2,382 / -0.0075 / +0.0032 / +0.39 | 4,751 / +0.0177 / -0.0110 / -2.17 |
| 35-40 | 1,080 / -0.0345 / +0.0325 / +2.30 | 2,128 / +0.0132 / -0.0187 / -2.47 |
| 40-45 | 480 / -0.0870 / +0.0284 / +1.34 | 995 / -0.0033 / -0.0044 / -0.34 |
| 45-50 | 247 / -0.0993 / +0.0347 / +1.19 | 568 / +0.0344 / +0.0030 / +0.15 |
| 50+ | 341 / -0.1438 / +0.0528 / +1.66 | 729 / -0.0160 / -0.0136 / -0.70 |

The precondition fails, and it fails in the most explicit way
available: on the recent year every bucket is positive, on the older
sample four of five are negative. The near target does not help
selectively at some trend strength and hurt at another — within a
sample it points the same way everywhere, and the direction is set by
which sample you are in. The conditional rules confirm it rather than
rescue it: ADX < 45 reads +0.0125 R at t = +2.11 on the recent year
and -0.0105 R at t = -3.03 on the older one, the same flip section 185
already recorded, now with a threshold bolted on. No rule is built.

Two things are worth carrying out of the table. The hypothesis was
backwards: on the recent year the near target's advantage *grows* with
ADX (+0.003 at 30-35 to +0.053 at 50+), the opposite of capping
runners in a strong trend. And the reason is in the live column beside
it — on the recent year the live rule's expectancy collapses as ADX
rises (-0.008 to -0.144 R), while on the older sample it does not move
with ADX at all (+0.018 to -0.016 R, no ordering). What looks like a
target effect conditioned on trend strength is a high-ADX entry
problem that exists in one sample only, and the near target merely
loses less of it.

The live target stays and no ADX condition is added. The regime
router's own threshold is untouched. Nothing changes.

## 187. An upper ADX bound on trend entries: the strongest single-sample effect the series has produced, and it is one sample wide

The regime router gates trend entries at ADX >= 30 and has no upper
bound. Section 186 found the reason to look for one: on the recent
year the live rule's expectancy falls monotonically with ADX at entry,
from -0.008 R below 35 to -0.144 R above 50. `scripts/adx_ceiling_filter.py`
books the ceiling as a filter at 35 / 40 / 45 / 50 — every signal's
live R against the variant's R, zero where the variant does not trade,
paired per trade, occupancy on the live rule so a filtered trade does
not free its slot and the measurement understates rather than flatters
the filter.

Because the hypothesis came from data already seen, the acceptance
rule was written out before the run and deliberately made asymmetric:
a filter removes trades rather than adding exposure, and one that
helps in one regime and is neutral in the other can never show t > 2
twice. Required were (a) a positive point estimate on both samples,
(b) t > 2 on at least one, (c) no reading below t = -1 on either,
(d) a sign stable across the sweep rather than at one cherry-picked
value, (e) no contradiction from the live journal.

| ceiling | last 365 d: cut n / cut E[R] / paired diff / t | prior 730 d: cut n / cut E[R] / paired diff / t |
|---|---|---|
| ADX < 35 | 2,150 / -0.0709 / +0.0336 / **+3.97** | 4,427 / +0.0051 / -0.0025 / -0.41 |
| ADX < 40 | 1,068 / -0.1074 / +0.0253 / **+4.04** | 2,297 / -0.0010 / +0.0003 / +0.06 |
| ADX < 45 | 587 / -0.1241 / +0.0161 / **+3.39** | 1,302 / +0.0004 / -0.0001 / -0.02 |
| ADX < 50 | 341 / -0.1431 / +0.0108 / **+2.82** | 729 / -0.0158 / +0.0013 / +0.46 |

On the recent year this is the strongest lever the series has
measured: every threshold positive, monotone in severity, t between
2.8 and 4.0, and the cut trades carry -0.07 to -0.14 R each. Criteria
(b) and (c) pass comfortably — the older sample never goes near -1.

(a) and (d) fail together, and they fail in a way worth naming
precisely. On the older sample the filter is not harmful; it is
nothing. All four readings sit between -0.0025 and +0.0013 R at
|t| <= 0.46, so the point estimate's sign there is decided by noise:
positive at two thresholds, negative at two. Picking ADX < 40 or
ADX < 50 because those happen to be the positive two is exactly the
cherry-pick (d) exists to forbid, and without that pick (a) is not
satisfied.

The per-strategy split argues the same way from the other side. On the
older sample turtle_breakout — the only strategy with a positive live
expectancy there (+0.030 R, t = +1.93) — reads a negative difference
at every threshold (-0.013 to -0.004 R), and its cut trades are its
good ones (+0.027 to +0.046 R). The aggregate's near-zero older
reading is not homogeneity; it is keltner's negative high-ADX trades
cancelling turtle's positive ones.

Criterion (e) returns nothing either way: the journal holds 34 closed
trades with a stored entry ADX, and every threshold reads |t| <= 0.46
on them. That is a sample too small to contradict or confirm, and it
is recorded as neither.

No ceiling is added; the router keeps its floor at 30 and no upper
bound. What is now measured rather than suspected: high-ADX trend
entries have been the recent year's worst segment by a wide margin and
carried no penalty at all in the two years before it. That is a
statement about the last twelve months, not about the system. Nothing
changes.

## 188. The third sample settles the ADX ceiling — BUILT IN

Section 187 measured an upper bound on the trend gate and refused it on
its own preregistered terms. The effect was the strongest the series
had produced on the recent year (t up to +4.04) and exactly nothing on
days 366-1,095, where all four thresholds sat between -0.0025 and
+0.0013 R at |t| <= 0.46. With the older sign decided by noise, picking
the two thresholds that happened to read positive would have been the
cherry-pick the acceptance rule existed to forbid.

What section 187 did not have was a third opinion. The venue serves
hourly history well past three years — 600 bars still come back at
1,825 days — so days 1,096-1,825 are available as a sample disjoint
from both others and never read for this question. The decision rule
was fixed before it was fetched: build the ceiling only if the third
sample is positive at every threshold, reaches t > 2 at least once, and
the cut segment carries E[R] <= 0 on all three samples; ship the
mildest threshold that holds its sign on all three, not the one with
the best t.

| ceiling | 365 d: cut E[R] / diff / t | 366-1,095 d: cut E[R] / diff / t | 1,096-1,825 d: cut E[R] / diff / t |
|---|---|---|---|
| ADX < 35 | -0.0709 / +0.0336 / +3.97 | **+0.0051** / -0.0025 / -0.41 | -0.0314 / +0.0151 / +2.42 |
| ADX < 40 | -0.1074 / +0.0253 / +4.04 | -0.0010 / +0.0003 / +0.06 | -0.0333 / +0.0083 / +1.83 |
| ADX < 45 | -0.1241 / +0.0161 / +3.39 | **+0.0004** / -0.0001 / -0.02 | -0.0650 / +0.0087 / +2.58 |
| **ADX < 50** | **-0.1431 / +0.0108 / +2.82** | **-0.0158 / +0.0013 / +0.46** | **-0.0348 / +0.0026 / +1.02** |

The third sample (n = 9,500, live E[R] -0.0230) is positive at all four
thresholds and clears t = 2 at two of them, so the first two conditions
pass. The third does the deciding work: at ADX < 35 and ADX < 45 the
middle sample's cut segment is positive — those thresholds would throw
away trades that made money — and only ADX < 40 and ADX < 50 keep a
non-positive cut on all three. Of those two, 50 is the milder: it
refuses 7-8 % of signals against 25 %.

So the rule that ships is the one the evidence supports rather than the
one that measures best. Entries at ADX >= 50 lost on all three disjoint
samples — -0.143, -0.016 and -0.035 R — and skipping them is paired-
positive on all three. The recent year's -0.143 R is what makes the
lever visible; the other two samples are what make it a rule rather
than a story about the last twelve months. The R sums move from -171.0
to -122.2 (365 d), +111.3 to +122.8 (730 d) and -218.6 to -193.7
(730 d): better on every sample, by construction of the acceptance rule
rather than by selection after the fact.

Implementation: `HURZ_REGIME_ADX_TREND_MAX`, default 50, enforced in
`regime.decide()` — the one function both `autotrade.py` and
`spot_backtest.py` call, so live and simulator cannot diverge. A trend
signal at or above the ceiling returns `blocked` with regime
`overextended`; the mean-reversion branch and the neutral branch are
untouched, and the fail-closed path for a missing ADX is unchanged. No
risk control is loosened: the rule only removes entries.

What this does not do is close the gap to the objective. On the recent
year the book still reads -0.0270 R a trade with the ceiling in place,
against -0.0377 without it. The ceiling makes a losing year less
losing; it does not make it a winning one.

## 189. The pin degree as an exclusion: the hypothesis was backwards, and the worst segment is the one the venue never touched

A trade pinned at 10 ATR carries its 1.5 R target 15 ATR away, which
inside a 24-bar leash is unreachable by construction — it can only stop
out or time out near zero while paying its full spread either way. The
reasoning is structural rather than regime-dependent, so if it held the
penalty would appear in every sample. `scripts/pin_degree_filter.py`
tests it as an exclusion at 4 / 6 / 8 / 10 ATR against the live rule,
on all three disjoint samples at once, under the acceptance rule that
settled the ADX ceiling in section 188.

This is also the first measurement taken against the post-188 system:
the ceiling is live in `gate()`, so the samples are 4,254 / 8,541 /
8,885 trades against the 4,530 / 9,169 / 9,500 of section 187. The R
sums confirm the build did what it was measured to do — the recent
year now reads -122.4 R where section 188 predicted -122.2, and the
small residual is the occupancy the filtered trades hand back.

| cap | 365 d: cut E[R] / diff / t | 366-1,095 d | 1,096-1,825 d |
|---|---|---|---|
| pin <= 4 | -0.0192 / +0.0105 / +1.68 | +0.0137 / -0.0082 / -1.67 | +0.0057 / -0.0028 / -0.62 |
| pin <= 6 | -0.0057 / +0.0022 / +0.52 | +0.0186 / -0.0072 / -2.13 | +0.0075 / -0.0020 / -0.71 |
| pin <= 8 | +0.0058 / -0.0015 / -0.50 | +0.0051 / -0.0012 / -0.52 | +0.0238 / -0.0030 / -1.71 |
| pin <= 10 | +0.0094 / -0.0014 / -0.69 | +0.0038 / -0.0005 / -0.33 | +0.0528 / -0.0027 / -2.59 |

No threshold is positive on more than one sample, none reaches t > 2 in
the positive direction, and on the two older samples every cut segment
is *positive* — the rule would be discarding trades that made money.
Condition (c) alone kills all four. Nothing is built.

The reason is in the bucket table, and it points the other way:

| pin at entry | 365 d: n / E[R] / t | 366-1,095 d | 1,096-1,825 d |
|---|---|---|---|
| **2-3 ATR** | 1,484 / **-0.0599** / -2.16 | 2,455 / **-0.0105** / -0.47 | 3,390 / **-0.0784** / -4.22 |
| 3-5 ATR | 822 / -0.0131 / -0.43 | 1,915 / +0.0319 / +1.66 | 2,163 / +0.0296 / +1.59 |
| 5-8 ATR | 877 / -0.0330 / -1.69 | 2,236 / +0.0342 / +2.65 | 2,221 / -0.0147 / -1.18 |
| 8-12 ATR | 751 / +0.0054 / +0.36 | 1,366 / +0.0041 / +0.33 | 958 / +0.0283 / +1.87 |
| 12+ ATR | 320 / +0.0066 / +0.42 | 569 / +0.0076 / +0.49 | 153 / -0.0043 / -0.12 |

The worst segment on all three samples is the least pinned one — the
2-3 ATR band, where the venue floor barely binds and the stop is very
nearly the 2 x ATR the strategy asked for. Those are the trades whose
volatility is large enough relative to price that 2 ATR already clears
the 1.05 % floor, and they read -0.060, -0.011 and -0.078 R. The
heavily pinned trades this run set out to exclude are, if anything, the
better half.

That inverts section 124's reading, which had the ATR-bound group
slightly ahead of the pinned one on a single sample and without the
ADX ceiling in place. It also inverts the intuition behind this run:
the unreachable target does not cost what it looks like it should,
because a trade that cannot reach its target also cannot be dragged to
its stop by ordinary noise — the same wide distance protects both
sides, and what is left is the drift.

No cap is added. The finding worth carrying is the segment itself,
consistent across three disjoint samples in the direction opposite to
the one tested, and it is the next run's question rather than this
one's conclusion.

## 190. The least-pinned band: the segment the venue floor never touched — BUILT IN

Section 189 tested the opposite rule and produced this one. Excluding
the most heavily pinned trades failed on every count, but its bucket
table showed the worst segment of the book is the *least* pinned one —
the 2-3 ATR band, where volatility alone already clears the venue's
1.05 % minimum, so the stop is very nearly the 2 x ATR the strategy
asked for. That band read -0.060, -0.011 and -0.078 R across the three
samples then available.

Those three samples produced the hypothesis, so they could not also
confirm it. The venue serves hourly history past seven years, so days
1,826-2,555 are a fourth disjoint sample, fetched for the first time
for this question, and the acceptance rule — section 188's, extended by
one sample — was fixed in `scripts/pin_floor_filter.py` before the
fetch: positive on all four, t > 2 on at least one, cut segment
E[R] <= 0 on all four, ship the mildest qualifying floor.

| floor | 365 d: cut E[R] / diff / t | 366-1,095 d | 1,096-1,825 d | 1,826-2,555 d (unseen) |
|---|---|---|---|---|
| pin >= 2.5 | -0.0831 / +0.0237 / +2.67 | -0.0295 / +0.0067 / +1.17 | -0.0950 / +0.0286 / +4.48 | +0.0066 / **-0.0021** / -0.31 |
| **pin >= 3.0** | **-0.0622 / +0.0216 / +2.23** | **-0.0107 / +0.0031 / +0.48** | **-0.0789 / +0.0301 / +4.24** | **-0.0051 / +0.0019 / +0.26** |
| pin >= 3.5 | -0.0430 / +0.0173 / +1.69 | +0.0130 / **-0.0045** / -0.66 | -0.0623 / +0.0277 / +3.66 | +0.0035 / -0.0016 / -0.20 |
| pin >= 4.0 | -0.0433 / +0.0196 / +1.83 | +0.0148 / **-0.0060** / -0.83 | -0.0526 / +0.0264 / +3.34 | +0.0037 / -0.0018 / -0.23 |

Exactly one threshold survives. At 3.0 ATR the difference is positive
on all four samples, clears t = 2 on two of them, and the cut segment
is negative on all four — including the unseen one, where the band
reads -0.0051 R and the filter +0.0019 at t = +0.26. The neighbouring
floors each fail on a different sample, which is the ordinary shape of
a real but modest effect rather than a lucky pick: 2.5 leaves part of
the bad band in, 3.5 and 4.0 start cutting into the good one.

The fourth sample deserves its own sentence. It is much weaker than the
other three — +0.0019 R at t = +0.26, against +0.022 and +0.030 — so
what it establishes is direction, not size. That is what it was asked
for.

**The concentration check.** The band is crypto-heavy: BTCUSD and
ETHUSD supply 34 / 47 / 32 / 38 % of the cut across the four samples,
which would make this a pair-selection question in disguise if they
carried the effect. They do not — they dilute it. Split by instrument
group, the filter is worth +0.049 / +0.005 / +0.043 / +0.010 R a trade
on everything except crypto, and -0.022 / -0.001 / +0.028 / -0.038 on
crypto alone. The rule ships unconditionally anyway: a crypto exemption
would be a threshold chosen after seeing the split, which is the move
this log refuses. (The per-instrument aggregation omits pairs whose cut
is under ten trades, so the group figures are directional; the ALL rows
above are the measurement.)

The mechanism is symmetric and explains why the wide stops are not the
problem they look like. A stop 8 ATR away cannot reach a target 12 ATR
away inside 24 bars — but ordinary noise cannot drag it to the stop
either, so the trade resolves on drift at the leash. A stop at 2 ATR
offers neither protection: close enough to be hit by noise, with a
target close enough that the trade ends before any drift accumulates.

Implementation: `HURZ_MIN_STOP_ATR_MULTIPLE`, default 3.0, applied in
`evaluate_pair` beside the existing 1 % price floor and at the matching
point in `spot_backtest._simulate_trades`, so live and simulator refuse
the same signals. It is read after the venue expansion and before the
cost widening further down the order path; widening only fires above a
5.25 % spread, which no instrument in the tradeable universe reaches,
so the two orderings differ on nothing the book trades. No risk control
is loosened — the rule removes entries and never widens a stop.

The cost is frequency: the floor refuses 29-38 % of router-passed
signals depending on the sample. That is the largest single reduction
in trade count the project has made, and it is the point — the R sums
go from -124.8 to -32.9, +124.3 to +150.5, -209.7 to +57.7 and +11.7
to +28.9. The third sample crosses from a heavy loss to a profit; the
recent year does not cross, it only loses less.

## 191. Extension at the breakout: overbought entries were the good ones for three years and the bad ones for the last

RSI(14) sits in `add_indicators` beside the ADX and the ATR and had
never been read as an entry segment. `scripts/rsi_extension_filter.py`
reads it in the signal's own direction — RSI for longs, 100 - RSI for
shorts, so "high" always means extended — and tests a refusal at 70 /
75 / 80 / 85 on all four disjoint samples, against the system as it now
stands: the ADX ceiling in `gate()` and the 3 x ATR floor replicated
from `_min_stop_atr_multiple()`.

| cap | 365 d: cut E[R] / diff / t | 366-1,095 d | 1,096-1,825 d | 1,826-2,555 d |
|---|---|---|---|---|
| rsi < 70 | -0.0343 / +0.0238 / +2.44 | +0.0209 / -0.0143 / -2.09 | +0.0159 / -0.0107 / -1.40 | +0.0081 / -0.0056 / -0.79 |
| rsi < 75 | -0.0290 / +0.0140 / +1.72 | +0.0323 / -0.0156 / -2.76 | +0.0252 / -0.0122 / -1.88 | +0.0109 / -0.0054 / -0.89 |
| rsi < 80 | -0.0450 / +0.0143 / +2.14 | +0.0129 / -0.0038 / -0.86 | +0.0173 / -0.0051 / -1.01 | +0.0340 / -0.0102 / -2.15 |
| rsi < 85 | -0.0402 / +0.0067 / +1.36 | +0.0064 / -0.0009 / -0.31 | +0.0327 / -0.0049 / -1.35 | +0.0575 / -0.0086 / -2.60 |

Not one threshold is positive on more than a single sample, and every
cut segment on the three older samples is positive — the rule would
discard winners in three years out of four. Condition (a) and condition
(c) both fail at every threshold. Nothing is built.

The bucket table says it more plainly. The 80+ band — the most extended
entries, roughly 30 % of the book — returns +0.013, +0.017 and +0.034 R
on the three older samples and -0.045 R at t = -2.14 on the recent
year. Buying a breakout that has already run was the profitable half of
this system for three years and became its worst segment in the last
one. That is the fourth different entry characteristic today to show
the same shape, and at some point the shape is the finding: the recent
year is not a weaker version of the previous three, it is a different
regime, and a filter fitted to it would be fitted to twelve months.

**What this run does establish, in its baseline column.** These samples
were measured against the post-190 system, so the live rows are a clean
out-of-sample read on what shipped today:

| sample | E[R] before (section 187/189) | E[R] now | n before | n now |
|---|---|---|---|---|
| 365 d | -0.0377 | **-0.0216** | 4,530 | 2,869 |
| 366-1,095 d | +0.0134 | **+0.0229** | 9,169 | 6,258 |
| 1,096-1,825 d | -0.0230 | **+0.0118** | 9,500 | 5,777 |
| 1,826-2,555 d | +0.0013 | **+0.0098** | 8,895 | 5,637 |

The ADX ceiling and the volatility floor together improve expectancy on
all four samples and flip the 1,096-1,825 sample from negative to
positive. Three of the four now read positive. The recent year does not
— it improves by 0.016 R a trade and stays at -0.022. Nothing about
today's two builds changes the fact that the last twelve months are the
sample the system cannot make money in, and no entry characteristic
tested so far separates that year from the three before it in a way
that survives out of sample.

## 192. Instrument expectancy transfers when three samples have to agree — BUILT IN

Section 130 tested the selector's premise with one prior sample against
one following it and found nothing: quartiles did not carry in either
direction, and the rank correlation was -0.22 (p 0.28). The conclusion
recorded then — the ranking is non-predictive — was correct for the
test it ran. The venue now serves enough history for a stricter form of
the same question, and the answer changes.

The rule: three disjoint training samples (days 366-1,095, 1,096-1,825,
1,826-2,555) must ALL read negative on an instrument before it is
flagged; the most recent year is held out entirely. Applied to the
per-instrument tables of section 191 — measured against the current
system, ADX ceiling and volatility floor included — it flags three of
the 24 instruments present throughout: AUDUSD, GBPCAD, GBPUSD.

Three of 24 is exactly the count chance produces under no transfer at
all (24 x 0.5³ = 3.0). The training agreement therefore proves nothing
by itself, and the acceptance rule was written to say so: the held-out
year decides, at paired t > 2, or nothing is blocked.

`scripts/instrument_consistency_block.py` books the block on the test
sample the way every filter in this log is booked — every signal's live
R against the variant's R, zero where the variant does not trade,
paired per trade.

| test sample (last 365 d) | n | E[R] | sum R | t |
|---|---|---|---|---|
| live | 2,870 | -0.0215 | -61.8 | -1.82 |
| the three flagged | 487 | **-0.0963** | -46.9 | **-5.34** |
| the other 21 | 2,383 | -0.0063 | -14.9 | -0.45 |
| rule (block the three) | 2,870 | **-0.0052** | -14.9 | — |

Paired difference **+0.0163 R at t = +5.22**. Each of the three is
significant on its own on data that did not select it: AUDUSD -0.155
(t -3.77), GBPUSD -0.084 (t -3.23), GBPCAD -0.054 (t -2.16). They are
17 % of the year's trades and 76 % of its loss — remove them and the
remaining 21 instruments are flat rather than losing.

**Method check.** A single held-out window is one degree of freedom, so
the same rule was run with the time direction reversed and from the
middle out:

| arrangement | flagged | test: flagged vs rest |
|---|---|---|
| forward (train on the 3 oldest, test on the recent year) | AUDUSD, GBPCAD, GBPUSD | -0.096 vs -0.006 |
| backward (train on the 3 newest, test on the oldest) | + EURAUD, UK100 | -0.006 vs +0.019 |
| middle-out (train on the 2 oldest, test on days 366-1,095) | + NZDUSD | -0.014 vs +0.032 |

Every arrangement points the same way, and the three forward-flagged
names appear in all three. That makes this the instruments rather than
the ordering. Only the three the preregistered forward rule produced
are blocked; EURAUD, UK100 and NZDUSD come from arrangements computed
after the fact and are not.

**The live journal, unprompted.** Across 536 closed trades the book has
lost 239.57 USD. The three flagged pairs account for 23.67 of that over
28 trades, AUDUSD alone for 27.98 over 15 — the largest single loser in
the real book. This is not part of the acceptance test (n is far too
small) but it does not contradict it.

Implementation: the three are added to `EXPECTANCY_BLOCKED_PAIRS`
beside AU200, which the entry guard in `evaluate_pair`, the order guard
in `execute_intent` and the nightly pair selector all consult through
`BLOCKED_PAIRS`. No open position is affected — none of the three was
open — and the guards refuse entries only; exits are untouched.

What section 130 concluded still holds and is worth keeping straight:
one prior sample does not predict the next. Three agreeing ones do, and
the reason is not subtle — an instrument that loses in three separate
market regimes is more plausibly a bad instrument than an unlucky one.

## 193. The consistency rule does not survive at combination granularity — and the day's balance

Section 192 blocked three instruments by requiring three disjoint
training samples to agree, with the recent year held out, and cleared
the bar at t = +5.22. The nightly selector ranks instrument-strategy
combinations rather than instruments, so the natural next question is
whether the same rule works one level down: donchian on DE40 and
keltner on DE40 are separate bets, and one could be sound while the
other is not.

`scripts/combo_consistency_block.py` dumps per-trade (pair, strategy,
direction, R) for all four samples against the current system — ADX
ceiling, 3 x ATR floor, and section 192's block list, so anything found
here is additional to it rather than a restatement. Flagging and test
are computed from the same booking.

Of the 51 combinations carrying 30+ trades in every training sample,
three read negative in all three: CHFJPY / donchian, EURAUD / keltner,
EURUSD / turtle. Chance alone would produce 6.4 — the rule finds
*fewer* consistent losers than randomness, which is the first hint.

| test sample (held out, last 365 d) | n | E[R] | t |
|---|---|---|---|
| live | 2,384 | -0.0062 | -0.45 |
| the three flagged combinations | 154 | **+0.0134** | +0.42 |
| the rest | 2,230 | -0.0075 | -0.52 |

Paired difference **-0.0009 R at t = -0.42**. The flagged combinations
are the *better* side on the held-out year, so the rule is not merely
unproven here, it points the wrong way. Nothing is blocked.

Run in all three arrangements the picture is consistent about being
inconsistent:

| arrangement | universe | flagged (chance) | flagged vs rest on test | paired / t |
|---|---|---|---|---|
| forward | 51 | 3 (6.4) | +0.013 vs -0.008 | -0.0009 / -0.42 |
| backward | 44 | 5 (5.5) | +0.055 vs +0.014 | -0.0045 / -1.57 |
| middle-out | 51 | 8 (6.4) | -0.002 vs +0.039 | +0.0003 / +0.10 |

In two of three the flagged combinations beat the rest. Combination
expectancy is not positively autocorrelated across samples at all,
which is why the flag count sits at or below chance in every
arrangement. The finding is about where persistence lives: an
instrument that loses in three separate regimes is plausibly a bad
instrument, but a *strategy on* an instrument losing in three regimes
carries no such information — the combination's result is dominated by
which of the three correlated breakout rules happened to fire, not by
anything durable about the pairing. The selector should keep ranking
combinations for what it uses them for, and expectancy blocks belong at
instrument level, where section 192 put them.

**The day's balance.** These four dumps are the first measurement of the
system with all three of today's builds in place — the ADX ceiling
(188), the 3 x ATR volatility floor (190) and the instrument block
(192):

| sample | E[R] this morning | E[R] now | sum R this morning | sum R now | n now |
|---|---|---|---|---|---|
| 365 d | -0.0377 | **-0.0062** | -171.0 | **-14.8** | 2,384 |
| 366-1,095 d | +0.0134 | **+0.0322** (t +3.36) | +122.8 | **+169.8** | 5,272 |
| 1,096-1,825 d | -0.0230 | **+0.0215** (t +2.02) | -218.6 | **+103.4** | 4,802 |
| 1,826-2,555 d | +0.0013 | **+0.0169** (t +1.75) | +11.7 | **+79.2** | 4,679 |

Every sample improves. Two now clear t = 2 positive where none did this
morning, the 1,096-1,825 sample crosses from -218.6 R to +103.4 R, and
the recent year goes from -171.0 R to -14.8 R — still negative, but
within a standard error of zero (t = -0.45) rather than three of them.
The cost is 47 % of the trades on the recent year.

The plain statement of where this leaves the objective: three of four
years would now be profitable and the fourth roughly breaks even before
slippage. That is not 50 EUR a day. At 3 USD of risk per trade and the
frequency that survives these filters, the recent year's rate is
approximately zero, and the older years' +0.02 to +0.03 R would be
2 to 4 USD a day. Reaching the target from here is a question of risk
per trade against a positive expectancy, not of another entry filter —
and the expectancy has to hold forward before that multiplication is
worth making.

## 194. The 1 % price floor cannot fire, and the comment that said it constrained frequency was wrong

The floor's own comment in `evaluate_pair` named the condition for
reopening it: expectancy negative on both sides, so trading more only
loses faster, "revisit only once expectancy is positive — at that point
the floor becomes the single largest constraint on frequency", and
removing it "multiplies volume roughly fifteenfold". After sections
188, 190 and 192 that condition is met on three of four samples, and
the daily objective is trades-per-day x E[R] x risk, so this was the
arithmetic's next binding term.

`scripts/stop_floor_revisit.py` books floors of 1.00 / 0.75 / 0.50 /
0.25 % and off, occupancy resolved per floor so the frequency change is
modelled rather than held fixed. The result is the same line five times:

| floor | 365 d | 366-1,095 d | 1,096-1,825 d | 1,826-2,555 d | added trades |
|---|---|---|---|---|---|
| 1.00 % (live) | -0.0060 / n 2,385 | +0.0322 / n 5,272 | +0.0213 / n 4,803 | +0.0169 / n 4,679 | — |
| 0.75 % | identical | identical | identical | identical | **0** |
| 0.50 % | identical | identical | identical | identical | **0** |
| 0.25 % | identical | identical | identical | identical | **0** |
| off | identical | identical | identical | identical | **0** |

Not one trade is added at any level, on any sample. The floor is not a
constraint on frequency; it cannot fire at all.

The reason is arithmetic and sits two constants apart:
`_min_stop_fraction()` is 1.00 % of price, `VENUE_MIN_STOP_FRACTION` is
1.05 %, and the floor is checked *after* the venue expansion. Every stop
that reaches the check has already been widened to at least 1.05 % of
price, so `stop_dist / entry < 0.01` is unreachable. The same holds in
the backtest, where `_venue_min_distance` returns a positive default for
instruments outside the audit file rather than zero.

The comment's figures were true once — they came from the era when the
floor was checked BEFORE the expansion, the ordering bug that
`StopFloorOrderTest` was written to prevent and whose fix is recorded in
`spot_backtest._simulate_trades` ("Checking it before expansion — as this
once did — rejects roughly fifteen signals in sixteen that live trades
happily"). The fifteenfold figure is that bug's magnitude, not the
floor's. The comment survived the fix and kept promising a lever that
the fix had already removed, which is exactly the kind of stale note
that costs a run — this one.

The floor stays in place: it is unreachable while the venue expansion
returns a positive distance, and it is the fail-closed backstop for the
case where that distance is zero. Only its comment changes, to say what
it actually does.

**Where frequency is really constrained**, on the recent year, in the
order the guards apply:

| stage | trades | removed |
|---|---|---|
| router-passed, before today's builds | 4,530 | — |
| after the ADX ceiling (188) | ~4,189 | 341 |
| after the 3 x ATR volatility floor (190) | 2,869 | 1,320 |
| after the instrument block (192) | 2,384 | 485 |

The volatility floor is the largest single reduction by a factor of
four, and it is the one that carried a measured expectancy improvement
on four samples. There is no free frequency to recover here — every cut
that remains was bought with expectancy. Any further increase in daily
gain has to come from risk per trade against the positive expectancy the
three older samples now show, and that multiplication is only worth
making once the expectancy holds forward on the live book.

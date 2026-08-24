# Structural signal preregistration, August 2026

This document fixes the experiment before the structural-signal holdout is
opened. The research command records its SHA-256 digest in the development
result and refuses to run the holdout if the file has changed.

## Data split and universe

- Capital.com demo mid-price OHLC, one-hour bars.
- History starts at `2023-11-30T00:00:00Z`.
- Development entries end before `2026-02-01T00:00:00Z`.
- Holdout entries start at `2026-02-01T00:00:00Z` and end before
  `2026-08-24T00:00:00Z`.
- The universe is the 14-instrument audited basket: EURUSD, GBPUSD, USDJPY,
  AUDUSD, USDCAD, USDCHF, BTCUSD, ETHUSD, SOLUSD, ADAUSD, DOGEUSD, XRPUSD,
  DOTUSD and AAVEUSD.
- Higher-timeframe and rolling indicators may use bars before the entry
  boundary as warm-up data. No trade may cross from development into the
  holdout.

The first four proposed structural sources are tested. Time-of-day and
weekday rules are deliberately excluded: their many arbitrary boundaries
create substantially more researcher degrees of freedom than the available
history can support.

## Fixed execution model

All variants use one execution profile so signal research cannot turn into a
second stop/target search:

- 1.0 ATR initial stop, widened to Capital.com's 1.0% minimum plus the
  established 5% buffer.
- 1.5 R target and 24 one-hour bars maximum holding period.
- Conservative stop-first ordering when one bar touches stop and target.
- Full audited bid/ask spread from `capital_spread_percent.json` charged once
  per round trip. Instruments absent because their market was closed during
  that audit use the latest available `capital_spreads.json` quote instead.
- Signals whose spread exceeds 10% of planned risk are not executable.
- Shared live/backtest sizing: 3 USD target risk, 250 USD notional ceiling,
  and audited broker minimum, increment and maximum sizes.

## Eighteen fixed variants

### Multi-timeframe confirmation (4)

A close beyond the previous 20- or 55-hour high/low is accepted only when a
completed 4-hour or daily close is on the same side of its 20-period EMA and
that EMA slopes in the signal direction:

- `mtf_break20_4h_ema20`
- `mtf_break55_4h_ema20`
- `mtf_break20_1d_ema20`
- `mtf_break55_1d_ema20`

### Volatility regime (6)

Four variants admit a 20- or 55-hour close breakout when current ATR is below
the prior 252-hour 25th percentile or above its 75th percentile. Two squeeze
variants require the prior 12 hours to contain Bollinger width below its
prior 252-hour 10th or 20th percentile before a 20-hour close breakout:

- `vol_atr_q25_break20`
- `vol_atr_q25_break55`
- `vol_atr_q75_break20`
- `vol_atr_q75_break55`
- `vol_squeeze_q10_break20`
- `vol_squeeze_q20_break20`

### Cross-asset leadership (4)

BTCUSD is the sole leader and the other seven crypto instruments are targets.
The leader's return over one or four hours, observed one bar earlier, must
exceed 0.75 or 1.25 trailing standard deviations. A target is eligible only
while its equivalently standardised move is less than half the leader move:

- `lead_btc_1h_z075`
- `lead_btc_1h_z125`
- `lead_btc_4h_z075`
- `lead_btc_4h_z125`

### Relative strength (4)

At each rebalance, independently rank the six-FX and eight-crypto baskets by
trailing return. Enter the strongest instrument long and the weakest short:

- `relative_24h_rebalance12h`
- `relative_24h_rebalance24h`
- `relative_72h_rebalance12h`
- `relative_72h_rebalance24h`

## Statistical decision rule

The number of distinct rules is 18. Development confidence bounds use a
one-sided family-wise alpha of 5% divided by all 18 variants (Bonferroni).
Bonferroni is chosen instead of Benjamini-Hochberg because the decision can
allocate capital to any apparent winner; controlling the probability of even
one false discovery is more appropriate than controlling an expected false
discovery proportion.

Dependence between simultaneous instruments is handled with week-clustered
standard errors. Stability is the share of five consecutive time segments
with positive net expectancy. Each family contributes exactly one frozen
development champion (the greatest corrected lower bound) to the holdout, so
holdout bounds divide alpha by four.

A deployable candidate must satisfy all of these conditions in development
and again in the untouched holdout:

- at least 50 executable trades;
- positive net expectancy and positive dollar PnL;
- at least four of five positive segments;
- Bonferroni-corrected one-sided 95% lower confidence bound above zero.

Instrument-level and segment-level diagnostics cannot be used to alter a
rule after the holdout is opened. A failed rule remains a negative result.

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

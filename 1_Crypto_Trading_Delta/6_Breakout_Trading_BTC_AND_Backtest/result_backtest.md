# Breakout backtest results

Window: 2026-05-19 to 2026-10-15 (150 calendar days). Tune window: first 100 days. Holdout: last 50 days.

The simulator follows the live breakout: previous completed candles set the range, one position at a time, no new entry on the exit candle, 20 orders per day, and a $10 daily loss cap that flattens at the bar close. BTCUSD contract value is 0.001 and tick size is 0.5. Taker fee is 5 bps per side on price x size x contract value. Funding is not included. A bar that touches both the take-profit and the stop books the stop. An open trade at the end of a window is closed at the last close.

## First run

Current `config.yaml` settings on each interval, full window, before any search.

| Interval | Net PnL | Avg daily | Median day | Days >= $6 | Trades | Win rate | Profit factor | Max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1h | -$185.97 | -$1.24 | $0.00 | 9.3% | 324 | 34.6% | 0.74 | $185.97 |
| 30m | -$189.88 | -$1.27 | -$0.47 | 7.3% | 354 | 35.0% | 0.75 | $204.01 |
| 15m | -$101.25 | -$0.68 | $0.00 | 12.0% | 375 | 38.1% | 0.87 | $127.14 |

Baseline settings: lookback 1, confirmation 150 points, live price cross, take-profit $5, stop-loss $3, order size 5, direction BOTH.

## Search

Combinations tested: 972. Kept for the holdout: 84 with positive net PnL on the tune window.

Top holdout results among combinations that were profitable on the tune window:

| Rank | Interval | Lookback | Confirm | Entry | TP | SL | Size | Holdout net | Holdout avg daily | Trades | Win rate | PF | Max DD |
| ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1h | 1 | 300 | cross | 12 | 6 | 5 | $19.77 | $0.40 | 31 | 38.7% | 1.17 | $38.55 |
| 2 | 30m | 1 | 50 | cross | 12 | 4 | 5 | $17.50 | $0.35 | 54 | 27.8% | 1.11 | $39.86 |
| 3 | 1h | 1 | 50 | close | 12 | 6 | 5 | $13.40 | $0.27 | 36 | 36.1% | 1.10 | $46.96 |
| 4 | 1h | 6 | 300 | close | 8 | 3 | 5 | $12.00 | $0.24 | 32 | 34.4% | 1.17 | $20.37 |
| 5 | 30m | 1 | 50 | cross | 8 | 4 | 5 | $8.95 | $0.18 | 77 | 36.4% | 1.04 | $39.04 |
| 6 | 1h | 1 | 50 | cross | 8 | 6 | 5 | $2.40 | $0.05 | 46 | 45.7% | 1.02 | $40.62 |
| 7 | 1h | 3 | 300 | close | 8 | 6 | 5 | -$2.92 | -$0.06 | 27 | 44.4% | 0.97 | $25.57 |
| 8 | 30m | 3 | 50 | close | 12 | 3 | 5 | -$3.34 | -$0.07 | 45 | 22.2% | 0.97 | $41.78 |
| 9 | 1h | 6 | 50 | close | 8 | 3 | 5 | -$3.75 | -$0.07 | 56 | 30.4% | 0.97 | $44.92 |
| 10 | 1h | 6 | 300 | close | 8 | 6 | 5 | -$4.08 | -$0.08 | 25 | 44.0% | 0.95 | $25.57 |

## Position size and session

Candle times in the CSV are India time (`Asia/Kolkata`). New York and London hours are converted from those timestamps, including daylight saving. In this sample, 19:00 IST is 09:30 New York and 12:30 IST is 08:00 London. The daily loss cap and the 20-order cap reset at midnight in the session timezone. New York entries are 09:30–16:00 America/New_York and London entries are 08:00–16:30 Europe/London. An open trade is closed on the last bar that still falls inside that window. Full day uses every IST hour and does not close on a clock.

The daily loss budget is ₹600. At ₹84 per dollar that is $7.14. The daily cap is never set above that amount. A row is marked over cap, and is not ranked, when its stop is larger than that amount, so the day limit does not replace the trade stop.

Fixed dollars keep a $12 take-profit and a $6 stop, so a larger size only shortens the price move. Scaled dollars keep the size-5 distance of 2,400 points to the target and 1,200 points to the stop. Rupee figures use the same ₹ per dollar rate.

A row is qualified only when it fits the ₹600 stop budget and the first 100 days are profitable after fees. Average daily PnL divides by those India-time calendar days so the three clocks are comparable.

### New York

| Size | Bracket | TP | SL | TP points | SL points | Daily cap | Tune net | Holdout net | Holdout avg | Holdout avg ₹ | Trades | Win rate | PF | Max DD | Qualified |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | fixed | 12 | 6 | 12000 | 6000 | 7.14 | -$11.35 | -$1.08 | -$0.02 | -₹2 | 35 | 45.7% | 0.88 | $7.10 | no |
| 1 | scaled | 2.4 | 1.2 | 2400 | 1200 | 2 | -$12.01 | -$5.48 | -$0.11 | -₹9 | 39 | 43.6% | 0.60 | $10.48 | no |
| 5 | fixed | 12 | 6 | 2400 | 1200 | 7.14 | -$60.06 | -$27.05 | -$0.54 | -₹45 | 39 | 43.6% | 0.60 | $52.06 | no |
| 5 | scaled | 12 | 6 | 2400 | 1200 | 7.14 | -$60.06 | -$27.05 | -$0.54 | -₹45 | 39 | 43.6% | 0.60 | $52.06 | no |
| 10 | fixed | 12 | 6 | 1200 | 600 | 7.14 | -$239.97 | -$160.84 | -$3.22 | -₹270 | 46 | 19.6% | 0.19 | $178.26 | no |
| 10 | scaled | 24 | 12 | 2400 | 1200 |  |  |  |  |  |  |  |  |  | over cap |
| 20 | fixed | 12 | 6 | 600 | 300 | 7.14 | -$505.69 | -$264.97 | -$5.30 | -₹445 | 35 | 0.0% | 0.00 | $264.97 | no |
| 20 | scaled | 48 | 24 | 2400 | 1200 |  |  |  |  |  |  |  |  |  | over cap |
| 50 | fixed | 12 | 6 | 240 | 120 | 7.14 | -$643.22 | -$351.96 | -$7.04 | -₹591 | 35 | 0.0% | 0.00 | $351.96 | no |
| 50 | scaled | 120 | 60 | 2400 | 1200 |  |  |  |  |  |  |  |  |  | over cap |

### London

| Size | Bracket | TP | SL | TP points | SL points | Daily cap | Tune net | Holdout net | Holdout avg | Holdout avg ₹ | Trades | Win rate | PF | Max DD | Qualified |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | fixed | 12 | 6 | 12000 | 6000 | 7.14 | -$6.22 | $4.35 | $0.09 | ₹7 | 41 | 48.8% | 1.31 | $5.97 | no |
| 1 | scaled | 2.4 | 1.2 | 2400 | 1200 | 2 | $1.15 | $0.52 | $0.01 | ₹1 | 47 | 48.9% | 1.03 | $8.58 | yes |
| 5 | fixed | 12 | 6 | 2400 | 1200 | 7.14 | -$0.90 | $7.01 | $0.14 | ₹12 | 47 | 48.9% | 1.08 | $38.48 | no |
| 5 | scaled | 12 | 6 | 2400 | 1200 | 7.14 | -$0.90 | $7.01 | $0.14 | ₹12 | 47 | 48.9% | 1.08 | $38.48 | no |
| 10 | fixed | 12 | 6 | 1200 | 600 | 7.14 | -$113.67 | -$80.06 | -$1.60 | -₹135 | 56 | 33.9% | 0.63 | $97.04 | no |
| 10 | scaled | 24 | 12 | 2400 | 1200 |  |  |  |  |  |  |  |  |  | over cap |
| 20 | fixed | 12 | 6 | 600 | 300 | 7.14 | -$557.24 | -$312.37 | -$6.25 | -₹525 | 41 | 0.0% | 0.00 | $312.37 | no |
| 20 | scaled | 48 | 24 | 2400 | 1200 |  |  |  |  |  |  |  |  |  | over cap |
| 50 | fixed | 12 | 6 | 240 | 120 | 7.14 | -$709.07 | -$411.96 | -$8.24 | -₹692 | 41 | 0.0% | 0.00 | $411.96 | no |
| 50 | scaled | 120 | 60 | 2400 | 1200 |  |  |  |  |  |  |  |  |  | over cap |

### Full day

| Size | Bracket | TP | SL | TP points | SL points | Daily cap | Tune net | Holdout net | Holdout avg | Holdout avg ₹ | Trades | Win rate | PF | Max DD | Qualified |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | fixed | 12 | 6 | 12000 | 6000 | 7.14 | $2.86 | $2.96 | $0.06 | ₹5 | 1 | 100.0% | inf | $0.00 | yes |
| 1 | scaled | 2.4 | 1.2 | 2400 | 1200 | 2 | $6.28 | $3.95 | $0.08 | ₹7 | 31 | 38.7% | 1.17 | $7.71 | yes |
| 5 | fixed | 12 | 6 | 2400 | 1200 | 7.14 | -$42.93 | $30.02 | $0.60 | ₹50 | 37 | 35.1% | 1.25 | $30.85 | no |
| 5 | scaled | 12 | 6 | 2400 | 1200 | 7.14 | -$42.93 | $30.02 | $0.60 | ₹50 | 37 | 35.1% | 1.25 | $30.85 | no |
| 10 | fixed | 12 | 6 | 1200 | 600 | 7.14 | -$232.62 | -$273.60 | -$5.47 | -₹460 | 79 | 15.2% | 0.33 | $284.81 | no |
| 10 | scaled | 24 | 12 | 2400 | 1200 |  |  |  |  |  |  |  |  |  | over cap |
| 20 | fixed | 12 | 6 | 600 | 300 | 7.14 | -$644.86 | -$335.26 | -$6.71 | -₹563 | 44 | 0.0% | 0.00 | $335.26 | no |
| 20 | scaled | 48 | 24 | 2400 | 1200 |  |  |  |  |  |  |  |  |  | over cap |
| 50 | fixed | 12 | 6 | 240 | 120 | 7.14 | -$820.12 | -$442.17 | -$8.84 | -₹743 | 44 | 0.0% | 0.00 | $442.17 | no |
| 50 | scaled | 120 | 60 | 2400 | 1200 |  |  |  |  |  |  |  |  |  | over cap |

Keeper risk: one stop is $1.20 (₹101) and the daily loss cap is $2.00 (₹168). This stop is inside the ₹600 budget. The daily cap on this row is the amount that was actually tested.

## Leverage

Leverage does not change dollar profit. Profit is still price change times size times 0.001. Leverage only moves the liquidation price. Margin is entry times size times 0.001 divided by leverage, so the adverse move that burns the margin is entry divided by leverage. If that price is closer than the strategy stop, the trade exits there and the loss is the margin.

The daily loss budget remains ₹600 ($7.14). Rows with a stop above that were not tested. A row is ranked only when the first 100 days are profitable. The config is changed only when the best holdout averages at least ₹500 a day.

| Rank | Session | Lev | Size | Bracket | SL | Daily cap | Liquidations | Holdout avg | Holdout avg ₹ | Tune net |
| ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | Full day | 20 | 1 | scaled | 1.2 | 2 | 0 | $0.08 | ₹7 | $6.28 |
| 2 | Full day | 25 | 1 | scaled | 1.2 | 2 | 0 | $0.08 | ₹7 | $6.28 |
| 3 | Full day | 50 | 1 | scaled | 1.2 | 2 | 0 | $0.08 | ₹7 | $6.29 |
| 4 | Full day | 20 | 1 | fixed | 6 | 7.14 | 0 | $0.06 | ₹5 | $7.81 |
| 5 | Full day | 25 | 1 | fixed | 6 | 7.14 | 1 | $0.03 | ₹3 | $4.46 |
| 6 | London | 20 | 1 | scaled | 1.2 | 2 | 0 | $0.01 | ₹1 | $1.15 |
| 7 | London | 25 | 1 | scaled | 1.2 | 2 | 0 | $0.01 | ₹1 | $1.15 |
| 8 | London | 50 | 1 | scaled | 1.2 | 2 | 0 | $0.01 | ₹1 | $1.15 |
| 9 | Full day | 100 | 1 | fixed | 6 | 7.14 | 15 | -$0.26 | -₹22 | $4.57 |

No tested leverage reached ₹500 a day inside the ₹600 loss limit. The best ranked holdout is ₹7 a day. 20x through 200x did not create that profit, because leverage does not increase the dollar result and a larger stop does not fit the ₹600 cap. The leverage test did not change `config.yaml`.

## Indicator filters

Order size is 5, 10, or 20. The breakout is unchanged: one previous candle, 300 points, live cross, both directions, full India-time day. EMA 20 and EMA 50 allow a long only when the previous close is above the average, and a short only below it. RSI 14 allows a long only at 55 or higher and a short only at 45 or lower. Bollinger 20 with 2 standard deviations allows a long only above the upper band and a short only below the lower band. The filter uses the previous completed candle, not the bar being entered.

A stop above $7.14 (₹600) is excluded. A row is ranked only when the first 100 days are profitable after fees.

| Rank | Interval | Filter | Size | Bracket | TP | SL | Cap | Tune net | Holdout avg | Holdout avg ₹ | Trades |
| ---: | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 30m | ema20 | 5 | fixed | 12 | 6 | 7.14 | $57.87 | -$0.79 | -₹67 | 38 |
| 2 | 30m | ema20 | 5 | scaled | 12 | 6 | 7.14 | $57.87 | -$0.79 | -₹67 | 38 |
| 3 | 1h | rsi14 | 5 | fixed | 12 | 6 | 7.14 | $2.64 | -$0.97 | -₹82 | 34 |
| 4 | 1h | rsi14 | 5 | scaled | 12 | 6 | 7.14 | $2.64 | -$0.97 | -₹82 | 34 |
| 5 | 30m | rsi14 | 5 | fixed | 12 | 6 | 7.14 | $22.09 | -$1.06 | -₹89 | 36 |
| 6 | 30m | rsi14 | 5 | scaled | 12 | 6 | 7.14 | $22.09 | -$1.06 | -₹89 | 36 |
| 7 | 1h | ema50 | 5 | fixed | 12 | 6 | 7.14 | $0.77 | -$1.68 | -₹141 | 41 |
| 8 | 1h | ema50 | 5 | scaled | 12 | 6 | 7.14 | $0.77 | -$1.68 | -₹141 | 41 |
| 9 | 30m | ema50 | 5 | fixed | 12 | 6 | 7.14 | $25.99 | -$1.73 | -₹146 | 35 |
| 10 | 30m | ema50 | 5 | scaled | 12 | 6 | 7.14 | $25.99 | -$1.73 | -₹146 | 35 |

Best ranked holdout is -$0.79 a day, which is -₹67 a day. That is $6.74, or ₹567, short of ₹500 a day. These 150 days do not support a living of ₹500–₹1,000 a day. A backtest cannot promise income.

## Final result

Chosen settings: Full day, ema20, fixed dollars, 30m, lookback 1, confirmation 300 points, live cross, TP $12.00, SL $6.00, order size 5, direction BOTH, daily cap $7.14.

The running config was then set to the 1-hour breakout with no filter, size 5, take-profit $12, stop $6, and a ₹600 daily cap. The last 50 days of that row were about ₹50 a day. These 150 days still do not support ₹500–₹1,000 a day.

| Window | Net PnL | Avg daily | Median day | Days >= $6 | Trades | Win rate | Profit factor | Max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tune | $57.87 | $0.58 | $0.00 | 13.0% | 58 | 36.2% | 1.32 | $88.56 |
| Holdout | -$39.63 | -$0.79 | $0.00 | 12.0% | 38 | 23.7% | 0.70 | $68.36 |

The last 50 days do not support an average of $6 per day. This configuration's holdout average is -$0.79 per day. That figure is the best holdout result among settings that made money on the earlier days. It is not a forecast, and the best tune-window result was not used as the recommendation.

### Config changes

- `timeframe`: 1h -> 30m
- `order_size`: 1 -> 5
- `max_loss_per_day_dollar`: 2 -> 7.14
- `take_profit.value`: 2.4 -> 12
- `stop_loss.value`: 1.2 -> 6
- `indicators.filter`: none -> ema20

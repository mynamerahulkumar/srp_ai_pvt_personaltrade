# BTCUSD and ETHUSD comparison

This report tests one rule on the saved hourly files: keep the breakout on BTCUSD, enter ETHUSD in the same direction at the same time, and exit both when BTCUSD reaches its dollar take-profit or stop-loss. The bot and `config.yaml` are unchanged.

## Proposed rule

The signal stays on BTCUSD. A long is a trade above the previous 1-hour high plus 150 points. A short is a trade below the previous 1-hour low minus 150 points. When that BTC signal fires, place the same direction on BTCUSD and ETHUSD at size 5. Close both legs when the BTC position reaches `+$5` or `-$3`. ETH does not have its own breakout, take-profit, or stop.

The 150-point buffer stays on BTC. At the last close, 150 BTC points is about `0.18%` of price. The same 150 points on ETH would be about `6%` of price, so an ETH copy of that buffer would not line up with BTC signals.

## Current bot settings used here

From `config.yaml` and the PnL formula in `main.py`:

- Symbol in the live config: `BTCUSD`
- Timeframe: `1h`
- Breakout lookback: `1` completed candle
- Direction: `BOTH`
- Confirmation: `150` points, live price (`candle_close_confirmation: false`)
- Size: `5`
- Take-profit: `$5` (PNL)
- Stop-loss: `$3` (PNL)
- Dollar PnL: `(price change) × quantity × contract_value`

Delta India product specs read for this report (the bot still loads these at runtime and does not hardcode them):

| Product | Contract value | Tick | Type |
| --- | ---: | ---: | --- |
| BTCUSD | 0.001 | 0.5 | perpetual future |
| ETHUSD | 0.01 | 0.05 | perpetual future |

At size 5, the BTC dollar targets are a `1,000` point favorable move for `+$5` and a `600` point adverse move for `-$3`.

## Sample

Both files line up on every timestamp. There are no missing hours and no symbol-only rows.

| | BTCUSD | ETHUSD |
| --- | --- | --- |
| File | `data/historical/BTCUSD_1h.csv` | `data/historical/ETHUSD_1h.csv` |
| Bars | 710 | 710 |
| First bar | 2026-09-12 00:30 | 2026-09-12 00:30 |
| Last bar | 2026-10-11 13:30 | 2026-10-11 13:30 |
| First close | 77,250.5 | 2,538.05 |
| Last close | 83,000 | 2,499.65 |
| Window change | +7.44% | -1.51% |

The window is about 30 days. BTC rose over the full sample and ETH fell. The paired trades below are held for hours, so that full-window gap is separate from the exit-by-exit result.

## Same size is a smaller ETH position

Size 5 is one order count on each product. The dollar exposure is different because the contract values differ.

| | BTCUSD size 5 | ETHUSD size 5 |
| --- | ---: | ---: |
| Notional at first close | $386 | $127 |
| Notional at last close | $415 | $125 |
| ETH notional / BTC notional | | 0.30 to 0.33 across the file (mean 0.32) |

ETH’s typical hourly range is wider in percent (`0.64%` versus `0.48%` for BTC). Hourly percent volatility is about `1.23` times BTC. Notional ratio times that volatility ratio is about `0.39`, which matches the exit results below: on a BTC take-profit ETH added about `38%` of the BTC dollars, and on a BTC stop it added about `39%`.

## Co-movement

Hourly close-to-close returns, 709 overlapping hours:

| Measure | Value |
| --- | --- |
| Correlation | 0.85 |
| ETH beta on BTC | 1.05 |
| Same-direction hours | 588 of 709 (83%) |
| Daily correlation (30 days) | 0.94 |
| Same-direction days | 23 of 30 |
| ETH follows when the BTC hour moves at least 0.5% | 98.7% of 76 hours |
| ETH follows when the BTC hour moves at least 1% | 15 of 15 hours |
| Correlation of ETH this hour with BTC next hour | -0.01 |
| Correlation of BTC this hour with ETH next hour | 0.02 |

Larger BTC hours are the hours ETH matches. Neither coin leads the other by one hour in this file.

On the 245 raw BTC breakout hours (before the one-position lock), ETH also broke its own previous-hour extreme in the BTC direction on 231 hours (94%). The signal-hour signed return averages `+0.34%` on BTC and `+0.39%` on ETH, and ETH is positive on `80%` of those hours. One hour later the signed averages are about zero (`+0.01%` BTC, `+0.03%` ETH), and ETH is positive on about half of the following hours. The later path to the dollar stop or target is what the trade replay measures. The signal itself does not leave a lasting drift in this sample.

The 150-point BTC filter is a small extension of a normal hour. Median BTC hourly range is about 321 points. About `18.5%` of hours trade more than 150 points above the previous high, and about `18.1%` trade more than 150 points below the previous low. That is why the file contains 245 raw signals in 30 days.

## Paired-exit replay

Rules used on the hourly bars:

- Long when the hour’s high clears the previous high by 150 points. Short when the hour’s low breaks the previous low by 150 points. Hours that do both are skipped (7 hours).
- Only one pair is open at a time. The next signal waits until BTC has exited.
- BTC entry is the breakout level, or the open if the hour opens already through the level. ETH entry is the price at the same fraction of that hour’s open-to-extreme path.
- The entry hour cannot stop the trade out, because the low may print before the breakout. Later hours can. If a later hour contains both the 600-point stop and the 1,000-point target, the stop is taken. That tie did not occur.
- ETH is marked at the BTC exit, on the same fraction of the exit hour’s path. ETH has no separate target.
- No fees, funding, or slippage. These are hourly approximations of a live-price cross, not exchange fills.

Result:

| | Count or dollars |
| --- | ---: |
| Raw breakout hours | 245 |
| Trades after the position lock | 62 (31 long, 31 short) |
| Closed | 61 |
| Still open at the last bar | 1 |
| Take-profits | 22 |
| Stop-losses | 39 |
| Win rate | 36.1% |
| Win rate that breaks even at $5 / $3, before fees | 37.5% |
| BTC closed PnL | -$7.00 |
| ETH closed PnL | -$3.65 |
| Both closed | -$10.65 |
| Mean hold of closed trades | about 8 hours |

ETH was on the same side of the trade as BTC on 60 of 61 closed trades.

| BTC exit | Trades | ETH also on that side | Average ETH PnL |
| --- | ---: | --- | ---: |
| Take-profit `+$5` | 22 | 22 of 22 | +$1.89 |
| Stop-loss `-$3` | 39 | 38 of 39 | -$1.16 |

The one split trade is the 12 Sep short: BTC `-$3.00`, ETH `+$0.40`.

On the take-profits the combined result averages about `+$6.89`. On the stop-losses it averages about `-$4.16`. ETH scales both outcomes. In this window the BTC book is slightly below the 37.5% breakeven win rate, so the ETH leg scales that result to a larger dollar loss. The second leg follows BTC. It does not create a separate edge in this file.

The open trade at the end of the file is the 9 Oct long, still open on 11 Oct 13:30, marked about BTC `+$0.81` and ETH `-$0.06`. It is excluded from the closed sums.

## Daily loss cap

`config.yaml` blocks new entries after `$10` of strategy loss in a day. Summing closed trades by exit day:

| Exit day | Trades | BTC | ETH | Both |
| --- | ---: | ---: | ---: | ---: |
| 2026-09-24 | 4 | -$12.00 | -$4.13 | -$16.13 |
| 2026-09-25 | 3 | -$9.00 | -$3.80 | -$12.80 |
| 2026-09-28 | 3 | -$9.00 | -$5.78 | -$14.78 |
| 2026-09-29 | 3 | -$9.00 | -$3.39 | -$12.39 |

BTC alone reaches `-$10` on 1 day. BTC plus ETH reaches `-$10` on these 4 days. The longest run of stop-losses is 16 closed trades.

## Trades

Dollar PnL uses size 5 and the contract values above. `OPEN` is the mark at the last bar.

| # | Entry | Exit | Side | Result | Hours | BTC | ETH | Both |
| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | 2026-09-12 22:30 | 2026-09-14 08:30 | SHORT | SL | 34 | -3.00 | +0.40 | -2.60 |
| 2 | 2026-09-14 11:30 | 2026-09-14 21:30 | LONG | TP | 10 | +5.00 | +0.37 | +5.37 |
| 3 | 2026-09-14 23:30 | 2026-09-15 03:30 | LONG | SL | 4 | -3.00 | -0.14 | -3.14 |
| 4 | 2026-09-15 04:30 | 2026-09-15 10:30 | SHORT | TP | 6 | +5.00 | +2.18 | +7.18 |
| 5 | 2026-09-15 12:30 | 2026-09-15 19:30 | SHORT | TP | 7 | +5.00 | +2.98 | +7.98 |
| 6 | 2026-09-15 22:30 | 2026-09-15 23:30 | LONG | SL | 1 | -3.00 | -1.25 | -4.25 |
| 7 | 2026-09-16 01:30 | 2026-09-16 16:30 | SHORT | SL | 15 | -3.00 | -1.42 | -4.42 |
| 8 | 2026-09-16 18:30 | 2026-09-16 23:30 | SHORT | SL | 5 | -3.00 | -1.03 | -4.03 |
| 9 | 2026-09-17 01:30 | 2026-09-17 03:30 | LONG | SL | 2 | -3.00 | -1.22 | -4.22 |
| 10 | 2026-09-17 04:30 | 2026-09-17 17:30 | LONG | TP | 13 | +5.00 | +3.14 | +8.14 |
| 11 | 2026-09-17 18:30 | 2026-09-17 19:30 | SHORT | SL | 1 | -3.00 | -1.45 | -4.45 |
| 12 | 2026-09-17 22:30 | 2026-09-18 03:30 | LONG | SL | 5 | -3.00 | -1.59 | -4.59 |
| 13 | 2026-09-18 06:30 | 2026-09-18 12:30 | LONG | TP | 6 | +5.00 | +1.79 | +6.79 |
| 14 | 2026-09-18 13:30 | 2026-09-18 18:30 | LONG | TP | 5 | +5.00 | +1.76 | +6.76 |
| 15 | 2026-09-18 19:30 | 2026-09-19 05:30 | LONG | TP | 10 | +5.00 | +2.57 | +7.57 |
| 16 | 2026-09-19 09:30 | 2026-09-19 19:30 | SHORT | SL | 10 | -3.00 | -1.42 | -4.42 |
| 17 | 2026-09-19 20:30 | 2026-09-20 01:30 | LONG | SL | 5 | -3.00 | -0.63 | -3.63 |
| 18 | 2026-09-20 02:30 | 2026-09-21 05:30 | SHORT | SL | 27 | -3.00 | -2.91 | -5.91 |
| 19 | 2026-09-21 10:30 | 2026-09-21 13:30 | LONG | TP | 3 | +5.00 | +0.44 | +5.44 |
| 20 | 2026-09-21 14:30 | 2026-09-21 18:30 | LONG | TP | 4 | +5.00 | +0.88 | +5.88 |
| 21 | 2026-09-21 19:30 | 2026-09-22 01:30 | LONG | TP | 6 | +5.00 | +1.70 | +6.70 |
| 22 | 2026-09-22 03:30 | 2026-09-22 06:30 | SHORT | TP | 3 | +5.00 | +1.53 | +6.53 |
| 23 | 2026-09-22 10:30 | 2026-09-22 13:30 | SHORT | SL | 3 | -3.00 | -0.94 | -3.94 |
| 24 | 2026-09-22 20:30 | 2026-09-23 03:30 | LONG | SL | 7 | -3.00 | -0.75 | -3.75 |
| 25 | 2026-09-23 04:30 | 2026-09-23 13:30 | LONG | SL | 9 | -3.00 | -1.92 | -4.92 |
| 26 | 2026-09-23 16:30 | 2026-09-23 19:30 | SHORT | TP | 3 | +5.00 | +2.48 | +7.48 |
| 27 | 2026-09-23 21:30 | 2026-09-23 23:30 | SHORT | SL | 2 | -3.00 | -1.24 | -4.24 |
| 28 | 2026-09-24 03:30 | 2026-09-24 06:30 | LONG | SL | 3 | -3.00 | -1.19 | -4.19 |
| 29 | 2026-09-24 07:30 | 2026-09-24 12:30 | SHORT | SL | 5 | -3.00 | -1.42 | -4.42 |
| 30 | 2026-09-24 13:30 | 2026-09-24 19:30 | SHORT | SL | 6 | -3.00 | -0.26 | -3.26 |
| 31 | 2026-09-24 21:30 | 2026-09-24 22:30 | LONG | SL | 1 | -3.00 | -1.27 | -4.27 |
| 32 | 2026-09-25 03:30 | 2026-09-25 06:30 | SHORT | SL | 3 | -3.00 | -0.74 | -3.74 |
| 33 | 2026-09-25 07:30 | 2026-09-25 14:30 | SHORT | SL | 7 | -3.00 | -2.06 | -5.06 |
| 34 | 2026-09-25 16:30 | 2026-09-25 17:30 | LONG | SL | 1 | -3.00 | -1.00 | -4.00 |
| 35 | 2026-09-25 18:30 | 2026-09-26 13:30 | SHORT | SL | 19 | -3.00 | -0.48 | -3.48 |
| 36 | 2026-09-26 18:30 | 2026-09-27 06:30 | SHORT | SL | 12 | -3.00 | -0.92 | -3.92 |
| 37 | 2026-09-27 12:30 | 2026-09-28 03:30 | LONG | SL | 15 | -3.00 | -2.37 | -5.37 |
| 38 | 2026-09-28 05:30 | 2026-09-28 06:30 | LONG | SL | 1 | -3.00 | -1.15 | -4.15 |
| 39 | 2026-09-28 07:30 | 2026-09-28 21:30 | SHORT | SL | 14 | -3.00 | -2.25 | -5.25 |
| 40 | 2026-09-28 22:30 | 2026-09-29 00:30 | LONG | SL | 2 | -3.00 | -1.52 | -4.52 |
| 41 | 2026-09-29 05:30 | 2026-09-29 11:30 | SHORT | SL | 6 | -3.00 | -1.06 | -4.06 |
| 42 | 2026-09-29 12:30 | 2026-09-29 20:30 | LONG | SL | 8 | -3.00 | -0.80 | -3.80 |
| 43 | 2026-09-29 23:30 | 2026-09-30 17:30 | LONG | TP | 18 | +5.00 | +1.22 | +6.22 |
| 44 | 2026-09-30 19:30 | 2026-09-30 21:30 | SHORT | SL | 2 | -3.00 | -0.54 | -3.54 |
| 45 | 2026-09-30 22:30 | 2026-10-01 21:30 | SHORT | SL | 23 | -3.00 | -0.73 | -3.73 |
| 46 | 2026-10-01 22:30 | 2026-10-02 09:30 | LONG | TP | 11 | +5.00 | +1.17 | +6.17 |
| 47 | 2026-10-02 13:30 | 2026-10-02 19:30 | LONG | SL | 6 | -3.00 | -2.15 | -5.15 |
| 48 | 2026-10-02 20:30 | 2026-10-02 23:30 | SHORT | TP | 3 | +5.00 | +1.54 | +6.54 |
| 49 | 2026-10-03 01:30 | 2026-10-05 00:30 | LONG | TP | 47 | +5.00 | +1.66 | +6.66 |
| 50 | 2026-10-05 01:30 | 2026-10-05 04:30 | LONG | TP | 3 | +5.00 | +1.64 | +6.64 |
| 51 | 2026-10-05 05:30 | 2026-10-05 06:30 | SHORT | SL | 1 | -3.00 | -0.63 | -3.63 |
| 52 | 2026-10-05 08:30 | 2026-10-05 20:30 | SHORT | TP | 12 | +5.00 | +1.26 | +6.26 |
| 53 | 2026-10-05 21:30 | 2026-10-05 23:30 | SHORT | SL | 2 | -3.00 | -1.41 | -4.41 |
| 54 | 2026-10-06 11:30 | 2026-10-06 13:30 | SHORT | SL | 2 | -3.00 | -0.97 | -3.97 |
| 55 | 2026-10-06 14:30 | 2026-10-06 21:30 | LONG | SL | 7 | -3.00 | -1.15 | -4.15 |
| 56 | 2026-10-07 06:30 | 2026-10-07 07:30 | SHORT | TP | 1 | +5.00 | +1.98 | +6.98 |
| 57 | 2026-10-07 14:30 | 2026-10-07 18:30 | SHORT | TP | 4 | +5.00 | +2.38 | +7.38 |
| 58 | 2026-10-07 21:30 | 2026-10-08 07:30 | LONG | SL | 10 | -3.00 | -0.05 | -3.05 |
| 59 | 2026-10-08 08:30 | 2026-10-08 20:30 | SHORT | TP | 12 | +5.00 | +4.56 | +9.56 |
| 60 | 2026-10-08 21:30 | 2026-10-08 23:30 | SHORT | SL | 2 | -3.00 | -1.60 | -4.60 |
| 61 | 2026-10-09 00:30 | 2026-10-09 13:30 | LONG | TP | 13 | +5.00 | +2.37 | +7.37 |
| 62 | 2026-10-09 16:30 | 2026-10-11 13:30 | LONG | OPEN | 45 | +0.81 | -0.06 | +0.75 |

## Conclusion

ETH confirms the BTC exit in this sample. It made money on every BTC take-profit and lost on 38 of 39 BTC stop-losses. Size 5 on ETH is about one-third of the BTC notional and adds roughly 38% more dollar PnL on those exits, so the pair is a larger version of the BTC trade. Over these 30 days the BTC book finished at `-$7` and both legs together at `-$10.65`. This window does not show a new edge from the second leg. The `$10` daily loss limit would also be reached on more days once ETH losses are included.

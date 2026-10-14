# Backtest report

Candles: 15 Sep 2026 to 14 Oct 2026. Size 5, contract value 0.001, fee 0.059% per side. The −$10 day lock includes fees. Settings were scored on 15 Sep–7 Oct, then checked once on 8–14 Oct. The order log was not used to pick settings. Only its last 7 days of BTCUSD size 5 are this bot: price −$10.48, fees $7.57, net −$18.05.

## Current 1-hour live cross

`timeframe: 1h`, enter when price crosses the prior candle by 150 points, stop −$3, trail from $4, hard target $10.

| Window | Trades | Net |
|---|---|---|
| 15 Sep–7 Oct | 50 | −$23.87 |
| 8–14 Oct | 15 | −$11.37 |

## What was rejected

The highest training result was a 1-hour close breakout, 2-candle range, −$5 stop, trail from $6, target $12. Training net +$37.40 on 23 trades. The unseen week was −$16.46 on 3 trades. That file is not the production change.

648 combinations were run. 14 made money in training with at least 20 trades. Two of those also made money on 8–14 Oct. Both stay on the 4-hour chart with the existing −$3 / $4 / $3 / $10 exits.

## Production change

In `config.yaml`, two lines:

```yaml
timeframe: "4h"
```

```yaml
candle_close_confirmation: true
```

Leave these as they are:

- `order_size: 5`
- `breakout_lookback_candles: 1`
- `direction: "BOTH"`
- `confirmation.value: 150`
- `take_profit.value: 10`
- `stop_loss.value: 3`
- `trailing.activate_profit: 4`
- `trailing.distance: 3`
- `max_loss_per_day_dollar: 10`

The bot enters only after a completed 4-hour candle closes 150 points beyond the previous 4-hour high or low. The stop, trail, hard target, and day lock do not change.

## Result of that change

31 trades, net **+$28.20**. Worst day −$6.99, inside the $10 cap.

| Window | Trades | Wins | Net |
|---|---|---|---|
| 15 Sep–7 Oct | 25 | 13 | +$25.55 |
| 8–14 Oct | 6 | 4 | +$2.65 |

The unseen week is six trades and +$2.65. That is the check, not a promise. Restart the bot after saving `config.yaml`. The fee-aware −$10 day lock is already in `main.py` and needs that restart too.

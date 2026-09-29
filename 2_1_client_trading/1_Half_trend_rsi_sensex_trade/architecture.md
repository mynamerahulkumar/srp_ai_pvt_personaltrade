# Half Trend Dhan Options Algo

## 1. Overview

This bot buys index options on Dhan. It does not buy or sell the index itself.

The index chart supplies the signal. A confirmed candle that closes through the Half Trend line chooses the option side:

- close crosses above the line: buy a call (CE)
- close crosses below the line: buy a put (PE)

The default index is SENSEX. NIFTY and BANKNIFTY are the other choices in `config.yaml`. SENSEX options use the BSE F&O segment. NIFTY and BANKNIFTY options use the NSE F&O segment. Index candles and the option chain use Dhan's index segment `IDX_I`.

`config.yaml` is the only trading configuration. `.env` is the only credential file. The default mode is PAPER. PAPER uses Dhan for prices and never calls `place_order`. LIVE uses the same signal path and then sends real orders after a printed preview.

The process polls every 5 seconds by default. That poll updates prices, gross P&L, take profit, stop loss, and the dashboard. A new trade decision waits for a new confirmed candle. The default candle is 5 minutes.

Take profit and stop loss are measured on the option premium. Combined gross P&L is the sum of this bot's closed trades and the open option. It is not the whole Dhan account. The daily loss limit is in rupees.

There is no database, web UI, or second broker.

## 2. Strategy Concept

Half Trend is a path that sits under price in an up swing and above price in a down swing. Two settings shape it:

- `amplitude` is how many recent bars are used for the high average, low average, and the extreme high or low. The default is 2.
- `channel_deviation` widens an ATR channel around that line. The default is 2. The channel is stored for inspection. The buy signal does not use the channel.

The line has an internal trend state. Trend 0 tracks a rising "max low" support-style value. Trend 1 tracks a falling "min high" resistance-style value. The state flips when the short high/low averages and the close break the tracked extreme.

A confirmed candle is one whose open time plus the timeframe is already in the past. The bot drops the still-forming candle before it calculates the indicator. Polling every 5 seconds does not create a 5-second signal.

Bullish means the previous confirmed close was on or below the line and the new confirmed close is above it. Bearish is the opposite cross. Staying on one side of the line does not create another order.

## 3. Mathematical Explanation

The formula is a readable Everget-style Half Trend. It is not a claim that Dhan's private indicator source was copied. The chart behavior this bot follows is the close-versus-line cross described above. Dhan's own arrows, if they use a different rule, are not reproduced here.

For each confirmed bar `i`, using only bars at or before `i`:

1. True range is the largest of
   - high - low
   - abs(high - previous close)
   - abs(low - previous close)
2. ATR is Wilder's average of true range with length 100. That length is the constant `ATR_PERIOD` in `main.py`. It is not a config setting, so the published-style formula stays intact. Until 100 true ranges exist, ATR is empty and the channel is empty. The line itself can still be calculated.
3. `atr2 = ATR / 2`
4. `deviation = channel_deviation * atr2`
5. Over the last `amplitude` bars:
   - `high_price` is the high of the bar with the highest high. A tie uses the newer bar.
   - `low_price` is the low of the bar with the lowest low. A tie uses the newer bar.
   - `high_ma` and `low_ma` are the simple averages of those highs and lows.
6. Trend state, matching the usual Half Trend recurrence:
   - When the next-trend flag is 1, `max_low = max(low_price, max_low)`. If `high_ma < max_low` and the close is below the previous low, trend becomes 1 and the tracked high is reset.
   - Otherwise `min_high = min(high_price, min_high)`. If `low_ma > min_high` and the close is above the previous high, trend becomes 0 and the tracked low is reset.
7. The line:
   - Trend 0: on a fresh flip, the line takes the previous down value. Otherwise it is `max(max_low, previous up line)`.
   - Trend 1: on a fresh flip, the line takes the previous up value. Otherwise it is `min(min_high, previous down line)`.
8. Upper channel = line + deviation. Lower channel = line - deviation.

The signal then uses only the closes and the line:

- previous close <= previous line and current close > current line → `BUY_CE`
- previous close >= previous line and current close < current line → `BUY_PE`

No bar looks at a later bar. A check that recomputes the first 50 rows alone matches row 50 of the full series.

## 4. Signal Generation

Bullish path:

```
SENSEX candle
→ Half Trend calculated on confirmed candles
→ confirmed close crosses above the line
→ BULLISH / BUY_CE
→ select expiry
→ select ATM, ITM, or OTM call
→ read that contract's security id and lot size
→ BUY CE
→ monitor the option premium
→ exit on TP, SL, opposite signal, session, daily loss, or stop
```

Bearish path:

```
SENSEX candle
→ Half Trend calculated on confirmed candles
→ confirmed close crosses below the line
→ BEARISH / BUY_PE
→ select the put with the same expiry and strike rules
→ BUY PE
→ monitor the option premium
→ exit on the same rules
```

The same candle timestamp cannot open a second entry. An opposite-signal exit does not buy the other option in that same cycle.

## 5. Architecture

```
config.yaml
→ main.py
→ Dhan authentication from .env
→ security master
→ underlying candles and quotes
→ Half Trend
→ signal
→ option chain, only when a contract must be chosen
→ option selection
→ PAPER fill or LIVE order
→ one position
→ premium P&L, TP, and SL
→ CLI
```

`stop.py` only writes `.bot_stop`. It does not connect to Dhan.

## 6. Function-by-Function Explanation

Helpers that only parse YAML, pad the dashboard, or unwrap a Dhan envelope are omitted here. The functions below are the ones that decide trades.

### Configuration and connection

`load_config` reads `config.yaml` and returns `AppConfig`. Unknown keys are rejected so a typo cannot be ignored. It fails if the file is missing or is not valid YAML.

`validate_config` checks enums, the SENSEX/NIFTY/BANKNIFTY names, PAPER/LIVE, the Dhan candle intervals 1/5/15/25/60, lot count, TP/SL, the daily loss, the session clock, and the holding/product pair. INTRADAY holding requires the INTRADAY product. SWING and POSITIONAL require MARGIN. `max_open_strategies` must be 1. A bad value stops startup before any order.

`load_environment` reads `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` from `.env`. Empty values stop startup. The token is never printed.

`create_dhan_client` builds `DhanContext` and `dhanhq(context)`. This is the only broker client.

`sdk_value` reads a constant from the installed SDK, such as `INDEX` or `INTRA`. If the constant is missing, the bot stops instead of inventing one.

### Security master and contracts

`load_security_master` uses `api-scrip-master.csv` when that file is already present. Otherwise it calls `dhanhq.fetch_security_list("compact")` once and saves the CSV. The frame stays in memory. The poll loop does not download it again.

`resolve_underlying` finds the index row by exchange and symbol name. SENSEX is looked up on BSE. NIFTY and BANKNIFTY are looked up on NSE. Security ids are not hardcoded. Zero matches or more than one distinct id stops the bot.

`resolve_option_contract` finds the chain's security id in the master and reads the trading symbol, CE/PE, strike, expiry, lot size, and tick size. A missing row or a CE/PE mismatch stops that order.

`fetch_expiry_list` calls `expiry_list` for the resolved index. `choose_expiry` keeps the earliest expiry on or after today in NEAREST mode. CONFIGURED mode requires the YAML date to be in Dhan's list. A missing expiry does not trade.

`fetch_option_chain` calls `option_chain` and waits so two chain calls are at least about 3 seconds apart. `select_option_contract` picks the strike. ATM is the nearest listed strike that has the requested side. ITM and OTM move by steps in that listed strike list. Offset 0 on ITM or OTM means one step. A call's OTM strike is higher. A put's OTM strike is lower. The reverse is ITM.

`order_quantity` is `lots × lot size`. `limit_price` uses the ask for a buy and the bid for a sell, falls back to the premium, and rounds to the contract tick.

### Market data

`fetch_underlying_candles` calls `intraday_minute_data` with instrument type `INDEX` and the configured interval. It keeps only the recent bars needed for ATR 100 plus amplitude. `drop_unconfirmed_candle` removes a candle whose close time is still in the future. `candles_need_refresh` waits until the next candle is due and then spaces history calls by at least 20 seconds.

`fetch_ltp` and `fetch_quotes` call `ticker_data`, then `ohlc_data` if needed. Quote calls are spaced by about one second. A missing price becomes a data error, not a guessed price.

`refresh_quotes` updates the index and, when a position is open, that option's premium in one quote request.

### Half Trend and signals

`calculate_atr` is the Wilder ATR used by the channel. `calculate_half_trend` writes `half_trend`, `ht_trend`, `ht_upper`, and `ht_lower`. `generate_signal` reads the last two confirmed rows. `describe_candle_side` explains a cross or the lack of one for the dashboard. `is_new_confirmed_candle` is false when that candle open time was already evaluated.

`refresh_indicator` fetches candles only when one should have closed, then recalculates the line. `process_signal` runs the cross, the opposite-signal exit, and the entry. `consider_entry` either sends the order or records why it did not. A block that can clear later, such as "session has not started", is retried on that same candle. A hard block, such as the order cap, consumes the candle.

### Orders and position

`validate_entry` requires a flat bot, a fresh quote, an open market, an open session, room under the daily entry cap, and no stop-loss or daily-loss lock.

`place_entry_order` fetches the chain, selects the contract, prints a preview, and calls `paper_enter` or `live_enter`. It does not call `place_order` itself.

`paper_enter` stores a long option filled at the preview price and increments the entry count. It raises if mode is not PAPER. `paper_exit` records the gross result and clears the position. It also raises if mode is not PAPER.

`live_enter` prints the preview, sets `ENTRY_PENDING`, and calls `place_order` once. The position is created only after status `TRADED`. `live_exit` does the same for one sell and uses `EXIT_PENDING`. A pending order blocks another order of that kind.

`check_order_status` reads `get_order_by_id`. `REJECTED`, `CANCELLED`, and `EXPIRED` on an entry return the bot to flat without a replacement. The same statuses on an exit keep the position and do not send a second sell. If a submit response is unclear, the bot looks up its order tag before it will consider the request lost.

`close_current_position` is the only exit used by TP, SL, the opposite signal, the session, the daily loss, and shutdown. `place_exit_order` calls that same function.

`get_current_position` returns this algo's memory position, not every Dhan position. `reconcile_live_positions` runs only in LIVE at startup. It adopts an open F&O quantity only when today's order tag starts with `HT` and the security id matches. Any other open F&O quantity stops the bot with `EXISTING LIVE POSITION DETECTED`. `refresh_broker_position` later notices if the broker is already flat, and it refuses to trade if the quantity no longer matches.

`calculate_tp_sl` converts percent or absolute settings into premium prices and rounds them to the tick. `calculate_position_pnl` is `(mark - entry) × quantity`. `calculate_combined_algo_pnl` adds realized and unrealized gross results.

### Risk, session, and shutdown

`check_take_profit` is true when the option premium is at or above the target. `check_stop_loss` is true at or below the stop. A missing premium is not a hit. `check_daily_loss` compares combined gross P&L with `-max_loss_per_day_inr`. `check_order_limit` counts successful entries only. `check_opposite_signal` is true when a call is open and the new signal is bearish, or a put is open and the new signal is bullish, and the YAML flag is on.

`apply_risk_exits` checks take profit, then stop loss, then the daily loss. Each of those closes and then stops the process. If the live exit is still pending, the stop waits until the fill is confirmed.

`check_market_closed` is true on weekends and outside 09:15–15:30 Asia/Kolkata. `check_trading_session` returns `OPEN`, `BEFORE_START`, or `AFTER_STOP`. CONTINUOUS mode, and a disabled session, do not use the clock window. `enforce_session` blocks new entries after the stop time. For INTRADAY holding it can close the algo option when `close_all_positions_at_stop` is true. `stop_bot_after_close` decides whether the process then exits. Those two flags are separate.

`check_manual_stop` is true when `.bot_stop` exists. `graceful_shutdown` cancels a not-yet-filled entry, closes when the reason requires it, waits briefly for a live exit, prints the gross summary, deletes the stop file, and exits. SWING and POSITIONAL positions are not force-closed only because the intraday clock ended. If the broker quantity no longer matches, shutdown does not guess and does not send a sell.

`roll_trading_day` resets the entry count, realized P&L, and the day's locks at the next exchange date. An open position is kept.

`render_startup` prints the resolved id, sample lot, and, in LIVE mode, `WARNING: LIVE TRADING ENABLED`. `render_dashboard` redraws the status box from memory. It does not call Dhan. `run_cycle` is one poll. `main` is the process entry.

## 7. Complete Trade Lifecycle

```
START
→ load config.yaml
→ load .env
→ connect to Dhan
→ load the security master
→ resolve the index id
→ fetch expiries and the chain
→ fetch confirmed candles
→ calculate Half Trend
→ in LIVE, reconcile positions
→ print the startup card
→ poll
→ on a new confirmed cross, select the option
→ print the order preview
→ PAPER fill or LIVE submit
→ wait until LIVE status is TRADED
→ store one long option
→ each poll: premium, gross P&L, TP, SL, daily loss
→ exit
→ record gross P&L
→ stop, or wait for a later candle if the exit was only an opposite signal
```

## 8. Example Bullish Trade

These numbers illustrate the arithmetic. They are not a forecast.

- Underlying: SENSEX
- Signal: bullish cross
- Option: ATM CE
- Entry premium: ₹100
- Quantity: 20
- Take profit: 20 percent → ₹120
- Stop loss: 10 percent → ₹90

If the premium reaches ₹120:

```
gross P&L = (120 - 100) × 20 = ₹400
```

If the premium reaches ₹90:

```
gross P&L = (90 - 100) × 20 = -₹200
```

The bot then stops. Brokerage, STT, GST, exchange charges, and slippage are not in that figure. A live fill can also differ from the preview price.

## 9. Example Bearish Trade

A bearish cross buys a put. The premium math is the same because the position is still a long option.

- Signal: bearish cross
- Option: ATM PE
- Entry premium: ₹100
- Quantity: 20
- Take profit: ₹120
- Stop loss: ₹90

A rise in the put premium to ₹120 is ₹400 gross. A fall to ₹90 is -₹200 gross. The index falling does not by itself mean the put premium rose. The bot marks the option, not the index.

## 10. Option Selection Examples

The chain's own strikes are the grid. Nothing in the code assumes 50, 100, or any other fixed gap.

Suppose the listed strikes around spot 80,010 are 79,800, 80,000, and 80,200.

- ATM is 80,000 for both the call and the put.
- OTM call, one step: 80,200 CE
- ITM call, one step: 79,800 CE
- OTM put, one step: 79,800 PE
- ITM put, one step: 80,200 PE

`strike_offset` counts steps. ATM must use 0. For ITM and OTM, 0 means the first step and 2 means two steps. If that step is outside the chain, no order is sent.

## 11. TP/SL

Both levels are premium prices, rounded to the contract tick.

- Percent take profit: `entry × (1 + value / 100)`
- Percent stop: `entry × (1 - value / 100)`
- Absolute take profit: `entry + value`
- Absolute stop: `entry - value`

The check uses the latest option LTP. If that quote is missing, the bot shows a data error and does not pretend the target was hit. Take profit is tested before the stop. A hit closes the option and stops the process. The bot does not immediately buy again.

## 12. Combined P&L

```
combined gross = realized gross + unrealized gross
```

Realized gross is the sum of `(exit premium - entry premium) × quantity` for trades this process closed. Unrealized gross uses the current premium of the one open option. The daily loss limit compares that combined figure with a rupee floor. At the default of 500, combined gross at or below -₹500 closes the option and stops the bot.

This is labeled gross on the dashboard and in the final summary. It is not net of charges, and it is not the P&L of unrelated Dhan positions.

The entry cap default is 10 successful buys. After that, new entries stop and an open option can still be exited. Exit orders are counted separately and do not consume the entry cap.

## 13. Parameter Tuning

Changing a setting changes how often the bot acts and how far an option can move before it is sold. None of these settings is a promise of profit. A value that looks calmer in one market can be late in another.

Evaluate changes with historical study, paper trading, and forward observation. Include transaction costs, slippage, and more than one kind of market. PAPER mode is the first place to watch the actual Dhan premiums before LIVE is considered.

- Smaller amplitude reacts to shorter swings and can produce more crosses, including noisy ones.
- Larger amplitude waits for a wider swing and can produce fewer crosses.
- A smaller candle timeframe produces more confirmed bars and can produce more signals.
- A larger timeframe produces fewer, slower signals.
- Near expiry changes how fast an option premium moves. A later expiry usually moves less for the same index move. NEAREST and CONFIGURED are both available so that choice is explicit.
- ATM is closest to the index. ITM costs more premium and is deeper. OTM costs less premium and is farther out. The offset picks how many listed strikes away.
- A higher take profit needs a larger premium move before the winner is sold. A lower one sells sooner.
- A wider stop allows a larger adverse premium move. A tighter stop caps that move and can exit more often.
- A shorter poll sees the premium sooner. It does not create extra candles. The default of 5 seconds is only the monitoring pace.
- The only confirmation implemented is `CANDLE_CLOSE`.
- Quantity is lots times the master lot size. More lots scale both gains and losses. The lot size itself comes from the contract, not from a constant in this file.

## 14. Risk Management

The bot holds one long option. It does not pyramid, sell naked options, or send a call and a put together.

- Quantity must be a multiple of the security-master lot size.
- Successful entries stop at `max_orders_per_day`. Exits still run.
- Combined gross loss at the rupee limit closes and stops.
- A stop-loss exit stops the bot. `no_reentry_after_stop_loss` is also checked so a later change cannot quietly buy again after that lock.
- The same confirmed candle cannot open two entries. A pending order blocks another order.
- LIVE startup will not trade if an open F&O position cannot be tied to this bot's `HT` order tag.
- Stale candles and missing quotes block new entries. They do not count as TP or SL.
- Temporary API errors are printed and retried. They do not send a duplicate order. Repeated failures stop the process.
- PAPER and LIVE share the signal code and diverge at the entry and exit functions. LIVE is printed at startup.
- Session stop and market close can close this algo's INTRADAY option when that flag is on. They do not close the rest of the account.

## 15. Market Closed Behavior

The exchange window used here is 09:15 to 15:30 on weekdays, in the configured timezone. Outside that window, and on Saturday and Sunday, the bot does not create candles and does not enter.

After the close it still shows the last index price, the last option premium, the last Half Trend value, the last candle time, the position, and gross P&L, when those values exist.

The default post-close sequence is 3 polls, 10 seconds apart, about 30 seconds. Each poll is labeled `MARKET CLOSED` and includes the poll count. No new trade is placed during those polls.

When the polls finish, an INTRADAY bot with `close_all_positions_at_stop` closes its own option. If `stop_bot_after_close` is true, the process then prints the summary and exits. SWING and POSITIONAL do not force that clock exit. A holiday calendar is not built in. If the feed goes stale during cash hours, new entries stay blocked.

## 16. stop.py

From the project directory:

```
python stop.py
```

That writes `.bot_stop` next to `main.py`. It does not read `.env`, import the bot, or send an order.

`main.py` looks for the file during each poll and during the one-second countdown. On a hit it stops new entries, cancels a working entry, closes the algo option when `close_all_positions_at_stop` is true, prints gross P&L, deletes the stop file, and exits.

A stop file left behind by an older run is removed when `main.py` starts, so the new process is not stopped immediately. Ctrl+C uses the same manual-stop path.

## 17. AWS 1 GB VM

The bot is a single Python process. It keeps a trimmed candle frame, one option chain when it needs a contract, and the security-master table. It does not run a database, Redis, a web server, or a browser.

On a small Linux VM:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
```

Edit `.env` with the Dhan client id and access token. Edit `config.yaml` if the defaults are not the ones you want. Leave `trading_mode` as `PAPER` until the dashboard and paper fills look right.

```
python main.py
```

In another terminal:

```
python stop.py
```

Quotes, candles, and the option chain need an active Dhan Data Plan. LIVE order placement also needs Dhan's static-IP allow list. Those are account settings, not something this repository can turn on.

The first start downloads `api-scrip-master.csv` if it is not already there. Later starts reuse the file. Delete it when you want a fresh master.

## 18. Troubleshooting

- Missing credentials: `.env` must contain both `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN`. The bot exits before trading and does not print the token.
- Data plan: history, quotes, and the option chain fail without an active Dhan Data Plan. The error text is shown as a data error. Orders are not sent to "work around" a data failure.
- Static IP: LIVE `place_order` can fail even when data works if the IP is not whitelisted on Dhan.
- Security id not found: the master has no index row or no option row for the chain id. The bot stops or skips the order. It does not substitute a remembered id.
- Option chain unavailable: startup exits. An empty chain during a signal skips the order.
- Invalid expiry: CONFIGURED mode exits when that date is not in `expiry_list`.
- Invalid strike: an offset past the listed strikes skips the order.
- Order rejected or cancelled: the entry returns to flat and does not send a replacement for that attempt. An exit rejection keeps the position and does not fire a second sell by itself.
- Order pending: the state stays `ENTRY_PENDING` or `EXIT_PENDING`. Another order of that kind is not sent.
- Market closed: the dashboard says `MARKET CLOSED`. Entries stay off.
- Stale data: the latest confirmed candle is older than two timeframes during cash hours. Entries stay off. The last Half Trend value can still be displayed.
- Rate limit: quote and chain calls are spaced. A rate-limit error is printed and retried. It does not create a second order.
- Invalid YAML: startup prints the parser or validation error and exits.
- PAPER and LIVE: the startup card and the dashboard both show the mode. LIVE also prints `WARNING: LIVE TRADING ENABLED`. PAPER cannot reach `place_order`.
- Duplicate orders: one position, one pending order, and one signal per confirmed candle. Shutdown cancels a working entry rather than leaving it to fill after the stop.

## 19. Code-to-Architecture Mapping

| Piece | Functions in main.py |
| --- | --- |
| Config | `load_config`, `validate_config` |
| Credentials | `load_environment`, `create_dhan_client` |
| Security master | `load_security_master`, `resolve_underlying`, `resolve_option_contract` |
| Candles and quotes | `fetch_underlying_candles`, `fetch_quotes`, `fetch_ltp`, `refresh_quotes` |
| Expiry and chain | `fetch_expiry_list`, `choose_expiry`, `fetch_option_chain`, `select_option_contract` |
| Half Trend | `calculate_atr`, `calculate_half_trend` |
| Signal | `generate_signal`, `is_new_confirmed_candle`, `process_signal` |
| Paper execution | `paper_enter`, `paper_exit` |
| Live execution | `live_enter`, `live_exit`, `check_order_status` |
| Position and P&L | `get_current_position`, `calculate_position_pnl`, `calculate_combined_algo_pnl`, `close_current_position` |
| Risk | `calculate_tp_sl`, `check_take_profit`, `check_stop_loss`, `check_daily_loss`, `check_order_limit` |
| Session and close | `check_trading_session`, `check_market_closed`, `enforce_session`, `poll_closed_market` |
| Manual stop | `check_manual_stop`, `graceful_shutdown` |
| CLI | `render_startup`, `render_dashboard` |
| Loop | `run_cycle`, `main` |

## 20. Future Portability

The indicator and the cross rule do not call Dhan. `calculate_half_trend`, `generate_signal`, `calculate_tp_sl`, and the position P&L math can stay as they are if prices and orders later come from somewhere else.

Dhan-specific work is grouped in the client, the security master, the history and quote calls, the option chain, and `live_enter` / `live_exit`. PAPER and LIVE already share the signal and diverge only at those execution functions.

No second broker is implemented. There is no broker interface module and no extra framework. Adding one later would mean replacing the data and order functions inside this file, not wrapping the strategy in a new package first.

---

The implementation, the comments in `main.py`, the function explanations above, the signal and trade flows, the premium examples, and the parameter notes are written so a trader can read what the bot actually does. They are technical educational material. They can be reused in notes or structured lessons about how this bot is built. They do not say that the strategy is profitable, and they do not say that any parameter will make money. The responsible way to study it is paper trading, historical review, and forward observation with costs included, before any live order is enabled.

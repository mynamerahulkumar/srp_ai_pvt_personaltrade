# Delta Exchange India Crypto Breakout Algo

This document explains the perpetual-futures breakout bot in this folder. The runnable files are `main.py`, `config.yaml`, `.env`, `env.example`, `stop.py`, and `requirements.txt`. Trading settings live only in `config.yaml`. API keys live only in `.env`. The bot never imports `docs/`.

LIVE mode can lose real money. 100x leverage increases margin use and liquidation sensitivity. It does not improve expected profit.

---

## 1. Introduction

The bot is a single Python process. It polls Delta Exchange India, evaluates a completed-candle breakout on one perpetual futures symbol, and manages at most one strategy position at a time.

Default operation is PAPER: real market data, simulated fills, no live orders. LIVE sends signed REST orders to `https://api.india.delta.exchange/v2`.

It is designed to run locally or on a 1 GB RAM AWS Linux VM. There is no database, web UI, or Docker requirement.

---

## 2. Strategy Objective

Identify a break of a prior N-candle high or low, confirm it by a configurable distance (points or percent), enter in that direction, and exit on take-profit, stop-loss, daily loss, or a configured session close.

The objective is a complete, inspectable execution engine. Parameter tuning can change historical behaviour. It cannot guarantee future profit.

---

## 3. Delta Exchange India Futures

India production REST:

```text
https://api.india.delta.exchange/v2
```

Authenticated calls sign:

```text
HMAC-SHA256(secret, METHOD + timestamp + /v2<path> + ?query + compact_json_body)
```

Headers: `api-key`, `timestamp`, `signature`. The signature is never printed.

This project trades perpetual futures only. Option chains, expiries, strikes, and multi-leg condor logic are not used.

---

## 4. Supported Symbols

Configured with `symbol` in `config.yaml`. One process trades one symbol.

| Config value | Resolved product |
|---|---|
| BTCUSD | BTCUSD |
| ETHUSD | ETHUSD |
| SOLUSD | SOLUSD |
| XAUSD | XAUTUSD |
| XAUTUSD | XAUTUSD |

Product id, tick size, contract value, min size, and tradable state are taken from `GET /products/{symbol}` at startup. Reference tables in `docs/` are hints only.

---

## 5. Perpetual Futures Concept

A perpetual future has no expiry. PnL is:

```text
(price change) × contract size × contract_value
```

`contract_value` is product-specific. A 1-lot BTCUSD move is not the same dollar amount as 1-lot ETHUSD, SOLUSD, or XAUTUSD. The bot never assumes they match.

Leverage changes margin and liquidation distance. It does not change the raw dollar PnL formula above.

---

## 6. Breakout Trading Concept

On each newly completed candle C:

1. Build the reference range from the previous `breakout_lookback_candles` completed candles. C is not in that range.
2. The still-forming candle is never in the range.
3. LONG if the confirmation price is above `range_high` plus the buffer.
4. SHORT if the confirmation price is below `range_low` minus the buffer.

Staying above a level is not a new signal. A unique signal identity is consumed after an entry attempt.

---

## 7. Candle Timeframes

`timeframe` may be `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `4h`, or `1d`. Default is `5m`.

Every candle opens and closes on `timezone` (`Asia/Kolkata`), not on a UTC boundary.

```text
5m: 21:00–21:05, then 21:05–21:10
1h: 21:00–22:00
4h: 20:00–00:00, 00:00–04:00, and so on
1d: 00:00–24:00 the same calendar day
```

At 21:03 on a 5-minute chart, the previous candle is 20:55–21:00. The previous day is yesterday 00:00–24:00 IST.

`1m` through `30m` are fetched at that resolution. `1h`, `4h`, and `1d` are built from 5-minute candles so the window follows the Indian clock. Fetch size is lookback plus a small buffer.

---

## 8. Lookback Range

```yaml
strategy:
  breakout_lookback_candles: 1
```

`1` means the previous completed candle of the configured `timeframe`. The still-forming candle is never included.

```text
5m: range_high / range_low = previous completed 5-minute candle
1d: range_high / range_low = previous completed daily candle (yesterday while today is forming)
```

The dashboard shows that live range (`completed[-lookback:]`). With `candle_close_confirmation: false`, the live price is tested against that same range. With it true, a newly closed candle is tested against the candles before it.

If fewer than lookback completed candles are available, the dashboard waits. Close confirmation needs one extra completed candle.

---

## 9. Price-Point Confirmation

```yaml
confirmation:
  enabled: true
  mode: "POINTS"   # or PERCENT
  value: 50
```

POINTS:

```text
long_level  = range_high + 50
short_level = range_low  - 50
```

PERCENT:

```text
long_level  = range_high × (1 + value/100)
short_level = range_low  × (1 - value/100)
```

Levels are snapped to the live tick size with half-up rounding. BTCUSD tick 0.5 is not ETHUSD tick 0.05.

---

## 10. Long Breakout

If `strategy.direction` is `LONG` or `BOTH`, and the confirmation price is strictly above `long_level`, the signal is LONG. LIVE/PAPER then buy `order_size` contracts of the selected perpetual.

---

## 11. Short Breakout

If direction is `SHORT` or `BOTH`, and the confirmation price is strictly below `short_level`, the signal is SHORT. The entry side is sell.

---

## 12. Candle Close Confirmation

```yaml
confirmation:
  candle_close_confirmation: false
```

When false, a market order is sent as soon as the live price crosses `long_level` or `short_level`. The range is the last completed candle shown on the dashboard (previous 5-minute bar, or previous day when `timeframe` is `1d`). The signal identity still prevents one breakout from becoming another order on every poll.

When true, only a completed candle close beyond the level can fire. That close is tested against the candles before it, so a bar is never compared with its own high and low.

---

## 13. Breakout Signal Lifecycle

```text
NONE → LONG or SHORT + metadata
```

Metadata: `breakout_level`, `range_high`, `range_low`, `candle_timestamp`, `confirmation_price`, `reason`, `signal_id`.

`signal_id` is:

```text
symbol | timeframe | direction | range_high | range_low | candle_timestamp
```

After an entry attempt that identity is consumed. A later poll with price still beyond the level does not send another order. A new completed candle produces a new identity only if the range and timestamp change.

`allow_reentry_after_exit: false` still requires a new identity. The bot never re-enters just because price remains through the old level.

---

## 14. Order Execution

PAPER: simulate fill at last ticker price. No `POST /orders`.

LIVE: one signed `POST /orders` with a deterministic `client_order_id` (max 32 chars). Market orders send no limit price. Limit orders use the last price snapped to tick; if still unfilled after a short wait they are cancelled and not blindly retried.

Unknown POST result → `GET` the order by client id. Never a second submit of the same signal.

---

## 15. Market Order

Default `order_type: market_order`. Fill is whatever the book gives. Trigger PnL and final realized PnL can differ because of slippage and fees. That is expected.

---

## 16. Duplicate Order Protection

Before entry the bot checks:

1. Local state (not ENTERING/EXITING, no open strategy position)
2. Exchange position for this product
3. Open/pending orders for this product (LIVE)
4. Consumed signal id
5. Last processed candle timestamp
6. `max_open_strategies`
7. `max_orders_per_day`
8. Daily loss limit
9. Schedule / stale data

`max_open_strategies` default is 1. A second position is never opened while the first is active.

---

## 17. Position Management

One logical strategy position is tracked:

```text
direction, quantity, entry_price, order ids, breakout metadata, paper flag
```

States: STARTING, WAITING, SCANNING, SIGNAL_FOUND, VALIDATING, ENTERING, LONG, SHORT, EXITING, CLOSED, STOPPED, ERROR.

Exits are reduce-only and only for this product. Unrelated user positions are not touched. There is no account-wide close-all.

---

## 18. Combined Strategy PnL

TP/SL use strategy PnL, not a random ticker field.

LIVE prefers exchange `unrealized_pnl` when present. Otherwise:

```text
LONG  PnL = (mark − entry) × qty × contract_value
SHORT PnL = (entry − mark) × qty × contract_value
```

Paper always uses that formula. Daily PnL is Kolkata-day realized plus current unrealized.

---

## 19. Take Profit

```yaml
take_profit:
  enabled: true
  mode: "PNL"     # or PERCENT
  value: 5
```

PNL: point move = `value / (quantity × contract_value)`. LONG target is `entry + move`. SHORT target is `entry − move`.

PERCENT: LONG target `entry × (1 + value/100)`; SHORT `entry × (1 − value/100)`.

After a live market fill, both prices are sent on `POST /orders/bracket`. Each leg is a `limit_order` with `stop_price` and `limit_price`. If that call fails, the bot immediately places a reduce-only take-profit limit and a reduce-only stop-loss limit on `POST /orders`. Paper mode logs the same prices and still exits in software.

`stop_after_tp: true` exits the Python process after the bracket fill or a verified close.

---

## 20. Stop Loss

```yaml
stop_loss:
  enabled: true
  mode: "PNL"
  value: 5
```

PNL: the same point move as take-profit, on the loss side of the entry. PERCENT: LONG `entry × (1 − value/100)`; SHORT `entry × (1 + value/100)`.

After a live market fill, the stop is a `limit_order` on `POST /orders/bracket` with both `stop_price` and `limit_price` set to that level, triggered on `mark_price`. A plain limit on the loss side would fill immediately. The stop price keeps it inactive until mark trades through it.

If `POST /orders/bracket` fails, the same two prices are placed at once as separate reduce-only orders: a take-profit limit, and a stop-loss limit with `stop_order_type: stop_loss_order`. If those also fail, the bot keeps the poll-based market exit. If a later poll finds the exchange already flat, that fill is recorded and no second market order is sent. A daily-loss or scheduled flatten cancels this product’s open orders first.

This is the trade stop. It is not the daily loss limit.

`stop_after_sl: true` exits the process after a verified SL close.

---

## 21. Daily Loss Limit

```yaml
max_loss_per_day_dollar: 5
```

Trading day is `Asia/Kolkata`. If realized + unrealized ≤ −$5:

1. New entries are blocked.
2. The active strategy position is closed (reduce-only, this product).
3. The process stops after that close.

The dashboard prints `DAILY LOSS LIMIT REACHED` / `NEW TRADES BLOCKED`.

---

## 22. Maximum Orders Per Day

```yaml
max_orders_per_day: 10
```

Entry fills increment the counter. TP/SL/daily/scheduled exits are never blocked by this limit. Risk exits always have priority.

---

## 23. Scheduling

```yaml
run_mode: "CONTINUOUS"   # or SCHEDULED
timezone: "Asia/Kolkata"
start_time: "09:30"
stop_time: "23:00"
```

CONTINUOUS ignores the clock.

SCHEDULED allows entries only inside the window. Overnight windows (start after stop) are supported.

---

## 24. Continuous Mode

The process trades for as long as `python main.py` runs, subject to risk limits. Use this for swing/positional style. Manual `stop.py` still does not flatten.

---

## 25. Day Trading Mode

```yaml
day_trading_enabled: true
close_positions_at_stop_time: true
stop_bot_after_close: true
```

When `run_mode` is SCHEDULED and these flags are true, stop_time:

1. Blocks new entries
2. Closes this strategy position
3. Verifies size is 0
4. Exits Python if `stop_bot_after_close` is true

`stop_bot_after_close` means exit after a close. It does not itself mean “close”.

---

## 26. Swing Trading Configuration

Example: `run_mode: CONTINUOUS`, `day_trading_enabled: false`, `stop_after_tp/sl` as desired. Positions may stay open across local midnight. Daily loss still resets on the Kolkata date.

---

## 27. Positional Trading Configuration

Same as swing: CONTINUOUS, do not enable scheduled flatten. TP/SL and daily loss remain the exits. Manual stop leaves the position open.

---

## 28. Manual Stop

```bash
python stop.py
```

Writes `.bot_stop_signal`. `main.py` notices it on the next poll, stops new trades, and exits. It does not close positions, cancel account orders, or call Delta.

A killed Python process also does not close exchange positions.

---

## 29. TP/SL Exit Lifecycle

```text
TP/SL triggered
→ lock exit_in_progress (no stacked exits)
→ block new entries
→ reduce-only market close of this product
→ verify exchange size is 0
→ record realized PnL
→ stop process if configured
```

If verify fails, the bot reports the position still open and retries on a later poll. It does not spray exits every 5 seconds while the lock is held; the lock clears if the order failed so a later poll can try again.

---

## 30. Position Reconciliation

On startup and each poll (LIVE with credentials):

- Query this product’s position
- If size ≠ 0 and local state is empty → resume LONG/SHORT and monitor TP/SL
- If exchange is flat → drop any stale local LIVE position and scan
- PAPER never treats a live leftover position as a paper trade; it warns and will not stack

There is no `runtime_state.json`. A restart plus a flat exchange starts a new idle cycle.

---

## 31. Paper Trading

PAPER fetches real products, tickers, and candles. Entries, exits, and PnL are simulated. Mutations are refused even if credentials exist.

If a live position is already on the account, PAPER logs a warning and does not close it.

---

## 32. Live Trading

LIVE requires `DELTA_API_KEY` and `DELTA_API_SECRET`. The CLI shows `MODE: LIVE` in red, a leverage warning, and an extra warning before the first real order.

Market orders can slip. After a live fill the bot rests a take-profit limit and a stop-limit on `POST /orders/bracket`. If that call fails, it places the two reduce-only orders immediately. Software polling remains only when both attempts fail.

---

## 33. Main.py Architecture

Logical sections inside the single file:

1. Imports and constants
2. Config / environment / logging
3. Dataclasses (`Config`, `ProductMeta`, `BreakoutSignal`, `StrategyPosition`, `Runtime`)
4. `DeltaClient` (sign, GET retries, mutation guard)
5. Product, candles, ticker
6. Breakout math and signal detection
7. PnL, TP/SL, risk, schedule, stop file
8. Paper and live entry/exit
9. Reconcile
10. Rich dashboard
11. Main poll loop

Dependencies: `requests`, `PyYAML`, `python-dotenv`, `rich`. No pandas, no extra modules.

---

## 34. Function-by-Function Explanation

| Function | Role |
|---|---|
| `load_config` / `validate_config` | Read and reject invalid `config.yaml` |
| `load_environment` | Keys from `.env` only |
| `setup_logging` | Event logs; no secrets |
| `create_delta_client` | PAPER cannot mutate |
| `sign_request` / `send_request` | India REST; GET backoff; no blind POST retry |
| `get_product_metadata` / `validate_product` | Live tick, contract value, min size, perpetual type |
| `get_candles` / `get_current_price` | Bounded history + ticker |
| `calculate_breakout_range` / `levels` | Prior completed bars only |
| `detect_breakout_signal` | LONG / SHORT / none + identity |
| `is_new_candle` | One evaluation per close |
| `validate_entry_conditions` / `validate_risk_conditions` / `check_duplicate_trade` | Three gates |
| `calculate_position_pnl` / `calculate_strategy_pnl` | Dollar PnL with product multiplier |
| `check_take_profit` / `check_stop_loss` | Exit predicates |
| `place_entry_order` / `monitor_order` / `place_exit_order` | One order, then query |
| `reconcile_position` | Restart-safe |
| `close_strategy_position` / `verify_position_closed` | Reduce-only, this product |
| `check_schedule` / `check_day_trading_window` / `check_stop_signal` | Clock and manual stop |
| `paper_enter_position` / `paper_exit_position` / `calculate_paper_pnl` | Simulation |
| `render_dashboard` | Terminal UI |
| `shutdown_bot` / `main` | Process lifecycle |

---

## 35. Configuration Reference

Every key in `config.yaml` is loaded, validated, and used:

- `symbol`, `mode`, `leverage`, `order_size`, `order_type`, `polling_seconds`, `timeframe`
- `run_mode`, `timezone`, `start_time`, `stop_time`, `close_positions_at_stop_time`, `stop_bot_after_close`
- `strategy.enabled`, `strategy.breakout_lookback_candles`, `strategy.direction`
- `confirmation.enabled`, `confirmation.mode`, `confirmation.value`, `confirmation.candle_close_confirmation`
- `take_profit.*`, `stop_loss.*`, `stop_after_tp`, `stop_after_sl`
- `day_trading_enabled`
- `max_open_strategies`, `max_orders_per_day`, `max_loss_per_day_dollar`
- `allow_reentry_after_exit`

There is no `engine.yaml`. Optional retest confirmation is not implemented and is not in the YAML.

---

## 36. CLI Dashboard

A Rich panel updates each poll. It shows mode (PAPER/LIVE), the current time in the configured timezone, symbol, timeframe, leverage, state, last/mark, range, breakout levels, position, live CURRENT PNL, TP/SL, daily PnL, last signal, API health, and a short event list. CURRENT PNL is `+$0.00` when flat. While a position is open it is the mark-to-market PnL each poll, shown to four decimal dollars.

LIVE and high leverage are visually prominent. Unchanged polls do not spam the event log.

---

## 37. Complete Trade Lifecycle

```text
SCANNING → completed candle → range + levels
→ SIGNAL → validate signal / risk / duplicates
→ ENTRY (paper or live) → LONG or SHORT
→ poll PnL → TP or SL or daily loss or schedule
→ reduce-only EXIT → verify flat → CLOSED
→ stop process if configured, else wait for a new signal id
```

---

## 38. Worked BTCUSD Example

Assume:

```text
Timeframe = 5m
Lookback  = 20 candles
Range high = 100000
Range low  = 99000
Confirmation = 50 POINTS
Tick size = 0.5
Contract value = 0.001
Order size = 1
```

Then:

```text
Long breakout  = 100050
Short breakout = 98950
```

A completed candle closes at 100080 → LONG signal (100080 > 100050).

Suppose fill is 100082.

Unrealized PnL at mark 100250:

```text
(100250 − 100082) × 1 × 0.001 = $0.168
```

PNL take-profit of +$5 needs a much larger move on 1 BTCUSD contract at 0.001 multiplier:

```text
$5 / 0.001 / 1 = 5000 points
```

That is why `order_size`, `contract_value`, and TP dollars must be thought about together. Copying a $5 TP from another coin is not equivalent risk.

---

## 39. Long Breakout Example

Range high 100000, buffer 50, close 100080 → BUY. State becomes LONG. The same 100080 close cannot buy again. Price lingering at 100200 does not create a second BUY.

---

## 40. Short Breakout Example

Range low 99000, buffer 50, close 98900 → SELL. State becomes SHORT. Same identity cannot sell again.

---

## 41. TP Example

Entry 100082, TP mode PNL value 5. When strategy PnL ≥ +5 the bot logs `TP TRIGGERED`, sends reduce-only sell (long) or buy (short), verifies size 0, prints final realized PnL (which may be $4.92 rather than $5.10 after slippage/fees), then stops if `stop_after_tp` is true.

---

## 42. SL Example

Same position, SL PNL value 5. When PnL ≤ −5, reduce-only close, verify, optional process stop via `stop_after_sl`.

---

## 43. Daily Loss Limit Example

Two paper losses of $3 each: daily realized −$6. Limit $5. Next poll: no new entries, flatten any remaining strategy position, stop. A single open trade whose unrealized is −$5 also trips the limit even if realized is 0.

---

## 44. Scheduled Close Example

`run_mode: SCHEDULED`, `stop_time: "23:00"`, `day_trading_enabled: true`, `close_positions_at_stop_time: true`. At 23:00 IST the bot stops entries, closes this product only, verifies, and exits if `stop_bot_after_close` is true.

---

## 45. Manual Stop Example

Operator runs `python stop.py` while a LONG is open. The bot exits. The LONG remains on Delta. TP/SL from this process no longer run. The operator must manage the position on the exchange or restart the bot in LIVE so it can resume monitoring.

---

## 46. Restart/Recovery Example

LIVE process dies with a 1-lot BTCUSD long still on the exchange. Restart: credentials, product metadata, `get_positions` sees size +1, state becomes LONG, TP/SL resume. If instead the exchange is flat, the bot scans; it does not keep a disk lock that would freeze trading forever.

---

## 47. Parameter Tuning

Things you can change, one at a time, and observe in PAPER:

- Lookback (10 vs 20 vs 50)
- Timeframe (5m vs 15m vs 1h)
- Confirmation distance (points vs percent)
- TP/SL dollars vs percent
- `max_orders_per_day`
- Session window vs CONTINUOUS

Larger confirmation reduces trades and late entries. Smaller lookback reacts faster and false-breaks more. None of this is a promise of profit.

---

## 48. How to Evaluate the Strategy

Useful evaluation, in order:

1. PAPER with default BTCUSD 5m
2. Log how many signals vs how many would have been stopped out
3. Include fees and slippage mentally (market orders)
4. Forward-test small LIVE size only after PAPER behaviour is understood
5. Do not judge from a handful of trades

Backtesting is not built into this bot. You can log candles and replay later offline if you add that yourself. This process is a live/paper forward engine.

---

## 49. Risk Management

Mandatory behaviour:

- Never duplicate entries for one signal
- Never blindly retry an unknown POST
- Never trade stale candles
- Never exceed daily orders or daily loss
- Never open a second strategy position
- Never flatten the whole account
- Manual stop does not close
- Exits are reduce-only
- PAPER cannot place live orders
- LIVE is visually obvious
- Credentials never logged

100x is high. A small adverse move can liquidate. Understand Delta India margin rules before LIVE.

---

## 50. AWS 1 GB Deployment

Lightweight VM is enough. No Docker, no database, no web server.

```bash
python3 --version   # 3.10+
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` from `env.example`. Edit `config.yaml`. Keep `mode: PAPER` until you intend real orders.

Polling every 5 seconds and a few dozen candles uses little RAM.

---

## 51. Installation

```bash
cd 1_BREAKOUT_Trading_Futures_Crypto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env   # then edit keys if using LIVE
```

---

## 52. Running the Bot

```bash
python main.py
```

Expect `BOT STARTED`, `MODE PAPER` or `MODE LIVE`, product metadata, then the dashboard in SCANNING.

---

## 53. Stopping the Bot

```bash
python stop.py
```

Or Ctrl+C. Positions stay open unless a configured TP/SL/daily/schedule close already ran.

---

## 54. Troubleshooting

| Symptom | Check |
|---|---|
| CONFIG ERROR | YAML types, allowed symbol/timeframe/mode |
| LIVE requires credentials | `.env` both key and secret |
| LEVERAGE CONFIGURATION REJECTED | Product does not accept that leverage; do not expect a silent substitute |
| MARKET DATA STALE | Clock, network, or candle API |
| ORDER REJECTION | Size, margin, trading status; query the order, do not resubmit blindly |
| PAPER warning about live size | A real position exists; PAPER will not stack or close it |
| Bot stopped, position still there | Designed behaviour of `stop.py` |

---

## 55. Future Extensions

Possible later work, not in this bot:

- Volatility-scaled confirmation
- Session filters beyond one window
- Offline backtester
- Multi-symbol processes (run one process per symbol)

Keep any extension aligned with: one config file, no docs/ imports, no duplicate-order retries, reduce-only exits only for this strategy.

---

## Security and operational notes

- LIVE can cause real financial loss.
- 100x leverage materially increases margin and liquidation sensitivity. Leverage is not an edge.
- Market orders slip. Trigger PnL and final PnL can differ because of fees and fills.
- API failures can delay exits. Software TP/SL is not an exchange resting stop.
- A stopped Python process does not close positions.
- Manual `stop.py` leaves positions open on purpose.
- Users must understand Delta India’s liquidation and margin rules.

Tuning parameters can improve historical characteristics. It cannot guarantee future profit.

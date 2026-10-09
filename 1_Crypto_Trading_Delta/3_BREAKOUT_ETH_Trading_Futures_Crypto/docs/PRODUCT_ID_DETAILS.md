You are an expert Python algorithmic-trading engineer specializing in Delta Exchange India REST APIs, perpetual crypto futures, breakout strategies, market-data processing, order execution, position management, risk management, PnL calculation, and production-safe trading systems.

Build a complete, production-ready **Delta Exchange India crypto perpetual futures breakout trading bot**.

The strategy is **futures-only**. Do NOT implement options trading.

The bot must support configurable BUY and SELL breakout trades on candle timeframes such as **minute, hourly, and daily candles**, with configurable price-point confirmation.

The initial/default market is:

- `BTCUSD`

The architecture must also support:

- `ETHUSD`
- `SOLUSD`
- `XAUSD` / `XAUTUSD`

through `config.yaml`, without requiring code changes.

The final implementation must be simple enough to run locally and on an AWS Linux VM with only **1 GB RAM**.

---

# 1. IMPORTANT REFERENCE FILES

The following files are available while developing this bot:

```text
docs/srp_delta_helper.md
docs/Delta_SRP.PY
docs/PRODUCT_ID_DETAILS.md
```

First inspect and understand them.

Use them as the primary reference for:

- Delta Exchange India authentication
- India production API
- REST request signing
- ticker retrieval
- product information
- perpetual futures
- order placement
- positions
- PnL
- leverage
- market orders
- reduce-only exit orders
- API errors
- Delta-specific request/response formats

The supplied product reference confirms that Delta India production uses:

```text
https://api.india.delta.exchange/v2
```

and that perpetual futures include BTCUSD, ETHUSD, SOLUSD and XAUTUSD. Use the exchange/product metadata as the source of truth rather than inventing product information.

The reference material also distinguishes perpetual futures from options. This project is **perpetual futures only**, so do not copy option-chain, expiry-selection, option-strike, four-leg, or option-payoff logic into this project.

The supplied reference specifically shows futures market orders such as:

```python
client.place_market_order(
    size=1,
    side="buy",
    product_symbol="ETHUSD",
    reduce_only=False,
    dry_run=False,
)
```

Use the actual API mechanics discovered from the reference files.

Do not blindly copy the reference implementation.

Extract only the functionality required for this futures breakout strategy and reimplement it inside `main.py`.

After development, the `docs/` reference files may be deleted.

Therefore:

**The production bot must NOT import anything from `docs/`.**

Do not create a dependency on `Delta_SRP.py`.

---

# 2. FINAL PROJECT FILES

The project should contain only:

```text
main.py
config.yaml
.env
env.example
stop.py
requirements.txt
architecture.md
```

There must NOT be:

```text
engine.yaml
runtime_state.json
database files
additional Python modules
additional YAML configuration files
Docker files
web applications
notebooks
unnecessary packages
```

There should be exactly **one user-facing trading configuration file**:

```text
config.yaml
```

Do NOT duplicate trading settings across multiple YAML files.

`config.yaml` is the single source of truth.

If a trading decision is configurable in `config.yaml`, it must not also exist in another configuration file.

---

# 3. ENVIRONMENT FILES

Create:

```text
.env
env.example
```

`.env` is for secrets/environment credentials only.

Never put trading strategy configuration in `.env`.

Use:

```text
DELTA_API_KEY=
DELTA_API_SECRET=
```

If the reference implementation requires additional Delta environment variables, only include them when actually required by the implementation.

Never print:

- API key
- API secret
- request authentication signature
- credentials

in the CLI or logs.

`env.example` must contain placeholder values only.

---

# 4. MAIN STRATEGY

Implement a **breakout trading strategy for Delta Exchange India perpetual futures**.

The strategy must support:

```text
BUY breakout
SELL breakout
```

The user must be able to configure whether the strategy allows:

```text
LONG
SHORT
BOTH
```

The default should allow both directions.

The strategy must operate using OHLCV candle data.

Supported candle timeframes should include at minimum:

```text
1m
3m
5m
15m
30m
1h
4h
1d
```

The timeframe must be configurable.

Default:

```yaml
timeframe: "5m"
```

Do not unnecessarily fetch every available timeframe.

Only fetch the timeframe required by the configured strategy.

---

# 5. BREAKOUT CONCEPT

The strategy should identify a breakout of a configurable previous price range.

Default conceptual strategy:

For each completed candle:

```text
previous range high = highest high of configured lookback candles

previous range low = lowest low of configured lookback candles
```

A bullish breakout occurs when price confirms above the previous range high.

A bearish breakout occurs when price confirms below the previous range low.

Do NOT use the current incomplete candle as part of the breakout range.

Use completed candles for breakout calculations.

---

# 6. BREAKOUT LOOKBACK

Make this configurable:

```yaml
strategy:
  breakout_lookback_candles: 20
```

Example:

If:

```text
breakout_lookback_candles = 20
```

then calculate:

```text
range_high = highest HIGH of previous 20 completed candles

range_low = lowest LOW of previous 20 completed candles
```

The current breakout candle must not contaminate the reference range.

---

# 7. PRICE-POINT CONFIRMATION

The user specifically wants breakout confirmation using price points.

Implement configurable confirmation.

Example:

```yaml
confirmation:
  enabled: true
  breakout_buffer_points: 50
```

For LONG:

```text
breakout_level = range_high + breakout_buffer_points
```

For SHORT:

```text
breakout_level = range_low - breakout_buffer_points
```

Only trigger a breakout after the confirmation condition is satisfied.

The implementation must correctly account for the symbol's tick size.

Do not blindly assume BTCUSD and ETHUSD have identical tick sizes.

Where possible, retrieve product metadata from Delta and normalize prices to the exchange tick size.

---

# 8. CONFIRMATION MODE

Support configurable confirmation modes:

```text
POINTS
PERCENT
```

Default:

```yaml
confirmation:
  mode: "POINTS"
  value: 50
```

For POINTS:

```text
LONG confirmation = range_high + value
SHORT confirmation = range_low - value
```

For PERCENT:

```text
LONG confirmation = range_high * (1 + value / 100)
SHORT confirmation = range_low * (1 - value / 100)
```

Only implement options that are actually connected to working code.

Do not create fake configuration.

---

# 9. CANDLE CONFIRMATION

Support configurable confirmation behavior.

Example:

```yaml
confirmation:
  candle_close_confirmation: true
```

If enabled:

LONG breakout:

```text
completed candle CLOSE > breakout confirmation level
```

SHORT breakout:

```text
completed candle CLOSE < breakout confirmation level
```

If disabled:

allow the live/current price to cross the breakout confirmation level.

However, be extremely careful not to repeatedly trigger the same breakout during every 5-second poll.

The default should use **completed candle confirmation** because it reduces false repeated signals.

---

# 10. BREAKOUT RE-ENTRY PROTECTION

This is critical.

The bot polls every few seconds but must NOT place the same order repeatedly.

Example:

If BTC breaks above a level and the bot places a BUY order:

```text
BUY BTCUSD
```

the next polling cycles must NOT place another BUY simply because price remains above the breakout.

Implement a strategy signal lock.

A breakout signal should have a unique identity based on information such as:

```text
symbol
timeframe
breakout direction
reference candle/range
breakout candle timestamp
```

Once a trade has been executed for that breakout, mark that signal as consumed.

Do not re-enter until a new valid breakout occurs and the configured re-entry rules permit it.

---

# 11. OPTIONAL RETEST CONFIRMATION

Keep the default configuration minimal.

If you implement retest confirmation, make it optional:

```yaml
confirmation:
  require_retest: false
```

When enabled:

LONG:

1. Break above breakout level.
2. Price returns/retests the breakout level.
3. Price confirms back above it.
4. Enter LONG.

SHORT:

1. Break below breakout level.
2. Price retests the breakout level.
3. Price confirms back below it.
4. Enter SHORT.

Do not make this unnecessarily complex.

If this feature adds excessive code or API requests, keep it disabled by default.

---

# 12. SYMBOL CONFIGURATION

Only one trading symbol is active per bot process.

Config:

```yaml
symbol: "BTCUSD"
```

Allowed examples:

```text
BTCUSD
ETHUSD
SOLUSD
XAUSD
XAUTUSD
```

Default:

```text
BTCUSD
```

Validate the symbol against Delta Exchange India.

Do not hardcode product IDs as the only source of truth.

Resolve or validate the product using Delta's product metadata.

The product metadata should be used for:

- product ID
- tick size
- contract value
- minimum order size
- trading status
- leverage restrictions where available

Do not assume all coins have identical contract specifications.

---

# 13. LEVERAGE

Make leverage configurable.

Default:

```yaml
leverage: 100
```

Before trading:

- validate the configured leverage where Delta supports validation
- apply the configured leverage to the selected product if required
- handle rejected leverage safely
- never silently substitute a different leverage

Display a clear CLI warning when high leverage is configured.

Do not claim that leverage improves profitability.

Explain in `architecture.md` that leverage changes margin usage and liquidation/risk sensitivity.

---

# 14. ORDER SIZE

Keep order size simple.

Example:

```yaml
order_size: 1
```

This represents the Delta contract quantity/size required by the API.

Do not assume that one contract has the same underlying exposure across BTC, ETH, SOL and XAUT.

Use product metadata where needed.

Validate order size before placing an order.

If the configured order size is invalid:

```text
DO NOT TRADE
```

Display the reason.

---

# 15. ORDER TYPE

Order type must be configurable.

Default:

```yaml
order_type: "market_order"
```

Support:

```text
market_order
limit_order
```

But keep the implementation minimal.

Market orders are the default.

For market orders:

Do not send a limit price.

For limit orders:

- configure limit behavior only if necessary
- cancel stale/unfilled orders before retrying
- never stack repeated limit orders
- never retry blindly

The configuration should make the order type obvious.

---

# 16. LIVE MODE / PAPER MODE

The trading mode must be controlled by `config.yaml`.

Use:

```yaml
mode: "PAPER"
```

Allowed:

```text
PAPER
LIVE
```

Default:

```text
PAPER
```

PAPER mode:

- fetch real market data
- calculate real breakout signals
- simulate entry
- simulate exit
- calculate simulated PnL
- NEVER send live orders

LIVE mode:

- send actual Delta orders

The CLI must prominently display:

```text
MODE: PAPER
```

or:

```text
MODE: LIVE
```

Before sending the first LIVE order, print a clear warning.

Never allow PAPER mode to accidentally submit live orders.

---

# 17. POLLING

Default:

```yaml
polling_seconds: 5
```

Every polling cycle should:

1. Check stop signal.
2. Check schedule.
3. Fetch required market data.
4. Detect completed candle.
5. Calculate breakout levels.
6. Check active position.
7. Check combined PnL.
8. Check TP.
9. Check SL.
10. Check daily loss.
11. Check forced close.
12. Evaluate new entry only if there is no active strategy.
13. Update dashboard.

Do not make unnecessary API calls.

Use lightweight polling suitable for a 1 GB RAM VM.

---

# 18. CANDLE DATA

Retrieve only the number of candles needed.

For example:

```text
lookback = 20
```

fetch approximately:

```text
lookback + small safety buffer
```

rather than thousands of candles.

Do not continuously build an unlimited in-memory dataset.

Keep memory usage low.

Use simple Python lists/dictionaries or lightweight structures.

Do not introduce pandas unless it is genuinely necessary.

Prefer lightweight dependencies for AWS 1 GB RAM.

---

# 19. NEW CANDLE DETECTION

The strategy must understand the difference between:

```text
new completed candle
```

and:

```text
same candle being polled repeatedly
```

If candle confirmation is enabled, evaluate the breakout only when a new candle closes.

Do not submit the same trade five, ten, or fifty times because polling occurs every 5 seconds.

Store the last processed candle timestamp in memory.

---

# 20. LONG ENTRY

Example:

Previous 20 completed candles:

```text
Range High = 100,000
```

Confirmation:

```text
50 points
```

Therefore:

```text
Breakout Level = 100,050
```

If the completed candle closes:

```text
100,080
```

then:

```text
LONG BREAKOUT CONFIRMED
```

Place:

```text
BUY BTCUSD
```

according to configured order size.

After successful entry:

```text
strategy state = LONG
```

Do not place another LONG entry for the same breakout.

---

# 21. SHORT ENTRY

Example:

```text
Range Low = 99,000
confirmation = 50 points
```

Therefore:

```text
Breakout Level = 98,950
```

If completed candle closes:

```text
98,900
```

then:

```text
SHORT BREAKOUT CONFIRMED
```

Place:

```text
SELL BTCUSD
```

according to configured order size.

After successful entry:

```text
strategy state = SHORT
```

Do not repeat the order for the same breakout.

---

# 22. POSITION MODEL

Treat the active futures trade as ONE logical strategy position.

Track:

```text
strategy_id
symbol
direction
order_id
entry_time
entry_price
current_price
quantity
leverage
stop_loss
take_profit
unrealized_pnl
realized_pnl
total_pnl
breakout_level
range_high
range_low
breakout_candle_timestamp
state
```

Possible states:

```text
STARTING
WAITING
SCANNING
SIGNAL_FOUND
VALIDATING
ENTERING
LONG
SHORT
EXITING
CLOSED
STOPPED
ERROR
```

Avoid dozens of Boolean flags.

Use a clear state machine.

---

# 23. TAKE PROFIT

TP must be configurable.

Support:

```text
PERCENT
PRICE
PNL
```

Keep the configuration simple.

Recommended configuration:

```yaml
take_profit:
  enabled: true
  mode: "PNL"
  value: 5
```

For PNL:

```text
close the active strategy when combined strategy PnL >= +$5
```

Also support percentage-of-entry-price if practical:

```yaml
take_profit:
  mode: "PERCENT"
  value: 1.0
```

LONG:

```text
TP price = entry_price * (1 + 1 / 100)
```

SHORT:

```text
TP price = entry_price * (1 - 1 / 100)
```

Only implement configurations that are fully wired into code.

---

# 24. STOP LOSS

SL must be configurable.

Example:

```yaml
stop_loss:
  enabled: true
  mode: "PNL"
  value: 5
```

This means:

```text
close strategy when combined strategy PnL <= -$5
```

Also support:

```text
PERCENT
PRICE
```

where practical.

For LONG percentage SL:

```text
entry_price * (1 - percentage / 100)
```

For SHORT:

```text
entry_price * (1 + percentage / 100)
```

The active strategy should have exactly one effective TP and one effective SL according to configuration.

---

# 25. COMBINED PNL

The user explicitly requires exit based on the combined PnL of trades placed by this algo.

For this futures-only strategy, the active strategy normally has one position.

Still implement a strategy-level PnL abstraction so that the bot can calculate:

```text
strategy_total_pnl
```

rather than relying blindly on a single displayed field.

Use exchange-reported PnL when available.

Otherwise calculate using:

```text
price movement
× position size
× contract value
```

using actual product metadata.

Do not assume BTCUSD, ETHUSD, SOLUSD and XAUTUSD have identical contract multipliers.

For example:

```text
strategy PnL = unrealized PnL + realized PnL attributable to this strategy
```

where appropriate.

TP/SL must use the strategy's combined PnL.

---

# 26. MAXIMUM LOSS PER DAY

Config:

```yaml
max_loss_per_day_dollar: 5
```

If daily loss reaches or exceeds:

```text
-$5
```

then:

1. Block new entries.
2. If configured, close the active strategy.
3. Stop the bot if configured.
4. Clearly display:

```text
DAILY LOSS LIMIT REACHED
NEW TRADES BLOCKED
```

Use `Asia/Kolkata` for the trading day.

Do not confuse:

```text
strategy stop loss
```

with:

```text
daily account/strategy loss limit
```

Clearly distinguish them.

---

# 27. MAX OPEN STRATEGIES

Config:

```yaml
max_open_strategies: 1
```

Default must be 1.

Because this project uses one selected symbol and one strategy process, do not create unnecessary multi-strategy complexity.

The risk check must still enforce the configured value.

Never open a new strategy if:

```text
open_strategy_count >= max_open_strategies
```

---

# 28. MAX ORDERS PER DAY

Config:

```yaml
max_orders_per_day: 10
```

Count actual entry/exit orders appropriately.

Do not allow unlimited order placement.

If the limit is reached:

```text
block new entries
```

Existing positions may still be exited for:

- TP
- SL
- forced close
- risk shutdown
- manual/strategy shutdown if configured

Do not prevent a necessary risk exit simply because the entry order limit has been reached.

---

# 29. SCHEDULE

Only `config.yaml` controls schedule.

Use:

```yaml
run_mode: "CONTINUOUS"
```

Allowed:

```text
CONTINUOUS
SCHEDULED
```

CONTINUOUS:

```text
start immediately
trade while python main.py is running
```

SCHEDULED:

```yaml
start_time: "09:30"
stop_time: "23:00"
```

Timezone:

```yaml
timezone: "Asia/Kolkata"
```

When SCHEDULED:

- do not open new trades before start time
- allow entries during the configured window
- stop new entries at stop time
- if `close_positions_at_stop_time: true`, close the strategy position
- after closing, stop the process if `stop_bot_after_close: true`

---

# 30. DAY TRADING MODE

Keep this separate from the general bot schedule.

Config:

```yaml
day_trading:
  enabled: true
  close_positions_at_stop_time: true
```

If enabled:

The configured schedule is treated as a day-trading window.

At the stop time:

1. Stop new entries.
2. Close the bot's active strategy position if configured.
3. Verify the position is closed.
4. Stop the bot if configured.

If disabled:

The strategy may continue running without an artificial intraday restriction.

This supports:

```text
intraday
swing
positional
```

behavior through configuration.

Do not create three separate codebases or strategy engines.

---

# 31. STOP TIME BEHAVIOR

Config:

```yaml
close_positions_at_stop_time: true
stop_bot_after_close: true
```

If stop time is reached and:

```text
close_positions_at_stop_time = true
```

close ONLY positions belonging to this bot/strategy.

Do not close unrelated manual positions.

After successful close:

```text
stop_bot_after_close = true
```

means exit the Python process.

Important:

`stop_bot_after_close` means:

**exit the process after closing positions**

It must NOT mean:

**close positions**

These are separate decisions.

---

# 32. MANUAL STOP.PY

Create:

```text
stop.py
```

Running:

```bash
python stop.py
```

must request a graceful bot shutdown.

Use a lightweight stop signal file or equivalent mechanism.

`main.py` checks it every polling cycle.

IMPORTANT USER REQUIREMENT:

When the user manually stops the bot, **do not automatically close positions by default**.

The user will close the position manually, or TP/SL will close it, or the strategy/schedule will close it according to configuration.

Therefore:

```text
python stop.py
```

should:

1. signal the bot to stop accepting new trades
2. NOT close positions
3. allow the current process to exit cleanly
4. display that existing positions remain open

Do NOT implement account-wide liquidation.

---

# 33. SAFE EXIT FUNCTION

Implement explicit functions such as:

```python
close_strategy_position()
verify_strategy_position_closed()
```

When TP/SL or configured scheduled exit requires closure:

- use reduce-only closing order
- close only the position associated with this strategy
- verify the exchange position afterward
- do not report CLOSED until the position quantity is zero

Never use exchange-wide "close everything" behavior.

---

# 34. ORDER DUPLICATION PROTECTION

This is one of the highest-priority requirements.

Before placing any entry:

Check:

1. Current bot state.
2. Existing position.
3. Open orders.
4. Last breakout signal.
5. Last processed candle.
6. Maximum open strategies.
7. Maximum daily orders.
8. Daily loss limit.

If an order may already have been submitted but the response is uncertain:

**query the order status before submitting another order.**

Never blindly retry an unknown order.

Never submit:

```text
BUY
BUY
BUY
BUY
```

because a polling cycle repeats.

Use a deterministic client order ID where supported.

---

# 35. LIVE ORDER SAFETY

LIVE mode can cause real financial loss.

Before placing an order:

```text
validate symbol
validate product
validate order size
validate leverage
validate market status
validate signal
validate duplicate protection
validate risk limits
validate TP/SL
validate schedule
validate daily loss
validate max orders
validate active position
```

If any validation fails:

```text
DO NOT TRADE
```

Display the exact reason.

---

# 36. POSITION RECONCILIATION

The exchange is the source of truth.

On startup:

1. Connect to Delta.
2. Validate credentials.
3. Validate account.
4. Retrieve selected product.
5. Fetch positions.
6. Fetch relevant open orders.
7. Determine whether the bot already has an active position.
8. Prevent duplicate entry.

Handle:

- process restart
- SSH disconnect
- network interruption
- AWS reboot
- API timeout

If an existing position belonging to the strategy exists:

Do NOT open another position.

The bot should safely resume monitoring where possible.

Do not create a permanent local lock that prevents trading after a restart when the exchange is actually flat.

---

# 37. BREAKOUT SIGNAL LOGIC

Implement a dedicated function:

```python
detect_breakout_signal(...)
```

It should return a structured signal such as:

```text
NONE
LONG
SHORT
```

plus useful metadata:

```text
breakout_level
range_high
range_low
candle_timestamp
confirmation_price
reason
```

Example:

```text
Signal:
LONG

Range High:
100000

Confirmation:
100050

Candle Close:
100080

Reason:
Bullish breakout confirmed above 100050
```

---

# 38. STRATEGY VALIDATION

Separate:

```text
signal generation
```

from:

```text
execution validation
```

from:

```text
risk validation
```

A trade should execute only if all three pass.

---

# 39. MAIN LOOP PRIORITY

The main loop should conceptually follow:

```text
1. Check stop signal
2. Check API connection
3. Check schedule
4. Reconcile existing position
5. If active position:
      check current PnL
      check TP
      check SL
      check daily loss
      check forced close
      update dashboard
      sleep
6. If no active position:
      fetch required candle data
      detect new candle
      calculate breakout
      validate risk
      validate duplicate protection
      execute entry
      update dashboard
7. sleep polling_seconds
```

TP/SL and risk exits must always have priority over new entries.

Never evaluate a new entry after an exit condition has already triggered.

---

# 40. TP/SL EXIT PROCESS

When TP is reached:

```text
TP TRIGGERED
↓
BLOCK NEW ENTRIES
↓
SEND REDUCE-ONLY EXIT
↓
VERIFY POSITION CLOSED
↓
CALCULATE FINAL PNL
↓
DISPLAY RESULT
↓
STOP BOT IF CONFIGURED
```

When SL is reached:

```text
SL TRIGGERED
↓
BLOCK NEW ENTRIES
↓
SEND REDUCE-ONLY EXIT
↓
VERIFY POSITION CLOSED
↓
CALCULATE FINAL PNL
↓
DISPLAY RESULT
↓
STOP BOT IF CONFIGURED
```

Do not repeatedly submit exit orders every 5 seconds.

Lock the exit state before submitting the first exit order.

---

# 41. TP/SL STOP OPTIONS

Config:

```yaml
stop_after_tp: true
stop_after_sl: true
```

If true:

After successful exit:

```text
stop the bot
```

If false:

Allow the bot to continue looking for the next valid breakout according to the re-entry rules.

Do not automatically re-enter from the same breakout signal.

---

# 42. BREAKOUT RE-ENTRY

Add only a minimal configuration:

```yaml
allow_reentry_after_exit: false
```

Default false.

If false:

After a trade exits:

```text
wait for a new breakout signal
```

Do not immediately re-enter the same breakout.

If true:

a new valid breakout signal must still occur.

Never re-enter simply because price remains beyond the old breakout level.

---

# 43. SCI-FI CLI

Build a lightweight futuristic terminal interface.

Do not build a web UI.

Use a lightweight terminal library such as `rich` if appropriate.

Keep memory and CPU consumption low.

Example style:

```text
╔══════════════════════════════════════════════════════════════╗
║          ◈ DELTA // BREAKOUT CORE ◈                         ║
╠══════════════════════════════════════════════════════════════╣
║ SYSTEM       ONLINE       MODE        PAPER                 ║
║ SYMBOL       BTCUSD       TF          5m                    ║
║ LEVERAGE     100X         STATE       SCANNING              ║
╠══════════════════════════════════════════════════════════════╣
║ CURRENT PRICE          100250.50                            ║
║ RANGE HIGH             100000.00                            ║
║ RANGE LOW               99000.00                            ║
║ BREAKOUT LONG          100050.00                            ║
║ BREAKOUT SHORT          98950.00                            ║
╠══════════════════════════════════════════════════════════════╣
║ POSITION               NONE                                 ║
║ CURRENT PNL            $0.00                                ║
║ TP TARGET              +$5.00                               ║
║ SL TARGET              -$5.00                               ║
║ DAILY PNL              $0.00                                ║
╠══════════════════════════════════════════════════════════════╣
║ LAST SIGNAL             NONE                                ║
║ LAST TRADE              NONE                                ║
║ NEXT POLL               5s                                  ║
║ API                     CONNECTED                           ║
╚══════════════════════════════════════════════════════════════╝
```

When a position exists, display:

```text
POSITION: LONG
ENTRY PRICE
CURRENT PRICE
POSITION SIZE
UNREALIZED PNL
REALIZED PNL
TOTAL STRATEGY PNL
TP PRICE
SL PRICE
DISTANCE TO TP
DISTANCE TO SL
```

For SHORT show the equivalent information.

Display:

```text
LIVE
```

or:

```text
PAPER
```

very prominently.

Also show warnings such as:

```text
⚠ LIVE TRADING ENABLED
⚠ LEVERAGE: 100X
```

---

# 44. PAPER TRADING

Paper mode should simulate:

- entry
- position
- PnL
- TP
- SL
- exit
- daily PnL

Use real market prices but do not send orders.

Clearly distinguish:

```text
PAPER ENTRY
```

from:

```text
LIVE ENTRY
```

---

# 45. API ERROR HANDLING

Handle:

```text
authentication failure
HTTP errors
timeout
rate limiting
invalid symbol
invalid product
insufficient margin
invalid quantity
invalid leverage
rejected order
network failure
malformed response
stale market data
unknown order status
```

Use bounded retries.

Use exponential backoff for safe read requests.

Never blindly retry an order submission whose result is unknown.

Before retrying an uncertain order:

```text
query order status
```

to prevent duplicate trades.

---

# 46. MARKET DATA FAILURE

If market data becomes stale:

```text
DO NOT TRADE
```

If there is an active position:

continue attempting safe monitoring/reconciliation.

Never open a new trade using stale data.

Display:

```text
MARKET DATA STALE — ENTRY BLOCKED
```

---

# 47. API RATE LIMITS

The bot polls every 5 seconds by default.

Do not unnecessarily call:

```text
product metadata
```

every 5 seconds.

Cache static information such as:

- product metadata
- product ID
- tick size
- contract value
- trading status

Refresh only when necessary.

Dynamic information should be fetched at reasonable intervals.

The implementation must be suitable for an AWS 1 GB RAM VM.

---

# 48. PRODUCT METADATA

For the selected perpetual product, retrieve and use exchange metadata for:

```text
product_id
symbol
tick_size
contract_value
trading status
minimum order size where available
```

Do not assume:

```text
BTCUSD = ETHUSD = SOLUSD
```

in contract specifications.

Normalize prices according to the exchange tick size.

Do not invent product IDs if the API can provide them.

The supplied reference identifies examples such as BTCUSD, ETHUSD, SOLUSD and XAUTUSD, but runtime exchange metadata should remain authoritative.

---

# 49. LEVERAGE SAFETY

The default requested configuration is:

```yaml
leverage: 100
```

However, the code must not assume that every product accepts exactly the same leverage.

Validate against Delta's actual response/API behavior.

If rejected:

```text
LEVERAGE CONFIGURATION REJECTED
```

and do not silently continue with a different leverage.

---

# 50. CONFIG.YAML

Keep this file **minimal**.

The user should not be overwhelmed by dozens of parameters.

Every setting must have a clear comment explaining what the user changes.

Use approximately this structure:

```yaml
# ============================================================
# DELTA CRYPTO BREAKOUT BOT
# User trading configuration
# ============================================================

# Delta perpetual futures symbol
# Examples: BTCUSD, ETHUSD, SOLUSD, XAUSD, XAUTUSD
symbol: "BTCUSD"

# PAPER = simulated trading
# LIVE  = real trading
mode: "PAPER"

# Leverage requested for the selected futures product
leverage: 100

# Number of contracts/orders for each entry
order_size: 1

# market_order is recommended for the simple default execution
# limit_order can be implemented if fully supported
order_type: "market_order"

# How often the bot polls the exchange
polling_seconds: 5

# Candle timeframe used by the breakout strategy
# Examples: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d
timeframe: "5m"

# ------------------------------------------------------------
# RUNNING MODE
# ------------------------------------------------------------

# CONTINUOUS = start immediately and trade while main.py runs
# SCHEDULED  = trade only inside start_time / stop_time
run_mode: "CONTINUOUS"

timezone: "Asia/Kolkata"

# Used only when run_mode = SCHEDULED
start_time: "09:30"
stop_time: "23:00"

# If true, close this strategy's open position at stop_time
close_positions_at_stop_time: true

# After the scheduled position is closed, exit Python
stop_bot_after_close: true

# ------------------------------------------------------------
# BREAKOUT STRATEGY
# ------------------------------------------------------------

strategy:
  enabled: true

  # Number of completed candles used to calculate the breakout range
  breakout_lookback_candles: 20

  # LONG, SHORT or BOTH
  direction: "BOTH"

# ------------------------------------------------------------
# BREAKOUT CONFIRMATION
# ------------------------------------------------------------

confirmation:
  enabled: true

  # POINTS or PERCENT
  mode: "POINTS"

  # Example: 50 points above/below breakout range
  value: 50

  # When true, require a completed candle close beyond confirmation level
  candle_close_confirmation: true

# ------------------------------------------------------------
# TP / SL
# ------------------------------------------------------------

take_profit:
  enabled: true

  # PNL or PERCENT
  mode: "PNL"

  # PNL = dollars
  # PERCENT = percentage from entry price
  value: 5

stop_loss:
  enabled: true

  # PNL or PERCENT
  mode: "PNL"

  # Positive dollar amount when mode = PNL
  # Example 5 means exit around -$5
  value: 5

# Stop after TP/SL exit
stop_after_tp: true
stop_after_sl: true

# ------------------------------------------------------------
# TRADING STYLE
# ------------------------------------------------------------

# true  = enforce scheduled/day-trading behavior
# false = allow the strategy to continue without an artificial
#         intraday restriction
day_trading_enabled: true

# ------------------------------------------------------------
# RISK LIMITS
# ------------------------------------------------------------

max_open_strategies: 1

max_orders_per_day: 10

# If daily strategy loss reaches this amount,
# block new entries and close active strategy position.
max_loss_per_day_dollar: 5

# ------------------------------------------------------------
# RE-ENTRY
# ------------------------------------------------------------

# false = do not immediately re-enter after an exit
allow_reentry_after_exit: false
```

Do not add unnecessary sections.

If a configuration option is not actually implemented, remove it.

If a configuration option is implemented, make sure it is actually read and used by `main.py`.

---

# 51. CONFIGURATION SYNCHRONIZATION AUDIT

Before finishing:

Perform a complete configuration audit.

For every key in `config.yaml`:

1. Find where it is loaded.
2. Find where it is validated.
3. Find where it is used.
4. Confirm its value actually changes behavior.

Remove:

- unused configuration
- duplicate configuration
- dead configuration
- configuration copied from the options project
- settings that are not relevant to futures breakout trading

There must be no configuration duplication.

`config.yaml` is the final authority.

---

# 52. MAIN.PY STRUCTURE

Everything must remain in:

```text
main.py
```

Organize it into logical sections:

```text
1. imports
2. constants
3. configuration
4. environment
5. logging
6. data models
7. Delta API client
8. product metadata
9. candle retrieval
10. breakout calculations
11. signal detection
12. PnL
13. risk management
14. order execution
15. position reconciliation
16. TP/SL
17. schedule
18. stop handling
19. paper trading
20. dashboard
21. shutdown
22. main loop
```

Keep the code readable.

Do not create an unnecessarily huge framework.

---

# 53. REQUIRED FUNCTIONS

Implement logically separated functions including, where applicable:

```python
load_config()
validate_config()
load_environment()
setup_logging()

create_delta_client()
sign_request()
send_request()

validate_exchange_connection()
validate_product()

get_product_metadata()
get_current_price()
get_candles()
get_current_position()
get_open_orders()

calculate_breakout_range()
calculate_breakout_levels()
detect_breakout_signal()
is_new_candle()

validate_entry_conditions()
validate_risk_conditions()
check_duplicate_trade()

calculate_position_pnl()
calculate_strategy_pnl()

calculate_take_profit()
calculate_stop_loss()

check_take_profit()
check_stop_loss()

place_entry_order()
monitor_order()
place_exit_order()

reconcile_position()

close_strategy_position()
verify_position_closed()

check_schedule()
check_day_trading_window()
check_stop_signal()

paper_enter_position()
paper_exit_position()
calculate_paper_pnl()

render_dashboard()

shutdown_bot()

main()
```

Do not implement fake functions just to satisfy a list.

Every function must have real purpose.

---

# 54. FUNCTION DOCUMENTATION

Every important function in `main.py` must have a clear docstring explaining:

- purpose
- inputs
- outputs
- when it is called
- important safety considerations

Example style:

```python
def calculate_breakout_range(candles, lookback):
    """
    Calculate the previous completed-candle high/low range used
    by the breakout strategy.

    Inputs:
        candles: completed OHLC candles.
        lookback: number of candles used for the range.

    Returns:
        range_high, range_low.

    Safety:
        The current incomplete candle must not be included in the
        breakout reference range.
    """
```

Add comments around important trading logic.

Do not add meaningless comments such as:

```python
# add 1
x += 1
```

Comments should explain the trading concept or safety reason.

---

# 55. CODE QUALITY

Use:

- Python 3.10+
- type hints where useful
- dataclasses where useful
- clear naming
- small functions
- explicit error handling
- minimal dependencies

Do not over-engineer.

Avoid:

- unnecessary classes
- unnecessary abstractions
- database systems
- async frameworks unless genuinely necessary
- web servers
- multiprocessing
- large ML libraries

This is a deterministic trading bot, not an AI system.

---

# 56. REQUIREMENTS.TXT

Keep dependencies minimal.

Likely dependencies may include:

```text
requests
PyYAML
python-dotenv
rich
```

Only include packages actually used.

Do not include pandas/numpy unless they are genuinely required.

The bot must be lightweight enough for:

```text
AWS Linux
1 GB RAM
```

---

# 57. STOP.PY

`stop.py` must be extremely lightweight.

Running:

```bash
python stop.py
```

should create/update the stop signal.

It must NOT:

- connect to Delta unnecessarily
- close positions
- cancel account-wide orders
- modify trading state directly

`main.py` handles the actual graceful shutdown.

---

# 58. LOGGING

Use concise logs.

Log important events:

```text
BOT STARTED
MODE
SYMBOL
TIMEFRAME
LEVERAGE
BREAKOUT RANGE
SIGNAL
ENTRY
EXIT
TP
SL
DAILY LOSS LIMIT
ORDER REJECTION
API ERROR
STOP REQUEST
FINAL PNL
```

Never log secrets.

Do not generate massive log files every 5 seconds.

Avoid printing the same unchanged message continuously.

---

# 59. TRADE EVENT EXAMPLE

The CLI should make the trade lifecycle understandable.

Example:

```text
[SCANNING]
BTCUSD
5m

Range High: 100000
Range Low : 99000

Long Breakout: 100050
Short Breakout: 98950
```

Then:

```text
[BREAKOUT DETECTED]

Direction: LONG
Candle Close: 100080
Breakout Level: 100050
```

Then:

```text
[ENTRY]

BUY BTCUSD
SIZE: 1
ENTRY: 100082
```

Then:

```text
[POSITION ACTIVE]

ENTRY: 100082
CURRENT: 100250
PNL: +$1.68
TP: +$5.00
SL: -$5.00
```

Then:

```text
[TP TRIGGERED]

PNL: +$5.10

CLOSING POSITION...
```

Then:

```text
[POSITION CLOSED]

FINAL REALIZED PNL: +$4.92
BOT STOPPED
```

Final PnL can differ from trigger PnL due to:

- execution price
- fees
- slippage

Explain this in `architecture.md`.

---

# 60. ARCHITECTURE.MD

Create a comprehensive but understandable technical document named:

```text
architecture.md
```

Write it as a structured technical chapter explaining exactly how the bot works.

Use this structure:

```text
# Delta Exchange India Crypto Breakout Algo

## 1. Introduction

## 2. Strategy Objective

## 3. Delta Exchange India Futures

## 4. Supported Symbols

## 5. Perpetual Futures Concept

## 6. Breakout Trading Concept

## 7. Candle Timeframes

## 8. Lookback Range

## 9. Price-Point Confirmation

## 10. Long Breakout

## 11. Short Breakout

## 12. Candle Close Confirmation

## 13. Breakout Signal Lifecycle

## 14. Order Execution

## 15. Market Order

## 16. Duplicate Order Protection

## 17. Position Management

## 18. Combined Strategy PnL

## 19. Take Profit

## 20. Stop Loss

## 21. Daily Loss Limit

## 22. Maximum Orders Per Day

## 23. Scheduling

## 24. Continuous Mode

## 25. Day Trading Mode

## 26. Swing Trading Configuration

## 27. Positional Trading Configuration

## 28. Manual Stop

## 29. TP/SL Exit Lifecycle

## 30. Position Reconciliation

## 31. Paper Trading

## 32. Live Trading

## 33. Main.py Architecture

## 34. Function-by-Function Explanation

## 35. Configuration Reference

## 36. CLI Dashboard

## 37. Complete Trade Lifecycle

## 38. Worked BTCUSD Example

## 39. Long Breakout Example

## 40. Short Breakout Example

## 41. TP Example

## 42. SL Example

## 43. Daily Loss Limit Example

## 44. Scheduled Close Example

## 45. Manual Stop Example

## 46. Restart/Recovery Example

## 47. Parameter Tuning

## 48. How to Evaluate the Strategy

## 49. Risk Management

## 50. AWS 1 GB Deployment

## 51. Installation

## 52. Running the Bot

## 53. Stopping the Bot

## 54. Troubleshooting

## 55. Future Extensions
```

---

# 61. WORKED EXAMPLE IN ARCHITECTURE.MD

Include a clear BTCUSD example.

Example:

```text
Timeframe = 5m
Lookback = 20 candles

Range High = 100000
Range Low = 99000

Confirmation = 50 points

Long Breakout = 100050
Short Breakout = 98950
```

Then show:

```text
Candle closes at 100080
```

Therefore:

```text
LONG SIGNAL
```

Suppose:

```text
Entry = 100082
```

Then explain how PnL changes:

```text
Current price = 100200
```

and then:

```text
Current price = 100400
```

until TP is reached.

Also provide a short example.

---

# 62. PARAMETER TUNING

In `architecture.md`, explain practical parameters a trader/developer can experiment with:

```text
timeframe
breakout_lookback_candles
confirmation value
confirmation mode
candle close confirmation
order size
stop loss
take profit
leverage
max loss per day
max orders per day
```

Explain the trade-offs.

For example:

Higher lookback:

- fewer breakout signals
- potentially wider/more significant ranges

Lower lookback:

- more signals
- potentially more noise

Larger confirmation:

- fewer marginal breakouts
- potentially later entries

Smaller confirmation:

- earlier entries
- potentially more false breakouts

Do NOT claim that a particular parameter guarantees profit.

Explain that parameters must be tested using historical data, paper trading, and controlled live deployment.

---

# 63. STRATEGY IMPROVEMENT

In `architecture.md`, explain ways the user can evaluate and improve the strategy without promising profitability.

Discuss:

- breakout lookback
- timeframe selection
- confirmation distance
- volatility-aware confirmation
- stop-loss placement
- take-profit placement
- risk/reward
- trading session filters
- maximum daily loss
- avoiding low-liquidity periods
- reducing overtrading
- paper testing
- backtesting
- forward testing
- transaction costs
- slippage
- leverage risk

Clearly state that tuning parameters can improve historical characteristics but cannot guarantee future profit.

---

# 64. SECURITY AND RISK

Explain clearly:

- LIVE mode can cause real financial loss.
- 100x leverage materially increases margin/liquidation sensitivity.
- Market orders can experience slippage.
- API failures can delay exits.
- TP/SL monitoring by the bot is not equivalent to an exchange-native stop unless the implementation explicitly uses exchange-native orders.
- A stopped Python process does not automatically close positions.
- A manually stopped bot may leave positions open by design.
- Users must understand the exchange's liquidation and margin rules.

---

# 65. AWS DEPLOYMENT

Explain lightweight deployment:

```bash
python3 --version

python3 -m venv .venv

source .venv/bin/activate

pip install -r requirements.txt
```

Create `.env`.

Configure:

```text
config.yaml
```

Then:

```bash
python main.py
```

Stopping:

```bash
python stop.py
```

Do not require Docker.

Do not require a database.

Do not require a web server.

Keep CPU/RAM usage low.

---

# 66. IMPORTANT SAFETY RULES

These rules are mandatory:

1. Never duplicate orders.
2. Never blindly retry an uncertain order.
3. Query order status before retrying an unknown submission.
4. Never trade if market data is stale.
5. Never trade if product validation fails.
6. Never trade if risk validation fails.
7. Never exceed `max_orders_per_day`.
8. Never exceed `max_open_strategies`.
9. Never trade after `max_loss_per_day_dollar` is reached.
10. Never open a second position while the strategy position is active.
11. Never close unrelated user positions.
12. Never perform account-wide liquidation.
13. Manual `stop.py` must NOT close positions.
14. TP/SL exits must use reduce-only orders.
15. Verify position closure after an exit.
16. Never treat a partially completed order workflow as a successful trade.
17. Never let polling create duplicate entries.
18. PAPER mode must never send live orders.
19. LIVE mode must be visually obvious.
20. Never log credentials.

---

# 67. CODE REVIEW BEFORE FINISHING

After implementing the bot, perform a complete self-review.

Check:

### Configuration

- Every config key is used.
- No unused config keys.
- No duplicate config.
- No `engine.yaml`.
- `config.yaml` is the single source of truth.

### Trading

- LONG works.
- SHORT works.
- BOTH works.
- Breakout calculation is correct.
- Current candle does not contaminate previous range.
- Candle confirmation works.
- Price-point confirmation works.
- Tick-size normalization works.
- TP works.
- SL works.
- Daily loss works.
- Scheduled close works.
- Continuous mode works.
- Manual stop does not close positions.

### Safety

- Duplicate order protection works.
- Unknown order status is reconciled before retry.
- Active position prevents new entry.
- Max orders/day works.
- Max open strategies works.
- Paper mode cannot place live orders.
- Live mode requires explicit configuration.
- Exit orders are reduce-only.
- Only this strategy's position is closed.

### API

- India production API is used.
- Authentication is correct according to the supplied reference.
- No option endpoints are unnecessarily used.
- Futures product metadata is used.
- Correct Delta request/response handling is implemented.

### Infrastructure

- Works on Python 3.10+.
- Low RAM usage.
- No unnecessary dependencies.
- No unnecessary background services.
- Suitable for AWS 1 GB RAM Linux VM.

---

# 68. FINAL CLEANUP

Before returning the completed implementation:

Remove:

- option strategy code
- option expiry code
- option strike code
- four-leg execution
- condor logic
- unused generic framework code
- duplicate configuration
- unused imports
- unused functions
- unnecessary dependencies
- unnecessary files
- unused configuration parameters

The final project must be a focused:

**Delta Exchange India perpetual futures breakout trading bot.**

Do not leave remnants of the previous options strategy.

---

# 69. FINAL DELIVERABLE

Generate:

```text
main.py
config.yaml
.env
env.example
stop.py
requirements.txt
architecture.md
```

The code must be runnable after installing dependencies and providing valid Delta India API credentials.

The implementation must be readable, logically structured, heavily documented around important trading logic, and written with clear function-level explanations so that each major trading concept can be understood by reading the code alongside `architecture.md`.

Most importantly, write the implementation in a clean explanatory style: the code, comments, function docstrings, examples, calculations, and architecture documentation should remain useful as educational technical material when reproduced in books or other long-form technical documentation.
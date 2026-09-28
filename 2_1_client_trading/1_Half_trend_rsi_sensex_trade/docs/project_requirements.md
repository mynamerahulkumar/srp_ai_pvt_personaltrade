You are GitHub Copilot working inside a Python trading project.

Build a complete, production-oriented but educational Dhan-only options algo trading bot based on the Half Trend indicator shown in the provided screenshot.

IMPORTANT: The uploaded reference files are reference-only. Reimplement only the required Dhan patterns inside main.py. Never import them. They will be deleted after development. The reference implementation uses DhanHQ v2, DhanContext, DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN, security-master resolution, Dhan option-chain/order/position APIs, and current SDK conventions. :chatgpt-content-reference{index="0"} The helper specifically confirms that the reference files should not be treated as a reusable broker package and that required helpers should be copied/reimplemented into the application. :chatgpt-content-reference{index="1"}

============================================================
1. PROJECT OBJECTIVE
============================================================

Create a Dhan-only options trading algo using the Half Trend indicator.

The primary/default underlying is:

SENSEX

The bot must trade OPTIONS, not the SENSEX underlying itself.

The same code must allow the trader to configure:

- SENSEX
- NIFTY
- BANKNIFTY

from config.yaml without changing Python code.

Default configuration must be SENSEX.

The strategy is based on the behavior shown in the supplied screenshot:

- 5-minute chart by default
- Dhan Half Trend
- Half Trend parameters shown as 2, 2
- When a CONFIRMED candle closes above the Half Trend line, generate a bullish signal.
- When a CONFIRMED candle closes below the Half Trend line, generate a bearish signal.
- Bullish signal -> BUY CALL option (CE)
- Bearish signal -> BUY PUT option (PE)

Do not repeatedly enter on every polling cycle.

The bot must wait for a new confirmed candle/signal transition.

The strategy must be deterministic and easy to understand.

This is an options trading implementation, not an equity trading implementation.

Do NOT implement:
- stock equity trading
- Delta Exchange
- Zerodha
- Fyers
- Angel One
- Upstox
- other brokers
- crypto
- futures
- multi-broker abstraction
- AI
- machine learning
- Telegram
- email
- web UI
- database
- Redis
- FastAPI
- Streamlit
- Docker
- Kubernetes

The implementation must remain Dhan-only.

============================================================
2. REQUIRED APPLICATION FILES
============================================================

Create only these application files:

1. main.py
2. config.yaml
3. .env
4. env.example
5. stop.py
6. requirements.txt
7. architecture.md

Do NOT create:

- strategy.py
- dhan.py
- broker.py
- utils.py
- risk.py
- options.py
- database.py
- logger.py
- engine.py
- engine.yaml
- any other Python module

All application logic must remain in main.py.

architecture.md is documentation only.

.env contains secrets only.

env.example is a safe credential template only.

config.yaml contains all user-changeable trading configuration.

stop.py only sends a graceful stop request.

requirements.txt contains dependencies.

IMPORTANT:

There must be only TWO runtime/user configuration sources:

1. config.yaml
2. .env

env.example is only a template for creating .env.

DO NOT create engine.yaml.

Do not duplicate configuration values across YAML files.

If something is configured in config.yaml, config.yaml is the single source of truth.

Never have one setting in config.yaml and another copy of the same setting in another YAML file.

============================================================
3. REFERENCE FILES
============================================================

The user has provided:

docs/project_requirements.md
docs/Dhan_SRP.py
docs/srp_dhan_helper.md

Treat them as REFERENCE ONLY.

Never import them.

Do not make the new application dependent on them.

Copy/reimplement only the Dhan SDK patterns required by this project into main.py.

After implementation, the project must work if all reference files are deleted.

The Dhan reference uses:

- dhanhq 2.2+
- DhanContext
- dhanhq(context)
- DHAN_CLIENT_ID
- DHAN_ACCESS_TOKEN
- security master
- option_chain()
- expiry_list()
- ticker_data()
- ohlc_data()
- intraday_minute_data()
- get_positions()
- get_order_list()
- get_order_by_id()
- get_trade_book()
- place_order()
- modify_order()
- cancel_order()

Use current installed DhanHQ SDK conventions.

Do not use obsolete Dhan constructors.

The reference also confirms that derivative security IDs and lot sizes must not be hardcoded because derivative contracts change. Resolve them dynamically from the security master. :chatgpt-content-reference{index="2"}

============================================================
4. DHAN AUTHENTICATION
============================================================

Use:

from dhanhq import DhanContext, dhanhq

Load:

DHAN_CLIENT_ID
DHAN_ACCESS_TOKEN

from .env using python-dotenv.

Initialize:

context = DhanContext(client_id, access_token)
dhan = dhanhq(context)

Never hardcode credentials.

Never print:

- access token
- client secret
- credentials

.env:

DHAN_CLIENT_ID=
DHAN_ACCESS_TOKEN=

env.example:

DHAN_CLIENT_ID=
DHAN_ACCESS_TOKEN=

Add comments explaining what values the user must enter.

If LIVE mode is selected and credentials are missing:

- fail safely
- display a clear error
- do not place any order

============================================================
5. SECURITY MASTER
============================================================

The bot must NOT hardcode:

- SENSEX security ID
- NIFTY security ID
- BANKNIFTY security ID
- option security IDs
- strike IDs
- expiry-specific derivative IDs
- option lot sizes

Use:

api-scrip-master.csv

as the local security-master source whenever available.

The CSV must be used to resolve:

- underlying security ID
- option security ID
- trading symbol
- exchange segment
- instrument name
- expiry
- strike
- option type
- lot size
- tick size

If the local api-scrip-master.csv does not exist, use Dhan's security-master API to obtain the current security master and safely cache it as api-scrip-master.csv if possible.

Do not download or reload the entire security master every 5 seconds.

Load it once and cache it in memory.

The security master contains fields such as:

SEM_SMST_SECURITY_ID
SEM_EXM_EXCH_ID
SEM_INSTRUMENT_NAME
SEM_TRADING_SYMBOL
SEM_CUSTOM_SYMBOL
SEM_LOT_UNITS
SEM_TICK_SIZE
SEM_EXPIRY_DATE
SEM_STRIKE_PRICE
SEM_OPTION_TYPE

Resolve the selected underlying dynamically.

For example:

SENSEX -> BSE index / IDX_I
NIFTY -> NSE index / IDX_I
BANKNIFTY -> NSE index / IDX_I

Do not hardcode the numeric IDs even if known examples exist.

============================================================
6. OPTIONS ONLY
============================================================

The strategy signal is generated from the UNDERLYING INDEX.

Example:

SENSEX spot chart
+
Half Trend
=
BUY CE or BUY PE

The bot does NOT trade the index itself.

Bullish:

Underlying candle closes above Half Trend
-> bullish signal
-> select configured CALL option
-> BUY CALL

Bearish:

Underlying candle closes below Half Trend
-> bearish signal
-> select configured PUT option
-> BUY PUT

Default behavior should be BUY OPTIONS ONLY.

Do not short CE/PE by default.

Do not create multi-leg option strategies.

One option contract at a time.

============================================================
7. OPTION UNDERLYING CONFIGURATION
============================================================

config.yaml must contain a very small, understandable configuration.

Example structure:

mode:
  trading_mode: "PAPER"       # PAPER or LIVE

market:
  underlying: "SENSEX"        # SENSEX | NIFTY | BANKNIFTY
  exchange: "BSE"             # automatically validated against underlying
  timeframe_minutes: 5

strategy:
  name: "HALF_TREND"
  amplitude: 2
  channel_deviation: 2

option:
  option_type_mode: "SIGNAL"  # bullish=CE, bearish=PE
  expiry_mode: "NEAREST"      # NEAREST or CONFIGURED
  expiry: ""                  # used only when CONFIGURED
  strike_mode: "ATM"          # ATM | ITM | OTM
  strike_offset: 0            # points relative to ATM; configurable
  lots: 1

execution:
  order_type: "LIMIT"
  product_type: "INTRADAY"

risk:
  take_profit:
    enabled: true
    type: "PERCENT"
    value: 20

  stop_loss:
    enabled: true
    type: "PERCENT"
    value: 10

  max_open_strategies: 1
  max_orders_per_day: 10
  max_loss_per_day_inr: 500
  no_reentry_after_stop_loss: true
  no_reentry_after_max_loss: true

session:
  enabled: true
  timezone: "Asia/Kolkata"
  start_time: "09:20"
  stop_time: "15:20"
  close_all_positions_at_stop: true
  stop_bot_after_close: true

runtime:
  polling_seconds: 5

  # CONTINUOUS = start immediately and keep monitoring while Python runs
  # SCHEDULED = trade only between start_time and stop_time
  run_mode: "SCHEDULED"

  post_close_polls: 3
  post_close_poll_seconds: 10

This is an example structure.

Improve it if necessary, but KEEP IT MINIMAL.

Do not expose unnecessary implementation settings to the user.

Every config value must have a clear comment explaining what the trader should enter.

============================================================
8. CONFIGURATION MUST BE THE SINGLE SOURCE OF TRUTH
============================================================

This is extremely important.

If a setting exists in config.yaml:

main.py must read it from config.yaml.

Do not duplicate it as a Python constant.

Do not duplicate it in engine.yaml.

Do not create environment variables for trading parameters.

.env is ONLY for credentials.

Examples:

polling_seconds must come from config.yaml.

Underlying must come from config.yaml.

Half Trend amplitude must come from config.yaml.

Channel deviation must come from config.yaml.

Expiry selection must come from config.yaml.

Strike selection must come from config.yaml.

Lot quantity must come from config.yaml.

TP/SL must come from config.yaml.

LIVE/PAPER must come from config.yaml.

Session start/stop must come from config.yaml.

Max daily loss must come from config.yaml.

============================================================
9. HALF TREND STRATEGY
============================================================

Implement a transparent Half Trend calculation inside main.py.

The screenshot shows:

Dhan HalfTrend 2 2

Therefore defaults are:

amplitude = 2
channel_deviation = 2

The exact implementation must be deterministic.

Implement the standard Half Trend-style calculation using:

- highest high over amplitude
- lowest low over amplitude
- SMA/high average
- SMA/low average
- trend state
- max low price
- min high price
- ATR-based channel deviation
- Half Trend line
- upper/lower channel values if useful internally

Keep the implementation readable.

Do not hide the formula inside an external package.

Do not require TA-Lib.

Use pandas and standard Python calculations.

IMPORTANT:

The screenshot is the visual reference for the desired behavior.

The implementation must clearly document which mathematical Half Trend formula is used.

If the exact proprietary implementation of Dhan's indicator cannot be verified from the supplied references, do NOT falsely claim that the implementation is byte-for-byte identical to Dhan's internal implementation.

Instead:

- implement a standard transparent Half Trend calculation
- make amplitude configurable
- make channel deviation configurable
- explain the formula in architecture.md
- explain that the signal behavior is designed to match the supplied chart behavior

============================================================
10. HALF TREND SIGNAL
============================================================

Use CONFIRMED candles.

Default timeframe:

5 minutes

The bot can poll every 5 seconds, but a 5-second poll is NOT a new candle.

Use the last confirmed 5-minute candle for signal generation.

LONG/BULLISH:

If the confirmed candle closes ABOVE the Half Trend line:

signal = BUY_CE

BEARISH:

If the confirmed candle closes BELOW the Half Trend line:

signal = BUY_PE

Do not generate repeated entries from the same candle.

Track:

last_signal_candle_timestamp

If the same candle has already generated a signal:

do nothing.

Prefer a transition/confirmation model.

Example:

Previous confirmed candle:
close <= Half Trend

Current confirmed candle:
close > Half Trend

=> BUY CE

Bearish:

Previous confirmed candle:
close >= Half Trend

Current confirmed candle:
close < Half Trend

=> BUY PE

If configured signal behavior requires only one-candle confirmation, make that the default.

Do not repeatedly buy CE every 5 seconds while price remains above Half Trend.

============================================================
11. OPTION SELECTION
============================================================

When a bullish signal occurs:

1. Get underlying spot.
2. Get configured expiry.
3. Get option chain.
4. Determine ATM strike.
5. Apply configured strike selection.
6. Select CE.
7. Resolve its security ID.
8. Get current option premium.
9. Validate quantity against security-master lot size.
10. Place the order.

When bearish:

same process but select PE.

Supported:

strike_mode:
- ATM
- ITM
- OTM

strike_offset:

Use the underlying strike interval from the option chain/security master rather than assuming a fixed strike interval.

Examples:

ATM:
strike = nearest available strike to spot

OTM CE:
strike above ATM

ITM CE:
strike below ATM

OTM PE:
strike below ATM

ITM PE:
strike above ATM

Do NOT hardcode strike intervals.

Do NOT hardcode lot sizes.

============================================================
12. EXPIRY SELECTION
============================================================

Support:

expiry_mode: NEAREST

and:

expiry_mode: CONFIGURED

NEAREST:

Use the nearest valid expiry returned by Dhan.

CONFIGURED:

Use:

expiry: "YYYY-MM-DD"

Validate that the expiry exists.

If the configured expiry does not exist:

- display clear error
- do not trade

For SENSEX, use the appropriate BSE option chain.

For NIFTY/BANKNIFTY, use the appropriate NSE option chain.

Do not assume all underlyings use the same exchange.

============================================================
13. OPTION CHAIN API
============================================================

Use Dhan's:

expiry_list()
option_chain()

patterns from the supplied reference.

Normalize option-chain data internally into simple fields such as:

strike
ce_security_id
ce_ltp
pe_security_id
pe_ltp
ce_bid
ce_ask
pe_bid
pe_ask
ce_oi
pe_oi

Do not expose all these as configuration settings.

Use only what is required for execution and dashboard.

IMPORTANT:

Dhan option-chain requests have rate limitations.

Do not request the full option chain unnecessarily every polling cycle.

Fetch the chain:

- when a new entry signal occurs
- when the selected option contract must be refreshed
- when expiry/contract changes
- when required by configuration

Cache it for the minimum practical period.

Current option premium can be fetched using quote/ticker data for the selected contract.

Do not exceed Dhan API limits.

The supplied helper documents the option-chain and quote request constraints. :chatgpt-content-reference{index="3"}

============================================================
14. POLLING
============================================================

Default:

polling_seconds: 5

Every polling cycle should:

1. Check stop signal.
2. Check current time.
3. Check market/session status.
4. Fetch current relevant price.
5. Fetch current option premium when a position exists.
6. Update P&L.
7. Check TP.
8. Check SL.
9. Check max daily loss.
10. Check open-position state.
11. Refresh candles only when necessary.
12. Calculate Half Trend.
13. Detect new confirmed candle.
14. Generate signal.
15. If flat, select option and place order if signal is valid.
16. Render CLI dashboard.
17. Sleep for configured polling_seconds.

Do NOT fetch expensive historical data unnecessarily on every 5-second poll.

Use the 5-second polling loop for monitoring.

Only refresh indicator candle data when a new candle is expected or when needed.

============================================================
15. MARKET DATA
============================================================

For underlying index:

Fetch enough historical/intraday candle data to calculate Half Trend.

Minimum required:

timestamp
open
high
low
close
volume if available

Use:

intraday_minute_data()

with configured timeframe.

For current underlying:

use ticker_data()/ohlc_data()/quote_data() where appropriate.

For active option position:

fetch current option LTP/premium.

The CLI must show:

Underlying
Underlying LTP
Option symbol
Option LTP
Half Trend
Candle timestamp
Signal
Position
Entry price
Current price
Quantity
TP
SL
Current P&L
Today's Algo P&L
Daily loss limit
Orders today
Market status
Next poll

============================================================
16. PAPER MODE
============================================================

config.yaml:

mode:
  trading_mode: "PAPER"

PAPER mode must NEVER call the live Dhan order-placement API.

Simulate:

- entry
- fill
- position
- exit
- TP
- SL
- P&L

Use current option LTP as simulated fill price unless configured order type requires another sensible simulation.

PAPER and LIVE must use the SAME strategy logic.

Only execution behavior differs.

Never silently convert LIVE to PAPER.

If:

trading_mode: LIVE

then actual Dhan orders may be submitted.

============================================================
17. LIVE MODE
============================================================

LIVE mode must:

1. Validate credentials.
2. Validate security ID.
3. Validate option contract.
4. Validate lot size.
5. Validate order quantity.
6. Validate order type.
7. Display a readable order preview.
8. Submit order.
9. Capture order ID.
10. Check order status.
11. Confirm actual TRADED/fill state.
12. Determine actual fill price.
13. Create internal position state only after successful execution.

Never assume:

API request succeeded = order filled.

The reference Dhan implementation explicitly checks order lifecycle and supports get_order_by_id/get_order_list/get_trade_book. :chatgpt-content-reference{index="4"}

============================================================
18. ORDER TYPE
============================================================

Keep order type configurable but minimal.

Example:

execution:
  order_type: "LIMIT"

Allowed:

LIMIT
MARKET

Default to LIMIT where practical.

If LIMIT:

Use current bid/ask/option premium intelligently.

Do not create complicated order execution algorithms.

If MARKET is selected, follow current Dhan SDK behavior and document that API market orders may be handled by Dhan using its current market protection behavior.

Validate tick size.

Round price according to contract tick size.

============================================================
19. LOT SIZE
============================================================

For options:

quantity must be a valid multiple of the contract lot size.

Do NOT hardcode:

NIFTY lot size
BANKNIFTY lot size
SENSEX lot size

Read it from security master.

Configuration should use:

lots: 1

The actual order quantity should be:

lots × contract lot size

Display both:

Lots
Actual quantity

Reject invalid quantity before LIVE execution.

The supplied Dhan reference explicitly requires F&O quantities to respect the security-master lot size. :chatgpt-content-reference{index="5"}

============================================================
20. POSITION MODEL
============================================================

Default:

max_open_strategies: 1

Only one active option position at a time.

States:

FLAT
ENTRY_PENDING
LONG_OPTION
EXIT_PENDING
STOPPING

Do not pyramid.

Do not create duplicate entries.

If an entry order is pending:

do not submit another entry.

If an exit order is pending:

do not submit another exit.

If an active position exists:

ignore new entry signals until the position is closed.

============================================================
21. TP / SL
============================================================

TP and SL must be configurable.

Support:

PERCENT

and:

ABSOLUTE

For a BUY option position:

PERCENT TP:

TP = entry_price × (1 + tp_percent / 100)

PERCENT SL:

SL = entry_price × (1 - sl_percent / 100)

ABSOLUTE:

TP = entry_price + value
SL = entry_price - value

Round TP/SL to contract tick size.

Check TP/SL against current option premium.

Do NOT calculate option TP/SL using the underlying index price.

TP/SL applies to the OPTION PREMIUM.

Example:

BUY SENSEX 82500 CE
Entry premium = ₹100

TP = 20%

TP = ₹120

SL = 10%

SL = ₹90

If option LTP reaches ₹120:

1. Exit option.
2. Confirm LIVE exit.
3. Calculate P&L.
4. Display trade result.
5. Stop bot if configured TP/SL shutdown is enabled.

If option LTP reaches ₹90:

1. Exit option.
2. Confirm LIVE exit.
3. Calculate P&L.
4. Display trade result.
5. Stop bot.

Do not immediately re-enter after TP/SL.

============================================================
22. COMBINED P&L
============================================================

The bot must calculate the combined P&L of ALL trades placed by this algo during the current trading session.

Track:

realized P&L
+
current unrealized P&L
=
combined algo P&L

Display:

Today's Algo P&L

Do not accidentally display the user's entire Dhan account P&L as the algo P&L.

Only include trades/positions attributable to this bot.

Use a unique order tag/correlation identifier for orders where supported.

Also maintain an internal trade ledger in memory for the current process.

For LIVE reconciliation, use Dhan order/trade/position APIs where possible.

Do not create a database.

P&L should be:

BUY option:

(current_option_price - entry_price) × quantity

For realized exit:

(exit_price - entry_price) × quantity

Do not claim this is exact net profit after brokerage, STT, GST, exchange charges, slippage, etc. unless those costs are actually included.

Clearly label:

Gross P&L

and explain that actual net P&L may differ.

============================================================
23. MAX LOSS PER DAY
============================================================

Support:

max_loss_per_day_inr

Example:

max_loss_per_day_inr: 500

Use INR because Dhan NSE/BSE trading P&L is denominated in INR.

If combined algo P&L reaches or falls below the configured maximum daily loss:

1. Stop new entries.
2. Close any active position.
3. Verify the position is closed in LIVE mode.
4. Record reason:
   MAX_DAILY_LOSS
5. Stop the bot.

Do not re-enter after max daily loss if:

no_reentry_after_max_loss: true

This setting should default to true.

============================================================
24. MAX ORDERS PER DAY
============================================================

Support:

max_orders_per_day: 10

Count successful entry orders and, if appropriate, track exit orders separately.

Do not exceed the configured entry limit.

When limit is reached:

- no new entry
- existing position may still be managed
- TP/SL remains active
- exit remains allowed
- display MAX ORDERS REACHED

============================================================
25. NO RE-ENTRY AFTER STOP LOSS
============================================================

Support:

no_reentry_after_stop_loss: true

If SL is hit:

- close position
- mark stop-loss lock
- do not create another entry
- stop the bot by default

Even if the bot is configured to continue, the no-reentry flag must prevent a new trade.

============================================================
26. OPPOSITE SIGNAL
============================================================

Because this implementation is option buying:

If currently holding CE and a valid bearish Half Trend signal appears:

close CE.

If currently holding PE and a valid bullish Half Trend signal appears:

close PE.

Add a simple config:

exit_on_opposite_signal: true

If false:

continue managing the current option until TP/SL/session stop.

If true:

close on confirmed opposite signal.

Do not immediately reverse in the same polling cycle.

Exit first.

Only consider a future new entry if the bot remains active and the configured risk/session rules allow it.

============================================================
27. SESSION / RUN MODE
============================================================

Support:

run_mode: "CONTINUOUS"

and:

run_mode: "SCHEDULED"

CONTINUOUS:

- start immediately
- trade whenever market/session rules permit
- continue while python main.py is running

SCHEDULED:

- only allow new entries between start_time and stop_time
- use timezone from config
- default Asia/Kolkata

Example:

run_mode: "SCHEDULED"
start_time: "09:20"
stop_time: "15:20"

If session.enabled = false:

Do not enforce the configured start/stop time.

If session is enabled and stop time is reached:

1. Stop new entries.
2. If close_all_positions_at_stop = true:
   close all positions belonging to this algo.
3. Verify closure.
4. Display final P&L.
5. If stop_bot_after_close = true:
   exit the process.

IMPORTANT:

`close_all_positions_at_stop` controls whether positions are closed.

`stop_bot_after_close` controls whether the Python process exits.

Do NOT confuse these two settings.

============================================================
28. INTRADAY / SWING / POSITIONAL
============================================================

Keep the architecture flexible enough that holding behavior can later support:

INTRADAY
SWING
POSITIONAL

but do NOT create a complicated framework.

Use a simple configuration such as:

holding_mode: "INTRADAY"

Supported values:

INTRADAY
SWING
POSITIONAL

INTRADAY:

- session close behavior applies
- close positions at configured session stop if enabled

SWING/POSITIONAL:

- do not automatically force an intraday exit merely because the normal intraday session ended
- TP/SL can continue to manage the position
- do not invent broker behavior
- validate that the selected Dhan product type supports the intended holding behavior

Do not claim overnight option holding is supported by a particular Dhan product unless the SDK/account configuration actually supports it.

For the initial/default configuration:

holding_mode = INTRADAY

Keep this simple.

============================================================
29. MARKET CLOSED BEHAVIOR
============================================================

When the market is closed:

NEVER place a new order.

The bot should still fetch/display the latest available information when the API permits.

Continue showing:

- underlying last price
- option last premium if a selected contract exists
- Half Trend value based on latest confirmed candle
- latest candle timestamp
- current position
- current P&L
- market status

Do NOT treat stale data as a fresh trading signal.

Do NOT manufacture new candles after market close.

Use the latest confirmed candle.

After detecting market close:

perform at least:

post_close_polls: 3

Default:

post_close_poll_seconds: 10

Therefore approximately:

3 polls × 10 seconds = approximately 30 seconds

During these polls:

- show MARKET CLOSED
- show poll count
- show latest available prices
- show option premium
- show P&L
- show indicator values
- do not enter new trades

After post-close polling:

1. Perform final position check.
2. If configured to close positions, close them.
3. Print final summary.
4. Exit if stop_bot_after_close = true.

============================================================
30. HALF TREND CANDLE HANDLING
============================================================

This is critical.

Polling:

5 seconds

Candle:

5 minutes

These are NOT the same.

Never generate a new strategy signal every 5 seconds.

Maintain:

last_confirmed_candle_timestamp

Only evaluate a new entry signal when a new confirmed candle becomes available.

Example:

10:05 candle confirmed.

If it closes above Half Trend:

BUY CE signal.

At:

10:05:05
10:05:10
10:05:15
...

Do NOT generate another BUY from the same candle.

Wait for the next confirmed candle.

============================================================
31. OPTION PREMIUM MONITORING
============================================================

When a position is open:

Every polling cycle:

- fetch current option LTP
- calculate unrealized P&L
- calculate TP/SL state
- display premium
- display P&L

Do not repeatedly fetch the full option chain if only the selected option LTP is needed.

Use ticker/quote data for the resolved option security ID.

If option price data is temporarily unavailable:

- display DATA ERROR
- do not assume TP/SL was hit
- retry safely
- do not duplicate exit orders

============================================================
32. DATA STALENESS
============================================================

Never trade on obviously stale data.

Track:

- latest underlying candle timestamp
- latest underlying price timestamp if available
- latest option LTP timestamp if available

If data is stale:

show:

DATA STALE

Do not create a new entry.

Continue monitoring an existing position safely where possible.

Distinguish:

MARKET CLOSED

from:

DATA ERROR

from:

DATA STALE

============================================================
33. CLI — SCI-FI STYLE
============================================================

Create a clean, lightweight sci-fi terminal dashboard using ANSI/standard terminal formatting only.

Do NOT use Streamlit or a web UI.

The CLI should look professional.

Example layout:

╔══════════════════════════════════════════════════════════╗
║       SRP HALF TREND OPTIONS ENGINE                      ║
╠══════════════════════════════════════════════════════════╣
║ MODE        : PAPER                                      ║
║ UNDERLYING  : SENSEX                                     ║
║ TIMEFRAME   : 5M                                         ║
║ MARKET      : OPEN                                       ║
╠══════════════════════════════════════════════════════════╣
║ UNDERLYING LTP : 72,771.72                               ║
║ HALF TREND     : 72,832.01                               ║
║ SIGNAL         : BEARISH                                 ║
║ CANDLE         : 2026-09-28 14:25:00                     ║
╠══════════════════════════════════════════════════════════╣
║ OPTION         : SENSEX ... PE                           ║
║ OPTION LTP     : ₹125.50                                  ║
║ POSITION       : LONG PE                                  ║
║ QUANTITY       : 20                                       ║
║ ENTRY          : ₹118.00                                  ║
║ TP             : ₹141.60                                  ║
║ SL             : ₹106.20                                  ║
║ CURRENT P&L    : ₹150.00                                  ║
╠══════════════════════════════════════════════════════════╣
║ TODAY ALGO P&L : ₹150.00                                  ║
║ ORDERS TODAY   : 2 / 10                                   ║
║ DAILY LOSS LIM : ₹500                                     ║
╠══════════════════════════════════════════════════════════╣
║ POLLING        : 5 sec                                    ║
║ NEXT POLL      : 4 sec                                    ║
║ LAST ACTION    : BUY PE                                   ║
╚══════════════════════════════════════════════════════════╝

Use lightweight terminal output.

Do not make the UI a separate module.

Keep it understandable.

============================================================
34. STARTUP DISPLAY
============================================================

At startup display:

- Bot name
- Version
- PAPER/LIVE
- Underlying
- Underlying security ID
- timeframe
- Half Trend amplitude
- Half Trend channel deviation
- option selection
- expiry
- strike mode
- lots
- quantity after lot calculation
- TP
- SL
- max daily loss
- max orders/day
- run mode
- session
- timezone
- polling interval

If LIVE:

display clearly:

WARNING: LIVE TRADING ENABLED

Do not hide the live status.

============================================================
35. STARTUP VALIDATION
============================================================

Validate before trading:

- config.yaml exists
- YAML is valid
- .env exists
- credentials exist for LIVE/data access
- selected underlying is supported
- underlying resolves
- option chain is available
- expiry is valid
- strike selection is valid
- option type mapping is valid
- lot count > 0
- actual quantity is valid
- timeframe > 0
- polling_seconds > 0
- TP configuration valid
- SL configuration valid
- daily loss limit valid
- max orders valid
- session times valid
- timezone valid
- run mode valid
- trading mode valid

If any critical validation fails:

- print clear reason
- do not trade
- exit safely

============================================================
36. CENTRAL EXECUTION FUNCTIONS
============================================================

Even though all code is in main.py, separate responsibilities into clear functions.

At minimum create functions similar to:

load_config()
load_environment()
create_dhan_client()
load_security_master()
resolve_underlying()
resolve_option_contract()
fetch_expiry_list()
fetch_option_chain()
select_option_contract()
fetch_underlying_candles()
fetch_ltp()
calculate_atr()
calculate_half_trend()
generate_signal()
is_new_confirmed_candle()
get_current_position()
calculate_position_pnl()
calculate_combined_algo_pnl()
calculate_tp_sl()
validate_entry()
place_entry_order()
check_order_status()
place_exit_order()
close_current_position()
check_take_profit()
check_stop_loss()
check_opposite_signal()
check_daily_loss()
check_order_limit()
check_trading_session()
check_market_closed()
check_manual_stop()
render_dashboard()
paper_enter()
paper_exit()
live_enter()
live_exit()
graceful_shutdown()
main()

Add functions only when genuinely necessary.

Do not split them into other files.

============================================================
37. FUNCTION DOCUMENTATION
============================================================

Every important function in main.py must have a useful docstring explaining:

- purpose
- inputs
- outputs
- why the function exists
- important trading considerations

Example:

def calculate_half_trend(...):
    """
    Calculate the Half Trend indicator from confirmed OHLC data.

    Purpose:
        Determine the current trend state used by the option-entry
        strategy.

    Inputs:
        OHLC dataframe and configured Half Trend parameters.

    Output:
        DataFrame/Series containing Half Trend values and trend state.

    Trading use:
        A confirmed candle closing above the Half Trend line creates
        a bullish signal; a close below creates a bearish signal.

    Important:
        The calculation must not use future candle information.
    """

Use comments around important calculations.

Comments should explain WHY something is done, not merely repeat the code.

============================================================
38. MAIN.PY STRUCTURE
============================================================

Organize main.py into clear sections:

# ============================================================
# IMPORTS
# ============================================================

# ============================================================
# CONFIGURATION
# ============================================================

# ============================================================
# DATA CLASSES / STATE
# ============================================================

# ============================================================
# DHAN CONNECTION
# ============================================================

# ============================================================
# SECURITY MASTER
# ============================================================

# ============================================================
# MARKET DATA
# ============================================================

# ============================================================
# OPTION SELECTION
# ============================================================

# ============================================================
# HALF TREND INDICATOR
# ============================================================

# ============================================================
# STRATEGY
# ============================================================

# ============================================================
# PAPER EXECUTION
# ============================================================

# ============================================================
# LIVE EXECUTION
# ============================================================

# ============================================================
# POSITION MANAGEMENT
# ============================================================

# ============================================================
# RISK MANAGEMENT
# ============================================================

# ============================================================
# SESSION / MARKET STATUS
# ============================================================

# ============================================================
# CLI DASHBOARD
# ============================================================

# ============================================================
# SHUTDOWN
# ============================================================

# ============================================================
# MAIN LOOP
# ============================================================

Keep the code sequential and easy to follow.

============================================================
39. STOP.PY
============================================================

Create a simple stop.py.

Running:

python stop.py

must create a stop signal that main.py detects.

stop.py must:

- not require Dhan credentials
- not import main.py
- not initialize Dhan
- not place orders

main.py should check for the stop signal every polling cycle.

When manual stop is detected:

1. Stop new entries.
2. If configured to close all algo positions on manual stop, close them.
3. Verify LIVE position closure.
4. Print final P&L.
5. Exit gracefully.

Use a simple local stop file such as:

.bot_stop

Do not add a complex process-management system.

============================================================
40. GRACEFUL SHUTDOWN
============================================================

Centralize shutdown in:

graceful_shutdown(reason)

It should:

- stop new entries
- close position if required by the reason/config
- verify LIVE position closure
- calculate final P&L
- display final summary
- clean up temporary state
- exit safely

Handle:

- TP
- SL
- max daily loss
- max orders
- session stop
- manual stop
- market close
- Ctrl+C
- critical runtime failure

Avoid duplicated exit logic.

============================================================
41. CLOSE CURRENT POSITION
============================================================

Create ONE central function:

close_current_position(reason)

It must:

1. Determine active position.
2. Determine option security ID.
3. Determine quantity.
4. Determine opposite transaction type.
5. Submit exit in LIVE mode.
6. Simulate exit in PAPER mode.
7. Verify LIVE exit.
8. Calculate realized P&L.
9. Add trade to algo P&L.
10. Display exit summary.
11. Update state.
12. Prevent unintended immediate re-entry.

Use it for:

- TP
- SL
- opposite signal
- session end
- manual stop
- daily loss
- shutdown

Do not duplicate exit logic.

============================================================
42. ERROR HANDLING
============================================================

The bot must not crash because of a temporary API problem.

Catch and clearly report:

- API timeout
- rate limit
- invalid response
- empty option chain
- missing LTP
- order rejection
- order pending
- order cancellation
- invalid security ID
- missing credentials
- market closed
- stale data
- invalid configuration

For temporary failures:

- display error
- retry safely
- never submit duplicate orders

For critical failures:

- close existing position if safely possible
- stop bot
- print reason

Never hide exceptions silently.

============================================================
43. RATE LIMIT SAFETY
============================================================

Respect Dhan API limits.

Do not call every API every 5 seconds just because the main loop runs every 5 seconds.

Use caching/state.

Recommended behavior:

Security master:
- once at startup

Underlying candle history:
- refresh when a new candle is expected
- or when data is required

Underlying LTP:
- polling cycle

Active option LTP:
- polling cycle

Option chain:
- only when selecting/reselecting a contract
- respect Dhan option-chain request interval

Order status:
- only while order is pending

Positions:
- refresh at sensible intervals and after order events

Never create duplicate orders because an API call is slow.

============================================================
44. LIVE ORDER SAFETY
============================================================

Before every LIVE order:

show an order preview containing:

ACTION
UNDERLYING
OPTION SYMBOL
SECURITY ID
EXPIRY
STRIKE
CE/PE
QUANTITY
LOT SIZE
ORDER TYPE
PRODUCT TYPE
PRICE
REASON
CURRENT LTP
TP
SL

Then place the order.

Capture:

order_id

Then verify:

orderStatus

Prefer:

TRADED

before treating the order as successfully entered.

If the order remains PENDING:

do not submit another order.

If the order is REJECTED/CANCELLED:

reset state safely and record the reason.

Never assume API success means execution.

============================================================
45. PAPER EXECUTION
============================================================

Paper mode should simulate the same lifecycle:

SIGNAL
-> OPTION SELECTION
-> PAPER ENTRY
-> POSITION
-> P&L MONITORING
-> TP/SL/OPPOSITE SIGNAL
-> PAPER EXIT
-> TRADE RECORD

Display:

PAPER ENTRY
PAPER EXIT

clearly.

============================================================
46. DAILY TRADE STATE
============================================================

Maintain in-memory state for:

orders_today
trades_today
realized_pnl
unrealized_pnl
combined_pnl
current_position
last_signal
last_signal_candle
last_option_contract
stop_loss_hit
max_loss_hit
session_stop_reached

No database.

Reset daily counters when a new trading date begins.

Use Asia/Kolkata for trading date.

============================================================
47. RESTART SAFETY
============================================================

On startup in LIVE mode:

query Dhan positions.

If an existing relevant option position is already open:

do not blindly create a new position.

Display:

EXISTING LIVE POSITION DETECTED

Reconcile the bot state as safely as possible.

If the existing position cannot confidently be attributed to this algo:

do not trade automatically.

Require a safe state rather than guessing.

============================================================
48. OPTION POSITION IDENTIFICATION
============================================================

Do not close unrelated Dhan positions.

The bot must operate only on positions attributable to this algo.

Use:

- order tag/correlation ID where supported
- selected security ID
- current session state
- trade/order history

Do not blindly close every Dhan F&O position in the account.

The term "close all positions" in this project means:

close all positions belonging to THIS ALGO.

============================================================
49. CONFIG.YAML — KEEP IT MINIMAL
============================================================

Create a clean final config.yaml.

Do not overwhelm the user with implementation parameters.

Only expose settings that a trader would realistically change.

The final YAML should be approximately structured like:

mode:
  trading_mode: "PAPER"        # PAPER or LIVE

market:
  underlying: "SENSEX"         # SENSEX | NIFTY | BANKNIFTY
  timeframe_minutes: 5

strategy:
  amplitude: 2
  channel_deviation: 2
  signal_confirmation: "CANDLE_CLOSE"
  exit_on_opposite_signal: true

option:
  expiry_mode: "NEAREST"       # NEAREST | CONFIGURED
  expiry: ""                   # YYYY-MM-DD when CONFIGURED
  strike_mode: "ATM"           # ATM | ITM | OTM
  strike_offset: 0
  lots: 1

execution:
  order_type: "LIMIT"
  product_type: "INTRADAY"

risk:
  take_profit:
    enabled: true
    type: "PERCENT"
    value: 20

  stop_loss:
    enabled: true
    type: "PERCENT"
    value: 10

  max_open_strategies: 1
  max_orders_per_day: 10
  max_loss_per_day_inr: 500
  no_reentry_after_stop_loss: true
  no_reentry_after_max_loss: true

session:
  enabled: true
  timezone: "Asia/Kolkata"
  run_mode: "SCHEDULED"        # CONTINUOUS | SCHEDULED
  start_time: "09:20"
  stop_time: "15:20"
  close_all_positions_at_stop: true
  stop_bot_after_close: true

runtime:
  polling_seconds: 5
  post_close_polls: 3
  post_close_poll_seconds: 10

You may adjust the exact nesting if it makes the implementation cleaner.

Do not add unnecessary settings.

============================================================
50. CONFIGURATION VALIDATION
============================================================

Validate all values.

Examples:

underlying must be:

SENSEX
NIFTY
BANKNIFTY

trading_mode:

PAPER
LIVE

run_mode:

CONTINUOUS
SCHEDULED

strike_mode:

ATM
ITM
OTM

expiry_mode:

NEAREST
CONFIGURED

TP/SL type:

PERCENT
ABSOLUTE

Reject invalid values with a clear message.

============================================================
51. REQUIREMENTS.TXT
============================================================

Keep dependencies minimal.

At minimum:

dhanhq
pandas
PyYAML
python-dotenv

Do not use TA-Lib.

Do not add:

numpy separately unless actually required and not already provided through pandas.

Do not add rich, colorama, blessed, curses libraries merely for the CLI.

Use ANSI terminal output with standard Python where possible.

Keep AWS 1 GB compatibility as a priority.

Use Python 3.10+.

============================================================
52. AWS 1 GB RAM COMPATIBILITY
============================================================

The bot must run on:

- local macOS
- Linux
- Windows where supported by Python
- AWS Linux VM with approximately 1 GB RAM

Do not use:

- database
- Redis
- large frameworks
- ML libraries
- browser automation
- GUI
- Docker
- WebSocket unless absolutely necessary

Keep memory usage low.

Do not keep unlimited candle history in memory.

Only retain the minimum candles needed for Half Trend calculation.

Use pandas efficiently.

Do not continuously append duplicate candles.

============================================================
53. ARCHITECTURE.MD
============================================================

Create architecture.md as a detailed technical chapter explaining the actual implementation.

It must match the actual code.

Do not document features that are not implemented.

Structure it like:

# Half Trend Dhan Options Algo

## 1. Overview

Explain:

- what the bot does
- Dhan integration
- index signal generation
- option selection
- CE/PE mapping
- PAPER/LIVE
- polling
- TP/SL
- risk management

## 2. Strategy Concept

Explain Half Trend.

Explain:

- amplitude
- channel deviation
- trend state
- Half Trend line
- confirmed candle
- bullish signal
- bearish signal

## 3. Mathematical Explanation

Explain the implemented Half Trend calculation step by step.

Use simple equations.

Explain every important variable.

Clearly state the formula actually implemented.

Do not falsely claim it is the proprietary internal Dhan formula if that cannot be verified.

## 4. Signal Generation

Example:

SENSEX candle
↓
Half Trend calculated
↓
confirmed candle closes above Half Trend
↓
BULLISH
↓
select CE
↓
select expiry
↓
select ATM/ITM/OTM
↓
get option security ID
↓
BUY CE
↓
monitor premium
↓
TP/SL/opposite signal/session exit

Bearish:

SENSEX candle
↓
Half Trend calculated
↓
confirmed candle closes below Half Trend
↓
BEARISH
↓
select PE
↓
BUY PE
↓
monitor premium
↓
TP/SL/opposite signal/session exit

## 5. Architecture

Explain:

config.yaml
↓
main.py
↓
Dhan authentication
↓
Security master
↓
Underlying market data
↓
Half Trend
↓
Signal engine
↓
Option chain
↓
Option selection
↓
Execution
↓
Position manager
↓
Risk manager
↓
P&L
↓
CLI

## 6. Function-by-Function Explanation

For every important function in main.py explain:

- purpose
- inputs
- outputs
- how it works
- where it is used
- failure behavior

## 7. Complete Trade Lifecycle

Explain:

START
↓
load config
↓
load environment
↓
connect Dhan
↓
load security master
↓
resolve underlying
↓
fetch candles
↓
calculate Half Trend
↓
wait for confirmed candle
↓
signal
↓
option selection
↓
order preview
↓
entry
↓
order verification
↓
position monitoring
↓
TP/SL/opposite signal
↓
exit
↓
P&L
↓
continue or stop

## 8. Example Bullish Trade

Use an illustrative example such as:

Underlying:
SENSEX

Signal:
Bullish

Option:
ATM CE

Entry premium:
₹100

Quantity:
20

TP:
20%

SL:
10%

TP:
₹120

SL:
₹90

If TP hits:

P&L =
(120 - 100) × 20
= ₹400 gross

If SL hits:

P&L =
(90 - 100) × 20
= -₹200 gross

Clearly explain that actual net P&L differs after charges/slippage.

## 9. Example Bearish Trade

Example:

SENSEX bearish signal
→ BUY PE

Entry premium:
₹100

Quantity:
20

TP:
₹120

SL:
₹90

Show both outcomes.

## 10. Option Selection Examples

Explain:

ATM

ITM

OTM

strike_offset

and how CE/PE selection works.

Use generic examples.

## 11. TP/SL

Explain option-premium based TP/SL.

## 12. Combined P&L

Explain:

realized P&L
+
unrealized P&L
=
combined algo P&L

Explain max daily loss.

## 13. Parameter Tuning

Explain how traders can experimentally evaluate:

- Half Trend amplitude
- channel deviation
- timeframe
- expiry selection
- ATM/ITM/OTM
- strike offset
- TP
- SL
- polling interval
- signal confirmation
- quantity

Explain trade-offs.

Examples:

Smaller amplitude:
- more responsive
- potentially more signals/noise

Larger amplitude:
- slower
- potentially fewer signals

Smaller timeframe:
- more signals
- potentially more noise

Larger timeframe:
- fewer signals
- slower signals

Higher TP:
- larger target
- may require stronger movement

Lower TP:
- smaller target
- may exit sooner

Wider SL:
- more price room
- larger potential loss

Tighter SL:
- smaller loss
- potentially more stop-outs

Do NOT promise profitability.

Do NOT state that any parameter guarantees profit.

Explain that proper evaluation should use:

- backtesting
- historical analysis
- paper trading
- forward testing
- transaction costs
- slippage
- different market conditions

## 14. Risk Management

Explain:

- position sizing
- lot size
- max orders
- max daily loss
- no duplicate trades
- no re-entry after SL
- LIVE/PAPER
- stale data
- API failures
- session closure

## 15. Market Closed Behavior

Explain the configured post-close polling.

Example:

3 polls
×
10 seconds
≈
30 seconds

Explain why no new trade is placed.

## 16. stop.py

Explain:

python stop.py

and how main.py detects the stop file.

## 17. AWS 1 GB VM

Provide simple commands for:

python virtual environment
pip install -r requirements.txt
create .env
edit config.yaml
run:

python main.py

stop:

python stop.py

Keep instructions lightweight.

## 18. Troubleshooting

Include:

- missing credentials
- Dhan data plan
- static IP/order access
- security ID not found
- option chain unavailable
- invalid expiry
- invalid strike
- order rejected
- order pending
- market closed
- stale data
- rate limit
- invalid YAML
- LIVE/PAPER confusion
- duplicate order prevention

The reference material notes that Dhan order APIs require appropriate static-IP setup and market-data APIs may require an active data plan. Explain these requirements without assuming the user's account configuration. :chatgpt-content-reference{index="6"}

## 19. Code-to-Architecture Mapping

Map each major architecture component to its function in main.py.

## 20. Future Portability

Explain that strategy logic is deliberately kept separate from direct Dhan API calls within main.py so that another broker can be implemented later.

Do NOT implement another broker now.

Do NOT create a multi-broker framework.

============================================================
54. IMPORTANT OPTION-SPECIFIC SAFETY
============================================================

Never:

- hardcode option security IDs
- hardcode lot sizes
- hardcode expiry
- assume fixed strike intervals
- trade the underlying instead of the option
- place a CE and PE simultaneously
- pyramid
- duplicate an order
- close unrelated Dhan positions
- treat API submission as execution
- use stale data for a new entry
- generate multiple trades from one candle

============================================================
55. IMPORTANT STATE MACHINE
============================================================

Implement deterministic state behavior:

FLAT:
    look for signal

ENTRY_PENDING:
    wait for order status
    do not submit another entry

LONG_OPTION:
    monitor premium
    monitor TP
    monitor SL
    monitor opposite signal
    monitor daily loss
    monitor session

EXIT_PENDING:
    wait for exit confirmation
    do not submit another exit

STOPPING:
    no new trades
    finish safe shutdown

TP:
    close
    record P&L
    stop

SL:
    close
    record P&L
    stop

MAX_DAILY_LOSS:
    close
    record P&L
    stop

SESSION_END:
    stop new entries
    close if configured
    stop if configured

MANUAL_STOP:
    stop new entries
    close if configured
    shutdown

MARKET_CLOSED:
    no new entries
    perform configured post-close observations
    final position check
    close if configured
    exit if configured

============================================================
56. FINAL CODE REVIEW
============================================================

After creating all files, thoroughly review the generated code.

Check:

1. Python syntax
2. Missing imports
3. Undefined variables
4. Undefined functions
5. Invalid YAML
6. Incorrect Dhan SDK method names
7. Incorrect Dhan constants
8. Incorrect option-chain parsing
9. Incorrect security-master parsing
10. Incorrect expiry handling
11. Incorrect strike selection
12. Incorrect lot-size calculation
13. Incorrect CE/PE mapping
14. Incorrect Half Trend calculation
15. Look-ahead bias
16. Same-candle duplicate signals
17. Duplicate orders
18. Pending order handling
19. LIVE/PAPER leakage
20. TP calculation
21. SL calculation
22. Option-premium P&L
23. Combined P&L
24. Max daily loss
25. Max orders/day
26. Session handling
27. CONTINUOUS mode
28. SCHEDULED mode
29. Timezone handling
30. Market-closed handling
31. Post-close polling
32. Manual stop
33. Ctrl+C
34. API failures
35. Rate limiting
36. stale data
37. 1 GB RAM compatibility
38. unnecessary configuration
39. unnecessary dependencies
40. documentation/code mismatch

Remove unnecessary code.

Remove duplicate logic.

Remove unused imports.

Remove unused configuration.

Remove unused functions.

Do not add complexity merely to make the project look more sophisticated.

============================================================
57. FINAL FILE REQUIREMENT
============================================================

The final workspace should contain:

main.py
config.yaml
.env
env.example
stop.py
requirements.txt
architecture.md

Do not create engine.yaml.

Do not create any other Python modules.

api-scrip-master.csv may exist as an external/runtime security-master data file, but do not create application logic around additional configuration files.

============================================================
58. IMPORTANT IMPLEMENTATION RULE
============================================================

The final code must be genuinely runnable.

Do not generate pseudo-code.

Do not leave:

TODO: implement

inside critical trading functions.

Do not leave fake Dhan API calls.

Use actual DhanHQ v2 method names from the supplied reference.

Where an SDK response can vary, write defensive parsing.

If a particular SDK capability cannot be safely verified:

- isolate it
- fail clearly
- do not invent an API

============================================================
59. FINAL EDUCATIONAL CODE QUALITY
============================================================

Although this is a real trading bot, write the code in a very readable way.

Prefer:

simple functions
clear variable names
small logical sections
type hints
dataclasses where useful
clear comments
clear docstrings
deterministic state transitions

Avoid clever one-liners.

Avoid unnecessary abstraction.

Every important trading calculation should be understandable by reading main.py.

At the end of architecture.md, include a section explaining that the implementation, code comments, function explanations, architecture diagrams/flows, trade examples, P&L examples, and parameter-tuning explanations are intentionally written clearly enough to be useful as high-quality technical educational material and can be reused in books or structured learning material.

Do not claim profitability.

Do not promise that the strategy will make money.

Explain how a trader can test and tune the strategy responsibly.

============================================================
60. FINAL INSTRUCTION TO COPILOT
============================================================

NOW IMPLEMENT THE COMPLETE PROJECT.

First inspect the supplied reference files.

Then create:

main.py
config.yaml
.env
env.example
stop.py
requirements.txt
architecture.md

Implement the complete Dhan-only Half Trend options strategy described above.

Default underlying:

SENSEX

Default signal:

confirmed candle close above Half Trend
→ BUY CE

confirmed candle close below Half Trend
→ BUY PE

Default Half Trend:

amplitude = 2
channel_deviation = 2

Default timeframe:

5 minutes

Default polling:

5 seconds

Default mode:

PAPER

Default holding mode:

INTRADAY

Default strike:

ATM

Default expiry:

NEAREST

Default:

max_open_strategies = 1

max_orders_per_day = 10

Use INR for the daily loss limit because this is Dhan Indian-market trading.

Before considering the implementation complete, review the entire code and remove unnecessary things.

Verify that the code is internally consistent.

Verify that config.yaml is the single source of truth for trading configuration.

Verify that .env is the only source of credentials.

Verify that PAPER mode can never place an actual order.

Verify that LIVE mode uses real Dhan order APIs.

Verify that option security IDs and lot sizes are resolved dynamically.

Verify that the bot cannot place duplicate entries.

Verify that TP/SL exits the option position and stops the bot when configured.

Verify that max daily loss closes the algo's active position and stops the bot.

Verify that session stop can close all positions belonging to this algo.

Verify that market-close post polling works.

Verify that current underlying price and option premium are displayed.

Verify that Half Trend values are displayed.

Verify that current position and P&L are displayed.

Verify that the CLI clearly shows when the bot will trade, what it is monitoring, and why a signal was generated.

Verify that the code remains lightweight enough for an AWS 1 GB RAM Linux VM.

Do not merely describe the implementation.

Actually write the complete files in the workspace.

Then perform a final code review for syntax errors, missing imports, undefined functions, invalid configuration, incorrect Dhan SDK usage, incorrect Half Trend logic, duplicate order paths, incorrect option selection, incorrect P&L, incorrect TP/SL, session bugs, timezone bugs, market-close bugs, and PAPER/LIVE execution leakage.

Fix everything you find before considering the project complete.

The final implementation must be simple, readable, deterministic, well-commented, Dhan-specific, and structured so that the code and architecture explanations are useful as technical educational material and can later be reused, including the code with explanations, in books.
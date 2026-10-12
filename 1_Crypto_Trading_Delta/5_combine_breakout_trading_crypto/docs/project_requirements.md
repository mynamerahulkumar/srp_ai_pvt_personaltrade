```text
You are an expert Python algorithmic-trading engineer specializing in Delta Exchange India APIs, crypto derivatives, options strategies, multi-leg execution, risk management, PnL calculation, and production-grade trading systems.

Build a complete, production-ready Python trading bot for a configurable FOUR-LEG BULLISH CONDOR options strategy on Delta Exchange India.

The bot must use the supplied reference files to understand the actual Delta Exchange India API, authentication, product metadata, option contracts, order placement, positions, PnL, leverage, and related implementation details.

============================================================
1. PROJECT FILES
============================================================

The final **runnable** project must contain ONLY these files:

1. main.py
2. config.yaml
3. .env
4. stop.py
5. requirements.txt
6. architecture.md

Do not create additional Python modules, packages, databases, notebooks, Docker files, web servers, or unnecessary project files.

The following files are the **portable Delta India options reference pack**. Copy them into another strategy repo (example: bearish condor). They are not imported at runtime.

docs/srp_delta_helper.md
docs/Delta_SRP.PY
docs/PRODUCT_ID_DETAILS.md
docs/project_requirements.md   (this file)

First inspect and understand these reference files.

After understanding them, implement everything required directly inside the requested six files.

The final project MUST NOT import or depend on the docs directory.

Do not blindly copy entire reference files into main.py. Extract and reimplement the functionality required by this strategy.

Never invent Delta Exchange India API endpoints, authentication methods, product IDs, symbol formats, order parameters, contract multipliers, PnL formulas, or API behavior.

Use the supplied reference files as the primary source for Delta-specific implementation.

Implemented hard rules (do not regress these):

- Option product IDs are resolved at runtime. Never hardcode them. Chain asset is BTC not BTCUSD.
- Config FIXED_DATE uses ISO YYYY-MM-DD; Delta chain expiry is DD-MM-YYYY. FIXED_DATE never rolls.
- MANUAL strikes are exact listed contracts. Do not snap to ATM.
- Four sequential POST /v2/orders. Batch is one product_id. Default market_order. Cancel unfilled before retry/timeout/unwind/shutdown.
- Entry sequence: BUY PUT_LONG, BUY CALL_LONG, SELL PUT_SHORT, SELL CALL_SHORT.
- Combined four-leg PnL only. Freeze TP/SL at fill (PERCENT_OF_MAX_* or ABSOLUTE_PNL). Never use spot as TP/SL.
- Cash amounts multiply premium points by live contract_value (BTC options often 0.001).
- One condor per process unless allow_reentry_after_close is true. Process restart + exchange flat = new idle cycle (do not keep STOPPED+entry_locked from disk).
- Flatten only the four stored product IDs with reduce_only.

============================================================
2. PRIMARY STRATEGY
============================================================

Implement a FOUR-LEG BULLISH CONDOR options strategy.

The strategy structure is:

LEG 1:
BUY lower-strike PUT

LEG 2:
SELL higher-strike PUT

LEG 3:
SELL lower-strike CALL

LEG 4:
BUY higher-strike CALL

Example:

BUY 77000 PUT
SELL 78000 PUT
SELL 79000 CALL
BUY 80000 CALL

All four legs MUST have:

- same underlying
- same expiry
- correct PUT/CALL type
- valid strike
- valid Delta product ID
- tradable status

Do not hardcode these strikes.

The user must control expiry and strike selection through config.yaml.

============================================================
3. USER-SELECTABLE UNDERLYING
============================================================

Allow exactly ONE underlying to be selected from config.yaml.

Example supported values:

BTCUSD
ETHUSD
XAUSD
SOLUSD

Default:

BTCUSD

Example:

underlying:
  symbol: "BTCUSD"

The bot must validate that the selected underlying is available on Delta Exchange India.

Do not assume every underlying always has options available.

If the underlying is invalid/unavailable:

- do not place orders
- show a clear error
- safely return to scanning or terminate according to configuration

The strategy engine must be generic enough to support additional Delta underlyings later.

============================================================
4. LEVERAGE
============================================================

Leverage must be configurable.

Default:

100x

Example:

leverage:
  enabled: true
  value: 100

Before trading:

- validate configured leverage against Delta Exchange/product restrictions where supported
- handle rejected leverage configuration safely
- never silently use unexpected leverage

Display a clear warning when high leverage is configured.

Do not claim that high leverage increases profitability.

Explain that leverage affects capital efficiency, margin usage, liquidation/margin sensitivity, and risk.

============================================================
5. USER-SELECTABLE EXPIRY
============================================================

EXPIRY MUST BE USER-SELECTABLE.

Default mode:

FIXED_DATE

Example:

strategy:
  expiry_selection:
    mode: "FIXED_DATE"
    expiry_date: "2026-10-23"

The selected expiry must apply to ALL FOUR option legs.

If the selected expiry is:

2026-10-23

then the bot must find all four contracts expiring on that exact date.

For example:

BUY  PUT 77000
SELL PUT 78000
SELL CALL 79000
BUY  CALL 80000

All must expire on:

2026-10-23

The bot must NEVER silently replace the selected expiry with another expiry.

============================================================
6. EXPIRY MODES
============================================================

Support:

FIXED_DATE
NEAREST
NEXT_AVAILABLE

Default:

FIXED_DATE

For FIXED_DATE:

- expiry_date is mandatory
- exact date must be used
- no automatic substitution

For NEAREST:

- select nearest valid expiry

For NEXT_AVAILABLE:

- select next available valid expiry

Automatic expiry selection is allowed ONLY if the user explicitly chooses the automatic mode.

If FIXED_DATE is selected and that expiry is unavailable:

DO NOT TRADE.

Display exactly why the strategy cannot be constructed.

============================================================
7. EXPIRY DATE FORMAT
============================================================

Prefer:

YYYY-MM-DD

Example:

expiry_date: "2026-10-23"

Validate:

- syntax
- valid calendar date
- not expired
- sufficient remaining time
- exit-before-expiry requirements

If insufficient time remains:

- block new entry
- continue monitoring existing strategy if applicable

============================================================
8. DELTA OPTION SYMBOL FORMAT
============================================================

Reference examples:

P-BTC-38100-230124
C-BTC-55800-190224

Interpretation:

P-BTC-38100-230124

P = Put
BTC = underlying
38100 = strike
230124 = expiry

C-BTC-55800-190224

C = Call
BTC = underlying
55800 = strike
190224 = expiry

IMPORTANT:

Do NOT blindly construct product IDs.

Retrieve actual Delta Exchange product metadata.

Match products using:

- underlying
- option type
- strike
- expiry

Then use the exchange-provided product ID for order placement.

Use actual Delta product metadata as the source of truth.

============================================================
9. STRIKE SELECTION
============================================================

Support:

MANUAL
AUTOMATIC

Default:

MANUAL

Example:

strategy:
  strike_selection:
    mode: "MANUAL"

    manual:
      put:
        long_strike: 77000
        short_strike: 78000

      call:
        short_strike: 79000
        long_strike: 80000

This produces:

BUY PUT 77000
SELL PUT 78000
SELL CALL 79000
BUY CALL 80000

For MANUAL mode:

- find exact strikes
- use selected expiry
- use selected underlying
- do not substitute another strike automatically
- do not snap to ATM

Shipped config keys (use these in main.py / config.yaml):

```yaml
strategy:
  strike_selection_mode: "MANUAL"
  manual_strikes:
    put_long: 77000
    put_short: 78000
    call_short: 79000
    call_long: 80000
```

If a strike is unavailable:

- block the trade
- clearly identify the missing leg
- do not create a different condor

============================================================
10. AUTOMATIC STRIKE SELECTION
============================================================

When:

strike_selection.mode = "AUTOMATIC"

support configurable selection.

Example:

strategy:
  strike_selection:
    mode: "AUTOMATIC"

    automatic:
      reference: "ATM"
      put_short_offset: -1000
      put_long_offset: -2000
      call_short_offset: 1000
      call_long_offset: 2000

The bot must:

1. Fetch current underlying price.
2. Retrieve available strikes.
3. Find appropriate ATM/reference strike.
4. Apply configured offsets.
5. Map them to actual available strikes.
6. Validate all four contracts.
7. Calculate the resulting payoff.
8. Validate risk/reward.

Use actual exchange-available strikes rather than assuming a fixed strike interval.

============================================================
11. CONFIG.YAML
============================================================

Create a complete, readable configuration file.

All reasonable strategy, risk, execution, schedule, TP, SL, expiry, strike, quantity, leverage, and monitoring parameters must be configurable.

Use a structure similar to:

app:
  name: "Delta Bullish Condor"
  environment: "LIVE"
  dry_run: true
  paper_trading: false
  timezone: "Asia/Kolkata"
  polling_seconds: 5
  log_level: "INFO"

underlying:
  symbol: "BTCUSD"

leverage:
  enabled: true
  value: 100

strategy:
  enabled: true
  name: "BULLISH_CONDOR"

  quantity:
    lots: 1
    contracts_per_leg: 1

  expiry_selection:
    mode: "FIXED_DATE"
    expiry_date: "2026-10-23"

  strike_selection:
    mode: "MANUAL"

    manual:
      put:
        long_strike: 77000
        short_strike: 78000

      call:
        short_strike: 79000
        long_strike: 80000

    automatic:
      reference: "ATM"
      put_short_offset: -1000
      put_long_offset: -2000
      call_short_offset: 1000
      call_long_offset: 2000

entry:
  enabled: true

  minimum_net_credit: 0
  maximum_net_credit: 999999

  minimum_reward_risk: 0

  max_bid_ask_spread: 999999
  max_slippage: 999999

  minimum_option_liquidity: 0

  max_entry_attempts: 1
  allow_reentry_after_close: false

exit:
  take_profit:
    enabled: true
    mode: "PERCENT_OF_MAX_PROFIT"   # or ABSOLUTE_PNL
    value: 70                       # percent of combined max profit, or USD if ABSOLUTE_PNL

  stop_loss:
    enabled: true
    mode: "PERCENT_OF_MAX_LOSS"     # or ABSOLUTE_PNL
    value: 50                       # percent of combined max loss, or USD if ABSOLUTE_PNL

  # Freeze TP/SL at fill from combined four-leg payoff. Live trigger uses sum of four legs, never spot.
  # Cash = premium_points * contract_value * qty. BTC options often contract_value=0.001 so 705 points ≈ $0.70.
  # ABSOLUTE_PNL 10 cannot hit if theoretical max profit is $0.70 at quantity 1.

  close_all_strategy_positions: true

  exit_on_expiry: true
  exit_before_expiry_minutes: 30

  stop_bot_after_tp: true
  stop_bot_after_sl: true
  stop_bot_after_expiry_exit: true

day_trading:
  enabled: false

  start_time: "09:30"
  stop_time: "23:00"

  close_positions_at_stop_time: true

schedule:
  enabled: false

  bot_start_time: "09:15"

  entry_start_time: "09:30"
  entry_stop_time: "22:30"

  close_all_time: "23:00"

  stop_bot_after_close: true

risk:
  max_strategy_loss: 100
  max_daily_loss: 500

  max_open_strategies: 1
  max_orders_per_day: 10

  minimum_available_margin: 0

  require_margin_check: true

  kill_switch_enabled: true

execution:
  order_type: "market_order"

  use_limit_orders: false
  fallback_to_market: false

  order_timeout_seconds: 15

  leg_execution_delay_ms: 250

  retry_failed_leg: false
  max_leg_retries: 0

  verify_each_order: true
  verify_position_after_execution: true

monitoring:
  polling_seconds: 5

  position_refresh_seconds: 5
  order_refresh_seconds: 5

logging:
  level: "INFO"

  file_enabled: true
  file_path: "bullish_condor.log"

state:
  stop_signal_file: "STOP"
  runtime_state_file: "runtime_state.json"

IMPORTANT:

Only implement configuration settings that actually work.

Do not create fake configuration options.

Every documented option must be connected to real code.

============================================================
12. CRITICAL TP/SL DESIGN
============================================================

THIS IS ONE OF THE MOST IMPORTANT REQUIREMENTS.

TP AND SL MUST BE BASED ON THE COMBINED FOUR-LEG STRATEGY.

DO NOT calculate TP or SL independently for each option leg.

DO NOT use only the short option PnL.

DO NOT use only the long option PnL.

DO NOT trigger TP/SL based on the underlying price alone.

TP and SL must use:

TOTAL COMBINED STRATEGY PNL

across all four legs.

The four-leg strategy is treated as ONE POSITION.

============================================================
13. MAXIMUM PROFIT
============================================================

Calculate the combined strategy's maximum theoretical profit from all four legs.

For a net-credit Bullish Condor:

Maximum Profit is based on the total net credit received, adjusted for:

- quantity
- contract multiplier
- applicable contract specifications

Use actual Delta product information.

Example from the screenshot:

MAX PROFIT:
14.46 USD

This represents the combined strategy's maximum profit.

The bot must use this combined maximum profit for TP calculations.

============================================================
14. MAXIMUM LOSS
============================================================

Calculate the combined strategy's maximum theoretical loss.

For the four-leg structure:

BUY lower PUT
SELL higher PUT
SELL lower CALL
BUY higher CALL

the maximum loss must be calculated from the complete strategy payoff.

Use:

wing width
net credit/debit
quantity
contract multiplier
actual contract specifications

Example from the screenshot:

MAX LOSS:
-5.53 USD

This is the combined maximum strategy loss.

The bot must use the combined maximum loss for SL calculations.

============================================================
15. TP BASED ON COMBINED MAX PROFIT
============================================================

Default:

exit:
  take_profit:
    enabled: true
    mode: "PERCENT_OF_MAX_PROFIT"
    value: 70

Formula:

combined_max_profit = calculated maximum profit of the entire four-leg strategy

take_profit_target =
    combined_max_profit × configured_percentage / 100

Example:

Maximum Profit = 14.46 USD

TP = 70%

TP target:

14.46 × 0.70 = 10.122 USD

Therefore the strategy TP is approximately:

+10.12 USD

The bot must monitor:

TOTAL COMBINED STRATEGY PNL

When:

TOTAL STRATEGY PNL >= TP TARGET

trigger TP.

IMPORTANT:

The target must be calculated from the COMBINED MAXIMUM PROFIT.

============================================================
16. SL BASED ON COMBINED MAX LOSS
============================================================

Default:

exit:
  stop_loss:
    enabled: true
    mode: "PERCENT_OF_MAX_LOSS"
    value: 50

The maximum loss is represented as a negative number.

Example:

Maximum Loss = -5.53 USD

SL = 50%

The stop-loss threshold should be:

-5.53 × 0.50
= -2.765 USD

Therefore:

SL target ≈ -2.77 USD

The bot must trigger SL when:

TOTAL COMBINED STRATEGY PNL <= -2.77 USD

IMPORTANT:

The SL must be based on the COMBINED MAXIMUM LOSS of the complete four-leg strategy.

It must NOT calculate 50% of the loss of individual legs.

============================================================
17. ABSOLUTE TP/SL SUPPORT
============================================================

Also support absolute PnL modes.

TP:

mode: "ABSOLUTE_PNL"

value: 10

means:

Trigger TP when:

combined strategy PnL >= +10 USD

SL:

mode: "ABSOLUTE_PNL"

value: 3

means:

Trigger SL when:

combined strategy PnL <= -3 USD

Clearly document the sign convention.

For example:

TP = positive profit threshold.

SL = positive loss amount converted internally to a negative PnL threshold.

Do not create ambiguity.

============================================================
18. TP/SL DISPLAY
============================================================

The CLI must display:

MAX PROFIT:       +14.46 USD
MAX LOSS:          -5.53 USD

TP MODE:           70% OF MAX PROFIT
TP TARGET:        +10.12 USD
CURRENT PNL:       +8.40 USD

SL MODE:           50% OF MAX LOSS
SL TARGET:         -2.77 USD
CURRENT PNL:       +8.40 USD

Also show progress:

TP PROGRESS
+8.40 / +10.12 USD

SL DISTANCE
Current PnL vs -2.77 USD

============================================================
19. TP EXECUTION
============================================================

When:

TOTAL COMBINED STRATEGY PNL >= TP TARGET

immediately transition:

MONITORING
↓
TP_TRIGGERED
↓
EXITING

Then:

1. Disable new entries.
2. Lock strategy state.
3. Close ALL FOUR strategy positions.
4. Verify every closing order.
5. Handle partial closing fills.
6. Retry failed closing orders according to configuration.
7. Re-fetch exchange positions.
8. Verify all four strategy positions are closed.
9. Calculate final realized PnL.
10. Record fees where available.
11. Display final PnL.
12. Mark strategy CLOSED.
13. Stop the bot if:
   stop_bot_after_tp = true

The bot must NOT continue opening new trades after TP if configured to stop.

============================================================
20. SL EXECUTION
============================================================

When:

TOTAL COMBINED STRATEGY PNL <= SL TARGET

transition:

MONITORING
↓
SL_TRIGGERED
↓
EXITING

Then:

1. Disable new entries.
2. Lock strategy state.
3. Close ALL FOUR strategy positions.
4. Verify every closing order.
5. Handle partial closing fills.
6. Retry failed closing orders according to configuration.
7. Re-fetch exchange positions.
8. Verify all four positions are closed.
9. Calculate final realized PnL.
10. Record fees where available.
11. Display final realized PnL.
12. Mark strategy CLOSED.
13. Stop the bot if:
   stop_bot_after_sl = true

============================================================
21. TP/SL PRIORITY
============================================================

When a strategy is active:

TP/SL checks must have priority over entry evaluation.

The main loop should conceptually perform:

1. Stop signal
2. Forced exit/schedule
3. Expiry protection
4. Current strategy PnL
5. TP check
6. SL check
7. Position reconciliation
8. Dashboard
9. Only if no active strategy:
   entry evaluation

Never allow a new entry to occur after TP or SL has already been triggered.

============================================================
22. CLOSE ALL POSITIONS AFTER TP/SL
============================================================

When TP or SL triggers:

CLOSE ALL POSITIONS BELONGING TO THIS STRATEGY.

The four-leg strategy must be flattened completely.

Default behavior:

close only this bot's Bullish Condor strategy positions.

Do NOT close unrelated manual account positions.

Implement:

close_strategy_positions()

and:

verify_strategy_closed()

The bot must not report success until exchange positions are verified.

Example:

TP TRIGGERED
+
10.15 USD

EXITING STRATEGY

[1/4] Closing BUY PUT
[2/4] Closing SELL PUT
[3/4] Closing SELL CALL
[4/4] Closing BUY CALL

VERIFYING POSITIONS...

✓ PUT 77000 CLOSED
✓ PUT 78000 CLOSED
✓ CALL 79000 CLOSED
✓ CALL 80000 CLOSED

FINAL REALIZED PNL:
+9.87 USD

The final realized PnL may differ from the trigger PnL because of:

- slippage
- fees
- execution prices
- partial fills

Document this clearly.

============================================================
23. DAY TRADING MODE
============================================================

Implement:

day_trading.enabled

If enabled:

- entries allowed only within configured trading window
- stop new entries at stop time
- close open strategy positions at stop time when configured
- verify positions closed
- stop bot if configured

Example:

day_trading:
  enabled: true
  start_time: "09:30"
  stop_time: "23:00"
  close_positions_at_stop_time: true

If disabled:

- no artificial day-trading restriction
- strategy can continue until:
  TP
  SL
  expiry
  scheduled exit
  manual stop
  configured risk shutdown

============================================================
24. BOT SCHEDULE
============================================================

Support:

schedule.enabled

Parameters:

bot_start_time
entry_start_time
entry_stop_time
close_all_time
stop_bot_after_close

Differentiate:

BOT START
ENTRY WINDOW
FORCED CLOSE
BOT STOP

When scheduling is disabled:

- bot runs continuously according to strategy/risk/exit rules

============================================================
25. STRATEGY PAYOFF
============================================================

Implement mathematically correct payoff calculations for the four legs.

For:

BUY PUT lower strike
SELL PUT higher strike
SELL CALL lower strike
BUY CALL higher strike

Calculate:

- premium paid
- premium received
- net credit/debit
- put wing width
- call wing width
- maximum profit
- maximum loss
- lower breakeven
- upper breakeven
- reward/risk
- theoretical payoff at expiry

Use actual Delta contract multiplier/product specification.

Do not assume all Delta products use the same PnL convention.

Separate:

THEORETICAL PAYOFF

from:

ACTUAL LIVE PNL

============================================================
26. STRATEGY PNL
============================================================

Treat the four legs as ONE combined strategy.

Calculate:

leg 1 PnL
leg 2 PnL
leg 3 PnL
leg 4 PnL

Then:

TOTAL STRATEGY PNL

The TP/SL engine MUST use:

TOTAL STRATEGY PNL

Use exchange-reported PnL when available.

Use mathematically calculated fallback where appropriate.

Account for:

- contract multiplier
- quantity
- option pricing convention
- fees where available
- realized PnL
- unrealized PnL

Do not incorrectly mix currencies or contract units.

============================================================
27. POSITION OBJECT
============================================================

Represent the strategy as one logical position.

Track:

strategy_id
underlying
expiry
four legs
entry timestamp
entry prices
current prices
quantity
leverage
net credit
max profit
max loss
lower breakeven
upper breakeven
reward/risk
TP target
SL target
realized PnL
unrealized PnL
total PnL
fees
status

Each leg:

side
option_type
strike
expiry
symbol
product_id
quantity
order_id
entry_price
current_price
pnl
status

============================================================
28. STATE MACHINE
============================================================

Use clear states:

WAITING
SCANNING
SIGNAL_FOUND
VALIDATING
ENTERING
PARTIALLY_ENTERED
ENTERED
MONITORING
TP_TRIGGERED
SL_TRIGGERED
EXPIRY_EXIT
EXITING
CLOSED
STOPPED
ERROR

Do not rely on dozens of ambiguous Boolean flags.

============================================================
29. ENTRY PROCESS
============================================================

Every configured polling interval, default 5 seconds:

1. Check stop signal.
2. Check schedule.
3. Check day-trading window.
4. Fetch underlying price.
5. Retrieve option products.
6. Select expiry.
7. Select strikes.
8. Find product IDs.
9. Fetch bid/ask/mark/last.
10. Calculate estimated net credit.
11. Calculate max profit.
12. Calculate max loss.
13. Calculate reward/risk.
14. Validate liquidity.
15. Validate spread.
16. Validate slippage.
17. Validate margin.
18. Validate leverage.
19. Validate maximum strategy loss.
20. Validate daily loss.
21. Check duplicate strategy.
22. Generate signal.
23. Lock entry.
24. Execute four legs.
25. Verify all orders.
26. Reconcile positions.
27. Set combined TP/SL.
28. Start monitoring.

Never place repeated orders every 5 seconds.

============================================================
30. ENTRY VALIDATION
============================================================

Separate:

STRATEGY SIGNAL

from:

EXECUTION VALIDATION

from:

RISK VALIDATION

A trade is allowed only when all three pass.

Strategy conditions:

- valid expiry
- valid strikes
- valid four-leg structure
- valid net credit
- valid reward/risk
- valid payoff

Execution conditions:

- valid products
- tradable contracts
- acceptable spread
- sufficient liquidity
- acceptable slippage
- valid prices
- API available

Risk conditions:

- sufficient margin
- acceptable max loss
- daily loss within limit
- max open strategies not exceeded
- max orders not exceeded
- leverage valid

============================================================
31. FOUR-LEG EXECUTION
============================================================

Treat four orders as one strategy transaction.

Before sending orders:

- validate every contract
- validate every price
- validate quantity
- validate margin
- validate risk
- validate leverage

During entry:

- deterministic leg order
- verify every order
- monitor fills
- handle partial fills
- handle rejected orders
- handle timeout

If all four fill:

ENTERED

If only some fill:

PARTIALLY_ENTERED

Never consider partial execution to be a successful Bullish Condor.

============================================================
32. PARTIAL ENTRY RECOVERY
============================================================

If 3/4 legs fill:

1. Identify missing leg.
2. Retry according to configuration.
3. Verify order.
4. If successful:
   - reconcile
   - ENTERED

If unsuccessful:

1. Stop further entry attempts.
2. Safely unwind filled legs according to configuration.
3. Verify all positions.
4. Record incident.
5. Mark ERROR or CLOSED.
6. Do not start another strategy until exposure is resolved.

Never leave unintended naked option exposure.

============================================================
33. PARTIAL EXIT RECOVERY
============================================================

The same protection applies when closing.

If 3/4 closing orders succeed:

- remaining position must be identified
- retry closing it
- verify exchange position
- continue until all strategy positions are closed

Never report:

"CLOSED"

while any strategy leg remains open.

============================================================
34. POSITION RECONCILIATION
============================================================

The exchange is always the source of truth.

On startup:

1. Connect to Delta.
2. Fetch account.
3. Fetch positions.
4. Fetch relevant orders.
5. Identify existing strategy positions.
6. Reconstruct strategy state if possible.
7. Prevent duplicate entry.

The bot must safely handle:

- AWS reboot
- process restart
- SSH disconnect
- network outage

============================================================
35. RISK MANAGEMENT
============================================================

Before every entry:

- maximum strategy loss
- maximum daily loss
- maximum open strategies
- maximum orders/day
- available margin
- required margin
- leverage
- spread
- liquidity
- slippage
- expiry
- schedule
- duplicate positions

If any condition fails:

DO NOT TRADE.

Display the exact reason.

============================================================
36. MAXIMUM STRATEGY LOSS LIMIT
============================================================

If:

calculated maximum loss > risk.max_strategy_loss

then:

BLOCK ENTRY

Example:

Calculated Max Loss:
$125

Configured Max Strategy Loss:
$100

Result:

[ENTRY BLOCKED]
Maximum strategy loss exceeds configured risk limit.

============================================================
37. DAILY LOSS LIMIT
============================================================

Implement:

risk.max_daily_loss

When reached:

- block new entries
- optionally close existing strategy
- optionally stop bot

Daily PnL must use configured timezone.

============================================================
38. MARGIN
============================================================

Before entry:

- retrieve available margin
- calculate/estimate requirement
- apply safety buffer where configured
- block entry if insufficient

Do not assume maximum theoretical loss equals exchange margin requirement.

============================================================
39. LIQUIDITY
============================================================

For every leg:

retrieve available market data.

Check:

bid
ask
last
mark where available
volume/liquidity where available

Calculate bid/ask spread.

Block strategy when configured limits are violated.

============================================================
40. ORDER TYPES
============================================================

Support:

market_order
limit_order

Safe default for four-leg options:

market_order
use_limit_orders: false
retry_failed_leg: false
max_leg_retries: 0

Delta batch orders take ONE product_id. A condor is four products — place four sequential POST /v2/orders.

If limit orders are enabled:

- cancel any unfilled order before retry, timeout, unwind, or shutdown
- never leave working limits stacked
- fallback_to_market only when explicitly enabled

============================================================
41. FEES AND SLIPPAGE
============================================================

Where available:

- retrieve/estimate fees
- account for fees
- account for expected slippage

Display theoretical and actual values separately.

Example:

THEORETICAL MAX PROFIT: +14.46 USD
TP TARGET: +10.12 USD
ACTUAL EXIT PNL: +9.87 USD

Explain why these can differ.

============================================================
42. DRY RUN
============================================================

Default:

dry_run: true

When enabled:

- no live orders
- real market data may be fetched
- strategy calculated normally
- simulated fills
- simulated combined PnL
- simulated TP
- simulated SL
- simulated four-leg exit

CLI must clearly display:

MODE: DRY RUN

Live mode:

MODE: LIVE

Never accidentally send real orders in dry-run mode.

============================================================
43. STOP.PY
============================================================

Create:

stop.py

Running:

python stop.py

must request safe shutdown.

Use a lightweight signal file:

STOP

main.py checks it every polling cycle.

Shutdown:

1. Stop new entries.
2. Close strategy positions if configured.
3. Verify positions.
4. Display final PnL.
5. Save state.
6. Exit cleanly.

Do not perform account-wide liquidation by default.

============================================================
44. SCI-FI CLI
============================================================

Create a professional futuristic terminal interface.

Concept:

╔══════════════════════════════════════════════════════════════╗
║        ◈ DELTA // BULLISH CONDOR CORE ◈                   ║
╠══════════════════════════════════════════════════════════════╣
║ SYSTEM       ONLINE       MODE        LIVE                 ║
║ UNDERLYING   BTCUSD       EXPIRY      23-OCT-2026          ║
║ LEVERAGE     100X         STATUS      MONITORING           ║
╠══════════════════════════════════════════════════════════════╣
║ BTCUSD PRICE             78,926.00                         ║
║ STRATEGY PNL                 +8.40 USD                     ║
║ REALIZED PNL                  0.00 USD                     ║
║ UNREALIZED PNL               +8.40 USD                     ║
║ MAX PROFIT                  +14.46 USD                     ║
║ MAX LOSS                     -5.53 USD                     ║
║ REWARD / RISK                   2.61                      ║
╠══════════════════════════════════════════════════════════════╣
║ TP MODE       70% OF MAX PROFIT                            ║
║ TP TARGET         +10.12 USD                               ║
║ TP PROGRESS        +8.40 / +10.12 USD                     ║
║                                                            ║
║ SL MODE       50% OF MAX LOSS                              ║
║ SL TARGET          -2.77 USD                               ║
║ SL DISTANCE         SAFE                                   ║
╠══════════════════════════════════════════════════════════════╣
║ BUY  PUT    77000    ENTRY xxxx    NOW xxxx    PNL xxxx   ║
║ SELL PUT    78000    ENTRY xxxx    NOW xxxx    PNL xxxx   ║
║ SELL CALL   79000    ENTRY xxxx    NOW xxxx    PNL xxxx   ║
║ BUY  CALL   80000    ENTRY xxxx    NOW xxxx    PNL xxxx   ║
╠══════════════════════════════════════════════════════════════╣
║ MARGIN          xxxx USD                                   ║
║ NEXT SCAN       5 seconds                                  ║
║ API             CONNECTED                                  ║
╚══════════════════════════════════════════════════════════════╝

The actual values must come from current/simulated data.

Clearly display:

- mode
- underlying
- expiry
- four legs
- current PnL
- realized PnL
- unrealized PnL
- max profit
- max loss
- reward/risk
- TP target
- SL target
- current distance to TP
- current distance to SL
- margin
- leverage
- strategy state
- next polling time
- exchange connection
- warnings/errors

Keep the interface lightweight.

Provide a plain terminal fallback if rich terminal rendering is unavailable.

============================================================
45. POLLING
============================================================

Default:

5 seconds

Configurable:

monitoring:
  polling_seconds: 5

Every polling cycle should:

- fetch required dynamic data
- evaluate active strategy
- update PnL
- check TP
- check SL
- check expiry
- check stop signal
- check schedule

Avoid unnecessary API requests.

Cache static information:

- products
- expiry
- strikes
- contract specifications

Respect API rate limits.

============================================================
46. API ERROR HANDLING
============================================================

Handle:

- timeout
- HTTP errors
- authentication failure
- rate limit
- malformed responses
- unavailable product
- unavailable expiry
- unavailable strike
- rejected order
- partial fill
- insufficient margin
- network interruption
- stale data
- unexpected shutdown

Use bounded retries and exponential backoff.

Never retry orders indefinitely.

Before retrying an order whose status is unknown:

QUERY ORDER STATUS FIRST.

Do not create duplicate orders.

============================================================
47. AUTHENTICATION
============================================================

Load:

DELTA_API_KEY
DELTA_API_SECRET

from .env.

Never:

- print credentials
- log credentials
- expose credentials in exceptions
- hardcode credentials

============================================================
48. MAIN.PY FUNCTION DESIGN
============================================================

Keep everything in main.py.

Organize into logical sections:

1. imports
2. constants
3. configuration
4. environment
5. logging
6. utilities
7. Delta API client
8. product discovery
9. market data
10. expiry
11. strike selection
12. strategy construction
13. payoff
14. PnL
15. risk
16. execution
17. reconciliation
18. TP/SL
19. schedule
20. stop
21. dashboard
22. shutdown
23. main loop

Every major function must have a docstring explaining:

- purpose
- inputs
- outputs
- when it is called
- important safety considerations

============================================================
49. IMPORTANT FUNCTIONS
============================================================

Implement logically separated functions for:

load_config()
validate_config()
load_environment()
setup_logging()

create_delta_client()
sign_request()
send_request()

validate_exchange_connection()
validate_account()

get_underlying_price()
get_option_products()
get_available_expiries()
get_available_strikes()

select_expiry()
select_manual_strikes()
select_automatic_strikes()

find_option_contract()

build_bullish_condor()

get_leg_market_data()

calculate_net_credit()
calculate_max_profit()
calculate_max_loss()
calculate_breakevens()
calculate_reward_risk()

calculate_combined_strategy_pnl()
calculate_leg_pnl()

calculate_take_profit_target()
calculate_stop_loss_target()

check_take_profit()
check_stop_loss()

validate_entry_conditions()
validate_risk()
validate_margin()
validate_liquidity()

place_leg_order()
monitor_order()

execute_condor_entry()
handle_partial_entry()
recover_partial_entry()

get_positions()
reconcile_positions()

close_strategy_positions()
close_all_strategy_positions()
verify_strategy_closed()

check_day_trading_schedule()
check_bot_schedule()
check_stop_signal()
check_expiry_exit()

save_runtime_state()
load_runtime_state()

render_dashboard()

shutdown_bot()

main()

Use actual Delta API mechanics discovered from the reference files.

============================================================
50. TP/SL FUNCTION DESIGN
============================================================

Create explicit functions:

calculate_take_profit_target(strategy)

calculate_stop_loss_target(strategy)

check_take_profit(strategy_pnl, tp_target)

check_stop_loss(strategy_pnl, sl_target)

These functions must operate on the combined strategy.

Example:

max_profit = +14.46
max_loss = -5.53

TP configuration:

70% of max profit

TP target:

+10.12

SL configuration:

50% of max loss

SL target:

-2.77

Then:

check_take_profit(+10.20, +10.12)
→ TRUE

check_stop_loss(-2.80, -2.77)
→ TRUE

============================================================
51. TP/SL SHOULD BE LOCKED AFTER ENTRY
============================================================

Once all four legs are successfully entered:

- calculate the strategy's actual entry net credit
- calculate maximum profit
- calculate maximum loss
- calculate TP target
- calculate SL target
- store them in runtime strategy state

Do not continuously change TP/SL because market price changes.

TP/SL thresholds should be based on the strategy's established entry/payoff characteristics.

If configuration uses absolute values, use those values.

============================================================
52. TP/SL MUST USE ACTUAL COMBINED PNL
============================================================

Every polling cycle for an active strategy:

1. Fetch current positions.
2. Calculate current PnL for each leg.
3. Sum all four legs.
4. Include realized/unrealized components correctly.
5. Account for available fee information.
6. Produce TOTAL STRATEGY PNL.
7. Compare TOTAL STRATEGY PNL against TP.
8. Compare TOTAL STRATEGY PNL against SL.

The comparison must NOT be based on one leg.

============================================================
53. POSITION CLOSURE AFTER TP/SL
============================================================

When TP or SL triggers:

No new trade.

No additional strategy.

No repeated exit signal.

Set:

exit_trigger = TP or SL

Then:

EXITING

Close all four positions.

After every close order:

verify status.

After all closing orders:

fetch positions again.

Only when:

all strategy quantities == 0

mark:

CLOSED

Then calculate:

FINAL REALIZED PNL

and display it.

============================================================
54. TRADE STATE EXAMPLE
============================================================

Example:

MAX PROFIT:
+14.46 USD

MAX LOSS:
-5.53 USD

TP:
70% of Max Profit

TP:
+10.12 USD

SL:
50% of Max Loss

SL:
-2.77 USD

Current PnL:

+8.00 USD

Status:

MONITORING

Later:

Current PnL:
+10.15 USD

Since:

+10.15 >= +10.12

TP triggers.

Then:

TP TRIGGERED
↓
NEW ENTRIES BLOCKED
↓
EXITING
↓
CLOSE FOUR LEGS
↓
VERIFY FOUR LEGS
↓
FINAL PNL
↓
CLOSED
↓
BOT STOPPED

The final realized PnL may be slightly different from +10.15 because of execution prices, fees, and slippage.

============================================================
55. SL EXAMPLE
============================================================

Example:

MAX LOSS:
-5.53 USD

SL:
50%

SL TARGET:
-2.77 USD

Current strategy PnL:

-2.80 USD

Since:

-2.80 <= -2.77

SL triggers.

Then:

SL TRIGGERED
↓
NEW ENTRIES BLOCKED
↓
EXITING
↓
CLOSE FOUR LEGS
↓
VERIFY
↓
FINAL REALIZED PNL
↓
CLOSED
↓
BOT STOPPED

============================================================
56. ARCHITECTURE.MD
============================================================

Create a comprehensive technical document.

Structure:

# Delta Exchange India Bullish Condor Algo

## 1. Introduction

## 2. Strategy Overview

## 3. Bullish Condor Structure

## 4. Four-Leg Construction

## 5. Example BTCUSD Strategy

## 6. User-Selected Expiry

## 7. Delta Option Product Identification

## 8. Manual Strike Selection

## 9. Automatic Strike Selection

## 10. Net Credit

## 11. Maximum Profit

## 12. Maximum Loss

## 13. Breakeven

## 14. Reward/Risk

## 15. Combined Strategy PnL

## 16. Combined TP Calculation

## 17. Combined SL Calculation

## 18. TP Exit Process

## 19. SL Exit Process

## 20. Four-Leg Execution

## 21. Partial-Fill Protection

## 22. Position Reconciliation

## 23. Entry Conditions

## 24. Risk Management

## 25. Day Trading Mode

## 26. Continuous Trading Mode

## 27. Scheduling

## 28. Main.py Architecture

## 29. Function-by-Function Explanation

## 30. Configuration Reference

## 31. CLI Dashboard

## 32. Complete Trade Lifecycle

## 33. Worked Trade Example

## 34. TP Example

## 35. SL Example

## 36. Forced Exit Example

## 37. Restart/Recovery Example

## 38. Parameter Tuning

## 39. Improving Strategy Quality

## 40. Risk Warnings

## 41. Dry Run

## 42. AWS Deployment

## 43. Installation

## 44. Running the Bot

## 45. Stopping the Bot

## 46. Troubleshooting

## 47. Future Extensions

============================================================
57. COMBINED TP/SL DOCUMENTATION
============================================================

architecture.md must make this concept extremely clear:

The four option contracts are ONE strategy.

Example:

BUY PUT
SELL PUT
SELL CALL
BUY CALL

The strategy has:

ONE combined entry
ONE combined maximum profit
ONE combined maximum loss
ONE combined current PnL
ONE combined TP
ONE combined SL
ONE combined exit

Explain why TP/SL is not calculated separately for individual legs.

Include a table:

| Metric | Meaning |
|---|---|
| Max Profit | Maximum theoretical profit of all four legs combined |
| Max Loss | Maximum theoretical loss of all four legs combined |
| Current PnL | Current combined PnL of all four legs |
| TP | Percentage/absolute target based on combined strategy |
| SL | Percentage/absolute loss threshold based on combined strategy |

============================================================
58. WORKED PAYOFF EXAMPLE
============================================================

Use an illustrative example based on:

BTCUSD:
78,926

BUY PUT:
77,000

SELL PUT:
78,000

SELL CALL:
79,000

BUY CALL:
80,000

Use example numbers similar to the screenshot where appropriate:

Maximum Profit:
+14.46 USD

Maximum Loss:
-5.53 USD

Reward/Risk:
2.61

Breakevens:
approximately 79,723.21
and
77,276.78

IMPORTANT:

Clearly identify these as illustrative/example values unless they are actually calculated from live prices.

Do not hardcode these numbers into the algorithm.

The actual bot must calculate them dynamically from actual execution prices and product specifications.

============================================================
59. PAYOFF SCENARIOS
============================================================

Explain the result when the underlying expires:

1. Below lower PUT strike.
2. Between lower PUT and short PUT.
3. Between short PUT and short CALL.
4. Between short CALL and upper CALL.
5. Above upper CALL strike.

Explain each leg's payoff.

Then explain the combined strategy payoff.

============================================================
60. PARAMETER TUNING
============================================================

architecture.md must explain how to tune:

expiry
strike distance
put wing width
call wing width
minimum credit
maximum credit
minimum reward/risk
TP percentage
SL percentage
absolute TP
absolute SL
quantity
lots
leverage
polling
spread
slippage
daily loss
max strategy loss
entry window
forced exit time

For each parameter explain:

- what it controls
- increasing it
- decreasing it
- potential advantage
- potential disadvantage
- what should be tested

Do not promise profitability.

============================================================
61. PROFITABILITY GUIDANCE
============================================================

Never say:

"this strategy will make profit"

Never guarantee performance.

Instead explain that outcomes depend on:

- option premiums
- volatility
- market movement
- liquidity
- bid/ask spread
- fees
- slippage
- execution quality
- expiry
- strike placement
- position sizing
- risk management
- market regime

Provide practical ways to improve strategy quality:

- select liquid options
- avoid excessive spreads
- require adequate net credit
- maintain acceptable reward/risk
- test strike distances
- test expiries
- use disciplined TP/SL
- control position size
- limit daily loss
- account for fees
- account for slippage
- dry-run first
- paper trade
- backtest
- walk-forward test
- evaluate different market regimes

Explicitly state that none of these guarantees profitability.

============================================================
62. ARCHITECTURE DIAGRAM
============================================================

Include:

CONFIG.YAML
     │
     ▼
MAIN.PY
     │
     ├── CONFIG ENGINE
     │
     ├── DELTA API CLIENT
     │       ├── PRODUCTS
     │       ├── MARKET DATA
     │       ├── ORDERS
     │       └── POSITIONS
     │
     ├── EXPIRY ENGINE
     │
     ├── STRIKE ENGINE
     │
     ├── BULLISH CONDOR ENGINE
     │
     ├── PAYOFF ENGINE
     │
     ├── RISK ENGINE
     │
     ├── EXECUTION ENGINE
     │
     ├── COMBINED PNL ENGINE
     │
     ├── TP/SL ENGINE
     │
     ├── SCHEDULE ENGINE
     │
     └── SCI-FI CLI

And:

MARKET DATA
     ↓
EXPIRY
     ↓
STRIKE SELECTION
     ↓
FOUR-LEG CONSTRUCTION
     ↓
PAYOFF
     ↓
RISK CHECK
     ↓
FOUR-LEG ENTRY
     ↓
POSITION RECONCILIATION
     ↓
COMBINED PNL
     ↓
       ┌───────────────┐
       │               │
       ▼               ▼
      TP              SL
       │               │
       └───────┬───────┘
               ▼
       CLOSE FOUR LEGS
               │
               ▼
        VERIFY POSITIONS
               │
               ▼
          FINAL PNL
               │
               ▼
         BOT STOPPED

============================================================
63. FUNCTION DOCUMENTATION
============================================================

architecture.md must explain every important function actually implemented in main.py.

For each:

### Function Name

Purpose:
Inputs:
Outputs:
When called:
Safety considerations:
Example:

The documentation must exactly match the implementation.

============================================================
64. AWS 1 GB RAM
============================================================

The bot must run comfortably on:

- local Linux
- macOS
- AWS Linux
- approximately 1 GB RAM

Avoid:

- large ML frameworks
- databases
- GUI frameworks
- unnecessary data processing
- unnecessary background workers

Use lightweight dependencies.

============================================================
65. REQUIREMENTS.TXT
============================================================

Only include packages actually required.

Possible lightweight dependencies may include:

PyYAML
python-dotenv
requests

and a lightweight terminal library if genuinely required.

Do not add unnecessary dependencies.

============================================================
66. .ENV
============================================================

Create:

DELTA_API_KEY=
DELTA_API_SECRET=

Use placeholders only.

Never use real credentials.

============================================================
67. STARTUP RECOVERY
============================================================

On startup:

1. Connect to Delta.
2. Retrieve account.
3. Retrieve positions.
4. Retrieve relevant orders.
5. Identify existing strategy.
6. Reconcile.
7. Prevent duplicate strategy.
8. Resume monitoring where possible.

If positions cannot safely be identified:

- do not open another trade
- show warning
- require safe resolution

============================================================
68. SAFE SHUTDOWN
============================================================

Handle:

KeyboardInterrupt
SIGINT
SIGTERM
stop.py
TP
SL
expiry
forced day-trading exit
scheduled close
fatal error

When configured:

close strategy positions
↓
verify
↓
final PnL
↓
save state
↓
exit

============================================================
69. SECURITY
============================================================

Never:

- print API secret
- log API secret
- expose authentication signatures
- hardcode credentials
- store credentials in runtime state
- include credentials in architecture.md

============================================================
70. CODE QUALITY
============================================================

Use:

Python 3.10+

Type hints where useful.

Clear names.

Docstrings.

Useful comments.

Defensive programming.

Structured logging.

Small logical functions.

Readable code.

Do not over-engineer.

Do not create unnecessary abstraction layers.

The entire trading flow should be understandable by reading main.py from top to bottom.

============================================================
71. FINAL VALIDATION
============================================================

Before completing:

1. Inspect all reference files.
2. Understand actual Delta Exchange India API.
3. Implement six requested files.
4. Remove dependency on docs.
5. Validate Python syntax.
6. Validate YAML syntax.
7. Validate imports.
8. Validate .env loading.
9. Validate authentication.
10. Validate underlying.
11. Validate fixed expiry.
12. Validate automatic expiry.
13. Validate manual strikes.
14. Validate automatic strikes.
15. Validate four-leg construction.
16. Validate product IDs.
17. Validate payoff.
18. Validate max profit.
19. Validate max loss.
20. Validate breakevens.
21. Validate reward/risk.
22. Validate combined strategy PnL.
23. Validate combined TP.
24. Validate combined SL.
25. Validate four-leg TP exit.
26. Validate four-leg SL exit.
27. Validate partial entry.
28. Validate partial exit.
29. Validate position reconciliation.
30. Validate duplicate-entry protection.
31. Validate day-trading mode.
32. Validate continuous mode.
33. Validate scheduled startup.
34. Validate scheduled exit.
35. Validate expiry exit.
36. Validate stop.py.
37. Validate graceful shutdown.
38. Validate dry-run.
39. Validate margin.
40. Validate liquidity.
41. Validate daily loss.
42. Validate maximum strategy loss.
43. Validate leverage.
44. Validate configurable polling.
45. Validate CLI.
46. Validate logging.
47. Validate restart recovery.
48. Verify no secret leakage.
49. Run syntax/static validation.
50. Fix all errors.

If live credentials are unavailable:

- do not fake API responses
- do not fake order IDs
- do not claim successful execution
- use dry-run behavior
- clearly state what requires live credentials

============================================================
72. CRITICAL SAFETY RULE
============================================================

NEVER leave an unintended naked option position because one leg failed.

NEVER assume four orders succeeded because four requests were sent.

Always query/verify actual exchange order status.

Always reconcile actual exchange positions.

Never report the strategy as CLOSED until all four intended strategy positions have been verified as closed.

============================================================
73. CRITICAL TP/SL RULE — FINAL
============================================================

The most important strategy-management rule is:

THE BULLISH CONDOR IS ONE COMBINED FOUR-LEG POSITION.

Therefore:

MAX PROFIT = combined maximum profit of all four legs.

MAX LOSS = combined maximum loss of all four legs.

CURRENT PNL = combined PnL of all four legs.

TP = based on combined MAX PROFIT or configured absolute combined PnL.

SL = based on combined MAX LOSS or configured absolute combined PnL.

When TP is reached:

CLOSE ALL FOUR STRATEGY POSITIONS.

When SL is reached:

CLOSE ALL FOUR STRATEGY POSITIONS.

After TP/SL exit:

STOP THE BOT if configured.

Do NOT calculate TP/SL independently for each leg.

============================================================
74. FINAL DOCUMENTATION QUALITY
============================================================

main.py must contain useful comments/docstrings explaining:

- API operations
- strategy construction
- expiry selection
- strike selection
- product discovery
- payoff calculations
- combined PnL
- TP calculation
- SL calculation
- four-leg execution
- partial-fill recovery
- risk management
- position reconciliation
- shutdown

architecture.md must contain:

- strategy explanation
- architecture diagrams
- formulas
- examples
- combined TP/SL explanation
- complete trade lifecycle
- function-by-function explanation
- configuration reference
- parameter tuning
- practical risk management
- AWS deployment
- troubleshooting
- dry-run workflow

The code, comments, examples, formulas, and documentation must remain synchronized with the actual implementation.

Write the implementation in a clean, professional, educational style so the source code and its explanations are suitable for later inclusion in high-quality technical books and educational material.

Do not mention that the project is specifically being created for a book.

Do not promise profits.

Produce REAL WORKING Python code, not pseudocode.

Most importantly, use the supplied Delta Exchange India reference files to implement the REAL Delta Exchange India API behavior and product conventions, and make the final six-file project completely independent of the reference files after implementation.

============================================================
59. PORTING TO BEARISH CONDOR
============================================================

Copy this reference pack into the other repo:

- docs/Delta_SRP.PY
- docs/srp_delta_helper.md
- docs/PRODUCT_ID_DETAILS.md
- docs/project_requirements.md

Keep the six-file layout. Reimplement helpers inside that bot's main.py. Do not import docs/.

The four-leg **credit condor product set does not change**:

BUY lower PUT, SELL higher PUT, SELL lower CALL, BUY higher CALL

Strike order remains:

PUT_LONG < PUT_SHORT < CALL_SHORT < CALL_LONG

Only strike **placement** vs spot changes.

Bullish (body around or slightly above spot), example:

BUY PUT 77000 / SELL PUT 78000 / SELL CALL 79000 / BUY CALL 80000

Bearish (body around or slightly below spot), example at the same ~79000 market:

BUY PUT 76000 / SELL PUT 77000 / SELL CALL 78000 / BUY CALL 79000

Change in config.yaml:

- strategy.name
- strategy.manual_strikes (or ATM-relative distances)
- dashboard / log names
- client_order_id prefix if desired

Reuse unchanged:

- Delta HMAC client and India REST paths
- FIXED_DATE / NEAREST / NEXT_AVAILABLE expiry
- MANUAL exact listed strikes (no ATM snap)
- Sequential market entry, cancel-on-timeout, unwind on partial
- Combined frozen TP/SL and four-leg live PnL
- Restart reconcile: resume if four sizes open; unwind 1-3; clear idle if flat
- stop.py / runtime_state.json / dry_run

Do not invert the four legs into a long/debit condor unless the new spec explicitly asks for that. A bearish **credit** condor is still defined-risk short iron-condor geometry.
```

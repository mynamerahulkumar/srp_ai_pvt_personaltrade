You are GitHub Copilot working inside a Python trading project.

Build a complete, production-oriented but educational **Dhan-only options algo trading system** based on the supplied HalfTrend SENSEX trading reference/screenshot and the requirements below.

The existing uploaded `project_requirements.md` is the baseline reference for this project. Preserve the useful Dhan integration, safety, configuration, execution, risk-management, CLI, and documentation principles from it, but **update and simplify the implementation according to this prompt**. The existing requirements specifically require Dhan-only execution, dynamic security-master resolution, PAPER/LIVE separation, minimal dependencies, AWS 1 GB compatibility, and `config.yaml` as the single source of trading configuration.

The final implementation must be reviewed end-to-end before completion. Do not leave pseudo-code, TODOs, fake API calls, duplicated configuration, unnecessary files, or unnecessary complexity.

---

# 1. PROJECT OBJECTIVE

Create a **Dhan-only Indian index options trading algo** using:

1. **Normal OHLC candles + HalfTrend + optional RSI**
2. **Renko candles + HalfTrend + optional RSI**

The default underlying is:

```text
SENSEX
```

The system must also support:

```text
SENSEX
NIFTY
BANKNIFTY
```

through `config.yaml`.

The bot trades **OPTIONS**, not the underlying index itself.

The underlying index generates the trading signal.

Example:

```text
SENSEX
   ↓
Normal Candle / Renko Candle
   ↓
HalfTrend
   ↓
Optional RSI filter
   ↓
BUY CE / BUY PE
   ↓
Select configured option
   ↓
Monitor position
   ↓
TP / SL / HalfTrend exit / opposite signal / session exit
```

The implementation must support:

```text
LIVE
PAPER
BACKTEST
```

through `config.yaml`.

Default:

```yaml
trading_mode: "PAPER"
```

Never place real orders in PAPER or BACKTEST mode.

---

# 2. IMPORTANT STRATEGY MODES

The system must have exactly two strategy candle modes.

```yaml
candle_mode: "NORMAL"
```

Supported:

```text
NORMAL
RENKO
```

Do not create unnecessary strategy files or abstraction layers.

All strategy logic remains in `main.py`.

---

# 3. NORMAL CANDLE STRATEGY

When:

```yaml
candle_mode: "NORMAL"
```

use normal OHLC candles.

Default timeframe:

```yaml
timeframe_minutes: 5
```

The polling interval is independent from the candle timeframe.

For example:

```yaml
polling_seconds: 5
```

means:

```text
poll every 5 seconds
```

but:

```text
do NOT generate a new signal every 5 seconds.
```

Only a newly confirmed candle can generate a new strategy signal.

---

# 4. NORMAL CANDLE + HALF TREND

Calculate a transparent HalfTrend indicator internally.

Default:

```yaml
halftrend:
  amplitude: 2
  channel_deviation: 2
```

The implementation should use a standard transparent HalfTrend-style calculation based on:

- highest high
- lowest low
- average high
- average low
- trend state
- max low
- min high
- ATR/channel deviation
- HalfTrend line

Do not require TA-Lib.

Use pandas/standard Python calculations.

Do not hide the calculation inside an external technical-analysis package.

Clearly document the exact formula implemented.

If the exact proprietary/internal Dhan HalfTrend implementation cannot be verified, do not claim that it is byte-for-byte identical to Dhan.

Instead state that:

```text
The implementation is a transparent standard HalfTrend-style calculation designed to reproduce the supplied chart behavior.
```

---

# 5. NORMAL CANDLE ENTRY LOGIC

Default bullish rule:

```text
Confirmed candle closes ABOVE HalfTrend
        +
RSI filter passes, if enabled
        ↓
BUY CE
```

Default bearish rule:

```text
Confirmed candle closes BELOW HalfTrend
        +
RSI filter passes, if enabled
        ↓
BUY PE
```

Use configurable RSI.

Example:

```yaml
rsi:
  enabled: true
  period: 14
  bullish_min: 50
  bearish_max: 50
```

If:

```yaml
enabled: false
```

RSI must not participate in the entry decision.

The same strategy must work correctly with RSI enabled or disabled.

Do not duplicate RSI logic.

---

# 6. RSI LOGIC

RSI must be calculated internally using pandas/Python.

Do not require TA-Lib.

For bullish signals:

```text
HalfTrend bullish
AND
RSI >= bullish_min
```

For bearish signals:

```text
HalfTrend bearish
AND
RSI <= bearish_max
```

Make the thresholds configurable.

Example:

```yaml
rsi:
  enabled: true
  period: 14
  bullish_min: 50
  bearish_max: 50
```

The CLI must show:

```text
RSI: 57.42
RSI FILTER: PASS
```

or:

```text
RSI FILTER: DISABLED
```

Do not generate a trade if RSI is enabled and its condition fails.

---

# 7. NORMAL CANDLE SIDEWAYS MARKET FILTER

A major requirement is avoiding trades when HalfTrend indicates a sideways market.

If the HalfTrend line is effectively flat/straight for a configurable number of confirmed candles, classify the market as:

```text
SIDEWAYS
```

and do not enter.

Add a minimal configuration:

```yaml
halftrend:
  amplitude: 2
  channel_deviation: 2
  sideways_filter_enabled: true
  sideways_lookback: 3
  sideways_tolerance_points: 5
```

The exact implementation must be sensible and deterministic.

For example, compare the HalfTrend line across the configured lookback:

```text
max(HalfTrend) - min(HalfTrend)
```

If the movement is within the configured tolerance:

```text
SIDEWAYS
```

Do not enter.

Do not use a complicated machine-learning classification.

The CLI should clearly show:

```text
MARKET STATE : SIDEWAYS
ENTRY        : BLOCKED
REASON       : HALF TREND FLAT
```

This filter applies to the Normal Candle strategy.

It may also be used for Renko if configured, but do not duplicate logic unnecessarily.

---

# 8. RENKO STRATEGY

When:

```yaml
candle_mode: "RENKO"
```

the strategy must use Renko candles generated from the underlying index price.

Default underlying:

```text
SENSEX
```

The Renko brick size must be configurable.

Example:

```yaml
renko:
  brick_size_points: 50
  entry_confirmation_bricks: 1
  exit_confirmation_bricks: 2
```

The user must be able to change:

```text
brick size
entry confirmation
exit confirmation
```

Do not hardcode a Renko brick size.

---

# 9. RENKO ENTRY RULE

Default Renko behavior:

### Bullish

A newly completed bullish Renko brick closes above the HalfTrend line.

If:

```text
bullish Renko brick
+
close > HalfTrend
+
RSI passes if enabled
+
market is not sideways
```

then:

```text
BUY CE
```

### Bearish

A newly completed bearish Renko brick closes below HalfTrend.

If:

```text
bearish Renko brick
+
close < HalfTrend
+
RSI passes if enabled
+
market is not sideways
```

then:

```text
BUY PE
```

---

# 10. RENKO ENTRY CONFIRMATION

Support configurable confirmation count.

Default:

```yaml
entry_confirmation_bricks: 1
```

Meaning:

```text
1 completed Renko brick above HalfTrend
```

is sufficient for entry.

If configured:

```yaml
entry_confirmation_bricks: 2
```

then two consecutive qualifying Renko bricks are required.

Do not enter repeatedly from the same Renko brick.

Track the Renko brick timestamp/index/state.

---

# 11. RENKO EXIT RULE

The default requirement is:

```text
Exit when TWO consecutive completed Renko candles/bricks close below HalfTrend.
```

for a bullish/CE position.

For a bearish/PE position:

```text
Exit when TWO consecutive completed Renko candles/bricks close above HalfTrend.
```

The default must be:

```yaml
exit_confirmation_bricks: 2
```

Make this configurable.

Example:

```text
LONG CE
   ↓
Renko closes below HalfTrend
   ↓
count = 1
   ↓
Renko closes below HalfTrend again
   ↓
count = 2
   ↓
EXIT CE
```

For PE:

```text
LONG PE
   ↓
Renko closes above HalfTrend
   ↓
count = 1
   ↓
Renko closes above HalfTrend again
   ↓
count = 2
   ↓
EXIT PE
```

Do not exit based on an incomplete Renko brick.

---

# 12. RSI FOR RENKO

RSI must also be independently configurable for Renko.

The simplest implementation should allow:

```yaml
rsi:
  enabled: true
```

to apply to whichever candle mode is selected.

Do not create unnecessary duplicate configuration.

RSI can be:

```text
ENABLED
DISABLED
```

If disabled:

```text
RSI must not block entries.
```

The CLI must clearly show the state.

---

# 13. EXACT PREMIUM SELECTION

The user must be able to select a specific option premium to trade.

This is especially important for SENSEX.

Support:

```text
ATM
ITM
OTM
PREMIUM
```

Example:

```yaml
option:
  selection_mode: "ATM"
```

Supported:

```text
ATM
ITM
OTM
PREMIUM
```

If:

```yaml
selection_mode: "PREMIUM"
target_premium: 100
```

the bot should select the appropriate CE/PE option whose current premium is closest to the configured target premium, subject to a configurable tolerance.

Example:

```yaml
option:
  selection_mode: "PREMIUM"
  target_premium: 100
  premium_tolerance: 20
```

This means the system should search the valid option chain and select the appropriate option near ₹100 premium.

Do not assume that an exact ₹100 option always exists.

If no option falls within the configured tolerance:

```text
do not trade
```

Display:

```text
NO PREMIUM MATCH
TARGET: ₹100
TOLERANCE: ₹20
```

For ATM/ITM/OTM, use the option chain/security master dynamically.

Do not hardcode strikes.

---

# 14. OPTION SELECTION

Support:

```text
SENSEX
NIFTY
BANKNIFTY
```

Signal mapping:

```text
Bullish → CE
Bearish → PE
```

The bot buys options only.

Do not implement:

- option selling
- multi-leg strategies
- spreads
- straddles
- strangles
- futures
- equity trading

unless explicitly required later.

---

# 15. EXPIRY SELECTION

Support:

```yaml
expiry_mode: "NEAREST"
```

and:

```yaml
expiry_mode: "CONFIGURED"
expiry: "YYYY-MM-DD"
```

NEAREST:

```text
Use nearest valid expiry returned by Dhan.
```

CONFIGURED:

```text
Use exact configured expiry.
```

If expiry does not exist:

```text
fail safely
do not trade
```

Do not hardcode expiry dates.

---

# 16. SECURITY MASTER

Use:

```text
api-scrip-master.csv
```

to dynamically resolve:

- security ID
- trading symbol
- custom symbol
- exchange segment
- instrument
- expiry
- strike
- option type
- lot size
- tick size

Do not hardcode:

```text
SENSEX security ID
NIFTY security ID
BANKNIFTY security ID
option security IDs
strike IDs
lot sizes
```

Load the security master once and keep only the required information in memory.

Do not reload the complete CSV every 5 seconds.

If the file does not exist, use an appropriate Dhan security-master mechanism if supported by the supplied Dhan reference.

If current SDK behavior cannot be safely determined:

```text
fail clearly
do not invent an API
```

---

# 17. TP / SL MUST USE SENSEX MOVEMENT POINTS

The primary TP/SL requirement is based on **underlying index movement**, not option premium percentage.

Example:

```text
SENSEX entry/reference = 82,000
TP points = 100
SL points = 50
```

Bullish CE:

```text
Entry/reference SENSEX = 82,000

TP = 82,100
SL = 81,950
```

Bearish PE:

```text
Entry/reference SENSEX = 82,000

TP = 81,900
SL = 82,050
```

Make this configurable.

Example:

```yaml
risk:
  tp_sl_mode: "UNDERLYING_POINTS"

  take_profit:
    enabled: true
    points: 100

  stop_loss:
    enabled: true
    points: 50
```

The TP/SL should trigger based on the configured underlying movement.

Do not confuse:

```text
SENSEX points
```

with:

```text
option premium points
```

The CLI must display both where useful:

```text
SENSEX ENTRY : 82,000
SENSEX NOW   : 82,075
TP           : 82,100
SL           : 81,950
```

Also show the option premium and P&L.

---

# 18. OPTIONAL OPTION-PREMIUM TP/SL

If useful for implementation flexibility, support an optional second mode:

```yaml
tp_sl_mode:
  UNDERLYING_POINTS
  OPTION_PREMIUM_POINTS
```

Do not expose complicated formulas.

Default must remain:

```text
UNDERLYING_POINTS
```

If:

```text
OPTION_PREMIUM_POINTS
```

is selected, calculate TP/SL using option entry premium.

Document both modes clearly.

---

# 19. TP/SL BOT STOP BEHAVIOR

When TP is reached:

```text
close current algo position
calculate P&L
display result
stop bot if configured
```

When SL is reached:

```text
close current algo position
calculate P&L
display result
stop bot if configured
```

Add one simple setting:

```yaml
risk:
  stop_bot_after_tp_sl: true
```

Default:

```text
true
```

Do not immediately re-enter after TP/SL.

---

# 20. COMBINED P&L

The system must track the combined P&L of all trades placed by this algo.

Track:

```text
realized P&L
+
current unrealized P&L
=
combined algo P&L
```

Use INR.

Example:

```yaml
max_loss_per_day_inr: 500
```

If combined algo P&L reaches:

```text
-₹500
```

or lower:

```text
stop new entries
close active algo position
verify LIVE exit
stop bot
```

Do not use the user's entire Dhan account P&L.

Only track positions/orders attributable to this algo.

Use an order tag/correlation identifier where Dhan supports it.

---

# 21. DAILY RISK CONFIGURATION

Keep only important risk settings:

```yaml
risk:
  tp_sl_mode: "UNDERLYING_POINTS"

  take_profit:
    enabled: true
    points: 100

  stop_loss:
    enabled: true
    points: 50

  stop_bot_after_tp_sl: true

  max_open_strategies: 1
  max_orders_per_day: 10
  max_loss_per_day_inr: 500

  no_reentry_after_stop_loss: true
  no_reentry_after_max_loss: true

  exit_on_opposite_signal: true
```

Do not create dozens of risk parameters.

---

# 22. MAX OPEN STRATEGIES

Default:

```yaml
max_open_strategies: 1
```

Only one active option position at a time.

Do not pyramid.

Do not create duplicate orders.

States may include:

```text
FLAT
ENTRY_PENDING
LONG_CE
LONG_PE
EXIT_PENDING
STOPPING
```

If an order is pending:

```text
do not submit another entry.
```

---

# 23. MAX ORDERS PER DAY

Default:

```yaml
max_orders_per_day: 10
```

When reached:

```text
no new entries
```

but existing positions continue to be managed.

TP/SL and exits remain active.

---

# 24. NO RE-ENTRY AFTER STOP LOSS

Default:

```yaml
no_reentry_after_stop_loss: true
```

After SL:

```text
close position
mark SL lock
prevent new entries
```

If:

```yaml
no_reentry_after_stop_loss: false
```

the normal strategy rules may continue, subject to all other risk controls.

---

# 25. NO RE-ENTRY AFTER DAILY MAX LOSS

Default:

```yaml
no_reentry_after_max_loss: true
```

Once maximum daily loss is reached:

```text
close active algo position
stop new entries
stop bot
```

---

# 26. OPPOSITE SIGNAL

Support:

```yaml
exit_on_opposite_signal: true
```

Example:

```text
Current position = CE

New confirmed bearish signal
        ↓
Close CE
```

and:

```text
Current position = PE

New confirmed bullish signal
        ↓
Close PE
```

Do not automatically reverse in the same polling cycle.

Exit first.

---

# 27. TRADING MODES

The bot must support exactly:

```text
PAPER
LIVE
BACKTEST
```

through:

```yaml
trading_mode: "PAPER"
```

---

# 28. PAPER MODE

PAPER mode must NEVER call a live Dhan order-placement API.

It should simulate:

```text
entry
fill
position
TP
SL
exit
P&L
```

Use the available market price/premium as simulated fill.

Use the exact same strategy logic as LIVE.

Only execution behavior changes.

Clearly display:

```text
PAPER MODE
```

at all times.

---

# 29. LIVE MODE

LIVE mode may place actual Dhan orders.

Before every live entry:

1. Validate credentials.
2. Validate security ID.
3. Validate option contract.
4. Validate expiry.
5. Validate strike.
6. Validate lot size.
7. Validate quantity.
8. Validate order type.
9. Display order preview.
10. Submit order.
11. Capture order ID.
12. Poll order status.
13. Confirm actual TRADED/fill status.
14. Determine actual fill price.
15. Create internal position state only after successful execution.

Never assume:

```text
API request succeeded = order filled
```

---

# 30. BACKTEST MODE

Add a genuine historical-data backtesting mode.

Example:

```yaml
trading_mode: "BACKTEST"

backtest:
  start_date: "2026-01-01"
  end_date: "2026-09-30"
```

The backtest must use historical underlying data to reconstruct:

```text
Normal candles
OR
Renko candles
HalfTrend
RSI
signals
entries
exits
TP
SL
```

Do not use future candles.

Avoid look-ahead bias.

A completed candle can only use information available at that candle's close.

---

# 31. BACKTEST OPTION EXECUTION

For options backtesting, use historical option data if Dhan provides the required historical contract/premium data through the available SDK/API.

The backtest should resolve:

```text
underlying
expiry
strike
CE/PE
security ID
historical premium
```

as far as historical data is actually available.

If exact historical option premium data is unavailable:

```text
DO NOT fabricate it.
```

Clearly report:

```text
Historical option premium data is unavailable for this backtest configuration.
```

and either:

```text
stop the backtest safely
```

or perform only an explicitly labeled underlying-signal backtest.

Do not pretend an underlying-only result is an actual options P&L backtest.

---

# 32. BACKTEST OUTPUT

At the end of a backtest display:

```text
BACKTEST COMPLETE

Strategy:
Candle Mode:
Underlying:
Period:

Total Trades:
Winning Trades:
Losing Trades:
Win Rate:
Gross P&L:
Average Trade:
Largest Win:
Largest Loss:
Max Drawdown:
```

Clearly label results as:

```text
SIMULATED / HISTORICAL
```

Do not claim future profitability.

---

# 33. BACKTEST TP/SL

The backtest must apply the same TP/SL logic used by live/paper mode.

For:

```text
UNDERLYING_POINTS
```

use the underlying historical movement.

For:

```text
OPTION_PREMIUM_POINTS
```

use historical option premium.

Document any limitation caused by candle-level historical data.

Do not assume the exact intrabar sequence when both TP and SL could have occurred inside the same candle.

If necessary, use a conservative deterministic rule and document it.

---

# 34. HISTORICAL DATA

Do not download unlimited historical data into memory.

Load only the requested backtest range.

For live/paper:

```text
keep only the minimum recent candles required for the indicators.
```

For backtest:

```text
process data efficiently and release unnecessary objects.
```

The application must remain usable on an AWS Linux VM with approximately 1 GB RAM.

---

# 35. RUN MODES / SESSION

Support:

```yaml
run_mode: "CONTINUOUS"
```

or:

```yaml
run_mode: "SCHEDULED"
```

CONTINUOUS:

```text
start immediately
continue while main.py is running
```

SCHEDULED:

```text
only trade between configured start_time and stop_time
```

Use:

```yaml
timezone: "Asia/Kolkata"
```

Default.

Example:

```yaml
run_mode: "SCHEDULED"
start_time: "09:20"
stop_time: "15:20"
```

---

# 36. STOP TIME

Add:

```yaml
close_all_positions_at_stop: true
stop_bot_after_close: true
```

These have different meanings.

`close_all_positions_at_stop`:

```text
close all positions belonging to THIS algo.
```

`stop_bot_after_close`:

```text
exit the Python process.
```

Do not confuse these settings.

If stop time is reached:

1. Stop new entries.
2. Close algo positions if configured.
3. Verify LIVE closure.
4. Calculate final P&L.
5. Display final summary.
6. Exit if configured.

---

# 37. MARKET CLOSED BEHAVIOR

When market is closed:

```text
NEVER place a new order.
```

But continue to fetch/display available information when the API permits.

Display:

```text
market status
last underlying price
latest option premium
latest HalfTrend
latest RSI
latest candle
position
P&L
```

Do not manufacture fresh candles after market close.

Do not treat stale prices as live signals.

---

# 38. POST-MARKET POLLING

After market/session closure:

```yaml
post_close_polls: 3
post_close_poll_seconds: 10
```

Default behavior:

```text
3 polls × 10 seconds ≈ 30 seconds
```

During these polls:

```text
MARKET CLOSED
```

and show:

```text
poll count
underlying price
option premium
HalfTrend
RSI
position
P&L
```

Do not enter new trades.

After the configured polls:

```text
final position check
close if configured
final P&L
shutdown if configured
```

---

# 39. POLLING

Default:

```yaml
polling_seconds: 5
```

Every polling cycle should:

1. Check manual stop.
2. Check current time.
3. Check market/session status.
4. Fetch current underlying price.
5. Fetch active option premium.
6. Calculate current P&L.
7. Check TP.
8. Check SL.
9. Check max daily loss.
10. Check position state.
11. Refresh candle data only when required.
12. Calculate indicators.
13. Detect new confirmed candle/Renko brick.
14. Generate signal.
15. Validate entry.
16. Select option.
17. Place paper/live order if appropriate.
18. Render dashboard.
19. Sleep.

Do not make expensive Dhan API calls unnecessarily every 5 seconds.

---

# 40. NORMAL CANDLE DATA REFRESH

If timeframe is:

```text
5 minutes
```

polling can still be:

```text
5 seconds
```

but candle history should only refresh when required.

Track:

```text
last_confirmed_candle_timestamp
```

Do not repeatedly trade from the same candle.

---

# 41. RENKO CONSTRUCTION

Implement Renko generation transparently inside `main.py`.

Do not require an external Renko library.

Use the configured:

```yaml
brick_size_points
```

Build completed Renko bricks only.

Do not treat every price poll as a completed Renko brick.

The Renko engine must handle:

- bullish bricks
- bearish bricks
- multiple bricks created during a large price movement
- continuation bricks
- reversal bricks
- timestamps
- open/close levels

Document the Renko construction algorithm in `architecture.md`.

The algorithm must be deterministic.

Avoid look-ahead bias.

---

# 42. RENKO + HALFTREND

Calculate HalfTrend using the Renko OHLC series when:

```yaml
candle_mode: "RENKO"
```

This must be explicitly documented.

Do not accidentally calculate HalfTrend on normal candles and then compare it against Renko candles unless that behavior is intentionally configured.

Default behavior:

```text
Normal mode:
Normal OHLC → HalfTrend

Renko mode:
Renko OHLC → HalfTrend
```

---

# 43. SIDEWAYS FILTER FOR RENKO

Use the same configurable HalfTrend flatness concept when appropriate.

If HalfTrend remains effectively flat:

```text
SIDEWAYS
```

and:

```text
do not enter
```

Show:

```text
MARKET STATE: SIDEWAYS
```

---

# 44. OPTION PREMIUM MONITORING

When a position exists:

Every polling cycle:

```text
fetch option LTP
calculate unrealized P&L
calculate TP/SL
display premium
display position
```

Do not repeatedly fetch the complete option chain.

Use quote/ticker data for the selected security ID.

If option LTP is temporarily unavailable:

```text
DATA ERROR
```

Do not assume TP/SL was reached.

Do not submit duplicate exit orders.

---

# 45. DATA STALENESS

Track:

```text
underlying data timestamp
option price timestamp
candle timestamp
```

If data is stale:

```text
DATA STALE
```

Do not generate a new entry from stale data.

Distinguish:

```text
MARKET CLOSED
DATA ERROR
DATA STALE
```

---

# 46. LIVE ORDER SAFETY

Before every LIVE order show:

```text
ACTION
UNDERLYING
OPTION SYMBOL
SECURITY ID
EXPIRY
STRIKE
CE/PE
QUANTITY
LOTS
LOT SIZE
ORDER TYPE
PRICE
CURRENT OPTION LTP
SENSEX LTP
TP
SL
REASON
```

Then submit.

Capture:

```text
order_id
```

Check order lifecycle.

If:

```text
PENDING
```

do not submit another order.

If:

```text
REJECTED
CANCELLED
```

reset safely and display reason.

---

# 47. POSITION IDENTIFICATION

Never close unrelated Dhan positions.

"Close all positions" means:

```text
close all positions belonging to THIS ALGO.
```

Use where available:

- order tag
- correlation ID
- security ID
- internal state
- order history
- trade history

If the bot cannot confidently identify a position as belonging to this algo:

```text
do not blindly close it.
```

---

# 48. RESTART SAFETY

When starting in LIVE mode:

1. Query Dhan positions.
2. Detect relevant existing positions.
3. Do not blindly open another position.
4. Display:

```text
EXISTING LIVE POSITION DETECTED
```

5. Reconcile only when attribution is reliable.

If attribution is uncertain:

```text
do not trade automatically.
```

---

# 49. CLI — SCI-FI STYLE

Create a lightweight sci-fi terminal dashboard using standard ANSI terminal formatting.

Do not use:

- Streamlit
- web UI
- Rich
- Colorama
- curses
- GUI

Keep it lightweight for AWS 1 GB RAM.

Example:

```text
╔════════════════════════════════════════════════════════════╗
║             SRP HALF TREND OPTIONS ENGINE                 ║
╠════════════════════════════════════════════════════════════╣
║ MODE        : PAPER                                        ║
║ STRATEGY    : NORMAL + HALFTREND + RSI                    ║
║ UNDERLYING  : SENSEX                                      ║
║ MARKET      : OPEN                                        ║
╠════════════════════════════════════════════════════════════╣
║ SENSEX LTP  : 82,075.00                                   ║
║ CANDLE      : 5M                                           ║
║ CANDLE TIME : 10:25:00                                     ║
║ HALF TREND  : 82,020.00                                    ║
║ RSI         : 57.42                                        ║
║ RSI FILTER  : PASS                                         ║
║ MARKET STATE: TRENDING                                     ║
║ SIGNAL      : BULLISH                                      ║
╠════════════════════════════════════════════════════════════╣
║ OPTION      : SENSEX 82500 CE                              ║
║ PREMIUM     : ₹102.50                                      ║
║ POSITION    : LONG CE                                      ║
║ QUANTITY    : 20                                           ║
║ ENTRY       : ₹95.00                                       ║
║ CURRENT P&L : ₹150.00                                      ║
╠════════════════════════════════════════════════════════════╣
║ SENSEX ENTRY: 82,000                                       ║
║ SENSEX NOW  : 82,075                                       ║
║ TP          : 82,100                                       ║
║ SL          : 81,950                                       ║
╠════════════════════════════════════════════════════════════╣
║ TODAY P&L   : ₹150.00                                      ║
║ ORDERS      : 2 / 10                                       ║
║ DAILY LIMIT : -₹500                                        ║
║ POLLING     : 5 SEC                                        ║
║ NEXT POLL   : 4 SEC                                        ║
║ LAST ACTION : BUY CE                                       ║
╚════════════════════════════════════════════════════════════╝
```

For Renko:

```text
STRATEGY     : RENKO + HALFTREND + RSI
RENKO BRICK  : 50 POINTS
BRICK STATE  : BULLISH
HT POSITION  : ABOVE
ENTRY CONFIRM: 1 / 1
EXIT CONFIRM : 0 / 2
```

The CLI must clearly tell the user:

```text
why a trade was taken
```

or:

```text
why a trade was blocked
```

Examples:

```text
ENTRY BLOCKED: HALF TREND SIDEWAYS
ENTRY BLOCKED: RSI FILTER FAILED
ENTRY BLOCKED: MAX ORDERS REACHED
ENTRY BLOCKED: DAILY LOSS LIMIT
ENTRY BLOCKED: NO PREMIUM MATCH
ENTRY BLOCKED: NO CONFIRMED CANDLE
SIGNAL: BUY CE
```

---

# 50. STARTUP DISPLAY

At startup display:

```text
BOT NAME
VERSION
TRADING MODE
CANDLE MODE
UNDERLYING
TIMEFRAME / RENKO BRICK
HALFTREND PARAMETERS
RSI STATUS
OPTION SELECTION
EXPIRY
STRIKE/PREMIUM
LOTS
ACTUAL QUANTITY
TP
SL
MAX LOSS
MAX ORDERS
RUN MODE
SESSION
TIMEZONE
POLLING
```

If LIVE:

```text
!!! WARNING: LIVE TRADING ENABLED !!!
```

must be highly visible.

---

# 51. MINIMAL CONFIG.YAML

Create a clean, trader-friendly `config.yaml`.

Do not overwhelm the user.

Use only parameters that a trader realistically needs to change.

Suggested final structure:

```yaml
# ============================================================
# TRADING MODE
# ============================================================

trading_mode: "PAPER"          # PAPER | LIVE | BACKTEST

# ============================================================
# MARKET
# ============================================================

underlying: "SENSEX"           # SENSEX | NIFTY | BANKNIFTY

candle_mode: "NORMAL"          # NORMAL | RENKO

timeframe_minutes: 5           # Used by NORMAL mode

polling_seconds: 5             # How often the bot checks the market

# ============================================================
# STRATEGY
# ============================================================

halftrend:
  amplitude: 2
  channel_deviation: 2

  sideways_filter_enabled: true
  sideways_lookback: 3
  sideways_tolerance_points: 5

rsi:
  enabled: true
  period: 14
  bullish_min: 50
  bearish_max: 50

# ============================================================
# RENKO
# ============================================================

renko:
  brick_size_points: 50
  entry_confirmation_bricks: 1
  exit_confirmation_bricks: 2

# ============================================================
# OPTIONS
# ============================================================

option:
  expiry_mode: "NEAREST"       # NEAREST | CONFIGURED
  expiry: ""                   # YYYY-MM-DD if CONFIGURED

  selection_mode: "ATM"        # ATM | ITM | OTM | PREMIUM

  strike_offset: 0             # Used by ITM/OTM selection

  target_premium: 100          # Used only when selection_mode=PREMIUM
  premium_tolerance: 20        # Maximum acceptable premium difference

  lots: 1

# ============================================================
# ORDER EXECUTION
# ============================================================

execution:
  order_type: "LIMIT"          # LIMIT | MARKET
  product_type: "INTRADAY"

# ============================================================
# RISK
# ============================================================

risk:
  tp_sl_mode: "UNDERLYING_POINTS"
  # UNDERLYING_POINTS | OPTION_PREMIUM_POINTS

  take_profit:
    enabled: true
    points: 100

  stop_loss:
    enabled: true
    points: 50

  stop_bot_after_tp_sl: true

  max_open_strategies: 1
  max_orders_per_day: 10

  max_loss_per_day_inr: 500

  no_reentry_after_stop_loss: true
  no_reentry_after_max_loss: true

  exit_on_opposite_signal: true

# ============================================================
# SESSION
# ============================================================

timezone: "Asia/Kolkata"

run_mode: "SCHEDULED"           # CONTINUOUS | SCHEDULED

start_time: "09:20"
stop_time: "15:20"

close_all_positions_at_stop: true
stop_bot_after_close: true

# ============================================================
# MARKET-CLOSE MONITORING
# ============================================================

post_close_polls: 3
post_close_poll_seconds: 10

# ============================================================
# BACKTEST
# ============================================================

backtest:
  start_date: "2026-01-01"
  end_date: "2026-09-30"
```

You may improve the exact nesting if it makes the code cleaner, but:

```text
KEEP IT MINIMAL.
```

Do not add unnecessary configuration.

---

# 52. CONFIG.YAML SINGLE SOURCE OF TRUTH

This is mandatory.

If a trading parameter exists in `config.yaml`:

```text
main.py must read it from config.yaml.
```

Do not duplicate it as a Python configuration constant.

Do not create:

```text
engine.yaml
```

Do not create:

```text
strategy.yaml
risk.yaml
broker.yaml
```

Do not put trading parameters in `.env`.

`.env` is only for credentials.

The configuration in `config.yaml` is final.

---

# 53. ONLY TWO CONFIGURATION SOURCES

Runtime/user configuration sources must be:

```text
1. config.yaml
2. .env
```

`.env`:

```text
credentials only
```

`config.yaml`:

```text
all trading configuration
```

`env.example`:

```text
credential template only
```

No other configuration file is allowed.

---

# 54. REQUIRED FILES

Create only:

```text
main.py
config.yaml
.env
env.example
stop.py
requirements.txt
architecture.md
```

The user may already have:

```text
api-scrip-master.csv
```

Do not create a separate Python module for it.

Do not create:

```text
strategy.py
dhan.py
broker.py
engine.py
engine.yaml
risk.py
options.py
utils.py
database.py
logger.py
```

All application logic remains in:

```text
main.py
```

---

# 55. REFERENCE FILES

The user may provide:

```text
docs/project_requirements.md
docs/Dhan_SRP.py
```

and possibly other Dhan helper/reference files.

Treat them as:

```text
REFERENCE ONLY
```

Inspect them before implementation.

Do not import them.

The final project must work if those reference files are deleted.

Reimplement only the required Dhan patterns inside `main.py`.

Use the current installed DhanHQ SDK conventions.

Do not blindly copy obsolete code.

---

# 56. DHAN AUTHENTICATION

Use the current DhanHQ SDK pattern from the supplied reference.

Expected environment variables:

```text
DHAN_CLIENT_ID
DHAN_ACCESS_TOKEN
```

Use:

```python
from dotenv import load_dotenv
```

Never hardcode credentials.

Never print credentials.

For LIVE mode:

```text
missing credentials = fail safely
```

For PAPER/BACKTEST, do not unnecessarily require live order credentials.

`env.example`:

```text
DHAN_CLIENT_ID=
DHAN_ACCESS_TOKEN=
```

Add comments explaining what the user needs to enter.

---

# 57. DYNAMIC LOT SIZE

The configuration should contain:

```yaml
lots: 1
```

not an absolute hardcoded quantity.

Calculate:

```text
actual quantity = lots × security-master lot size
```

Validate that the quantity is legal for the selected option.

Display:

```text
LOTS: 1
LOT SIZE: 20
QUANTITY: 20
```

Do not hardcode SENSEX/NIFTY/BANKNIFTY lot sizes.

---

# 58. FUNCTION ORGANIZATION IN MAIN.PY

All important functionality must be implemented as readable functions.

At minimum, create functions similar to:

```text
load_config()
validate_config()
load_environment()
create_dhan_client()
load_security_master()
resolve_underlying()
fetch_expiry_list()
fetch_option_chain()
select_option_contract()
fetch_underlying_data()
fetch_option_ltp()

calculate_atr()
calculate_half_trend()
calculate_rsi()

build_renko()
generate_normal_signal()
generate_renko_signal()
detect_sideways_market()

calculate_tp_sl()
calculate_position_pnl()
calculate_combined_algo_pnl()

validate_entry()
place_entry_order()
check_order_status()
close_current_position()

check_take_profit()
check_stop_loss()
check_opposite_signal()
check_daily_loss()
check_order_limit()

check_trading_session()
check_market_closed()
check_manual_stop()

paper_enter()
paper_exit()
live_enter()
live_exit()

run_backtest()
run_live_or_paper()

render_dashboard()
graceful_shutdown()

main()
```

Add functions only when genuinely necessary.

Do not create artificial functions simply to increase the function count.

---

# 59. FUNCTION DOCUMENTATION

Every important function must have a useful docstring.

Each docstring should explain:

```text
Purpose
Inputs
Outputs
Why it exists
Trading considerations
```

Example:

```python
def calculate_half_trend(...):
    """
    Calculate the transparent HalfTrend indicator.

    Purpose:
        Determine the trend direction and HalfTrend line used by
        the entry/exit strategy.

    Inputs:
        OHLC data and configured HalfTrend parameters.

    Outputs:
        Indicator values and trend state.

    Trading use:
        A confirmed candle closing above HalfTrend can create a
        bullish signal; a confirmed candle closing below HalfTrend
        can create a bearish signal.

    Important:
        The calculation must not use future candle information.
    """
```

Comments should explain **WHY**, not merely repeat what the code does.

---

# 60. MAIN.PY STRUCTURE

Organize `main.py` into clear sections:

```text
IMPORTS

CONFIGURATION

DATA CLASSES / STATE

DHAN CONNECTION

SECURITY MASTER

MARKET DATA

OPTION SELECTION

HALF TREND

RSI

RENKO ENGINE

STRATEGY SIGNALS

PAPER EXECUTION

LIVE EXECUTION

BACKTEST ENGINE

POSITION MANAGEMENT

RISK MANAGEMENT

SESSION / MARKET STATUS

CLI DASHBOARD

SHUTDOWN

MAIN LOOP
```

Keep the code sequential and readable.

---

# 61. STOP.PY

Create:

```text
stop.py
```

Running:

```bash
python stop.py
```

must create:

```text
.bot_stop
```

Main bot checks for this file every polling cycle.

`stop.py` must:

- not require Dhan credentials
- not import `main.py`
- not initialize Dhan
- not place orders

When the stop signal is detected:

```text
stop new entries
close current algo position if configured
verify LIVE closure
show final P&L
exit gracefully
```

Use a simple local stop-file mechanism.

Do not create complex process-management code.

---

# 62. GRACEFUL SHUTDOWN

Create one central:

```python
graceful_shutdown(reason)
```

Use it for:

```text
TP
SL
MAX DAILY LOSS
SESSION STOP
MANUAL STOP
MARKET CLOSE
CTRL+C
CRITICAL ERROR
```

Avoid duplicate shutdown logic.

---

# 63. CENTRAL EXIT FUNCTION

Create one central:

```python
close_current_position(reason)
```

It must:

1. Find current algo position.
2. Determine security ID.
3. Determine quantity.
4. Determine exit transaction.
5. Submit LIVE exit if LIVE.
6. Simulate exit if PAPER.
7. Verify LIVE execution.
8. Calculate realized P&L.
9. Update combined P&L.
10. Record reason.
11. Update state.
12. Prevent unintended immediate re-entry.

Use it for:

```text
TP
SL
opposite signal
session stop
manual stop
daily loss
shutdown
Renko HalfTrend exit
```

---

# 64. DAILY STATE

Maintain in-memory state:

```text
orders_today
trades_today
realized_pnl
unrealized_pnl
combined_pnl
current_position
last_signal
last_signal_candle
last_renko_brick
last_option_contract
stop_loss_hit
max_loss_hit
session_stop_reached
```

No database.

Use:

```text
Asia/Kolkata
```

for trading date.

Reset daily counters when a new trading day starts.

---

# 65. ERROR HANDLING

Safely handle:

```text
API timeout
rate limit
invalid response
empty option chain
missing LTP
order rejection
order pending
order cancellation
invalid security ID
missing credentials
market closed
stale data
invalid configuration
historical data unavailable
```

Temporary failure:

```text
display
retry
do not duplicate order
```

Critical failure:

```text
safely close position if possible
stop bot
display reason
```

Never silently swallow exceptions.

---

# 66. RATE LIMIT SAFETY

Do not call every API every 5 seconds just because polling is 5 seconds.

Use caching/state.

Security master:

```text
load once
```

Underlying candles:

```text
refresh only when necessary
```

Underlying LTP:

```text
polling cycle
```

Active option LTP:

```text
polling cycle
```

Option chain:

```text
only when selecting/reselecting an option
```

Order status:

```text
only while order is pending
```

Never create duplicate orders because an API request is slow.

---

# 67. REQUIREMENTS.TXT

Keep dependencies minimal.

At minimum:

```text
dhanhq
pandas
PyYAML
python-dotenv
```

Do not use:

```text
TA-Lib
```

Do not add unnecessary packages.

Avoid adding:

```text
rich
colorama
blessed
curses
```

for CLI.

Use standard ANSI formatting.

Use Python 3.10+.

---

# 68. AWS 1 GB RAM

The system must run efficiently on:

```text
AWS Linux
approximately 1 GB RAM
```

Do not use:

```text
database
Redis
ML
GUI
browser automation
Docker
Kubernetes
large frameworks
web server
```

Keep memory usage low.

Do not store unlimited historical candles.

Only keep the data required for:

```text
HalfTrend
RSI
Renko
current strategy state
```

---

# 69. BACKTEST VS PAPER VS LIVE ARCHITECTURE

All three modes must use the same:

```text
indicator logic
signal logic
risk logic
TP/SL logic
option-selection logic where data permits
```

Only data/execution differs.

Conceptually:

```text
                 ┌───────────────┐
                 │   Strategy    │
                 │ HalfTrend RSI │
                 │ Normal/Renko  │
                 └───────┬───────┘
                         │
             ┌───────────┼───────────┐
             ↓           ↓           ↓
          PAPER        LIVE      BACKTEST
             ↓           ↓           ↓
         Simulator      Dhan      Historical
```

Do not duplicate strategy logic between modes.

---

# 70. ARCHITECTURE.MD

Create a detailed `architecture.md`.

It must describe the actual implementation and must be updated to match the final code.

Do not document features that are not implemented.

Use this structure:

```text
# Dhan HalfTrend + RSI Options Algo

## 1. Overview

## 2. Project Structure

## 3. Configuration

## 4. Strategy Modes

## 5. Normal Candle Strategy

## 6. Renko Strategy

## 7. HalfTrend Calculation

## 8. RSI Calculation

## 9. Sideways Market Detection

## 10. Signal Generation

## 11. Option Selection

## 12. Premium Selection

## 13. Expiry Selection

## 14. TP/SL

## 15. Combined P&L

## 16. Risk Management

## 17. PAPER Mode

## 18. LIVE Mode

## 19. BACKTEST Mode

## 20. Dhan Integration

## 21. Security Master

## 22. Polling Architecture

## 23. Market Closed Behavior

## 24. Renko Construction

## 25. Position Management

## 26. Stop Mechanism

## 27. Function-by-Function Explanation

## 28. Complete Trade Lifecycle

## 29. Example Normal Candle Trade

## 30. Example Renko Trade

## 31. Example TP/SL

## 32. Parameter Tuning

## 33. Risk Management

## 34. Backtesting Methodology

## 35. AWS 1 GB Deployment

## 36. Troubleshooting

## 37. Final Implementation Notes
```

---

# 71. ARCHITECTURE — STRATEGY EXPLANATION

Explain Normal Candle:

```text
SENSEX
 ↓
5-minute OHLC
 ↓
HalfTrend
 ↓
RSI filter
 ↓
Sideways filter
 ↓
Confirmed candle
 ↓
BUY CE / BUY PE
```

Explain Renko:

```text
SENSEX price
 ↓
Renko bricks
 ↓
HalfTrend on Renko
 ↓
RSI
 ↓
Sideways filter
 ↓
1+ confirmed brick
 ↓
BUY CE / BUY PE
```

---

# 72. ARCHITECTURE — RENKO EXIT EXAMPLE

Include an example:

```text
Position:
LONG CE

Renko Brick 1:
Close below HalfTrend
→ Exit confirmation = 1/2
→ HOLD

Renko Brick 2:
Close below HalfTrend
→ Exit confirmation = 2/2
→ EXIT CE
```

For PE:

```text
Position:
LONG PE

Renko Brick 1:
Close above HalfTrend
→ 1/2

Renko Brick 2:
Close above HalfTrend
→ 2/2

→ EXIT PE
```

---

# 73. ARCHITECTURE — NORMAL CANDLE EXAMPLE

Example:

```text
SENSEX = 82,000

5-minute candle closes:
82,075

HalfTrend:
82,020

RSI:
57

RSI minimum:
50

HalfTrend:
Bullish

Market state:
Trending

Signal:
BUY CE
```

Then:

```text
Select configured expiry
Select configured option
Resolve security ID
Determine lot size
Calculate quantity
Place PAPER/LIVE entry
Monitor
```

---

# 74. ARCHITECTURE — TP/SL EXAMPLE

Example:

```text
SENSEX entry/reference = 82,000
TP = 100 points
SL = 50 points
```

Bullish:

```text
TP = 82,100
SL = 81,950
```

If SENSEX reaches:

```text
82,100
```

then:

```text
EXIT
```

If SENSEX reaches:

```text
81,950
```

then:

```text
EXIT
```

For bearish PE:

```text
TP = 81,900
SL = 82,050
```

Explain this clearly.

---

# 75. ARCHITECTURE — P&L EXAMPLE

Example option:

```text
Entry premium = ₹100
Quantity = 20
Exit premium = ₹120
```

Gross P&L:

```text
(120 - 100) × 20
= ₹400
```

Loss example:

```text
(90 - 100) × 20
= -₹200
```

Clearly state:

```text
This is gross P&L.
Brokerage, STT, GST, exchange charges, slippage and other costs can change actual net P&L.
```

Do not promise profitability.

---

# 76. PARAMETER TUNING

Architecture documentation must explain how traders can test:

```text
HalfTrend amplitude
HalfTrend channel deviation
RSI period
RSI threshold
timeframe
Renko brick size
Renko entry confirmation
Renko exit confirmation
sideways lookback
sideways tolerance
expiry
ATM/ITM/OTM
premium target
TP points
SL points
polling interval
quantity
```

Explain trade-offs.

Example:

```text
Smaller HalfTrend amplitude
→ more responsive
→ potentially more signals/noise

Larger amplitude
→ slower
→ potentially fewer signals

Smaller Renko brick
→ more sensitive
→ more signals/noise

Larger Renko brick
→ smoother
→ fewer signals

Higher RSI threshold
→ stricter bullish confirmation

Lower RSI threshold
→ more permissive bullish confirmation

Larger TP
→ larger target
→ may require stronger movement

Smaller TP
→ quicker exits
→ may reduce average winning-trade size

Wider SL
→ more room
→ larger potential loss

Tighter SL
→ smaller potential loss
→ potentially more stop-outs
```

Never say a parameter guarantees profit.

---

# 77. RESPONSIBLE BACKTESTING

Explain that traders should evaluate:

```text
historical backtests
paper trading
forward testing
different market regimes
transaction costs
slippage
liquidity
option spread
gap risk
```

Do not optimize parameters solely on one historical period.

Explain overfitting simply.

---

# 78. FUNCTION-BY-FUNCTION ARCHITECTURE

For every major function actually implemented in `main.py`, explain:

```text
Purpose
Inputs
Outputs
How it works
Where it is called
Failure behavior
```

The architecture document should be detailed enough that a reader can follow the complete program from startup to shutdown.

---

# 79. COMPLETE TRADE LIFECYCLE

Document:

```text
START
 ↓
Load config
 ↓
Validate config
 ↓
Load environment
 ↓
Initialize Dhan
 ↓
Load security master
 ↓
Resolve underlying
 ↓
Load market data
 ↓
Build Normal/Renko candles
 ↓
Calculate HalfTrend
 ↓
Calculate RSI
 ↓
Detect sideways market
 ↓
Wait for confirmed candle/brick
 ↓
Generate signal
 ↓
Select option
 ↓
Validate lot size
 ↓
Preview order
 ↓
PAPER/LIVE entry
 ↓
Confirm execution
 ↓
Monitor position
 ↓
Check TP
 ↓
Check SL
 ↓
Check HalfTrend exit
 ↓
Check opposite signal
 ↓
Check daily loss
 ↓
Exit
 ↓
Calculate P&L
 ↓
Continue or shutdown
```

---

# 80. CODE QUALITY

The code must be:

```text
simple
readable
deterministic
well-commented
type-hinted where useful
safe
Dhan-specific
lightweight
```

Avoid:

```text
clever one-liners
unnecessary abstraction
overengineering
duplicate logic
unused imports
unused configuration
unused functions
dead code
```

---

# 81. FINAL CONFIG REVIEW

After generating the code:

Review every setting in `config.yaml`.

For every configuration value verify:

```text
Is it actually used?
Is it needed by the user?
Is it documented?
Is it read by main.py?
Is it duplicated somewhere else?
```

If not needed:

```text
REMOVE IT.
```

The goal is:

```text
minimum configuration
maximum clarity
```

---

# 82. FINAL CODE REVIEW

Before declaring the project complete, inspect the complete implementation.

Check specifically for:

```text
syntax errors
missing imports
undefined functions
incorrect Dhan SDK usage
incorrect security-master handling
incorrect option selection
incorrect premium selection
incorrect lot size
incorrect HalfTrend
incorrect RSI
incorrect Renko construction
look-ahead bias
duplicate entries
duplicate exits
PAPER/LIVE leakage
incorrect TP/SL
incorrect P&L
incorrect daily loss logic
incorrect session logic
timezone errors
market-close errors
stale-data trading
backtest errors
memory issues
AWS 1 GB compatibility
```

Fix every issue found.

---

# 83. PAPER/LIVE SAFETY CHECK

Perform a final explicit review proving:

```text
PAPER mode cannot call a live order-placement function.
```

```text
BACKTEST mode cannot place live orders.
```

```text
Only LIVE mode can submit actual Dhan orders.
```

Do not rely only on comments.

The code structure itself must enforce this.

---

# 84. TP/SL SAFETY CHECK

Verify:

```text
TP can only trigger once.
SL can only trigger once.
No duplicate exit orders.
After TP/SL, bot stops if configured.
No immediate re-entry.
```

---

# 85. MARKET-CLOSE SAFETY CHECK

Verify:

```text
No new order after market close.
Latest available data can still be displayed.
Configured post-close polls execute.
Position closure happens according to configuration.
Final P&L is displayed.
Bot exits if configured.
```

---

# 86. SIDEWAYS SAFETY CHECK

Verify:

```text
HalfTrend flat
+
sideways_filter_enabled=true
=
NO ENTRY
```

The CLI must display the reason.

---

# 87. RENKO SAFETY CHECK

Verify:

```text
No signal from incomplete Renko brick.
No duplicate signal from same brick.
Entry confirmation count works.
Exit confirmation count works.
HalfTrend is calculated correctly on Renko data.
RSI works when enabled.
RSI does not affect entries when disabled.
```

---

# 88. BACKTEST SAFETY CHECK

Verify:

```text
No future data used.
No future option premium used.
No look-ahead bias.
Entry occurs only after signal confirmation.
TP/SL follows configured logic.
Results are deterministic.
Unavailable historical option data is not fabricated.
```

---

# 89. FINAL PROJECT FILES

The final project must contain:

```text
main.py
config.yaml
.env
env.example
stop.py
requirements.txt
architecture.md
```

Only these application files.

Do not create `engine.yaml`.

Do not create additional strategy modules.

---

# 90. FINAL EDUCATIONAL QUALITY

Although this is a real trading application, write the implementation in a clear, structured and educational manner.

Every important calculation should be understandable from `main.py`.

Comments and docstrings should explain the purpose and reasoning behind the code.

`architecture.md` must explain the complete system with:

```text
strategy explanation
mathematical formulas
architecture
function explanations
trade examples
P&L examples
Renko examples
TP/SL examples
parameter tuning
risk management
backtesting
deployment
troubleshooting
```

Write the code, comments, function explanations, architecture, examples, and technical explanations clearly enough that they are useful as high-quality technical educational material, including material that can later be reused in books.

Do not claim the strategy is profitable.

Do not promise returns.

Explain that actual performance depends on market conditions, execution, liquidity, costs, slippage, and parameter selection.

---

# 91. FINAL INSTRUCTION TO COPILOT

NOW IMPLEMENT THE COMPLETE PROJECT.

First inspect the supplied reference files.

Use the existing `project_requirements.md` as the baseline, but update it according to this prompt.

Create:

```text
main.py
config.yaml
.env
env.example
stop.py
requirements.txt
architecture.md
```

Implement:

```text
Dhan-only
SENSEX default
NIFTY configurable
BANKNIFTY configurable

Normal Candle + HalfTrend + optional RSI

Renko + HalfTrend + optional RSI

HalfTrend sideways filter

Configurable Renko brick size

Configurable Renko entry confirmation

Default Renko exit:
2 consecutive bricks on the opposite side of HalfTrend

Configurable TP/SL based on underlying index points

Optional option-premium TP/SL

ATM / ITM / OTM option selection

Specific option-premium selection

Nearest/configured expiry

Dynamic security-master resolution

Dynamic lot-size resolution

PAPER
LIVE
BACKTEST

Combined algo P&L

Maximum daily loss

Maximum orders per day

No re-entry after SL

No re-entry after maximum daily loss

Session scheduling

Continuous mode

Market-close monitoring

Post-close polling

Manual stop.py

Sci-fi lightweight CLI

AWS 1 GB compatibility
```

Use:

```text
config.yaml
```

as the single source of truth for every trading parameter.

Use:

```text
.env
```

only for credentials.

Do not create:

```text
engine.yaml
```

Do not create unnecessary files.

Do not import the reference files.

Do not leave pseudo-code.

Do not leave TODOs in critical trading logic.

Do not invent Dhan APIs.

Use the actual current DhanHQ SDK methods supported by the supplied reference/current installed package.

If an API behavior cannot safely be verified, isolate it and fail clearly instead of inventing behavior.

Finally:

1. Review every generated file.
2. Review every configuration setting.
3. Remove unnecessary code and configuration.
4. Check all imports.
5. Check all function calls.
6. Check all strategy paths.
7. Check Normal Candle mode.
8. Check Renko mode.
9. Check RSI enabled/disabled.
10. Check sideways filter.
11. Check TP/SL.
12. Check premium selection.
13. Check option selection.
14. Check security-master resolution.
15. Check PAPER/LIVE/BACKTEST separation.
16. Check duplicate-order prevention.
17. Check P&L.
18. Check daily loss.
19. Check session handling.
20. Check market-close behavior.
21. Check manual stop.
22. Check AWS 1 GB memory usage.
23. Check architecture.md against the actual code.

Fix everything you find.

The final result must be a clean, minimal, understandable, genuinely runnable Dhan options trading project.
# Dhan HalfTrend + RSI Options Algo

## 1. Overview

This bot buys index options on Dhan. It does not buy or sell the index itself.

The index chart supplies the signal. A confirmed Normal candle or a completed Renko brick, HalfTrend, an optional RSI filter, and an optional sideways filter choose the option side:

- close above HalfTrend (and filters pass): buy a call (CE)
- close below HalfTrend (and filters pass): buy a put (PE)

The default index is SENSEX. NIFTY and BANKNIFTY are configurable in `config.yaml`. SENSEX options use BSE F&O. NIFTY and BANKNIFTY options use NSE F&O. Index candles and the option chain use Dhan segment `IDX_I`.

Trading modes:

- `PAPER` — same strategy as LIVE, simulated fills, never calls `place_order`
- `LIVE` — real Dhan orders after a printed preview
- `BACKTEST` — historical replay with Dhan minute and rolling-option data, then exit

`config.yaml` is the only trading configuration. `.env` holds only credentials. Default mode is PAPER.

Take profit and stop loss default to **underlying index points**, not option-premium percent. Combined gross P&L is this algo’s closed trades plus the open option. It is not the whole Dhan account.

There is no database, web UI, second broker, or extra strategy module. Application logic lives in `main.py`.

Actual performance depends on market conditions, execution, liquidity, costs, slippage, and parameter selection. This document does not claim the strategy is profitable.

## 2. Project Structure

```text
main.py              All bot logic
config.yaml          Trading settings only
.env                 DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN
env.example          Credential template
stop.py              Creates .bot_stop for graceful exit
requirements.txt     dhanhq, pandas, PyYAML, python-dotenv
architecture.md      This file
api-scrip-master.csv Optional local security master (downloaded if missing)
docs/                Reference only; not imported at runtime
```

## 3. Configuration

Every trading parameter is read from `config.yaml`. Nothing is duplicated as a Python constant for trader settings.

Important groups:

| Group | Role |
|-------|------|
| `trading_mode` | PAPER / LIVE / BACKTEST |
| `candle_mode` | NORMAL or RENKO |
| `halftrend` | Amplitude, channel, sideways filter |
| `rsi` | Optional entry filter |
| `renko` | Brick size and confirmation counts |
| `option` | Expiry, ATM/ITM/OTM/PREMIUM, lots |
| `risk` | TP/SL mode and points, daily limits |
| `backtest` | Historical date range |

Dropped from the older schema: `holding_mode`, `signal_confirmation`, premium `PERCENT`/`ABSOLUTE`, and `session.enabled`. `run_mode: SCHEDULED` with `start_time` / `stop_time` replaces the session switch. `product_type` remains the Dhan product (`INTRADAY` or `MARGIN`).

## 4. Strategy Modes

Exactly two candle modes:

```text
NORMAL → confirmed OHLC → HalfTrend (+ RSI) → signal
RENKO  → 1-minute closes → Renko bricks → HalfTrend on bricks (+ RSI) → signal
```

HalfTrend in Renko mode is calculated on Renko OHLC, not on normal candles.

## 5. Normal Candle Strategy

Default timeframe is 5 minutes. Polling (default 5 seconds) updates prices and risk. A new strategy decision waits for a **new confirmed candle**.

Bullish: confirmed close above HalfTrend, RSI passes if enabled, market not sideways → BUY CE.

Bearish: confirmed close below HalfTrend, RSI passes if enabled, market not sideways → BUY PE.

One bar is evaluated once. An open position blocks another entry. Staying above the line does not spam orders every poll.

## 6. Renko Strategy

Renko bricks are built from confirmed 1-minute underlying closes. A poll does not create a brick.

Entry needs `entry_confirmation_bricks` consecutive bricks that are in the signal direction and on the correct side of HalfTrend (default 1).

Exit (when `exit_on_opposite_signal` is true) needs `exit_confirmation_bricks` consecutive completed bricks closing on the wrong side of HalfTrend (default 2). Incomplete bricks do not count. The trailing count resets when a brick does not qualify.

Exit happens first. The same cycle does not reverse into a new option.

## 7. HalfTrend Calculation

The implementation is a transparent Everget-style HalfTrend calculation designed to reproduce the supplied chart behavior. It is **not** claimed to be byte-for-byte identical to Dhan’s private indicator.

For each confirmed bar `i`, using only bars at or before `i`:

1. True range = max(high−low, |high−prev close|, |low−prev close|)
2. ATR = Wilder average of true range, length 100 (`ATR_PERIOD` in `main.py`)
3. `deviation = channel_deviation * (ATR / 2)`
4. Over the last `amplitude` bars: high/low extremes and simple high/low averages
5. Trend 0 tracks a rising max-low; trend 1 tracks a falling min-high
6. The line flips when averages and close break the tracked extreme
7. Upper/lower channel = line ± deviation (stored for inspection; entry uses the line)

## 8. RSI Calculation

Wilder RSI on the same series as HalfTrend (normal closes or Renko closes). No TA-Lib.

When `rsi.enabled` is true:

- Bullish needs `RSI >= bullish_min`
- Bearish needs `RSI <= bearish_max`

When disabled, RSI does not participate. The CLI shows `RSI FILTER: PASS`, `FAIL`, or `DISABLED`.

## 9. Sideways Market Detection

When `sideways_filter_enabled` is true:

```text
max(HalfTrend) - min(HalfTrend) over sideways_lookback bars
```

If that range is within `sideways_tolerance_points`, the market is SIDEWAYS and new entries are blocked. The CLI shows `MARKET STATE : SIDEWAYS` and `ENTRY : BLOCKED`. The same test is used for Normal and Renko.

## 10. Signal Generation

```text
Underlying price
  → NORMAL confirmed OHLC  or  RENKO completed bricks
  → HalfTrend (+ RSI)
  → Sideways filter
  → BUY CE / BUY PE
  → Select option
  → Paper / Live / Backtest fill
  → Monitor TP / SL / opposite / session / daily loss
```

## 11. Option Selection

Bullish → CE. Bearish → PE. Long options only (no selling, spreads, or futures).

Modes: ATM, ITM, OTM (strike steps from the live chain), or PREMIUM.

Quantity = `lots ×` security-master lot size. Lot sizes are never hardcoded for SENSEX/NIFTY/BANKNIFTY.

## 12. Premium Selection

When `selection_mode: PREMIUM`:

- Search the chain for the requested CE/PE
- Pick the premium closest to `target_premium` within `premium_tolerance`
- If none match: print `NO PREMIUM MATCH` and do not trade

## 13. Expiry Selection

- `NEAREST` — next valid Dhan expiry on or after today
- `CONFIGURED` — exact `YYYY-MM-DD`; missing expiry fails safely

## 14. TP/SL

Default `tp_sl_mode: UNDERLYING_POINTS`.

Example CE at index 82,000 with TP 100 / SL 50:

```text
TP = 82,100
SL = 81,950
```

PE at the same reference:

```text
TP = 81,900
SL = 82,050
```

Optional `OPTION_PREMIUM_POINTS` adds/subtracts points from the long option premium.

When `stop_bot_after_tp_sl` is true, the process exits after a completed TP or SL. There is no same-cycle re-entry.

## 15. Combined P&L

```text
combined = realized gross + unrealized gross
```

Only this algo’s tagged positions. Daily max loss uses that combined figure. Displayed P&L excludes brokerage, STT, GST, and slippage.

## 16. Risk Management

- One open option (`max_open_strategies: 1`)
- Daily entry cap (`max_orders_per_day`)
- Daily loss floor (`max_loss_per_day_inr`)
- Optional no re-entry after SL or after max loss
- Opposite-signal exit (Normal: one opposite close; Renko: confirmation bricks)
- Session stop and market-close handling close **this algo’s** position only

## 17. PAPER Mode

Uses live market data for signals and marks. Simulates entry, fill, TP, SL, exit, and P&L. Never calls Dhan order APIs. Dashboard always shows PAPER.

## 18. LIVE Mode

Validates credentials and contract, prints a full order preview, submits, polls status, and creates internal position state only after TRADED. Pending orders block duplicates. Startup reconciles open HT-tagged positions.

## 19. BACKTEST Mode

Loads `backtest.start_date` … `end_date` via `intraday_minute_data` (chunked). Renko uses 1-minute closes. Option P&L uses `expired_options_data` (rolling option) for ATM/ITM/OTM strike codes.

Hard stops without inventing premium:

- `PREMIUM` selection
- calendar `CONFIGURED` expiry
- empty or failed rolling-option history

Message used:

```text
Historical option premium data is unavailable for this backtest configuration.
```

Same-bar TP and SL: stop loss is assumed hit (candle data cannot order the two touches). Results are labeled `SIMULATED / HISTORICAL`.

The SDK documents minute history as recent trading days. If Dhan returns less than the requested range, the report states what was actually received.

## 20. Dhan Integration

Current SDK pattern: `DhanContext` + `dhanhq`. Methods used include `intraday_minute_data`, `ticker_data` / `ohlc_data`, `expiry_list`, `option_chain`, `place_order`, `get_order_by_id`, `get_positions`, `cancel_order`, `expired_options_data`, and security-list fetch when the local CSV is missing.

Reference files under `docs/` are not imported.

## 21. Security Master

`api-scrip-master.csv` resolves security id, symbol, segment, expiry, strike, option type, lot size, and tick size. Loaded once and kept in memory. Index security ids and option lot sizes are not hardcoded.

## 22. Polling Architecture

Each poll: manual stop → clock → session/market → quotes → P&L → TP/SL → daily loss → position state → refresh candles/Renko only when due → indicators → new bar signal → validate → order if allowed → dashboard → sleep.

Quote APIs are paced (~1/sec). History is not fetched every 5 seconds.

## 23. Market Closed Behavior

No new orders when the exchange is closed. The bot may still display last prices and indicators. After close, `post_close_polls` × `post_close_poll_seconds` observe, then close/shutdown if configured. Stale prices do not create live signals.

## 24. Renko Construction

Classic rules on closes:

- Continuation: one brick of `brick_size_points`
- Reversal: two bricks
- Large jumps emit multiple bricks in one step
- Each brick stores open, high, low, close, and source timestamp

Deterministic. No look-ahead. Incomplete bricks are never signals.

## 25. Position Management

States include FLAT, ENTRY_PENDING, LONG_OPTION, EXIT_PENDING, STOPPING. Order tags use an `HT` prefix. Unrelated Dhan positions are never closed.

## 26. Stop Mechanism

`python stop.py` writes `.bot_stop`. The next poll runs graceful shutdown. Ctrl+C uses the same path. Pending entries are cancelled; pending exits are not duplicated.

## 27. Function-by-Function Explanation

| Function | Purpose |
|----------|---------|
| `load_config` / `validate_config` | Single YAML source; reject illegal settings |
| `create_dhan_client` | Authenticated SDK client |
| `load_security_master` / `resolve_underlying` | Dynamic ids and symbols |
| `fetch_underlying_candles` | Confirmed OHLC or 1-minute series for Renko |
| `build_renko` | Completed bricks from closes |
| `calculate_half_trend` / `calculate_rsi` | Indicators without TA-Lib |
| `detect_sideways_market` | Flat HalfTrend entry block |
| `generate_normal_signal` / `generate_renko_signal` | Direction from confirmed bars |
| `renko_exit_ready` | Opposite-side brick confirmation |
| `select_option_contract` / `_select_by_premium` | ATM/ITM/OTM/PREMIUM |
| `calculate_tp_sl` | Index or premium point levels |
| `paper_enter` / `paper_exit` | Simulated execution |
| `live_enter` / `live_exit` | Real orders with status polling |
| `run_backtest` | Historical replay; no orders |
| `render_dashboard` | Sci-fi CLI status |
| `graceful_shutdown` | One exit path for all stop reasons |

## 28. Complete Trade Lifecycle

1. Startup loads config and credentials, resolves underlying and expiry
2. Poll updates index and option marks
3. On a new confirmed bar, indicators and filters run
4. Entry selects contract and quantity, then paper or live fill
5. Position stores option premium and underlying entry
6. Each poll checks TP/SL, daily loss, opposite signal, session
7. Exit records gross P&L; optional process stop after TP/SL

## 29. Example Normal Candle Trade

```text
SENSEX = 82,000
5-minute close = 82,075
HalfTrend = 82,020
RSI = 57 (>= 50)
Market state = Trending
→ BUY CE
→ Nearest expiry, ATM call, lot from master
→ PAPER/LIVE entry
→ Monitor index TP/SL and premium P&L
```

## 30. Example Renko Trade

```text
LONG CE
Brick 1 close below HalfTrend → exit confirm 1/2 → HOLD
Brick 2 close below HalfTrend → exit confirm 2/2 → EXIT CE
```

## 31. Example TP/SL

Index mode CE: entry 82,000, TP 100, SL 50 → exit at 82,100 or 81,950.

Premium P&L example: entry ₹100, exit ₹120, quantity 20 → gross ₹400. Loss exit ₹90 → gross −₹200. Charges are not included.

## 32. Parameter Tuning

| Change | Typical effect |
|--------|----------------|
| Smaller HalfTrend amplitude | More responsive, more noise |
| Larger amplitude | Slower, fewer signals |
| Smaller Renko brick | More bricks/signals |
| Larger Renko brick | Smoother, fewer signals |
| Higher RSI bullish_min | Stricter calls |
| Larger TP points | Needs a bigger index move |
| Tighter SL points | Smaller loss, more stop-outs |

No parameter guarantees profit. Do not optimize only on one historical window (overfitting).

## 33. Risk Management

Prefer paper and forward tests before LIVE. Respect daily loss, order caps, and session stops. LIVE needs a static IP for order APIs and an active data plan for quotes/history/chain.

## 34. Backtesting Methodology

Evaluate history, paper, forward tests, costs, spreads, gaps, and liquidity. BACKTEST uses real Dhan history only. When option history cannot be verified, the run stops instead of fabricating P&L.

## 35. AWS 1 GB Deployment

Keep dependencies minimal (`requirements.txt`). Hold only recent candles in PAPER/LIVE. Backtest chunks history and releases large frames after the report. Avoid loading unlimited history into memory.

## 36. Troubleshooting

| Symptom | Check |
|---------|-------|
| Startup fails on candles/quotes | Data plan, token, DH-902 / 806 |
| LIVE order rejected | Static IP whitelist, segment, product |
| NO PREMIUM MATCH | Widen tolerance or change target |
| Sideways always blocking | Raise tolerance or disable filter |
| Backtest no premium | ATM/ITM/OTM + NEAREST only; check date range |
| Manual stop ignored | Confirm `.bot_stop` beside `main.py` |

## 37. Final Implementation Notes

- All strategy paths stay in `main.py`
- PAPER and BACKTEST never place live orders
- HalfTrend is a transparent standard-style formula, not a Dhan clone claim
- Gross P&L is educational and operational, not net of charges
- Switch `trading_mode` to LIVE only after reviewing `config.yaml`

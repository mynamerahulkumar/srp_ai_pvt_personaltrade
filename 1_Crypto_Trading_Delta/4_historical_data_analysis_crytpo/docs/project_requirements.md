# Cursor Master Prompt: Reusable Delta Exchange Historical Dataset + Configurable Algo Strategy Backtesting

## 1. Project objective

Extend the existing SRP Algo Trading project to support historical market data downloading, reusable CSV datasets, strategy parameter configuration, and historical backtesting.

The application must support two independent capabilities:

1. **Historical Data Engine:** Fetch Delta Exchange India historical OHLCV candles and save them to CSV.
2. **Strategy Backtesting Engine:** Allow any existing or future algorithmic trading strategy to consume those CSV datasets, change strategy parameters, simulate trades, and evaluate performance without placing real orders.

Use these reference files only to understand the existing Delta Exchange India API and product information:

- `docs/Delta_SRP.PY`
- `docs/srp_delta_helper.md`
- `docs/PRODUCT_ID_DETAILS.md`

Inspect the existing repository and all relevant strategy files before implementation.

These reference files may be deleted after the implementation. Do not create runtime dependencies on them.

Do not unnecessarily rewrite, remove, or break the existing live trading functionality.

## 2. Required architecture

Design the application so the data downloader and strategy engine work independently.

```text
                    config.yaml
                        |
                        v
          Historical Data Downloader
                        |
                        v
             Delta Exchange India API
                        |
                        v
              Historical OHLCV CSV
                        |
                        v
             Dataset Validation Layer
                        |
                        v
               Strategy Backtester
                        |
              +---------+---------+
              |                   |
              v                   v
        Strategy Config      Historical Candles
              |                   |
              +---------+---------+
                        |
                        v
               Simulated Execution
                        |
                        v
             Performance Analytics
                        |
              +---------+---------+
              |                   |
              v                   v
         Trade History       Results CSV/JSON
```

The architecture must make it easy to add a new strategy without rewriting the downloader, CSV loader, or execution simulator.

## 3. Configuration requirements

Create or extend `config.yaml` without removing unrelated existing settings.

Separate data configuration, strategy configuration, backtesting configuration, and execution mode.

Example configuration:

```yaml
exchange:
  name: "delta_exchange_india"
  base_url: "VERIFIED_INDIA_API_BASE_URL"

historical_data:
  symbols:
    - "BTCUSD"
    - "ETHUSD"
    - "SOLUSD"

  interval: "5m"

  start_date: "2025-01-01"
  end_date: "2025-12-31"

  timezone: "Asia/Kolkata"
  output_directory: "data/historical"

  separate_file_per_symbol: true
  include_volume: true
  deduplicate: true
  validate_ohlc: true

dataset:
  input_directory: "data/historical"
  timestamp_column: "timestamp"
  datetime_column: "datetime"
  symbol_column: "symbol"
  interval_column: "interval"

  allow_missing_candles: true
  report_missing_candles: true
  reject_duplicate_timestamps: true
  sort_by_timestamp: true

backtesting:
  enabled: true
  mode: "historical"

  initial_capital: 100000
  position_size_mode: "fixed_quantity"
  position_size: 1

  commission_percent: 0.05
  slippage_percent: 0.02

  allow_long: true
  allow_short: true

  calculate_metrics: true
  export_trade_history: true
  export_equity_curve: true

  output_directory: "data/backtest_results"

strategy:
  name: "ema_rsi"

  symbol: "BTCUSD"
  interval: "5m"

  parameters:
    fast_ema_period: 9
    slow_ema_period: 21
    rsi_period: 14
    rsi_buy_threshold: 55
    rsi_sell_threshold: 45

  risk_management:
    stop_loss_percent: 1.0
    take_profit_percent: 2.0
    max_open_positions: 1

execution:
  mode: "backtest"
  allow_live_orders: false
```

This is a configuration example, not a requirement to overwrite the current project's configuration blindly. Merge it carefully with the actual project structure.

All example commission, slippage, and strategy values must be configurable. Document that these are illustrative assumptions and must be calibrated to the actual exchange fees and execution conditions.

The application must not silently ignore configuration keys.

## 4. Reusable historical dataset format

Use a consistent CSV format across all supported strategies.

Example:

```csv
timestamp,datetime,date,time,symbol,interval,open,high,low,close,volume
1735689600000,2025-01-01 05:30:00,2025-01-01,05:30:00,BTCUSD,5m,95000,95100,94900,95050,12.5
```

The values above are illustrative and must not be treated as actual historical market data.

Requirements:

- Use real candles retrieved from the verified Delta Exchange India API.
- Store one row per candle.
- Include the candle opening timestamp.
- Keep the timestamp unit consistent and documented.
- Normalize timezone handling consistently.
- Sort candles chronologically.
- Validate OHLC relationships.
- Deduplicate by symbol, interval, and timestamp.
- Detect missing intervals without inventing candles.
- Preserve actual OHLCV values.
- Keep the schema stable for all strategies.

Example datasets:

```text
data/
├── historical/
│   ├── BTCUSD_1m_2025-01-01_2025-12-31.csv
│   ├── BTCUSD_5m_2025-01-01_2025-12-31.csv
│   ├── BTCUSD_1h_2025-01-01_2025-12-31.csv
│   ├── ETHUSD_5m_2025-01-01_2025-12-31.csv
│   └── SOLUSD_5m_2025-01-01_2025-12-31.csv
└── backtest_results/
```

The strategy engine must read datasets from disk. It must not download historical candles separately for each strategy when a valid local dataset already exists.

## 5. Generic dataset loader

Create a reusable dataset loader independent of any specific trading strategy.

Suggested module:

`src/backtesting/dataset_loader.py`

Responsibilities:

- Load the configured CSV file.
- Support symbol-specific and timeframe-specific datasets.
- Validate required columns.
- Parse timestamps correctly.
- Convert timestamps to a consistent internal representation.
- Sort data chronologically.
- Detect duplicate candles.
- Detect missing candle intervals.
- Validate OHLCV values.
- Handle empty datasets and malformed rows.
- Support configured date-range filtering.
- Report the actual first and last candle timestamps.
- Report the number of rows loaded and rejected.
- Avoid silently filling missing prices.
- Provide a clear error if the selected dataset does not match the configured symbol or timeframe.

Implement a function with an interface similar to:

```python
def load_historical_dataset(
    file_path,
    symbol=None,
    interval=None,
    start_date=None,
    end_date=None,
):
    """
    Load and validate a historical OHLCV CSV dataset.

    Return chronologically sorted, validated candle data.
    """
```

Adapt the implementation to the project's actual data structures and dependencies.

## 6. Strategy-independent backtesting interface

Create a common strategy interface that every strategy can implement.

Suggested module:

`src/backtesting/base_strategy.py`

Example:

```python
from abc import ABC, abstractmethod


class BaseStrategy(ABC):

    def __init__(self, parameters):
        self.parameters = parameters

    @abstractmethod
    def generate_signal(self, historical_data, index):
        """
        Return a strategy signal using only data
        available at the current simulation point.
        """
        raise NotImplementedError
```

Define a consistent signal contract.

Supported signals should include:

- `BUY`
- `SELL`
- `HOLD`
- `CLOSE_LONG`
- `CLOSE_SHORT`

Clearly distinguish between entering a position and exiting a position.

Do not assume every strategy needs all signals. Validate the output from each strategy and reject invalid signals.

The interface must permit future strategies such as:

- EMA crossover.
- EMA + RSI.
- Bollinger Bands.
- Supertrend.
- Moving-average crossover.
- Breakout trading.
- RSI mean reversion.
- Multi-indicator strategies.
- Custom strategies implemented by the user.

Do not hardcode all strategy logic inside the backtesting engine.

## 7. Existing and future strategy integration

Inspect the repository to identify the existing strategies and their configuration conventions.

Integrate existing strategies where practical without breaking their current behavior.

If a strategy is tightly coupled to live trading, separate its signal-generation logic from its order-placement logic.

Create reusable components for:

- Indicator calculation.
- Signal generation.
- Risk management.
- Position sizing.
- Simulated execution.
- Performance analysis.

A strategy should be able to consume the same validated dataset regardless of whether the strategy uses EMA, RSI, Bollinger Bands, Supertrend, or a custom indicator.

Do not duplicate the same CSV-loading or timestamp-validation code across individual strategies.

### Example: EMA + RSI strategy

Support these configurable parameters:

```yaml
parameters:
  fast_ema_period: 9
  slow_ema_period: 21
  rsi_period: 14
  rsi_buy_threshold: 55
  rsi_sell_threshold: 45
```

Illustrative rules:

- Enter long when the fast EMA crosses above the slow EMA and RSI satisfies the configured bullish condition.
- Enter short when the fast EMA crosses below the slow EMA and RSI satisfies the configured bearish condition, if shorting is enabled.
- Exit on the strategy's configured exit signal, stop-loss, take-profit, or another enabled risk-management rule.

These rules are examples. Preserve the actual logic of any existing strategy unless explicitly instructed to modify it.

A strategy's indicator periods, thresholds, and risk parameters must be configurable without editing its Python source.

## 8. Backtesting engine

Create a reusable backtesting engine.

Suggested module:

`src/backtesting/engine.py`

The engine must:

1. Load the configured dataset.
2. Load the selected strategy.
3. Initialize capital and simulated execution settings.
4. Calculate indicators using historical data only.
5. Generate signals chronologically.
6. Simulate entries, exits, and position changes.
7. Apply configurable fees and slippage.
8. Apply configured position-sizing rules.
9. Track open positions, cash, realized P&L, and unrealized P&L.
10. Apply stop-loss and take-profit rules.
11. Track the equity curve.
12. Export trade history.
13. Calculate performance metrics.
14. Export a backtest summary.
15. Report any data validation errors or incomplete simulations.

Support both long and short simulation when enabled.

For derivatives such as Delta Exchange India futures, document whether position size represents contracts, units, or another quantity. Do not assume contract multipliers, tick sizes, margin requirements, or P&L calculations without verifying the relevant product specifications.

Do not simulate liquidation or leveraged margin behavior inaccurately. If a faithful margin model is not implemented, clearly label the simulation as a simplified P&L model.

## 9. Prevent look-ahead bias

Backtest results must not use future data to generate past trading decisions.

Requirements:

- Calculate indicators using only available historical candles.
- Generate signals in chronological order.
- Do not use a candle's final close to enter at that same close unless the execution model explicitly supports a realistic closing-auction assumption.
- Use a configurable execution convention, with next-candle open as a sensible default for close-derived signals.
- Prevent future candles from affecting indicator values or signals.
- Handle indicator warm-up periods.
- Avoid using future high or low values to decide whether a trade could have entered earlier in the same candle.
- Document how stop-loss and take-profit are evaluated when both levels fall inside a candle's high-low range.
- Use a conservative and configurable ambiguity policy for intrabar stop-loss/take-profit collisions.
- Avoid using incomplete candles as completed historical signals unless explicitly supported.

Use the actual candle sequence and a documented fill model.

## 10. Risk management and parameter configuration

Keep risk management independent from the indicator logic.

Support configurable:

- Stop-loss percentage.
- Take-profit percentage.
- Fixed quantity or another explicitly implemented sizing mode.
- Initial capital.
- Maximum concurrent positions.
- Long and short permissions.
- Commission.
- Slippage.
- Maximum allowed position exposure, where implemented.
- Strategy-specific exit rules.

Validate parameters before the backtest starts.

For example, reject invalid EMA periods, nonpositive position sizes, negative fees, invalid date ranges, and unsupported risk-management modes.

Changing the values in configuration must not require editing the strategy's Python source.

Do not optimize parameters automatically unless explicitly enabled.

## 11. Parameter optimization

Add an optional parameter optimization feature that can evaluate multiple parameter combinations against the same historical dataset.

Example:

```yaml
optimization:
  enabled: false
  objective: "sharpe_ratio"

  parameter_ranges:
    fast_ema_period: [5, 9, 12]
    slow_ema_period: [20, 21, 30]
    rsi_period: [14, 21]
    rsi_buy_threshold: [50, 55, 60]
```

Requirements:

- Keep optimization disabled by default.
- Validate parameter combinations.
- Reject invalid combinations such as a fast EMA period greater than or equal to the slow EMA period, when the strategy requires fast less than slow.
- Reuse the loaded historical dataset instead of repeatedly downloading it.
- Record the parameters and metrics for every completed run.
- Export a ranked results CSV.
- Support an explicitly selected optimization objective.
- Avoid selecting a best strategy based only on net profit.
- Provide metrics for drawdown and trade count.
- Warn about overfitting.
- Support chronological train/test separation or walk-forward validation as a separate capability.

Do not use the same data to optimize parameters and claim an unbiased out-of-sample result.

## 12. Backtesting output

For each run, generate a unique results directory.

Example:

```text
data/backtest_results/
└── ema_rsi_BTCUSD_5m_2025-01-01_2025-12-31/
    ├── backtest_summary.json
    ├── trade_history.csv
    ├── equity_curve.csv
    ├── monthly_returns.csv
    ├── data_quality_report.json
    └── optimization_results.csv
```

Only create files relevant to the selected features.

### Trade history columns

Include as applicable:

- `trade_id`
- `symbol`
- `strategy`
- `interval`
- `side`
- `entry_time`
- `entry_price`
- `exit_time`
- `exit_price`
- `quantity`
- `gross_pnl`
- `commission`
- `slippage_cost`
- `net_pnl`
- `exit_reason`
- `holding_duration`
- `parameters`

Document the P&L formula and avoid double-counting fees or slippage.

### Backtest summary metrics

Calculate:

- Initial capital.
- Final equity.
- Net profit and loss.
- Return percentage.
- Gross profit.
- Gross loss.
- Number of completed trades.
- Winning trades.
- Losing trades.
- Win rate.
- Profit factor.
- Maximum drawdown.
- Average winning trade.
- Average losing trade.
- Average trade return.
- Total commissions.
- Total estimated slippage.
- Exposure or time in market, if supported.
- Sharpe ratio, with its return frequency and annualization assumptions documented.

Handle zero-trade runs, zero gross losses, missing periods, and undefined metrics without crashing or reporting misleading values.

Do not present hypothetical backtest results as actual trading profits.

## 13. Command-line interface

Provide a simple command-line interface.

Examples:

```bash
python main.py
```

Run the configured workflow.

```bash
python main.py --mode download
```

Download historical candles.

```bash
python main.py --mode backtest
```

Run a historical backtest using the existing dataset.

```bash
python main.py --mode backtest --symbol BTCUSD --interval 5m
```

Backtest the specified symbol and timeframe.

```bash
python main.py --mode optimize
```

Run parameter optimization only when explicitly enabled and correctly configured.

The CLI must report the selected strategy, symbol, interval, dataset path, date range, and output directory.

If a dataset is missing, do not silently switch to live trading or place orders. Return a clear error explaining how to download the required data.

## 14. Strict separation from live trading

This is a historical research and backtesting feature.

- Do not place real orders.
- Do not call order-placement endpoints.
- Do not enable live trading as a fallback.
- Do not modify live API credentials.
- Do not change the existing production trading configuration unexpectedly.
- Do not share mutable strategy state between a backtest and a live trading process.
- Do not let backtest execution import or invoke live order-placement code.
- Default to `execution.mode: backtest`.
- Fail closed if the execution mode is invalid.

If the existing project combines signal generation and order placement, refactor only as necessary to isolate signal generation while preserving existing live behavior.

## 15. Testing

Write automated tests for:

- CSV schema validation.
- Timestamp parsing and timezone conversion.
- Duplicate candle handling.
- Missing candle detection.
- OHLC validation.
- Strategy parameter validation.
- Indicator warm-up.
- Signal generation.
- Long and short position accounting.
- Entry and exit execution conventions.
- Stop-loss and take-profit handling.
- Fees and slippage.
- Position sizing.
- Equity curve calculation.
- Performance metrics.
- Parameter optimization.
- Train/test separation.
- Missing dataset errors.
- Ensuring the backtesting path cannot invoke live order placement.

Use small, deterministic, synthetic datasets for unit tests. Synthetic data is acceptable for testing but must never be mixed into real historical CSV files.

Use mocked exchange API responses for downloader tests.

## 16. Recommended project structure

Adapt to the current repository and reuse existing modules where appropriate.

```text
project-root/
├── config.yaml
├── main.py
├── requirements.txt
├── README.md
├── src/
│   ├── historical_downloader/
│   │   ├── delta_client.py
│   │   ├── candle_fetcher.py
│   │   └── csv_exporter.py
│   ├── data/
│   │   ├── dataset_loader.py
│   │   └── validators.py
│   ├── strategies/
│   │   ├── base_strategy.py
│   │   ├── ema_rsi.py
│   │   └── strategy_registry.py
│   └── backtesting/
│       ├── engine.py
│       ├── execution_simulator.py
│       ├── risk_manager.py
│       ├── metrics.py
│       ├── optimizer.py
│       └── reporting.py
├── data/
│   ├── historical/
│   └── backtest_results/
└── tests/
```

Do not create duplicate modules if the project already has equivalent implementations.

## 17. Final Cursor execution instructions

Execute the task in the following order:

1. Inspect the repository, existing configuration, historical downloader, and all existing strategy modules.
2. Read the three Delta reference files and verify the historical API contract.
3. Identify the existing live trading boundaries and protect them from unintended changes.
4. Implement or finish the reusable historical dataset loader.
5. Establish the standard OHLCV CSV schema.
6. Implement the strategy interface and registry.
7. Integrate the selected existing strategy without changing its intended trading logic.
8. Implement the backtesting engine and simulated execution.
9. Implement risk management, fees, slippage, and performance metrics.
10. Implement optional parameter optimization and out-of-sample evaluation.
11. Write automated tests.
12. Run the test suite and resolve failures.
13. Perform a small historical API integration test only if network access is available.
14. Verify that the same saved CSV can be used by multiple strategies without downloading it again.
15. Update the README with installation, configuration, execution, and troubleshooting instructions.
16. Provide a list of created and modified files and the exact commands to download data, backtest a strategy, and optimize parameters.

Do not stop after producing a plan or code snippets. Implement and test the application in the existing workspace.

Do not fabricate market data, exchange responses, or performance metrics. Clearly identify any functionality that could not be verified.

## Final acceptance criteria

The completed application must allow me to:

1. Download historical Delta Exchange India candles into CSV files.
2. Select BTCUSD, ETHUSD, SOLUSD, or another supported product through configuration.
3. Select any supported candle timeframe and date range.
4. Reuse the same CSV dataset across different algo strategies.
5. Change indicator parameters, entry conditions, stop-loss, take-profit, and position sizing through configuration.
6. Run backtests without making real trades.
7. Export trade history, equity curves, and performance metrics.
8. Optionally compare parameter combinations and test them on separate historical periods.
9. Add future strategies without rewriting the downloader or backtesting engine.
10. Keep the existing live trading system isolated and unaffected.

**The most important requirement:** historical data, strategy logic, strategy parameters, and simulated execution must remain separate components. Any strategy I select in Cursor should be able to reuse the standard historical dataset and configuration-driven backtesting framework.

.env file for keys
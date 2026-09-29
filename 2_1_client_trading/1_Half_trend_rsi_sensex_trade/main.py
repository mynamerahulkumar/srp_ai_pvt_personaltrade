"""Dhan-only Half Trend options bot.

The whole application lives in this file. Reference material under docs/ is
not imported and is not required at runtime. config.yaml holds trading
settings. .env holds Dhan credentials and nothing else.
"""

from __future__ import annotations

import math
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml


# ============================================================
# CONFIGURATION
# ============================================================

BOT_NAME = "SRP HALF TREND OPTIONS ENGINE"
BOT_VERSION = "1.0.0"
ALGO_TAG_PREFIX = "HT"
ATR_PERIOD = 100
NOTIONAL_WARNING_RS = 50_000
MARKET_OPEN = clock_time(9, 15)
MARKET_CLOSE = clock_time(15, 30)
DHAN_INTERVALS = {1, 5, 15, 25, 60}
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"
STOP_PATH = ROOT / ".bot_stop"
MASTER_PATH = ROOT / "api-scrip-master.csv"
DASHBOARD_WIDTH = 58

# Symbol text used to find the index row in the security master.
# These are names, not security IDs. IDs are read from the master.
UNDERLYING_SPECS: dict[str, dict[str, Any]] = {
    "SENSEX": {
        "names": ("SENSEX",),
        "preferred_symbol": "SENSEX",
        "index_exchange": "BSE",
        "option_exchange": "BSE",
        "option_segment": "BSE_FNO",
    },
    "NIFTY": {
        "names": ("NIFTY", "NIFTY 50", "NIFTY50"),
        "preferred_symbol": "NIFTY",
        "index_exchange": "NSE",
        "option_exchange": "NSE",
        "option_segment": "NSE_FNO",
    },
    "BANKNIFTY": {
        "names": ("BANKNIFTY", "NIFTY BANK", "BANK NIFTY"),
        "preferred_symbol": "BANKNIFTY",
        "index_exchange": "NSE",
        "option_exchange": "NSE",
        "option_segment": "NSE_FNO",
    },
}

RETRYABLE_ENTRY_BLOCKS = {"OUTSIDE_SESSION", "DATA_ERROR", "MARKET_CLOSED"}


@dataclass
class AppConfig:
    """Trading settings loaded from config.yaml."""

    trading_mode: str
    underlying: str
    timeframe_minutes: int
    amplitude: int
    channel_deviation: float
    signal_confirmation: str
    exit_on_opposite_signal: bool
    expiry_mode: str
    expiry: str
    strike_mode: str
    strike_offset: int
    lots: int
    order_type: str
    product_type: str
    holding_mode: str
    tp_enabled: bool
    tp_type: str
    tp_value: float
    sl_enabled: bool
    sl_type: str
    sl_value: float
    max_open_strategies: int
    max_orders_per_day: int
    max_loss_per_day_inr: float
    no_reentry_after_stop_loss: bool
    no_reentry_after_max_loss: bool
    session_enabled: bool
    timezone_name: str
    run_mode: str
    start_time: str
    stop_time: str
    close_all_positions_at_stop: bool
    stop_bot_after_close: bool
    polling_seconds: int
    post_close_polls: int
    post_close_poll_seconds: int

    @property
    def timezone(self) -> ZoneInfo:
        """Return the configured exchange timezone."""
        return ZoneInfo(self.timezone_name)

    @property
    def option_segment(self) -> str:
        """Return the Dhan F&O segment for the configured index."""
        return str(UNDERLYING_SPECS[self.underlying]["option_segment"])

    @property
    def option_exchange(self) -> str:
        """Return the exchange id used in the security master for options."""
        return str(UNDERLYING_SPECS[self.underlying]["option_exchange"])


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    """Return a mapping or raise a configuration error."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a YAML mapping")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], label: str) -> None:
    """Fail when the YAML contains a setting this bot does not read."""
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"Unknown {label} settings: {', '.join(unknown)}")


def _as_bool(value: Any, label: str) -> bool:
    """Accept a real boolean. Quoted true/false strings are also accepted."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"{label} must be true or false")


def _as_int(value: Any, label: str) -> int:
    """Accept an integer, including whole numbers stored as floats by the CSV master.

    Booleans are rejected because they are ints in Python. NumPy numbers from
    a DataFrame are accepted when they are whole numbers.
    """
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, int):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(number)


def _as_float(value: Any, label: str) -> float:
    """Accept a number for prices, percentages, and channel width."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    return float(value)


def _as_choice(value: Any, label: str, choices: set[str]) -> str:
    """Normalize one enum-style setting."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be one of: {', '.join(sorted(choices))}")
    cleaned = value.strip().upper()
    if cleaned not in choices:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(choices))}")
    return cleaned


def _parse_clock(value: str, label: str) -> clock_time:
    """Parse HH:MM. Invalid clocks are rejected before the bot can trade."""
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"{label} must use HH:MM")
    hour = _as_int(int(parts[0]) if parts[0].isdigit() else parts[0], label)
    minute = _as_int(int(parts[1]) if parts[1].isdigit() else parts[1], label)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"{label} must use HH:MM")
    return clock_time(hour, minute)


def validate_config(config: AppConfig) -> None:
    """Reject settings that would make orders, sessions, or exits ambiguous.

    Purpose:
        Stop the process before any order when the YAML is inconsistent.

    Inputs:
        A parsed AppConfig.

    Output:
        None. Raises ValueError with the first problem found.

    Trading use:
        Keeps PAPER/LIVE, the index, the product type, and the session clock
        inside the combinations this bot can actually execute.
    """
    if config.underlying not in UNDERLYING_SPECS:
        raise ValueError("underlying must be SENSEX, NIFTY, or BANKNIFTY")
    if config.trading_mode not in {"PAPER", "LIVE"}:
        raise ValueError("trading_mode must be PAPER or LIVE")
    if config.run_mode not in {"CONTINUOUS", "SCHEDULED"}:
        raise ValueError("run_mode must be CONTINUOUS or SCHEDULED")
    if config.strike_mode not in {"ATM", "ITM", "OTM"}:
        raise ValueError("strike_mode must be ATM, ITM, or OTM")
    if config.expiry_mode not in {"NEAREST", "CONFIGURED"}:
        raise ValueError("expiry_mode must be NEAREST or CONFIGURED")
    if config.tp_type not in {"PERCENT", "ABSOLUTE"} or config.sl_type not in {"PERCENT", "ABSOLUTE"}:
        raise ValueError("TP/SL type must be PERCENT or ABSOLUTE")
    if config.signal_confirmation != "CANDLE_CLOSE":
        raise ValueError("signal_confirmation must be CANDLE_CLOSE")
    if config.timeframe_minutes not in DHAN_INTERVALS:
        raise ValueError("timeframe_minutes must be one of 1, 5, 15, 25, 60")
    if config.amplitude < 1:
        raise ValueError("amplitude must be at least 1")
    if config.channel_deviation <= 0:
        raise ValueError("channel_deviation must be greater than 0")
    if config.lots < 1:
        raise ValueError("lots must be at least 1")
    if config.strike_offset < 0:
        raise ValueError("strike_offset cannot be negative")
    if config.strike_mode == "ATM" and config.strike_offset != 0:
        raise ValueError("ATM strike_mode requires strike_offset 0")
    if config.order_type not in {"LIMIT", "MARKET"}:
        raise ValueError("order_type must be LIMIT or MARKET")
    if config.product_type not in {"INTRADAY", "MARGIN"}:
        raise ValueError("product_type must be INTRADAY or MARGIN")
    if config.holding_mode not in {"INTRADAY", "SWING", "POSITIONAL"}:
        raise ValueError("holding_mode must be INTRADAY, SWING, or POSITIONAL")
    if config.holding_mode == "INTRADAY" and config.product_type != "INTRADAY":
        raise ValueError("INTRADAY holding_mode requires product_type INTRADAY")
    if config.holding_mode in {"SWING", "POSITIONAL"} and config.product_type != "MARGIN":
        raise ValueError("SWING and POSITIONAL require product_type MARGIN")
    if config.max_open_strategies != 1:
        raise ValueError("max_open_strategies must be 1")
    if config.max_orders_per_day < 1:
        raise ValueError("max_orders_per_day must be at least 1")
    if config.max_loss_per_day_inr <= 0:
        raise ValueError("max_loss_per_day_inr must be greater than 0")
    if config.tp_enabled and config.tp_value <= 0:
        raise ValueError("take profit value must be greater than 0")
    if config.sl_enabled and config.sl_value <= 0:
        raise ValueError("stop loss value must be greater than 0")
    if config.sl_enabled and config.sl_type == "PERCENT" and config.sl_value >= 100:
        raise ValueError("percent stop loss must be below 100")
    if config.polling_seconds < 1 or config.post_close_polls < 1 or config.post_close_poll_seconds < 1:
        raise ValueError("polling and post-close settings must be at least 1")
    if config.expiry_mode == "CONFIGURED":
        try:
            datetime.strptime(config.expiry, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("expiry must be YYYY-MM-DD when expiry_mode is CONFIGURED") from exc
    try:
        ZoneInfo(config.timezone_name)
    except Exception as exc:
        raise ValueError(f"timezone is not valid: {config.timezone_name}") from exc
    start = _parse_clock(config.start_time, "start_time")
    stop = _parse_clock(config.stop_time, "stop_time")
    if start >= stop:
        raise ValueError("start_time must be earlier than stop_time")


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    """Load and validate config.yaml.

    Purpose:
        Make the YAML file the only source of trading settings.

    Inputs:
        Path to config.yaml.

    Output:
        An AppConfig. Invalid files raise ValueError or a YAML error.

    Trading use:
        Every later decision reads this object. Nothing is copied into a
        second configuration file.
    """
    if not path.exists():
        raise ValueError(f"config.yaml was not found at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"config.yaml is not valid YAML: {exc}") from exc
    root = _require_mapping(raw, "config.yaml")
    _reject_unknown(root, {"mode", "market", "strategy", "option", "execution", "risk", "session", "runtime"}, "top-level")
    mode = _require_mapping(root.get("mode"), "mode")
    market = _require_mapping(root.get("market"), "market")
    strategy = _require_mapping(root.get("strategy"), "strategy")
    option = _require_mapping(root.get("option"), "option")
    execution = _require_mapping(root.get("execution"), "execution")
    risk = _require_mapping(root.get("risk"), "risk")
    session = _require_mapping(root.get("session"), "session")
    runtime = _require_mapping(root.get("runtime"), "runtime")
    _reject_unknown(mode, {"trading_mode"}, "mode")
    _reject_unknown(market, {"underlying", "timeframe_minutes"}, "market")
    _reject_unknown(strategy, {"amplitude", "channel_deviation", "signal_confirmation", "exit_on_opposite_signal"}, "strategy")
    _reject_unknown(option, {"expiry_mode", "expiry", "strike_mode", "strike_offset", "lots"}, "option")
    _reject_unknown(execution, {"order_type", "product_type", "holding_mode"}, "execution")
    _reject_unknown(risk, {"take_profit", "stop_loss", "max_open_strategies", "max_orders_per_day", "max_loss_per_day_inr", "no_reentry_after_stop_loss", "no_reentry_after_max_loss"}, "risk")
    _reject_unknown(session, {"enabled", "timezone", "run_mode", "start_time", "stop_time", "close_all_positions_at_stop", "stop_bot_after_close"}, "session")
    _reject_unknown(runtime, {"polling_seconds", "post_close_polls", "post_close_poll_seconds"}, "runtime")
    take_profit = _require_mapping(risk.get("take_profit"), "risk.take_profit")
    stop_loss = _require_mapping(risk.get("stop_loss"), "risk.stop_loss")
    _reject_unknown(take_profit, {"enabled", "type", "value"}, "risk.take_profit")
    _reject_unknown(stop_loss, {"enabled", "type", "value"}, "risk.stop_loss")
    expiry = "" if option.get("expiry") is None else str(option.get("expiry")).strip()
    config = AppConfig(
        trading_mode=_as_choice(mode.get("trading_mode"), "trading_mode", {"PAPER", "LIVE"}),
        underlying=_as_choice(market.get("underlying"), "underlying", set(UNDERLYING_SPECS)),
        timeframe_minutes=_as_int(market.get("timeframe_minutes"), "timeframe_minutes"),
        amplitude=_as_int(strategy.get("amplitude"), "amplitude"),
        channel_deviation=_as_float(strategy.get("channel_deviation"), "channel_deviation"),
        signal_confirmation=_as_choice(strategy.get("signal_confirmation"), "signal_confirmation", {"CANDLE_CLOSE"}),
        exit_on_opposite_signal=_as_bool(strategy.get("exit_on_opposite_signal"), "exit_on_opposite_signal"),
        expiry_mode=_as_choice(option.get("expiry_mode"), "expiry_mode", {"NEAREST", "CONFIGURED"}),
        expiry=expiry,
        strike_mode=_as_choice(option.get("strike_mode"), "strike_mode", {"ATM", "ITM", "OTM"}),
        strike_offset=_as_int(option.get("strike_offset"), "strike_offset"),
        lots=_as_int(option.get("lots"), "lots"),
        order_type=_as_choice(execution.get("order_type"), "order_type", {"LIMIT", "MARKET"}),
        product_type=_as_choice(execution.get("product_type"), "product_type", {"INTRADAY", "MARGIN"}),
        holding_mode=_as_choice(execution.get("holding_mode"), "holding_mode", {"INTRADAY", "SWING", "POSITIONAL"}),
        tp_enabled=_as_bool(take_profit.get("enabled"), "take_profit.enabled"),
        tp_type=_as_choice(take_profit.get("type"), "take_profit.type", {"PERCENT", "ABSOLUTE"}),
        tp_value=_as_float(take_profit.get("value"), "take_profit.value"),
        sl_enabled=_as_bool(stop_loss.get("enabled"), "stop_loss.enabled"),
        sl_type=_as_choice(stop_loss.get("type"), "stop_loss.type", {"PERCENT", "ABSOLUTE"}),
        sl_value=_as_float(stop_loss.get("value"), "stop_loss.value"),
        max_open_strategies=_as_int(risk.get("max_open_strategies"), "max_open_strategies"),
        max_orders_per_day=_as_int(risk.get("max_orders_per_day"), "max_orders_per_day"),
        max_loss_per_day_inr=_as_float(risk.get("max_loss_per_day_inr"), "max_loss_per_day_inr"),
        no_reentry_after_stop_loss=_as_bool(risk.get("no_reentry_after_stop_loss"), "no_reentry_after_stop_loss"),
        no_reentry_after_max_loss=_as_bool(risk.get("no_reentry_after_max_loss"), "no_reentry_after_max_loss"),
        session_enabled=_as_bool(session.get("enabled"), "session.enabled"),
        timezone_name=str(session.get("timezone") or "").strip(),
        run_mode=_as_choice(session.get("run_mode"), "run_mode", {"CONTINUOUS", "SCHEDULED"}),
        start_time=str(session.get("start_time") or "").strip(),
        stop_time=str(session.get("stop_time") or "").strip(),
        close_all_positions_at_stop=_as_bool(session.get("close_all_positions_at_stop"), "close_all_positions_at_stop"),
        stop_bot_after_close=_as_bool(session.get("stop_bot_after_close"), "stop_bot_after_close"),
        polling_seconds=_as_int(runtime.get("polling_seconds"), "polling_seconds"),
        post_close_polls=_as_int(runtime.get("post_close_polls"), "post_close_polls"),
        post_close_poll_seconds=_as_int(runtime.get("post_close_poll_seconds"), "post_close_poll_seconds"),
    )
    validate_config(config)
    return config


def load_environment(path: Path = ENV_PATH) -> tuple[str, str]:
    """Load Dhan credentials from .env.

    Purpose:
        Keep the client id and access token out of config.yaml and out of logs.

    Inputs:
        Path to .env.

    Output:
        (client_id, access_token). The token is never printed.

    Trading use:
        Both PAPER and LIVE need the token for market data. LIVE also needs it
        for orders. Missing values stop the bot before any order.
    """
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("python-dotenv is not installed. Run: pip install -r requirements.txt") from exc
    if not path.exists():
        raise RuntimeError(".env was not found. Copy env.example to .env and add Dhan credentials.")
    load_dotenv(path)
    client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
    access_token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    if not client_id or not access_token:
        raise RuntimeError(
            "DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN must be set in .env. "
            "The values were not printed."
        )
    return client_id, access_token


# ============================================================
# DATA CLASSES / STATE
# ============================================================

@dataclass
class OptionContract:
    """One option selected for a possible order."""

    security_id: str
    trading_symbol: str
    option_type: str
    strike: float
    expiry: str
    lot_size: int
    tick_size: float
    ltp: float | None
    bid: float | None
    ask: float | None
    exchange_segment: str


@dataclass
class Position:
    """The single long option this algo is managing."""

    security_id: str
    trading_symbol: str
    option_type: str
    strike: float
    expiry: str
    quantity: int
    lot_size: int
    tick_size: float
    entry_price: float
    product_type: str
    exchange_segment: str
    entry_time: datetime
    order_id: str
    order_tag: str
    signal: str
    tp_price: float | None
    sl_price: float | None


@dataclass
class TradeRecord:
    """One completed option round trip. P&L is gross premium."""

    symbol: str
    option_type: str
    quantity: int
    entry_price: float
    exit_price: float
    pnl: float
    reason: str
    entry_time: datetime
    exit_time: datetime


@dataclass
class BotState:
    """In-memory session state. Nothing here is written to a database."""

    status: str = "FLAT"
    trading_date: date | None = None
    orders_today: int = 0
    exit_orders_today: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    position: Position | None = None
    pending_order_id: str | None = None
    pending_kind: str | None = None
    pending_exit_reason: str | None = None
    pending_tag: str | None = None
    last_signal: str | None = None
    last_signal_candle: datetime | None = None
    last_confirmed_candle: datetime | None = None
    last_evaluated_candle: datetime | None = None
    deferred_signal: str | None = None
    deferred_candle: datetime | None = None
    last_option_contract: OptionContract | None = None
    stop_loss_hit: bool = False
    max_loss_hit: bool = False
    session_stop_reached: bool = False
    allow_entries: bool = True
    last_action: str = "STARTING"
    data_status: str = "OK"
    quote_failed: bool = False
    half_trend_value: float | None = None
    signal_label: str = "NONE"
    signal_reason: str = "Waiting for a confirmed candle"
    underlying_ltp: float | None = None
    option_ltp: float | None = None
    candle_time: datetime | None = None
    market_status: str = "UNKNOWN"
    post_close_polls_done: int = 0
    market_close_handled: bool = False
    trades: list[TradeRecord] = field(default_factory=list)
    events: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    candles: pd.DataFrame | None = None
    last_candle_fetch: datetime | None = None
    expiries: list[str] = field(default_factory=list)
    resolved_expiry: str = ""
    underlying_security_id: str = ""
    underlying_symbol: str = ""
    sample_lot_size: int | None = None
    chain_rows: list[dict[str, Any]] | None = None
    chain_spot: float | None = None
    chain_fetched_at: datetime | None = None
    chain_expiry: str = ""
    next_poll_seconds: int = 0
    consecutive_errors: int = 0
    exit_lookup_attempts: int = 0
    entry_lookup_attempts: int = 0
    last_position_refresh: datetime | None = None
    last_quote_monotonic: float = 0.0
    shutting_down: bool = False
    entry_skip_candle: datetime | None = None
    shutdown_after_exit: str | None = None
    unsafe_to_close: bool = False


class DhanConnection:
    """Holds the SDK module and the authenticated client."""

    def __init__(self, client: object, sdk: object) -> None:
        self.dhan = client
        self.sdk = sdk


CONFIG: AppConfig | None = None
STATE = BotState()
BROKER: DhanConnection | None = None
MASTER: pd.DataFrame | None = None


def now_in_tz() -> datetime:
    """Current time in the configured market timezone."""
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    return datetime.now(CONFIG.timezone)


def note(message: str) -> None:
    """Remember a short event for the dashboard and print it once.

    The same message is not printed again on the next poll. That keeps a
    repeated "outside session" or "max orders" line from flooding the terminal.
    """
    if STATE.last_action == message:
        return
    STATE.events.appendleft(message)
    STATE.last_action = message
    print(message)


def format_inr(value: float | None) -> str:
    """Format a rupee amount. None stays a dash so missing data is obvious."""
    if value is None:
        return "-"
    return f"₹{value:,.2f}"


def format_number(value: float | None) -> str:
    """Format an index or indicator value."""
    if value is None:
        return "-"
    return f"{value:,.2f}"


def positive_float(value: Any) -> float | None:
    """Return a positive float, or None when the field is missing or unusable."""
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def round_to_tick(price: float, tick_size: float) -> float:
    """Round a premium to the contract tick so Dhan will accept a limit price.

    Purpose:
        Avoid order rejects caused by an off-tick limit.

    Inputs:
        Price in rupees and the security-master tick size.

    Output:
        The nearest tick, still in rupees.

    Trading use:
        Applied to limit orders and to TP/SL levels. It does not change a
        market order.
    """
    if tick_size <= 0:
        raise ValueError("Tick size must be positive")
    steps = round(price / tick_size)
    return round(steps * tick_size, 10)


def underlying_spec(name: str) -> dict[str, Any]:
    """Return the exchange mapping for one supported index."""
    try:
        return UNDERLYING_SPECS[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported underlying: {name}") from exc


# ============================================================
# DHAN CONNECTION
# ============================================================

def create_dhan_client(client_id: str, access_token: str) -> DhanConnection:
    """Open a DhanHQ v2 client.

    Purpose:
        Authenticate with DhanContext. This is the only broker connection.

    Inputs:
        Client id and access token from the environment.

    Output:
        DhanConnection. The token is not stored in the dashboard state.

    Trading use:
        PAPER uses the client for data only. LIVE uses the same client for
        orders. Credentials are never printed.
    """
    try:
        from dhanhq import DhanContext, dhanhq
    except ImportError as exc:
        raise RuntimeError("dhanhq is not installed. Run: pip install -r requirements.txt") from exc
    context = DhanContext(client_id, access_token)
    return DhanConnection(dhanhq(context), dhanhq)


def sdk_value(name: str) -> Any:
    """Read one Dhan constant from the installed SDK. Do not invent a replacement."""
    if BROKER is None or not hasattr(BROKER.sdk, name):
        raise RuntimeError(f"Installed Dhan SDK has no constant '{name}'")
    return getattr(BROKER.sdk, name)


def unwrap_sdk_data(response: Any) -> Any:
    """Return the payload from a Dhan SDK envelope.

    The SDK usually returns status/remarks/data. A failure becomes an exception
    so callers do not treat a rejected request as a filled order.
    """
    if not isinstance(response, dict):
        raise RuntimeError(f"Unexpected Dhan response type: {type(response).__name__}")
    status = str(response.get("status", "")).lower()
    if status == "failure":
        remarks = response.get("remarks") or response.get("data") or "Dhan request failed"
        raise RuntimeError(str(remarks))
    if "data" in response:
        return response["data"]
    return response


def _is_rate_limit(exc: Exception) -> bool:
    """Detect a Dhan rate-limit message without depending on one error code."""
    text = str(exc).lower()
    return any(token in text for token in ("rate", "too many", "429", "dh-904"))


def _data_plan_hint(exc: Exception) -> str:
    """Add a short hint when Dhan reports a data-plan or auth problem."""
    text = str(exc).lower()
    if any(token in text for token in ("dh-902", "806", "data plan", "invalid token", "unauthorized")):
        return (
            f"{exc}. Check the Dhan access token and that a Data Plan is active. "
            "Order placement separately needs a whitelisted static IP."
        )
    return str(exc)


# ============================================================
# SECURITY MASTER
# ============================================================

def _require_master_columns(frame: pd.DataFrame) -> None:
    """Fail clearly when the cached master cannot resolve contracts."""
    required = {
        "SEM_SMST_SECURITY_ID",
        "SEM_EXM_EXCH_ID",
        "SEM_INSTRUMENT_NAME",
        "SEM_TRADING_SYMBOL",
        "SEM_LOT_UNITS",
        "SEM_TICK_SIZE",
        "SEM_EXPIRY_DATE",
        "SEM_STRIKE_PRICE",
        "SEM_OPTION_TYPE",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(
            "Security master is missing columns: "
            + ", ".join(missing)
            + ". Delete api-scrip-master.csv and start again so it can be downloaded."
        )


def load_security_master(refresh: bool = False) -> pd.DataFrame:
    """Load the Dhan security master once.

    Purpose:
        Resolve index and option ids, lot size, and tick size from data rather
        than from constants in this file.

    Inputs:
        refresh=True forces a download. The polling loop does not do that.

    Output:
        The master DataFrame, also kept in memory.

    Trading use:
        Derivative security ids change with expiry, so a hardcoded option id
        would eventually trade the wrong contract.
    """
    global MASTER
    if MASTER is not None and not refresh:
        return MASTER
    if MASTER_PATH.exists() and not refresh:
        MASTER = pd.read_csv(MASTER_PATH, low_memory=False)
        _require_master_columns(MASTER)
        return MASTER
    try:
        from dhanhq import dhanhq
    except ImportError as exc:
        raise RuntimeError("dhanhq is not installed. Run: pip install -r requirements.txt") from exc
    downloaded = dhanhq.fetch_security_list("compact")
    if downloaded is None or getattr(downloaded, "empty", True):
        raise RuntimeError("Dhan did not return a security master. Check the network and access token.")
    _require_master_columns(downloaded)
    MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    downloaded.to_csv(MASTER_PATH, index=False)
    MASTER = downloaded
    return MASTER


def resolve_underlying(master: pd.DataFrame, underlying: str) -> dict[str, str]:
    """Find the index security id for SENSEX, NIFTY, or BANKNIFTY.

    Purpose:
        Avoid hardcoded index ids. The id is whatever the current master says.

    Inputs:
        Security-master frame and the configured underlying name.

    Output:
        security_id, trading symbol, and the quote segment IDX_I.

    Trading use:
        Candle history, quotes, expiry lists, and the option chain all use this id.
        If the master is ambiguous, the bot stops instead of guessing.
    """
    spec = underlying_spec(underlying)
    exchange = master["SEM_EXM_EXCH_ID"].astype(str).str.upper().str.strip()
    instrument = master["SEM_INSTRUMENT_NAME"].astype(str).str.upper().str.strip()
    trading = master["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.strip()
    if "SEM_CUSTOM_SYMBOL" in master.columns:
        custom = master["SEM_CUSTOM_SYMBOL"].astype(str).str.upper().str.strip()
    else:
        custom = trading
    names = set(spec["names"])
    symbol_match = trading.isin(names) | custom.isin(names)
    matches = master[
        symbol_match
        & instrument.isin({"INDEX", "IDX"})
        & (exchange == spec["index_exchange"])
    ]
    if matches.empty:
        raise RuntimeError(
            f"Could not resolve {underlying} from the security master "
            f"on {spec['index_exchange']}. No index row matched."
        )
    exact = matches[trading == spec["preferred_symbol"]]
    chosen = exact if not exact.empty else matches
    security_ids = chosen["SEM_SMST_SECURITY_ID"].astype(str).str.strip().unique().tolist()
    if len(security_ids) != 1:
        raise RuntimeError(
            f"{underlying} matched multiple security ids in the master: {', '.join(security_ids)}. "
            "The bot will not guess."
        )
    row = chosen.iloc[0]
    return {
        "security_id": str(row["SEM_SMST_SECURITY_ID"]).strip(),
        "trading_symbol": str(row["SEM_TRADING_SYMBOL"]).strip(),
        "quote_segment": "IDX_I",
    }


def resolve_option_contract(security_id: str, chain_row: dict[str, Any], option_type: str) -> OptionContract:
    """Attach lot size, tick size, and symbol to a chain security id.

    Purpose:
        The option chain supplies the current contract id and premium. The
        master supplies the quantity and tick rules.

    Inputs:
        Security id, the normalized chain row, and CE or PE.

    Output:
        OptionContract. Raises if the id is missing from the master.

    Trading use:
        Quantity is lots times this lot size. A missing tick stops the order
        instead of using a guessed tick.
    """
    if MASTER is None:
        raise RuntimeError("Security master is not loaded")
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    side = option_type.lower()
    wanted = str(security_id).strip()
    rows = MASTER[MASTER["SEM_SMST_SECURITY_ID"].astype(str).str.strip() == wanted]
    exchange = rows["SEM_EXM_EXCH_ID"].astype(str).str.upper().str.strip()
    instrument = rows["SEM_INSTRUMENT_NAME"].astype(str).str.upper().str.strip()
    option_rows = rows[
        (exchange == CONFIG.option_exchange)
        & instrument.isin({"OPTIDX", "OPTSTK"})
    ]
    if option_rows.empty:
        raise RuntimeError(f"Option security id {wanted} was not found in the security master")
    row = option_rows.iloc[0]
    master_side = str(row.get("SEM_OPTION_TYPE", "")).upper().strip()
    if master_side and master_side != option_type:
        raise RuntimeError(f"Security id {wanted} is {master_side}, not {option_type}")
    lot_size = _as_int(row["SEM_LOT_UNITS"], "lot size")
    tick_size = positive_float(row["SEM_TICK_SIZE"])
    if lot_size < 1 or tick_size is None:
        raise RuntimeError(f"Security id {wanted} has no usable lot size or tick size")
    expiry = str(row.get("SEM_EXPIRY_DATE", ""))[:10]
    strike = positive_float(row.get("SEM_STRIKE_PRICE")) or float(chain_row["strike"])
    ltp = positive_float(chain_row.get(f"{side}_ltp"))
    return OptionContract(
        security_id=wanted,
        trading_symbol=str(row["SEM_TRADING_SYMBOL"]).strip(),
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        lot_size=lot_size,
        tick_size=tick_size,
        ltp=ltp,
        bid=positive_float(chain_row.get(f"{side}_bid")),
        ask=positive_float(chain_row.get(f"{side}_ask")),
        exchange_segment=CONFIG.option_segment,
    )


# ============================================================
# MARKET DATA
# ============================================================

def _normalize_timestamp(value: Any, converter: Any = None) -> datetime:
    """Convert a Dhan candle time to the configured timezone."""
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    tz = CONFIG.timezone
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return _normalize_timestamp(int(text), converter)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    if isinstance(value, (int, float)) and converter is not None:
        return _normalize_timestamp(converter(value), converter=None)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds = seconds / 1000.0
        return datetime.fromtimestamp(seconds, tz=tz)
    raise ValueError(f"Could not read a candle timestamp from {value!r}")


def _candles_from_payload(payload: Any) -> pd.DataFrame:
    """Turn a Dhan history payload into timestamp/open/high/low/close rows."""
    if payload is None:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    frame = pd.DataFrame(payload)
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    rename = {
        "start_time": "timestamp",
        "starttime": "timestamp",
        "time": "timestamp",
    }
    frame = frame.rename(columns=rename)
    if "timestamp" not in frame.columns:
        raise RuntimeError("Dhan candle response did not include timestamps")
    for column in ("open", "high", "low", "close"):
        if column not in frame.columns:
            raise RuntimeError(f"Dhan candle response did not include {column}")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    converter = getattr(BROKER.dhan, "convert_to_date_time", None) if BROKER is not None else None
    frame["timestamp"] = frame["timestamp"].map(lambda value: _normalize_timestamp(value, converter))
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    columns = ["timestamp", "open", "high", "low", "close"]
    if "volume" in frame.columns:
        columns.append("volume")
    return frame[columns].reset_index(drop=True)


def fetch_underlying_candles(now: datetime | None = None) -> pd.DataFrame:
    """Download index candles and keep only confirmed, recent rows.

    Purpose:
        Feed Half Trend without calling history on every 5-second poll.

    Inputs:
        Current exchange time. Uses the resolved index security id.

    Output:
        OHLC frame in the configured timezone. The forming candle is removed.

    Trading use:
        Signals come from this frame. A failed download leaves the previous
        candles in place and blocks a new entry.
    """
    if BROKER is None or CONFIG is None:
        raise RuntimeError("Dhan and config must be ready before fetching candles")
    moment = now or now_in_tz()
    start = (moment.date() - timedelta(days=10)).isoformat()
    end = moment.date().isoformat()
    response = BROKER.dhan.intraday_minute_data(
        security_id=str(STATE.underlying_security_id),
        exchange_segment=sdk_value("INDEX"),
        instrument_type="INDEX",
        from_date=start,
        to_date=end,
        interval=int(CONFIG.timeframe_minutes),
    )
    frame = _candles_from_payload(unwrap_sdk_data(response))
    frame = drop_unconfirmed_candle(frame, moment)
    keep = ATR_PERIOD + CONFIG.amplitude + 30
    if len(frame) > keep:
        frame = frame.iloc[-keep:].reset_index(drop=True)
    STATE.candles = frame
    STATE.last_candle_fetch = moment
    if not frame.empty:
        STATE.candle_time = frame.iloc[-1]["timestamp"]
        STATE.last_confirmed_candle = STATE.candle_time
    return frame


def drop_unconfirmed_candle(frame: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Remove the candle that has not closed yet.

    Dhan timestamps are treated as the candle open. A 10:05 poll must not
    treat the 10:05 candle as confirmed when the timeframe is 5 minutes.
    """
    if CONFIG is None or frame.empty:
        return frame
    last_open = frame.iloc[-1]["timestamp"]
    if last_open.tzinfo is None:
        last_open = last_open.replace(tzinfo=CONFIG.timezone)
    if last_open + timedelta(minutes=CONFIG.timeframe_minutes) > now:
        return frame.iloc[:-1].reset_index(drop=True)
    return frame.reset_index(drop=True)


def candles_need_refresh(now: datetime) -> bool:
    """True when a new confirmed candle should exist or no candles are loaded.

    History is not fetched on every poll. After a candle is due, retries are
    spaced out so a slow API cannot be hammered.
    """
    if CONFIG is None:
        return False
    if STATE.candles is None or STATE.last_candle_fetch is None or STATE.candle_time is None:
        return True
    due_at = STATE.candle_time + timedelta(minutes=CONFIG.timeframe_minutes)
    if now < due_at:
        return False
    return (now - STATE.last_candle_fetch) >= timedelta(seconds=max(20, CONFIG.polling_seconds))


def extract_quote_price(payload: Any, security_id: str) -> float | None:
    """Find last_price for one security id inside a nested quote response."""
    target = str(security_id)
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key) == target and isinstance(value, dict):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    for item in found:
        for field_name in ("last_price", "ltp", "close"):
            price = positive_float(item.get(field_name))
            if price is not None:
                return price
    return None


def fetch_ltp(security_id: str, exchange_segment: str) -> float | None:
    """Fetch one last traded price.

    Purpose:
        Read the index or the open option without downloading the option chain.

    Inputs:
        Security id and Dhan segment such as IDX_I or BSE_FNO.

    Output:
        Positive price, or None when the quote has no usable price.

    Trading use:
        Option TP/SL uses this premium, not the index price.
    """
    prices = fetch_quotes({exchange_segment: [str(security_id)]})
    return prices.get(str(security_id))


def fetch_quotes(instruments: dict[str, list[str]]) -> dict[str, float]:
    """Fetch several LTPs in one quote call.

    Quote APIs are limited to about one request per second, so the bot waits
    if the previous quote was too recent. ticker_data is tried first.
    """
    if BROKER is None:
        raise RuntimeError("Dhan is not connected")
    payload: dict[str, list[int]] = {}
    for segment, security_ids in instruments.items():
        cleaned = [int(str(security_id)) for security_id in security_ids if str(security_id).strip()]
        if cleaned:
            payload[segment] = cleaned
    if not payload:
        return {}
    elapsed = time.monotonic() - STATE.last_quote_monotonic
    if STATE.last_quote_monotonic and elapsed < 1.05:
        time.sleep(1.05 - elapsed)
    last_error: Exception | None = None
    for method_name in ("ticker_data", "ohlc_data"):
        try:
            response = getattr(BROKER.dhan, method_name)(payload)
            STATE.last_quote_monotonic = time.monotonic()
            body = unwrap_sdk_data(response)
            prices: dict[str, float] = {}
            for security_ids in payload.values():
                for security_id in security_ids:
                    price = extract_quote_price(body, str(security_id))
                    if price is None:
                        price = extract_quote_price(response, str(security_id))
                    if price is not None:
                        prices[str(security_id)] = price
            if prices:
                return prices
        except Exception as exc:
            last_error = exc
            STATE.last_quote_monotonic = time.monotonic()
            if _is_rate_limit(exc):
                time.sleep(1.0)
                continue
            break
    if last_error is not None:
        raise RuntimeError(_data_plan_hint(last_error))
    return {}


def fetch_expiry_list() -> list[str]:
    """Return expiry dates for the resolved index.

    Purpose:
        Let NEAREST and CONFIGURED expiry modes use Dhan's list.

    Inputs:
        Uses the index security id already stored on the state.

    Output:
        Sorted YYYY-MM-DD strings. Empty means the bot must not trade.

    Trading use:
        An expiry that is not on this list is rejected.
    """
    if BROKER is None:
        raise RuntimeError("Dhan is not connected")
    response = BROKER.dhan.expiry_list(
        under_security_id=int(STATE.underlying_security_id),
        under_exchange_segment=sdk_value("INDEX"),
    )
    payload = unwrap_sdk_data(response)
    if isinstance(payload, dict):
        for key in ("data", "expiryDates", "expiry_dates", "expiries"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise RuntimeError("Dhan expiry list had an unexpected shape")
    dates: list[str] = []
    for item in payload:
        text = str(item)[:10]
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            continue
        dates.append(text)
    dates = sorted(set(dates))
    STATE.expiries = dates
    return dates


def choose_expiry(expiries: list[str], today: date) -> str:
    """Pick NEAREST or the configured expiry. Do not trade a missing date."""
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    if not expiries:
        raise RuntimeError("Dhan returned no option expiries")
    if CONFIG.expiry_mode == "CONFIGURED":
        if CONFIG.expiry not in expiries:
            raise RuntimeError(
                f"Configured expiry {CONFIG.expiry} is not in the Dhan expiry list. No order was sent."
            )
        STATE.resolved_expiry = CONFIG.expiry
        return CONFIG.expiry
    upcoming = [item for item in expiries if datetime.strptime(item, "%Y-%m-%d").date() >= today]
    if not upcoming:
        raise RuntimeError("No upcoming expiry is available. No order was sent.")
    STATE.resolved_expiry = upcoming[0]
    return upcoming[0]


def _normalize_option_chain(payload: Any) -> tuple[float, list[dict[str, Any]]]:
    """Convert Dhan's strike-keyed chain into simple CE/PE rows."""
    if not isinstance(payload, dict):
        raise RuntimeError("Option chain response was not an object")
    spot = positive_float(payload.get("last_price"))
    if spot is None:
        raise RuntimeError("Option chain did not include the underlying price")
    chain = payload.get("oc") or payload.get("option_chain") or {}
    if not isinstance(chain, dict) or not chain:
        raise RuntimeError("Option chain was empty")
    rows: list[dict[str, Any]] = []
    for strike_key, legs in chain.items():
        try:
            strike = float(strike_key)
        except (TypeError, ValueError):
            continue
        if not isinstance(legs, dict):
            continue
        row: dict[str, Any] = {"strike": strike}
        for side in ("ce", "pe"):
            leg = legs.get(side) or {}
            if not isinstance(leg, dict):
                leg = {}
            security_id = leg.get("security_id")
            row[f"{side}_security_id"] = None if security_id is None else str(security_id)
            row[f"{side}_ltp"] = positive_float(leg.get("last_price"))
            row[f"{side}_bid"] = positive_float(leg.get("top_bid_price"))
            row[f"{side}_ask"] = positive_float(leg.get("top_ask_price"))
            row[f"{side}_oi"] = leg.get("oi")
        rows.append(row)
    rows.sort(key=lambda item: float(item["strike"]))
    if not rows:
        raise RuntimeError("Option chain did not contain any strikes")
    return spot, rows


def fetch_option_chain(expiry: str) -> tuple[float, list[dict[str, Any]]]:
    """Download one expiry chain and cache it.

    Purpose:
        Select a contract only when an entry needs it, not on every poll.

    Inputs:
        Expiry date YYYY-MM-DD.

    Output:
        Spot price from the chain and normalized strike rows.

    Trading use:
        Dhan allows a unique chain request about every 3 seconds. The cache
        records the fetch time so the next call waits when needed.
    """
    if BROKER is None:
        raise RuntimeError("Dhan is not connected")
    if STATE.chain_fetched_at is not None:
        elapsed = (now_in_tz() - STATE.chain_fetched_at).total_seconds()
        if elapsed < 3:
            time.sleep(3 - elapsed)
    response = BROKER.dhan.option_chain(
        under_security_id=int(STATE.underlying_security_id),
        under_exchange_segment=sdk_value("INDEX"),
        expiry=expiry,
    )
    spot, rows = _normalize_option_chain(unwrap_sdk_data(response))
    STATE.chain_rows = rows
    STATE.chain_spot = spot
    STATE.chain_expiry = expiry
    STATE.chain_fetched_at = now_in_tz()
    return spot, rows


# ============================================================
# OPTION SELECTION
# ============================================================

def select_option_contract(
    rows: list[dict[str, Any]],
    spot: float,
    option_type: str,
    strike_mode: str,
    strike_offset: int,
) -> OptionContract:
    """Choose CE or PE from listed strikes.

    Purpose:
        Map a bullish signal to a call and a bearish signal to a put.

    Inputs:
        Normalized chain, spot, CE/PE, ATM/ITM/OTM, and the strike step count.

    Output:
        OptionContract enriched from the security master.

    Trading use:
        ATM is the nearest listed strike that has the requested side. ITM and
        OTM move by real chain steps, not by a hardcoded point interval.
        Offset 0 on ITM/OTM means one step. The chosen id must exist in the master.
    """
    side = option_type.lower()
    usable = [row for row in rows if row.get(f"{side}_security_id")]
    if not usable:
        raise RuntimeError(f"The option chain has no {option_type} contracts")
    usable.sort(key=lambda row: float(row["strike"]))
    atm_index = min(range(len(usable)), key=lambda index: abs(float(usable[index]["strike"]) - spot))
    if strike_mode == "ATM":
        direction = 0
        steps = 0
    else:
        steps = max(int(strike_offset), 1)
        if option_type == "CE":
            direction = -1 if strike_mode == "ITM" else 1
        else:
            direction = 1 if strike_mode == "ITM" else -1
    chosen_index = atm_index + (direction * steps)
    if chosen_index < 0 or chosen_index >= len(usable):
        raise RuntimeError("Requested strike is outside the available option chain")
    chosen = usable[chosen_index]
    security_id = str(chosen[f"{side}_security_id"])
    return resolve_option_contract(security_id, chosen, option_type)


def order_quantity(contract: OptionContract) -> int:
    """Return lots times the contract lot size. Reject a bad multiple."""
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    quantity = CONFIG.lots * contract.lot_size
    if quantity < contract.lot_size or quantity % contract.lot_size != 0:
        raise RuntimeError(
            f"Quantity {quantity} is not a multiple of lot size {contract.lot_size}"
        )
    return quantity


def limit_price(side: str, contract: OptionContract, fallback_ltp: float | None) -> float:
    """Choose a limit price from the current ask (buy) or bid (sell).

    The chain quote is enough for the order that immediately follows a chain
    fetch. LTP is the fallback. The result is rounded to the contract tick.
    """
    if side == "BUY":
        raw = contract.ask or contract.ltp or fallback_ltp
    else:
        raw = contract.bid or contract.ltp or fallback_ltp
    price = positive_float(raw)
    if price is None:
        raise RuntimeError("No option premium is available for a limit order")
    return round_to_tick(price, contract.tick_size)


# ============================================================
# HALF TREND INDICATOR
# ============================================================

def calculate_atr(frame: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Wilder ATR.

    Purpose:
        Build the Half Trend channel width. The signal itself uses the line,
        not the channel.

    Inputs:
        OHLC frame and the ATR length. The length is fixed at 100 so the
        published Half Trend formula stays intact without another config knob.

    Output:
        Series aligned to the frame. Early values are NaN until the length is filled.

    Trading use:
        This is the standard Wilder average, not a claim about Dhan's private
        indicator code. Channel values are informational.
    """
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = pd.Series(float("nan"), index=frame.index, dtype="float64")
    if len(true_range) < period:
        return atr
    atr.iloc[period - 1] = float(true_range.iloc[:period].mean())
    for index in range(period, len(true_range)):
        previous = float(atr.iloc[index - 1])
        atr.iloc[index] = ((previous * (period - 1)) + float(true_range.iloc[index])) / period
    return atr


def _latest_extreme(values: list[float], find_max: bool) -> float:
    """Return the newest max or min. Ties use the later bar, matching highestbars."""
    best_index = 0
    best_value = values[0]
    for index, value in enumerate(values):
        if find_max and value >= best_value:
            best_value = value
            best_index = index
        if not find_max and value <= best_value:
            best_value = value
            best_index = index
    return values[best_index]


def calculate_half_trend(
    frame: pd.DataFrame,
    amplitude: int,
    channel_deviation: float,
) -> pd.DataFrame:
    """Calculate a transparent Everget-style Half Trend line.

    Purpose:
        Produce the line used by the candle-close signal.

    Inputs:
        Confirmed OHLC rows, amplitude, and channel deviation.

    Output:
        Copy of the frame with half_trend, ht_trend, ht_upper, and ht_lower.
        Each row uses only that row and older rows.

    Trading use:
        A confirmed close crossing above the line is bullish. A cross below is
        bearish. This is a published-style formula. It is not documented as a
        byte-for-byte copy of Dhan's internal Half Trend.

    Formula, bar by bar:
        high/low window = last `amplitude` bars
        high_ma / low_ma = simple average of that window
        ATR channel uses Wilder ATR(100) / 2, times channel_deviation
        Trend 0 keeps the line at a rising max-low (support-style up line)
        Trend 1 keeps the line at a falling min-high (resistance-style down line)
        The line flips when the averages and the close break the tracked extreme
    """
    data = frame.reset_index(drop=True).copy()
    if data.empty:
        data["half_trend"] = []
        data["ht_trend"] = []
        data["ht_upper"] = []
        data["ht_lower"] = []
        return data
    atr = calculate_atr(data, ATR_PERIOD).tolist()
    highs = [float(value) for value in data["high"].tolist()]
    lows = [float(value) for value in data["low"].tolist()]
    closes = [float(value) for value in data["close"].tolist()]
    trend = 0
    next_trend = 0
    max_low = lows[0]
    min_high = highs[0]
    up: float | None = None
    down: float | None = None
    previous_trend: int | None = None
    half: list[float] = []
    trends: list[int] = []
    upper: list[float] = []
    lower: list[float] = []
    for index in range(len(data)):
        start = max(0, index - amplitude + 1)
        window_high = highs[start : index + 1]
        window_low = lows[start : index + 1]
        high_price = _latest_extreme(window_high, find_max=True)
        low_price = _latest_extreme(window_low, find_max=False)
        enough = (index + 1) >= amplitude
        high_ma = sum(window_high[-amplitude:]) / amplitude if enough else None
        low_ma = sum(window_low[-amplitude:]) / amplitude if enough else None
        previous_low = lows[index - 1] if index else lows[0]
        previous_high = highs[index - 1] if index else highs[0]
        if next_trend == 1:
            max_low = max(low_price, max_low)
            if (
                enough
                and high_ma is not None
                and high_ma < max_low
                and closes[index] < previous_low
            ):
                trend = 1
                next_trend = 0
                min_high = high_price
        else:
            min_high = min(high_price, min_high)
            if (
                enough
                and low_ma is not None
                and low_ma > min_high
                and closes[index] > previous_high
            ):
                trend = 0
                next_trend = 1
                max_low = low_price
        if trend == 0:
            if previous_trend is not None and previous_trend != 0:
                up = down if down is not None else up
            else:
                up = max_low if up is None else max(max_low, up)
            line = float(up)
        else:
            if previous_trend is not None and previous_trend != 1:
                down = up if up is not None else down
            else:
                down = min_high if down is None else min(min_high, down)
            line = float(down)
        previous_trend = trend
        half.append(line)
        trends.append(trend)
        atr_value = atr[index]
        if atr_value is None or (isinstance(atr_value, float) and math.isnan(atr_value)):
            upper.append(float("nan"))
            lower.append(float("nan"))
        else:
            deviation = channel_deviation * (float(atr_value) / 2.0)
            upper.append(line + deviation)
            lower.append(line - deviation)
    data["half_trend"] = half
    data["ht_trend"] = trends
    data["ht_upper"] = upper
    data["ht_lower"] = lower
    data["atr"] = atr
    return data


def generate_signal(candles: pd.DataFrame) -> str | None:
    """Return BUY_CE, BUY_PE, or None from the last two confirmed candles.

    Purpose:
        Turn a close crossing the Half Trend line into an option-buying signal.

    Inputs:
        Frame that already contains close and half_trend.

    Output:
        BUY_CE when price crosses above the line.
        BUY_PE when price crosses below the line.
        None when there is no new cross.

    Trading use:
        The caller must run this only for a new confirmed candle. Staying above
        the line is not another buy. The bot buys options; it does not buy the index.
    """
    if candles is None or len(candles) < 2:
        return None
    if "half_trend" not in candles.columns:
        return None
    previous = candles.iloc[-2]
    current = candles.iloc[-1]
    values = [
        float(previous["close"]),
        float(previous["half_trend"]),
        float(current["close"]),
        float(current["half_trend"]),
    ]
    if any(math.isnan(value) for value in values):
        return None
    previous_close, previous_line, close, line = values
    if previous_close <= previous_line and close > line:
        return "BUY_CE"
    if previous_close >= previous_line and close < line:
        return "BUY_PE"
    return None


def describe_candle_side(candles: pd.DataFrame) -> str:
    """Explain the latest close versus the line, including a non-cross."""
    signal = generate_signal(candles)
    if signal == "BUY_CE":
        return "Confirmed close crossed above Half Trend"
    if signal == "BUY_PE":
        return "Confirmed close crossed below Half Trend"
    if candles is None or candles.empty or "half_trend" not in candles.columns:
        return "Half Trend is not ready"
    close = float(candles.iloc[-1]["close"])
    line = float(candles.iloc[-1]["half_trend"])
    if math.isnan(close) or math.isnan(line):
        return "Half Trend is not ready"
    if close > line:
        return "Close is above Half Trend without a new cross"
    if close < line:
        return "Close is below Half Trend without a new cross"
    return "Close equals Half Trend"


def is_new_confirmed_candle(candle_timestamp: datetime | None = None) -> bool:
    """True when this confirmed candle has not been evaluated yet.

    Purpose:
        Separate the 5-second poll from the 5-minute signal.

    Inputs:
        Candle open time. When omitted, the state's latest candle is used.

    Output:
        False for the same timestamp that was already evaluated.

    Trading use:
        Prevents one bullish candle from buying a call on every poll.
    """
    timestamp = STATE.candle_time if candle_timestamp is None else candle_timestamp
    if timestamp is None:
        return False
    return timestamp != STATE.last_evaluated_candle


# ============================================================
# STRATEGY
# ============================================================

def signal_label(signal: str | None) -> str:
    """Map an internal signal to the dashboard word."""
    return {"BUY_CE": "BULLISH", "BUY_PE": "BEARISH"}.get(signal or "", "NONE")


def option_side(signal: str) -> str:
    """BUY_CE selects a call. BUY_PE selects a put."""
    if signal == "BUY_CE":
        return "CE"
    if signal == "BUY_PE":
        return "PE"
    raise ValueError(f"Unsupported signal: {signal}")


def next_order_tag(kind: str) -> str:
    """Build a short order tag that identifies this algo and this order.

    Tags look like HTE26092801. Restart logic treats an HT prefix as this bot.
    """
    if STATE.trading_date is None:
        raise RuntimeError("Trading date is not set")
    sequence = STATE.orders_today + STATE.exit_orders_today + 1
    return f"{ALGO_TAG_PREFIX}{kind}{STATE.trading_date.strftime('%y%m%d')}{sequence:02d}"


def refresh_indicator(now: datetime) -> None:
    """Recalculate Half Trend when a new candle is due."""
    if CONFIG is None:
        return
    if candles_need_refresh(now):
        fetch_underlying_candles(now)
    if STATE.candles is None or STATE.candles.empty:
        STATE.half_trend_value = None
        STATE.signal_reason = "No confirmed candles yet"
        return
    calculated = calculate_half_trend(
        STATE.candles,
        CONFIG.amplitude,
        CONFIG.channel_deviation,
    )
    STATE.candles = calculated
    STATE.half_trend_value = float(calculated.iloc[-1]["half_trend"])
    STATE.candle_time = calculated.iloc[-1]["timestamp"]
    STATE.last_confirmed_candle = STATE.candle_time


def consider_entry(signal: str, candle_time: datetime) -> None:
    """Enter once for a fresh signal, or remember it when the block is temporary."""
    if STATE.last_signal_candle == candle_time or STATE.entry_skip_candle == candle_time:
        return
    allowed, reason = validate_entry(signal)
    if not allowed:
        note(f"Entry skipped: {reason}")
        if reason in RETRYABLE_ENTRY_BLOCKS:
            STATE.deferred_signal = signal
            STATE.deferred_candle = candle_time
        else:
            STATE.last_signal_candle = candle_time
            STATE.deferred_signal = None
        return
    STATE.deferred_signal = None
    place_entry_order(signal, candle_time)
    STATE.last_signal_candle = candle_time


def process_signal() -> None:
    """Evaluate a new confirmed candle and maybe enter or exit on the opposite side.

    An opposite-signal exit does not reverse in the same cycle. A deferred
    signal from earlier in the same candle can still enter once the block clears.
    """
    if CONFIG is None or STATE.candles is None or STATE.candle_time is None:
        return
    candle_time = STATE.candle_time
    if STATE.deferred_candle is not None and STATE.deferred_candle != candle_time:
        STATE.deferred_signal = None
        STATE.deferred_candle = None
    if is_new_confirmed_candle(candle_time):
        STATE.last_evaluated_candle = candle_time
        signal = generate_signal(STATE.candles)
        STATE.signal_reason = describe_candle_side(STATE.candles)
        STATE.signal_label = signal_label(signal)
        if signal is not None:
            STATE.last_signal = signal
        if (
            signal is not None
            and STATE.data_status == "OK"
            and CONFIG.exit_on_opposite_signal
            and check_opposite_signal(signal)
        ):
            close_current_position("OPPOSITE_SIGNAL")
            STATE.entry_skip_candle = candle_time
            STATE.last_signal_candle = candle_time
            return
        if signal is not None and STATE.status == "FLAT":
            consider_entry(signal, candle_time)
            return
    if (
        STATE.status == "FLAT"
        and STATE.deferred_signal is not None
        and STATE.deferred_candle == candle_time
        and STATE.last_signal_candle != candle_time
        and STATE.entry_skip_candle != candle_time
    ):
        consider_entry(STATE.deferred_signal, candle_time)


# ============================================================
# PAPER EXECUTION
# ============================================================

def _assert_paper() -> None:
    """Stop a paper function from running in LIVE mode."""
    if CONFIG is None or CONFIG.trading_mode != "PAPER":
        raise RuntimeError("Paper execution was called while trading_mode is not PAPER")


def paper_enter(contract: OptionContract, signal: str, price: float) -> Position:
    """Simulate a buy fill at the current option premium.

    Purpose:
        Follow the same entry path as LIVE without sending an order.

    Inputs:
        Selected contract, BUY_CE or BUY_PE, and the simulated fill price.

    Output:
        The open paper position stored on the bot state.

    Trading use:
        This function must never call the broker order API. The fill is the
        premium already chosen for the order preview.
    """
    _assert_paper()
    if STATE.status != "FLAT" or STATE.position is not None:
        raise RuntimeError("Paper entry refused because the bot is not flat")
    quantity = order_quantity(contract)
    tp_price, sl_price = calculate_tp_sl(price, contract.tick_size)
    filled_at = now_in_tz()
    position = Position(
        security_id=contract.security_id,
        trading_symbol=contract.trading_symbol,
        option_type=contract.option_type,
        strike=contract.strike,
        expiry=contract.expiry,
        quantity=quantity,
        lot_size=contract.lot_size,
        tick_size=contract.tick_size,
        entry_price=price,
        product_type=CONFIG.product_type if CONFIG else "INTRADAY",
        exchange_segment=contract.exchange_segment,
        entry_time=filled_at,
        order_id="PAPER",
        order_tag=next_order_tag("E"),
        signal=signal,
        tp_price=tp_price,
        sl_price=sl_price,
    )
    STATE.position = position
    STATE.last_option_contract = contract
    STATE.option_ltp = price
    STATE.status = "LONG_OPTION"
    STATE.orders_today += 1
    STATE.unrealized_pnl = 0.0
    note(f"PAPER ENTRY {position.trading_symbol} qty {quantity} @ {format_inr(price)}")
    return position


def paper_exit(reason: str, price: float) -> TradeRecord:
    """Simulate the sell that closes the paper option.

    Purpose:
        Complete TP, SL, opposite-signal, session, and manual exits without
        a broker order.

    Inputs:
        Exit reason and the current option premium.

    Output:
        The stored trade record. Gross P&L is (exit - entry) times quantity.

    Trading use:
        The position is cleared before the function returns, so a second exit
        cannot run in the same cycle.
    """
    _assert_paper()
    position = get_current_position()
    if position is None:
        raise RuntimeError("Paper exit found no position")
    trade = _record_exit(position, price, reason, "PAPER")
    note(
        f"PAPER EXIT {position.trading_symbol} @ {format_inr(price)} "
        f"gross {format_inr(trade.pnl)} ({reason})"
    )
    return trade


# ============================================================
# LIVE EXECUTION
# ============================================================

def _assert_live() -> None:
    """Stop a live order function from running in PAPER mode."""
    if CONFIG is None or CONFIG.trading_mode != "LIVE":
        raise RuntimeError("LIVE order blocked because trading_mode is not LIVE")
    if BROKER is None:
        raise RuntimeError("LIVE order blocked because Dhan is not connected")


def build_order_preview(
    contract: OptionContract,
    side: str,
    quantity: int,
    price: float,
    reason: str,
    tp_price: float | None,
    sl_price: float | None,
) -> str:
    """Readable order ticket printed before a LIVE submit and before a paper fill."""
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    notional = price * quantity if price else 0.0
    lines = [
        "--- ORDER PREVIEW ---",
        f"ACTION:        {side}",
        f"UNDERLYING:    {CONFIG.underlying}",
        f"OPTION SYMBOL: {contract.trading_symbol}",
        f"SECURITY ID:   {contract.security_id}",
        f"EXPIRY:        {contract.expiry}",
        f"STRIKE:        {contract.strike:g}",
        f"CE/PE:         {contract.option_type}",
        f"QUANTITY:      {quantity}",
        f"LOT SIZE:      {contract.lot_size}",
        f"LOTS:          {CONFIG.lots}",
        f"ORDER TYPE:    {CONFIG.order_type}",
        f"PRODUCT TYPE:  {CONFIG.product_type}",
        f"PRICE:         {format_inr(price) if CONFIG.order_type == 'LIMIT' else 'MARKET / MPP'}",
        f"REASON:        {reason}",
        f"CURRENT LTP:   {format_inr(contract.ltp or STATE.option_ltp)}",
        f"TP:            {format_inr(tp_price)}",
        f"SL:            {format_inr(sl_price)}",
    ]
    if notional:
        lines.append(f"NOTIONAL:      {format_inr(notional)}")
    if notional > NOTIONAL_WARNING_RS:
        lines.append("WARNING:       Notional exceeds ₹50,000")
    lines.append("--------------------")
    return "\n".join(lines)


def _place_live_order(
    contract: OptionContract,
    side: str,
    quantity: int,
    price: float,
    tag: str,
) -> str:
    """Submit one Dhan order and return its id. Caller must already be in LIVE mode."""
    _assert_live()
    if CONFIG is None or BROKER is None:
        raise RuntimeError("LIVE order is not available")
    segment_name = {"BSE_FNO": "BSE_FNO", "NSE_FNO": "NSE_FNO"}[contract.exchange_segment]
    product_name = {"INTRADAY": "INTRA", "MARGIN": "MARGIN"}[CONFIG.product_type]
    order_name = {"LIMIT": "LIMIT", "MARKET": "MARKET"}[CONFIG.order_type]
    response = BROKER.dhan.place_order(
        security_id=str(contract.security_id),
        exchange_segment=sdk_value(segment_name),
        transaction_type=sdk_value(side),
        quantity=int(quantity),
        order_type=sdk_value(order_name),
        product_type=sdk_value(product_name),
        price=0.0 if CONFIG.order_type == "MARKET" else float(price),
        trigger_price=0.0,
        disclosed_quantity=0,
        after_market_order=False,
        validity=sdk_value("DAY"),
        amo_time="OPEN",
        tag=tag,
    )
    order_id = extract_order_id(response)
    if not order_id:
        raise RuntimeError(f"Dhan accepted no order id for {side} {contract.trading_symbol}")
    return order_id


def live_enter(contract: OptionContract, signal: str, price: float) -> None:
    """Submit a live buy and wait for TRADED before a position exists.

    Purpose:
        Send one real entry order after the preview.

    Inputs:
        Contract, signal, and limit price. Market orders pass price 0 to Dhan.

    Output:
        None. State becomes ENTRY_PENDING, then LONG_OPTION only after TRADED.

    Trading use:
        A slow or pending order does not create a second buy. API success is
        not treated as a fill.
    """
    _assert_live()
    if STATE.status != "FLAT" or STATE.position is not None or STATE.pending_order_id:
        raise RuntimeError("Live entry refused because another order or position is active")
    quantity = order_quantity(contract)
    tp_price, sl_price = calculate_tp_sl(price if price > 0 else (contract.ltp or price), contract.tick_size)
    tag = next_order_tag("E")
    preview = build_order_preview(contract, "BUY", quantity, price, signal, tp_price, sl_price)
    print(preview)
    for line in preview.splitlines():
        STATE.events.appendleft(line)
    STATE.status = "ENTRY_PENDING"
    STATE.pending_kind = "ENTRY"
    STATE.pending_tag = tag
    STATE.last_option_contract = contract
    STATE.entry_lookup_attempts = 0
    try:
        order_id = _place_live_order(contract, "BUY", quantity, price, tag)
    except Exception:
        recovered = _find_order_by_tag(tag)
        if recovered is None:
            STATE.status = "FLAT"
            STATE.pending_kind = None
            STATE.pending_tag = None
            raise
        order_id = recovered
        note("Entry request returned an error. Tracking the tagged order instead of sending another.")
    STATE.pending_order_id = order_id
    note(f"LIVE ENTRY SUBMITTED {contract.trading_symbol} order {order_id}")
    check_order_status()


def live_exit(reason: str) -> None:
    """Submit one live sell for this algo's option.

    Purpose:
        Close the current long option without touching unrelated positions.

    Inputs:
        Exit reason such as TP, SL, or SESSION_END.

    Output:
        None. State becomes EXIT_PENDING until the sell is TRADED.

    Trading use:
        A second sell is refused while the first exit is still pending.
    """
    _assert_live()
    position = get_current_position()
    if position is None:
        return
    if STATE.status == "EXIT_PENDING":
        return
    contract = OptionContract(
        security_id=position.security_id,
        trading_symbol=position.trading_symbol,
        option_type=position.option_type,
        strike=position.strike,
        expiry=position.expiry,
        lot_size=position.lot_size,
        tick_size=position.tick_size,
        ltp=STATE.option_ltp,
        bid=None,
        ask=None,
        exchange_segment=position.exchange_segment,
    )
    price = 0.0
    if CONFIG is not None and CONFIG.order_type == "LIMIT":
        price = limit_price("SELL", contract, STATE.option_ltp)
    tag = next_order_tag("X")
    preview = build_order_preview(
        contract,
        "SELL",
        position.quantity,
        price,
        reason,
        position.tp_price,
        position.sl_price,
    )
    print(preview)
    STATE.status = "EXIT_PENDING"
    STATE.pending_kind = "EXIT"
    STATE.pending_exit_reason = reason
    STATE.pending_tag = tag
    STATE.exit_lookup_attempts = 0
    try:
        order_id = _place_live_order(contract, "SELL", position.quantity, price, tag)
    except Exception:
        recovered = _find_order_by_tag(tag)
        if recovered is None:
            note("Exit submit failed and no tagged exit order was found. No second sell was sent.")
            STATE.exit_lookup_attempts += 1
            if STATE.exit_lookup_attempts >= 3:
                raise RuntimeError("Live exit could not be confirmed. Bot will stop without another sell.")
            return
        order_id = recovered
    STATE.pending_order_id = order_id
    note(f"LIVE EXIT SUBMITTED {position.trading_symbol} order {order_id} ({reason})")
    check_order_status()


def extract_order_id(response: Any) -> str:
    """Read an order id from the several shapes Dhan uses."""
    try:
        payload = unwrap_sdk_data(response)
    except Exception:
        payload = response
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("orderId", "order_id", "orderNo"):
            if payload.get(key):
                return str(payload[key])
        nested = payload.get("data")
        if isinstance(nested, dict):
            return extract_order_id(nested)
    return ""


def parse_order_status(response: Any) -> str:
    """Return the broker order status, uppercased."""
    payload: Any
    try:
        payload = unwrap_sdk_data(response)
    except Exception:
        payload = response
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict) and "orderStatus" not in payload:
        for key in ("data", "order"):
            if isinstance(payload.get(key), (dict, list)):
                return parse_order_status(payload[key])
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("orderStatus") or payload.get("status") or "").upper()


def parse_fill_price(response: Any, fallback: float) -> float:
    """Use the average traded price when Dhan provides one."""
    try:
        payload = unwrap_sdk_data(response)
    except Exception:
        payload = response
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict):
        for key in ("averageTradedPrice", "avgPrice", "tradedPrice", "price"):
            price = positive_float(payload.get(key))
            if price is not None:
                return price
        nested = payload.get("data")
        if isinstance(nested, (dict, list)):
            return parse_fill_price(nested, fallback)
    return fallback


def _order_rows(response: Any) -> list[dict[str, Any]]:
    """Normalize an order-list response into dictionaries."""
    try:
        payload = unwrap_sdk_data(response)
    except Exception:
        payload = response
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "orders"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    return []


def _position_rows(response: Any) -> list[dict[str, Any]]:
    """Normalize a position response into dictionaries."""
    try:
        payload = unwrap_sdk_data(response)
    except Exception:
        payload = response
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "positions", "netPositions"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    return []


def _row_security_id(row: dict[str, Any]) -> str:
    """Read a security id from an order or position row."""
    for key in ("securityId", "security_id", "SEM_SMST_SECURITY_ID"):
        if row.get(key) is not None:
            return str(row[key]).strip()
    return ""


def _row_tag(row: dict[str, Any]) -> str:
    """Read the algo tag from an order row."""
    for key in ("tag", "correlationId", "correlation_id"):
        if row.get(key):
            return str(row[key])
    return ""


def _row_quantity(row: dict[str, Any]) -> int:
    """Read a net or filled quantity. Missing values become zero."""
    for key in ("netQty", "filledQty", "quantity", "tradedQuantity"):
        if row.get(key) is None or row.get(key) == "":
            continue
        try:
            return int(float(row[key]))
        except (TypeError, ValueError):
            continue
    return 0


def _is_open_fno(row: dict[str, Any]) -> bool:
    """True for a non-zero NSE/BSE F&O position."""
    segment = str(row.get("exchangeSegment") or row.get("exchange_segment") or "").upper()
    if "FNO" not in segment and segment not in {"NSE_FNO", "BSE_FNO"}:
        return False
    return _row_quantity(row) != 0


def _find_order_by_tag(tag: str) -> str | None:
    """Look up one of this bot's orders by tag. Used when a submit response is unclear."""
    if BROKER is None or not tag:
        return None
    rows = _order_rows(BROKER.dhan.get_order_list())
    for row in rows:
        if _row_tag(row) == tag:
            order_id = row.get("orderId") or row.get("order_id")
            if order_id:
                return str(order_id)
    return None


def check_order_status() -> str:
    """Advance ENTRY_PENDING or EXIT_PENDING from the broker order.

    Purpose:
        Create or remove a position only after TRADED.

    Inputs:
        Uses the pending order id on the state.

    Output:
        The latest status string. Pending leaves the state unchanged.

    Trading use:
        REJECTED, CANCELLED, and EXPIRED clear a pending entry. They do not
        send a replacement. A pending exit is never replaced automatically.
    """
    if CONFIG is None or CONFIG.trading_mode != "LIVE" or BROKER is None:
        return ""
    if STATE.pending_kind is None:
        return ""
    if not STATE.pending_order_id and STATE.pending_tag:
        STATE.pending_order_id = _find_order_by_tag(STATE.pending_tag)
    if not STATE.pending_order_id:
        if STATE.pending_kind == "ENTRY":
            STATE.entry_lookup_attempts += 1
        else:
            STATE.exit_lookup_attempts += 1
        if STATE.entry_lookup_attempts >= 3 and STATE.pending_kind == "ENTRY":
            STATE.status = "FLAT"
            STATE.pending_kind = None
            STATE.pending_tag = None
            note("Pending entry disappeared. No replacement order was sent.")
        if STATE.exit_lookup_attempts >= 3 and STATE.pending_kind == "EXIT":
            STATE.unsafe_to_close = True
            note("Exit order was not found. No second sell will be sent.")
            graceful_shutdown("CRITICAL")
        return ""
    response = BROKER.dhan.get_order_by_id(STATE.pending_order_id)
    status = parse_order_status(response)
    if status == "TRADED":
        if STATE.pending_kind == "ENTRY":
            _finalize_live_entry(response)
        elif STATE.pending_kind == "EXIT":
            _finalize_live_exit(response)
    elif status in {"REJECTED", "CANCELLED", "EXPIRED"}:
        kind = STATE.pending_kind
        STATE.pending_order_id = None
        STATE.pending_tag = None
        STATE.pending_kind = None
        if kind == "ENTRY":
            STATE.status = "FLAT"
            note(f"Entry order {status}. State returned to FLAT. No new order was sent.")
        else:
            STATE.status = "LONG_OPTION" if STATE.position is not None else "FLAT"
            note(f"Exit order {status}. Position was kept. No second exit was sent.")
    elif status:
        note(f"Order {STATE.pending_order_id} is {status}")
    return status


def _finalize_live_entry(response: Any) -> None:
    """Create the long-option state from a traded buy."""
    contract = STATE.last_option_contract
    if contract is None or CONFIG is None:
        raise RuntimeError("Traded entry has no selected contract")
    fallback = contract.ltp or STATE.underlying_ltp or 0.0
    fill = parse_fill_price(response, float(fallback))
    tp_price, sl_price = calculate_tp_sl(fill, contract.tick_size)
    position = Position(
        security_id=contract.security_id,
        trading_symbol=contract.trading_symbol,
        option_type=contract.option_type,
        strike=contract.strike,
        expiry=contract.expiry,
        quantity=order_quantity(contract),
        lot_size=contract.lot_size,
        tick_size=contract.tick_size,
        entry_price=fill,
        product_type=CONFIG.product_type,
        exchange_segment=contract.exchange_segment,
        entry_time=now_in_tz(),
        order_id=STATE.pending_order_id or "",
        order_tag=STATE.pending_tag or "",
        signal=STATE.last_signal or "",
        tp_price=tp_price,
        sl_price=sl_price,
    )
    STATE.position = position
    STATE.option_ltp = fill
    STATE.orders_today += 1
    STATE.status = "LONG_OPTION"
    STATE.pending_order_id = None
    STATE.pending_kind = None
    STATE.pending_tag = None
    STATE.unrealized_pnl = 0.0
    note(f"LIVE ENTRY FILLED {position.trading_symbol} @ {format_inr(fill)}")


def _finalize_live_exit(response: Any) -> None:
    """Record a traded sell and return the bot to flat."""
    position = get_current_position()
    if position is None:
        STATE.status = "FLAT"
        STATE.pending_order_id = None
        STATE.pending_kind = None
        return
    fill = parse_fill_price(response, STATE.option_ltp or position.entry_price)
    reason = STATE.pending_exit_reason or "EXIT"
    trade = _record_exit(position, fill, reason, STATE.pending_order_id or "")
    note(
        f"LIVE EXIT FILLED {trade.symbol} @ {format_inr(fill)} "
        f"gross {format_inr(trade.pnl)} ({reason})"
    )


def _cancel_pending_entry() -> None:
    """Cancel a not-yet-filled entry during shutdown. Do not cancel an exit."""
    if BROKER is None or CONFIG is None or CONFIG.trading_mode != "LIVE":
        return
    if STATE.pending_kind != "ENTRY" or not STATE.pending_order_id:
        return
    try:
        BROKER.dhan.cancel_order(STATE.pending_order_id)
        note(f"Cancelled pending entry {STATE.pending_order_id}")
    except Exception as exc:
        note(f"Could not cancel pending entry: {exc}")
    check_order_status()


def reconcile_live_positions() -> None:
    """Adopt this algo's open option, or stop if another F&O position is unexplained.

    Purpose:
        Avoid a second entry on top of a position from an earlier run, and
        avoid closing a position that this algo did not open.

    Inputs:
        Live order list and position book.

    Output:
        None. May set LONG_OPTION or raise RuntimeError.

    Trading use:
        A position is adopted only when today's order tag starts with HT and
        the security id matches. Any other open F&O quantity blocks trading.
    """
    if BROKER is None or CONFIG is None or MASTER is None:
        raise RuntimeError("Cannot reconcile without Dhan and the security master")
    orders = _order_rows(BROKER.dhan.get_order_list())
    positions = _position_rows(BROKER.dhan.get_positions())
    tagged = [
        row for row in orders
        if _row_tag(row).startswith(ALGO_TAG_PREFIX)
    ]
    traded_entries = [
        row for row in tagged
        if str(row.get("transactionType") or row.get("transaction_type") or "").upper() == "BUY"
        and str(row.get("orderStatus") or "").upper() == "TRADED"
    ]
    STATE.orders_today = len(traded_entries)
    pending = [
        row for row in tagged
        if str(row.get("orderStatus") or "").upper() in {"PENDING", "TRANSIT", "PART_TRADED", "OPEN"}
    ]
    open_fno = [row for row in positions if _is_open_fno(row)]
    tagged_ids = {_row_security_id(row) for row in tagged if _row_security_id(row)}
    unexplained = [row for row in open_fno if _row_security_id(row) not in tagged_ids]
    attributable = [row for row in open_fno if _row_security_id(row) in tagged_ids]
    if unexplained:
        print("EXISTING LIVE POSITION DETECTED")
        print("An open F&O position is not tagged by this algo. The bot will not trade or close it.")
        raise RuntimeError("Unattributed live F&O position")
    if len(attributable) > 1:
        print("EXISTING LIVE POSITION DETECTED")
        raise RuntimeError("More than one algo option position is open")
    if pending and not attributable:
        row = pending[0]
        side = str(row.get("transactionType") or "").upper()
        STATE.pending_order_id = str(row.get("orderId") or "")
        STATE.pending_tag = _row_tag(row)
        STATE.pending_kind = "EXIT" if side == "SELL" else "ENTRY"
        STATE.status = "EXIT_PENDING" if side == "SELL" else "ENTRY_PENDING"
        print("EXISTING LIVE POSITION DETECTED")
        note(f"Tracking existing {STATE.pending_kind} order {STATE.pending_order_id}")
        return
    if not attributable:
        return
    row = attributable[0]
    if _row_quantity(row) < 0:
        print("EXISTING LIVE POSITION DETECTED")
        raise RuntimeError("Algo tag matches a short option. This bot only manages long options.")
    security_id = _row_security_id(row)
    chain_stub = {"strike": positive_float(row.get("drvStrikePrice")) or 0.0}
    option_type = str(row.get("drvOptionType") or "").upper()
    if option_type not in {"CE", "PE"}:
        master_row = MASTER[MASTER["SEM_SMST_SECURITY_ID"].astype(str).str.strip() == security_id]
        if master_row.empty:
            raise RuntimeError("Could not identify the existing option type")
        option_type = str(master_row.iloc[0].get("SEM_OPTION_TYPE", "")).upper()
    if option_type not in {"CE", "PE"}:
        raise RuntimeError("Existing position is not a CE or PE this bot can manage")
    contract = resolve_option_contract(security_id, chain_stub, option_type)
    entry = None
    for key in ("buyAvg", "costPrice", "averagePrice", "netAvg"):
        entry = positive_float(row.get(key))
        if entry is not None:
            break
    if entry is None:
        for order in reversed(traded_entries):
            if _row_security_id(order) == security_id:
                entry = positive_float(order.get("averageTradedPrice") or order.get("price"))
                if entry is not None:
                    break
    if entry is None:
        raise RuntimeError("Existing algo position has no entry price. The bot will not guess.")
    quantity = _row_quantity(row)
    if quantity % contract.lot_size != 0:
        raise RuntimeError("Existing position quantity is not a multiple of the lot size")
    tp_price, sl_price = calculate_tp_sl(entry, contract.tick_size)
    print("EXISTING LIVE POSITION DETECTED")
    STATE.position = Position(
        security_id=contract.security_id,
        trading_symbol=contract.trading_symbol,
        option_type=contract.option_type,
        strike=contract.strike,
        expiry=contract.expiry,
        quantity=quantity,
        lot_size=contract.lot_size,
        tick_size=contract.tick_size,
        entry_price=entry,
        product_type=CONFIG.product_type,
        exchange_segment=contract.exchange_segment,
        entry_time=now_in_tz(),
        order_id="RESTORED",
        order_tag=ALGO_TAG_PREFIX,
        signal="BUY_CE" if option_type == "CE" else "BUY_PE",
        tp_price=tp_price,
        sl_price=sl_price,
    )
    STATE.status = "LONG_OPTION"
    STATE.last_option_contract = contract
    note(f"Restored {contract.trading_symbol} qty {quantity} @ {format_inr(entry)}")


def refresh_broker_position() -> None:
    """If the broker is already flat, drop the local position instead of selling again."""
    if CONFIG is None or BROKER is None or CONFIG.trading_mode != "LIVE" or STATE.position is None:
        return
    if STATE.status != "LONG_OPTION":
        return
    moment = now_in_tz()
    if STATE.last_position_refresh is not None:
        if (moment - STATE.last_position_refresh).total_seconds() < 60:
            return
    STATE.last_position_refresh = moment
    rows = _position_rows(BROKER.dhan.get_positions())
    match = [
        row for row in rows
        if _row_security_id(row) == STATE.position.security_id and _is_open_fno(row)
    ]
    if not match:
        note("Broker shows this option flat. Local state was cleared. No extra exit was sent.")
        STATE.position = None
        STATE.status = "FLAT"
        STATE.option_ltp = None
        STATE.unrealized_pnl = 0.0
        return
    broker_qty = _row_quantity(match[0])
    if broker_qty != STATE.position.quantity:
        STATE.unsafe_to_close = True
        raise RuntimeError(
            f"Broker quantity {broker_qty} does not match the algo quantity {STATE.position.quantity}. "
            "No order was sent."
        )


# ============================================================
# POSITION MANAGEMENT
# ============================================================

def get_current_position() -> Position | None:
    """Return the option this algo currently holds, if any.

    Purpose:
        Give every exit path one place to look.

    Inputs:
        None. Reads memory state.

    Output:
        The Position, or None when flat.

    Trading use:
        This is not the whole Dhan position book. Unrelated positions are ignored.
    """
    return STATE.position


def calculate_position_pnl(position: Position, mark_price: float) -> float:
    """Gross open P&L for a bought option: (mark - entry) times quantity.

    Charges, taxes, and slippage are not included.
    """
    return (mark_price - position.entry_price) * position.quantity


def calculate_combined_algo_pnl() -> float:
    """Realized gross P&L plus the open option's unrealized gross P&L.

    Purpose:
        Drive the daily loss check and the dashboard.

    Inputs:
        In-memory realized and unrealized amounts for this process.

    Output:
        Combined gross rupees. This is not account-level Dhan P&L.

    Trading use:
        Only trades this bot recorded are included.
    """
    return STATE.realized_pnl + STATE.unrealized_pnl


def calculate_tp_sl(entry_price: float, tick_size: float) -> tuple[float | None, float | None]:
    """Turn the configured TP and SL into premium prices.

    Purpose:
        Keep targets on the option premium.

    Inputs:
        Fill price and the contract tick size.

    Output:
        (take-profit price or None, stop-loss price or None), rounded to the tick.

    Trading use:
        Percent 20 on a ₹100 premium is ₹120. Percent 10 is ₹90.
        Absolute values are rupees added or subtracted. Disabled sides return None.
    """
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    take_profit: float | None = None
    stop_loss: float | None = None
    if CONFIG.tp_enabled:
        if CONFIG.tp_type == "PERCENT":
            take_profit = entry_price * (1 + (CONFIG.tp_value / 100.0))
        else:
            take_profit = entry_price + CONFIG.tp_value
        take_profit = round_to_tick(take_profit, tick_size)
    if CONFIG.sl_enabled:
        if CONFIG.sl_type == "PERCENT":
            stop_loss = entry_price * (1 - (CONFIG.sl_value / 100.0))
        else:
            stop_loss = entry_price - CONFIG.sl_value
        stop_loss = round_to_tick(stop_loss, tick_size)
        if stop_loss <= 0:
            raise RuntimeError("Stop loss rounded to a non-positive premium")
    if take_profit is not None and stop_loss is not None and stop_loss >= take_profit:
        raise RuntimeError("Stop loss must be below take profit")
    return take_profit, stop_loss


def _record_exit(position: Position, exit_price: float, reason: str, order_id: str) -> TradeRecord:
    """Store a completed trade and clear the position."""
    pnl = (exit_price - position.entry_price) * position.quantity
    trade = TradeRecord(
        symbol=position.trading_symbol,
        option_type=position.option_type,
        quantity=position.quantity,
        entry_price=position.entry_price,
        exit_price=exit_price,
        pnl=pnl,
        reason=reason,
        entry_time=position.entry_time,
        exit_time=now_in_tz(),
    )
    STATE.trades.append(trade)
    STATE.realized_pnl += pnl
    STATE.unrealized_pnl = 0.0
    STATE.exit_orders_today += 1
    STATE.position = None
    STATE.option_ltp = None
    STATE.status = "FLAT"
    STATE.pending_order_id = None
    STATE.pending_kind = None
    STATE.pending_tag = None
    STATE.pending_exit_reason = None
    STATE.last_action = f"EXIT {reason} {order_id}".strip()
    return trade


def close_current_position(reason: str) -> bool:
    """Close the algo's current option through the paper or live path.

    Purpose:
        Keep TP, SL, opposite signal, session, loss, and shutdown on one exit.

    Inputs:
        A short reason code.

    Output:
        True when the position is already flat. False when a live exit is still pending.

    Trading use:
        Paper fills at the latest option premium. Live sends one sell and does
        not send another while that sell is pending. Immediate re-entry on the
        same candle is blocked by the caller.
    """
    position = get_current_position()
    if position is None:
        return True
    if STATE.status == "EXIT_PENDING":
        return False
    if CONFIG is not None and CONFIG.trading_mode == "PAPER":
        if STATE.option_ltp is None:
            raise RuntimeError("Cannot paper-exit without an option premium")
        paper_exit(reason, STATE.option_ltp)
        return True
    live_exit(reason)
    return STATE.position is None


def place_entry_order(signal: str, candle_time: datetime) -> None:
    """Select the option and hand it to paper or live entry.

    Purpose:
        One entry door so the strategy does not call the broker itself.

    Inputs:
        BUY_CE or BUY_PE, and the candle that created it.

    Output:
        None. PAPER becomes LONG_OPTION. LIVE becomes ENTRY_PENDING or LONG_OPTION.

    Trading use:
        The option chain is fetched here, not on every poll. PAPER cannot reach
        the live order function.
    """
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    if STATE.last_signal_candle == candle_time:
        return
    if STATE.status != "FLAT" or STATE.position is not None:
        note("Entry skipped: NOT_FLAT")
        return
    spot = STATE.underlying_ltp
    if spot is None:
        raise RuntimeError("Cannot select a strike without the underlying price")
    _spot, rows = fetch_option_chain(STATE.resolved_expiry)
    contract = select_option_contract(
        rows,
        spot,
        option_side(signal),
        CONFIG.strike_mode,
        CONFIG.strike_offset,
    )
    premium = contract.ask or contract.ltp
    if premium is None:
        premium = fetch_ltp(contract.security_id, contract.exchange_segment)
        contract.ltp = premium
    if premium is None:
        raise RuntimeError("Selected option has no premium. No order was sent.")
    price = premium
    if CONFIG.order_type == "LIMIT":
        price = limit_price("BUY", contract, premium)
    elif CONFIG.order_type == "MARKET":
        price = premium
    tp_price, sl_price = calculate_tp_sl(price, contract.tick_size)
    if CONFIG.trading_mode == "PAPER":
        preview = build_order_preview(contract, "BUY", order_quantity(contract), price, signal, tp_price, sl_price)
        print(preview)
        paper_enter(contract, signal, price)
        return
    if CONFIG.trading_mode == "LIVE":
        live_enter(contract, signal, price)
        return
    raise RuntimeError("trading_mode is neither PAPER nor LIVE")


def place_exit_order(reason: str) -> bool:
    """Exit wrapper used by risk and session checks.

    Purpose:
        Give callers one named exit function besides close_current_position.

    Inputs:
        Exit reason.

    Output:
        Whether the position is flat after the attempt.

    Trading use:
        Delegates to the single close path so sells are not implemented twice.
    """
    return close_current_position(reason)


# ============================================================
# RISK MANAGEMENT
# ============================================================

def validate_entry(signal: str) -> tuple[bool, str]:
    """Decide whether a new option buy is allowed.

    Purpose:
        Block duplicate, late, stale, or over-limit entries before any order.

    Inputs:
        BUY_CE or BUY_PE.

    Output:
        (True, OK) or (False, reason code).

    Trading use:
        Temporary reasons can be retried on the same candle. Risk locks and the
        order cap consume the candle so the bot does not retry every poll.
    """
    if CONFIG is None:
        return False, "NO_CONFIG"
    if signal not in {"BUY_CE", "BUY_PE"}:
        return False, "NO_SIGNAL"
    if STATE.status != "FLAT" or STATE.position is not None or STATE.pending_order_id:
        return False, "NOT_FLAT"
    if not STATE.allow_entries or STATE.session_stop_reached:
        return False, "ENTRIES_BLOCKED"
    if STATE.stop_loss_hit:
        return False, "STOP_LOSS_LOCK"
    if STATE.max_loss_hit:
        return False, "MAX_LOSS_LOCK"
    if not check_order_limit():
        return False, "MAX_ORDERS"
    if STATE.market_status != "OPEN":
        return False, "MARKET_CLOSED"
    if STATE.data_status == "DATA STALE":
        return False, "DATA_STALE"
    if STATE.data_status == "DATA ERROR":
        return False, "DATA_ERROR"
    if check_trading_session(now_in_tz()) != "OPEN":
        return False, "OUTSIDE_SESSION"
    if STATE.underlying_ltp is None:
        return False, "DATA_ERROR"
    return True, "OK"


def check_take_profit() -> bool:
    """True when the option premium has reached the configured target.

    Missing premiums do not count as a hit.
    """
    position = get_current_position()
    if position is None or not position.tp_price or STATE.option_ltp is None or STATE.quote_failed:
        return False
    if CONFIG is None or not CONFIG.tp_enabled or STATE.status != "LONG_OPTION":
        return False
    return STATE.option_ltp >= position.tp_price


def check_stop_loss() -> bool:
    """True when the option premium has fallen to the configured stop.

    Missing premiums do not count as a hit.
    """
    position = get_current_position()
    if position is None or not position.sl_price or STATE.option_ltp is None or STATE.quote_failed:
        return False
    if CONFIG is None or not CONFIG.sl_enabled or STATE.status != "LONG_OPTION":
        return False
    return STATE.option_ltp <= position.sl_price


def check_opposite_signal(signal: str) -> bool:
    """True when the open option is the other side of this confirmed signal.

    Purpose:
        Exit a call on a bearish cross and a put on a bullish cross.

    Inputs:
        The new signal.

    Output:
        False when flat, when the setting is off, or when the signal matches.

    Trading use:
        The caller closes first and does not buy the new side in the same cycle.
    """
    position = get_current_position()
    if CONFIG is None or not CONFIG.exit_on_opposite_signal or position is None:
        return False
    if STATE.status != "LONG_OPTION":
        return False
    if position.option_type == "CE" and signal == "BUY_PE":
        return True
    if position.option_type == "PE" and signal == "BUY_CE":
        return True
    return False


def check_daily_loss() -> bool:
    """True when combined gross P&L is at or below the rupee loss limit.

    An open option with a missing premium does not trip the limit. The bot
    waits for a real quote instead of using a stale mark.
    """
    if CONFIG is None:
        return False
    if STATE.position is not None and (STATE.quote_failed or STATE.option_ltp is None):
        return False
    return calculate_combined_algo_pnl() <= -abs(CONFIG.max_loss_per_day_inr)


def check_order_limit() -> bool:
    """True when another successful entry is still inside the daily cap.

    Exits are not blocked by this cap.
    """
    if CONFIG is None:
        return False
    return STATE.orders_today < CONFIG.max_orders_per_day


def apply_risk_exits() -> bool:
    """Close for TP, SL, or daily loss. Returns True when that exit has started.

    The poll order is take profit, then stop loss, then the daily loss limit.
    Each of those stops the bot once the close is confirmed. A live exit that
    is still pending stores the reason and stops on a later poll.
    """
    if STATE.status == "FLAT" and check_daily_loss():
        STATE.max_loss_hit = True
        STATE.shutdown_after_exit = "MAX_DAILY_LOSS"
        graceful_shutdown("MAX_DAILY_LOSS")
        return True
    if STATE.status != "LONG_OPTION":
        return False
    reason: str | None = None
    if check_take_profit():
        reason = "TP"
    elif check_stop_loss():
        STATE.stop_loss_hit = True
        reason = "SL"
    elif check_daily_loss():
        STATE.max_loss_hit = True
        reason = "MAX_DAILY_LOSS"
    if reason is None:
        return False
    STATE.shutdown_after_exit = reason
    if close_current_position(reason):
        graceful_shutdown(reason)
    return True


# ============================================================
# SESSION / MARKET STATUS
# ============================================================

def check_market_closed(moment: datetime | None = None) -> bool:
    """True on weekends and outside 09:15-15:30 exchange time.

    Purpose:
        Separate the exchange session from the bot's own scheduled window.

    Inputs:
        A timezone-aware time. Defaults to now.

    Output:
        True when new orders are forbidden because the exchange is shut.

    Trading use:
        The bot can still display the last candle and premium. It does not
        invent candles after the close.
    """
    current = moment or now_in_tz()
    if current.weekday() >= 5:
        return True
    stamp = current.timetz().replace(tzinfo=None)
    return stamp < MARKET_OPEN or stamp >= MARKET_CLOSE


def check_trading_session(moment: datetime | None = None) -> str:
    """Return OPEN, BEFORE_START, or AFTER_STOP.

    Purpose:
        Apply the scheduled window only when the session is enabled.

    Inputs:
        Current time in the configured timezone.

    Output:
        OPEN when a new entry is allowed by the clock.
        CONTINUOUS mode and a disabled session stay OPEN.

    Trading use:
        AFTER_STOP can close and exit. BEFORE_START only waits.
    """
    if CONFIG is None:
        return "BEFORE_START"
    current = moment or now_in_tz()
    if not CONFIG.session_enabled or CONFIG.run_mode == "CONTINUOUS":
        return "OPEN"
    start = _parse_clock(CONFIG.start_time, "start_time")
    stop = _parse_clock(CONFIG.stop_time, "stop_time")
    stamp = current.timetz().replace(tzinfo=None)
    if stamp < start:
        return "BEFORE_START"
    if stamp >= stop:
        return "AFTER_STOP"
    return "OPEN"


def check_manual_stop() -> bool:
    """True when stop.py has created the local stop file.

    Purpose:
        Let another terminal ask this process to exit.

    Inputs:
        None. Looks for .bot_stop beside this file.

    Output:
        True when the file exists.

    Trading use:
        The bot stops new entries and then follows the manual-stop close rule.
        The stop script itself never places an order.
    """
    return STOP_PATH.exists()


def roll_trading_day(moment: datetime) -> None:
    """Reset daily counters when the exchange date changes. Keep an open option."""
    if STATE.trading_date == moment.date():
        return
    STATE.trading_date = moment.date()
    STATE.orders_today = 0
    STATE.exit_orders_today = 0
    STATE.realized_pnl = 0.0
    STATE.stop_loss_hit = False
    STATE.max_loss_hit = False
    STATE.session_stop_reached = False
    STATE.trades.clear()
    STATE.allow_entries = True
    note(f"Trading date is {STATE.trading_date.isoformat()}. Daily counters were reset.")


def enforce_session(moment: datetime) -> None:
    """Stop entries at the scheduled stop, then close or exit if configured.

    A failed close is retried on a later poll. The session flag alone does not
    mean the option was sold.
    """
    if CONFIG is None:
        return
    if check_trading_session(moment) != "AFTER_STOP":
        return
    if not STATE.session_stop_reached:
        STATE.session_stop_reached = True
        STATE.allow_entries = False
        note("Session stop reached. New entries are blocked.")
    should_close = CONFIG.close_all_positions_at_stop and CONFIG.holding_mode == "INTRADAY"
    if should_close and get_current_position() is not None and STATE.status != "EXIT_PENDING":
        if CONFIG.stop_bot_after_close:
            STATE.shutdown_after_exit = "SESSION_END"
        close_current_position("SESSION_END")
    elif CONFIG.stop_bot_after_close and STATE.shutdown_after_exit is None:
        STATE.shutdown_after_exit = "SESSION_END"
    if STATE.status != "EXIT_PENDING" and CONFIG.stop_bot_after_close:
        graceful_shutdown("SESSION_END")


def mark_data_status(moment: datetime) -> None:
    """Set MARKET CLOSED, DATA ERROR, DATA STALE, or OK."""
    if STATE.market_status != "OPEN":
        STATE.data_status = "MARKET CLOSED"
        return
    candle_stale = STATE.candle_time is None
    if CONFIG is not None and STATE.candle_time is not None:
        age_limit = timedelta(minutes=CONFIG.timeframe_minutes * 2)
        candle_stale = (moment - STATE.candle_time) > age_limit
    if STATE.quote_failed:
        STATE.data_status = "DATA ERROR"
    elif candle_stale:
        STATE.data_status = "DATA STALE"
    else:
        STATE.data_status = "OK"


def refresh_quotes() -> None:
    """Update the index price and, when a position is open, the option premium."""
    instruments: dict[str, list[str]] = {"IDX_I": [STATE.underlying_security_id]}
    position = get_current_position()
    if position is not None:
        instruments.setdefault(position.exchange_segment, []).append(position.security_id)
    try:
        prices = fetch_quotes(instruments)
        underlying = prices.get(str(STATE.underlying_security_id))
        STATE.quote_failed = underlying is None
        if underlying is not None:
            STATE.underlying_ltp = underlying
        if position is not None:
            premium = prices.get(str(position.security_id))
            if premium is None:
                STATE.quote_failed = True
            else:
                STATE.option_ltp = premium
                STATE.unrealized_pnl = calculate_position_pnl(position, premium)
    except Exception as exc:
        STATE.quote_failed = True
        note(f"DATA ERROR: {exc}")


def finish_pending_exit_shutdown() -> None:
    """Stop the process after a live exit fills when that exit was meant to stop."""
    if STATE.position is not None or STATE.status == "EXIT_PENDING":
        return
    reason = STATE.shutdown_after_exit
    if reason:
        graceful_shutdown(reason)


# ============================================================
# CLI DASHBOARD
# ============================================================

def _box_row(text: str) -> str:
    """One padded dashboard row."""
    clipped = text[:DASHBOARD_WIDTH]
    return "║ " + clipped.ljust(DASHBOARD_WIDTH) + " ║"


def _box_rule(left: str, fill: str, right: str) -> str:
    """A horizontal dashboard border."""
    return left + (fill * (DASHBOARD_WIDTH + 2)) + right


def render_startup() -> None:
    """Print the fixed settings before the first poll.

    LIVE mode adds a warning that cannot be missed. Quantity shown here uses
    the sample contract lot from startup; the order path checks the actual
    contract again.
    """
    if CONFIG is None:
        return
    quantity = "-"
    if STATE.sample_lot_size:
        quantity = str(CONFIG.lots * STATE.sample_lot_size)
    lines = [
        f"{BOT_NAME}  v{BOT_VERSION}",
        f"Mode: {CONFIG.trading_mode}",
        f"Underlying: {CONFIG.underlying}  id {STATE.underlying_security_id}  ({STATE.underlying_symbol})",
        f"Timeframe: {CONFIG.timeframe_minutes}m   Half Trend {CONFIG.amplitude}, {CONFIG.channel_deviation}",
        f"Expiry: {CONFIG.expiry_mode} {STATE.resolved_expiry}",
        f"Strike: {CONFIG.strike_mode} offset {CONFIG.strike_offset}",
        f"Lots: {CONFIG.lots}   sample lot {STATE.sample_lot_size or '-'}   sample quantity {quantity}",
        f"Order: {CONFIG.order_type}   Product: {CONFIG.product_type}   Holding: {CONFIG.holding_mode}",
        f"TP: {CONFIG.tp_type} {CONFIG.tp_value} enabled={CONFIG.tp_enabled}",
        f"SL: {CONFIG.sl_type} {CONFIG.sl_value} enabled={CONFIG.sl_enabled}",
        f"Max daily loss: {format_inr(CONFIG.max_loss_per_day_inr)}   Max entries: {CONFIG.max_orders_per_day}",
        f"Run: {CONFIG.run_mode}   Session {CONFIG.start_time}-{CONFIG.stop_time} {CONFIG.timezone_name}",
        f"Session enabled: {CONFIG.session_enabled}   Close on stop: {CONFIG.close_all_positions_at_stop}",
        f"Stop process after close: {CONFIG.stop_bot_after_close}",
        f"Poll: {CONFIG.polling_seconds}s   Post-close: {CONFIG.post_close_polls} x {CONFIG.post_close_poll_seconds}s",
        "P&L shown by this bot is gross option premium, before charges and slippage.",
    ]
    print("\n".join(lines))
    if CONFIG.trading_mode == "LIVE":
        print("WARNING: LIVE TRADING ENABLED")


def render_dashboard() -> None:
    """Redraw the terminal status box.

    Purpose:
        Show what the bot is watching and why the latest signal exists.

    Inputs:
        Reads the in-memory state. It does not call Dhan.

    Output:
        Prints one box. On a terminal, the previous box is cleared first.

    Trading use:
        The box is the operator view for mode, prices, Half Trend, position,
        gross P&L, and the next poll. It is not a broker confirmation.
    """
    if CONFIG is None:
        return
    position = get_current_position()
    if position is None:
        option_name = "-"
        position_text = "FLAT"
        quantity = "-"
        entry = "-"
        take_profit = "-"
        stop_loss = "-"
        current_pnl = format_inr(0.0)
    else:
        option_name = position.trading_symbol
        position_text = f"LONG {position.option_type}"
        quantity = str(position.quantity)
        entry = format_inr(position.entry_price)
        take_profit = format_inr(position.tp_price)
        stop_loss = format_inr(position.sl_price)
        current_pnl = format_inr(STATE.unrealized_pnl)
    if not check_order_limit() and STATE.status == "FLAT":
        order_text = f"{STATE.orders_today} / {CONFIG.max_orders_per_day}  MAX ORDERS REACHED"
    else:
        order_text = f"{STATE.orders_today} / {CONFIG.max_orders_per_day}"
    candle_text = STATE.candle_time.strftime("%Y-%m-%d %H:%M:%S") if STATE.candle_time else "-"
    rows = [
        _box_rule("╔", "═", "╗"),
        _box_row(BOT_NAME),
        _box_rule("╠", "═", "╣"),
        _box_row(f"MODE        : {CONFIG.trading_mode}"),
        _box_row(f"UNDERLYING  : {CONFIG.underlying}"),
        _box_row(f"TIMEFRAME   : {CONFIG.timeframe_minutes}M"),
        _box_row(f"MARKET      : {STATE.market_status}"),
        _box_row(f"DATA        : {STATE.data_status}"),
        _box_rule("╠", "═", "╣"),
        _box_row(f"UNDERLYING LTP : {format_number(STATE.underlying_ltp)}"),
        _box_row(f"HALF TREND     : {format_number(STATE.half_trend_value)}"),
        _box_row(f"SIGNAL         : {STATE.signal_label}"),
        _box_row(f"CANDLE         : {candle_text}"),
        _box_row(f"WHY            : {STATE.signal_reason}"),
        _box_rule("╠", "═", "╣"),
        _box_row(f"OPTION         : {option_name}"),
        _box_row(f"OPTION LTP     : {format_inr(STATE.option_ltp)}"),
        _box_row(f"POSITION       : {position_text}"),
        _box_row(f"STATE          : {STATE.status}"),
        _box_row(f"QUANTITY       : {quantity}"),
        _box_row(f"ENTRY          : {entry}"),
        _box_row(f"TP             : {take_profit}"),
        _box_row(f"SL             : {stop_loss}"),
        _box_row(f"CURRENT P&L    : {current_pnl} gross"),
        _box_rule("╠", "═", "╣"),
        _box_row(f"TODAY ALGO P&L : {format_inr(calculate_combined_algo_pnl())} gross"),
        _box_row(f"ORDERS TODAY   : {order_text}"),
        _box_row(f"DAILY LOSS LIM : {format_inr(CONFIG.max_loss_per_day_inr)}"),
        _box_rule("╠", "═", "╣"),
        _box_row(f"POLLING        : {CONFIG.polling_seconds} sec"),
        _box_row(f"NEXT POLL      : {STATE.next_poll_seconds} sec"),
        _box_row(f"POST CLOSE     : {STATE.post_close_polls_done}/{CONFIG.post_close_polls}"),
        _box_row(f"LAST ACTION    : {STATE.last_action}"),
        _box_rule("╚", "═", "╝"),
    ]
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")
    print("\n".join(rows))


def sleep_until_next_poll(seconds: int) -> None:
    """Wait in one-second steps so the countdown and manual stop stay responsive.

    This wait does not call Dhan. A stop file ends the wait immediately.
    """
    for remaining in range(max(int(seconds), 1), 0, -1):
        if STATE.shutting_down:
            return
        STATE.next_poll_seconds = remaining
        if check_manual_stop():
            graceful_shutdown("MANUAL_STOP")
            return
        render_dashboard()
        time.sleep(1)


# ============================================================
# SHUTDOWN
# ============================================================

def _should_close_on_shutdown(reason: str) -> bool:
    """Whether this shutdown reason should sell the algo option."""
    if get_current_position() is None or CONFIG is None:
        return False
    if reason in {"TP", "SL", "MAX_DAILY_LOSS"}:
        return True
    if reason == "CRITICAL":
        return True
    if reason in {"SESSION_END", "MARKET_CLOSED"} and CONFIG.holding_mode != "INTRADAY":
        return False
    if reason in {"MANUAL_STOP", "SESSION_END", "MARKET_CLOSED", "CRITICAL"}:
        return CONFIG.close_all_positions_at_stop or reason == "CRITICAL"
    return False


def _wait_for_exit() -> None:
    """Give a live exit a few short checks before the process ends."""
    if CONFIG is None or CONFIG.trading_mode != "LIVE":
        return
    checks = 0
    while STATE.status == "EXIT_PENDING" and checks < 5:
        try:
            check_order_status()
        except Exception as exc:
            note(f"Exit status check failed: {exc}")
            return
        if STATE.position is None:
            return
        checks += 1
        time.sleep(1)


def _print_final_summary(reason: str) -> None:
    """Print gross results. This is the last thing the operator sees."""
    print("")
    print(f"FINAL SUMMARY  reason={reason}")
    print(f"Today gross realized: {format_inr(STATE.realized_pnl)}")
    print(f"Open gross unrealized: {format_inr(STATE.unrealized_pnl)}")
    print(f"Combined gross: {format_inr(calculate_combined_algo_pnl())}")
    print("This P&L excludes brokerage, STT, GST, exchange charges, and slippage.")
    if not STATE.trades:
        print("No completed algo trades were recorded in this process.")
    for trade in STATE.trades:
        print(
            f"{trade.reason} {trade.symbol} qty {trade.quantity} "
            f"{format_inr(trade.entry_price)} -> {format_inr(trade.exit_price)} "
            f"gross {format_inr(trade.pnl)}"
        )
    if get_current_position() is not None:
        position = STATE.position
        print(
            f"Position still open: {position.trading_symbol if position else ''} "
            f"qty {position.quantity if position else ''}. It was not closed by this shutdown."
        )
    if STATE.status == "EXIT_PENDING":
        print(f"Exit order still pending: {STATE.pending_order_id}. No second exit was sent.")


def graceful_shutdown(reason: str) -> None:
    """Stop entries, close when the reason requires it, print P&L, and exit.

    Purpose:
        One shutdown path for TP, SL, daily loss, session, manual stop,
        market close, Ctrl+C, and critical errors.

    Inputs:
        A reason code.

    Output:
        Does not return. Raises SystemExit after the summary.

    Trading use:
        Pending entries are cancelled. Pending exits are waited on, not duplicated.
        SWING and POSITIONAL positions are not force-closed just because the
        intraday session ended.
    """
    if STATE.shutting_down:
        return
    STATE.shutting_down = True
    STATE.allow_entries = False
    STATE.last_action = f"SHUTDOWN {reason}"
    note(f"Shutdown started: {reason}")
    try:
        _cancel_pending_entry()
        if (
            not STATE.unsafe_to_close
            and _should_close_on_shutdown(reason)
            and STATE.status != "EXIT_PENDING"
        ):
            close_current_position(reason)
        elif STATE.unsafe_to_close:
            note("Close skipped because the live position no longer matches this algo.")
        _wait_for_exit()
    except Exception as exc:
        note(f"Shutdown could not finish the position safely: {exc}")
    if reason in {"SESSION_END", "MARKET_CLOSED"} and CONFIG is not None and CONFIG.holding_mode != "INTRADAY":
        note("Holding mode is not INTRADAY, so the option was not force-closed at the session end.")
    _print_final_summary(reason)
    if STOP_PATH.exists():
        try:
            STOP_PATH.unlink()
        except OSError:
            pass
    raise SystemExit(0)


# ============================================================
# MAIN LOOP
# ============================================================

def clear_stale_stop_file() -> None:
    """Remove a stop file left by an earlier run so this start can proceed."""
    if STOP_PATH.exists():
        STOP_PATH.unlink()
        print("Cleared a previous .bot_stop file from an earlier run.")


def startup_checks() -> None:
    """Resolve the index, expiry, chain, and a sample lot before trading.

    A failure here exits before the loop. The sample lot is display-only;
    every order resolves the real contract again.
    """
    if CONFIG is None or BROKER is None:
        raise RuntimeError("Startup is missing config or Dhan")
    STATE.trading_date = now_in_tz().date()
    master = load_security_master()
    resolved = resolve_underlying(master, CONFIG.underlying)
    STATE.underlying_security_id = resolved["security_id"]
    STATE.underlying_symbol = resolved["trading_symbol"]
    expiries = fetch_expiry_list()
    expiry = choose_expiry(expiries, STATE.trading_date)
    spot, rows = fetch_option_chain(expiry)
    STATE.underlying_ltp = spot
    sample = select_option_contract(rows, spot, "CE", "ATM", 0)
    STATE.sample_lot_size = sample.lot_size
    if CONFIG.lots * sample.lot_size % sample.lot_size != 0:
        raise RuntimeError("Sample quantity is not a valid lot multiple")
    if check_market_closed():
        STATE.market_status = "CLOSED"
        note("Market is closed at startup. No entry will be sent.")
        try:
            refresh_indicator(now_in_tz())
        except Exception as exc:
            note(f"Candles unavailable while the market is closed: {exc}")
    else:
        STATE.market_status = "OPEN"
        try:
            refresh_indicator(now_in_tz())
        except Exception as exc:
            raise RuntimeError(_data_plan_hint(exc)) from exc
        if STATE.candles is None or STATE.candles.empty:
            raise RuntimeError("No confirmed index candles were returned during market hours")
    if CONFIG.trading_mode == "LIVE":
        reconcile_live_positions()


def handle_runtime_error(exc: Exception) -> None:
    """Show a temporary failure and keep the current order state unchanged."""
    STATE.consecutive_errors += 1
    STATE.quote_failed = True
    STATE.data_status = "DATA ERROR"
    note(f"DATA ERROR: {exc}")
    if _is_rate_limit(exc):
        time.sleep(1.0)
    if STATE.consecutive_errors >= 15:
        graceful_shutdown("CRITICAL")


def poll_open_market(moment: datetime) -> None:
    """One monitoring pass while the exchange is open."""
    if CONFIG is None:
        return
    STATE.market_status = "OPEN"
    STATE.market_close_handled = False
    STATE.post_close_polls_done = 0
    if not STATE.session_stop_reached and not STATE.stop_loss_hit and not STATE.max_loss_hit:
        STATE.allow_entries = True
    enforce_session(moment)
    if STATE.shutting_down:
        return
    refresh_quotes()
    if STATE.position is not None and STATE.option_ltp is not None and not STATE.quote_failed:
        STATE.unrealized_pnl = calculate_position_pnl(STATE.position, STATE.option_ltp)
    mark_data_status(moment)
    if STATE.status == "ENTRY_PENDING":
        check_order_status()
    if STATE.status == "EXIT_PENDING":
        check_order_status()
        finish_pending_exit_shutdown()
        return
    if apply_risk_exits():
        return
    try:
        refresh_broker_position()
    except Exception as exc:
        note(str(exc))
        graceful_shutdown("CRITICAL")
        return
    if STATE.status in {"FLAT", "LONG_OPTION"}:
        try:
            refresh_indicator(moment)
        except Exception as exc:
            STATE.data_status = "DATA ERROR"
            note(f"Candle refresh failed: {exc}")
        else:
            mark_data_status(moment)
            if STATE.data_status != "DATA ERROR":
                process_signal()
    if not check_order_limit() and STATE.status == "FLAT":
        STATE.last_action = "MAX ORDERS REACHED"
    STATE.consecutive_errors = 0


def poll_closed_market(moment: datetime) -> None:
    """Observe after the close, then close or exit once the configured polls finish."""
    if CONFIG is None:
        return
    STATE.market_status = "CLOSED"
    STATE.data_status = "MARKET CLOSED"
    STATE.allow_entries = False
    if STATE.status in {"ENTRY_PENDING", "EXIT_PENDING"}:
        try:
            check_order_status()
        except Exception as exc:
            note(f"Order check while closed failed: {exc}")
        finish_pending_exit_shutdown()
    if not STATE.market_close_handled:
        try:
            refresh_quotes()
        except Exception as exc:
            note(f"Quote error while closed: {exc}")
        if STATE.post_close_polls_done < CONFIG.post_close_polls:
            STATE.post_close_polls_done += 1
            note(
                f"MARKET CLOSED poll {STATE.post_close_polls_done}/{CONFIG.post_close_polls}. "
                "No new trade."
            )
        if STATE.post_close_polls_done >= CONFIG.post_close_polls:
            should_close = (
                CONFIG.holding_mode == "INTRADAY"
                and CONFIG.close_all_positions_at_stop
                and get_current_position() is not None
                and STATE.status != "EXIT_PENDING"
            )
            try:
                if should_close:
                    if CONFIG.stop_bot_after_close:
                        STATE.shutdown_after_exit = "MARKET_CLOSED"
                    close_current_position("MARKET_CLOSED")
                elif CONFIG.stop_bot_after_close and STATE.shutdown_after_exit is None:
                    STATE.shutdown_after_exit = "MARKET_CLOSED"
            except Exception as exc:
                note(f"Could not close after the market close: {exc}")
                sleep_until_next_poll(CONFIG.post_close_poll_seconds)
                return
            STATE.market_close_handled = True
            if STATE.status != "EXIT_PENDING" and CONFIG.stop_bot_after_close:
                graceful_shutdown("MARKET_CLOSED")
        sleep_until_next_poll(CONFIG.post_close_poll_seconds)
        return
    sleep_until_next_poll(CONFIG.polling_seconds)


def run_cycle() -> None:
    """Run one poll: stop file, clock, prices, risk, signal, then dashboard wait."""
    if check_manual_stop():
        graceful_shutdown("MANUAL_STOP")
        return
    moment = now_in_tz()
    roll_trading_day(moment)
    try:
        if check_market_closed(moment):
            poll_closed_market(moment)
        else:
            poll_open_market(moment)
            if not STATE.shutting_down:
                sleep_until_next_poll(CONFIG.polling_seconds if CONFIG else 5)
    except SystemExit:
        raise
    except Exception as exc:
        handle_runtime_error(exc)
        if not STATE.shutting_down:
            sleep_until_next_poll(CONFIG.polling_seconds if CONFIG else 5)


def main() -> None:
    """Load configuration, connect to Dhan, validate the contract universe, and poll.

    Purpose:
        Start the bot.

    Inputs:
        config.yaml and .env beside this file.

    Output:
        Runs until a shutdown reason exits the process.

    Trading use:
        PAPER never reaches the live order call. LIVE can place real orders
        after startup validation and an on-screen preview.
    """
    global CONFIG, BROKER
    try:
        clear_stale_stop_file()
        CONFIG = load_config()
        client_id, access_token = load_environment()
        BROKER = create_dhan_client(client_id, access_token)
        startup_checks()
        render_startup()
        while True:
            run_cycle()
    except KeyboardInterrupt:
        graceful_shutdown("MANUAL_STOP")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Startup failed: {exc}")
        print("No order was sent.")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()


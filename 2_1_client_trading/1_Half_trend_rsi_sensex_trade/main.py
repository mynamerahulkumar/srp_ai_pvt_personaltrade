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
BOT_VERSION = "2.0.0"
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
DASHBOARD_WIDTH = 60

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
    candle_mode: str
    timeframe_minutes: int
    polling_seconds: int
    amplitude: int
    channel_deviation: float
    sideways_filter_enabled: bool
    sideways_lookback: int
    sideways_tolerance_points: float
    rsi_enabled: bool
    rsi_period: int
    rsi_bullish_min: float
    rsi_bearish_max: float
    renko_brick_size_points: float
    renko_entry_confirmation_bricks: int
    renko_exit_confirmation_bricks: int
    expiry_mode: str
    expiry: str
    selection_mode: str
    strike_offset: int
    target_premium: float
    premium_tolerance: float
    lots: int
    order_type: str
    product_type: str
    tp_sl_mode: str
    tp_enabled: bool
    tp_points: float
    sl_enabled: bool
    sl_points: float
    stop_bot_after_tp_sl: bool
    max_open_strategies: int
    max_orders_per_day: int
    max_loss_per_day_inr: float
    no_reentry_after_stop_loss: bool
    no_reentry_after_max_loss: bool
    exit_on_opposite_signal: bool
    timezone_name: str
    run_mode: str
    start_time: str
    stop_time: str
    close_all_positions_at_stop: bool
    stop_bot_after_close: bool
    post_close_polls: int
    post_close_poll_seconds: int
    backtest_start_date: str
    backtest_end_date: str

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


def _parse_iso_date(value: str, label: str) -> date:
    """Parse YYYY-MM-DD. Invalid dates are rejected before any market call."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD") from exc


def validate_config(config: AppConfig, *, for_backtest: bool = False) -> None:
    """Reject settings that would make orders, sessions, or exits ambiguous.

    Purpose:
        Stop the process before any order when the YAML is inconsistent.

    Inputs:
        A parsed AppConfig, and whether this load is for historical replay.

    Output:
        None. Raises ValueError with the first problem found.

    Trading use:
        PAPER/LIVE come from config.yaml. BACKTEST comes only from test.yaml
        via backtest.py. The index, product type, and session clock must stay
        inside the combinations this bot can actually execute.
    """
    if config.underlying not in UNDERLYING_SPECS:
        raise ValueError("underlying must be SENSEX, NIFTY, or BANKNIFTY")
    if for_backtest:
        if config.trading_mode != "BACKTEST":
            raise ValueError("backtest loads must use trading_mode BACKTEST")
    elif config.trading_mode not in {"PAPER", "LIVE"}:
        raise ValueError("trading_mode must be PAPER or LIVE in config.yaml")
    if config.candle_mode not in {"NORMAL", "RENKO"}:
        raise ValueError("candle_mode must be NORMAL or RENKO")
    if config.run_mode not in {"CONTINUOUS", "SCHEDULED"}:
        raise ValueError("run_mode must be CONTINUOUS or SCHEDULED")
    if config.selection_mode not in {"ATM", "ITM", "OTM", "PREMIUM"}:
        raise ValueError("selection_mode must be ATM, ITM, OTM, or PREMIUM")
    if config.expiry_mode not in {"NEAREST", "CONFIGURED"}:
        raise ValueError("expiry_mode must be NEAREST or CONFIGURED")
    if config.tp_sl_mode not in {"UNDERLYING_POINTS", "OPTION_PREMIUM_POINTS"}:
        raise ValueError("tp_sl_mode must be UNDERLYING_POINTS or OPTION_PREMIUM_POINTS")
    if config.timeframe_minutes not in DHAN_INTERVALS:
        raise ValueError("timeframe_minutes must be one of 1, 5, 15, 25, 60")
    if config.amplitude < 1:
        raise ValueError("amplitude must be at least 1")
    if config.channel_deviation <= 0:
        raise ValueError("channel_deviation must be greater than 0")
    if config.sideways_lookback < 2:
        raise ValueError("sideways_lookback must be at least 2")
    if config.sideways_tolerance_points < 0:
        raise ValueError("sideways_tolerance_points cannot be negative")
    if config.rsi_period < 2:
        raise ValueError("rsi period must be at least 2")
    if config.renko_brick_size_points <= 0:
        raise ValueError("renko brick_size_points must be greater than 0")
    if config.renko_entry_confirmation_bricks < 1 or config.renko_exit_confirmation_bricks < 1:
        raise ValueError("renko confirmation counts must be at least 1")
    if config.lots < 1:
        raise ValueError("lots must be at least 1")
    if config.strike_offset < 0:
        raise ValueError("strike_offset cannot be negative")
    if config.selection_mode == "ATM" and config.strike_offset != 0:
        raise ValueError("ATM selection_mode requires strike_offset 0")
    if config.selection_mode == "PREMIUM" and config.target_premium <= 0:
        raise ValueError("PREMIUM selection requires target_premium greater than 0")
    if config.selection_mode == "PREMIUM" and config.premium_tolerance < 0:
        raise ValueError("premium_tolerance cannot be negative")
    if config.order_type not in {"LIMIT", "MARKET"}:
        raise ValueError("order_type must be LIMIT or MARKET")
    if config.product_type not in {"INTRADAY", "MARGIN"}:
        raise ValueError("product_type must be INTRADAY or MARGIN")
    if config.max_open_strategies != 1:
        raise ValueError("max_open_strategies must be 1")
    if config.max_orders_per_day < 1:
        raise ValueError("max_orders_per_day must be at least 1")
    if config.max_loss_per_day_inr <= 0:
        raise ValueError("max_loss_per_day_inr must be greater than 0")
    if config.tp_enabled and config.tp_points <= 0:
        raise ValueError("take profit points must be greater than 0")
    if config.sl_enabled and config.sl_points <= 0:
        raise ValueError("stop loss points must be greater than 0")
    if config.polling_seconds < 1 or config.post_close_polls < 1 or config.post_close_poll_seconds < 1:
        raise ValueError("polling and post-close settings must be at least 1")
    if config.expiry_mode == "CONFIGURED":
        _parse_iso_date(config.expiry, "expiry")
    if for_backtest:
        start_date = _parse_iso_date(config.backtest_start_date, "backtest.start_date")
        end_date = _parse_iso_date(config.backtest_end_date, "backtest.end_date")
        if start_date > end_date:
            raise ValueError("backtest.start_date must be on or before backtest.end_date")
    try:
        ZoneInfo(config.timezone_name)
    except Exception as exc:
        raise ValueError(f"timezone is not valid: {config.timezone_name}") from exc
    start = _parse_clock(config.start_time, "start_time")
    stop = _parse_clock(config.stop_time, "stop_time")
    if start >= stop:
        raise ValueError("start_time must be earlier than stop_time")


def load_config(path: Path = CONFIG_PATH, *, for_backtest: bool = False) -> AppConfig:
    """Load and validate a YAML trading configuration.

    Purpose:
        Keep trading settings in YAML. PAPER/LIVE use config.yaml.
        Historical runs use test.yaml through backtest.py.

    Inputs:
        Path to the YAML file, and for_backtest=True when loading test.yaml.

    Output:
        An AppConfig. Invalid files raise ValueError or a YAML error.

    Trading use:
        Live loads reject BACKTEST and a backtest: block. Backtest loads force
        trading_mode BACKTEST, require backtest dates, and reject trading_mode
        in the YAML so modes cannot drift.
    """
    label = path.name
    if not path.exists():
        raise ValueError(f"{label} was not found at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{label} is not valid YAML: {exc}") from exc
    root = _require_mapping(raw, label)
    allowed_root = {
        "underlying",
        "candle_mode",
        "timeframe_minutes",
        "polling_seconds",
        "halftrend",
        "rsi",
        "renko",
        "option",
        "execution",
        "risk",
        "timezone",
        "run_mode",
        "start_time",
        "stop_time",
        "close_all_positions_at_stop",
        "stop_bot_after_close",
        "post_close_polls",
        "post_close_poll_seconds",
    }
    if for_backtest:
        allowed_root.add("backtest")
        if "trading_mode" in root:
            raise ValueError(
                f"{label} must not set trading_mode. "
                "Running backtest.py always means BACKTEST."
            )
    else:
        allowed_root.add("trading_mode")
    _reject_unknown(root, allowed_root, "top-level")
    halftrend = _require_mapping(root.get("halftrend"), "halftrend")
    rsi = _require_mapping(root.get("rsi"), "rsi")
    renko = _require_mapping(root.get("renko"), "renko")
    option = _require_mapping(root.get("option"), "option")
    execution = _require_mapping(root.get("execution"), "execution")
    risk = _require_mapping(root.get("risk"), "risk")
    if for_backtest:
        backtest = _require_mapping(root.get("backtest"), "backtest")
        _reject_unknown(backtest, {"start_date", "end_date"}, "backtest")
        backtest_start = str(backtest.get("start_date") or "").strip()
        backtest_end = str(backtest.get("end_date") or "").strip()
        trading_mode = "BACKTEST"
    else:
        backtest_start = ""
        backtest_end = ""
        trading_mode = _as_choice(root.get("trading_mode"), "trading_mode", {"PAPER", "LIVE"})
    _reject_unknown(
        halftrend,
        {"amplitude", "channel_deviation", "sideways_filter_enabled", "sideways_lookback", "sideways_tolerance_points"},
        "halftrend",
    )
    _reject_unknown(rsi, {"enabled", "period", "bullish_min", "bearish_max"}, "rsi")
    _reject_unknown(renko, {"brick_size_points", "entry_confirmation_bricks", "exit_confirmation_bricks"}, "renko")
    _reject_unknown(
        option,
        {"expiry_mode", "expiry", "selection_mode", "strike_offset", "target_premium", "premium_tolerance", "lots"},
        "option",
    )
    _reject_unknown(execution, {"order_type", "product_type"}, "execution")
    _reject_unknown(
        risk,
        {
            "tp_sl_mode",
            "take_profit",
            "stop_loss",
            "stop_bot_after_tp_sl",
            "max_open_strategies",
            "max_orders_per_day",
            "max_loss_per_day_inr",
            "no_reentry_after_stop_loss",
            "no_reentry_after_max_loss",
            "exit_on_opposite_signal",
        },
        "risk",
    )
    take_profit = _require_mapping(risk.get("take_profit"), "risk.take_profit")
    stop_loss = _require_mapping(risk.get("stop_loss"), "risk.stop_loss")
    _reject_unknown(take_profit, {"enabled", "points"}, "risk.take_profit")
    _reject_unknown(stop_loss, {"enabled", "points"}, "risk.stop_loss")
    expiry = "" if option.get("expiry") is None else str(option.get("expiry")).strip()
    config = AppConfig(
        trading_mode=trading_mode,
        underlying=_as_choice(root.get("underlying"), "underlying", set(UNDERLYING_SPECS)),
        candle_mode=_as_choice(root.get("candle_mode"), "candle_mode", {"NORMAL", "RENKO"}),
        timeframe_minutes=_as_int(root.get("timeframe_minutes"), "timeframe_minutes"),
        polling_seconds=_as_int(root.get("polling_seconds"), "polling_seconds"),
        amplitude=_as_int(halftrend.get("amplitude"), "halftrend.amplitude"),
        channel_deviation=_as_float(halftrend.get("channel_deviation"), "halftrend.channel_deviation"),
        sideways_filter_enabled=_as_bool(halftrend.get("sideways_filter_enabled"), "sideways_filter_enabled"),
        sideways_lookback=_as_int(halftrend.get("sideways_lookback"), "sideways_lookback"),
        sideways_tolerance_points=_as_float(halftrend.get("sideways_tolerance_points"), "sideways_tolerance_points"),
        rsi_enabled=_as_bool(rsi.get("enabled"), "rsi.enabled"),
        rsi_period=_as_int(rsi.get("period"), "rsi.period"),
        rsi_bullish_min=_as_float(rsi.get("bullish_min"), "rsi.bullish_min"),
        rsi_bearish_max=_as_float(rsi.get("bearish_max"), "rsi.bearish_max"),
        renko_brick_size_points=_as_float(renko.get("brick_size_points"), "renko.brick_size_points"),
        renko_entry_confirmation_bricks=_as_int(renko.get("entry_confirmation_bricks"), "entry_confirmation_bricks"),
        renko_exit_confirmation_bricks=_as_int(renko.get("exit_confirmation_bricks"), "exit_confirmation_bricks"),
        expiry_mode=_as_choice(option.get("expiry_mode"), "expiry_mode", {"NEAREST", "CONFIGURED"}),
        expiry=expiry,
        selection_mode=_as_choice(option.get("selection_mode"), "selection_mode", {"ATM", "ITM", "OTM", "PREMIUM"}),
        strike_offset=_as_int(option.get("strike_offset"), "strike_offset"),
        target_premium=_as_float(option.get("target_premium"), "target_premium"),
        premium_tolerance=_as_float(option.get("premium_tolerance"), "premium_tolerance"),
        lots=_as_int(option.get("lots"), "lots"),
        order_type=_as_choice(execution.get("order_type"), "order_type", {"LIMIT", "MARKET"}),
        product_type=_as_choice(execution.get("product_type"), "product_type", {"INTRADAY", "MARGIN"}),
        tp_sl_mode=_as_choice(risk.get("tp_sl_mode"), "tp_sl_mode", {"UNDERLYING_POINTS", "OPTION_PREMIUM_POINTS"}),
        tp_enabled=_as_bool(take_profit.get("enabled"), "take_profit.enabled"),
        tp_points=_as_float(take_profit.get("points"), "take_profit.points"),
        sl_enabled=_as_bool(stop_loss.get("enabled"), "stop_loss.enabled"),
        sl_points=_as_float(stop_loss.get("points"), "stop_loss.points"),
        stop_bot_after_tp_sl=_as_bool(risk.get("stop_bot_after_tp_sl"), "stop_bot_after_tp_sl"),
        max_open_strategies=_as_int(risk.get("max_open_strategies"), "max_open_strategies"),
        max_orders_per_day=_as_int(risk.get("max_orders_per_day"), "max_orders_per_day"),
        max_loss_per_day_inr=_as_float(risk.get("max_loss_per_day_inr"), "max_loss_per_day_inr"),
        no_reentry_after_stop_loss=_as_bool(risk.get("no_reentry_after_stop_loss"), "no_reentry_after_stop_loss"),
        no_reentry_after_max_loss=_as_bool(risk.get("no_reentry_after_max_loss"), "no_reentry_after_max_loss"),
        exit_on_opposite_signal=_as_bool(risk.get("exit_on_opposite_signal"), "exit_on_opposite_signal"),
        timezone_name=str(root.get("timezone") or "").strip(),
        run_mode=_as_choice(root.get("run_mode"), "run_mode", {"CONTINUOUS", "SCHEDULED"}),
        start_time=str(root.get("start_time") or "").strip(),
        stop_time=str(root.get("stop_time") or "").strip(),
        close_all_positions_at_stop=_as_bool(root.get("close_all_positions_at_stop"), "close_all_positions_at_stop"),
        stop_bot_after_close=_as_bool(root.get("stop_bot_after_close"), "stop_bot_after_close"),
        post_close_polls=_as_int(root.get("post_close_polls"), "post_close_polls"),
        post_close_poll_seconds=_as_int(root.get("post_close_poll_seconds"), "post_close_poll_seconds"),
        backtest_start_date=backtest_start,
        backtest_end_date=backtest_end,
    )
    validate_config(config, for_backtest=for_backtest)
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
    underlying_entry: float
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
    last_signal_bar: str | None = None
    last_confirmed_candle: datetime | None = None
    last_evaluated_bar: str | None = None
    bar_id: str | None = None
    deferred_signal: str | None = None
    deferred_bar: str | None = None
    last_option_contract: OptionContract | None = None
    stop_loss_hit: bool = False
    max_loss_hit: bool = False
    session_stop_reached: bool = False
    allow_entries: bool = True
    last_action: str = "STARTING"
    data_status: str = "OK"
    quote_failed: bool = False
    half_trend_value: float | None = None
    rsi_value: float | None = None
    rsi_filter: str = "DISABLED"
    market_state: str = "WAITING"
    entry_block_reason: str = ""
    entry_confirm_count: int = 0
    exit_confirm_count: int = 0
    brick_state: str = "-"
    ht_position: str = "-"
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
    minute_candles: pd.DataFrame | None = None
    source_candle_time: datetime | None = None
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
    entry_skip_bar: str | None = None
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
    preferred = matches["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.strip()
    exact = matches[preferred == spec["preferred_symbol"]]
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


def bar_minutes() -> int:
    """Return the source-candle size. Renko is built from 1-minute closes."""
    if CONFIG is None:
        return 1
    if CONFIG.candle_mode == "RENKO":
        return 1
    return CONFIG.timeframe_minutes


def fetch_underlying_candles(now: datetime | None = None) -> pd.DataFrame:
    """Download index candles and keep only confirmed, recent rows.

    Purpose:
        Feed Half Trend without calling history on every 5-second poll.

    Inputs:
        Current exchange time. Uses the resolved index security id.

    Output:
        OHLC frame in the configured timezone. The forming candle is removed.
        Renko mode stores 1-minute closes. Normal mode stores the configured timeframe.

    Trading use:
        Signals come from this frame. A failed download leaves the previous
        candles in place and blocks a new entry.
    """
    if BROKER is None or CONFIG is None:
        raise RuntimeError("Dhan and config must be ready before fetching candles")
    moment = now or now_in_tz()
    minutes = bar_minutes()
    lookback_days = 12 if CONFIG.candle_mode == "RENKO" else 10
    start = (moment.date() - timedelta(days=lookback_days)).isoformat()
    end = moment.date().isoformat()
    response = BROKER.dhan.intraday_minute_data(
        security_id=str(STATE.underlying_security_id),
        exchange_segment=sdk_value("INDEX"),
        instrument_type="INDEX",
        from_date=start,
        to_date=end,
        interval=int(minutes),
    )
    frame = _candles_from_payload(unwrap_sdk_data(response))
    frame = drop_unconfirmed_candle(frame, moment, minutes)
    if CONFIG.candle_mode == "RENKO":
        keep = 1500
    else:
        keep = ATR_PERIOD + CONFIG.amplitude + CONFIG.rsi_period + CONFIG.sideways_lookback + 30
    if len(frame) > keep:
        frame = frame.iloc[-keep:].reset_index(drop=True)
    if CONFIG.candle_mode == "RENKO":
        STATE.minute_candles = frame
    else:
        STATE.candles = frame
    STATE.last_candle_fetch = moment
    if not frame.empty:
        STATE.source_candle_time = frame.iloc[-1]["timestamp"]
        if CONFIG.candle_mode != "RENKO":
            STATE.candle_time = STATE.source_candle_time
            STATE.last_confirmed_candle = STATE.candle_time
    return frame


def drop_unconfirmed_candle(frame: pd.DataFrame, now: datetime, minutes: int | None = None) -> pd.DataFrame:
    """Remove the candle that has not closed yet.

    Dhan timestamps are treated as the candle open. A 10:05 poll must not
    treat the 10:05 candle as confirmed when the timeframe is 5 minutes.
    Renko uses the same rule on 1-minute candles.
    """
    if CONFIG is None or frame.empty:
        return frame
    width = CONFIG.timeframe_minutes if minutes is None else minutes
    last_open = frame.iloc[-1]["timestamp"]
    if last_open.tzinfo is None:
        last_open = last_open.replace(tzinfo=CONFIG.timezone)
    if last_open + timedelta(minutes=width) > now:
        return frame.iloc[:-1].reset_index(drop=True)
    return frame.reset_index(drop=True)


def candles_need_refresh(now: datetime) -> bool:
    """True when a new confirmed source candle should exist or none are loaded.

    History is not fetched on every poll. After a candle is due, retries are
    spaced out so a slow API cannot be hammered. Renko watches the 1-minute
    clock, not the 5-minute clock.
    """
    if CONFIG is None:
        return False
    source = STATE.minute_candles if CONFIG.candle_mode == "RENKO" else STATE.candles
    if source is None or STATE.last_candle_fetch is None or STATE.source_candle_time is None:
        return True
    due_at = STATE.source_candle_time + timedelta(minutes=bar_minutes())
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
        Underlying-point targets use the index price. Premium-point targets
        use this option price. A missing quote does not count as a hit.
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
    nested = payload.get("data")
    if "last_price" not in payload and isinstance(nested, dict) and (
        "last_price" in nested or "oc" in nested
    ):
        payload = nested
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
    """Choose CE or PE from listed strikes or from a target premium.

    Purpose:
        Map a bullish signal to a call and a bearish signal to a put.

    Inputs:
        Normalized chain, spot, CE/PE, ATM/ITM/OTM/PREMIUM, and the strike step count.

    Output:
        OptionContract enriched from the security master.

    Trading use:
        ATM is the nearest listed strike that has the requested side. ITM and
        OTM move by real chain steps, not by a hardcoded point interval.
        Offset 0 on ITM/OTM means one step. PREMIUM picks the side whose LTP
        is closest to target_premium and still inside premium_tolerance.
        No premium match raises PremiumMatchError and does not order.
        The chosen id must exist in the master.
    """
    if strike_mode == "PREMIUM":
        return _select_by_premium(rows, spot, option_type)
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


class PremiumMatchError(RuntimeError):
    """Raised when no option premium is close enough to the configured target."""


def _select_by_premium(
    rows: list[dict[str, Any]],
    spot: float,
    option_type: str,
) -> OptionContract:
    """Pick the CE or PE whose premium is closest to the configured target.

    Purpose:
        Let the trader ask for a premium near a rupee amount instead of a strike step.

    Inputs:
        Chain rows, spot, and CE or PE.

    Output:
        The closest contract inside the tolerance.

    Trading use:
        An exact premium is not required. If nothing is inside the tolerance,
        the caller must not send an order.
    """
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    side = option_type.lower()
    target = CONFIG.target_premium
    tolerance = CONFIG.premium_tolerance
    candidates: list[tuple[float, float, dict[str, Any]]] = []
    for row in rows:
        if not row.get(f"{side}_security_id"):
            continue
        premium = positive_float(row.get(f"{side}_ltp"))
        if premium is None:
            continue
        distance = abs(premium - target)
        if distance <= tolerance:
            candidates.append((distance, abs(float(row["strike"]) - spot), row))
    if not candidates:
        print("NO PREMIUM MATCH")
        print(f"TARGET: ₹{target:g}")
        print(f"TOLERANCE: ₹{tolerance:g}")
        raise PremiumMatchError(
            f"NO PREMIUM MATCH target ₹{target:g} tolerance ₹{tolerance:g}"
        )
    candidates.sort(key=lambda item: (item[0], item[1], float(item[2]["strike"])))
    chosen = candidates[0][2]
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


def calculate_rsi(closes: pd.Series, period: int) -> pd.Series:
    """Calculate Wilder RSI from closes.

    Purpose:
        Provide the optional entry filter without TA-Lib.

    Inputs:
        Close prices and the RSI period.

    Output:
        A series aligned to the closes. Early rows are NaN until the period exists.

    Trading use:
        A bullish entry needs RSI at or above bullish_min. A bearish entry needs
        RSI at or below bearish_max. Disabled RSI is not calculated into the decision.

    Important:
        Each value uses only that close and older closes.
    """
    result = pd.Series(float("nan"), index=closes.index, dtype=float)
    if period < 2 or len(closes) <= period:
        return result
    delta = closes.astype(float).diff()
    gain = delta.clip(lower=0.0).fillna(0.0)
    loss = (-delta.clip(upper=0.0)).fillna(0.0)
    avg_gain = float(gain.iloc[1 : period + 1].mean())
    avg_loss = float(loss.iloc[1 : period + 1].mean())
    result.iloc[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))
    for index in range(period + 1, len(closes)):
        avg_gain = ((avg_gain * (period - 1)) + float(gain.iloc[index])) / period
        avg_loss = ((avg_loss * (period - 1)) + float(loss.iloc[index])) / period
        if avg_loss == 0:
            result.iloc[index] = 100.0
        else:
            result.iloc[index] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))
    return result


def detect_sideways_market(half_trend: pd.Series) -> bool:
    """True when the Half Trend line is flat across the configured lookback.

    Purpose:
        Block new entries when the line is not moving.

    Inputs:
        Half Trend values in time order.

    Output:
        True when max(line) - min(line) is within sideways_tolerance_points.
        Also True when the filter is on and there are not enough values yet.

    Trading use:
        The same test is used for normal candles and Renko bricks. It does not
        close an open option by itself.
    """
    if CONFIG is None or not CONFIG.sideways_filter_enabled:
        return False
    lookback = CONFIG.sideways_lookback
    if len(half_trend) < lookback:
        return True
    window = half_trend.iloc[-lookback:].astype(float)
    if window.isna().any():
        return True
    return float(window.max() - window.min()) <= CONFIG.sideways_tolerance_points


def build_renko(frame: pd.DataFrame, brick_size: float) -> pd.DataFrame:
    """Build completed Renko bricks from underlying closes.

    Purpose:
        Turn 1-minute index closes into bricks before Half Trend is calculated.

    Inputs:
        A time-ordered OHLC frame and the brick size in index points.

    Output:
        One row per completed brick: timestamp, open, high, low, close.
        Several bricks can share a source timestamp after a large jump.

    Trading use:
        A poll does not create a brick. Continuation needs one brick. A reversal
        needs two bricks. The algorithm uses closes only, so it does not invent
        an intrabar path.

    Important:
        Brick i uses only closes at or before its source timestamp.
    """
    columns = ["timestamp", "open", "high", "low", "close"]
    empty = pd.DataFrame(columns=columns)
    if frame is None or frame.empty or brick_size <= 0 or len(frame) < 2:
        return empty
    last_close = float(frame.iloc[0]["close"])
    direction = 0
    rows: list[dict[str, Any]] = []
    for index in range(1, len(frame)):
        price = float(frame.iloc[index]["close"])
        timestamp = frame.iloc[index]["timestamp"]
        while True:
            if direction >= 0 and price >= last_close + brick_size:
                brick_open = last_close
                brick_close = last_close + brick_size
                direction = 1
            elif direction <= 0 and price <= last_close - brick_size:
                brick_open = last_close
                brick_close = last_close - brick_size
                direction = -1
            elif direction == 1 and price <= last_close - (2 * brick_size):
                brick_open = last_close - brick_size
                brick_close = last_close - (2 * brick_size)
                direction = -1
            elif direction == -1 and price >= last_close + (2 * brick_size):
                brick_open = last_close + brick_size
                brick_close = last_close + (2 * brick_size)
                direction = 1
            else:
                break
            rows.append(
                {
                    "timestamp": timestamp,
                    "open": brick_open,
                    "high": max(brick_open, brick_close),
                    "low": min(brick_open, brick_close),
                    "close": brick_close,
                }
            )
            last_close = brick_close
    if not rows:
        return empty
    return pd.DataFrame(rows, columns=columns)


def generate_normal_signal(candles: pd.DataFrame) -> str | None:
    """Return BUY_CE or BUY_PE when the latest confirmed close is off the line.

    Purpose:
        Turn one confirmed close into an option-buying direction.

    Inputs:
        Frame that already contains close and half_trend.

    Output:
        BUY_CE when the close is above Half Trend.
        BUY_PE when the close is below Half Trend.
        None when the close equals the line or the line is not ready.

    Trading use:
        The caller must run this only for a new confirmed candle. An open
        position blocks another buy. This is not a two-candle cross.
    """
    if candles is None or candles.empty or "half_trend" not in candles.columns:
        return None
    close = float(candles.iloc[-1]["close"])
    line = float(candles.iloc[-1]["half_trend"])
    if math.isnan(close) or math.isnan(line):
        return None
    if close > line:
        return "BUY_CE"
    if close < line:
        return "BUY_PE"
    return None


def generate_signal(candles: pd.DataFrame) -> str | None:
    """Direction of the latest confirmed close versus Half Trend."""
    return generate_normal_signal(candles)


def _brick_qualifies(row: pd.Series, side: str) -> bool:
    """True when a completed brick has the required direction and line side."""
    close = float(row["close"])
    brick_open = float(row["open"])
    line = float(row["half_trend"])
    if any(math.isnan(value) for value in (close, brick_open, line)):
        return False
    if side == "CE":
        return close > brick_open and close > line
    return close < brick_open and close < line


def _trailing_brick_count(candles: pd.DataFrame, side: str) -> int:
    """Count consecutive qualifying bricks at the end of the Renko series."""
    count = 0
    for _, row in candles.iloc[::-1].iterrows():
        if _brick_qualifies(row, side):
            count += 1
        else:
            break
    return count


def generate_renko_signal(candles: pd.DataFrame) -> str | None:
    """Return a Renko entry only after enough consecutive qualifying bricks.

    Purpose:
        Require the configured confirmation count before a Renko entry.

    Inputs:
        Completed Renko bricks that already contain Half Trend.

    Output:
        BUY_CE, BUY_PE, or None.

    Trading use:
        One brick is the default. The same brick is not traded twice because
        the caller tracks the brick identity.
    """
    if CONFIG is None or candles is None or candles.empty or "half_trend" not in candles.columns:
        return None
    needed = CONFIG.renko_entry_confirmation_bricks
    bullish = _trailing_brick_count(candles, "CE")
    bearish = _trailing_brick_count(candles, "PE")
    STATE.entry_confirm_count = max(bullish, bearish)
    if bullish >= needed:
        return "BUY_CE"
    if bearish >= needed:
        return "BUY_PE"
    return None


def renko_exit_ready(candles: pd.DataFrame, option_type: str) -> bool:
    """True when enough Renko bricks have closed on the wrong side of Half Trend.

    Purpose:
        Exit a call after consecutive bricks below the line, and a put after
        consecutive bricks above the line.

    Inputs:
        Completed bricks and CE or PE.

    Output:
        True when the trailing count reaches exit_confirmation_bricks.

    Trading use:
        An incomplete brick cannot increment the count. A brick that is not
        on the wrong side resets the trailing count.
    """
    if CONFIG is None or candles is None or candles.empty:
        STATE.exit_confirm_count = 0
        return False
    count = 0
    for _, row in candles.iloc[::-1].iterrows():
        close = float(row["close"])
        line = float(row["half_trend"])
        if math.isnan(close) or math.isnan(line):
            break
        opposite = (option_type == "CE" and close < line) or (option_type == "PE" and close > line)
        if opposite:
            count += 1
        else:
            break
    STATE.exit_confirm_count = count
    return count >= CONFIG.renko_exit_confirmation_bricks


def describe_candle_side(candles: pd.DataFrame) -> str:
    """Explain the latest close versus the line."""
    signal = generate_normal_signal(candles)
    if signal == "BUY_CE":
        return "Confirmed close is above Half Trend"
    if signal == "BUY_PE":
        return "Confirmed close is below Half Trend"
    if candles is None or candles.empty or "half_trend" not in candles.columns:
        return "Half Trend is not ready"
    close = float(candles.iloc[-1]["close"])
    line = float(candles.iloc[-1]["half_trend"])
    if math.isnan(close) or math.isnan(line):
        return "Half Trend is not ready"
    return "Close equals Half Trend"


def is_new_confirmed_candle(bar_id: str | None = None) -> bool:
    """True when this confirmed bar has not been evaluated yet.

    Purpose:
        Separate the 5-second poll from a new candle or Renko brick.

    Inputs:
        Bar identity. When omitted, the state's latest bar id is used.

    Output:
        False for the same bar that was already evaluated.

    Trading use:
        Prevents one bullish candle from buying a call on every poll.
        Renko bricks that share a source timestamp still have different ids.
    """
    identity = STATE.bar_id if bar_id is None else bar_id
    if identity is None:
        return False
    return identity != STATE.last_evaluated_bar


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


def _series_bar_id(frame: pd.DataFrame) -> str:
    """Identity of the latest bar. Renko includes the row count so shared timestamps stay unique."""
    timestamp = frame.iloc[-1]["timestamp"]
    stamp = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
    close = float(frame.iloc[-1]["close"])
    return f"{stamp}|{len(frame)}|{close:.4f}"


def _refresh_filters(frame: pd.DataFrame) -> None:
    """Store RSI, sideways state, and the latest line for the dashboard."""
    if CONFIG is None or frame.empty:
        return
    last = frame.iloc[-1]
    line = float(last["half_trend"])
    STATE.half_trend_value = None if math.isnan(line) else line
    close = float(last["close"])
    if CONFIG.candle_mode == "RENKO":
        STATE.brick_state = "BULLISH" if close > float(last["open"]) else "BEARISH" if close < float(last["open"]) else "FLAT"
    if STATE.half_trend_value is None:
        STATE.ht_position = "-"
    elif close > line:
        STATE.ht_position = "ABOVE"
    elif close < line:
        STATE.ht_position = "BELOW"
    else:
        STATE.ht_position = "EQUAL"
    rsi = float(last["rsi"]) if "rsi" in frame.columns else float("nan")
    STATE.rsi_value = None if math.isnan(rsi) else rsi
    if not CONFIG.sideways_filter_enabled:
        STATE.market_state = "FILTER OFF"
    elif len(frame) < CONFIG.sideways_lookback or frame["half_trend"].iloc[-CONFIG.sideways_lookback :].isna().any():
        STATE.market_state = "WAITING"
    elif detect_sideways_market(frame["half_trend"]):
        STATE.market_state = "SIDEWAYS"
    else:
        STATE.market_state = "TRENDING"
    direction = generate_normal_signal(frame)
    if not CONFIG.rsi_enabled:
        STATE.rsi_filter = "DISABLED"
    elif STATE.rsi_value is None:
        STATE.rsi_filter = "WAITING"
    elif direction == "BUY_CE":
        STATE.rsi_filter = "PASS" if STATE.rsi_value >= CONFIG.rsi_bullish_min else "FAIL"
    elif direction == "BUY_PE":
        STATE.rsi_filter = "PASS" if STATE.rsi_value <= CONFIG.rsi_bearish_max else "FAIL"
    else:
        STATE.rsi_filter = "WAITING"


def entry_filters_pass(direction: str | None) -> bool:
    """True when sideways and RSI allow this direction to become an entry.

    Purpose:
        Keep one filter path for normal candles and Renko.

    Inputs:
        BUY_CE, BUY_PE, or None.

    Output:
        False when the direction is missing, the line is flat, or RSI fails.

    Trading use:
        A failed filter blocks the entry and writes the dashboard reason.
        It does not by itself close an open option.
    """
    STATE.entry_block_reason = ""
    if direction is None or CONFIG is None:
        return False
    if STATE.market_state in {"SIDEWAYS", "WAITING"} and CONFIG.sideways_filter_enabled:
        if STATE.market_state == "SIDEWAYS":
            STATE.entry_block_reason = "ENTRY BLOCKED: HALF TREND SIDEWAYS"
        else:
            STATE.entry_block_reason = "ENTRY BLOCKED: HALF TREND NOT READY"
        return False
    if CONFIG.rsi_enabled and STATE.rsi_filter != "PASS":
        if STATE.rsi_filter == "WAITING":
            STATE.entry_block_reason = "ENTRY BLOCKED: RSI NOT READY"
        else:
            STATE.entry_block_reason = "ENTRY BLOCKED: RSI FILTER FAILED"
        return False
    return True


def refresh_indicator(now: datetime) -> None:
    """Recalculate Half Trend, RSI, and Renko when a new source candle is due.

    Purpose:
        Keep the signal series current without downloading history every poll.

    Inputs:
        Current exchange time.

    Output:
        None. Updates STATE.candles and the dashboard fields.

    Trading use:
        Normal mode calculates Half Trend on confirmed OHLC candles.
        Renko mode builds bricks from 1-minute closes and calculates Half Trend
        on those bricks only.
    """
    if CONFIG is None:
        return
    if candles_need_refresh(now):
        fetch_underlying_candles(now)
    source = STATE.minute_candles if CONFIG.candle_mode == "RENKO" else STATE.candles
    if source is None or source.empty:
        STATE.half_trend_value = None
        STATE.signal_reason = "No confirmed candles yet"
        return
    if CONFIG.candle_mode == "RENKO":
        bricks = build_renko(source, CONFIG.renko_brick_size_points)
        if bricks.empty:
            STATE.candles = bricks
            STATE.half_trend_value = None
            STATE.signal_reason = "No completed Renko brick yet"
            STATE.bar_id = None
            return
        series = bricks
    else:
        series = source
    calculated = calculate_half_trend(series, CONFIG.amplitude, CONFIG.channel_deviation)
    calculated["rsi"] = calculate_rsi(calculated["close"], CONFIG.rsi_period)
    STATE.candles = calculated
    STATE.candle_time = calculated.iloc[-1]["timestamp"]
    STATE.last_confirmed_candle = STATE.candle_time
    STATE.bar_id = _series_bar_id(calculated)
    _refresh_filters(calculated)


def consider_entry(signal: str, bar_id: str) -> None:
    """Enter once for a fresh signal, or remember it when the block is temporary."""
    if STATE.last_signal_bar == bar_id or STATE.entry_skip_bar == bar_id:
        return
    allowed, reason = validate_entry(signal)
    if not allowed:
        note(f"Entry skipped: {reason}")
        STATE.entry_block_reason = f"ENTRY BLOCKED: {reason}"
        if reason in RETRYABLE_ENTRY_BLOCKS:
            STATE.deferred_signal = signal
            STATE.deferred_bar = bar_id
        else:
            STATE.last_signal_bar = bar_id
            STATE.deferred_signal = None
        return
    STATE.deferred_signal = None
    place_entry_order(signal, bar_id)
    STATE.last_signal_bar = bar_id


def process_signal() -> None:
    """Evaluate a new confirmed bar and maybe enter or exit on the opposite side.

    An opposite-signal exit does not reverse in the same cycle. A deferred
    signal from earlier in the same bar can still enter once the block clears.
    Normal mode uses one close versus Half Trend. Renko mode uses the configured
    brick counts for entry and for the opposite exit.
    """
    if CONFIG is None or STATE.candles is None or STATE.bar_id is None:
        return
    bar_id = STATE.bar_id
    if STATE.deferred_bar is not None and STATE.deferred_bar != bar_id:
        STATE.deferred_signal = None
        STATE.deferred_bar = None
    if is_new_confirmed_candle(bar_id):
        STATE.last_evaluated_bar = bar_id
        _refresh_filters(STATE.candles)
        if CONFIG.candle_mode == "RENKO":
            direction = generate_renko_signal(STATE.candles)
            position = get_current_position()
            if position is not None:
                renko_exit_ready(STATE.candles, position.option_type)
            STATE.signal_reason = describe_candle_side(STATE.candles)
            if direction is None and STATE.entry_block_reason == "":
                needed = CONFIG.renko_entry_confirmation_bricks
                STATE.signal_reason = f"Renko confirmation {STATE.entry_confirm_count}/{needed}"
        else:
            direction = generate_normal_signal(STATE.candles)
            STATE.entry_confirm_count = 1 if direction else 0
            STATE.signal_reason = describe_candle_side(STATE.candles)
        filtered = direction if entry_filters_pass(direction) else None
        if STATE.entry_block_reason:
            STATE.signal_reason = STATE.entry_block_reason
        STATE.signal_label = signal_label(filtered or direction)
        if direction is not None:
            STATE.last_signal = direction
        opposite = False
        if STATE.data_status == "OK" and CONFIG.exit_on_opposite_signal:
            if CONFIG.candle_mode == "RENKO":
                position = get_current_position()
                opposite = position is not None and renko_exit_ready(STATE.candles, position.option_type)
            else:
                opposite = direction is not None and check_opposite_signal(direction)
        if opposite:
            close_current_position("OPPOSITE_SIGNAL")
            STATE.entry_skip_bar = bar_id
            STATE.last_signal_bar = bar_id
            STATE.signal_reason = "EXIT: OPPOSITE SIGNAL"
            return
        if filtered is not None and STATE.status == "FLAT":
            consider_entry(filtered, bar_id)
            return
    if (
        STATE.status == "FLAT"
        and STATE.deferred_signal is not None
        and STATE.deferred_bar == bar_id
        and STATE.last_signal_bar != bar_id
        and STATE.entry_skip_bar != bar_id
    ):
        consider_entry(STATE.deferred_signal, bar_id)


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
    filled_at = now_in_tz()
    position = build_position(contract, signal, price, quantity, "PAPER", next_order_tag("E"))
    position.entry_time = filled_at
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
        f"CURRENT OPTION LTP: {format_inr(contract.ltp or STATE.option_ltp)}",
        f"UNDERLYING LTP: {format_number(STATE.underlying_ltp)}",
        f"TP:            {format_number(tp_price) if CONFIG.tp_sl_mode == 'UNDERLYING_POINTS' else format_inr(tp_price)}",
        f"SL:            {format_number(sl_price) if CONFIG.tp_sl_mode == 'UNDERLYING_POINTS' else format_inr(sl_price)}",
        f"TP/SL MODE:    {CONFIG.tp_sl_mode}",
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
    reference = price if price > 0 else (contract.ltp or price)
    preview_tp, preview_sl = calculate_tp_sl(
        reference,
        STATE.underlying_ltp or 0.0,
        contract.option_type,
        contract.tick_size,
    )
    tag = next_order_tag("E")
    preview = build_order_preview(contract, "BUY", quantity, price, signal, preview_tp, preview_sl)
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
    position = build_position(
        contract,
        STATE.last_signal or ("BUY_CE" if contract.option_type == "CE" else "BUY_PE"),
        fill,
        order_quantity(contract),
        STATE.pending_order_id or "",
        STATE.pending_tag or "",
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
    print("EXISTING LIVE POSITION DETECTED")
    note("Restored index TP/SL uses the current underlying price because the original index entry was not stored.")
    STATE.position = build_position(
        contract,
        "BUY_CE" if option_type == "CE" else "BUY_PE",
        entry,
        quantity,
        "RESTORED",
        ALGO_TAG_PREFIX,
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


def calculate_tp_sl(
    entry_premium: float,
    underlying_price: float,
    option_type: str,
    tick_size: float,
) -> tuple[float | None, float | None]:
    """Turn configured points into a take-profit level and a stop-loss level.

    Purpose:
        Keep the default target on the underlying index, with an optional premium mode.

    Inputs:
        Option fill, index price at entry, CE or PE, and the option tick size.

    Output:
        (take-profit level or None, stop-loss level or None).

    Trading use:
        UNDERLYING_POINTS on a call at 82,000 with 100/50 is 82,100 and 81,950.
        The same points on a put are 81,900 and 82,050.
        OPTION_PREMIUM_POINTS adds or subtracts points from the long premium.
        A disabled side returns None.
    """
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    take_profit: float | None = None
    stop_loss: float | None = None
    if CONFIG.tp_sl_mode == "UNDERLYING_POINTS":
        if underlying_price <= 0:
            raise RuntimeError("Underlying entry price is required for index-point TP/SL")
        direction = 1.0 if option_type == "CE" else -1.0
        if CONFIG.tp_enabled:
            take_profit = underlying_price + (direction * CONFIG.tp_points)
        if CONFIG.sl_enabled:
            stop_loss = underlying_price - (direction * CONFIG.sl_points)
        return take_profit, stop_loss
    if CONFIG.tp_enabled:
        take_profit = round_to_tick(entry_premium + CONFIG.tp_points, tick_size)
    if CONFIG.sl_enabled:
        stop_loss = round_to_tick(entry_premium - CONFIG.sl_points, tick_size)
        if stop_loss <= 0:
            raise RuntimeError("Stop loss rounded to a non-positive premium")
    if take_profit is not None and stop_loss is not None and stop_loss >= take_profit:
        raise RuntimeError("Stop loss must be below take profit")
    return take_profit, stop_loss


def build_position(
    contract: OptionContract,
    signal: str,
    entry_price: float,
    quantity: int,
    order_id: str,
    order_tag: str,
    underlying_price: float | None = None,
) -> Position:
    """Create the single long-option position and its TP/SL levels.

    Purpose:
        Use one construction path for paper fills, live fills, and restored positions.

    Inputs:
        Contract, signal, option fill, quantity, order identity, and the index price.

    Output:
        A Position. The index price is stored even when targets use the premium.

    Trading use:
        Index targets are measured from this underlying price, not from a later quote.
    """
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    underlying = STATE.underlying_ltp if underlying_price is None else underlying_price
    if underlying is None:
        raise RuntimeError("Cannot open a position without the underlying price")
    tp_price, sl_price = calculate_tp_sl(entry_price, underlying, contract.option_type, contract.tick_size)
    return Position(
        security_id=contract.security_id,
        trading_symbol=contract.trading_symbol,
        option_type=contract.option_type,
        strike=contract.strike,
        expiry=contract.expiry,
        quantity=quantity,
        lot_size=contract.lot_size,
        tick_size=contract.tick_size,
        entry_price=entry_price,
        underlying_entry=underlying,
        product_type=CONFIG.product_type,
        exchange_segment=contract.exchange_segment,
        entry_time=now_in_tz(),
        order_id=order_id,
        order_tag=order_tag,
        signal=signal,
        tp_price=tp_price,
        sl_price=sl_price,
    )


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


def place_entry_order(signal: str, bar_id: str) -> None:
    """Select the option and hand it to paper or live entry.

    Purpose:
        One entry door so the strategy does not call the broker itself.

    Inputs:
        BUY_CE or BUY_PE, and the bar identity that created it.

    Output:
        None. PAPER becomes LONG_OPTION. LIVE becomes ENTRY_PENDING or LONG_OPTION.

    Trading use:
        The option chain is fetched here, not on every poll. PAPER cannot reach
        the live order function. BACKTEST never calls this function.
    """
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    if CONFIG.trading_mode == "BACKTEST":
        raise RuntimeError("BACKTEST cannot place an order")
    if STATE.last_signal_bar == bar_id:
        return
    if STATE.status != "FLAT" or STATE.position is not None:
        note("Entry skipped: NOT_FLAT")
        return
    spot = STATE.underlying_ltp
    if spot is None:
        raise RuntimeError("Cannot select a strike without the underlying price")
    _spot, rows = fetch_option_chain(STATE.resolved_expiry)
    try:
        contract = select_option_contract(
            rows,
            spot,
            option_side(signal),
            CONFIG.selection_mode,
            CONFIG.strike_offset,
        )
    except PremiumMatchError as exc:
        STATE.entry_block_reason = "ENTRY BLOCKED: NO PREMIUM MATCH"
        STATE.signal_reason = STATE.entry_block_reason
        STATE.last_signal_bar = bar_id
        note(str(exc))
        return
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
    tp_price, sl_price = calculate_tp_sl(price, spot, contract.option_type, contract.tick_size)
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


def _target_reached(position: Position, level: float | None, take_profit: bool) -> bool:
    """True when the watched price has reached one TP or SL level.

    Underlying mode watches the index. Premium mode watches the option.
    A missing price is not a hit. Calls and puts use opposite index directions.
    """
    if position is None or level is None or CONFIG is None:
        return False
    if CONFIG.tp_sl_mode == "UNDERLYING_POINTS":
        price = STATE.underlying_ltp
        if price is None:
            return False
        if position.option_type == "CE":
            return price >= level if take_profit else price <= level
        return price <= level if take_profit else price >= level
    if STATE.option_ltp is None or STATE.quote_failed:
        return False
    return STATE.option_ltp >= level if take_profit else STATE.option_ltp <= level


def check_take_profit() -> bool:
    """True when the configured take-profit level has been reached.

    Missing prices do not count as a hit.
    """
    position = get_current_position()
    if position is None or CONFIG is None or not CONFIG.tp_enabled or STATE.status != "LONG_OPTION":
        return False
    return _target_reached(position, position.tp_price, True)


def check_stop_loss() -> bool:
    """True when the configured stop-loss level has been reached.

    Missing prices do not count as a hit.
    """
    position = get_current_position()
    if position is None or CONFIG is None or not CONFIG.sl_enabled or STATE.status != "LONG_OPTION":
        return False
    return _target_reached(position, position.sl_price, False)


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
    Daily loss always stops the bot. TP and SL stop the bot only when
    stop_bot_after_tp_sl is true. A same-cycle re-entry is still blocked.
    A live exit that is still pending stores the reason and finishes later.
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
        if CONFIG is not None and CONFIG.no_reentry_after_stop_loss:
            STATE.stop_loss_hit = True
        reason = "SL"
    elif check_daily_loss():
        STATE.max_loss_hit = True
        reason = "MAX_DAILY_LOSS"
    if reason is None:
        return False
    stop_after = reason == "MAX_DAILY_LOSS" or (reason in {"TP", "SL"} and CONFIG is not None and CONFIG.stop_bot_after_tp_sl)
    if stop_after:
        STATE.shutdown_after_exit = reason
    STATE.entry_skip_bar = STATE.bar_id
    if close_current_position(reason):
        if stop_after:
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
        Apply the scheduled window when run_mode is SCHEDULED.

    Inputs:
        Current time in the configured timezone.

    Output:
        OPEN when a new entry is allowed by the clock.
        CONTINUOUS mode stays OPEN.

    Trading use:
        AFTER_STOP can close and exit. BEFORE_START only waits.
    """
    if CONFIG is None:
        return "BEFORE_START"
    current = moment or now_in_tz()
    if CONFIG.run_mode == "CONTINUOUS":
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
    should_close = CONFIG.close_all_positions_at_stop
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
    candle_stale = STATE.source_candle_time is None
    if CONFIG is not None and STATE.source_candle_time is not None:
        age_limit = timedelta(minutes=bar_minutes() * 2)
        candle_stale = (moment - STATE.source_candle_time) > age_limit
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
    lot_size = STATE.sample_lot_size or "-"
    if STATE.sample_lot_size:
        quantity = str(CONFIG.lots * STATE.sample_lot_size)
    if CONFIG.candle_mode == "RENKO":
        chart = f"RENKO brick {CONFIG.renko_brick_size_points:g} points"
    else:
        chart = f"NORMAL {CONFIG.timeframe_minutes}m"
    rsi_text = "ENABLED" if CONFIG.rsi_enabled else "DISABLED"
    if CONFIG.selection_mode == "PREMIUM":
        selection = f"PREMIUM target {CONFIG.target_premium:g} tolerance {CONFIG.premium_tolerance:g}"
    else:
        selection = f"{CONFIG.selection_mode} offset {CONFIG.strike_offset}"
    lines = [
        f"BOT NAME: {BOT_NAME}",
        f"VERSION: {BOT_VERSION}",
        f"TRADING MODE: {CONFIG.trading_mode}",
        f"CANDLE MODE: {CONFIG.candle_mode}",
        f"UNDERLYING: {CONFIG.underlying}  id {STATE.underlying_security_id}  ({STATE.underlying_symbol})",
        f"TIMEFRAME / RENKO BRICK: {chart}",
        f"HALFTREND: amplitude {CONFIG.amplitude}, channel {CONFIG.channel_deviation}",
        f"SIDEWAYS FILTER: {CONFIG.sideways_filter_enabled} lookback {CONFIG.sideways_lookback} tolerance {CONFIG.sideways_tolerance_points:g}",
        f"RSI STATUS: {rsi_text} period {CONFIG.rsi_period} bullish>={CONFIG.rsi_bullish_min:g} bearish<={CONFIG.rsi_bearish_max:g}",
        f"OPTION SELECTION: {selection}",
        f"EXPIRY: {CONFIG.expiry_mode} {STATE.resolved_expiry}",
        f"LOTS: {CONFIG.lots}",
        f"LOT SIZE: {lot_size}",
        f"QUANTITY: {quantity}",
        f"ORDER: {CONFIG.order_type}  PRODUCT: {CONFIG.product_type}",
        f"TP/SL MODE: {CONFIG.tp_sl_mode}",
        f"TP: {CONFIG.tp_points:g} points enabled={CONFIG.tp_enabled}",
        f"SL: {CONFIG.sl_points:g} points enabled={CONFIG.sl_enabled}",
        f"STOP AFTER TP/SL: {CONFIG.stop_bot_after_tp_sl}",
        f"MAX LOSS: {format_inr(CONFIG.max_loss_per_day_inr)}",
        f"MAX ORDERS: {CONFIG.max_orders_per_day}",
        f"RUN MODE: {CONFIG.run_mode}",
        f"SESSION: {CONFIG.start_time}-{CONFIG.stop_time}",
        f"TIMEZONE: {CONFIG.timezone_name}",
        f"POLLING: {CONFIG.polling_seconds}s  Post-close: {CONFIG.post_close_polls} x {CONFIG.post_close_poll_seconds}s",
        "P&L shown by this bot is gross option premium, before charges and slippage.",
        "Half Trend is a transparent standard-style calculation. It is not claimed to match Dhan byte for byte.",
    ]
    print("\n".join(lines))
    if CONFIG.trading_mode == "LIVE":
        print("!!! WARNING: LIVE TRADING ENABLED !!!")


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
    index_name = CONFIG.underlying
    if position is None:
        option_name = "-"
        position_text = "FLAT"
        quantity = "-"
        entry = "-"
        index_entry = "-"
        take_profit = "-"
        stop_loss = "-"
        current_pnl = format_inr(0.0)
    else:
        option_name = position.trading_symbol
        position_text = f"LONG {position.option_type}"
        quantity = str(position.quantity)
        entry = format_inr(position.entry_price)
        index_entry = format_number(position.underlying_entry)
        if CONFIG.tp_sl_mode == "UNDERLYING_POINTS":
            take_profit = format_number(position.tp_price)
            stop_loss = format_number(position.sl_price)
        else:
            take_profit = format_inr(position.tp_price)
            stop_loss = format_inr(position.sl_price)
        current_pnl = format_inr(STATE.unrealized_pnl)
    if not check_order_limit() and STATE.status == "FLAT":
        order_text = f"{STATE.orders_today} / {CONFIG.max_orders_per_day} MAX ORDERS"
    else:
        order_text = f"{STATE.orders_today} / {CONFIG.max_orders_per_day}"
    candle_text = STATE.candle_time.strftime("%H:%M:%S") if STATE.candle_time else "-"
    rsi_text = "DISABLED" if STATE.rsi_filter == "DISABLED" else (
        "-" if STATE.rsi_value is None else f"{STATE.rsi_value:.2f}"
    )
    if CONFIG.candle_mode == "RENKO":
        strategy = "RENKO + HALFTREND" + (" + RSI" if CONFIG.rsi_enabled else "")
    else:
        strategy = "NORMAL + HALFTREND" + (" + RSI" if CONFIG.rsi_enabled else "")
    why = STATE.entry_block_reason or STATE.signal_reason
    rows = [
        _box_rule("╔", "═", "╗"),
        _box_row(BOT_NAME),
        _box_rule("╠", "═", "╣"),
        _box_row(f"MODE        : {CONFIG.trading_mode}"),
        _box_row(f"STRATEGY    : {strategy}"),
        _box_row(f"UNDERLYING  : {CONFIG.underlying}"),
        _box_row(f"MARKET      : {STATE.market_status}"),
        _box_row(f"DATA        : {STATE.data_status}"),
        _box_rule("╠", "═", "╣"),
        _box_row(f"{index_name} LTP  : {format_number(STATE.underlying_ltp)}"),
        _box_row(f"CANDLE      : {CONFIG.timeframe_minutes}M" if CONFIG.candle_mode == "NORMAL" else f"RENKO BRICK : {CONFIG.renko_brick_size_points:g} POINTS"),
        _box_row(f"CANDLE TIME : {candle_text}"),
        _box_row(f"HALF TREND  : {format_number(STATE.half_trend_value)}"),
        _box_row(f"RSI         : {rsi_text}"),
        _box_row(f"RSI FILTER  : {STATE.rsi_filter}"),
        _box_row(f"MARKET STATE: {STATE.market_state}"),
        _box_row(f"SIGNAL      : {STATE.signal_label}"),
        _box_row(f"WHY         : {why}"),
    ]
    if CONFIG.candle_mode == "RENKO":
        rows.extend(
            [
                _box_row(f"BRICK STATE : {STATE.brick_state}"),
                _box_row(f"HT POSITION : {STATE.ht_position}"),
                _box_row(f"ENTRY CONFIRM: {STATE.entry_confirm_count} / {CONFIG.renko_entry_confirmation_bricks}"),
                _box_row(f"EXIT CONFIRM : {STATE.exit_confirm_count} / {CONFIG.renko_exit_confirmation_bricks}"),
            ]
        )
    rows.extend(
        [
            _box_rule("╠", "═", "╣"),
            _box_row(f"OPTION      : {option_name}"),
            _box_row(f"PREMIUM     : {format_inr(STATE.option_ltp)}"),
            _box_row(f"POSITION    : {position_text}"),
            _box_row(f"STATE       : {STATE.status}"),
            _box_row(f"QUANTITY    : {quantity}"),
            _box_row(f"ENTRY       : {entry}"),
            _box_row(f"CURRENT P&L : {current_pnl}"),
            _box_rule("╠", "═", "╣"),
            _box_row(f"{index_name} ENTRY: {index_entry}"),
            _box_row(f"{index_name} NOW  : {format_number(STATE.underlying_ltp)}"),
            _box_row(f"TP          : {take_profit}"),
            _box_row(f"SL          : {stop_loss}"),
            _box_rule("╠", "═", "╣"),
            _box_row(f"TODAY P&L   : {format_inr(calculate_combined_algo_pnl())}"),
            _box_row(f"ORDERS      : {order_text}"),
            _box_row(f"DAILY LIMIT : -{format_inr(CONFIG.max_loss_per_day_inr)}"),
            _box_row(f"POLLING     : {CONFIG.polling_seconds} SEC"),
            _box_row(f"NEXT POLL   : {STATE.next_poll_seconds} SEC"),
            _box_row(f"POST CLOSE  : {STATE.post_close_polls_done}/{CONFIG.post_close_polls}"),
            _box_row(f"LAST ACTION : {STATE.last_action}"),
            _box_rule("╚", "═", "╝"),
        ]
    )
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
                CONFIG.close_all_positions_at_stop
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


# ============================================================
# BACKTEST ENGINE
# ============================================================

PREMIUM_HISTORY_UNAVAILABLE = (
    "Historical option premium data is unavailable for this backtest configuration."
)


def _date_chunks(start: date, end: date, days: int = 5) -> list[tuple[date, date]]:
    """Split a date range so one history request does not ask for unlimited data."""
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=days - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _concat_candles(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Combine history chunks and keep one row per timestamp."""
    if not frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    data = pd.concat(frames, ignore_index=True)
    data = data.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    return data.reset_index(drop=True)


def _fetch_underlying_history(start: date, end: date, interval: int) -> pd.DataFrame:
    """Download index candles for the requested backtest range, in short chunks."""
    if BROKER is None or CONFIG is None:
        raise RuntimeError("Dhan and config must be ready before a backtest")
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for chunk_start, chunk_end in _date_chunks(start, end):
        try:
            response = BROKER.dhan.intraday_minute_data(
                security_id=str(STATE.underlying_security_id),
                exchange_segment=sdk_value("INDEX"),
                instrument_type="INDEX",
                from_date=chunk_start.isoformat(),
                to_date=chunk_end.isoformat(),
                interval=int(interval),
            )
            frame = _candles_from_payload(unwrap_sdk_data(response))
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:
            errors.append(str(exc))
        time.sleep(0.25)
    data = _concat_candles(frames)
    if data.empty:
        detail = errors[-1] if errors else "Dhan returned no candles"
        raise RuntimeError(f"No underlying history was returned. {detail}")
    stamps = data["timestamp"].map(lambda value: value.date())
    data = data[(stamps >= start) & (stamps <= end)].reset_index(drop=True)
    return data


def _rolling_strike(option_type: str) -> str:
    """Map ATM/ITM/OTM to the rolling-option strike code Dhan accepts."""
    if CONFIG is None or CONFIG.selection_mode == "ATM":
        return "ATM"
    steps = max(CONFIG.strike_offset, 1)
    if option_type == "CE":
        signed = -steps if CONFIG.selection_mode == "ITM" else steps
    else:
        signed = steps if CONFIG.selection_mode == "ITM" else -steps
    if signed > 0:
        return f"ATM+{signed}"
    if signed < 0:
        return f"ATM{signed}"
    return "ATM"


def _option_candles_from_rolling_payload(payload: Any, drv_option_type: str) -> pd.DataFrame:
    """Parse Dhan rolling-option history into OHLC rows.

    Purpose:
        The rolling-option API nests CE/PE under data.data and may return empty
        iv/oi/strike lists beside full OHLC lists. Only equal-length OHLC fields
        are kept so pandas does not reject the payload.

    Inputs:
        Unwrapped SDK data and CALL or PUT.

    Output:
        timestamp/open/high/low/close frame, or empty when that side is missing.
    """
    empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    if payload is None:
        return empty
    body = payload
    if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
        nested = body["data"]
        if any(key in nested for key in ("ce", "pe", "CE", "PE")):
            body = nested
    if not isinstance(body, dict):
        return empty
    side_key = "ce" if str(drv_option_type).upper() == "CALL" else "pe"
    block = body.get(side_key)
    if block is None:
        block = body.get(side_key.upper())
    if not isinstance(block, dict):
        return empty
    lengths = {
        key: len(value)
        for key, value in block.items()
        if isinstance(value, list)
    }
    needed = ("timestamp", "open", "high", "low", "close")
    if any(name not in lengths or lengths[name] == 0 for name in needed):
        return empty
    size = lengths["timestamp"]
    if any(lengths[name] != size for name in needed):
        return empty
    cleaned = {name: block[name] for name in needed}
    if "volume" in lengths and lengths["volume"] == size:
        cleaned["volume"] = block["volume"]
    return _candles_from_payload(cleaned)


def _fetch_rolling_option_history(
    strike: str,
    drv_option_type: str,
    start: date,
    end: date,
    interval: int,
) -> pd.DataFrame:
    """Download rolling expired-option candles. An empty frame means the API gave no premiums."""
    if BROKER is None or CONFIG is None:
        raise RuntimeError("Dhan and config must be ready before a backtest")
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    # Dhan allows up to 30 days per rolling-option call.
    for chunk_start, chunk_end in _date_chunks(start, end, days=30):
        try:
            response = BROKER.dhan.expired_options_data(
                security_id=int(str(STATE.underlying_security_id)),
                exchange_segment=sdk_value(CONFIG.option_segment),
                instrument_type="OPTIDX",
                expiry_flag="WEEK",
                expiry_code=1,
                strike=strike,
                drv_option_type=drv_option_type,
                required_data=["open", "high", "low", "close"],
                from_date=chunk_start.isoformat(),
                to_date=chunk_end.isoformat(),
                interval=int(interval),
            )
            if isinstance(response, dict) and response.get("status") == "failure":
                remarks = response.get("remarks")
                errors.append(str(remarks or "rolling-option failure"))
                continue
            frame = _option_candles_from_rolling_payload(unwrap_sdk_data(response), drv_option_type)
            if not frame.empty:
                frames.append(frame)
            else:
                errors.append(
                    f"No {drv_option_type} OHLC in rolling-option response "
                    f"for {chunk_start.isoformat()} to {chunk_end.isoformat()}"
                )
        except Exception as exc:
            errors.append(str(exc))
        time.sleep(0.25)
    data = _concat_candles(frames)
    if data.empty:
        if errors:
            note(f"Rolling option history empty: {errors[-1]}")
        return data
    stamps = data["timestamp"].map(lambda value: value.date())
    return data[(stamps >= start) & (stamps <= end)].reset_index(drop=True)


def _current_option_lot(master: pd.DataFrame) -> int:
    """Read one current index-option lot size for the configured underlying."""
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    spec = underlying_spec(CONFIG.underlying)
    exchange = master["SEM_EXM_EXCH_ID"].astype(str).str.upper().str.strip()
    instrument = master["SEM_INSTRUMENT_NAME"].astype(str).str.upper().str.strip()
    option_type = master["SEM_OPTION_TYPE"].astype(str).str.upper().str.strip()
    symbol = master["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.strip()
    prefix = f"{CONFIG.underlying}-"
    rows = master[
        (exchange == spec["option_exchange"])
        & instrument.eq("OPTIDX")
        & option_type.isin({"CE", "CALL"})
        & symbol.str.startswith(prefix)
    ]
    if rows.empty:
        raise RuntimeError(f"Security master has no {CONFIG.underlying} option lot size")
    lot = _as_int(rows.iloc[0]["SEM_LOT_UNITS"], "lot size")
    if lot < 1:
        raise RuntimeError("Security master lot size is not usable")
    return lot


def _align_premium(signal_frame: pd.DataFrame, option_frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Attach the latest known option OHLC at or before each signal timestamp."""
    right = option_frame[["timestamp", "high", "low", "close"]].rename(
        columns={"high": f"{prefix}_high", "low": f"{prefix}_low", "close": f"{prefix}_close"}
    )
    left = signal_frame.sort_values("timestamp")
    right = right.sort_values("timestamp")
    return pd.merge_asof(left, right, on="timestamp", direction="backward")


def _same_bar_exit(option_type: str, high: float, low: float, take_profit: float | None, stop_loss: float | None) -> str | None:
    """Return TP or SL when this bar could have touched a level. Both touches count as SL.

    Candle history cannot prove which level printed first. The stop is the
    conservative result.
    """
    if CONFIG is None:
        return None
    hit_sl = False
    hit_tp = False
    if CONFIG.tp_sl_mode == "UNDERLYING_POINTS":
        if option_type == "CE":
            hit_sl = stop_loss is not None and low <= stop_loss
            hit_tp = take_profit is not None and high >= take_profit
        else:
            hit_sl = stop_loss is not None and high >= stop_loss
            hit_tp = take_profit is not None and low <= take_profit
    else:
        hit_sl = stop_loss is not None and low <= stop_loss
        hit_tp = take_profit is not None and high >= take_profit
    if hit_sl:
        return "SL"
    if hit_tp:
        return "TP"
    return None


def _print_backtest_report(
    trades: list[TradeRecord],
    requested_start: date,
    requested_end: date,
    actual_start: datetime | None,
    actual_end: datetime | None,
    stopped_reason: str,
) -> None:
    """Print the historical summary. These numbers are not a forecast."""
    if CONFIG is None:
        return
    wins = [trade.pnl for trade in trades if trade.pnl > 0]
    losses = [trade.pnl for trade in trades if trade.pnl <= 0]
    gross = sum(trade.pnl for trade in trades)
    average = gross / len(trades) if trades else 0.0
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    print("")
    print("SIMULATED / HISTORICAL")
    print("BACKTEST COMPLETE")
    print("")
    print("Strategy: HalfTrend + optional RSI")
    print(f"Candle Mode: {CONFIG.candle_mode}")
    print(f"Underlying: {CONFIG.underlying}")
    print(f"Requested period: {requested_start.isoformat()} to {requested_end.isoformat()}")
    if actual_start is not None and actual_end is not None:
        print(f"Received period: {actual_start.date().isoformat()} to {actual_end.date().isoformat()}")
    print(f"Period: {requested_start.isoformat()} to {requested_end.isoformat()}")
    print("")
    print(f"Total Trades: {len(trades)}")
    print(f"Winning Trades: {len(wins)}")
    print(f"Losing Trades: {len(losses)}")
    win_rate = (100.0 * len(wins) / len(trades)) if trades else 0.0
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Gross P&L: {format_inr(gross)}")
    print(f"Average Trade: {format_inr(average)}")
    print(f"Largest Win: {format_inr(max(wins) if wins else 0.0)}")
    print(f"Largest Loss: {format_inr(min(losses) if losses else 0.0)}")
    print(f"Max Drawdown: {format_inr(max_drawdown)}")
    print("")
    print("Option P&L uses Dhan rolling-option closes. It is not a promise of future results.")
    print("If one candle could have touched both TP and SL, the stop is assumed to have happened first.")
    print("Quantity uses the current security-master lot size, which can differ from an expired contract.")
    if stopped_reason:
        print(stopped_reason)
    print("No live order was sent.")


def _run_backtest_loop(
    signal_frame: pd.DataFrame,
    quantity: int,
) -> tuple[list[TradeRecord], str]:
    """Walk completed bars once. No bar is allowed to see a later bar."""
    if CONFIG is None:
        raise RuntimeError("Config is not loaded")
    trades: list[TradeRecord] = []
    position: dict[str, Any] | None = None
    day_realized = 0.0
    orders_today = 0
    trading_day: date | None = None
    stop_lock = False
    loss_lock = False
    stopped = ""
    start_clock = _parse_clock(CONFIG.start_time, "start_time")
    stop_clock = _parse_clock(CONFIG.stop_time, "stop_time")
    warmup = max(CONFIG.amplitude, CONFIG.sideways_lookback, CONFIG.rsi_period if CONFIG.rsi_enabled else 1)

    def close_trade(reason: str, exit_premium: float, when: datetime) -> None:
        nonlocal position, day_realized, stopped, stop_lock, loss_lock
        if position is None:
            return
        pnl = (exit_premium - float(position["entry_premium"])) * quantity
        trades.append(
            TradeRecord(
                symbol=f"{CONFIG.underlying} {position['option_type']}",
                option_type=str(position["option_type"]),
                quantity=quantity,
                entry_price=float(position["entry_premium"]),
                exit_price=exit_premium,
                pnl=pnl,
                reason=reason,
                entry_time=position["entry_time"],
                exit_time=when,
            )
        )
        day_realized += pnl
        if reason == "SL" and CONFIG.no_reentry_after_stop_loss:
            stop_lock = True
        position = None
        if reason in {"TP", "SL"} and CONFIG.stop_bot_after_tp_sl:
            stopped = "Backtest stopped after TP/SL because stop_bot_after_tp_sl is true."
        if day_realized <= -abs(CONFIG.max_loss_per_day_inr):
            loss_lock = True
            if CONFIG.no_reentry_after_max_loss and CONFIG.stop_bot_after_tp_sl:
                stopped = "Backtest stopped because combined daily gross P&L reached the loss limit."

    for index in range(len(signal_frame)):
        if stopped:
            break
        if index < warmup:
            continue
        row = signal_frame.iloc[index]
        when = row["timestamp"]
        if not isinstance(when, datetime):
            continue
        bar_day = when.date()
        if trading_day != bar_day:
            trading_day = bar_day
            day_realized = 0.0
            orders_today = 0
            stop_lock = False
            loss_lock = False
        clock = when.timetz().replace(tzinfo=None)
        session_open = CONFIG.run_mode == "CONTINUOUS" or (start_clock <= clock < stop_clock)
        window = signal_frame.iloc[: index + 1]
        _refresh_filters(window)
        option_type = "CE" if position is None else str(position["option_type"])
        prefix = option_type.lower()
        premium_close = row.get(f"{prefix}_close")
        if position is not None:
            if CONFIG.tp_sl_mode == "UNDERLYING_POINTS":
                level_high = float(row["high"])
                level_low = float(row["low"])
            else:
                level_high = row.get(f"{prefix}_high")
                level_low = row.get(f"{prefix}_low")
            if premium_close is None or pd.isna(premium_close) or pd.isna(level_high) or pd.isna(level_low):
                print(PREMIUM_HISTORY_UNAVAILABLE)
                stopped = "Backtest stopped because an open option had no historical premium on this bar."
                position = None
                break
            exit_reason = _same_bar_exit(
                str(position["option_type"]),
                float(level_high),
                float(level_low),
                position["tp"],
                position["sl"],
            )
            if exit_reason:
                close_trade(exit_reason, float(premium_close), when)
                continue
            if CONFIG.exit_on_opposite_signal:
                opposite = False
                if CONFIG.candle_mode == "RENKO":
                    opposite = renko_exit_ready(window, str(position["option_type"]))
                else:
                    direction = generate_normal_signal(window)
                    opposite = (
                        (position["option_type"] == "CE" and direction == "BUY_PE")
                        or (position["option_type"] == "PE" and direction == "BUY_CE")
                    )
                if opposite:
                    close_trade("OPPOSITE_SIGNAL", float(premium_close), when)
                    continue
            if CONFIG.close_all_positions_at_stop and CONFIG.run_mode == "SCHEDULED" and clock >= stop_clock:
                close_trade("SESSION_END", float(premium_close), when)
            continue
        if not session_open or stop_lock or loss_lock or orders_today >= CONFIG.max_orders_per_day:
            continue
        if day_realized <= -abs(CONFIG.max_loss_per_day_inr):
            loss_lock = True
            continue
        if CONFIG.candle_mode == "RENKO":
            direction = generate_renko_signal(window)
        else:
            direction = generate_normal_signal(window)
        if not entry_filters_pass(direction) or direction is None:
            continue
        side = option_side(direction)
        entry_premium = row.get(f"{side.lower()}_close")
        if entry_premium is None or pd.isna(entry_premium):
            continue
        underlying_entry = float(row["close"])
        tp_price, sl_price = calculate_tp_sl(float(entry_premium), underlying_entry, side, 0.05)
        position = {
            "option_type": side,
            "entry_premium": float(entry_premium),
            "entry_time": when,
            "tp": tp_price,
            "sl": sl_price,
        }
        orders_today += 1
    if position is not None and not stopped:
        last = signal_frame.iloc[-1]
        prefix = str(position["option_type"]).lower()
        exit_premium = last.get(f"{prefix}_close")
        if exit_premium is None or pd.isna(exit_premium):
            print(PREMIUM_HISTORY_UNAVAILABLE)
            stopped = "Backtest stopped because the final option premium was missing. That open trade was not priced."
        else:
            close_trade("END_OF_DATA", float(exit_premium), last["timestamp"])
    return trades, stopped


def run_backtest() -> None:
    """Replay historical candles and rolling option premiums. Never place an order.

    Purpose:
        Test the same signal and TP/SL rules on completed history.

    Inputs:
        test.yaml backtest dates, candle mode, and option selection, loaded
        through backtest.py with for_backtest=True.

    Output:
        A printed SIMULATED / HISTORICAL summary, then the process exits.

    Trading use:
        PREMIUM selection and a calendar CONFIGURED expiry cannot be rebuilt
        from Dhan's rolling option API, so those runs stop without a P&L.
        Missing premium history is never replaced with an invented price.
    """
    if CONFIG is None or BROKER is None:
        raise RuntimeError("Backtest is missing config or Dhan")
    if CONFIG.trading_mode != "BACKTEST":
        raise RuntimeError("run_backtest was called while trading_mode is not BACKTEST")
    requested_start = _parse_iso_date(CONFIG.backtest_start_date, "backtest.start_date")
    requested_end = _parse_iso_date(CONFIG.backtest_end_date, "backtest.end_date")
    print("SIMULATED / HISTORICAL")
    print(f"BACKTEST {CONFIG.underlying} {CONFIG.candle_mode}")
    print("No order will be sent.")
    if CONFIG.selection_mode == "PREMIUM" or CONFIG.expiry_mode == "CONFIGURED":
        print(PREMIUM_HISTORY_UNAVAILABLE)
        print("PREMIUM selection and a calendar expiry are not available from the rolling option history.")
        raise SystemExit(0)
    master = load_security_master()
    resolved = resolve_underlying(master, CONFIG.underlying)
    STATE.underlying_security_id = resolved["security_id"]
    STATE.underlying_symbol = resolved["trading_symbol"]
    STATE.trading_date = requested_start
    quantity = CONFIG.lots * _current_option_lot(master)
    interval = 1 if CONFIG.candle_mode == "RENKO" else CONFIG.timeframe_minutes
    underlying = _fetch_underlying_history(requested_start, requested_end, interval)
    if underlying.empty:
        raise RuntimeError("No underlying candles were returned for the backtest range. No order was sent.")
    actual_start = underlying.iloc[0]["timestamp"]
    actual_end = underlying.iloc[-1]["timestamp"]
    if actual_start.date() != requested_start or actual_end.date() != requested_end:
        print(
            "Dhan returned a shorter underlying range than requested: "
            f"{actual_start.date().isoformat()} to {actual_end.date().isoformat()}."
        )
        print("The installed history API documents recent minute data. Missing days were not invented.")
    signal_source = build_renko(underlying, CONFIG.renko_brick_size_points) if CONFIG.candle_mode == "RENKO" else underlying
    if signal_source.empty:
        raise RuntimeError("The history did not produce a completed signal bar. No order was sent.")
    signal_frame = calculate_half_trend(signal_source, CONFIG.amplitude, CONFIG.channel_deviation)
    signal_frame["rsi"] = calculate_rsi(signal_frame["close"], CONFIG.rsi_period)
    call_strike = _rolling_strike("CE")
    put_strike = _rolling_strike("PE")
    calls = _fetch_rolling_option_history(call_strike, "CALL", requested_start, requested_end, interval if CONFIG.candle_mode == "NORMAL" else 1)
    puts = _fetch_rolling_option_history(put_strike, "PUT", requested_start, requested_end, interval if CONFIG.candle_mode == "NORMAL" else 1)
    if calls.empty or puts.empty:
        print(PREMIUM_HISTORY_UNAVAILABLE)
        print("The rolling option request returned no premium candles for this strike and date range.")
        raise SystemExit(0)
    signal_frame = _align_premium(signal_frame, calls, "ce")
    signal_frame = _align_premium(signal_frame, puts, "pe")
    trades, stopped = _run_backtest_loop(signal_frame, quantity)
    _print_backtest_report(trades, requested_start, requested_end, actual_start, actual_end, stopped)
    del underlying, signal_source, signal_frame, calls, puts


def main() -> None:
    """Load configuration, connect to Dhan, and poll in PAPER or LIVE mode.

    Purpose:
        Start the live/paper bot.

    Inputs:
        config.yaml and .env beside this file.

    Output:
        Runs until a shutdown reason exits the process.

    Trading use:
        PAPER never reaches the live order call. LIVE can place real orders
        after startup validation and an on-screen preview. Historical replay
        is started with backtest.py and test.yaml, not this entry point.
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


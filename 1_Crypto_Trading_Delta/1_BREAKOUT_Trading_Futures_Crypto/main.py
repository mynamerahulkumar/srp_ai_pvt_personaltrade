#!/usr/bin/env python3
"""Delta Exchange India perpetual-futures breakout trading bot.

Single-file bot. config.yaml is the only trading configuration.
PAPER mode never sends live orders. LIVE mode can lose real money.

This module does not import anything from docs/.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
import yaml
from dotenv import load_dotenv
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# 2. constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
STOP_FILE = BASE_DIR / ".bot_stop_signal"
ENV_PATH = BASE_DIR / ".env"

INDIA_PROD_REST = "https://api.india.delta.exchange/v2"
USER_AGENT = "DeltaBreakoutBot/1.0"

ALLOWED_SYMBOLS = {"BTCUSD", "ETHUSD", "SOLUSD", "XAUSD", "XAUTUSD"}
SYMBOL_ALIASES = {"XAUSD": "XAUTUSD"}
ALLOWED_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}
RESOLUTION_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
PERPETUAL_TYPES = {"perpetual_futures", "perpetual_futures_v2"}
IST_AGGREGATED_TIMEFRAMES = {"1h", "4h", "1d"}
HIGH_LEVERAGE_WARN = 20
MAX_CONSUMED_SIGNALS = 100
LIMIT_FILL_WAIT_SECONDS = 15
MARKET_FILL_WAIT_SECONDS = 20
GET_RETRY_ATTEMPTS = 3
CANDLE_BUFFER = 6

console = Console()
log = logging.getLogger("breakout")


# ---------------------------------------------------------------------------
# exceptions / helpers
# ---------------------------------------------------------------------------


class DeltaAPIError(Exception):
    """Structured Delta REST error. Never includes credentials."""

    def __init__(self, code: str, message: str, status: int | None = None):
        self.code = code or "UnknownError"
        self.message = message or "Delta API request failed"
        self.status = status
        super().__init__(self.__str__())

    def __str__(self) -> str:
        parts = [f"{self.code}: {self.message}"]
        if self.status is not None:
            parts.append(f"status={self.status}")
        return " | ".join(parts)


class ConfigError(Exception):
    """Invalid config.yaml value. Trading must not start."""


class HaltBot(Exception):
    """Request a clean process exit after the current action finishes."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _money(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):.{digits}f}"


def _px(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def _body_string(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return ""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _query_string(params: dict[str, Any] | None) -> str:
    if not params:
        return ""
    clean: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        clean.append((str(key), str(value)))
    if not clean:
        return ""
    return "?" + urlencode(clean)


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None}


def _bool_str(value: bool | None) -> str | None:
    if value is None:
        return None
    return "true" if value else "false"


def round_to_tick(price: float, tick_size: Decimal) -> float:
    """Snap a price to the product tick size using half-up rounding."""
    if tick_size <= 0:
        return price
    value = Decimal(str(price))
    steps = (value / tick_size).to_integral_value(rounding=ROUND_HALF_UP)
    snapped = (steps * tick_size).normalize()
    return float(snapped)


# ---------------------------------------------------------------------------
# 3. configuration
# ---------------------------------------------------------------------------


@dataclass
class ConfirmationConfig:
    enabled: bool
    mode: str
    value: float
    candle_close_confirmation: bool


@dataclass
class ExitConfig:
    enabled: bool
    mode: str
    value: float


@dataclass
class StrategyConfig:
    enabled: bool
    breakout_lookback_candles: int
    direction: str


@dataclass
class Config:
    symbol: str
    mode: str
    leverage: int
    order_size: int
    order_type: str
    polling_seconds: int
    timeframe: str
    run_mode: str
    timezone: str
    start_time: str
    stop_time: str
    close_positions_at_stop_time: bool
    stop_bot_after_close: bool
    strategy: StrategyConfig
    confirmation: ConfirmationConfig
    take_profit: ExitConfig
    stop_loss: ExitConfig
    stop_after_tp: bool
    stop_after_sl: bool
    day_trading_enabled: bool
    max_open_strategies: int
    max_orders_per_day: int
    max_loss_per_day_dollar: float
    allow_reentry_after_exit: bool
    tz: ZoneInfo = field(init=False)

    def __post_init__(self) -> None:
        self.tz = ZoneInfo(self.timezone)


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load trading settings from config.yaml only.

    Inputs: path to YAML file.
    Returns: validated Config.
    Called once at process start. Strategy settings never come from .env.
    """
    if not path.exists():
        raise ConfigError(f"Missing {path.name}. Create it before starting the bot.")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml must be a mapping of keys to values.")

    strategy_raw = raw.get("strategy") or {}
    confirmation_raw = raw.get("confirmation") or {}
    tp_raw = raw.get("take_profit") or {}
    sl_raw = raw.get("stop_loss") or {}

    cfg = Config(
        symbol=str(raw.get("symbol", "BTCUSD")).strip().upper(),
        mode=str(raw.get("mode", "PAPER")).strip().upper(),
        leverage=int(raw.get("leverage", 100)),
        order_size=int(raw.get("order_size", 1)),
        order_type=str(raw.get("order_type", "market_order")).strip(),
        polling_seconds=int(raw.get("polling_seconds", 5)),
        timeframe=str(raw.get("timeframe", "5m")).strip(),
        run_mode=str(raw.get("run_mode", "CONTINUOUS")).strip().upper(),
        timezone=str(raw.get("timezone", "Asia/Kolkata")).strip(),
        start_time=str(raw.get("start_time", "09:30")).strip(),
        stop_time=str(raw.get("stop_time", "23:00")).strip(),
        close_positions_at_stop_time=bool(raw.get("close_positions_at_stop_time", True)),
        stop_bot_after_close=bool(raw.get("stop_bot_after_close", True)),
        strategy=StrategyConfig(
            enabled=bool(strategy_raw.get("enabled", True)),
            breakout_lookback_candles=int(strategy_raw.get("breakout_lookback_candles", 20)),
            direction=str(strategy_raw.get("direction", "BOTH")).strip().upper(),
        ),
        confirmation=ConfirmationConfig(
            enabled=bool(confirmation_raw.get("enabled", True)),
            mode=str(confirmation_raw.get("mode", "POINTS")).strip().upper(),
            value=float(confirmation_raw.get("value", 50)),
            candle_close_confirmation=bool(confirmation_raw.get("candle_close_confirmation", True)),
        ),
        take_profit=ExitConfig(
            enabled=bool(tp_raw.get("enabled", True)),
            mode=str(tp_raw.get("mode", "PNL")).strip().upper(),
            value=float(tp_raw.get("value", 5)),
        ),
        stop_loss=ExitConfig(
            enabled=bool(sl_raw.get("enabled", True)),
            mode=str(sl_raw.get("mode", "PNL")).strip().upper(),
            value=float(sl_raw.get("value", 5)),
        ),
        stop_after_tp=bool(raw.get("stop_after_tp", True)),
        stop_after_sl=bool(raw.get("stop_after_sl", True)),
        day_trading_enabled=bool(raw.get("day_trading_enabled", True)),
        max_open_strategies=int(raw.get("max_open_strategies", 1)),
        max_orders_per_day=int(raw.get("max_orders_per_day", 10)),
        max_loss_per_day_dollar=float(raw.get("max_loss_per_day_dollar", 5)),
        allow_reentry_after_exit=bool(raw.get("allow_reentry_after_exit", False)),
    )
    validate_config(cfg)
    return cfg


def _parse_hhmm(value: str, field_name: str) -> tuple[int, int]:
    try:
        hour_s, minute_s = value.split(":")
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except (TypeError, ValueError):
        raise ConfigError(f"{field_name} must be HH:MM, got {value!r}") from None


def validate_config(cfg: Config) -> None:
    """Reject config values that would make trading undefined.

    Called immediately after load_config(). A failure must stop the process
    before any order is considered.
    """
    if cfg.symbol not in ALLOWED_SYMBOLS:
        raise ConfigError(f"symbol must be one of {sorted(ALLOWED_SYMBOLS)}")
    if cfg.mode not in {"PAPER", "LIVE"}:
        raise ConfigError("mode must be PAPER or LIVE")
    if cfg.leverage < 1:
        raise ConfigError("leverage must be a positive integer")
    if cfg.order_size < 1:
        raise ConfigError("order_size must be a positive integer")
    if cfg.order_type not in {"market_order", "limit_order"}:
        raise ConfigError("order_type must be market_order or limit_order")
    if cfg.polling_seconds < 1:
        raise ConfigError("polling_seconds must be >= 1")
    if cfg.timeframe not in ALLOWED_TIMEFRAMES:
        raise ConfigError(f"timeframe must be one of {sorted(ALLOWED_TIMEFRAMES)}")
    if cfg.run_mode not in {"CONTINUOUS", "SCHEDULED"}:
        raise ConfigError("run_mode must be CONTINUOUS or SCHEDULED")
    try:
        ZoneInfo(cfg.timezone)
    except Exception as exc:
        raise ConfigError(f"unknown timezone {cfg.timezone!r}") from exc
    _parse_hhmm(cfg.start_time, "start_time")
    _parse_hhmm(cfg.stop_time, "stop_time")
    if cfg.strategy.breakout_lookback_candles < 1:
        raise ConfigError("breakout_lookback_candles must be >= 1")
    if cfg.strategy.direction not in {"LONG", "SHORT", "BOTH"}:
        raise ConfigError("strategy.direction must be LONG, SHORT or BOTH")
    if cfg.confirmation.mode not in {"POINTS", "PERCENT"}:
        raise ConfigError("confirmation.mode must be POINTS or PERCENT")
    if cfg.confirmation.value < 0:
        raise ConfigError("confirmation.value must be >= 0")
    if cfg.take_profit.mode not in {"PNL", "PERCENT"}:
        raise ConfigError("take_profit.mode must be PNL or PERCENT")
    if cfg.stop_loss.mode not in {"PNL", "PERCENT"}:
        raise ConfigError("stop_loss.mode must be PNL or PERCENT")
    if cfg.take_profit.value <= 0:
        raise ConfigError("take_profit.value must be > 0")
    if cfg.stop_loss.value <= 0:
        raise ConfigError("stop_loss.value must be > 0")
    if cfg.max_open_strategies < 1:
        raise ConfigError("max_open_strategies must be >= 1")
    if cfg.max_orders_per_day < 1:
        raise ConfigError("max_orders_per_day must be >= 1")
    if cfg.max_loss_per_day_dollar <= 0:
        raise ConfigError("max_loss_per_day_dollar must be > 0")


# ---------------------------------------------------------------------------
# 4. environment
# ---------------------------------------------------------------------------


@dataclass
class Environment:
    api_key: str
    api_secret: str

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)


def _credential(value: str) -> str:
    """Drop CR, spaces, and wrapping quotes so a copied .env does not change the HMAC."""
    text = value.replace("\r", "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].replace("\r", "").strip()
    return text


def _clock_skew_seconds(date_header: str | None) -> float | None:
    if not date_header:
        return None
    try:
        server = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError):
        return None
    if server.tzinfo is None:
        server = server.replace(tzinfo=timezone.utc)
    return abs(time.time() - server.timestamp())


def load_environment() -> Environment:
    """Load API credentials from .env. Trading settings stay in config.yaml.

    LIVE mode requires both key and secret. PAPER can fetch public candles
    without credentials.
    """
    load_dotenv(ENV_PATH)
    env = Environment(
        api_key=_credential(os.getenv("DELTA_API_KEY") or ""),
        api_secret=_credential(os.getenv("DELTA_API_SECRET") or ""),
    )
    if bool(env.api_key) != bool(env.api_secret):
        raise ConfigError("Set both DELTA_API_KEY and DELTA_API_SECRET, or neither.")
    return env


# ---------------------------------------------------------------------------
# 5. logging
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    """Configure concise event logging. Secrets are never logged."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def log_event(runtime: "Runtime", message: str, level: int = logging.INFO) -> None:
    """Record a state-change event once. Dashboard polls must not spam this."""
    runtime.events.append(message)
    runtime.last_message = message
    log.log(level, message)


# ---------------------------------------------------------------------------
# 6. data models
# ---------------------------------------------------------------------------


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class ProductMeta:
    product_id: int
    symbol: str
    contract_type: str
    tick_size: Decimal
    contract_value: float
    min_size: int
    state: str
    trading_status: str


@dataclass
class BreakoutSignal:
    direction: str
    range_high: float
    range_low: float
    breakout_level: float
    confirmation_price: float
    candle_timestamp: int
    reason: str
    signal_id: str


@dataclass
class StrategyPosition:
    strategy_id: str
    symbol: str
    direction: str
    quantity: int
    entry_price: float
    entry_time: datetime
    order_id: str | None
    client_order_id: str | None
    breakout_level: float | None
    range_high: float | None
    range_low: float | None
    breakout_candle_timestamp: int | None
    paper: bool
    realized_pnl: float = 0.0
    tp_price: float | None = None
    sl_price: float | None = None
    bracket_placed: bool = False


@dataclass
class Runtime:
    status: str = "STARTING"
    product: ProductMeta | None = None
    position: StrategyPosition | None = None
    last_signal: BreakoutSignal | None = None
    last_processed_candle_ts: int | None = None
    consumed_signal_ids: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_CONSUMED_SIGNALS))
    pending_client_order_id: str | None = None
    exit_in_progress: bool = False
    daily_realized_pnl: float = 0.0
    daily_orders: int = 0
    trading_day: date | None = None
    range_high: float | None = None
    range_low: float | None = None
    long_level: float | None = None
    short_level: float | None = None
    current_price: float | None = None
    mark_price: float | None = None
    exchange_unrealized: float | None = None
    last_error: str | None = None
    api_ok: bool = True
    market_stale: bool = False
    last_message: str = ""
    live_warning_shown: bool = False
    shutdown_reason: str | None = None
    events: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=ZoneInfo("UTC")))
    stop_requested: bool = False
    last_trade_summary: str = "NONE"
    next_poll_seconds: int = 5
    entries_blocked_reason: str | None = None
    last_market_fetch_ts: float = 0.0
    scheduled_close_done: bool = False
    interrupt: bool = False
    paper_live_position_warned: bool = False
    last_announced_signal_id: str | None = None


@dataclass
class App:
    cfg: Config
    env: Environment
    client: "DeltaClient"
    runtime: Runtime


# ---------------------------------------------------------------------------
# 7. Delta API client
# ---------------------------------------------------------------------------


def sign_request(secret: str, method: str, timestamp: str, path: str, query: str, body: str) -> str:
    """HMAC-SHA256 signature used by Delta India REST.

    Inputs: API secret, HTTP method, unix timestamp string, /v2-prefixed path,
    query string beginning with ?, compact JSON body.
    Returns: hex digest. Never log the secret or the signature.
    """
    payload = f"{method}{timestamp}{path}{query}{body}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


class DeltaClient:
    """Minimal India-production REST client for perpetual futures only."""

    def __init__(self, api_key: str, api_secret: str, allow_mutations: bool):
        self.api_key = api_key
        self.api_secret = api_secret
        self.allow_mutations = allow_mutations
        self.base_url = INDIA_PROD_REST
        self.session = requests.Session()
        self.timeout = (10.0, 30.0)

    def close(self) -> None:
        self.session.close()

    def send_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        auth: bool = False,
        allow_retry: bool = True,
    ) -> Any:
        """Send one REST call. GET may retry; mutations never retry blindly.

        Safety: 429/5xx GET retries use exponential backoff. POST/PUT/DELETE
        are attempted once. Callers must query order status before a resubmit.
        """
        method = method.upper().strip()
        if not path.startswith("/"):
            path = "/" + path
        if path.startswith("/v2/"):
            path = path[3:]
        url = self.base_url + path
        body = _body_string(payload) if payload is not None else ""
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if auth:
            if not (self.api_key and self.api_secret):
                raise DeltaAPIError("AuthError", "API credentials missing")
            ts = str(int(time.time()))
            query = _query_string(params)
            signature = sign_request(self.api_secret, method, ts, "/v2" + path, query, body)
            headers.update(
                {
                    "api-key": self.api_key,
                    "timestamp": ts,
                    "signature": signature,
                }
            )

        attempts = GET_RETRY_ATTEMPTS if (method == "GET" and allow_retry) else 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=body if payload is not None else None,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = DeltaAPIError("NetworkError", str(exc))
                if attempt >= attempts:
                    raise last_error
                time.sleep(0.5 * (2 ** (attempt - 1)))
                continue

            parsed: Any
            try:
                parsed = response.json() if response.content else {}
            except ValueError:
                parsed = {"raw": response.text[:500]}

            if response.status_code == 429 and method == "GET" and attempt < attempts:
                retry_after = _to_float(response.headers.get("Retry-After"), 1.0) or 1.0
                time.sleep(min(max(retry_after, 0.5), 8.0))
                continue
            if 500 <= response.status_code <= 599 and method == "GET" and attempt < attempts:
                time.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            if response.status_code >= 400:
                exc = self._extract_error(response.status_code, parsed)
                if response.status_code == 401:
                    exc = self._explain_401(exc, response.headers.get("Date"))
                raise exc
            if isinstance(parsed, dict) and parsed.get("success") is False:
                raise self._extract_error(response.status_code, parsed)
            return parsed

        raise last_error or DeltaAPIError("HTTPError", "request failed")

    @staticmethod
    def _extract_error(status: int, body: Any) -> DeltaAPIError:
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                return DeltaAPIError(
                    code=str(err.get("code") or err.get("error_code") or "DeltaError"),
                    message=str(err.get("message") or err.get("error") or "Delta API error"),
                    status=status,
                )
            return DeltaAPIError(
                code=str(body.get("code") or "DeltaError"),
                message=str(body.get("message") or body.get("error") or f"HTTP {status}"),
                status=status,
            )
        return DeltaAPIError("HTTPError", f"HTTP {status}", status=status)

    @staticmethod
    def _explain_401(exc: DeltaAPIError, date_header: str | None) -> DeltaAPIError:
        """Say whether a 401 is a bad clock or a secret that does not match the India key."""
        skew = _clock_skew_seconds(date_header)
        if skew is not None and skew > 5:
            message = (
                "This machine's clock differs from Delta by more than 5 seconds. "
                "On Amazon Linux run: sudo timedatectl set-ntp true"
            )
        else:
            message = (
                "Delta India found this API key, but the secret on this machine does not match it. "
                "Put a Delta India key pair in .env with no quotes and no spaces. "
                "A global delta.exchange key will not sign for api.india.delta.exchange."
            )
        return DeltaAPIError(exc.code, message, status=401)

    def _mutate(self, method: str, path: str, payload: dict[str, Any]) -> Any:
        if not self.allow_mutations:
            raise PermissionError("PAPER mode blocked a live mutation. No order was sent.")
        return self.send_request(method, path, payload=_clean_payload(payload), auth=True, allow_retry=False)

    def get_product(self, symbol: str) -> dict[str, Any]:
        return self.send_request("GET", f"/products/{symbol}")

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        return self.send_request("GET", f"/tickers/{symbol}")

    def get_candles(self, symbol: str, resolution: str, start: int, end: int) -> dict[str, Any]:
        return self.send_request(
            "GET",
            "/history/candles",
            params={"symbol": symbol, "resolution": resolution, "start": start, "end": end},
        )

    def get_positions(self, product_id: int) -> dict[str, Any]:
        return self.send_request(
            "GET",
            "/positions/margined",
            params={"product_ids": str(product_id)},
            auth=True,
        )

    def get_open_orders(self, product_id: int) -> dict[str, Any]:
        return self.send_request(
            "GET",
            "/orders",
            params={"product_ids": str(product_id), "states": "open,pending"},
            auth=True,
        )

    def get_order_by_id(self, order_id: int | None = None, client_order_id: str | None = None) -> dict[str, Any]:
        if order_id is not None:
            return self.send_request("GET", f"/orders/{order_id}", auth=True)
        if client_order_id:
            return self.send_request(
                "GET",
                "/orders/client_order_id",
                params={"client_order_id": client_order_id},
                auth=True,
            )
        raise ValueError("Pass order_id or client_order_id")

    def get_product_leverage(self, product_id: int) -> dict[str, Any]:
        return self.send_request("GET", f"/products/{product_id}/orders/leverage", auth=True)

    def set_product_leverage(self, product_id: int, leverage: str) -> dict[str, Any]:
        return self._mutate("POST", f"/products/{product_id}/orders/leverage", {"leverage": leverage})

    def place_bracket_orders(
        self,
        *,
        product_id: int,
        product_symbol: str,
        stop_loss_order: dict[str, Any] | None = None,
        take_profit_order: dict[str, Any] | None = None,
        bracket_stop_trigger_method: str = "mark_price",
    ) -> dict[str, Any]:
        if stop_loss_order is None and take_profit_order is None:
            raise ValueError("At least one of stop_loss_order or take_profit_order is required")
        payload = {
            "product_id": product_id,
            "stop_loss_order": stop_loss_order,
            "take_profit_order": take_profit_order,
            "bracket_stop_trigger_method": bracket_stop_trigger_method,
        }
        return self._mutate("POST", "/orders/bracket", payload)

    def place_order(
        self,
        *,
        size: int,
        side: str,
        order_type: str,
        product_symbol: str,
        reduce_only: bool = False,
        client_order_id: str | None = None,
        limit_price: str | None = None,
        stop_order_type: str | None = None,
        stop_price: str | None = None,
        stop_trigger_method: str | None = None,
    ) -> dict[str, Any]:
        if order_type == "market_order" and limit_price is not None:
            raise ValueError("market_order must not include limit_price")
        if order_type == "limit_order" and not limit_price:
            raise ValueError("limit_price is required for limit_order")
        payload = {
            "size": int(size),
            "side": side,
            "order_type": order_type,
            "product_symbol": product_symbol,
            "reduce_only": _bool_str(reduce_only),
            "client_order_id": client_order_id,
            "limit_price": limit_price,
            "stop_order_type": stop_order_type,
            "stop_price": stop_price,
            "stop_trigger_method": stop_trigger_method,
        }
        return self._mutate("POST", "/orders", payload)

    def cancel_order(self, *, product_id: int, order_id: int | None = None, client_order_id: str | None = None) -> dict[str, Any]:
        payload = {
            "product_id": product_id,
            "id": order_id,
            "client_order_id": client_order_id,
        }
        return self._mutate("DELETE", "/orders", payload)


def create_delta_client(cfg: Config, env: Environment) -> DeltaClient:
    """Build the REST client. LIVE enables mutations; PAPER cannot place orders."""
    if cfg.mode == "LIVE" and not env.has_credentials:
        raise ConfigError("LIVE mode requires DELTA_API_KEY and DELTA_API_SECRET in .env")
    return DeltaClient(
        api_key=env.api_key,
        api_secret=env.api_secret,
        allow_mutations=(cfg.mode == "LIVE"),
    )


# ---------------------------------------------------------------------------
# 8. product metadata
# ---------------------------------------------------------------------------


def resolved_symbol(cfg: Config) -> str:
    return SYMBOL_ALIASES.get(cfg.symbol, cfg.symbol)


def get_product_metadata(app: App) -> ProductMeta:
    """Fetch live product metadata. Exchange data is the source of truth.

    Safety: do not hardcode product IDs. Reject non-perpetual contracts.
    """
    symbol = resolved_symbol(app.cfg)
    raw = _unwrap(app.client.get_product(symbol))
    if not isinstance(raw, dict):
        raise ConfigError(f"Unexpected product payload for {symbol}")
    contract_type = str(raw.get("contract_type") or "")
    if contract_type not in PERPETUAL_TYPES:
        raise ConfigError(f"{symbol} is {contract_type or 'unknown'}, not a perpetual future")
    tick_raw = raw.get("tick_size")
    try:
        tick = Decimal(str(tick_raw))
        if tick <= 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConfigError(f"{symbol} has invalid tick_size {tick_raw!r}") from exc
    contract_value = _to_float(raw.get("contract_value"))
    if contract_value is None or contract_value <= 0:
        raise ConfigError(f"{symbol} has invalid contract_value")
    min_size = _to_int(raw.get("min_size") or raw.get("minimum_order_size"), 1) or 1
    product_id = _to_int(raw.get("id") or raw.get("product_id"))
    if product_id is None:
        raise ConfigError(f"{symbol} product_id missing from exchange metadata")
    state = str(raw.get("state") or raw.get("trading_status") or "")
    return ProductMeta(
        product_id=product_id,
        symbol=str(raw.get("symbol") or symbol),
        contract_type=contract_type,
        tick_size=tick,
        contract_value=contract_value,
        min_size=min_size,
        state=state,
        trading_status=str(raw.get("trading_status") or state),
    )


def validate_product(app: App, product: ProductMeta) -> None:
    """Refuse to trade a halted, expired, or undersized product."""
    status = f"{product.state} {product.trading_status}".lower()
    if any(flag in status for flag in ("expired", "halt", "delist", "inactive", "unlisted")):
        raise ConfigError(f"Product {product.symbol} is not tradable ({product.state})")
    if app.cfg.order_size < product.min_size:
        raise ConfigError(
            f"order_size {app.cfg.order_size} is below exchange min_size {product.min_size}"
        )


def validate_exchange_connection(app: App) -> None:
    """Confirm India production REST is reachable via a public product lookup."""
    get_product_metadata(app)


def apply_leverage(app: App) -> None:
    """Set configured leverage on LIVE. Never silently substitute another value.

    PAPER skips the mutation. A rejected LIVE leverage request stops the bot.
    """
    if app.cfg.mode != "LIVE":
        return
    if not app.env.has_credentials:
        raise ConfigError("LIVE leverage requires API credentials")
    assert app.runtime.product is not None
    product_id = app.runtime.product.product_id
    try:
        current = _unwrap(app.client.get_product_leverage(product_id))
        if isinstance(current, dict):
            log.debug("current leverage payload keys=%s", sorted(current.keys()))
        app.client.set_product_leverage(product_id, str(app.cfg.leverage))
    except (DeltaAPIError, PermissionError) as exc:
        raise ConfigError(f"LEVERAGE CONFIGURATION REJECTED: {exc}") from exc
    log_event(app.runtime, f"LEVERAGE SET TO {app.cfg.leverage}X")


# ---------------------------------------------------------------------------
# 9. candle retrieval / market data
# ---------------------------------------------------------------------------


def get_current_price(app: App) -> tuple[float, float]:
    """Return (last, mark) from the ticker. Either may equal the other."""
    symbol = app.runtime.product.symbol if app.runtime.product else resolved_symbol(app.cfg)
    raw = _unwrap(app.client.get_ticker(symbol))
    if not isinstance(raw, dict):
        raise DeltaAPIError("TickerError", f"Malformed ticker for {symbol}")
    last = _to_float(raw.get("close") or raw.get("last_price") or raw.get("mark_price") or raw.get("spot_price"))
    mark = _to_float(raw.get("mark_price") or last)
    if last is None:
        raise DeltaAPIError("TickerError", f"Ticker for {symbol} has no price")
    app.runtime.last_market_fetch_ts = time.time()
    return last, (mark if mark is not None else last)


def candle_window_start(ts: float, timeframe: str, tz: ZoneInfo) -> int:
    """Floor a unix time to the candle open on the configured local clock.

    5m at 21:03 IST opens at 21:00. 1h opens on the hour. 4h opens at
    0, 4, 8, 12, 16, 20. 1d opens at local midnight.
    """
    local = datetime.fromtimestamp(ts, tz=tz)
    if timeframe == "1d":
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif timeframe == "4h":
        start = local.replace(hour=(local.hour // 4) * 4, minute=0, second=0, microsecond=0)
    elif timeframe == "1h":
        start = local.replace(minute=0, second=0, microsecond=0)
    else:
        period = RESOLUTION_SECONDS[timeframe] // 60
        minutes = ((local.hour * 60 + local.minute) // period) * period
        start = local.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    return int(start.timestamp())


def aggregate_candles(candles: list[Candle], timeframe: str, tz: ZoneInfo) -> list[Candle]:
    """Fold smaller bars into local-time windows. Open is the first tick, close the last."""
    buckets: dict[int, Candle] = {}
    order: list[int] = []
    for candle in candles:
        start = candle_window_start(candle.time, timeframe, tz)
        existing = buckets.get(start)
        if existing is None:
            buckets[start] = Candle(
                time=start,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
            order.append(start)
            continue
        existing.high = max(existing.high, candle.high)
        existing.low = min(existing.low, candle.low)
        existing.close = candle.close
        existing.volume += candle.volume
    return [buckets[key] for key in order]


def get_candles(app: App) -> list[Candle]:
    """Fetch the configured timeframe, or 5m bars when the timeframe must be built in IST.

    1h, 4h, and 1d are aggregated so they open on the Indian clock, not UTC.
    """
    symbol = app.runtime.product.symbol if app.runtime.product else resolved_symbol(app.cfg)
    lookback = app.cfg.strategy.breakout_lookback_candles
    res = app.cfg.timeframe
    aggregate = res in IST_AGGREGATED_TIMEFRAMES
    fetch_res = "5m" if aggregate else res
    seconds = RESOLUTION_SECONDS[res]
    end = int(time.time())
    start = end - seconds * (lookback + CANDLE_BUFFER + 2)
    raw = _unwrap(app.client.get_candles(symbol, fetch_res, start, end))
    rows = raw if isinstance(raw, list) else []
    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = _to_int(row.get("time") or row.get("timestamp"))
        o = _to_float(row.get("open"))
        h = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        c = _to_float(row.get("close"))
        if ts is None or o is None or h is None or low is None or c is None:
            continue
        if ts > 10_000_000_000:
            ts //= 1000
        candles.append(
            Candle(time=ts, open=o, high=h, low=low, close=c, volume=_to_float(row.get("volume"), 0.0) or 0.0)
        )
    candles.sort(key=lambda item: item.time)
    if aggregate:
        candles = aggregate_candles(candles, res, app.cfg.tz)
    app.runtime.last_market_fetch_ts = time.time()
    return candles


def split_completed_candles(
    candles: list[Candle],
    timeframe: str,
    tz: ZoneInfo,
    now: float | None = None,
) -> tuple[list[Candle], Candle | None]:
    """Separate completed candles from the bar that contains local now."""
    now = now if now is not None else time.time()
    if not candles:
        return [], None
    current_open = candle_window_start(now, timeframe, tz)
    completed = [candle for candle in candles if candle.time < current_open]
    forming = [candle for candle in candles if candle.time >= current_open]
    return completed, (forming[-1] if forming else None)


def market_data_is_stale(app: App, completed: list[Candle]) -> bool:
    """Block entries when candles or ticker age beyond a safe window."""
    seconds = RESOLUTION_SECONDS[app.cfg.timeframe]
    if not completed:
        return True
    age = time.time() - completed[-1].time
    if age > seconds * 2.5:
        return True
    ticker_age = time.time() - app.runtime.last_market_fetch_ts
    if ticker_age > max(30.0, app.cfg.polling_seconds * 4):
        return True
    return False


# ---------------------------------------------------------------------------
# 10. breakout calculations
# ---------------------------------------------------------------------------


def calculate_breakout_range(candles: list[Candle], lookback: int) -> tuple[float, float]:
    """Calculate the previous completed-candle high/low range.

    Inputs:
        candles: completed OHLC candles that do NOT include the breakout bar.
        lookback: number of candles used for the range.
    Returns:
        range_high, range_low.
    Safety:
        The current incomplete candle must not be included in the
        breakout reference range. The candidate breakout candle is also
        excluded so it cannot contaminate the level it is tested against.
    """
    window = candles[-lookback:]
    if len(window) < lookback:
        raise ValueError("not enough completed candles for breakout range")
    return max(c.high for c in window), min(c.low for c in window)


def calculate_breakout_levels(
    range_high: float,
    range_low: float,
    confirmation: ConfirmationConfig,
    tick_size: Decimal,
) -> tuple[float, float]:
    """Convert the raw range into long/short confirmation prices.

    POINTS adds/subtracts `value`. PERCENT scales the range edges.
    Levels are snapped to the product tick size.
    """
    if not confirmation.enabled or confirmation.value == 0:
        long_level = range_high
        short_level = range_low
    elif confirmation.mode == "PERCENT":
        long_level = range_high * (1 + confirmation.value / 100.0)
        short_level = range_low * (1 - confirmation.value / 100.0)
    else:
        long_level = range_high + confirmation.value
        short_level = range_low - confirmation.value
    return round_to_tick(long_level, tick_size), round_to_tick(short_level, tick_size)


def is_new_candle(app: App, candle_ts: int) -> bool:
    """True when this completed candle has not been evaluated yet.

    Prevents the 5-second poll from treating the same close as a new signal.
    """
    return app.runtime.last_processed_candle_ts != candle_ts


def _signal_id(symbol: str, timeframe: str, direction: str, range_high: float, range_low: float, candle_ts: int) -> str:
    return f"{symbol}|{timeframe}|{direction}|{range_high:.8f}|{range_low:.8f}|{candle_ts}"


def detect_breakout_signal(
    app: App,
    completed: list[Candle],
    live_price: float | None,
) -> BreakoutSignal | None:
    """Detect a LONG/SHORT breakout against the prior lookback range.

    Close confirmation tests the latest completed candle against the bars
    before it. Live confirmation tests the current price against the last
    completed candle, which is the range shown on the dashboard.
    """
    lookback = app.cfg.strategy.breakout_lookback_candles
    close_confirm = app.cfg.confirmation.candle_close_confirmation
    needed = lookback + 1 if close_confirm else lookback
    if len(completed) < needed:
        app.runtime.entries_blocked_reason = "WAITING FOR CANDLES"
        return None
    reference = completed[-1]
    source = completed[:-1] if close_confirm else completed
    try:
        range_high, range_low = calculate_breakout_range(source, lookback)
    except ValueError:
        return None
    assert app.runtime.product is not None
    long_level, short_level = calculate_breakout_levels(
        range_high, range_low, app.cfg.confirmation, app.runtime.product.tick_size
    )
    app.runtime.range_high = range_high
    app.runtime.range_low = range_low
    app.runtime.long_level = long_level
    app.runtime.short_level = short_level

    if close_confirm:
        test_price = reference.close
        if not is_new_candle(app, reference.time):
            return None
    else:
        if live_price is None:
            return None
        test_price = live_price

    allowed = app.cfg.strategy.direction
    symbol = app.runtime.product.symbol
    tf = app.cfg.timeframe

    if allowed in {"LONG", "BOTH"} and test_price > long_level:
        return BreakoutSignal(
            direction="LONG",
            range_high=range_high,
            range_low=range_low,
            breakout_level=long_level,
            confirmation_price=test_price,
            candle_timestamp=reference.time,
            reason=f"Bullish breakout confirmed above {long_level}",
            signal_id=_signal_id(symbol, tf, "LONG", range_high, range_low, reference.time),
        )
    if allowed in {"SHORT", "BOTH"} and test_price < short_level:
        return BreakoutSignal(
            direction="SHORT",
            range_high=range_high,
            range_low=range_low,
            breakout_level=short_level,
            confirmation_price=test_price,
            candle_timestamp=reference.time,
            reason=f"Bearish breakout confirmed below {short_level}",
            signal_id=_signal_id(symbol, tf, "SHORT", range_high, range_low, reference.time),
        )
    return None


# ---------------------------------------------------------------------------
# 11. PnL
# ---------------------------------------------------------------------------


def calculate_position_pnl(position: StrategyPosition, current_price: float, contract_value: float) -> float:
    """Unrealized dollar PnL from price movement × size × contract value.

    LONG: (mark - entry) * qty * cv
    SHORT: (entry - mark) * qty * cv
    Contract value is product-specific. BTC is not ETH.
    """
    if position.direction == "LONG":
        return (current_price - position.entry_price) * position.quantity * contract_value
    return (position.entry_price - current_price) * position.quantity * contract_value


def calculate_paper_pnl(app: App, current_price: float) -> float:
    """Paper unrealized PnL using the same contract-value formula as live."""
    pos = app.runtime.position
    product = app.runtime.product
    if pos is None or product is None:
        return 0.0
    return calculate_position_pnl(pos, current_price, product.contract_value)


def calculate_strategy_pnl(app: App, exchange_unrealized: float | None, current_price: float) -> float:
    """Strategy-level PnL used for TP/SL.

    Prefer the exchange-reported unrealized PnL when LIVE. Fall back to the
    contract-value formula. Paper always uses the local formula.
    """
    pos = app.runtime.position
    product = app.runtime.product
    if pos is None or product is None:
        return 0.0
    if pos.paper:
        return calculate_paper_pnl(app, current_price) + pos.realized_pnl
    if exchange_unrealized is not None:
        return exchange_unrealized + pos.realized_pnl
    return calculate_position_pnl(pos, current_price, product.contract_value) + pos.realized_pnl


def trading_day_now(cfg: Config) -> date:
    return datetime.now(tz=cfg.tz).date()


def refresh_trading_day(app: App) -> None:
    today = trading_day_now(app.cfg)
    if app.runtime.trading_day != today:
        app.runtime.trading_day = today
        app.runtime.daily_realized_pnl = 0.0
        app.runtime.daily_orders = 0
        app.runtime.scheduled_close_done = False


def daily_pnl_total(app: App, unrealized: float) -> float:
    return app.runtime.daily_realized_pnl + unrealized


# ---------------------------------------------------------------------------
# 12. TP / SL
# ---------------------------------------------------------------------------


def calculate_take_profit(app: App) -> tuple[float | None, float | None]:
    """Return (tp_pnl_target, tp_price) according to config.

    PNL mode uses a dollar target. PERCENT uses an entry-price target.
    """
    pos = app.runtime.position
    tp = app.cfg.take_profit
    if pos is None or not tp.enabled:
        return None, None
    if tp.mode == "PNL":
        return tp.value, None
    if pos.direction == "LONG":
        return None, pos.entry_price * (1 + tp.value / 100.0)
    return None, pos.entry_price * (1 - tp.value / 100.0)


def calculate_stop_loss(app: App) -> tuple[float | None, float | None]:
    """Return (sl_pnl_abs, sl_price). sl_pnl_abs is a positive dollar amount."""
    pos = app.runtime.position
    sl = app.cfg.stop_loss
    if pos is None or not sl.enabled:
        return None, None
    if sl.mode == "PNL":
        return sl.value, None
    if pos.direction == "LONG":
        return None, pos.entry_price * (1 - sl.value / 100.0)
    return None, pos.entry_price * (1 + sl.value / 100.0)


def check_take_profit(app: App, strategy_pnl: float, current_price: float) -> bool:
    """True when the configured TP condition is satisfied."""
    tp_pnl, tp_price = calculate_take_profit(app)
    pos = app.runtime.position
    if pos is None or not app.cfg.take_profit.enabled:
        return False
    if tp_pnl is not None:
        return strategy_pnl >= tp_pnl
    if tp_price is None:
        return False
    if pos.direction == "LONG":
        return current_price >= tp_price
    return current_price <= tp_price


def _price_str(price: float, tick_size: Decimal) -> str:
    exponent = tick_size.as_tuple().exponent
    digits = max(0, -exponent) if isinstance(exponent, int) else 8
    return f"{price:.{digits}f}"


def exit_limit_prices(app: App, direction: str, entry_price: float, quantity: int) -> tuple[float | None, float | None]:
    """Take-profit and stop-loss prices from the fill and config.

    PNL converts the dollar target into points with quantity and contract value.
    PERCENT moves the entry price by the configured percent.
    """
    product = app.runtime.product
    if product is None or quantity <= 0:
        return None, None
    tick = product.tick_size
    contract_value = product.contract_value

    def _from_pnl(dollars: float, profit: bool) -> float:
        move = dollars / (quantity * contract_value)
        if direction == "LONG":
            return entry_price + move if profit else entry_price - move
        return entry_price - move if profit else entry_price + move

    tp_price: float | None = None
    sl_price: float | None = None
    tp = app.cfg.take_profit
    sl = app.cfg.stop_loss
    if tp.enabled:
        if tp.mode == "PNL":
            tp_price = _from_pnl(tp.value, profit=True)
        elif direction == "LONG":
            tp_price = entry_price * (1 + tp.value / 100.0)
        else:
            tp_price = entry_price * (1 - tp.value / 100.0)
        tp_price = round_to_tick(tp_price, tick)
    if sl.enabled:
        if sl.mode == "PNL":
            sl_price = _from_pnl(sl.value, profit=False)
        elif direction == "LONG":
            sl_price = entry_price * (1 - sl.value / 100.0)
        else:
            sl_price = entry_price * (1 + sl.value / 100.0)
        sl_price = round_to_tick(sl_price, tick)
    return tp_price, sl_price


def check_stop_loss(app: App, strategy_pnl: float, current_price: float) -> bool:
    """True when the configured SL condition is satisfied."""
    sl_pnl, sl_price = calculate_stop_loss(app)
    pos = app.runtime.position
    if pos is None or not app.cfg.stop_loss.enabled:
        return False
    if sl_pnl is not None:
        return strategy_pnl <= -sl_pnl
    if sl_price is None:
        return False
    if pos.direction == "LONG":
        return current_price <= sl_price
    return current_price >= sl_price


# ---------------------------------------------------------------------------
# 13. risk / schedule / stop
# ---------------------------------------------------------------------------


def check_stop_signal(app: App) -> bool:
    """True when stop.py wrote the stop file after this process started.

    Manual stop does not close positions. The process exits after the
    current poll finishes.
    """
    if app.runtime.interrupt:
        return True
    if not STOP_FILE.exists():
        return False
    try:
        mtime = STOP_FILE.stat().st_mtime
    except OSError:
        return False
    return mtime >= app.runtime.started_at.timestamp() - 1


def _seconds_since_midnight(moment: datetime) -> int:
    return moment.hour * 3600 + moment.minute * 60 + moment.second


def check_schedule(app: App) -> tuple[bool, bool]:
    """Return (entries_allowed_by_clock, stop_time_reached).

    CONTINUOUS: entries always allowed by the clock.
    SCHEDULED: entries only inside start_time/stop_time in config timezone.
    Overnight windows (start > stop) are supported.
    """
    if app.cfg.run_mode != "SCHEDULED":
        return True, False
    now = datetime.now(tz=app.cfg.tz)
    now_s = _seconds_since_midnight(now)
    start_h, start_m = _parse_hhmm(app.cfg.start_time, "start_time")
    stop_h, stop_m = _parse_hhmm(app.cfg.stop_time, "stop_time")
    start_s = start_h * 3600 + start_m * 60
    stop_s = stop_h * 3600 + stop_m * 60
    if start_s == stop_s:
        return True, False
    if start_s < stop_s:
        in_window = start_s <= now_s < stop_s
        stop_reached = now_s >= stop_s
    else:
        in_window = now_s >= start_s or now_s < stop_s
        stop_reached = now_s >= stop_s and now_s < start_s
    return in_window, stop_reached


def check_day_trading_window(app: App) -> bool:
    """True when a scheduled day-trading session should flatten at stop_time."""
    if app.cfg.run_mode != "SCHEDULED":
        return False
    if not app.cfg.day_trading_enabled:
        return False
    if not app.cfg.close_positions_at_stop_time:
        return False
    _in_window, stop_reached = check_schedule(app)
    return stop_reached


def open_strategy_count(app: App) -> int:
    return 1 if app.runtime.position is not None else 0


def validate_risk_conditions(app: App, unrealized: float) -> str | None:
    """Return a human reason if new entries are forbidden by risk limits."""
    if not app.cfg.strategy.enabled:
        return "STRATEGY DISABLED"
    if open_strategy_count(app) >= app.cfg.max_open_strategies:
        return "MAX OPEN STRATEGIES"
    if app.runtime.daily_orders >= app.cfg.max_orders_per_day:
        return "MAX ORDERS PER DAY"
    if daily_pnl_total(app, unrealized) <= -app.cfg.max_loss_per_day_dollar:
        return "DAILY LOSS LIMIT REACHED"
    in_window, stop_reached = check_schedule(app)
    if not in_window:
        return "OUTSIDE SCHEDULE"
    if stop_reached:
        return "STOP TIME REACHED"
    if app.runtime.market_stale:
        return "MARKET DATA STALE — ENTRY BLOCKED"
    if app.runtime.exit_in_progress:
        return "EXIT IN PROGRESS"
    if app.runtime.pending_client_order_id:
        return "ORDER RECONCILE PENDING"
    return None


def validate_entry_conditions(app: App, signal: BreakoutSignal) -> str | None:
    """Signal-level checks separate from risk checks."""
    if app.runtime.position is not None:
        return "POSITION ALREADY ACTIVE"
    if app.runtime.status in {"ENTERING", "EXITING", "STOPPED", "ERROR"}:
        return f"STATE {app.runtime.status} BLOCKS ENTRY"
    if signal.direction not in {"LONG", "SHORT"}:
        return "INVALID DIRECTION"
    if app.cfg.strategy.direction not in {"BOTH", signal.direction}:
        return "DIRECTION FILTER"
    if app.cfg.mode == "LIVE":
        try:
            open_orders = get_open_orders(app)
        except DeltaAPIError as exc:
            return f"OPEN ORDER QUERY FAILED {exc}"
        working = [
            o
            for o in open_orders
            if str(o.get("state") or "").lower() in {"open", "pending", "unfilled", "partially_filled"}
        ]
        if working:
            return "OPEN ORDERS ALREADY EXIST"
    return None


def check_duplicate_trade(app: App, signal: BreakoutSignal) -> str | None:
    """Refuse to fire the same breakout identity twice.

    Identity is symbol + timeframe + direction + range + breakout candle.
    Remaining above the level on later polls must not place another BUY.
    """
    if signal.signal_id in app.runtime.consumed_signal_ids:
        return "SIGNAL ALREADY CONSUMED"
    if not app.cfg.allow_reentry_after_exit:
        if app.runtime.last_signal and app.runtime.last_signal.signal_id == signal.signal_id:
            return "SAME BREAKOUT AFTER EXIT"
    return None


def mark_signal_consumed(app: App, signal: BreakoutSignal) -> None:
    if signal.signal_id not in app.runtime.consumed_signal_ids:
        app.runtime.consumed_signal_ids.append(signal.signal_id)
    app.runtime.last_signal = signal
    app.runtime.last_processed_candle_ts = signal.candle_timestamp


# ---------------------------------------------------------------------------
# 14. order execution / paper / positions
# ---------------------------------------------------------------------------


def _client_order_id(prefix: str, signal_id: str) -> str:
    digest = hashlib.sha1(signal_id.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}{digest}"[:32]


def _order_result_dict(payload: Any) -> dict[str, Any]:
    raw = _unwrap(payload)
    if isinstance(raw, list) and raw:
        raw = raw[0]
    return raw if isinstance(raw, dict) else {}


def get_current_position(app: App) -> dict[str, Any] | None:
    """Fetch this product's exchange position. Never inspect unrelated symbols."""
    if app.runtime.product is None:
        return None
    if not app.env.has_credentials:
        return None
    raw = _unwrap(app.client.get_positions(app.runtime.product.product_id))
    rows: list[dict[str, Any]]
    if isinstance(raw, list):
        rows = [r for r in raw if isinstance(r, dict)]
    elif isinstance(raw, dict):
        rows = [raw]
    else:
        rows = []
    pid = app.runtime.product.product_id
    symbol = app.runtime.product.symbol
    for row in rows:
        row_pid = _to_int(row.get("product_id") or (row.get("product") or {}).get("id"))
        row_sym = str(row.get("product_symbol") or (row.get("product") or {}).get("symbol") or "")
        if row_pid == pid or row_sym == symbol or (row_pid is None and not row_sym):
            size = _to_int(row.get("size"), 0) or 0
            if size != 0:
                return row
    return None


def get_open_orders(app: App) -> list[dict[str, Any]]:
    if app.runtime.product is None or not app.env.has_credentials:
        return []
    raw = _unwrap(app.client.get_open_orders(app.runtime.product.product_id))
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def monitor_order(app: App, *, order_id: int | None, client_order_id: str | None, timeout: float) -> dict[str, Any]:
    """Poll one order until filled, cancelled, or timeout.

    Safety: this function never places a second order. Unknown state is
    returned to the caller for explicit handling.
    """
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            last = _order_result_dict(app.client.get_order_by_id(order_id=order_id, client_order_id=client_order_id))
        except DeltaAPIError:
            time.sleep(0.5)
            continue
        state = str(last.get("state") or last.get("status") or "").lower()
        unfilled = _to_int(last.get("unfilled_size"), 0) or 0
        if state in {"closed", "filled"} or (state == "partially_filled" and unfilled == 0):
            return last
        if state in {"cancelled", "canceled", "rejected"}:
            return last
        time.sleep(0.5)
    return last


def _fill_price(order: dict[str, Any], fallback: float) -> float:
    return _to_float(order.get("average_fill_price") or order.get("limit_price"), fallback) or fallback


def paper_enter_position(app: App, signal: BreakoutSignal, price: float) -> None:
    """Simulate an entry at the live market price. No REST mutation."""
    app.runtime.position = StrategyPosition(
        strategy_id=signal.signal_id,
        symbol=app.runtime.product.symbol if app.runtime.product else app.cfg.symbol,
        direction=signal.direction,
        quantity=app.cfg.order_size,
        entry_price=price,
        entry_time=datetime.now(tz=app.cfg.tz),
        order_id="PAPER",
        client_order_id=_client_order_id("p", signal.signal_id),
        breakout_level=signal.breakout_level,
        range_high=signal.range_high,
        range_low=signal.range_low,
        breakout_candle_timestamp=signal.candle_timestamp,
        paper=True,
    )
    app.runtime.daily_orders += 1
    app.runtime.status = signal.direction
    app.runtime.last_trade_summary = f"PAPER {signal.direction} {app.cfg.order_size} @ {price}"
    log_event(app.runtime, f"PAPER ENTRY {signal.direction} {app.cfg.symbol} SIZE {app.cfg.order_size} ENTRY {price}")
    app.runtime.exchange_unrealized = None
    tp_price, sl_price = exit_limit_prices(app, signal.direction, price, app.cfg.order_size)
    if app.runtime.position is not None:
        app.runtime.position.tp_price = tp_price
        app.runtime.position.sl_price = sl_price
    if tp_price is not None or sl_price is not None:
        log_event(app.runtime, f"PAPER TP/SL LIMITS TP {tp_price} SL {sl_price}")


def paper_exit_position(app: App, price: float, reason: str) -> float:
    """Simulate a reduce-only exit. Returns realized dollar PnL of the trade."""
    pos = app.runtime.position
    product = app.runtime.product
    if pos is None or product is None:
        return 0.0
    pnl = calculate_position_pnl(pos, price, product.contract_value)
    app.runtime.daily_realized_pnl += pnl
    app.runtime.last_trade_summary = f"PAPER EXIT {reason} {_money(pnl)}"
    log_event(app.runtime, f"PAPER EXIT {reason} FINAL PNL {_money(pnl)}")
    app.runtime.position = None
    app.runtime.exchange_unrealized = None
    app.runtime.status = "CLOSED"
    return pnl


def place_entry_order(app: App, signal: BreakoutSignal, last_price: float) -> None:
    """Place one entry. PAPER simulates. LIVE sends a single signed order.

    Safety: client_order_id is deterministic for this signal. If the POST
    result is unknown, store the id and reconcile before any retry.
    """
    if app.cfg.mode == "PAPER":
        paper_enter_position(app, signal, last_price)
        mark_signal_consumed(app, signal)
        return

    assert app.runtime.product is not None
    coid = _client_order_id("e", signal.signal_id)
    app.runtime.pending_client_order_id = coid
    app.runtime.status = "ENTERING"
    side = "buy" if signal.direction == "LONG" else "sell"
    limit_price = None
    if app.cfg.order_type == "limit_order":
        limit_price = str(round_to_tick(last_price, app.runtime.product.tick_size))
    try:
        payload = app.client.place_order(
            size=app.cfg.order_size,
            side=side,
            order_type=app.cfg.order_type,
            product_symbol=app.runtime.product.symbol,
            reduce_only=False,
            client_order_id=coid,
            limit_price=limit_price,
        )
    except DeltaAPIError as exc:
        existing = _lookup_existing_order(app, coid)
        if existing is None:
            app.runtime.pending_client_order_id = None
            app.runtime.status = "SCANNING"
            log_event(app.runtime, f"ORDER REJECTION {exc}", logging.ERROR)
            return
        payload = existing
    order = _order_result_dict(payload)
    order_id = _to_int(order.get("id"))
    wait = MARKET_FILL_WAIT_SECONDS if app.cfg.order_type == "market_order" else LIMIT_FILL_WAIT_SECONDS
    filled = monitor_order(app, order_id=order_id, client_order_id=coid, timeout=wait)
    state = str(filled.get("state") or filled.get("status") or "").lower()
    if app.cfg.order_type == "limit_order" and state in {"open", "pending", "unfilled", ""}:
        try:
            if order_id is not None:
                app.client.cancel_order(product_id=app.runtime.product.product_id, order_id=order_id)
            log_event(app.runtime, "LIMIT UNFILLED — CANCELLED, NOT RETRIED")
        except DeltaAPIError as exc:
            log_event(app.runtime, f"LIMIT CANCEL FAILED {exc}", logging.ERROR)
        app.runtime.pending_client_order_id = None
        app.runtime.status = "SCANNING"
        mark_signal_consumed(app, signal)
        return
    if state in {"cancelled", "canceled", "rejected"}:
        app.runtime.pending_client_order_id = None
        app.runtime.status = "SCANNING"
        log_event(app.runtime, f"ENTRY {state.upper()}")
        mark_signal_consumed(app, signal)
        return
    entry_price = _fill_price(filled, last_price)
    app.runtime.position = StrategyPosition(
        strategy_id=signal.signal_id,
        symbol=app.runtime.product.symbol,
        direction=signal.direction,
        quantity=app.cfg.order_size,
        entry_price=entry_price,
        entry_time=datetime.now(tz=app.cfg.tz),
        order_id=str(order_id) if order_id is not None else None,
        client_order_id=coid,
        breakout_level=signal.breakout_level,
        range_high=signal.range_high,
        range_low=signal.range_low,
        breakout_candle_timestamp=signal.candle_timestamp,
        paper=False,
    )
    app.runtime.daily_orders += 1
    app.runtime.pending_client_order_id = None
    app.runtime.status = signal.direction
    app.runtime.last_trade_summary = f"LIVE {signal.direction} {app.cfg.order_size} @ {entry_price}"
    mark_signal_consumed(app, signal)
    log_event(app.runtime, f"LIVE ENTRY {signal.direction} {app.cfg.symbol} SIZE {app.cfg.order_size} ENTRY {entry_price}")
    app.runtime.exchange_unrealized = None
    attach_bracket_orders(app)


def _lookup_existing_order(app: App, client_order_id: str) -> dict[str, Any] | None:
    """Query an uncertain submission by client_order_id. Never place another."""
    try:
        return _order_result_dict(app.client.get_order_by_id(client_order_id=client_order_id))
    except DeltaAPIError:
        return None


def attach_bracket_orders(app: App) -> None:
    """Rest a take-profit limit and a stop-limit stop-loss after the market fill."""
    pos = app.runtime.position
    product = app.runtime.product
    if pos is None or product is None or pos.paper:
        return
    tp_price, sl_price = exit_limit_prices(app, pos.direction, pos.entry_price, pos.quantity)
    pos.tp_price = tp_price
    pos.sl_price = sl_price
    if tp_price is None and sl_price is None:
        return
    take_profit_order = None
    stop_loss_order = None
    if tp_price is not None:
        tp_text = _price_str(tp_price, product.tick_size)
        take_profit_order = {"order_type": "limit_order", "stop_price": tp_text, "limit_price": tp_text}
    if sl_price is not None:
        sl_text = _price_str(sl_price, product.tick_size)
        stop_loss_order = {"order_type": "limit_order", "stop_price": sl_text, "limit_price": sl_text}
    try:
        app.client.place_bracket_orders(
            product_id=product.product_id,
            product_symbol=product.symbol,
            stop_loss_order=stop_loss_order,
            take_profit_order=take_profit_order,
            bracket_stop_trigger_method="mark_price",
        )
    except DeltaAPIError as exc:
        log_event(app.runtime, f"BRACKET ORDER FAILED — PLACING SEPARATE LIMITS {exc}", logging.ERROR)
        if _place_separate_exit_orders(app, tp_price, sl_price):
            pos.bracket_placed = True
            log_event(app.runtime, f"SEPARATE LIMITS PLACED TP {tp_price} SL {sl_price}")
        return
    pos.bracket_placed = True
    log_event(app.runtime, f"BRACKET LIMITS PLACED TP {tp_price} SL {sl_price}")


def _place_separate_exit_orders(app: App, tp_price: float | None, sl_price: float | None) -> bool:
    """Place reduce-only TP and SL immediately when the bracket endpoint rejects."""
    pos = app.runtime.position
    product = app.runtime.product
    if pos is None or product is None:
        return False
    side = "sell" if pos.direction == "LONG" else "buy"
    placed = True
    if tp_price is not None:
        try:
            app.client.place_order(
                size=pos.quantity,
                side=side,
                order_type="limit_order",
                product_symbol=product.symbol,
                reduce_only=True,
                client_order_id=_client_order_id("t", pos.strategy_id),
                limit_price=_price_str(tp_price, product.tick_size),
            )
        except DeltaAPIError as exc:
            placed = False
            log_event(app.runtime, f"TP LIMIT FAILED {exc}", logging.ERROR)
    if sl_price is not None:
        sl_text = _price_str(sl_price, product.tick_size)
        try:
            app.client.place_order(
                size=pos.quantity,
                side=side,
                order_type="limit_order",
                product_symbol=product.symbol,
                reduce_only=True,
                client_order_id=_client_order_id("s", pos.strategy_id),
                limit_price=sl_text,
                stop_order_type="stop_loss_order",
                stop_price=sl_text,
                stop_trigger_method="mark_price",
            )
        except DeltaAPIError as exc:
            placed = False
            log_event(app.runtime, f"SL LIMIT FAILED {exc}", logging.ERROR)
    return placed


def cancel_product_orders(app: App) -> None:
    """Cancel resting orders on this product before a bot-driven market flatten."""
    product = app.runtime.product
    if product is None:
        return
    try:
        orders = get_open_orders(app)
    except DeltaAPIError as exc:
        log_event(app.runtime, f"OPEN ORDER LOOKUP FAILED {exc}", logging.WARNING)
        return
    for order in orders:
        order_id = _to_int(order.get("id"))
        if order_id is None:
            continue
        try:
            app.client.cancel_order(product_id=product.product_id, order_id=order_id)
        except DeltaAPIError as exc:
            log_event(app.runtime, f"CANCEL ORDER {order_id} FAILED {exc}", logging.WARNING)


def _bracket_exit_reason(pos: StrategyPosition, price: float) -> str:
    if pos.tp_price is None and pos.sl_price is None:
        return "TP"
    if pos.direction == "LONG":
        tp_hit = pos.tp_price is not None and price >= pos.tp_price
        sl_hit = pos.sl_price is not None and price <= pos.sl_price
    else:
        tp_hit = pos.tp_price is not None and price <= pos.tp_price
        sl_hit = pos.sl_price is not None and price >= pos.sl_price
    if tp_hit and not sl_hit:
        return "TP"
    if sl_hit and not tp_hit:
        return "SL"
    tp_dist = abs(price - pos.tp_price) if pos.tp_price is not None else float("inf")
    sl_dist = abs(price - pos.sl_price) if pos.sl_price is not None else float("inf")
    return "TP" if tp_dist <= sl_dist else "SL"


def finalize_bracket_fill(app: App) -> None:
    """Record a close already done by the exchange bracket. Do not send another order."""
    pos = app.runtime.position
    product = app.runtime.product
    if pos is None or product is None:
        return
    mark = app.runtime.mark_price or app.runtime.current_price or pos.entry_price
    reason = _bracket_exit_reason(pos, mark)
    exit_price = pos.tp_price if reason == "TP" and pos.tp_price is not None else pos.sl_price
    if exit_price is None:
        exit_price = mark
    pnl = calculate_position_pnl(pos, exit_price, product.contract_value)
    app.runtime.daily_realized_pnl += pnl
    app.runtime.last_trade_summary = f"LIVE EXIT {reason} {_money(pnl)}"
    log_event(app.runtime, f"BRACKET FILLED {reason} FINAL REALIZED PNL {_money(pnl)}")
    app.runtime.position = None
    app.runtime.exchange_unrealized = None
    app.runtime.status = "CLOSED"
    app.runtime.exit_in_progress = False
    _maybe_stop_after_exit(app, reason, pnl)


def place_exit_order(app: App, reason: str, last_price: float) -> float | None:
    """Close only this strategy's product with a reduce-only order.

    Locks exit_in_progress so polling cannot stack exits. Returns realized
    PnL after the exchange position is verified flat, or None if still open.
    """
    pos = app.runtime.position
    product = app.runtime.product
    if pos is None or product is None:
        return None
    if app.runtime.exit_in_progress:
        return None
    app.runtime.exit_in_progress = True
    app.runtime.status = "EXITING"
    log_event(app.runtime, f"{reason} TRIGGERED — CLOSING POSITION")

    if pos.paper or app.cfg.mode == "PAPER":
        pnl = paper_exit_position(app, last_price, reason)
        app.runtime.exit_in_progress = False
        return pnl

    cancel_product_orders(app)
    side = "sell" if pos.direction == "LONG" else "buy"
    coid = _client_order_id("x", pos.strategy_id + reason)
    app.runtime.pending_client_order_id = coid
    try:
        payload = app.client.place_order(
            size=pos.quantity,
            side=side,
            order_type="market_order",
            product_symbol=product.symbol,
            reduce_only=True,
            client_order_id=coid,
        )
    except DeltaAPIError as exc:
        existing = _lookup_existing_order(app, coid)
        if existing is None:
            log_event(app.runtime, f"EXIT ORDER ERROR {exc}", logging.ERROR)
            app.runtime.exit_in_progress = False
            app.runtime.pending_client_order_id = None
            app.runtime.status = pos.direction
            return None
        payload = existing
    order = _order_result_dict(payload)
    order_id = _to_int(order.get("id"))
    filled = monitor_order(app, order_id=order_id, client_order_id=coid, timeout=MARKET_FILL_WAIT_SECONDS)
    exit_price = _fill_price(filled, last_price)
    if not verify_position_closed(app):
        log_event(app.runtime, "EXIT SUBMITTED BUT POSITION STILL OPEN — WILL RETRY NEXT POLL", logging.WARNING)
        app.runtime.exit_in_progress = False
        app.runtime.pending_client_order_id = None
        app.runtime.status = pos.direction
        return None
    pnl = calculate_position_pnl(pos, exit_price, product.contract_value)
    app.runtime.daily_realized_pnl += pnl
    app.runtime.last_trade_summary = f"LIVE EXIT {reason} {_money(pnl)}"
    log_event(app.runtime, f"POSITION CLOSED {reason} FINAL REALIZED PNL {_money(pnl)}")
    app.runtime.position = None
    app.runtime.exchange_unrealized = None
    app.runtime.pending_client_order_id = None
    app.runtime.exit_in_progress = False
    app.runtime.status = "CLOSED"
    return pnl


def close_strategy_position(app: App, reason: str, last_price: float) -> float | None:
    """Reduce-only close of this bot's product. Never account-wide flatten."""
    return place_exit_order(app, reason, last_price)


def verify_position_closed(app: App) -> bool:
    """True only when exchange size for this product is zero."""
    if app.cfg.mode == "PAPER":
        return app.runtime.position is None
    try:
        row = get_current_position(app)
    except DeltaAPIError as exc:
        log_event(app.runtime, f"VERIFY CLOSE FAILED {exc}", logging.WARNING)
        return False
    return row is None


def reconcile_position(app: App) -> None:
    """Align local state with the exchange after start, dropouts, or unknown orders.

    If the exchange is flat, do not keep a permanent local lock.
    If this product has size, resume LONG/SHORT monitoring.
    PAPER never adopts a live exchange position as a paper trade.
    """
    if app.runtime.product is None:
        return
    if app.runtime.pending_client_order_id and app.cfg.mode == "LIVE":
        existing = _lookup_existing_order(app, app.runtime.pending_client_order_id)
        if existing:
            state = str(existing.get("state") or "").lower()
            log_event(app.runtime, f"RECONCILED PENDING ORDER STATE={state or 'unknown'}")

    if not app.env.has_credentials:
        return
    try:
        row = get_current_position(app)
    except DeltaAPIError as exc:
        app.runtime.api_ok = False
        app.runtime.last_error = str(exc)
        return
    app.runtime.api_ok = True

    if app.cfg.mode == "PAPER":
        if row is not None:
            size = _to_int(row.get("size"), 0) or 0
            app.runtime.entries_blocked_reason = "LIVE EXCHANGE POSITION EXISTS — PAPER WILL NOT STACK"
            if not app.runtime.paper_live_position_warned:
                app.runtime.paper_live_position_warned = True
                log_event(
                    app.runtime,
                    f"WARNING live {app.cfg.symbol} size {size} exists while PAPER is running. Not closing it.",
                    logging.WARNING,
                )
        return

    if row is None:
        pos = app.runtime.position
        if pos is not None and not pos.paper and not app.runtime.exit_in_progress:
            if pos.bracket_placed:
                finalize_bracket_fill(app)
            else:
                log_event(app.runtime, "EXCHANGE FLAT — LOCAL POSITION CLEARED")
                app.runtime.position = None
                app.runtime.exchange_unrealized = None
                if app.runtime.status in {"LONG", "SHORT", "ENTERING"}:
                    app.runtime.status = "SCANNING"
        return

    size = _to_int(row.get("size"), 0) or 0
    if size == 0:
        return
    direction = "LONG" if size > 0 else "SHORT"
    entry = _to_float(row.get("entry_price") or row.get("average_entry_price")) or app.runtime.current_price or 0.0
    app.runtime.exchange_unrealized = _exchange_unrealized(row)
    if app.runtime.position is None:
        app.runtime.position = StrategyPosition(
            strategy_id=f"resume-{app.runtime.product.product_id}",
            symbol=app.runtime.product.symbol,
            direction=direction,
            quantity=abs(size),
            entry_price=entry,
            entry_time=datetime.now(tz=app.cfg.tz),
            order_id=str(row.get("order_id") or "resume"),
            client_order_id=None,
            breakout_level=None,
            range_high=None,
            range_low=None,
            breakout_candle_timestamp=None,
            paper=False,
        )
        app.runtime.status = direction
        log_event(app.runtime, f"RECONCILED EXISTING {direction} SIZE {abs(size)} ENTRY {entry}")
    else:
        app.runtime.position.quantity = abs(size)
        app.runtime.position.direction = direction
        if entry:
            app.runtime.position.entry_price = entry


# ---------------------------------------------------------------------------
# 17. dashboard
# ---------------------------------------------------------------------------


def _mode_color(mode: str) -> str:
    return "bold red" if mode == "LIVE" else "bold green"


def render_dashboard(app: App) -> Panel:
    """Build the sci-fi terminal panel. Called every poll; not a log line."""
    cfg = app.cfg
    rt = app.runtime
    pos = rt.position
    product = rt.product
    current = rt.current_price
    mark = rt.mark_price or current
    unrealized = 0.0
    if pos is not None and product is not None and mark is not None:
        if rt.exchange_unrealized is not None and not pos.paper:
            unrealized = rt.exchange_unrealized
        else:
            unrealized = calculate_position_pnl(pos, mark, product.contract_value)

    tp_pnl, tp_price = calculate_take_profit(app)
    sl_pnl, sl_price = calculate_stop_loss(app)
    digits = max(0, min(8, -int(product.tick_size.as_tuple().exponent) if product else 2))

    header = Table.grid(expand=True)
    header.add_column(justify="center")
    title = Text("◈ DELTA // BREAKOUT CORE ◈", style="bold cyan")
    header.add_row(title)
    if cfg.mode == "LIVE":
        header.add_row(Text("⚠ LIVE TRADING ENABLED", style="bold red"))
    if cfg.leverage >= HIGH_LEVERAGE_WARN:
        header.add_row(Text(f"⚠ LEVERAGE: {cfg.leverage}X", style="bold yellow"))

    sys_table = Table.grid(expand=True, padding=(0, 2))
    sys_table.add_column(style="dim", width=14)
    sys_table.add_column(width=16)
    sys_table.add_column(style="dim", width=14)
    sys_table.add_column()
    sys_table.add_row("SYSTEM", "ONLINE" if rt.api_ok else "DEGRADED", "MODE", Text(cfg.mode, style=_mode_color(cfg.mode)))
    sys_table.add_row("SYMBOL", cfg.symbol, "TF", cfg.timeframe)
    now_local = datetime.now(tz=cfg.tz)
    sys_table.add_row("TIME", now_local.strftime("%d %b %Y  %H:%M:%S IST"), "", "")
    sys_table.add_row("LEVERAGE", f"{cfg.leverage}X", "STATE", rt.status)
    sys_table.add_row("RUN", cfg.run_mode, "DIR", cfg.strategy.direction)

    mkt = Table.grid(expand=True, padding=(0, 2))
    mkt.add_column(style="dim", width=22)
    mkt.add_column()
    mkt.add_row("CURRENT PRICE", _px(current, digits))
    mkt.add_row("RANGE HIGH", _px(rt.range_high, digits))
    mkt.add_row("RANGE LOW", _px(rt.range_low, digits))
    mkt.add_row("BREAKOUT LONG", _px(rt.long_level, digits))
    mkt.add_row("BREAKOUT SHORT", _px(rt.short_level, digits))

    pos_table = Table.grid(expand=True, padding=(0, 2))
    pos_table.add_column(style="dim", width=22)
    pos_table.add_column()
    if pos is None:
        pos_table.add_row("POSITION", "NONE")
        pos_table.add_row("CURRENT PNL", _money(0.0))
        pos_table.add_row("TP TARGET", _money(tp_pnl) if tp_pnl is not None else _px(tp_price, digits))
        pos_table.add_row("SL TARGET", _money(-sl_pnl) if sl_pnl is not None else _px(sl_price, digits))
    else:
        pos_table.add_row("POSITION", f"{pos.direction}  SIZE {pos.quantity}")
        pos_table.add_row("CURRENT PNL", _money(unrealized, 4))
        pos_table.add_row("ENTRY PRICE", _px(pos.entry_price, digits))
        pos_table.add_row("CURRENT PRICE", _px(current, digits))
        pos_table.add_row("REALIZED PNL", _money(pos.realized_pnl))
        pos_table.add_row("TOTAL STRATEGY PNL", _money(unrealized + pos.realized_pnl))
        if tp_price is not None:
            pos_table.add_row("TP PRICE", _px(tp_price, digits))
            if current is not None:
                pos_table.add_row("DISTANCE TO TP", _px(abs(tp_price - current), digits))
        else:
            pos_table.add_row("TP TARGET", _money(tp_pnl))
        if sl_price is not None:
            pos_table.add_row("SL PRICE", _px(sl_price, digits))
            if current is not None:
                pos_table.add_row("DISTANCE TO SL", _px(abs(sl_price - current), digits))
        else:
            pos_table.add_row("SL TARGET", _money(-sl_pnl) if sl_pnl is not None else "—")
    pos_table.add_row("DAILY PNL", _money(daily_pnl_total(app, unrealized if pos else 0.0)))
    pos_table.add_row("DAILY ORDERS", f"{rt.daily_orders}/{cfg.max_orders_per_day}")

    foot = Table.grid(expand=True, padding=(0, 2))
    foot.add_column(style="dim", width=22)
    foot.add_column()
    last_sig = rt.last_signal.direction if rt.last_signal else "NONE"
    foot.add_row("LAST SIGNAL", last_sig)
    foot.add_row("LAST TRADE", rt.last_trade_summary)
    foot.add_row("NEXT POLL", f"{cfg.polling_seconds}s")
    foot.add_row("API", "CONNECTED" if rt.api_ok else "ERROR")
    if rt.market_stale:
        foot.add_row("MARKET", Text("STALE — ENTRY BLOCKED", style="bold red"))
    if rt.entries_blocked_reason:
        foot.add_row("ENTRY GATE", rt.entries_blocked_reason)
    if rt.last_error:
        foot.add_row("LAST ERROR", rt.last_error[:80])

    events = Text("\n".join(rt.events) or "waiting…", style="dim")
    body = Group(
        Align.center(header),
        sys_table,
        Text(""),
        mkt,
        Text(""),
        pos_table,
        Text(""),
        foot,
        Text(""),
        Text("EVENTS", style="bold cyan"),
        events,
    )
    border = "red" if cfg.mode == "LIVE" else "cyan"
    return Panel(body, border_style=border, title="DELTA INDIA  •  PERPETUAL FUTURES", subtitle="stop.py does not close positions")


# ---------------------------------------------------------------------------
# 21. shutdown
# ---------------------------------------------------------------------------


def shutdown_bot(app: App, reason: str, *, close_position: bool = False, price: float | None = None) -> None:
    """Exit the process. Manual stop never flattens. Scheduled/risk exits may.

    A stopped Python process does not automatically close exchange positions.
    """
    app.runtime.shutdown_reason = reason
    app.runtime.status = "STOPPED"
    if close_position and app.runtime.position is not None and price is not None:
        close_strategy_position(app, reason, price)
    log_event(app.runtime, f"BOT STOPPED — {reason}")
    if app.runtime.position is not None:
        log_event(app.runtime, "EXISTING POSITION REMAINS OPEN")
    raise HaltBot(reason)


def _install_signal_handlers(app: App) -> None:
    def _handle(_signum: int, _frame: Any) -> None:
        app.runtime.interrupt = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def _clear_stale_stop_file() -> None:
    if STOP_FILE.exists():
        try:
            STOP_FILE.unlink()
        except OSError:
            pass


def interruptible_sleep(app: App) -> None:
    deadline = time.time() + app.cfg.polling_seconds
    while time.time() < deadline:
        if check_stop_signal(app):
            return
        time.sleep(min(0.5, max(0.0, deadline - time.time())))


# ---------------------------------------------------------------------------
# 22. main loop
# ---------------------------------------------------------------------------


def _maybe_stop_after_exit(app: App, reason: str, pnl: float | None) -> None:
    if pnl is None:
        return
    if reason == "TP" and app.cfg.stop_after_tp:
        shutdown_bot(app, "TP EXIT COMPLETE")
    if reason == "SL" and app.cfg.stop_after_sl:
        shutdown_bot(app, "SL EXIT COMPLETE")
    if reason in {"DAILY LOSS", "SCHEDULED CLOSE"}:
        shutdown_bot(app, f"{reason} COMPLETE")


def _exchange_unrealized(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    return _to_float(row.get("unrealized_pnl") or row.get("unrealized_cash_pnl"))


def manage_open_position(app: App, last_price: float, mark_price: float) -> None:
    """Priority path: PnL, TP, SL, daily loss, scheduled flatten. No new entries."""
    pos = app.runtime.position
    product = app.runtime.product
    if pos is None or product is None:
        return
    exchange_row = None
    if app.cfg.mode == "LIVE" and app.env.has_credentials:
        try:
            exchange_row = get_current_position(app)
        except DeltaAPIError as exc:
            app.runtime.last_error = str(exc)
            log_event(app.runtime, f"API ERROR {exc}", logging.ERROR)
    unrealized = calculate_strategy_pnl(app, _exchange_unrealized(exchange_row), mark_price)
    app.runtime.exchange_unrealized = _exchange_unrealized(exchange_row)
    app.runtime.current_price = last_price
    app.runtime.mark_price = mark_price

    day_total = daily_pnl_total(app, unrealized)
    if day_total <= -app.cfg.max_loss_per_day_dollar:
        log_event(app.runtime, "DAILY LOSS LIMIT REACHED — NEW TRADES BLOCKED")
        pnl = close_strategy_position(app, "DAILY LOSS", last_price)
        _maybe_stop_after_exit(app, "DAILY LOSS", pnl)
        return

    if not pos.bracket_placed and check_take_profit(app, unrealized, mark_price):
        pnl = close_strategy_position(app, "TP", last_price)
        _maybe_stop_after_exit(app, "TP", pnl)
        return

    if not pos.bracket_placed and check_stop_loss(app, unrealized, mark_price):
        pnl = close_strategy_position(app, "SL", last_price)
        _maybe_stop_after_exit(app, "SL", pnl)
        return

    if check_day_trading_window(app) and not app.runtime.scheduled_close_done:
        pnl = close_strategy_position(app, "SCHEDULED CLOSE", last_price)
        if app.runtime.position is None:
            app.runtime.scheduled_close_done = True
            if app.cfg.stop_bot_after_close:
                _maybe_stop_after_exit(app, "SCHEDULED CLOSE", pnl if pnl is not None else 0.0)
        return


def scan_for_entry(app: App, last_price: float, completed: list[Candle]) -> None:
    """Evaluate a new breakout only when no strategy position is active."""
    if app.runtime.position is not None:
        return
    if not app.cfg.confirmation.candle_close_confirmation and completed:
        app.runtime.last_processed_candle_ts = completed[-1].time

    signal = detect_breakout_signal(app, completed, last_price)
    if app.cfg.confirmation.candle_close_confirmation and completed:
        app.runtime.last_processed_candle_ts = completed[-1].time

    if signal is None:
        if app.runtime.status not in {"WAITING", "SCANNING", "STOPPED"}:
            app.runtime.status = "SCANNING"
        return

    app.runtime.status = "SIGNAL_FOUND"
    if app.runtime.last_announced_signal_id != signal.signal_id:
        app.runtime.last_announced_signal_id = signal.signal_id
        log_event(
            app.runtime,
            f"SIGNAL {signal.direction} close/price={signal.confirmation_price} level={signal.breakout_level}",
        )
    app.runtime.status = "VALIDATING"
    dup = check_duplicate_trade(app, signal)
    if dup:
        app.runtime.entries_blocked_reason = dup
        app.runtime.status = "SCANNING"
        return
    entry_block = validate_entry_conditions(app, signal)
    if entry_block:
        app.runtime.entries_blocked_reason = entry_block
        app.runtime.status = "SCANNING"
        return
    risk_block = validate_risk_conditions(app, 0.0)
    if risk_block:
        app.runtime.entries_blocked_reason = risk_block
        app.runtime.status = "SCANNING"
        return
    if app.cfg.mode == "LIVE" and not app.runtime.live_warning_shown:
        log_event(app.runtime, "⚠ FIRST LIVE ORDER ABOUT TO BE SENT")
        app.runtime.live_warning_shown = True
    place_entry_order(app, signal, last_price)


def run_poll(app: App) -> None:
    """One polling cycle. Exits always outrank new entries."""
    refresh_trading_day(app)
    app.runtime.next_poll_seconds = app.cfg.polling_seconds
    app.runtime.entries_blocked_reason = None

    if check_stop_signal(app):
        shutdown_bot(app, "MANUAL STOP", close_position=False)

    try:
        last_price, mark_price = get_current_price(app)
        app.runtime.current_price = last_price
        app.runtime.mark_price = mark_price
        app.runtime.api_ok = True
        app.runtime.last_error = None
    except DeltaAPIError as exc:
        app.runtime.api_ok = False
        app.runtime.last_error = str(exc)
        log_event(app.runtime, f"API ERROR {exc}", logging.ERROR)
        if app.runtime.position is None:
            return
        last_price = app.runtime.current_price or 0.0
        mark_price = app.runtime.mark_price or last_price

    in_window, stop_reached = check_schedule(app)
    if app.cfg.run_mode == "SCHEDULED" and not in_window and app.runtime.position is None:
        app.runtime.status = "WAITING"
        app.runtime.entries_blocked_reason = "OUTSIDE SCHEDULE"
        if stop_reached and app.cfg.stop_bot_after_close and app.cfg.day_trading_enabled:
            shutdown_bot(app, "SCHEDULE ENDED")
        return

    try:
        reconcile_position(app)
    except DeltaAPIError as exc:
        app.runtime.api_ok = False
        app.runtime.last_error = str(exc)

    completed: list[Candle] = []
    try:
        candles = get_candles(app)
        completed, _forming = split_completed_candles(candles, app.cfg.timeframe, app.cfg.tz)
        app.runtime.market_stale = market_data_is_stale(app, completed)
        lookback = app.cfg.strategy.breakout_lookback_candles
        if len(completed) >= lookback and app.runtime.product is not None:
            range_high, range_low = calculate_breakout_range(completed, lookback)
            app.runtime.range_high = range_high
            app.runtime.range_low = range_low
            app.runtime.long_level, app.runtime.short_level = calculate_breakout_levels(
                range_high, range_low, app.cfg.confirmation, app.runtime.product.tick_size
            )
    except DeltaAPIError as exc:
        app.runtime.api_ok = False
        app.runtime.market_stale = True
        app.runtime.last_error = str(exc)
        log_event(app.runtime, f"MARKET DATA FAILURE {exc}", logging.ERROR)

    if app.runtime.position is not None:
        manage_open_position(app, last_price, mark_price)
        return

    if daily_pnl_total(app, 0.0) <= -app.cfg.max_loss_per_day_dollar:
        log_event(app.runtime, "DAILY LOSS LIMIT REACHED — NEW TRADES BLOCKED")
        shutdown_bot(app, "DAILY LOSS LIMIT")

    if app.runtime.market_stale:
        app.runtime.entries_blocked_reason = "MARKET DATA STALE — ENTRY BLOCKED"
        if app.runtime.status not in {"STOPPED"}:
            app.runtime.status = "SCANNING"
        return
    scan_for_entry(app, last_price, completed)


def main() -> int:
    """Process entry. Loads config, validates the product, then polls."""
    setup_logging()
    try:
        cfg = load_config()
        env = load_environment()
    except ConfigError as exc:
        console.print(f"[red]CONFIG ERROR:[/red] {exc}")
        return 2

    runtime = Runtime(status="STARTING", next_poll_seconds=cfg.polling_seconds, started_at=datetime.now(tz=cfg.tz))
    client = create_delta_client(cfg, env)
    app = App(cfg=cfg, env=env, client=client, runtime=runtime)
    _install_signal_handlers(app)
    _clear_stale_stop_file()

    log_event(runtime, "BOT STARTED")
    log_event(runtime, f"MODE {cfg.mode}")
    log_event(runtime, f"SYMBOL {cfg.symbol} TIMEFRAME {cfg.timeframe} LEVERAGE {cfg.leverage}X")
    if cfg.mode == "LIVE":
        log_event(runtime, "⚠ LIVE TRADING ENABLED — REAL MONEY AT RISK")
        runtime.live_warning_shown = False
    if cfg.leverage >= HIGH_LEVERAGE_WARN:
        log_event(runtime, f"⚠ LEVERAGE {cfg.leverage}X increases margin and liquidation sensitivity")

    try:
        validate_exchange_connection(app)
        product = get_product_metadata(app)
        validate_product(app, product)
        runtime.product = product
        log_event(
            runtime,
            f"PRODUCT id={product.product_id} tick={product.tick_size} contract_value={product.contract_value}",
        )
        apply_leverage(app)
        runtime.status = "SCANNING"
        reconcile_position(app)
    except (ConfigError, DeltaAPIError) as exc:
        console.print(f"[red]STARTUP FAILED:[/red] {exc}")
        client.close()
        return 2

    try:
        with Live(render_dashboard(app), console=console, refresh_per_second=4, screen=True) as live:
            while True:
                poll_started = time.time()
                try:
                    run_poll(app)
                except HaltBot:
                    live.update(render_dashboard(app))
                    break
                except DeltaAPIError as exc:
                    runtime.api_ok = False
                    runtime.last_error = str(exc)
                    log_event(runtime, f"API ERROR {exc}", logging.ERROR)
                live.update(render_dashboard(app))
                elapsed = time.time() - poll_started
                runtime.next_poll_seconds = max(0, int(cfg.polling_seconds - elapsed))
                remain = cfg.polling_seconds - elapsed
                if remain > 0:
                    deadline = time.time() + remain
                    next_draw = time.time() + 1
                    while time.time() < deadline:
                        if check_stop_signal(app):
                            break
                        if time.time() >= next_draw:
                            live.update(render_dashboard(app))
                            next_draw = time.time() + 1
                        time.sleep(min(0.25, max(0.0, deadline - time.time())))
                if check_stop_signal(app):
                    try:
                        shutdown_bot(app, "MANUAL STOP", close_position=False)
                    except HaltBot:
                        live.update(render_dashboard(app))
                        break
    except KeyboardInterrupt:
        runtime.interrupt = True
        log_event(runtime, "BOT STOPPED — KEYBOARD INTERRUPT")
        if runtime.position is not None:
            log_event(runtime, "EXISTING POSITION REMAINS OPEN")
    finally:
        client.close()

    if runtime.position is not None:
        console.print("[yellow]Bot stopped. Position was left open by design.[/yellow]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

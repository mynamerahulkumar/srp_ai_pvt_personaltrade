"""Strict configuration loader.

Unknown keys raise ConfigError.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from src.errors import ConfigError
from src.intervals import RESOLUTIONS

TOP_LEVEL = {"exchange", "historical_data"}
EXCHANGE_KEYS = {"name", "base_url"}
HISTORICAL_KEYS = {"symbols", "interval", "days", "timezone", "output_directory"}


def _reject_unknown(section: str, data: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"Unknown {section} configuration keys: {', '.join(unknown)}")


def _require(section: str, data: dict[str, Any], keys: set[str]) -> None:
    missing = sorted(keys - set(data))
    if missing:
        raise ConfigError(f"Missing {section} configuration keys: {', '.join(missing)}")


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be a mapping")
    return value


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty string")
    return value.strip()


def _int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field_name} must be an integer")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    number = _int(value, field_name)
    if number <= 0:
        raise ConfigError(f"{field_name} must be a positive integer")
    return number


def _interval(value: Any, field_name: str) -> str:
    text = _text(value, field_name)
    if text not in RESOLUTIONS:
        allowed = ", ".join(sorted(RESOLUTIONS))
        raise ConfigError(f"{field_name} '{text}' is unsupported. Allowed: {allowed}")
    return text


def _timezone(value: Any) -> str:
    name = _text(value, "historical_data.timezone")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"historical_data.timezone '{name}' is not a valid timezone") from exc
    return name


@dataclass
class HistoricalConfig:
    symbols: list[str]
    interval: str
    days: int
    timezone: str
    output_directory: str


@dataclass
class AppConfig:
    exchange_name: str
    base_url: str
    historical: HistoricalConfig
    config_path: Path | None = None

    def with_overrides(self, symbol: str | None = None, interval: str | None = None) -> AppConfig:
        historical = self.historical
        if symbol is not None:
            historical = replace(historical, symbols=[_text(symbol, "symbol")])
        if interval is not None:
            historical = replace(historical, interval=_interval(interval, "interval"))
        return replace(self, historical=historical)


def load_credentials(env: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """Return the API key and secret.

    Both may be empty. Exactly one of the two is an error.
    """
    import os

    source = env if env is not None else os.environ
    key = (source.get("DELTA_API_KEY") or "").strip()
    secret = (source.get("DELTA_API_SECRET") or "").strip()
    if bool(key) != bool(secret):
        raise ConfigError("Set both DELTA_API_KEY and DELTA_API_SECRET, or leave both empty.")
    if not key:
        return None, None
    return key, secret


def dataset_filename(symbol: str, interval: str) -> str:
    return f"{symbol}_{interval}.csv"


def dataset_path(config: AppConfig, symbol: str | None = None, interval: str | None = None) -> Path:
    symbol = symbol or config.historical.symbols[0]
    interval = interval or config.historical.interval
    return Path(config.historical.output_directory) / dataset_filename(symbol, interval)


def parse_config(data: dict[str, Any], config_path: Path | None = None) -> AppConfig:
    if not isinstance(data, dict):
        raise ConfigError("Configuration root must be a mapping")
    _reject_unknown("top-level", data, TOP_LEVEL)
    _require("top-level", data, TOP_LEVEL)

    exchange = _mapping(data["exchange"], "exchange")
    _reject_unknown("exchange", exchange, EXCHANGE_KEYS)
    _require("exchange", exchange, EXCHANGE_KEYS)

    historical_raw = _mapping(data["historical_data"], "historical_data")
    _reject_unknown("historical_data", historical_raw, HISTORICAL_KEYS)
    _require("historical_data", historical_raw, HISTORICAL_KEYS)
    symbols = historical_raw["symbols"]
    if not isinstance(symbols, list) or not symbols or not all(isinstance(item, str) and item.strip() for item in symbols):
        raise ConfigError("historical_data.symbols must be a non-empty list of strings")
    historical = HistoricalConfig(
        symbols=[item.strip() for item in symbols],
        interval=_interval(historical_raw["interval"], "historical_data.interval"),
        days=_positive_int(historical_raw["days"], "historical_data.days"),
        timezone=_timezone(historical_raw["timezone"]),
        output_directory=_text(historical_raw["output_directory"], "historical_data.output_directory"),
    )

    return AppConfig(
        exchange_name=_text(exchange["name"], "exchange.name"),
        base_url=_text(exchange["base_url"], "exchange.base_url").rstrip("/"),
        historical=historical,
        config_path=config_path,
    )


def load_config(path: Path | str) -> AppConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        raise ConfigError(f"Configuration file is empty: {config_path}")
    return parse_config(data, config_path)

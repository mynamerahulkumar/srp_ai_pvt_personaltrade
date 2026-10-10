"""Shared fixtures for candle download tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ZONE = ZoneInfo("Asia/Kolkata")


def epoch_ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    moment = datetime(year, month, day, hour, minute, tzinfo=ZONE)
    return int(moment.timestamp() * 1000)


def minimal_config(**overrides) -> dict:
    config = {
        "exchange": {
            "name": "delta_exchange_india",
            "base_url": "https://api.india.delta.exchange/v2",
        },
        "historical_data": {
            "symbols": ["BTCUSD"],
            "interval": "5m",
            "days": 14,
            "timezone": "Asia/Kolkata",
            "output_directory": "data/historical",
        },
    }
    for key, value in overrides.items():
        config[key] = value
    return config


def write_config(path: Path, config: dict) -> Path:
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path

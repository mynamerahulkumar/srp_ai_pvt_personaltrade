"""Write a simple OHLC CSV."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.historical_downloader.candle_fetcher import RawCandle

CSV_COLUMNS = ["datetime", "open", "high", "low", "close"]
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def export_candles(candles: list[RawCandle], path: Path, timezone_name: str) -> Path:
    zone = ZoneInfo(timezone_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for candle in candles:
            moment = datetime.fromtimestamp(candle.timestamp / 1000, tz=zone).replace(microsecond=0)
            writer.writerow(
                {
                    "datetime": moment.strftime(DATETIME_FORMAT),
                    "open": _format_number(candle.open),
                    "high": _format_number(candle.high),
                    "low": _format_number(candle.low),
                    "close": _format_number(candle.close),
                }
            )
    return path


def _format_number(value: float) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"

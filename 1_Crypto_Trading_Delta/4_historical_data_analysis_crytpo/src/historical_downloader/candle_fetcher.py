"""Paginated historical candle download."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.data.validators import ohlc_error
from src.errors import DeltaClientError
from src.historical_downloader.delta_client import DeltaCandleClient, unwrap_result
from src.intervals import interval_seconds

CHUNK_CANDLES = 500


def download_window(days: int, timezone_name: str, now: datetime | None = None) -> tuple[date, date]:
    """Return the inclusive calendar window ending today in the given timezone."""
    zone = ZoneInfo(timezone_name)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    end_date = clock.astimezone(zone).date()
    start_date = end_date - timedelta(days=days - 1)
    return start_date, end_date


@dataclass
class RawCandle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def fetch_candles(
    client: DeltaCandleClient,
    symbol: str,
    interval: str,
    start_date: date,
    end_date: date,
    timezone_name: str,
    *,
    deduplicate: bool = True,
    validate_ohlc: bool = True,
    pause_seconds: float = 0.0,
    sleep: Any = None,
    now: datetime | None = None,
) -> tuple[list[RawCandle], dict[str, Any]]:
    """Download completed candles. Missing bars are reported and not invented."""
    zone = ZoneInfo(timezone_name)
    start_sec = int(datetime.combine(start_date, time.min, tzinfo=zone).timestamp())
    end_exclusive = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
    end_sec = int(end_exclusive.timestamp())
    step = interval_seconds(interval)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    now_sec = int(clock.timestamp())
    collected: dict[int, RawCandle] = {}
    rejected = 0
    cursor = start_sec
    requests_made = 0
    while cursor < end_sec:
        window_end = min(end_sec, cursor + (CHUNK_CANDLES * step))
        payload = client.get_candles(symbol, interval, cursor, window_end)
        requests_made += 1
        rows = unwrap_result(payload)
        if not isinstance(rows, list):
            raise DeltaClientError(f"Candle response for {symbol} was not a list")
        newest = cursor
        for row in rows:
            candle, reason = _parse_api_row(row, validate_ohlc)
            if reason:
                rejected += 1
                continue
            assert candle is not None
            open_sec = candle.timestamp // 1000
            if open_sec < start_sec or open_sec >= end_sec:
                continue
            if open_sec + step > now_sec:
                continue
            previous = collected.get(candle.timestamp)
            if previous is not None:
                if not deduplicate or not _same(previous, candle):
                    raise DeltaClientError(f"Duplicate candle timestamp {candle.timestamp} for {symbol} {interval}")
                continue
            collected[candle.timestamp] = candle
            newest = max(newest, open_sec)
        if rows:
            advanced = newest + step
            cursor = advanced if advanced > cursor else window_end
        else:
            cursor = window_end
        if pause_seconds and sleep is not None and cursor < end_sec:
            sleep(pause_seconds)
    if rejected:
        raise DeltaClientError(f"Rejected {rejected} malformed candle row(s) for {symbol} {interval}")
    ordered = [collected[key] for key in sorted(collected)]
    report = {
        "symbol": symbol,
        "interval": interval,
        "requests": requests_made,
        "rows": len(ordered),
        "first_timestamp": ordered[0].timestamp if ordered else None,
        "last_timestamp": ordered[-1].timestamp if ordered else None,
    }
    if not ordered:
        raise DeltaClientError(
            f"No completed candles returned for {symbol} {interval} between {start_date.isoformat()} and {end_date.isoformat()}"
        )
    return ordered, report


def _parse_api_row(row: Any, validate_ohlc: bool) -> tuple[RawCandle | None, str | None]:
    if not isinstance(row, dict):
        return None, "row is not an object"
    try:
        timestamp = _to_milliseconds(row.get("time", row.get("timestamp")))
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        volume = float(row.get("volume") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None, "missing OHLCV fields"
    if validate_ohlc:
        error = ohlc_error(open_, high, low, close, volume)
        if error:
            return None, error
    return RawCandle(timestamp, open_, high, low, close, volume), None


def _to_milliseconds(value: Any) -> int:
    timestamp = int(value)
    if timestamp > 10_000_000_000_000:
        return timestamp // 1000
    if timestamp > 10_000_000_000:
        return timestamp
    return timestamp * 1000


def _same(left: RawCandle, right: RawCandle) -> bool:
    return (
        left.open == right.open
        and left.high == right.high
        and left.low == right.low
        and left.close == right.close
        and left.volume == right.volume
    )

"""Shared interval definitions for Delta Exchange India candles."""

from __future__ import annotations

RESOLUTIONS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "1d": 86400,
    "1w": 604800,
}

# Seconds in a 365-day year. Used only to annualize Sharpe ratios.
SECONDS_PER_YEAR = 365 * 24 * 60 * 60


def interval_seconds(interval: str) -> int:
    try:
        return RESOLUTIONS[interval]
    except KeyError as exc:
        allowed = ", ".join(sorted(RESOLUTIONS))
        raise ValueError(f"Unsupported interval '{interval}'. Allowed: {allowed}") from exc


def bars_per_year(interval: str) -> float:
    return SECONDS_PER_YEAR / interval_seconds(interval)

"""Download Delta Exchange India candles to CSV.

This process never places, edits, or cancels exchange orders.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from src.config_loader import dataset_path, load_config, load_credentials
from src.errors import ConfigError, DeltaClientError
from src.historical_downloader.candle_fetcher import download_window, fetch_candles
from src.historical_downloader.csv_exporter import export_candles
from src.historical_downloader.delta_client import DeltaCandleClient

ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download Delta candles to CSV.")
    parser.add_argument("--symbol", help="Override the configured symbol")
    parser.add_argument("--interval", help="Override the configured candle interval")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"), help="Path to config.yaml")
    args = parser.parse_args(argv)
    try:
        load_dotenv(ROOT / ".env")
        load_credentials()
        config = load_config(args.config).with_overrides(args.symbol, args.interval)
        _print_context(config)
        _download(config)
        return 0
    except (ConfigError, DeltaClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _print_context(config) -> None:
    print(f"Symbol: {', '.join(config.historical.symbols)}")
    print(f"Interval: {config.historical.interval}")


def _download(config) -> None:
    api_key, api_secret = load_credentials()
    client = DeltaCandleClient(config.base_url, api_key, api_secret)
    try:
        start_date, end_date = download_window(config.historical.days, config.historical.timezone)
        for symbol in config.historical.symbols:
            candles, report = fetch_candles(
                client,
                symbol,
                config.historical.interval,
                start_date,
                end_date,
                config.historical.timezone,
                pause_seconds=0.15,
                sleep=time.sleep,
            )
            destination = dataset_path(config, symbol, config.historical.interval)
            export_candles(candles, destination, config.historical.timezone)
            print(f"Saved {report['rows']} candles to {destination}")
    finally:
        client.session.close()


if __name__ == "__main__":
    sys.exit(main())

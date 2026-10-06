"""Historical backtest entry point for the Half Trend options bot.

Loads test.yaml (not config.yaml), connects to Dhan for history only, and
runs the shared replay engine in main.py. Never places an order.

Usage:
    python backtest.py

Switch candle_mode in test.yaml between NORMAL and RENKO to test either path.
"""

from __future__ import annotations

from pathlib import Path

import main as bot

ROOT = Path(__file__).resolve().parent
TEST_YAML = ROOT / "test.yaml"


def main() -> None:
    """Load test.yaml, connect for market history, and print a SIMULATED report."""
    try:
        bot.CONFIG = bot.load_config(TEST_YAML, for_backtest=True)
        client_id, access_token = bot.load_environment()
        bot.BROKER = bot.create_dhan_client(client_id, access_token)
        print(f"Loaded {TEST_YAML.name}")
        print("Mode: BACKTEST (no live orders)")
        bot.run_backtest()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Backtest failed: {exc}")
        print("No order was sent.")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

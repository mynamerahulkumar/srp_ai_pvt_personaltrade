from pathlib import Path

import pytest

from src.config_loader import dataset_filename, load_config, load_credentials, parse_config
from src.errors import ConfigError
from tests.conftest import minimal_config

ROOT = Path(__file__).resolve().parents[1]


def test_repository_config_loads():
    config = load_config(ROOT / "config.yaml")
    assert config.base_url == "https://api.india.delta.exchange/v2"
    assert config.historical.symbols == ["BTCUSD", "ETHUSD", "SOLUSD"]
    assert config.historical.interval == "5m"
    assert config.historical.days == 14
    assert dataset_filename("BTCUSD", "5m") == "BTCUSD_5m.csv"


def test_unknown_key_is_rejected():
    config = minimal_config()
    config["historical_data"]["start_date"] = "2026-09-26"
    with pytest.raises(ConfigError, match="Unknown historical_data"):
        parse_config(config)


def test_symbol_override_uses_interval_filename():
    config = parse_config(minimal_config()).with_overrides("ETHUSD", "15m")
    assert config.historical.symbols == ["ETHUSD"]
    assert config.historical.interval == "15m"
    assert dataset_filename(config.historical.symbols[0], config.historical.interval) == "ETHUSD_15m.csv"


def test_days_must_be_positive():
    config = minimal_config()
    config["historical_data"]["days"] = 0
    with pytest.raises(ConfigError, match="days"):
        parse_config(config)


def test_partial_credentials_are_rejected():
    with pytest.raises(ConfigError, match="both"):
        load_credentials({"DELTA_API_KEY": "abc", "DELTA_API_SECRET": ""})


def test_empty_credentials_are_allowed():
    assert load_credentials({"DELTA_API_KEY": "", "DELTA_API_SECRET": ""}) == (None, None)

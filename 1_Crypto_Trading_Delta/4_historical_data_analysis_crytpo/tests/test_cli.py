from pathlib import Path

import pytest

from main import main
from src.historical_downloader.candle_fetcher import RawCandle
from tests.conftest import epoch_ms, minimal_config, write_config


def test_download_writes_ohlc_csv(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("DELTA_API_KEY", raising=False)
    monkeypatch.delenv("DELTA_API_SECRET", raising=False)
    monkeypatch.setattr("main.load_dotenv", lambda *args, **kwargs: None)
    config = minimal_config()
    config["historical_data"]["output_directory"] = str(tmp_path)
    path = write_config(tmp_path / "config.yaml", config)
    timestamp = epoch_ms(2026, 10, 10, 9, 0)

    def fake_fetch(*args, **kwargs):
        candle = RawCandle(timestamp=timestamp, open=100, high=101, low=99, close=100.5, volume=3)
        return [candle], {"rows": 1}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.session = self

        def close(self):
            return None

    monkeypatch.setattr("main.fetch_candles", fake_fetch)
    monkeypatch.setattr("main.DeltaCandleClient", FakeClient)
    code = main(["--config", str(path), "--symbol", "BTCUSD", "--interval", "5m"])
    captured = capsys.readouterr()
    saved = tmp_path / "BTCUSD_5m.csv"
    assert code == 0
    assert "Date range" not in captured.out
    assert "Interval: 5m" in captured.out
    assert f"Saved 1 candles to {saved}" in captured.out
    assert saved.read_text(encoding="utf-8").splitlines() == [
        "datetime,open,high,low,close",
        "2026-10-10 09:00:00,100,101,99,100.5",
    ]


def test_backtest_mode_is_not_a_command():
    with pytest.raises(SystemExit):
        main(["--mode", "backtest"])


def test_source_cannot_name_an_order_endpoint():
    root = Path(__file__).resolve().parents[1]
    forbidden = ["/orders", "place_order", "place_market_order", "place_bracket", "cancel_order"]
    files = list((root / "src").rglob("*.py")) + [root / "main.py"]
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.name}: {token}")
    assert offenders == []

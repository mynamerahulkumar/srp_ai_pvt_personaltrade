from datetime import datetime, timedelta, timezone

import pytest

from src.errors import DeltaClientError
from src.historical_downloader.candle_fetcher import RawCandle, download_window, fetch_candles
from src.historical_downloader.csv_exporter import export_candles
from src.historical_downloader.delta_client import DeltaCandleClient
from src.intervals import interval_seconds
from tests.conftest import epoch_ms


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self.handler(url, params, headers)

    def close(self):
        return None


def test_non_get_is_refused_before_network():
    def handler(url, params, headers):
        raise AssertionError("network should not be called")

    client = DeltaCandleClient("https://api.india.delta.exchange/v2", session=FakeSession(handler))
    with pytest.raises(DeltaClientError, match="Refused HTTP POST"):
        client.request("POST", "/history/candles")


def test_signed_get_does_not_send_a_body_method():
    seen = {}

    def handler(url, params, headers):
        seen["headers"] = headers
        return FakeResponse({"success": True, "result": []})

    client = DeltaCandleClient(
        "https://api.india.delta.exchange/v2",
        api_key="key",
        api_secret="secret",
        session=FakeSession(handler),
        now=lambda: 1_700_000_000,
    )
    client.get_candles("BTCUSD", "5m", 1, 2)
    assert seen["headers"]["api-key"] == "key"
    assert "signature" in seen["headers"]
    assert seen["headers"]["timestamp"] == "1700000000"


def test_pagination_dedup_and_forming_candle(monkeypatch):
    import src.historical_downloader.candle_fetcher as fetcher

    monkeypatch.setattr(fetcher, "CHUNK_CANDLES", 1)
    start = epoch_ms(2026, 9, 26, 9, 0)
    step = interval_seconds("5m") * 1000
    rows = {
        start: _api_candle(start, 10),
        start + step: _api_candle(start + step, 11),
        start + 2 * step: _api_candle(start + 2 * step, 12),
    }

    def handler(url, params, headers):
        window_start = int(params["start"]) * 1000
        window_end = int(params["end"]) * 1000
        matched = [row for ts, row in rows.items() if window_start <= ts < window_end]
        return FakeResponse({"success": True, "result": matched})

    client = DeltaCandleClient("https://api.india.delta.exchange/v2", session=FakeSession(handler))
    now = datetime.fromtimestamp((start + 2 * step) / 1000, tz=timezone.utc)
    candles, report = fetch_candles(
        client,
        "BTCUSD",
        "5m",
        start_date=datetime(2026, 9, 26).date(),
        end_date=datetime(2026, 9, 26).date(),
        timezone_name="Asia/Kolkata",
        now=now,
    )
    assert [candle.timestamp for candle in candles] == [start, start + step]
    assert report["requests"] >= 2
    assert len(client.session.calls) >= 2


def test_download_window_covers_recent_days():
    now = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
    start_date, end_date = download_window(14, "Asia/Kolkata", now=now)
    assert end_date.isoformat() == "2026-10-10"
    assert start_date == end_date - timedelta(days=13)


def test_export_writes_ohlc_only(tmp_path):
    timestamp = epoch_ms(2026, 10, 10, 9, 5)
    path = export_candles(
        [RawCandle(timestamp=timestamp, open=10, high=12, low=9, close=11, volume=4)],
        tmp_path / "BTCUSD_5m.csv",
        "Asia/Kolkata",
    )
    assert path.name == "BTCUSD_5m.csv"
    assert path.read_text(encoding="utf-8").splitlines() == [
        "datetime,open,high,low,close",
        "2026-10-10 09:05:00,10,12,9,11",
    ]


def _api_candle(timestamp_ms: int, price: float) -> dict:
    return {
        "time": timestamp_ms // 1000,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price + 0.5,
        "volume": 3,
    }

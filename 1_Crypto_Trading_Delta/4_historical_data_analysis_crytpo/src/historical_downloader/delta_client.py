"""GET-only Delta Exchange India market-data client.

The request helper rejects every method other than GET before any network call.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import requests

from src.errors import DeltaClientError
from src.intervals import RESOLUTIONS


class DeltaCandleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (5.0, 30.0),
        now: Any = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = session or requests.Session()
        self.timeout = timeout
        self._now = now or time.time

    def request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        method = method.upper().strip()
        if method != "GET":
            raise DeltaClientError(f"Refused HTTP {method}. This client only fetches historical market data.")
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        headers = {"Accept": "application/json", "User-Agent": "srp-historical-backtest/1.0"}
        if self.api_key and self.api_secret:
            headers.update(self._signed_headers(method, path, params))
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 2:
                    raise DeltaClientError(f"Market data request failed: {exc}") from exc
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                continue
            return self._parse(response)
        raise DeltaClientError(f"Market data request failed: {last_error}")

    def get_candles(self, symbol: str, resolution: str, start: int, end: int) -> Any:
        if resolution not in RESOLUTIONS:
            allowed = ", ".join(sorted(RESOLUTIONS))
            raise DeltaClientError(f"Unsupported resolution '{resolution}'. Allowed: {allowed}")
        return self.request(
            "GET",
            "/history/candles",
            params={"symbol": symbol, "resolution": resolution, "start": start, "end": end},
        )

    def _signed_headers(self, method: str, path: str, params: dict[str, Any] | None) -> dict[str, str]:
        timestamp = str(int(self._now()))
        query = _query_string(params)
        sign_path = "/v2" + path if self.base_url.endswith("/v2") else path
        payload = f"{method}{timestamp}{sign_path}{query}"
        signature = hmac.new(
            (self.api_secret or "").encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "api-key": self.api_key or "",
            "timestamp": timestamp,
            "signature": signature,
        }

    @staticmethod
    def _parse(response: requests.Response) -> Any:
        try:
            body = response.json()
        except ValueError as exc:
            raise DeltaClientError(f"Market data response was not JSON (HTTP {response.status_code})") from exc
        if response.status_code >= 400 or (isinstance(body, dict) and body.get("success") is False):
            message = body if isinstance(body, dict) else response.text
            raise DeltaClientError(f"Market data request failed with HTTP {response.status_code}: {message}")
        return body


def unwrap_result(payload: Any) -> Any:
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def _query_string(params: dict[str, Any] | None) -> str:
    if not params:
        return ""
    clean = [(str(key), str(value)) for key, value in params.items() if value is not None]
    if not clean:
        return ""
    return "?" + urlencode(clean)

"""Delta SRP: single-file utility for Delta Exchange India algo trading.

This module is intentionally self-contained for cloud runners that execute one file.
It provides reusable primitives for:
- Market data
- Account/portfolio reads
- Full order lifecycle (place, edit, cancel, batch, bracket, leverage, margin)
- Four-leg credit-condor option helpers (chain, expiry, MANUAL strikes, payoff, TP/SL,
  sequential entry with cancel-on-timeout, flatten, restart reconcile)

Safety defaults:
- Environment defaults to India production.
- Mode defaults to read-only; set mode="trade" (or DELTA_MODE=trade) to enable mutations.
- Every mutation supports dry_run=True.

Copy this file into another strategy repo. Do not import it from a live bot's docs/
folder — reimplement the pieces you need inside that bot's main.py.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import requests

try:
	import yaml  # type: ignore
except Exception:  # pragma: no cover
	yaml = None


INDIA_PROD_REST = "https://api.india.delta.exchange/v2"
INDIA_TESTNET_REST = "https://cdn-ind.testnet.deltaex.org/v2"
INDIA_DEVNET_REST = "https://cdn-ind.devnet.deltaex.org/v2"

ENV_BASE_URLS = {
	"india_prod": INDIA_PROD_REST,
	"india_testnet": INDIA_TESTNET_REST,
	"india_devnet": INDIA_DEVNET_REST,
}

PRODUCT_ID_DETAILS = {
	"BTCUSD": {
		"product_id": 27,
		"asset": "BTC",
		"contract_type": "perpetual_futures",
		"tick_size": "0.5",
		"contract_value": "0.001",
	},
	"ETHUSD": {
		"product_id": 3136,
		"asset": "ETH",
		"contract_type": "perpetual_futures",
		"tick_size": "0.05",
		"contract_value": "0.01",
	},
	"SOLUSD": {
		"product_id": 14823,
		"asset": "SOL",
		"contract_type": "perpetual_futures",
		"tick_size": "0.0001",
		"contract_value": "1",
	},
	"XAUTUSD": {
		"product_id": 131253,
		"asset": "XAUT",
		"contract_type": "perpetual_futures",
		"tick_size": "0.01",
		"contract_value": "0.001",
	},
	"XAUSD": {
		"product_id": 131253,
		"asset": "XAUT",
		"contract_type": "perpetual_futures",
		"tick_size": "0.01",
		"contract_value": "0.001",
	},
}

RESOLUTIONS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d", "1w"}
MAX_BATCH = 50
VALID_ORDER_TYPES = {"limit_order", "market_order"}
RECENT_WINDOW_NOTICE = (
	"No start_time_us was provided; Delta returned its default recent window (~90 days). "
	"To include older records, pass explicit start_time_us (microseconds epoch)."
)

ORDER_LEVEL_BOOL_FIELDS = {
	"post_only",
	"reduce_only",
	"mmp",
}

STOP_TRIGGER_METHODS = {"mark_price", "last_traded_price", "spot_price"}


class DeltaAPIError(Exception):
	"""Structured Delta API error with context and remediation hints."""

	def __init__(self, code: str, message: str, status: int | None = None, context: dict[str, Any] | None = None):
		self.code = code or "UnknownError"
		self.message = message or "Delta API request failed"
		self.status = status
		self.context = context or {}
		super().__init__(self.__str__())

	def _hint(self) -> str | None:
		code = self.code.lower()
		text = f"{self.message} {self.context}".lower()
		if "invalidapikey" in code or "invalid api key" in text:
			return "Confirm API key/secret match the selected environment (prod vs testnet)."
		if "signatureexpired" in code or "expired" in text:
			return "Clock skew detected. Sync system time and retry."
		if "notwhitelisted" in code or "whitelist" in text:
			ip = self.context.get("request_ip") or self.context.get("ip")
			if ip:
				return f"Whitelist this IP in Delta API settings: {ip}"
			return "Whitelist your current public IP in Delta API settings."
		return None

	def __str__(self) -> str:
		parts = [f"{self.code}: {self.message}"]
		if self.status is not None:
			parts.append(f"status={self.status}")
		hint = self._hint()
		if hint:
			parts.append(f"hint={hint}")
		return " | ".join(parts)


@dataclass(frozen=True)
class DeltaConfig:
	env: str = "india_prod"
	base_url: str = INDIA_PROD_REST
	api_key: str | None = None
	api_secret: str | None = None
	mode: str = "read"
	debug: bool = False
	audit: bool = True
	timeout_connect: float = 10.0
	timeout_read: float = 30.0

	@property
	def has_credentials(self) -> bool:
		return bool(self.api_key and self.api_secret)

	def validate(self) -> None:
		if self.env not in ENV_BASE_URLS:
			raise ValueError(f"Unsupported env '{self.env}'. Supported: {sorted(ENV_BASE_URLS)}")
		if self.mode not in {"read", "trade"}:
			raise ValueError("mode must be 'read' or 'trade'")
		if bool(self.api_key) != bool(self.api_secret):
			raise ValueError("Both api_key and api_secret must be set together")


def _truthy(value: str | None) -> bool:
	if value is None:
		return False
	return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config_from_env() -> DeltaConfig:
	env = os.getenv("DELTA_ENV", "india_prod").strip().lower()
	base_url = os.getenv("DELTA_BASE_URL", ENV_BASE_URLS.get(env, INDIA_PROD_REST)).strip()
	if base_url.endswith("/"):
		base_url = base_url[:-1]
	if not base_url.endswith("/v2"):
		base_url = base_url + "/v2"

	cfg = DeltaConfig(
		env=env,
		base_url=base_url,
		api_key=os.getenv("DELTA_API_KEY"),
		api_secret=os.getenv("DELTA_API_SECRET"),
		mode=os.getenv("DELTA_MODE", "read").strip().lower(),
		debug=_truthy(os.getenv("DELTA_DEBUG")),
		audit=not _truthy(os.getenv("DELTA_AUDIT_OFF")),
		timeout_connect=float(os.getenv("DELTA_TIMEOUT_CONNECT", "10")),
		timeout_read=float(os.getenv("DELTA_TIMEOUT_READ", "30")),
	)
	cfg.validate()
	return cfg


def _body_string(payload: dict[str, Any] | list[Any] | None) -> str:
	if payload is None:
		return ""
	return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _query_string(params: dict[str, Any] | None) -> str:
	if not params:
		return ""
	clean: list[tuple[str, str]] = []
	for key, value in params.items():
		if value is None:
			continue
		clean.append((str(key), str(value)))
	if not clean:
		return ""
	return "?" + urlencode(clean)


def sign_request(secret: str, method: str, timestamp: str, path: str, query: str, body: str) -> str:
	payload = f"{method}{timestamp}{path}{query}{body}"
	return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
	return {k: v for k, v in payload.items() if v is not None}


def _csv(values: list[str] | None) -> str | None:
	if not values:
		return None
	return ",".join(values)


def _csv_ints(values: list[int] | None) -> str | None:
	if not values:
		return None
	return ",".join(str(v) for v in values)


def _bs(value: bool | None) -> str | None:
	if value is None:
		return None
	return "true" if value else "false"


def _safe_export_path(output_path: str) -> Path:
	raw = Path(output_path).expanduser()
	resolved = (Path.cwd() / raw).resolve() if not raw.is_absolute() else raw.resolve()
	cwd = Path.cwd().resolve()
	home = Path.home().resolve()
	if not (resolved.is_relative_to(cwd) or resolved.is_relative_to(home)):
		raise ValueError(f"output_path must be inside cwd ({cwd}) or home ({home}); got {resolved}")
	return resolved


def _is_option_position(position: dict[str, Any]) -> bool:
	contract_type = position.get("contract_type")
	if not contract_type and isinstance(position.get("product"), dict):
		contract_type = position["product"].get("contract_type")
	if contract_type in {"call_options", "put_options"}:
		return True
	symbol = position.get("product_symbol") or ""
	return isinstance(symbol, str) and (symbol.startswith("C-") or symbol.startswith("P-"))


def _patch_short_option_pnl(result: dict[str, Any]) -> dict[str, Any]:
	positions = result.get("result")
	if not isinstance(positions, list):
		return result
	for position in positions:
		if not isinstance(position, dict) or not _is_option_position(position):
			continue
		try:
			size = int(position.get("size"))
		except (TypeError, ValueError):
			continue
		if size >= 0:
			continue
		try:
			entry = float(position["entry_price"])
			mark = float(position["mark_price"])
		except (KeyError, TypeError, ValueError):
			continue
		contract_value_raw = position.get("contract_value")
		if contract_value_raw is None and isinstance(position.get("product"), dict):
			contract_value_raw = position["product"].get("contract_value")
		try:
			contract_value = float(contract_value_raw)
		except (TypeError, ValueError):
			continue
		position["unrealized_pnl"] = str((mark - entry) * size * contract_value)
	return result


def _annotate_default_window(result: dict[str, Any], start_time_us: int | None) -> dict[str, Any]:
	if start_time_us is None:
		result["notice"] = RECENT_WINDOW_NOTICE
	return result


class _FileJsonlLogger:
	def __init__(self, file_path: Path):
		self.file_path = file_path
		self.file_path.parent.mkdir(parents=True, exist_ok=True)
		if not self.file_path.exists():
			self.file_path.touch()
			try:
				os.chmod(self.file_path, 0o600)
			except PermissionError:
				pass
		self._lock = threading.Lock()

	def write(self, payload: dict[str, Any]) -> None:
		line = json.dumps(payload, ensure_ascii=True)
		with self._lock:
			with self.file_path.open("a", encoding="utf-8") as fp:
				fp.write(line + "\n")


class DeltaSRP:
	"""Single-file Delta Exchange India helper for algo-cloud usage."""

	def __init__(self, config: DeltaConfig | None = None):
		self.config = config or load_config_from_env()
		self.config.validate()

		self.session = requests.Session()
		self.timeout = (self.config.timeout_connect, self.config.timeout_read)
		self._user_id_cache: int | None = None
		self._tick_cache: dict[int | str, Decimal] = {}
		self._products_cache_loaded = False

		for symbol, details in PRODUCT_ID_DETAILS.items():
			tick_raw = details.get("tick_size")
			pid = details.get("product_id")
			if tick_raw is not None and pid is not None:
				self._tick_cache[int(pid)] = Decimal(str(tick_raw))
				self._tick_cache[symbol] = Decimal(str(tick_raw))

		self.debug_logger: _FileJsonlLogger | None = None
		if self.config.debug:
			stamp = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
			debug_path = Path(os.getenv("DELTA_DEBUG_FILE", f"~/.delta-srp/logs/debug-{stamp}-{os.getpid()}.log")).expanduser()
			self.debug_logger = _FileJsonlLogger(debug_path)

		self.audit_logger: _FileJsonlLogger | None = None
		if self.config.mode == "trade" and self.config.audit:
			stamp = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
			audit_path = Path(os.getenv("DELTA_AUDIT_FILE", f"~/.delta-srp/audit/audit-{stamp}-{os.getpid()}.log")).expanduser()
			self.audit_logger = _FileJsonlLogger(audit_path)

	@classmethod
	def from_env(cls) -> "DeltaSRP":
		return cls(load_config_from_env())

	def close(self) -> None:
		self.session.close()

	def _log_debug(self, payload: dict[str, Any]) -> None:
		if not self.debug_logger:
			return
		safe = dict(payload)
		headers = dict(safe.get("headers", {}))
		for key in ["api-key", "signature", "timestamp"]:
			if key in headers:
				headers[key] = "***"
		safe["headers"] = headers
		self.debug_logger.write(safe)

	def _audit(self, tool: str, params: dict[str, Any], result: Any | None = None, error: str | None = None, dry_run: bool = False) -> None:
		if not self.audit_logger:
			return
		redacted = {k: v for k, v in params.items() if k.lower() not in {"api_key", "api_secret", "signature"}}
		rec: dict[str, Any] = {
			"ts": dt.datetime.utcnow().isoformat() + "Z",
			"env": self.config.env,
			"tool": tool,
			"dry_run": dry_run,
			"params": redacted,
		}
		if error is not None:
			rec["error"] = error
		else:
			rec["result"] = self._summarize_result(result)
		self.audit_logger.write(rec)

	@staticmethod
	def _summarize_result(result: Any) -> Any:
		if isinstance(result, dict):
			root = result.get("result", result)
			if isinstance(root, dict):
				keys = ["id", "state", "client_order_id", "product_id", "product_symbol"]
				return {k: root.get(k) for k in keys if k in root}
			if isinstance(root, list):
				out: list[dict[str, Any]] = []
				for item in root[:10]:
					if isinstance(item, dict):
						row = {}
						for key in ["id", "state", "client_order_id", "product_id", "product_symbol"]:
							if key in item:
								row[key] = item.get(key)
						if row:
							out.append(row)
				return {"count": len(root), "sample": out}
		return result

	@staticmethod
	def _ensure_path(path: str) -> str:
		if not path.startswith("/"):
			path = "/" + path
		if path.startswith("/v2/"):
			return path[3:]
		if path == "/v2":
			return "/"
		return path

	def _signed_headers(self, method: str, path: str, params: dict[str, Any] | None, payload: dict[str, Any] | list[Any] | None) -> dict[str, str]:
		if not self.config.has_credentials:
			raise ValueError("API credentials missing. Set DELTA_API_KEY and DELTA_API_SECRET.")
		ts = str(int(time.time()))
		query = _query_string(params)
		body = _body_string(payload)
		sign_path = "/v2" + path
		signature = sign_request(self.config.api_secret or "", method, ts, sign_path, query, body)
		return {
			"Content-Type": "application/json",
			"api-key": self.config.api_key or "",
			"timestamp": ts,
			"signature": signature,
			"User-Agent": "DeltaSRP/1.0",
		}

	@staticmethod
	def _extract_error(status: int, body: Any) -> DeltaAPIError:
		if isinstance(body, dict):
			err = body.get("error")
			if isinstance(err, dict):
				return DeltaAPIError(
					code=str(err.get("code") or err.get("error_code") or "DeltaError"),
					message=str(err.get("message") or err.get("error") or "Delta API error"),
					status=status,
					context=err,
				)
			if body.get("success") is False:
				return DeltaAPIError(
					code=str(body.get("code") or "DeltaError"),
					message=str(body.get("message") or body.get("error") or "Delta API error"),
					status=status,
					context=body,
				)
		return DeltaAPIError(code="HTTPError", message=f"HTTP {status}", status=status, context={"body": body})

	def _request(
		self,
		method: str,
		path: str,
		*,
		params: dict[str, Any] | None = None,
		payload: dict[str, Any] | list[Any] | None = None,
		auth: bool = False,
		allow_retry: bool = True,
	) -> Any:
		method = method.upper().strip()
		if method not in {"GET", "POST", "PUT", "DELETE"}:
			raise ValueError(f"Unsupported method: {method}")
		path = self._ensure_path(path)
		url = self.config.base_url + path
		headers = {"Content-Type": "application/json", "User-Agent": "DeltaSRP/1.0"}
		if auth:
			headers = self._signed_headers(method, path, params, payload)

		max_attempts = 3 if (method == "GET" and allow_retry) else 1
		attempt = 0
		while True:
			attempt += 1
			body = _body_string(payload)
			self._log_debug(
				{
					"ts": dt.datetime.utcnow().isoformat() + "Z",
					"event": "request",
					"method": method,
					"url": url,
					"params": params,
					"body": body[:50000],
					"headers": headers,
				}
			)
			try:
				response = self.session.request(
					method=method,
					url=url,
					params=params,
					data=body if payload is not None else None,
					headers=headers,
					timeout=self.timeout,
				)
			except requests.RequestException as exc:
				self._log_debug(
					{
						"ts": dt.datetime.utcnow().isoformat() + "Z",
						"event": "network_error",
						"method": method,
						"url": url,
						"attempt": attempt,
						"error": str(exc),
					}
				)
				if attempt >= max_attempts:
					raise
				time.sleep(0.5 * (2 ** (attempt - 1)))
				continue

			raw_text = response.text
			parsed: Any
			try:
				parsed = response.json()
			except ValueError:
				parsed = {"raw": raw_text}

			self._log_debug(
				{
					"ts": dt.datetime.utcnow().isoformat() + "Z",
					"event": "response",
					"method": method,
					"url": url,
					"status": response.status_code,
					"attempt": attempt,
					"body": raw_text[:50000],
				}
			)

			if response.status_code == 429 and attempt < max_attempts:
				reset_ms = response.headers.get("X-RATE-LIMIT-RESET")
				wait_secs = 1.0
				if reset_ms:
					try:
						wait_secs = min(float(reset_ms) / 1000.0, 5.0)
					except ValueError:
						wait_secs = 1.0
				time.sleep(wait_secs)
				continue

			if 500 <= response.status_code <= 599 and method == "GET" and attempt < max_attempts:
				time.sleep(0.5 * (2 ** (attempt - 1)))
				continue

			if response.status_code >= 400:
				raise self._extract_error(response.status_code, parsed)

			if isinstance(parsed, dict) and parsed.get("success") is False:
				raise self._extract_error(response.status_code, parsed)

			return parsed

	def _ensure_trade_mode(self) -> None:
		if self.config.mode != "trade":
			raise PermissionError("Mutation blocked: mode is read. Set DELTA_MODE=trade to enable trading actions.")

	@staticmethod
	def _require_one(left: Any, right: Any, left_name: str, right_name: str) -> None:
		if bool(left is None) == bool(right is None):
			raise ValueError(f"Pass exactly one of {left_name} or {right_name}")

	@staticmethod
	def _validate_order(order_type: str | None, limit_price: str | None, size: int | None) -> None:
		if size is not None and size <= 0:
			raise ValueError("size must be positive")
		if order_type not in VALID_ORDER_TYPES:
			raise ValueError(f"order_type must be one of {sorted(VALID_ORDER_TYPES)}")
		if order_type == "limit_order" and limit_price is None:
			raise ValueError("limit_price is required for limit_order")
		if order_type == "market_order" and limit_price is not None:
			raise ValueError("market_order must not include limit_price")

	@staticmethod
	def _round_to_tick(price: str, tick: Decimal) -> tuple[str, bool]:
		try:
			value = Decimal(price)
		except (InvalidOperation, ValueError, TypeError):
			return price, False
		if tick <= 0:
			return price, False
		steps = (value / tick).to_integral_value(rounding=ROUND_HALF_UP)
		snapped = (steps * tick).normalize()
		normalized = f"{snapped:f}"
		return normalized, normalized != price

	def _cache_product(self, product: dict[str, Any]) -> None:
		tick_raw = product.get("tick_size")
		if tick_raw is None:
			return
		try:
			tick = Decimal(str(tick_raw))
		except (InvalidOperation, ValueError):
			return
		pid = product.get("id")
		symbol = product.get("symbol")
		if pid is not None:
			self._tick_cache[int(pid)] = tick
		if symbol:
			self._tick_cache[str(symbol)] = tick

	def _resolve_tick(self, product_id: int | None, product_symbol: str | None) -> Decimal | None:
		key: int | str | None = product_symbol if product_symbol is not None else product_id
		if key is not None and key in self._tick_cache:
			return self._tick_cache[key]
		try:
			if product_symbol:
				product = self.get_product(product_symbol)
				inner = product.get("result", product) if isinstance(product, dict) else {}
				if isinstance(inner, dict):
					self._cache_product(inner)
			elif not self._products_cache_loaded:
				products = self.list_products(page_size=500)
				inner = products.get("result", []) if isinstance(products, dict) else []
				if isinstance(inner, list):
					for product in inner:
						if isinstance(product, dict):
							self._cache_product(product)
				self._products_cache_loaded = True
		except Exception:
			return None
		if key is None:
			return None
		return self._tick_cache.get(key)

	def _normalize_prices(
		self,
		product_id: int | None,
		product_symbol: str | None,
		fields: dict[str, str | None],
	) -> list[dict[str, str]]:
		if not any(v is not None for v in fields.values()):
			return []
		tick = self._resolve_tick(product_id, product_symbol)
		if tick is None:
			return []
		adjustments: list[dict[str, str]] = []
		for field, value in fields.items():
			if value is None:
				continue
			snapped, changed = self._round_to_tick(value, tick)
			fields[field] = snapped
			if changed:
				adjustments.append({"field": field, "sent": value, "normalized": snapped})
		return adjustments

	@staticmethod
	def _flag_partial(result: dict[str, Any], sent_orders: list[dict[str, Any]]) -> dict[str, Any]:
		if result.get("dry_run"):
			return result
		returned = result.get("result")
		if not isinstance(returned, list):
			return result
		if len(returned) >= len(sent_orders):
			return result
		returned_ids = {x.get("id") for x in returned if isinstance(x, dict)}
		returned_coids = {x.get("client_order_id") for x in returned if isinstance(x, dict)}

		dropped_ids = [
			o["id"]
			for o in sent_orders
			if isinstance(o, dict) and o.get("id") is not None and o["id"] not in returned_ids
		]
		dropped_coids = [
			o["client_order_id"]
			for o in sent_orders
			if isinstance(o, dict)
			and o.get("client_order_id") is not None
			and o["client_order_id"] not in returned_coids
		]
		partial: dict[str, Any] = {
			"requested": len(sent_orders),
			"succeeded": len(returned),
			"dropped": len(sent_orders) - len(returned),
		}
		if dropped_ids:
			partial["dropped_ids"] = dropped_ids
		if dropped_coids:
			partial["dropped_client_order_ids"] = dropped_coids
		result["partial_failure"] = partial
		return result

	def _user_id(self) -> int:
		if self._user_id_cache is not None:
			return self._user_id_cache
		profile = self.get_profile()
		inner = profile.get("result", profile) if isinstance(profile, dict) else {}
		if not isinstance(inner, dict):
			raise ValueError("Unable to parse profile response")
		uid = inner.get("id") or inner.get("user_id")
		if uid is None:
			raise ValueError("Unable to resolve user_id from /profile")
		self._user_id_cache = int(uid)
		return self._user_id_cache

	def _with_adjustments(self, result: dict[str, Any], adjustments: list[dict[str, str]]) -> dict[str, Any]:
		if not adjustments:
			return result
		key = "adjustments" if result.get("dry_run") else "price_adjustments"
		result[key] = adjustments
		return result

	def _mutate(
		self,
		*,
		tool: str,
		method: str,
		path: str,
		payload: dict[str, Any],
		dry_run: bool,
	) -> dict[str, Any]:
		self._ensure_trade_mode()
		clean_payload = _clean(payload)
		if dry_run:
			out = {
				"dry_run": True,
				"method": method,
				"path": "/v2" + self._ensure_path(path),
				"payload": clean_payload,
			}
			self._audit(tool, clean_payload, result=out, dry_run=True)
			return out
		try:
			result = self._request(method, path, payload=clean_payload, auth=True, allow_retry=False)
			if not isinstance(result, dict):
				result = {"result": result}
			self._audit(tool, clean_payload, result=result)
			return result
		except Exception as exc:
			self._audit(tool, clean_payload, error=str(exc))
			raise

	# Market data methods
	def list_products(
		self,
		contract_types: list[str] | None = None,
		states: list[str] | None = None,
		expiry: str | None = None,
		page_size: int = 100,
		after: str | None = None,
	) -> dict[str, Any]:
		return self._request(
			"GET",
			"/products",
			params={
				"contract_types": _csv(contract_types),
				"states": _csv(states),
				"expiry": expiry,
				"page_size": page_size,
				"after": after,
			},
			auth=False,
		)

	def get_product(self, symbol: str) -> dict[str, Any]:
		return self._request("GET", f"/products/{symbol}")

	def get_ticker(self, symbol: str) -> dict[str, Any]:
		return self._request("GET", f"/tickers/{symbol}")

	def list_tickers(
		self,
		contract_types: list[str] | None = None,
		underlying_asset_symbols: list[str] | None = None,
	) -> dict[str, Any]:
		return self._request(
			"GET",
			"/tickers",
			params={
				"contract_types": _csv(contract_types),
				"underlying_asset_symbols": _csv(underlying_asset_symbols),
			},
		)

	def get_orderbook(self, symbol: str, depth: int | None = None) -> dict[str, Any]:
		return self._request("GET", f"/l2orderbook/{symbol}", params={"depth": depth})

	def get_recent_trades(self, symbol: str) -> dict[str, Any]:
		return self._request("GET", f"/trades/{symbol}")

	def get_candles(self, symbol: str, resolution: str, start: int, end: int) -> dict[str, Any]:
		if resolution not in RESOLUTIONS:
			raise ValueError(f"Invalid resolution '{resolution}'. Allowed: {sorted(RESOLUTIONS)}")
		return self._request(
			"GET",
			"/history/candles",
			params={"symbol": symbol, "resolution": resolution, "start": start, "end": end},
		)

	def get_settlement_prices(
		self,
		contract_types: list[str] | None = None,
		page_size: int = 100,
		after: str | None = None,
	) -> dict[str, Any]:
		return self._request(
			"GET",
			"/products",
			params={
				"contract_types": _csv(contract_types),
				"states": "expired",
				"page_size": page_size,
				"after": after,
			},
		)

	def get_funding_history(self, symbol: str, resolution: str, start: int, end: int) -> dict[str, Any]:
		return self.get_candles(f"FUNDING:{symbol}", resolution, start, end)

	def get_mark_price_history(self, symbol: str, resolution: str, start: int, end: int) -> dict[str, Any]:
		return self.get_candles(f"MARK:{symbol}", resolution, start, end)

	def get_oi_history(self, symbol: str, resolution: str, start: int, end: int) -> dict[str, Any]:
		return self.get_candles(f"OI:{symbol}", resolution, start, end)

	def get_options_chain(self, underlying: str, expiry_date: str) -> dict[str, Any]:
		return self._request(
			"GET",
			"/tickers",
			params={
				"contract_types": "call_options,put_options",
				"underlying_asset_symbols": underlying,
				"expiry_date": expiry_date,
			},
		)

	def get_indices(self) -> dict[str, Any]:
		return self._request("GET", "/indices")

	def get_reference_data(self) -> dict[str, Any]:
		assets = self._request("GET", "/assets")
		indices = self.get_indices()
		return {"assets": assets.get("result", assets), "indices": indices.get("result", indices)}

	# Account methods
	def get_positions(
		self,
		product_id: int | None = None,
		underlying_asset_symbol: str | None = None,
	) -> dict[str, Any]:
		self._require_one(product_id, underlying_asset_symbol, "product_id", "underlying_asset_symbol")
		return self._request(
			"GET",
			"/positions",
			params={"product_id": product_id, "underlying_asset_symbol": underlying_asset_symbol},
			auth=True,
		)

	def get_margined_positions(
		self,
		product_ids: list[int] | None = None,
		contract_types: list[str] | None = None,
	) -> dict[str, Any]:
		result = self._request(
			"GET",
			"/positions/margined",
			params={"product_ids": _csv_ints(product_ids), "contract_types": _csv(contract_types)},
			auth=True,
		)
		if isinstance(result, dict):
			return _patch_short_option_pnl(result)
		return {"result": result}

	def get_wallet_balances(self) -> dict[str, Any]:
		return self._request("GET", "/wallet/balances", auth=True)

	def get_wallet_transactions(
		self,
		asset_ids: list[int] | None = None,
		transaction_types: list[str] | None = None,
		start_time_us: int | None = None,
		end_time_us: int | None = None,
		page_size: int = 50,
		after: str | None = None,
		before: str | None = None,
	) -> dict[str, Any]:
		result = self._request(
			"GET",
			"/wallet/transactions",
			params={
				"asset_ids": _csv_ints(asset_ids),
				"transaction_types": _csv(transaction_types),
				"start_time": start_time_us,
				"end_time": end_time_us,
				"page_size": page_size,
				"after": after,
				"before": before,
			},
			auth=True,
		)
		if isinstance(result, dict):
			return _annotate_default_window(result, start_time_us)
		return {"result": result}

	def get_fills(
		self,
		product_ids: list[int] | None = None,
		contract_types: list[str] | None = None,
		start_time_us: int | None = None,
		end_time_us: int | None = None,
		page_size: int = 50,
		after: str | None = None,
	) -> dict[str, Any]:
		result = self._request(
			"GET",
			"/fills",
			params={
				"product_ids": _csv_ints(product_ids),
				"contract_types": _csv(contract_types),
				"start_time": start_time_us,
				"end_time": end_time_us,
				"page_size": page_size,
				"after": after,
			},
			auth=True,
		)
		if isinstance(result, dict):
			return _annotate_default_window(result, start_time_us)
		return {"result": result}

	def get_open_orders(
		self,
		product_ids: list[int] | None = None,
		states: list[str] | None = None,
		contract_types: list[str] | None = None,
		page_size: int = 50,
		after: str | None = None,
	) -> dict[str, Any]:
		return self._request(
			"GET",
			"/orders",
			params={
				"product_ids": _csv_ints(product_ids),
				"states": _csv(states),
				"contract_types": _csv(contract_types),
				"page_size": page_size,
				"after": after,
			},
			auth=True,
		)

	def get_order_history(
		self,
		product_ids: list[int] | None = None,
		contract_types: list[str] | None = None,
		order_types: list[str] | None = None,
		start_time_us: int | None = None,
		end_time_us: int | None = None,
		page_size: int = 50,
		after: str | None = None,
	) -> dict[str, Any]:
		result = self._request(
			"GET",
			"/orders/history",
			params={
				"product_ids": _csv_ints(product_ids),
				"contract_types": _csv(contract_types),
				"order_types": _csv(order_types),
				"start_time": start_time_us,
				"end_time": end_time_us,
				"page_size": page_size,
				"after": after,
			},
			auth=True,
		)
		if isinstance(result, dict):
			return _annotate_default_window(result, start_time_us)
		return {"result": result}

	def get_order_by_id(self, order_id: int | None = None, client_order_id: str | None = None) -> dict[str, Any]:
		self._require_one(order_id, client_order_id, "order_id", "client_order_id")
		if order_id is not None:
			return self._request("GET", f"/orders/{order_id}", auth=True)
		return self._request("GET", "/orders/client_order_id", params={"client_order_id": client_order_id}, auth=True)

	def get_product_leverage(self, product_id: int) -> dict[str, Any]:
		return self._request("GET", f"/products/{product_id}/orders/leverage", auth=True)

	def get_trading_stats(self) -> dict[str, Any]:
		return self._request("GET", "/trading_stats", auth=True)

	def get_trading_preferences(self) -> dict[str, Any]:
		return self._request("GET", "/profile/trading_preference", auth=True)

	def get_profile(self) -> dict[str, Any]:
		return self._request("GET", "/profile", auth=True)

	def bulk_fills_export(
		self,
		output_path: str,
		start_time_us: int | None = None,
		end_time_us: int | None = None,
		product_ids: list[int] | None = None,
		contract_types: list[str] | None = None,
		page_size: int = 200,
	) -> dict[str, Any]:
		target = _safe_export_path(output_path)
		target.parent.mkdir(parents=True, exist_ok=True)

		cursor: str | None = None
		rows: list[dict[str, Any]] = []
		while True:
			data = self.get_fills(
				product_ids=product_ids,
				contract_types=contract_types,
				start_time_us=start_time_us,
				end_time_us=end_time_us,
				page_size=page_size,
				after=cursor,
			)
			batch = data.get("result", []) if isinstance(data, dict) else []
			if isinstance(batch, list):
				rows.extend(x for x in batch if isinstance(x, dict))
			meta = data.get("meta", {}) if isinstance(data, dict) else {}
			cursor = meta.get("after") if isinstance(meta, dict) else None
			if not cursor:
				break

		if rows:
			keys: list[str] = sorted({k for row in rows for k in row.keys()})
			with target.open("w", encoding="utf-8", newline="") as fp:
				writer = csv.DictWriter(fp, fieldnames=keys)
				writer.writeheader()
				for row in rows:
					writer.writerow(row)
		else:
			with target.open("w", encoding="utf-8") as fp:
				fp.write("")

		out = {
			"path": str(target),
			"row_count": len(rows),
			"size_bytes": target.stat().st_size,
		}
		return _annotate_default_window(out, start_time_us)

	# Trading methods
	def place_order(
		self,
		*,
		size: int,
		side: str,
		order_type: str,
		product_id: int | None = None,
		product_symbol: str | None = None,
		limit_price: str | None = None,
		stop_order_type: str | None = None,
		stop_price: str | None = None,
		trail_amount: str | None = None,
		stop_trigger_method: str | None = None,
		time_in_force: str | None = None,
		post_only: bool | None = None,
		reduce_only: bool | None = None,
		client_order_id: str | None = None,
		bracket_stop_loss_price: str | None = None,
		bracket_stop_loss_limit_price: str | None = None,
		bracket_take_profit_price: str | None = None,
		bracket_take_profit_limit_price: str | None = None,
		bracket_trail_amount: str | None = None,
		bracket_stop_trigger_method: str | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		self._require_one(product_id, product_symbol, "product_id", "product_symbol")
		self._validate_order(order_type, limit_price, size)
		if stop_trigger_method and stop_trigger_method not in STOP_TRIGGER_METHODS:
			raise ValueError(f"Invalid stop_trigger_method: {stop_trigger_method}")
		if bracket_stop_trigger_method and bracket_stop_trigger_method not in STOP_TRIGGER_METHODS:
			raise ValueError(f"Invalid bracket_stop_trigger_method: {bracket_stop_trigger_method}")
		if client_order_id and len(client_order_id) > 32:
			raise ValueError("client_order_id max length is 32")

		prices = {
			"limit_price": limit_price,
			"stop_price": stop_price,
			"trail_amount": trail_amount,
			"bracket_stop_loss_price": bracket_stop_loss_price,
			"bracket_stop_loss_limit_price": bracket_stop_loss_limit_price,
			"bracket_take_profit_price": bracket_take_profit_price,
			"bracket_take_profit_limit_price": bracket_take_profit_limit_price,
			"bracket_trail_amount": bracket_trail_amount,
		}
		adjustments = self._normalize_prices(product_id, product_symbol, prices)

		payload = {
			"size": int(size),
			"side": side,
			"order_type": order_type,
			"product_id": product_id,
			"product_symbol": product_symbol,
			"limit_price": prices["limit_price"],
			"stop_order_type": stop_order_type,
			"stop_price": prices["stop_price"],
			"trail_amount": prices["trail_amount"],
			"stop_trigger_method": stop_trigger_method,
			"time_in_force": time_in_force,
			"post_only": _bs(post_only),
			"reduce_only": _bs(reduce_only),
			"client_order_id": client_order_id,
			"bracket_stop_loss_price": prices["bracket_stop_loss_price"],
			"bracket_stop_loss_limit_price": prices["bracket_stop_loss_limit_price"],
			"bracket_take_profit_price": prices["bracket_take_profit_price"],
			"bracket_take_profit_limit_price": prices["bracket_take_profit_limit_price"],
			"bracket_trail_amount": prices["bracket_trail_amount"],
			"bracket_stop_trigger_method": bracket_stop_trigger_method,
		}
		result = self._mutate(tool="place_order", method="POST", path="/orders", payload=payload, dry_run=dry_run)
		return self._with_adjustments(result, adjustments)

	def place_market_order(
		self,
		*,
		size: int,
		side: str,
		product_id: int | None = None,
		product_symbol: str | None = None,
		stop_order_type: str | None = None,
		stop_price: str | None = None,
		trail_amount: str | None = None,
		stop_trigger_method: str | None = None,
		reduce_only: bool | None = None,
		client_order_id: str | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		"""Convenience wrapper for market_order placement."""
		return self.place_order(
			size=size,
			side=side,
			order_type="market_order",
			product_id=product_id,
			product_symbol=product_symbol,
			stop_order_type=stop_order_type,
			stop_price=stop_price,
			trail_amount=trail_amount,
			stop_trigger_method=stop_trigger_method,
			reduce_only=reduce_only,
			client_order_id=client_order_id,
			dry_run=dry_run,
		)

	def edit_order(
		self,
		*,
		order_id: int,
		product_id: int | None = None,
		product_symbol: str | None = None,
		size: int | None = None,
		limit_price: str | None = None,
		stop_price: str | None = None,
		trail_amount: str | None = None,
		stop_trigger_method: str | None = None,
		post_only: bool | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		self._require_one(product_id, product_symbol, "product_id", "product_symbol")
		prices = {
			"limit_price": limit_price,
			"stop_price": stop_price,
			"trail_amount": trail_amount,
		}
		adjustments = self._normalize_prices(product_id, product_symbol, prices)
		payload = {
			"id": order_id,
			"product_id": product_id,
			"product_symbol": product_symbol,
			"size": size,
			"limit_price": prices["limit_price"],
			"stop_price": prices["stop_price"],
			"trail_amount": prices["trail_amount"],
			"stop_trigger_method": stop_trigger_method,
			"post_only": _bs(post_only),
		}
		result = self._mutate(tool="edit_order", method="PUT", path="/orders", payload=payload, dry_run=dry_run)
		return self._with_adjustments(result, adjustments)

	def cancel_order(
		self,
		*,
		product_id: int,
		order_id: int | None = None,
		client_order_id: str | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		self._require_one(order_id, client_order_id, "order_id", "client_order_id")
		payload = {
			"product_id": product_id,
			"id": order_id,
			"client_order_id": client_order_id,
		}
		return self._mutate(tool="cancel_order", method="DELETE", path="/orders", payload=payload, dry_run=dry_run)

	def place_batch_orders(
		self,
		*,
		orders: list[dict[str, Any]],
		product_id: int | None = None,
		product_symbol: str | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		self._require_one(product_id, product_symbol, "product_id", "product_symbol")
		if not orders:
			raise ValueError("orders cannot be empty")
		if len(orders) > MAX_BATCH:
			raise ValueError(f"Batch size exceeds max {MAX_BATCH}")

		seen_coids: set[str] = set()
		normalized: list[dict[str, Any]] = []
		for order in orders:
			o = dict(order)
			self._validate_order(o.get("order_type"), o.get("limit_price"), o.get("size"))
			coid = o.get("client_order_id")
			if coid:
				if len(coid) > 32:
					raise ValueError("client_order_id max length is 32")
				if coid in seen_coids:
					raise ValueError(f"Duplicate client_order_id in batch: {coid}")
				seen_coids.add(coid)

			prices = {
				"limit_price": o.get("limit_price"),
				"stop_price": o.get("stop_price"),
				"trail_amount": o.get("trail_amount"),
			}
			self._normalize_prices(product_id, product_symbol, prices)
			o["limit_price"] = prices["limit_price"]
			o["stop_price"] = prices["stop_price"]
			o["trail_amount"] = prices["trail_amount"]
			for flag in ORDER_LEVEL_BOOL_FIELDS:
				if flag in o:
					o[flag] = _bs(o.get(flag))
			normalized.append(_clean(o))

		payload = {
			"product_id": product_id,
			"product_symbol": product_symbol,
			"orders": normalized,
		}
		result = self._mutate(tool="place_batch_orders", method="POST", path="/orders/batch", payload=payload, dry_run=dry_run)
		return self._flag_partial(result, normalized)

	def place_batch_market_orders(
		self,
		*,
		orders: list[dict[str, Any]],
		product_id: int | None = None,
		product_symbol: str | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		"""Place many market orders; each item must include at least size and side."""
		market_orders: list[dict[str, Any]] = []
		for order in orders:
			row = dict(order)
			row["order_type"] = "market_order"
			# Guard against accidental limit caps for market orders.
			row.pop("limit_price", None)
			market_orders.append(row)
		return self.place_batch_orders(
			orders=market_orders,
			product_id=product_id,
			product_symbol=product_symbol,
			dry_run=dry_run,
		)

	def edit_batch_orders(
		self,
		*,
		orders: list[dict[str, Any]],
		product_id: int | None = None,
		product_symbol: str | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		self._require_one(product_id, product_symbol, "product_id", "product_symbol")
		if not orders:
			raise ValueError("orders cannot be empty")
		if len(orders) > MAX_BATCH:
			raise ValueError(f"Batch size exceeds max {MAX_BATCH}")

		normalized: list[dict[str, Any]] = []
		for order in orders:
			o = dict(order)
			if "id" not in o:
				raise ValueError("Each edit order must include id")
			prices = {
				"limit_price": o.get("limit_price"),
				"stop_price": o.get("stop_price"),
				"trail_amount": o.get("trail_amount"),
			}
			self._normalize_prices(product_id, product_symbol, prices)
			o["limit_price"] = prices["limit_price"]
			o["stop_price"] = prices["stop_price"]
			o["trail_amount"] = prices["trail_amount"]
			for flag in ORDER_LEVEL_BOOL_FIELDS:
				if flag in o:
					o[flag] = _bs(o.get(flag))
			normalized.append(_clean(o))

		payload = {
			"product_id": product_id,
			"product_symbol": product_symbol,
			"orders": normalized,
		}
		result = self._mutate(tool="edit_batch_orders", method="PUT", path="/orders/batch", payload=payload, dry_run=dry_run)
		return self._flag_partial(result, normalized)

	def cancel_batch_orders(
		self,
		*,
		orders: list[dict[str, Any]],
		product_id: int | None = None,
		product_symbol: str | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		self._require_one(product_id, product_symbol, "product_id", "product_symbol")
		if not orders:
			raise ValueError("orders cannot be empty")
		if len(orders) > MAX_BATCH:
			raise ValueError(f"Batch size exceeds max {MAX_BATCH}")
		for order in orders:
			has_id = order.get("id") is not None
			has_coid = bool(order.get("client_order_id"))
			if has_id == has_coid:
				raise ValueError("Each cancel item must include exactly one of id or client_order_id")

		payload = {
			"product_id": product_id,
			"product_symbol": product_symbol,
			"orders": orders,
		}
		result = self._mutate(tool="cancel_batch_orders", method="DELETE", path="/orders/batch", payload=payload, dry_run=dry_run)
		return self._flag_partial(result, orders)

	def place_bracket_order(
		self,
		*,
		product_id: int | None = None,
		product_symbol: str | None = None,
		stop_loss_order: dict[str, Any] | None = None,
		take_profit_order: dict[str, Any] | None = None,
		bracket_stop_trigger_method: str | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		self._require_one(product_id, product_symbol, "product_id", "product_symbol")
		if stop_loss_order is None and take_profit_order is None:
			raise ValueError("At least one of stop_loss_order or take_profit_order is required")

		payload = {
			"product_id": product_id,
			"product_symbol": product_symbol,
			"stop_loss_order": stop_loss_order,
			"take_profit_order": take_profit_order,
			"bracket_stop_trigger_method": bracket_stop_trigger_method,
		}
		return self._mutate(
			tool="place_bracket_order",
			method="POST",
			path="/positions/bracket_orders",
			payload=payload,
			dry_run=dry_run,
		)

	def edit_bracket_order(
		self,
		*,
		order_id: int,
		product_id: int,
		bracket_stop_loss_price: str | None = None,
		bracket_stop_loss_limit_price: str | None = None,
		bracket_take_profit_price: str | None = None,
		bracket_take_profit_limit_price: str | None = None,
		bracket_trail_amount: str | None = None,
		bracket_stop_trigger_method: str | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		prices = {
			"bracket_stop_loss_price": bracket_stop_loss_price,
			"bracket_stop_loss_limit_price": bracket_stop_loss_limit_price,
			"bracket_take_profit_price": bracket_take_profit_price,
			"bracket_take_profit_limit_price": bracket_take_profit_limit_price,
			"bracket_trail_amount": bracket_trail_amount,
		}
		adjustments = self._normalize_prices(product_id, None, prices)
		payload = {
			"id": order_id,
			"product_id": product_id,
			"bracket_stop_loss_price": prices["bracket_stop_loss_price"],
			"bracket_stop_loss_limit_price": prices["bracket_stop_loss_limit_price"],
			"bracket_take_profit_price": prices["bracket_take_profit_price"],
			"bracket_take_profit_limit_price": prices["bracket_take_profit_limit_price"],
			"bracket_trail_amount": prices["bracket_trail_amount"],
			"bracket_stop_trigger_method": bracket_stop_trigger_method,
		}
		result = self._mutate(tool="edit_bracket_order", method="PUT", path="/orders", payload=payload, dry_run=dry_run)
		return self._with_adjustments(result, adjustments)

	def cancel_all_orders(
		self,
		*,
		product_id: int | None = None,
		contract_types: list[str] | None = None,
		cancel_limit_orders: bool | None = None,
		cancel_stop_orders: bool | None = None,
		cancel_reduce_only_orders: bool | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		if cancel_limit_orders is None and cancel_stop_orders is None and cancel_reduce_only_orders is None:
			cancel_limit_orders = True
			cancel_stop_orders = True
			cancel_reduce_only_orders = True
		payload = {
			"product_id": product_id,
			"contract_types": _csv(contract_types),
			"cancel_limit_orders": _bs(cancel_limit_orders),
			"cancel_stop_orders": _bs(cancel_stop_orders),
			"cancel_reduce_only_orders": _bs(cancel_reduce_only_orders),
		}
		return self._mutate(tool="cancel_all_orders", method="DELETE", path="/orders/all", payload=payload, dry_run=dry_run)

	def set_product_leverage(self, *, product_id: int, leverage: str, dry_run: bool = False) -> dict[str, Any]:
		payload = {"leverage": leverage}
		return self._mutate(
			tool="set_product_leverage",
			method="POST",
			path=f"/products/{product_id}/orders/leverage",
			payload=payload,
			dry_run=dry_run,
		)

	def adjust_position_margin(self, *, product_id: int, delta_margin: str, dry_run: bool = False) -> dict[str, Any]:
		payload = {"product_id": product_id, "delta_margin": delta_margin}
		return self._mutate(
			tool="adjust_position_margin",
			method="POST",
			path="/positions/change_margin",
			payload=payload,
			dry_run=dry_run,
		)

	def close_all_positions(
		self,
		*,
		close_all_portfolio: bool = False,
		close_all_isolated: bool = False,
		dry_run: bool = False,
	) -> dict[str, Any]:
		if not close_all_portfolio and not close_all_isolated:
			raise ValueError("At least one of close_all_portfolio or close_all_isolated must be true")
		payload = {
			"user_id": self._user_id(),
			"close_all_portfolio": close_all_portfolio,
			"close_all_isolated": close_all_isolated,
		}
		return self._mutate(
			tool="close_all_positions",
			method="POST",
			path="/positions/close_all",
			payload=payload,
			dry_run=dry_run,
		)

	def configure_auto_topup(self, *, product_id: int, auto_topup: bool, dry_run: bool = False) -> dict[str, Any]:
		payload = {"product_id": product_id, "auto_topup": auto_topup}
		return self._mutate(
			tool="configure_auto_topup",
			method="POST",
			path="/positions/auto_topup",
			payload=payload,
			dry_run=dry_run,
		)

	@staticmethod
	def load_strategy_config(config_path: str = "config.yaml") -> dict[str, Any]:
		"""Load strategy config from YAML file.

		If PyYAML is unavailable, a JSON-compatible YAML file is still supported
		because YAML is a superset of JSON.
		"""
		raw = Path(config_path).read_text(encoding="utf-8")
		if yaml is not None:
			parsed = yaml.safe_load(raw)
			if not isinstance(parsed, dict):
				raise ValueError("config root must be a mapping/object")
			return parsed
		try:
			parsed = json.loads(raw)
		except json.JSONDecodeError as exc:
			raise ValueError(
				"PyYAML is not installed and config is not JSON-compatible YAML. "
				"Install pyyaml or use JSON-compatible config content."
			) from exc
		if not isinstance(parsed, dict):
			raise ValueError("config root must be a mapping/object")
		return parsed

	@staticmethod
	def _cfg_dict(cfg: dict[str, Any], key: str) -> dict[str, Any]:
		val = cfg.get(key, {})
		if not isinstance(val, dict):
			raise ValueError(f"config.{key} must be an object")
		return val

	@staticmethod
	def _normalize_order_type(value: str | None) -> str:
		ot = (value or "market_order").strip().lower()
		if ot in {"market", "marketorder", "market_order"}:
			return "market_order"
		if ot in {"limit", "limitorder", "limit_order"}:
			return "limit_order"
		raise ValueError("order_type must be market_order or limit_order")

	@staticmethod
	def _normalize_instrument_type(value: str | None) -> str:
		it = (value or "futures").strip().lower()
		if it in {"future", "futures", "perp", "perpetual", "perpetual_futures"}:
			return "futures"
		if it in {"option", "options"}:
			return "options"
		raise ValueError("instrument.type must be futures or options")

	@staticmethod
	def _normalize_side(value: str | None) -> str:
		side = (value or "").strip().lower()
		if side not in {"buy", "sell"}:
			raise ValueError("side must be buy or sell")
		return side

	@staticmethod
	def _build_bracket_legs(risk_cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
		sl_price = risk_cfg.get("stop_loss_price")
		tp_price = risk_cfg.get("take_profit_price")
		sl_limit = risk_cfg.get("stop_loss_limit_price")
		tp_limit = risk_cfg.get("take_profit_limit_price")

		stop_loss_order: dict[str, Any] | None = None
		take_profit_order: dict[str, Any] | None = None

		if sl_price is not None:
			stop_loss_order = {
				"order_type": DeltaSRP._normalize_order_type(str(risk_cfg.get("stop_loss_order_type", "market_order"))),
				"stop_price": str(sl_price),
			}
			if sl_limit is not None:
				stop_loss_order["limit_price"] = str(sl_limit)

		if tp_price is not None:
			take_profit_order = {
				"order_type": DeltaSRP._normalize_order_type(str(risk_cfg.get("take_profit_order_type", "market_order"))),
				"stop_price": str(tp_price),
			}
			if tp_limit is not None:
				take_profit_order["limit_price"] = str(tp_limit)

		return stop_loss_order, take_profit_order

	def _execute_entry_from_order_block(
		self,
		*,
		instrument_cfg: dict[str, Any],
		order_cfg: dict[str, Any],
		dry_run: bool,
	) -> dict[str, Any]:
		order_type = self._normalize_order_type(order_cfg.get("order_type"))
		side = self._normalize_side(order_cfg.get("side"))
		size_raw = order_cfg.get("size")
		if size_raw is None:
			raise ValueError("order.size is required")
		size = int(size_raw)

		product_symbol = instrument_cfg.get("symbol")
		product_id_raw = instrument_cfg.get("product_id")
		product_id = int(product_id_raw) if product_id_raw is not None else None
		self._require_one(product_id, product_symbol, "instrument.product_id", "instrument.symbol")

		limit_price_raw = order_cfg.get("limit_price")
		limit_price = str(limit_price_raw) if limit_price_raw is not None else None

		payload: dict[str, Any] = {
			"size": size,
			"side": side,
			"order_type": order_type,
			"product_id": product_id,
			"product_symbol": product_symbol,
			"limit_price": limit_price,
			"time_in_force": order_cfg.get("time_in_force"),
			"post_only": order_cfg.get("post_only"),
			"reduce_only": order_cfg.get("reduce_only"),
			"client_order_id": order_cfg.get("client_order_id"),
			"dry_run": dry_run,
		}

		if order_type == "market_order":
			payload.pop("limit_price", None)
			entry = self.place_market_order(
				size=payload["size"],
				side=payload["side"],
				product_id=payload["product_id"],
				product_symbol=payload["product_symbol"],
				reduce_only=payload["reduce_only"],
				client_order_id=payload["client_order_id"],
				dry_run=dry_run,
			)
		else:
			entry = self.place_order(**payload)
		return {
			"entry": entry,
			"product_id": product_id,
			"product_symbol": product_symbol,
		}

	def execute_from_config_dict(self, config: dict[str, Any]) -> dict[str, Any]:
		"""Execute config-driven order flow for futures or options (multi-leg)."""
		runtime_cfg = self._cfg_dict(config, "runtime")
		instrument_cfg = self._cfg_dict(config, "instrument")
		order_cfg = self._cfg_dict(config, "order")
		risk_cfg = self._cfg_dict(config, "risk")

		dry_run = bool(runtime_cfg.get("dry_run", True))
		instrument_type = self._normalize_instrument_type(instrument_cfg.get("type"))

		summary: dict[str, Any] = {
			"ok": True,
			"instrument_type": instrument_type,
			"dry_run": dry_run,
			"entries": [],
			"risk_actions": [],
			"notes": [],
		}

		if instrument_type == "futures":
			entry_block = self._execute_entry_from_order_block(
				instrument_cfg=instrument_cfg,
				order_cfg=order_cfg,
				dry_run=dry_run,
			)
			summary["entries"].append(entry_block["entry"])

			stop_loss_order, take_profit_order = self._build_bracket_legs(risk_cfg)
			if stop_loss_order or take_profit_order:
				risk_res = self.place_bracket_order(
					product_id=entry_block["product_id"],
					product_symbol=entry_block["product_symbol"],
					stop_loss_order=stop_loss_order,
					take_profit_order=take_profit_order,
					bracket_stop_trigger_method=risk_cfg.get("stop_trigger_method"),
					dry_run=dry_run,
				)
				summary["risk_actions"].append(risk_res)
			else:
				summary["notes"].append("No TP/SL configured in risk block.")

			return summary

		legs = config.get("option_legs")
		if not isinstance(legs, list) or not legs:
			raise ValueError("instrument.type=options requires non-empty option_legs list")

		for idx, leg in enumerate(legs):
			if not isinstance(leg, dict):
				raise ValueError(f"option_legs[{idx}] must be an object")

			leg_instrument = {
				"symbol": leg.get("product_symbol"),
				"product_id": leg.get("product_id"),
				"type": "options",
			}
			leg_order = {
				"order_type": leg.get("order_type", order_cfg.get("order_type", "market_order")),
				"side": leg.get("side"),
				"size": leg.get("size"),
				"limit_price": leg.get("limit_price"),
				"time_in_force": leg.get("time_in_force", order_cfg.get("time_in_force")),
				"post_only": leg.get("post_only", order_cfg.get("post_only")),
				"reduce_only": leg.get("reduce_only", order_cfg.get("reduce_only")),
				"client_order_id": leg.get("client_order_id"),
			}

			entry_block = self._execute_entry_from_order_block(
				instrument_cfg=leg_instrument,
				order_cfg=leg_order,
				dry_run=dry_run,
			)
			summary["entries"].append(entry_block["entry"])

			leg_risk_cfg = leg.get("risk") if isinstance(leg.get("risk"), dict) else {}
			stop_loss_order, take_profit_order = self._build_bracket_legs(leg_risk_cfg)
			if stop_loss_order or take_profit_order:
				risk_res = self.place_bracket_order(
					product_id=entry_block["product_id"],
					product_symbol=entry_block["product_symbol"],
					stop_loss_order=stop_loss_order,
					take_profit_order=take_profit_order,
					bracket_stop_trigger_method=leg_risk_cfg.get("stop_trigger_method"),
					dry_run=dry_run,
				)
				summary["risk_actions"].append(risk_res)
			else:
				summary["notes"].append(f"No leg-level TP/SL for option_legs[{idx}].")

		if risk_cfg:
			summary["notes"].append(
				"Global risk block is not auto-applied to multi-leg options. Use option_legs[].risk per leg."
			)
		return summary

	def execute_from_config_file(self, config_path: str = "config.yaml") -> dict[str, Any]:
		config = self.load_strategy_config(config_path)
		return self.execute_from_config_dict(config)

	def get_trading_status(self) -> dict[str, Any]:
		return {
			"mode": self.config.mode,
			"audit_log_path": str(self.audit_logger.file_path) if self.audit_logger else None,
			"debug_log_path": str(self.debug_logger.file_path) if self.debug_logger else None,
			"has_credentials": self.config.has_credentials,
		}


# ---------------------------------------------------------------------------
# Four-leg credit-condor primitives
#
# Copy this section into another options-strategy repo (bullish condor, bearish
# condor, iron condor). Strike *placement* is strategy-specific. Execution,
# expiry, MANUAL exact strikes, combined TP/SL, and restart reconcile are not.
#
# Delta batch POST /v2/orders/batch takes ONE product_id. A condor is four
# products — place four sequential POST /v2/orders. Never send a condor as one
# batch. Cancel any unfilled order before retry, timeout, unwind, or shutdown.
# ---------------------------------------------------------------------------

CREDIT_CONDOR_ENTRY = ("PUT_LONG", "CALL_LONG", "PUT_SHORT", "CALL_SHORT")
CREDIT_CONDOR_DISPLAY = ("PUT_LONG", "PUT_SHORT", "CALL_SHORT", "CALL_LONG")
CREDIT_CONDOR_EXIT = ("PUT_SHORT", "CALL_SHORT", "PUT_LONG", "CALL_LONG")
VALID_EXPIRY_MODES = {"FIXED_DATE", "NEAREST", "NEXT_AVAILABLE"}
CLIENT_ORDER_PREFIX = "opt"


def inner_result(payload: Any) -> Any:
	if isinstance(payload, dict):
		return payload.get("result", payload)
	return payload


def nested_get(cfg: dict[str, Any], path: str, default: Any = None) -> Any:
	cur: Any = cfg
	for part in path.split("."):
		if not isinstance(cur, dict) or part not in cur:
			return default
		cur = cur[part]
	return cur


def to_float(value: Any, default: float = 0.0) -> float:
	try:
		if value is None or value == "":
			return default
		return float(value)
	except (TypeError, ValueError):
		return default


def to_int(value: Any, default: int = 0) -> int:
	try:
		if value is None or value == "":
			return default
		return int(float(value))
	except (TypeError, ValueError):
		return default


def map_underlying_to_asset(symbol: str) -> str:
	"""BTCUSD -> BTC for option-chain queries. XAUSD maps to XAUT."""
	meta = PRODUCT_ID_DETAILS.get(symbol.upper())
	if meta:
		return str(meta.get("asset") or "")
	cleaned = symbol.upper().replace("USD", "").replace("USDT", "")
	return cleaned or symbol.upper()


def resolve_underlying_symbol(symbol: str) -> str:
	upper = symbol.upper().strip()
	if upper == "XAUSD":
		return "XAUTUSD"
	return upper


def parse_option_symbol(symbol: str) -> dict[str, Any] | None:
	"""Parse C-BTC-90000-310125 into type, asset, strike, expiry."""
	parts = str(symbol or "").split("-")
	if len(parts) < 4:
		return None
	kind = parts[0].upper()
	if kind not in {"C", "P"}:
		return None
	asset = parts[1].upper()
	expiry_raw = parts[-1]
	strike_raw = "-".join(parts[2:-1])
	try:
		expiry_dt = dt.datetime.strptime(expiry_raw, "%d%m%y")
	except ValueError:
		return None
	return {
		"kind": kind,
		"option_type": "call" if kind == "C" else "put",
		"contract_type": "call_options" if kind == "C" else "put_options",
		"asset": asset,
		"strike": to_float(strike_raw),
		"expiry_raw": expiry_raw,
		"expiry_date": expiry_dt.strftime("%d-%m-%Y"),
		"expiry_dt": expiry_dt,
		"symbol": symbol,
	}


def extract_chain_rows(payload: Any) -> list[dict[str, Any]]:
	result = inner_result(payload)
	if isinstance(result, list):
		return [row for row in result if isinstance(row, dict)]
	if isinstance(result, dict):
		for key in ("tickers", "options", "products"):
			maybe = result.get(key)
			if isinstance(maybe, list):
				return [row for row in maybe if isinstance(row, dict)]
		if result.get("symbol"):
			return [result]
	return []


def fetch_option_chain(client: DeltaSRP, asset: str, expiry_date: str) -> list[dict[str, Any]]:
	"""expiry_date must be Delta format DD-MM-YYYY. asset is BTC not BTCUSD."""
	return extract_chain_rows(client.get_options_chain(asset, expiry_date))


def fetch_option_positions(client: DeltaSRP) -> list[dict[str, Any]]:
	payload = client.get_margined_positions(contract_types=["call_options", "put_options"])
	result = inner_result(payload)
	return [r for r in result if isinstance(r, dict)] if isinstance(result, list) else []


def discover_option_expiries(client: DeltaSRP, asset: str) -> list[dt.datetime]:
	payload = client.list_tickers(
		contract_types=["call_options", "put_options"],
		underlying_asset_symbols=[asset],
	)
	seen: set[str] = set()
	out: list[dt.datetime] = []
	for row in extract_chain_rows(payload):
		parsed = parse_option_symbol(str(row.get("symbol") or ""))
		if not parsed:
			continue
		key = parsed["expiry_raw"]
		if key in seen:
			continue
		seen.add(key)
		out.append(parsed["expiry_dt"])
	out.sort()
	return out


def parse_iso_expiry_date(value: Any) -> tuple[dt.date | None, str]:
	if value is None or value == "":
		return None, "expiry_date is required when expiry mode is FIXED_DATE"
	if isinstance(value, dt.datetime):
		return value.date(), ""
	if isinstance(value, dt.date):
		return value, ""
	text = str(value).strip()
	try:
		return dt.datetime.strptime(text, "%Y-%m-%d").date(), ""
	except ValueError:
		return None, f"Invalid expiry_date {text!r}; use ISO YYYY-MM-DD"


def to_delta_expiry_str(exp: dt.date | dt.datetime) -> str:
	if isinstance(exp, dt.datetime):
		exp = exp.date()
	return exp.strftime("%d-%m-%Y")


def to_iso_expiry_str(exp: dt.date | dt.datetime) -> str:
	if isinstance(exp, dt.datetime):
		exp = exp.date()
	return exp.strftime("%Y-%m-%d")


def select_expiry(
	expiries: list[dt.datetime],
	*,
	mode: str,
	expiry_date: Any = None,
	min_dte_hours: float = 6.0,
	max_dte_hours: float = 240.0,
	exit_before_expiry_minutes: float = 30.0,
) -> tuple[dt.datetime | None, str]:
	"""FIXED_DATE never rolls. NEAREST / NEXT_AVAILABLE pick the soonest eligible listing."""
	mode = str(mode or "FIXED_DATE").strip().upper()
	now = dt.datetime.now(tz=dt.timezone.utc)
	buffer = dt.timedelta(minutes=exit_before_expiry_minutes)

	def still_tradable(exp: dt.datetime) -> bool:
		exp_utc = exp.replace(tzinfo=dt.timezone.utc) if exp.tzinfo is None else exp.astimezone(dt.timezone.utc)
		return now < (exp_utc - buffer)

	if mode == "FIXED_DATE":
		parsed, err = parse_iso_expiry_date(expiry_date)
		if err:
			return None, err
		iso = to_iso_expiry_str(parsed)
		match = next((exp for exp in expiries if exp.date() == parsed), None)
		if match is None:
			return None, f"Configured expiry {iso} is not listed"
		if not still_tradable(match):
			return None, f"Configured expiry {iso} is within exit_before_expiry_minutes or expired"
		return match, ""

	eligible: list[dt.datetime] = []
	for exp in sorted(expiries, key=lambda item: item.date()):
		if not still_tradable(exp):
			continue
		exp_utc = exp.replace(tzinfo=dt.timezone.utc) if exp.tzinfo is None else exp.astimezone(dt.timezone.utc)
		hours = (exp_utc - now).total_seconds() / 3600.0
		if hours < min_dte_hours:
			continue
		if mode == "NEAREST" and hours > max_dte_hours:
			continue
		eligible.append(exp)
	if not eligible:
		return None, "No eligible expiry found"
	return eligible[0], ""


def listed_has_strike(strikes: list[float], target: float) -> bool:
	return any(abs(s - target) < 1e-6 for s in strikes)


def nearest_strike(strikes: list[float], target: float) -> float | None:
	if not strikes:
		return None
	return min(strikes, key=lambda s: (abs(s - target), s))


def select_manual_strikes(
	*,
	put_long: float,
	put_short: float,
	call_short: float,
	call_long: float,
	put_strikes: list[float],
	call_strikes: list[float],
) -> dict[str, float] | None:
	"""Exact listed strikes only. Do not snap MANUAL targets to ATM."""
	targets = {
		"PUT_LONG": float(put_long),
		"PUT_SHORT": float(put_short),
		"CALL_SHORT": float(call_short),
		"CALL_LONG": float(call_long),
	}
	if any(v <= 0 for v in targets.values()):
		return None
	if not listed_has_strike(put_strikes, targets["PUT_LONG"]):
		return None
	if not listed_has_strike(put_strikes, targets["PUT_SHORT"]):
		return None
	if not listed_has_strike(call_strikes, targets["CALL_SHORT"]):
		return None
	if not listed_has_strike(call_strikes, targets["CALL_LONG"]):
		return None
	if not (targets["PUT_LONG"] < targets["PUT_SHORT"] < targets["CALL_SHORT"] < targets["CALL_LONG"]):
		return None
	return targets


def find_contract(
	rows: list[dict[str, Any]],
	*,
	option_type: str,
	strike: float,
	expiry_date: str,
	asset: str,
) -> dict[str, Any] | None:
	wanted_kind = "P" if option_type == "put" else "C"
	wanted_ct = "put_options" if option_type == "put" else "call_options"
	for row in rows:
		symbol = str(row.get("symbol") or "")
		parsed = parse_option_symbol(symbol)
		strike_px = to_float(row.get("strike_price"), parsed["strike"] if parsed else 0.0)
		ct = str(row.get("contract_type") or (parsed["contract_type"] if parsed else ""))
		exp = (parsed["expiry_date"] if parsed else "") or str(row.get("expiry_date") or "")
		if parsed and parsed["asset"] != asset:
			continue
		if abs(strike_px - strike) > 1e-9:
			continue
		if ct and ct != wanted_ct and not symbol.startswith(wanted_kind + "-"):
			continue
		if exp and exp != expiry_date:
			continue
		if parsed and parsed["kind"] != wanted_kind:
			continue
		return row
	return None


def enrich_option_product(client: DeltaSRP, symbol: str) -> dict[str, Any]:
	"""Resolve live option product_id / tick_size / contract_value. Never hardcode option IDs."""
	product = inner_result(client.get_product(symbol))
	return product if isinstance(product, dict) else {}


@dataclass
class OptionLeg:
	name: str
	side: str
	option_type: str
	strike: float
	symbol: str = ""
	product_id: int = 0
	contract_type: str = ""
	expiry_date: str = ""
	quantity: int = 1
	tick_size: float = 0.1
	contract_value: float = 1.0
	bid: float = 0.0
	ask: float = 0.0
	mark: float = 0.0
	last: float = 0.0
	entry_price: float | None = None
	current_price: float | None = None
	order_id: int | None = None
	fill_status: str = "pending"
	pnl: float = 0.0

	@property
	def signed_size(self) -> int:
		qty = int(self.quantity)
		return qty if self.side == "buy" else -qty


def ticker_quotes(row: dict[str, Any]) -> tuple[float, float, float, float]:
	quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
	bid = to_float(row.get("best_bid") or row.get("bid") or quotes.get("bid"), 0.0)
	ask = to_float(row.get("best_ask") or row.get("ask") or quotes.get("ask"), 0.0)
	mark = to_float(row.get("mark_price") or quotes.get("mark_price"), 0.0)
	last = to_float(row.get("close") or row.get("last_price") or row.get("last"), 0.0)
	mid = mark or last
	if bid <= 0:
		bid = mid
	if ask <= 0:
		ask = mid
	return bid, ask, mark, last


def row_to_option_leg(
	name: str,
	side: str,
	option_type: str,
	strike: float,
	row: dict[str, Any],
	quantity: int,
	expiry_date: str,
) -> OptionLeg:
	parsed = parse_option_symbol(str(row.get("symbol") or "")) or {}
	bid, ask, mark, last = ticker_quotes(row)
	return OptionLeg(
		name=name,
		side=side,
		option_type=option_type,
		strike=strike,
		symbol=str(row.get("symbol") or parsed.get("symbol") or ""),
		product_id=to_int(row.get("product_id") or row.get("id")),
		contract_type=str(row.get("contract_type") or parsed.get("contract_type") or ""),
		expiry_date=expiry_date,
		quantity=quantity,
		tick_size=to_float(row.get("tick_size"), 0.1),
		contract_value=to_float(row.get("contract_value"), 0.0),
		bid=bid,
		ask=ask,
		mark=mark,
		last=last,
		current_price=mark or last,
	)


def validate_credit_condor_legs(legs: dict[str, OptionLeg], underlying_asset: str) -> list[str]:
	issues: list[str] = []
	if set(legs) < set(CREDIT_CONDOR_DISPLAY):
		issues.append("Missing one or more of the four required legs")
		return issues
	expiries = {leg.expiry_date for leg in legs.values()}
	if len(expiries) != 1:
		issues.append(f"Legs do not share one expiry: {sorted(expiries)}")
	ids = [leg.product_id for leg in legs.values()]
	if any(pid <= 0 for pid in ids):
		issues.append("One or more legs are missing a valid product_id")
	if len(set(ids)) < 4:
		issues.append("Duplicate product IDs in the four-leg set")
	for leg in legs.values():
		parsed = parse_option_symbol(leg.symbol)
		if parsed and parsed["asset"] != underlying_asset:
			issues.append(f"{leg.symbol} is not {underlying_asset}")
	put_long, put_short = legs["PUT_LONG"].strike, legs["PUT_SHORT"].strike
	call_short, call_long = legs["CALL_SHORT"].strike, legs["CALL_LONG"].strike
	if not (put_long < put_short < call_short < call_long):
		issues.append(f"Strike order invalid: PUT {put_long}/{put_short} CALL {call_short}/{call_long}")
	return issues


def estimate_leg_premium(leg: OptionLeg) -> float:
	if leg.side == "buy":
		return leg.ask or leg.mark or leg.last
	return leg.bid or leg.mark or leg.last


@dataclass
class CondorPayoff:
	net_credit: float = 0.0
	net_credit_cash: float = 0.0
	max_profit: float = 0.0
	max_loss: float = 0.0
	reward_risk: float = 0.0
	lower_breakeven: float = 0.0
	upper_breakeven: float = 0.0
	is_credit: bool = True


def calculate_credit_condor_payoff(
	legs: dict[str, OptionLeg],
	quantity: int,
	multiplier_override: float | None = None,
) -> CondorPayoff:
	"""Cash = premium points * contract_value * qty. BTC options often use 0.001."""
	if any(name not in legs for name in CREDIT_CONDOR_DISPLAY):
		return CondorPayoff()
	premiums: dict[str, float] = {}
	for name, leg in legs.items():
		premiums[name] = float(leg.entry_price) if leg.entry_price is not None else estimate_leg_premium(leg)
	received = premiums["PUT_SHORT"] + premiums["CALL_SHORT"]
	paid = premiums["PUT_LONG"] + premiums["CALL_LONG"]
	net_credit = received - paid
	cv = multiplier_override if multiplier_override and multiplier_override > 0 else 0.0
	if cv <= 0:
		cvs = [leg.contract_value for leg in legs.values() if leg.contract_value > 0]
		cv = cvs[0] if cvs else 1.0
	qty = max(1, int(quantity))
	cash = net_credit * cv * qty
	put_width = abs(legs["PUT_SHORT"].strike - legs["PUT_LONG"].strike)
	call_width = abs(legs["CALL_LONG"].strike - legs["CALL_SHORT"].strike)
	wing_cash = max(put_width, call_width) * cv * qty
	if net_credit >= 0:
		max_profit = cash
		max_loss = max(wing_cash - cash, 0.0)
	else:
		max_profit = max(wing_cash + cash, 0.0)
		max_loss = abs(cash)
	reward_risk = (max_profit / max_loss) if max_loss > 1e-12 else 0.0
	return CondorPayoff(
		net_credit=net_credit,
		net_credit_cash=cash,
		max_profit=max_profit,
		max_loss=max_loss,
		reward_risk=reward_risk,
		lower_breakeven=legs["PUT_SHORT"].strike - net_credit,
		upper_breakeven=legs["CALL_SHORT"].strike + net_credit,
		is_credit=net_credit >= 0,
	)


def calculate_leg_pnl(leg: OptionLeg) -> float:
	entry = leg.entry_price
	current = leg.current_price if leg.current_price is not None else (leg.mark or leg.last)
	if entry is None or current is None:
		return 0.0
	cv = leg.contract_value if leg.contract_value > 0 else 1.0
	return (current - entry) * leg.signed_size * cv


def calculate_combined_pnl(
	legs: dict[str, OptionLeg],
	exchange_pnls: dict[int, float] | None = None,
) -> float:
	"""Sum of all four legs. Never use spot as TP/SL input."""
	total = 0.0
	for leg in legs.values():
		if exchange_pnls is not None and leg.product_id in exchange_pnls:
			value = exchange_pnls[leg.product_id]
		else:
			value = calculate_leg_pnl(leg)
		leg.pnl = value
		total += value
	return total


def resolve_tp_sl_targets(
	payoff: CondorPayoff,
	*,
	tp_mode: str = "PERCENT_OF_MAX_PROFIT",
	tp_value: float = 70.0,
	sl_mode: str = "PERCENT_OF_MAX_LOSS",
	sl_value: float = 50.0,
	tp_enabled: bool = True,
	sl_enabled: bool = True,
) -> tuple[float, float]:
	"""Freeze absolute cash levels at fill. Do not recompute from live marks.

	PERCENT_OF_MAX_PROFIT 70 with max_profit 10 -> TP +7
	PERCENT_OF_MAX_LOSS   50 with max_loss 5    -> SL -2.5
	ABSOLUTE_PNL: TP = +abs(value), SL = -abs(value)
	"""
	tp_mode = str(tp_mode or "").strip().upper().replace(" ", "_")
	sl_mode = str(sl_mode or "").strip().upper().replace(" ", "_")
	if tp_mode in {"PERCENT_MAX_PROFIT"}:
		tp_mode = "PERCENT_OF_MAX_PROFIT"
	if sl_mode in {"PERCENT_MAX_LOSS"}:
		sl_mode = "PERCENT_OF_MAX_LOSS"
	tp = 0.0
	sl = 0.0
	if tp_enabled:
		if tp_mode == "ABSOLUTE_PNL":
			tp = abs(tp_value)
		else:
			tp = abs(payoff.max_profit) * (tp_value / 100.0)
	if sl_enabled:
		if sl_mode == "ABSOLUTE_PNL":
			sl = -abs(sl_value)
		else:
			sl = -abs(payoff.max_loss) * (sl_value / 100.0)
	return tp, sl


def make_client_order_id(strategy_id: str, leg_name: str, attempt: int) -> str:
	short = strategy_id.replace("-", "")[:10]
	tag = {"PUT_LONG": "pl", "CALL_LONG": "cl", "PUT_SHORT": "ps", "CALL_SHORT": "cs"}.get(leg_name, "xx")
	return f"{CLIENT_ORDER_PREFIX}{short}{tag}{attempt}"[:32]


def extract_order(payload: Any) -> dict[str, Any]:
	result = inner_result(payload)
	if isinstance(result, list) and result and isinstance(result[0], dict):
		return result[0]
	if isinstance(result, dict):
		return result
	return {}


def order_is_filled(order: dict[str, Any]) -> bool:
	state = str(order.get("state") or "").lower()
	unfilled = to_int(order.get("unfilled_size"), -1)
	return unfilled == 0 or state in {"closed", "filled"}


def order_is_dead(order: dict[str, Any]) -> bool:
	return str(order.get("state") or "").lower() in {"cancelled", "rejected", "expired"}


def fill_price_from_order(order: dict[str, Any], fallback: float) -> float:
	for key in ("average_fill_price", "avg_fill_price", "average_price", "limit_price"):
		px = to_float(order.get(key), 0.0)
		if px > 0:
			return px
	return fallback


def wait_for_fill(
	client: DeltaSRP,
	*,
	order_id: int | None,
	client_order_id: str | None = None,
	timeout: float = 15.0,
	stop_flag: Callable[[], bool] | None = None,
) -> dict[str, Any]:
	"""Poll until filled/dead or timeout. Caller MUST cancel on timeout."""
	deadline = time.time() + max(1.0, timeout)
	last: dict[str, Any] = {}
	while time.time() < deadline:
		if stop_flag is not None and stop_flag():
			return last or {"state": "cancelled", "unfilled_size": -1, "aborted": True}
		try:
			last = extract_order(
				client.get_order_by_id(order_id=order_id, client_order_id=None if order_id else client_order_id)
			)
		except Exception:
			time.sleep(0.4)
			continue
		if order_is_filled(last) or order_is_dead(last):
			return last
		time.sleep(0.4)
	return last


def cancel_leg_order(client: DeltaSRP, leg: OptionLeg, *, dry_run: bool = False) -> None:
	if dry_run or not leg.order_id or not leg.product_id:
		return
	client.cancel_order(product_id=leg.product_id, order_id=leg.order_id, dry_run=False)


def place_option_leg(
	client: DeltaSRP,
	leg: OptionLeg,
	*,
	order_type: str = "market_order",
	client_order_id: str,
	reduce_only: bool = False,
	dry_run: bool = False,
	timeout: float = 15.0,
	stop_flag: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
	"""Place one option order. Market orders must not send limit_price.

	On timeout or abort the working order is cancelled so retries cannot stack.
	"""
	kwargs: dict[str, Any] = {
		"size": int(leg.quantity),
		"side": leg.side if not reduce_only else ("sell" if leg.side == "buy" else "buy"),
		"order_type": order_type,
		"product_id": leg.product_id,
		"client_order_id": client_order_id,
		"reduce_only": reduce_only,
		"dry_run": dry_run,
	}
	if order_type == "limit_order":
		px = (leg.ask or leg.mark or leg.last) if kwargs["side"] == "buy" else (leg.bid or leg.mark or leg.last)
		kwargs["limit_price"] = str(px)
	raw = client.place_order(**kwargs)
	if dry_run:
		leg.fill_status = "filled"
		leg.entry_price = estimate_leg_premium(leg)
		return True, "dry_run"
	order = extract_order(raw)
	leg.order_id = to_int(order.get("id"), 0) or None
	filled = wait_for_fill(client, order_id=leg.order_id, timeout=timeout, stop_flag=stop_flag)
	if order_is_filled(filled):
		leg.fill_status = "filled"
		leg.entry_price = fill_price_from_order(filled, estimate_leg_premium(leg))
		return True, "filled"
	cancel_leg_order(client, leg, dry_run=False)
	return False, str(filled.get("state") or "unfilled")


def execute_four_leg_entry(
	client: DeltaSRP,
	legs: dict[str, OptionLeg],
	*,
	strategy_id: str,
	order_type: str = "market_order",
	delay_s: float = 0.25,
	timeout: float = 15.0,
	retry_failed_leg: bool = False,
	dry_run: bool = False,
	stop_flag: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
	"""Wings first. Cancel unfilled before the next attempt. Unwind if a later leg fails."""
	for index, name in enumerate(CREDIT_CONDOR_ENTRY, start=1):
		leg = legs[name]
		attempts = 2 if retry_failed_leg else 1
		ok = False
		reason = ""
		for attempt in range(1, attempts + 1):
			if attempt > 1:
				cancel_leg_order(client, leg, dry_run=dry_run)
			ok, reason = place_option_leg(
				client,
				leg,
				order_type=order_type,
				client_order_id=make_client_order_id(strategy_id, name, attempt),
				dry_run=dry_run,
				timeout=timeout,
				stop_flag=stop_flag,
			)
			if ok:
				break
		if not ok:
			unwind_filled_option_legs(client, legs, strategy_id=strategy_id, dry_run=dry_run, timeout=timeout)
			return False, f"Failed {name}: {reason}"
		if delay_s and index < len(CREDIT_CONDOR_ENTRY):
			time.sleep(delay_s)
	return True, "entered"


def unwind_filled_option_legs(
	client: DeltaSRP,
	legs: dict[str, OptionLeg],
	*,
	strategy_id: str,
	dry_run: bool = False,
	timeout: float = 15.0,
) -> None:
	for name in reversed(CREDIT_CONDOR_ENTRY):
		leg = legs.get(name)
		if not leg or leg.fill_status != "filled":
			if leg:
				cancel_leg_order(client, leg, dry_run=dry_run)
			continue
		place_option_leg(
			client,
			leg,
			order_type="market_order",
			client_order_id=make_client_order_id(strategy_id, name, 90),
			reduce_only=True,
			dry_run=dry_run,
			timeout=timeout,
		)
		leg.fill_status = "closed"


def flatten_four_legs(
	client: DeltaSRP,
	legs: dict[str, OptionLeg],
	*,
	strategy_id: str,
	dry_run: bool = False,
	timeout: float = 15.0,
	delay_s: float = 0.25,
) -> bool:
	"""Close only these four product IDs. Shorts first. reduce_only on live orders."""
	for name in CREDIT_CONDOR_EXIT:
		leg = legs.get(name)
		if not leg:
			continue
		cancel_leg_order(client, leg, dry_run=dry_run)
		if leg.fill_status not in {"filled", "closed"}:
			continue
		ok, _reason = place_option_leg(
			client,
			leg,
			order_type="market_order",
			client_order_id=make_client_order_id(strategy_id, name, 80),
			reduce_only=True,
			dry_run=dry_run,
			timeout=timeout,
		)
		if ok:
			leg.fill_status = "closed"
		if delay_s:
			time.sleep(delay_s)
	return all(leg.fill_status == "closed" for leg in legs.values())


def count_open_strategy_legs(live_rows: list[dict[str, Any]], legs: dict[str, OptionLeg]) -> int:
	wanted = {leg.product_id for leg in legs.values() if leg.product_id}
	count = 0
	for row in live_rows:
		pid = to_int(row.get("product_id") or (row.get("product") or {}).get("id"))
		if pid in wanted and to_int(row.get("size"), 0) != 0:
			count += 1
	return count


def classify_restart_action(open_strategy_legs: int, positions_fetched: bool = True) -> str:
	"""Startup recipe when a previous cycle was persisted.

	resume          — four sizes still open: continue MONITORING, keep lock
	unwind_partial  — 1-3 sizes: flatten remainder, lock this session
	clear_idle      — exchange flat: drop STOPPED/CLOSED/ERROR and allow a new one-shot
	lock_unverified — could not fetch positions: do not guess, do not enter
	"""
	if not positions_fetched:
		return "lock_unverified"
	if open_strategy_legs >= 4:
		return "resume"
	if open_strategy_legs > 0:
		return "unwind_partial"
	return "clear_idle"


def create_client(config: DeltaConfig | None = None) -> DeltaSRP:
	"""Factory helper for algo files importing this module."""
	return DeltaSRP(config=config)


if __name__ == "__main__":
	client = DeltaSRP.from_env()
	run_from_config = _truthy(os.getenv("DELTA_RUN_CONFIG", "false"))
	config_path = os.getenv("DELTA_CONFIG_PATH", "config.yaml")
	if len(sys.argv) > 1:
		config_path = sys.argv[1]
		run_from_config = True

	if run_from_config:
		result = client.execute_from_config_file(config_path=config_path)
		print(json.dumps(result, indent=2))
	else:
		status = client.get_trading_status()
		print(json.dumps(status, indent=2))

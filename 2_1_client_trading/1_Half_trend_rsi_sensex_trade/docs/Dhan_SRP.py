"""REFERENCE ONLY — DhanHQ v2 patterns for stocks and options.

Do not import this file from application code (main.py, strategies, etc.).
Copy the snippets or helpers you need into your own modules, then delete
this file when you no longer need it.

SDK: dhanhq 2.2+ with DhanContext
Docs: https://dhanhq.co/docs/v2/
      https://dhanhq.co/docs/DhanHQ-py/
      https://dhanhq.co/docs/v2/instruments/
      https://dhanhq.co/docs/v2/option-chain/
      https://dhanhq.co/docs/v2/orders/

Safety:
- Default to LIMIT orders. Dhan currently converts API MARKET orders to limit+MPP.
- Never use CNC or MTF on F&O / commodity / currency segments.
- F&O quantity must be a multiple of lot size from the security master.
- Order APIs need a whitelisted static IP. Data APIs need an active Data Plan.
- Live place_order / modify / cancel / kill_switch / multi-leg must be confirmed first.
- Never hardcode credentials. Use DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dhanhq import DhanContext, FullDepth, MarketFeed, OrderUpdate, dhanhq

try:
    from dhanhq import DhanLogin
except ImportError:
    DhanLogin = None


# ---------------------------------------------------------------------------
# 1. Constants — SDK enums and convenience IDs
# ---------------------------------------------------------------------------
# Exchange: dhanhq.NSE="NSE_EQ", BSE="BSE_EQ", NSE_FNO, BSE_FNO,
#           MCX="MCX_COMM", CUR="NSE_CURRENCY", INDEX="IDX_I"
# Transaction: BUY / SELL
# Order type: LIMIT / MARKET / SL="STOP_LOSS" / SLM="STOP_LOSS_MARKET"
# Product: CNC / INTRA="INTRADAY" / MARGIN / MTF
# Validity: DAY / IOC

# Index underlyings for option-chain / expiry_list. Convenience only — re-check
# the security master if anything looks off.
INDEX_UNDERLYINGS = {
    "NIFTY": {"security_id": 13, "segment": "IDX_I"},
    "BANKNIFTY": {"security_id": 25, "segment": "IDX_I"},
    "FINNIFTY": {"security_id": 27, "segment": "IDX_I"},
    "MIDCPNIFTY": {"security_id": 442, "segment": "IDX_I"},
    "SENSEX": {"security_id": 51, "segment": "IDX_I"},
}

# Equity IDs are relatively stable. Derivative contract IDs are not.
# RELIANCE=2885, HDFCBANK=1333, TCS=11536, INFY=1594, ICICIBANK=4963, SBIN=3045

EQUITY_SEGMENTS = {"NSE_EQ", "BSE_EQ"}
DERIVATIVE_SEGMENTS = {"NSE_FNO", "BSE_FNO", "MCX_COMM", "NSE_CURRENCY", "BSE_CURRENCY"}
EQUITY_PRODUCTS = {"CNC", "INTRADAY", "MARGIN", "MTF"}
DERIVATIVE_PRODUCTS = {"INTRADAY", "MARGIN"}
VALID_ORDER_TYPES = {"LIMIT", "MARKET", "STOP_LOSS", "STOP_LOSS_MARKET"}
NOTIONAL_WARNING_RS = 50_000

_MASTER_CACHE: pd.DataFrame | None = None


# ---------------------------------------------------------------------------
# 2. Client bootstrap
# ---------------------------------------------------------------------------
def get_client(config_path: str | None = None):
    """Return (dhan, dhan_context) from env, then optional JSON.

    Primary: DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN
    Optional JSON keys: client_id / access_token (or ClientCode / token_id)
    """
    client_id = (os.environ.get("DHAN_CLIENT_ID") or "").strip()
    access_token = (os.environ.get("DHAN_ACCESS_TOKEN") or "").strip()

    for path in (config_path, os.environ.get("DHAN_CONFIG_PATH"), "config.json"):
        if client_id and access_token:
            break
        if not path or not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
        client_id = client_id or str(config.get("client_id") or config.get("ClientCode") or "")
        access_token = access_token or str(config.get("access_token") or config.get("token_id") or "")

    if not client_id or not access_token:
        raise ValueError(
            "Credentials not found. Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN."
        )

    context = DhanContext(client_id, access_token)
    return dhanhq(context), context


def check_profile(client_id: str | None = None, access_token: str | None = None) -> dict[str, Any]:
    """Optional access check before live or data work.

    Useful fields: tokenValidity, activeSegment, ddpi, mtf, dataPlan, dataValidity.
    """
    if DhanLogin is None:
        raise RuntimeError("DhanLogin is not available in this dhanhq install.")
    client_id = client_id or os.environ["DHAN_CLIENT_ID"]
    access_token = access_token or os.environ["DHAN_ACCESS_TOKEN"]
    login = DhanLogin(client_id)
    return login.user_profile(access_token)


def unwrap_sdk_data(response: dict[str, Any]) -> Any:
    """SDK wraps HTTP as {status, remarks, data}. Raise on failure."""
    if response.get("status") != "success":
        raise ValueError(response.get("remarks") or response)
    return response.get("data")


# ---------------------------------------------------------------------------
# 3. Security master
# Columns: SEM_SMST_SECURITY_ID, SEM_EXM_EXCH_ID, SEM_INSTRUMENT_NAME,
#          SEM_TRADING_SYMBOL, SEM_CUSTOM_SYMBOL, SEM_LOT_UNITS,
#          SEM_TICK_SIZE, SEM_EXPIRY_DATE, SEM_STRIKE_PRICE, SEM_OPTION_TYPE
# Compact CSV: https://images.dhan.co/api-data/api-scrip-master.csv
# Detailed:    https://images.dhan.co/api-data/api-scrip-master-detailed.csv
# ---------------------------------------------------------------------------
def load_security_master(
    cache_path: str | Path | None = None,
    refresh: bool = False,
    mode: str = "compact",
) -> pd.DataFrame:
    """Load the compact security master. Prefer this over hardcoded IDs."""
    global _MASTER_CACHE
    path = Path(cache_path) if cache_path else None

    if not refresh and _MASTER_CACHE is not None:
        return _MASTER_CACHE
    if not refresh and path and path.exists():
        _MASTER_CACHE = pd.read_csv(path, low_memory=False)
        return _MASTER_CACHE

    master = dhanhq.fetch_security_list(mode)
    if master is None or getattr(master, "empty", False):
        raise ValueError("Unable to fetch the Dhan security master")
    _MASTER_CACHE = master
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        master.to_csv(path, index=False)
    return _MASTER_CACHE


def _exchange_from_segment(exchange_segment: str) -> str:
    mapping = {
        "NSE_EQ": "NSE",
        "BSE_EQ": "BSE",
        "NSE_FNO": "NSE",
        "BSE_FNO": "BSE",
        "MCX_COMM": "MCX",
        "NSE_CURRENCY": "NSE",
        "BSE_CURRENCY": "BSE",
        "IDX_I": "NSE",
    }
    key = exchange_segment.upper()
    if key in mapping:
        return mapping[key]
    return key.split("_")[0]


def resolve_symbol(
    symbol: str,
    exchange_segment: str = "NSE_EQ",
    instrument_name: str = "EQUITY",
    master: pd.DataFrame | None = None,
) -> dict[str, Any] | None:
    """Resolve an NSE/BSE equity (or other cash instrument) from the master."""
    df = master if master is not None else load_security_master()
    query = symbol.upper().strip()
    exchange = _exchange_from_segment(exchange_segment)
    base = (
        (df["SEM_EXM_EXCH_ID"].astype(str).str.upper() == exchange)
        & (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper() == instrument_name.upper())
    )
    exact = df[base & (df["SEM_TRADING_SYMBOL"].astype(str).str.upper() == query)]
    if exact.empty and "SEM_CUSTOM_SYMBOL" in df.columns:
        exact = df[base & (df["SEM_CUSTOM_SYMBOL"].astype(str).str.upper() == query)]
    if exact.empty and "SEM_CUSTOM_SYMBOL" in df.columns:
        exact = df[base & df["SEM_CUSTOM_SYMBOL"].astype(str).str.upper().str.contains(query, na=False)]
    if exact.empty:
        return None
    row = exact.iloc[0]
    return {
        "security_id": str(row["SEM_SMST_SECURITY_ID"]),
        "trading_symbol": str(row["SEM_TRADING_SYMBOL"]),
        "display_name": str(row.get("SEM_CUSTOM_SYMBOL", "")),
        "exchange_segment": exchange_segment,
        "instrument_name": str(row["SEM_INSTRUMENT_NAME"]),
        "lot_size": int(row["SEM_LOT_UNITS"]) if not pd.isna(row.get("SEM_LOT_UNITS")) else 1,
        "tick_size": float(row["SEM_TICK_SIZE"]) if not pd.isna(row.get("SEM_TICK_SIZE")) else None,
    }


def resolve_derivative(
    underlying: str,
    *,
    instrument_names: tuple[str, ...] = ("OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"),
    strike: float | None = None,
    option_type: str | None = None,
    expiry: str | None = None,
    exchange: str = "NSE",
    master: pd.DataFrame | None = None,
) -> dict[str, Any] | None:
    """Resolve an option/future contract. Derivative IDs change every expiry."""
    df = master if master is not None else load_security_master()
    name = underlying.upper().strip()
    mask = (
        (df["SEM_EXM_EXCH_ID"].astype(str).str.upper() == exchange.upper())
        & (df["SEM_INSTRUMENT_NAME"].isin(instrument_names))
    )
    if "SM_SYMBOL_NAME" in df.columns:
        mask &= df["SM_SYMBOL_NAME"].astype(str).str.upper() == name
    else:
        trading = df["SEM_TRADING_SYMBOL"].astype(str).str.upper()
        custom = df["SEM_CUSTOM_SYMBOL"].astype(str).str.upper()
        mask &= (custom == name) | trading.str.startswith(name) | custom.str.startswith(name + " ")

    if strike is not None:
        mask &= df["SEM_STRIKE_PRICE"].astype(float) == float(strike)
    if option_type is not None:
        mask &= df["SEM_OPTION_TYPE"].astype(str).str.upper() == option_type.upper()
    if expiry is not None:
        mask &= df["SEM_EXPIRY_DATE"].astype(str).str[:10] == str(expiry)[:10]

    matches = df[mask].sort_values(["SEM_EXPIRY_DATE", "SEM_TRADING_SYMBOL"])
    if matches.empty:
        return None
    row = matches.iloc[0]
    return {
        "security_id": str(row["SEM_SMST_SECURITY_ID"]),
        "trading_symbol": str(row["SEM_TRADING_SYMBOL"]),
        "lot_size": int(row["SEM_LOT_UNITS"]) if not pd.isna(row.get("SEM_LOT_UNITS")) else None,
        "tick_size": float(row["SEM_TICK_SIZE"]) if not pd.isna(row.get("SEM_TICK_SIZE")) else None,
        "expiry": str(row.get("SEM_EXPIRY_DATE", "")),
        "instrument_name": str(row["SEM_INSTRUMENT_NAME"]),
        "option_type": str(row.get("SEM_OPTION_TYPE", "")),
        "strike": float(row["SEM_STRIKE_PRICE"]) if not pd.isna(row.get("SEM_STRIKE_PRICE")) else None,
    }


def get_lot_size(
    *,
    security_id: str | None = None,
    trading_symbol: str | None = None,
    underlying: str | None = None,
    master: pd.DataFrame | None = None,
) -> int | None:
    """Lot size from the security master. Do not hardcode NIFTY/BANKNIFTY lots."""
    df = master if master is not None else load_security_master()
    if security_id is not None:
        match = df[df["SEM_SMST_SECURITY_ID"].astype(str) == str(security_id)]
        if not match.empty:
            return int(match.iloc[0]["SEM_LOT_UNITS"])
    if trading_symbol is not None:
        match = df[df["SEM_TRADING_SYMBOL"].astype(str).str.upper() == trading_symbol.upper()]
        if not match.empty:
            return int(match.iloc[0]["SEM_LOT_UNITS"])
    if underlying is not None:
        match = df[
            (df["SEM_CUSTOM_SYMBOL"].astype(str).str.upper() == underlying.upper())
            & (df["SEM_INSTRUMENT_NAME"].isin(["OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"]))
        ]
        if match.empty and "SM_SYMBOL_NAME" in df.columns:
            match = df[
                (df["SM_SYMBOL_NAME"].astype(str).str.upper() == underlying.upper())
                & (df["SEM_INSTRUMENT_NAME"].isin(["OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"]))
            ]
        if not match.empty:
            return int(match.iloc[0]["SEM_LOT_UNITS"])
    return None


# ---------------------------------------------------------------------------
# 4. Quotes — ticker_data / ohlc_data / quote_data  (1 request/sec, max 1000 IDs)
# Request: {"NSE_EQ": [2885, 1333], "NSE_FNO": [49081], "IDX_I": [13]}
# ---------------------------------------------------------------------------
def ticker_ltp(dhan, securities: dict[str, list[int]]) -> dict[str, Any]:
    return unwrap_sdk_data(dhan.ticker_data(securities))


def ohlc_snapshot(dhan, securities: dict[str, list[int]]) -> dict[str, Any]:
    return unwrap_sdk_data(dhan.ohlc_data(securities))


def full_quote(dhan, securities: dict[str, list[int]]) -> dict[str, Any]:
    return unwrap_sdk_data(dhan.quote_data(securities))


# ---------------------------------------------------------------------------
# 5. History — timestamps are epoch; convert with dhan.convert_to_date_time
# expiry_code documented values: 0, 1, 2
# Minute intervals: 1, 5, 15, 25, 60
# ---------------------------------------------------------------------------
def history_daily_df(
    dhan,
    security_id: str,
    exchange_segment: str,
    instrument_type: str,
    from_date: str,
    to_date: str,
    expiry_code: int = 0,
    oi: bool = False,
) -> pd.DataFrame:
    response = dhan.historical_daily_data(
        security_id=security_id,
        exchange_segment=exchange_segment,
        instrument_type=instrument_type,
        from_date=from_date,
        to_date=to_date,
        expiry_code=expiry_code,
        oi=oi,
    )
    return _candles_to_df(dhan, unwrap_sdk_data(response))


def history_minute_df(
    dhan,
    security_id: str,
    exchange_segment: str,
    instrument_type: str,
    from_date: str,
    to_date: str,
    interval: int = 1,
    oi: bool = False,
) -> pd.DataFrame:
    response = dhan.intraday_minute_data(
        security_id=security_id,
        exchange_segment=exchange_segment,
        instrument_type=instrument_type,
        from_date=from_date,
        to_date=to_date,
        interval=interval,
        oi=oi,
    )
    return _candles_to_df(dhan, unwrap_sdk_data(response))


def _candles_to_df(dhan, data: Any) -> pd.DataFrame:
    df = pd.DataFrame(data)
    if df.empty or "timestamp" not in df.columns:
        return df
    df["timestamp"] = [dhan.convert_to_date_time(ts) for ts in df["timestamp"]]
    return df


# ---------------------------------------------------------------------------
# 6. Option chain
# Raw: data.last_price, data.oc keyed by strike string ("25650.000000")
# Normalized columns (repo names, not raw Dhan names): strike, ce_ltp, pe_oi, ...
# Rate: one unique option-chain request every 3 seconds
# ---------------------------------------------------------------------------
def fetch_expiry_list(dhan, under_security_id: int, under_exchange_segment: str = "IDX_I") -> list[str]:
    return unwrap_sdk_data(
        dhan.expiry_list(
            under_security_id=under_security_id,
            under_exchange_segment=under_exchange_segment,
        )
    )


def normalize_option_chain(response: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    data = unwrap_sdk_data(response)
    spot = float(data["last_price"])
    rows: list[dict[str, Any]] = []
    for strike_key, payload in sorted((data.get("oc") or {}).items(), key=lambda item: float(item[0])):
        row: dict[str, Any] = {"strike": float(strike_key)}
        for side in ("ce", "pe"):
            leg = payload.get(side) or {}
            greeks = leg.get("greeks") or {}
            row[f"{side}_security_id"] = str(leg["security_id"]) if leg.get("security_id") is not None else None
            row[f"{side}_ltp"] = leg.get("last_price")
            row[f"{side}_avg_price"] = leg.get("average_price")
            row[f"{side}_oi"] = leg.get("oi")
            row[f"{side}_oi_change"] = leg.get("oi_change")
            row[f"{side}_volume"] = leg.get("volume")
            row[f"{side}_iv"] = leg.get("implied_volatility")
            row[f"{side}_bid_price"] = leg.get("top_bid_price")
            row[f"{side}_bid_qty"] = leg.get("top_bid_quantity")
            row[f"{side}_ask_price"] = leg.get("top_ask_price")
            row[f"{side}_ask_qty"] = leg.get("top_ask_quantity")
            row[f"{side}_delta"] = greeks.get("delta")
            row[f"{side}_gamma"] = greeks.get("gamma")
            row[f"{side}_theta"] = greeks.get("theta")
            row[f"{side}_vega"] = greeks.get("vega")
        rows.append(row)
    return spot, rows


def fetch_chain_df(
    dhan,
    under_security_id: int,
    expiry: str,
    under_exchange_segment: str = "IDX_I",
) -> tuple[pd.DataFrame, float]:
    response = dhan.option_chain(
        under_security_id=under_security_id,
        under_exchange_segment=under_exchange_segment,
        expiry=expiry,
    )
    spot, rows = normalize_option_chain(response)
    return pd.DataFrame(rows), spot


def find_atm_row(chain_df: pd.DataFrame, spot: float) -> pd.Series:
    return chain_df.iloc[(chain_df["strike"] - spot).abs().argsort().iloc[0]]


def nearest_expiry(dhan, under_security_id: int = 13, under_exchange_segment: str = "IDX_I") -> str:
    expiries = fetch_expiry_list(dhan, under_security_id, under_exchange_segment)
    if not expiries:
        raise ValueError("No expiries returned")
    return expiries[0]


# ---------------------------------------------------------------------------
# 7. Order preview + validation
# ---------------------------------------------------------------------------
def preview_order(
    security_id: str,
    exchange_segment: str,
    transaction_type: str,
    quantity: int,
    order_type: str,
    product_type: str,
    *,
    price: float = 0.0,
    trading_symbol: str | None = None,
) -> str:
    notional = price * quantity if price else 0
    lines = [
        "--- ORDER PREVIEW ---",
        f"Security:     {trading_symbol or security_id}",
        f"Exchange:     {exchange_segment}",
        f"Action:       {transaction_type}",
        f"Quantity:     {quantity}",
        f"Order Type:   {order_type}",
        f"Product Type: {product_type}",
        f"Price:        {'MARKET / MPP' if order_type == 'MARKET' else f'Rs. {price:,.2f}'}",
    ]
    if notional:
        lines.append(f"Notional:     Rs. {notional:,.2f}")
    if notional > NOTIONAL_WARNING_RS:
        lines.append("Warning:      Notional exceeds Rs. 50,000")
    lines.append("---------------------")
    return "\n".join(lines)


def validate_order_payload(
    *,
    security_id: str | None = None,
    exchange_segment: str | None = None,
    transaction_type: str | None = None,
    quantity: int | None = None,
    order_type: str | None = None,
    product_type: str | None = None,
    price: float = 0,
    trigger_price: float = 0,
    validity: str = "DAY",
    after_market_order: bool = False,
    trading_symbol: str | None = None,
    lot_size: int | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    exchange_segment = exchange_segment.upper() if exchange_segment else exchange_segment
    transaction_type = transaction_type.upper() if transaction_type else transaction_type
    order_type = order_type.upper() if order_type else order_type
    product_type = product_type.upper() if product_type else product_type
    validity = validity.upper() if validity else validity

    if not security_id:
        errors.append("security_id is required")
    if not exchange_segment:
        errors.append("exchange_segment is required")
    if not transaction_type:
        errors.append("transaction_type is required")
    if quantity is None or quantity <= 0:
        errors.append("quantity must be a positive integer")
    if not order_type:
        errors.append("order_type is required")
    if not product_type:
        errors.append("product_type is required")
    if exchange_segment and exchange_segment not in EQUITY_SEGMENTS | DERIVATIVE_SEGMENTS:
        errors.append(f"Invalid exchange_segment: {exchange_segment}")
    if transaction_type and transaction_type not in {"BUY", "SELL"}:
        errors.append(f"Invalid transaction_type: {transaction_type}")
    if order_type and order_type not in VALID_ORDER_TYPES:
        errors.append(f"Invalid order_type: {order_type}")
    if validity and validity not in {"DAY", "IOC"}:
        errors.append(f"Invalid validity: {validity}")
    if order_type in {"LIMIT", "STOP_LOSS"} and price <= 0:
        errors.append(f"price is required for {order_type} orders")
    if order_type in {"STOP_LOSS", "STOP_LOSS_MARKET"} and trigger_price <= 0:
        errors.append(f"trigger_price is required for {order_type} orders")
    if exchange_segment in EQUITY_SEGMENTS and product_type and product_type not in EQUITY_PRODUCTS:
        errors.append(f"Invalid product_type '{product_type}' for equity.")
    if exchange_segment in DERIVATIVE_SEGMENTS and product_type and product_type not in DERIVATIVE_PRODUCTS:
        errors.append(f"Invalid product_type '{product_type}' for derivatives. Use INTRADAY or MARGIN.")
    if order_type == "MARKET":
        warnings.append("API market orders are currently converted to limit orders with MPP.")

    effective_lot = lot_size or get_lot_size(security_id=security_id, trading_symbol=trading_symbol)
    if exchange_segment in DERIVATIVE_SEGMENTS and quantity:
        if effective_lot is not None and quantity % effective_lot != 0:
            errors.append(f"Derivative quantity must be a multiple of lot size {effective_lot}.")
        elif effective_lot is None:
            warnings.append("Could not resolve lot size. Confirm from the security master.")

    if price and quantity and price * quantity > NOTIONAL_WARNING_RS:
        warnings.append(f"High notional: Rs. {price * quantity:,.2f}")
    if not after_market_order and datetime.now().weekday() >= 5:
        warnings.append("Weekend. Use AMO only if intentional.")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _sdk_segment(segment: str) -> str:
    mapping = {
        "NSE_EQ": dhanhq.NSE,
        "BSE_EQ": dhanhq.BSE,
        "NSE_FNO": dhanhq.NSE_FNO,
        "BSE_FNO": dhanhq.BSE_FNO,
        "MCX_COMM": dhanhq.MCX,
        "NSE_CURRENCY": dhanhq.CUR,
        "IDX_I": dhanhq.INDEX,
    }
    key = segment.upper()
    if key not in mapping:
        raise ValueError(f"Unsupported exchange_segment: {segment}")
    return mapping[key]


def _sdk_order_type(order_type: str) -> str:
    mapping = {
        "LIMIT": dhanhq.LIMIT,
        "MARKET": dhanhq.MARKET,
        "STOP_LOSS": dhanhq.SL,
        "STOP_LOSS_MARKET": dhanhq.SLM,
        "SL": dhanhq.SL,
        "SLM": dhanhq.SLM,
    }
    return mapping[order_type.upper()]


def _sdk_product_type(product_type: str) -> str:
    mapping = {
        "CNC": dhanhq.CNC,
        "INTRADAY": dhanhq.INTRA,
        "MIS": dhanhq.INTRA,
        "MARGIN": dhanhq.MARGIN,
        "MTF": dhanhq.MTF,
    }
    return mapping[product_type.upper()]


# ---------------------------------------------------------------------------
# 8. Orders — default LIMIT, default dry_run=True
# ---------------------------------------------------------------------------
def place_order_safe(
    dhan,
    *,
    security_id: str,
    exchange_segment: str,
    transaction_type: str,
    quantity: int,
    order_type: str = "LIMIT",
    product_type: str = "CNC",
    price: float = 0,
    trigger_price: float = 0,
    validity: str = "DAY",
    disclosed_quantity: int = 0,
    after_market_order: bool = False,
    amo_time: str = "OPEN",
    tag: str | None = None,
    trading_symbol: str | None = None,
    lot_size: int | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    validation = validate_order_payload(
        security_id=security_id,
        exchange_segment=exchange_segment,
        transaction_type=transaction_type,
        quantity=quantity,
        order_type=order_type,
        product_type=product_type,
        price=price,
        trigger_price=trigger_price,
        validity=validity,
        after_market_order=after_market_order,
        trading_symbol=trading_symbol,
        lot_size=lot_size,
    )
    preview = preview_order(
        security_id=security_id,
        exchange_segment=exchange_segment,
        transaction_type=transaction_type,
        quantity=quantity,
        order_type=order_type.upper(),
        product_type=product_type.upper(),
        price=price,
        trading_symbol=trading_symbol,
    )
    result: dict[str, Any] = {
        "status": "validation" if validation["valid"] else "failure",
        "preview": preview,
        "validation": validation,
    }
    if not validation["valid"] or dry_run:
        return result

    payload = {
        "security_id": str(security_id),
        "exchange_segment": _sdk_segment(exchange_segment),
        "transaction_type": dhanhq.BUY if transaction_type.upper() == "BUY" else dhanhq.SELL,
        "quantity": int(quantity),
        "order_type": _sdk_order_type(order_type),
        "product_type": _sdk_product_type(product_type),
        "price": float(price),
        "trigger_price": float(trigger_price),
        "disclosed_quantity": int(disclosed_quantity),
        "after_market_order": after_market_order,
        "validity": dhanhq.DAY if validity.upper() == "DAY" else dhanhq.IOC,
        "amo_time": amo_time,
    }
    if tag is not None:
        payload["tag"] = tag
    response = dhan.place_order(**payload)
    result["status"] = response.get("status")
    result["response"] = response
    return result


def modify_order_request(dhan, order_id: str, order_type: str, quantity: int, price: float, trigger_price: float = 0, disclosed_quantity: int = 0, validity: str = "DAY", leg_name=None):
    return dhan.modify_order(
        order_id=order_id,
        order_type=_sdk_order_type(order_type),
        leg_name=leg_name,
        quantity=int(quantity),
        price=float(price),
        trigger_price=float(trigger_price),
        disclosed_quantity=int(disclosed_quantity),
        validity=dhanhq.DAY if validity.upper() == "DAY" else dhanhq.IOC,
    )


def cancel_order_request(dhan, order_id: str):
    return dhan.cancel_order(order_id=order_id)


def place_slice_order_request(dhan, **kwargs):
    """Use after freeze-quantity checks. Same fields as place_order plus large qty."""
    return dhan.place_slice_order(**kwargs)


# ---------------------------------------------------------------------------
# 9. Margin, funds, portfolio
# Fund field spelling is Dhan's: availabelBalance
# ---------------------------------------------------------------------------
def check_margin(
    dhan,
    *,
    security_id: str,
    exchange_segment: str,
    transaction_type: str,
    quantity: int,
    product_type: str,
    price: float,
    trigger_price: float = 0,
) -> dict[str, Any]:
    margin = unwrap_sdk_data(
        dhan.margin_calculator(
            security_id=security_id,
            exchange_segment=_sdk_segment(exchange_segment),
            transaction_type=dhanhq.BUY if transaction_type.upper() == "BUY" else dhanhq.SELL,
            quantity=quantity,
            product_type=_sdk_product_type(product_type),
            price=price,
            trigger_price=trigger_price,
        )
    )
    funds = unwrap_sdk_data(dhan.get_fund_limits())
    total_margin = margin.get("totalMargin", 0.0) or 0.0
    available = funds.get("availabelBalance", 0.0) or 0.0
    return {
        "total_margin": total_margin,
        "available_balance": available,
        "brokerage": margin.get("brokerage", 0.0),
        "leverage": margin.get("leverage"),
        "span_margin": margin.get("spanMargin"),
        "exposure_margin": margin.get("exposureMargin"),
        "sufficient": available >= total_margin,
        "shortfall": max(0.0, total_margin - available),
    }


def format_pnl_report(holdings_response: dict[str, Any], positions_response: dict[str, Any]) -> dict[str, Any]:
    holdings = unwrap_sdk_data(holdings_response) or []
    positions = unwrap_sdk_data(positions_response) or []
    report = {
        "total_investment": 0.0,
        "current_value": 0.0,
        "total_pnl": 0.0,
        "day_pnl": 0.0,
        "holdings_count": len(holdings),
        "positions_count": len(positions),
    }
    for holding in holdings:
        qty = holding.get("totalQty", 0) or 0
        report["total_investment"] += (holding.get("avgCostPrice", 0) or 0) * qty
        report["current_value"] += holding.get("marketValue", 0) or 0
        report["total_pnl"] += holding.get("pnl", 0) or 0
        report["day_pnl"] += holding.get("dayPnl", 0) or 0
    for position in positions:
        report["total_pnl"] += (position.get("realizedProfit", 0) or 0) + (position.get("unrealizedProfit", 0) or 0)
    return report


# ---------------------------------------------------------------------------
# 10. Multi-leg (sequential). Confirm before live execution.
# ---------------------------------------------------------------------------
def check_margin_for_orders(dhan, orders: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    total_margin = 0.0
    available = 0.0
    for order in orders:
        margin = check_margin(
            dhan,
            security_id=str(order["security_id"]),
            exchange_segment=order["exchange_segment"],
            transaction_type=order["transaction_type"],
            quantity=int(order["quantity"]),
            product_type=order["product_type"],
            price=float(order.get("price", 0) or 0),
            trigger_price=float(order.get("trigger_price", 0) or 0),
        )
        results.append({"order": order, "margin": margin})
        total_margin += float(margin["total_margin"])
        available = margin["available_balance"]
    return {
        "orders": results,
        "total_margin": total_margin,
        "available_balance": available,
        "sufficient": available >= total_margin,
        "shortfall": max(0.0, total_margin - available),
    }


def place_multi_leg_orders(dhan, orders: list[dict[str, Any]], dry_run: bool = True, continue_on_error: bool = False) -> dict[str, Any]:
    results = []
    for order in orders:
        result = place_order_safe(
            dhan,
            security_id=str(order["security_id"]),
            exchange_segment=order.get("exchange_segment", "NSE_FNO"),
            transaction_type=order.get("transaction_type", "BUY"),
            quantity=int(order.get("quantity", 1)),
            order_type=order.get("order_type", "LIMIT"),
            product_type=order.get("product_type", "INTRADAY"),
            price=float(order.get("price", 0) or 0),
            trigger_price=float(order.get("trigger_price", 0) or 0),
            validity=order.get("validity", "DAY"),
            tag=order.get("tag"),
            trading_symbol=order.get("trading_symbol") or order.get("symbol"),
            lot_size=order.get("lot_size"),
            dry_run=dry_run,
        )
        results.append(result)
        if not dry_run:
            response = result.get("response") or {}
            if response.get("status") == "failure" and not continue_on_error:
                break
    return {"dry_run": dry_run, "orders": results}


def build_iron_condor_orders(
    chain_df: pd.DataFrame,
    spot: float,
    *,
    lot_size: int,
    short_offset: float = 200,
    wing_width: float = 200,
    product_type: str = "INTRADAY",
    order_type: str = "LIMIT",
) -> list[dict[str, Any]]:
    """Pattern only: long iron condor = buy wings, sell closer strikes.

    Long iron condor: BUY PE (far) + SELL PE (near) + SELL CE (near) + BUY CE (far).
    """
    strikes = sorted(chain_df["strike"].tolist())
    sell_ce = min(strikes, key=lambda value: abs(value - (spot + short_offset)))
    buy_ce = sell_ce + wing_width
    sell_pe = min(strikes, key=lambda value: abs(value - (spot - short_offset)))
    buy_pe = sell_pe - wing_width

    def row_for(strike: float) -> pd.Series:
        match = chain_df[chain_df["strike"] == strike]
        if match.empty:
            raise ValueError(f"Strike {strike} not in chain")
        return match.iloc[0]

    specs = [
        ("BUY", "pe", buy_pe),
        ("SELL", "pe", sell_pe),
        ("SELL", "ce", sell_ce),
        ("BUY", "ce", buy_ce),
    ]
    orders = []
    for action, side, strike in specs:
        row = row_for(strike)
        orders.append(
            {
                "security_id": row[f"{side}_security_id"],
                "exchange_segment": "NSE_FNO",
                "transaction_type": action,
                "quantity": lot_size,
                "order_type": order_type,
                "product_type": product_type,
                "price": float(row[f"{side}_ltp"] or 0),
                "trading_symbol": f"{int(strike)} {side.upper()}",
                "lot_size": lot_size,
            }
        )
    return orders


# ---------------------------------------------------------------------------
# 11. Advanced orders (comment live calls before copying)
# Forever SDK param is trigger_Price (capital P). Super uses targetPrice / stopLossPrice.
# ---------------------------------------------------------------------------
def place_forever_order(dhan, **kwargs):
    return dhan.place_forever(**kwargs)


def place_super_order_request(dhan, **kwargs):
    return dhan.place_super_order(**kwargs)


# ---------------------------------------------------------------------------
# 12. WebSocket feeds — need DhanContext, version="v2"
# MarketFeed modes: Ticker=15, Quote=17, Depth=19, Full=21
# Limits: 5 sockets/user, 5000 instruments/connection, 100 per subscribe message
# ---------------------------------------------------------------------------
def create_market_feed(dhan_context, instruments, on_message=None, on_connect=None, on_close=None):
    return MarketFeed(
        dhan_context,
        instruments,
        "v2",
        on_connect=on_connect,
        on_message=on_message,
        on_close=on_close,
    )


def create_order_update_feed(dhan_context, on_update=None):
    feed = OrderUpdate(dhan_context)
    if on_update is not None:
        feed.on_update = on_update
    return feed


def create_full_depth(dhan_context, instruments, depth_level: int = 20):
    return FullDepth(dhan_context, instruments, depth_level=depth_level)


# ---------------------------------------------------------------------------
# 13. Thin adapter — copy methods you need; do not import this module
# ---------------------------------------------------------------------------
class Dhansrp:
    """Current-SDK adapter. Env credentials. dry_run=True by default on orders."""

    def __init__(self, instrument_cache_path: str | None = None):
        self.dhan, self.dhan_context = get_client()
        self.instrument_df = load_security_master(cache_path=instrument_cache_path)

    def resolve_symbol(self, symbol: str, exchange_segment: str = "NSE_EQ", instrument_name: str = "EQUITY"):
        return resolve_symbol(symbol, exchange_segment, instrument_name, master=self.instrument_df)

    def resolve_derivative(self, underlying: str, **kwargs):
        kwargs.setdefault("master", self.instrument_df)
        return resolve_derivative(underlying, **kwargs)

    def get_lot_size(self, **kwargs):
        kwargs.setdefault("master", self.instrument_df)
        return get_lot_size(**kwargs)

    def ticker_ltp(self, securities):
        return ticker_ltp(self.dhan, securities)

    def ohlc_snapshot(self, securities):
        return ohlc_snapshot(self.dhan, securities)

    def full_quote(self, securities):
        return full_quote(self.dhan, securities)

    def history_daily_df(self, **kwargs):
        return history_daily_df(self.dhan, **kwargs)

    def history_minute_df(self, **kwargs):
        return history_minute_df(self.dhan, **kwargs)

    def fetch_expiry_list(self, under_security_id: int, under_exchange_segment: str = "IDX_I"):
        return fetch_expiry_list(self.dhan, under_security_id, under_exchange_segment)

    def fetch_chain_df(self, under_security_id: int, expiry: str, under_exchange_segment: str = "IDX_I"):
        return fetch_chain_df(self.dhan, under_security_id, expiry, under_exchange_segment)

    def find_atm_row(self, chain_df: pd.DataFrame, spot: float):
        return find_atm_row(chain_df, spot)

    def get_option_chain_snapshot(
        self,
        under_security_id: int = 13,
        under_exchange_segment: str = "IDX_I",
        expiry: str | None = None,
        points_each_side: float = 500,
    ):
        expiry = expiry or nearest_expiry(self.dhan, under_security_id, under_exchange_segment)
        chain_df, spot = self.fetch_chain_df(under_security_id, expiry, under_exchange_segment)
        atm = find_atm_row(chain_df, spot)
        nearby = chain_df[
            (chain_df["strike"] >= atm["strike"] - points_each_side)
            & (chain_df["strike"] <= atm["strike"] + points_each_side)
        ].reset_index(drop=True)
        return {"expiry": expiry, "spot": spot, "atm_strike": float(atm["strike"]), "chain": nearby, "atm": atm}

    def preview_order(self, **kwargs):
        return preview_order(**kwargs)

    def validate_order_payload(self, **kwargs):
        return validate_order_payload(**kwargs)

    def place_stock_order(self, symbol: str, quantity: int, price: float, transaction_type: str = "BUY", product_type: str = "INTRADAY", order_type: str = "LIMIT", exchange_segment: str = "NSE_EQ", dry_run: bool = True):
        resolved = self.resolve_symbol(symbol, exchange_segment=exchange_segment)
        if resolved is None:
            raise ValueError(f"Unable to resolve {symbol}")
        return place_order_safe(
            self.dhan,
            security_id=resolved["security_id"],
            exchange_segment=exchange_segment,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=order_type,
            product_type=product_type,
            price=price,
            trading_symbol=resolved["trading_symbol"],
            dry_run=dry_run,
        )

    def place_option_order(
        self,
        underlying: str,
        expiry: str,
        strike: float,
        option_type: str,
        quantity: int | None = None,
        transaction_type: str = "BUY",
        order_type: str = "LIMIT",
        product_type: str = "INTRADAY",
        price: float = 0,
        exchange: str = "NSE",
        dry_run: bool = True,
    ):
        contract = self.resolve_derivative(
            underlying,
            instrument_names=("OPTIDX", "OPTSTK"),
            strike=strike,
            option_type=option_type,
            expiry=expiry,
            exchange=exchange,
        )
        if contract is None:
            raise ValueError(f"Unable to resolve {underlying} {strike} {option_type} {expiry}")
        qty = quantity or contract["lot_size"]
        return place_order_safe(
            self.dhan,
            security_id=contract["security_id"],
            exchange_segment="NSE_FNO" if exchange == "NSE" else "BSE_FNO",
            transaction_type=transaction_type,
            quantity=qty,
            order_type=order_type,
            product_type=product_type,
            price=price,
            trading_symbol=contract["trading_symbol"],
            lot_size=contract["lot_size"],
            dry_run=dry_run,
        )

    def check_margin(self, **kwargs):
        return check_margin(self.dhan, **kwargs)

    def get_fund_limits(self):
        return self.dhan.get_fund_limits()

    def get_holdings(self):
        return self.dhan.get_holdings()

    def get_positions(self):
        return self.dhan.get_positions()

    def get_order_list(self):
        return self.dhan.get_order_list()

    def get_order_by_id(self, order_id: str):
        return self.dhan.get_order_by_id(order_id=order_id)

    def get_trade_book(self, order_id: str | None = None):
        if order_id:
            return self.dhan.get_trade_book(order_id=order_id)
        return self.dhan.get_trade_book()

    def convert_position(self, from_product_type, exchange_segment, position_type, security_id, convert_qty, to_product_type):
        return self.dhan.convert_position(
            from_product_type=from_product_type,
            exchange_segment=exchange_segment,
            position_type=position_type,
            security_id=security_id,
            convert_qty=convert_qty,
            to_product_type=to_product_type,
        )

    def place_multi_leg_orders(self, orders, dry_run: bool = True, continue_on_error: bool = False):
        return place_multi_leg_orders(self.dhan, orders, dry_run=dry_run, continue_on_error=continue_on_error)

    def place_iron_condor(
        self,
        under_security_id: int = 13,
        under_exchange_segment: str = "IDX_I",
        expiry: str | None = None,
        short_offset: float = 200,
        wing_width: float = 200,
        lot_size: int | None = None,
        dry_run: bool = True,
    ):
        expiry = expiry or nearest_expiry(self.dhan, under_security_id, under_exchange_segment)
        chain_df, spot = self.fetch_chain_df(under_security_id, expiry, under_exchange_segment)
        qty = lot_size or self.get_lot_size(underlying="NIFTY")
        if not qty:
            raise ValueError("Resolve lot_size from the security master before placing.")
        orders = build_iron_condor_orders(
            chain_df, spot, lot_size=qty, short_offset=short_offset, wing_width=wing_width
        )
        margin = check_margin_for_orders(self.dhan, orders)
        placements = place_multi_leg_orders(self.dhan, orders, dry_run=dry_run)
        return {"spot": spot, "expiry": expiry, "orders": orders, "margin": margin, "placements": placements}

    def create_market_feed(self, instruments, **kwargs):
        return create_market_feed(self.dhan_context, instruments, **kwargs)

    def create_order_update_feed(self, on_update=None):
        return create_order_update_feed(self.dhan_context, on_update=on_update)

    def kill_switch(self, action: str = "ACTIVATE"):
        mapped = {"ON": "ACTIVATE", "OFF": "DEACTIVATE"}.get(action.upper(), action.upper())
        return self.dhan.kill_switch(mapped)

    def status_kill_switch(self):
        return self.dhan.status_kill_switch()


# ---------------------------------------------------------------------------
# Copy-paste recipes (live place_order calls stay commented)
# ---------------------------------------------------------------------------
def example_equity_limit_preview():
    dhan, _ = get_client()
    resolved = resolve_symbol("RELIANCE")
    print(
        preview_order(
            security_id=resolved["security_id"],
            exchange_segment=dhanhq.NSE,
            transaction_type=dhanhq.BUY,
            quantity=1,
            order_type=dhanhq.LIMIT,
            product_type=dhanhq.CNC,
            price=2450.0,
            trading_symbol="RELIANCE",
        )
    )
    print(
        place_order_safe(
            dhan,
            security_id="2885",
            exchange_segment="NSE_EQ",
            transaction_type="BUY",
            quantity=1,
            order_type="LIMIT",
            product_type="CNC",
            price=2450.0,
            trading_symbol="RELIANCE",
            dry_run=True,
        )
    )
    # response = dhan.place_order(
    #     security_id="2885",
    #     exchange_segment=dhanhq.NSE,
    #     transaction_type=dhanhq.BUY,
    #     quantity=1,
    #     order_type=dhanhq.LIMIT,
    #     product_type=dhanhq.CNC,
    #     price=2450.0,
    #     validity=dhanhq.DAY,
    #     tag="equity_example",
    # )


def example_nifty_atm_ce():
    dhan, _ = get_client()
    expiry = nearest_expiry(dhan, 13, "IDX_I")
    chain_df, spot = fetch_chain_df(dhan, 13, expiry, "IDX_I")
    atm = find_atm_row(chain_df, spot)
    lot = get_lot_size(underlying="NIFTY")
    print(spot, atm["strike"], atm["ce_security_id"], atm["ce_ltp"], lot)
    print(
        place_order_safe(
            dhan,
            security_id=str(atm["ce_security_id"]),
            exchange_segment="NSE_FNO",
            transaction_type="BUY",
            quantity=lot,
            order_type="LIMIT",
            product_type="INTRADAY",
            price=float(atm["ce_ltp"]),
            trading_symbol=f"NIFTY {int(atm['strike'])} CE",
            lot_size=lot,
            dry_run=True,
        )
    )
    # response = dhan.place_order(
    #     security_id=str(atm["ce_security_id"]),
    #     exchange_segment=dhanhq.NSE_FNO,
    #     transaction_type=dhanhq.BUY,
    #     quantity=lot,
    #     order_type=dhanhq.LIMIT,
    #     product_type=dhanhq.INTRA,
    #     price=float(atm["ce_ltp"]),
    #     validity=dhanhq.DAY,
    # )


def example_stock_option_chain():
    dhan, _ = get_client()
    expiries = fetch_expiry_list(dhan, 2885, "NSE_EQ")
    chain_df, spot = fetch_chain_df(dhan, 2885, expiries[0], "NSE_EQ")
    atm = find_atm_row(chain_df, spot)
    print("RELIANCE spot", spot, "ATM", atm["strike"])


def example_order_lifecycle():
    dhan, _ = get_client()
    # place -> get_order_by_id -> modify_order -> cancel_order -> get_order_list / get_trade_book
    orders = dhan.get_order_list()
    trades = dhan.get_trade_book()
    print(orders.get("status"), trades.get("status"))


def example_funds_and_portfolio():
    dhan, _ = get_client()
    funds = unwrap_sdk_data(dhan.get_fund_limits())
    print(funds["availabelBalance"], funds["utilizedAmount"])
    holdings = dhan.get_holdings()
    positions = dhan.get_positions()
    print(format_pnl_report(holdings, positions))


def example_market_feed():
    _, context = get_client()

    def on_message(instance, message):
        print(message)

    feed = create_market_feed(
        context,
        [
            (MarketFeed.NSE, "2885", MarketFeed.Ticker),
            (MarketFeed.NSE, "1333", MarketFeed.Quote),
            (MarketFeed.IDX, "13", MarketFeed.Ticker),
        ],
        on_message=on_message,
    )
    # feed.run_forever()
    return feed


if __name__ == "__main__":
    print("docs/Dhan_SRP.py is a DhanHQ v2 reference file. Do not import it from the app.")
    print("Copy patterns into your own code. Live order examples stay commented.")

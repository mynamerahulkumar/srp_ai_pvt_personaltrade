# SRP Dhan Helper — DhanHQ v2 cookbook

**Reference only.** Copy patterns into your own code. Do not import this guide or `Dhan_SRP.py` from `main.py` or any strategy module. Delete both files when you no longer need them.

Companion file: [`Dhan_SRP.py`](Dhan_SRP.py)

SDK: `dhanhq` 2.2+ with `DhanContext`

- https://dhanhq.co/docs/v2/
- https://dhanhq.co/docs/DhanHQ-py/
- https://dhanhq.co/docs/v2/instruments/
- https://dhanhq.co/docs/v2/option-chain/
- https://dhanhq.co/docs/v2/orders/

---

## What these files are

| File | Role |
|------|------|
| `docs/Dhan_SRP.py` | Current-SDK functions + a thin `Dhansrp` adapter |
| `docs/srp_dhan_helper.md` | This cookbook |

They are **not** a portable broker package. Reimplement the few helpers you need in your own modules.

---

## Setup

```bash
pip install dhanhq pandas python-dotenv
```

Credentials (same as `.env.example`):

```bash
export DHAN_CLIENT_ID="YOUR_CLIENT_ID"
export DHAN_ACCESS_TOKEN="YOUR_ACCESS_TOKEN"
```

```python
import os
from dhanhq import DhanContext, dhanhq

dhan_context = DhanContext(
    os.environ["DHAN_CLIENT_ID"],
    os.environ["DHAN_ACCESS_TOKEN"],
)
dhan = dhanhq(dhan_context)
```

From the reference file:

```python
# copy get_client() from Dhan_SRP.py — do not import docs/
dhan, dhan_context = get_client()
```

Optional JSON (`client_id` / `access_token`) is a fallback only. Env vars are the primary path.

### Access checks before live or data use

| Need | Requirement |
|------|-------------|
| Place / modify / cancel / super / forever orders | Static IP whitelisted on Dhan |
| Quotes, history, option chain, live feed | Active Data Plan (`dataPlan`) |

Optional profile check (`DhanLogin.user_profile`): `tokenValidity`, `activeSegment`, `ddpi`, `mtf`, `dataPlan`, `dataValidity`.

---

## Safety

1. Confirm before any live order, modify, cancel, kill switch, or multi-leg execution.
2. Print a readable preview first.
3. Default to `LIMIT`. API `MARKET` orders are currently converted to limit with MPP.
4. Warn when notional exceeds Rs. 50,000.
5. F&O quantity must be a multiple of **security-master** lot size.
6. Never use `CNC` or `MTF` on `NSE_FNO`, `BSE_FNO`, commodity, or currency.
7. Never hardcode credentials.
8. Keep live `dhan.place_order(...)` commented until you are ready.

Product types:

| Segment | Allowed |
|---------|---------|
| `NSE_EQ`, `BSE_EQ` | `CNC`, `INTRADAY`, `MARGIN`, `MTF` |
| `NSE_FNO`, `BSE_FNO`, `MCX_COMM`, `NSE_CURRENCY`, `BSE_CURRENCY` | `INTRADAY`, `MARGIN` |

---

## SDK constants

| Category | Constant | Value |
|----------|----------|-------|
| Exchange | `dhanhq.NSE` | `NSE_EQ` |
| | `dhanhq.BSE` | `BSE_EQ` |
| | `dhanhq.NSE_FNO` | `NSE_FNO` |
| | `dhanhq.BSE_FNO` | `BSE_FNO` |
| | `dhanhq.MCX` | `MCX_COMM` |
| | `dhanhq.CUR` | `NSE_CURRENCY` |
| | `dhanhq.INDEX` | `IDX_I` |
| Transaction | `dhanhq.BUY` / `dhanhq.SELL` | `BUY` / `SELL` |
| Order type | `dhanhq.LIMIT` | `LIMIT` |
| | `dhanhq.MARKET` | `MARKET` |
| | `dhanhq.SL` | `STOP_LOSS` |
| | `dhanhq.SLM` | `STOP_LOSS_MARKET` |
| Product | `dhanhq.CNC` | `CNC` |
| | `dhanhq.INTRA` | `INTRADAY` |
| | `dhanhq.MARGIN` | `MARGIN` |
| | `dhanhq.MTF` | `MTF` |
| Validity | `dhanhq.DAY` / `dhanhq.IOC` | `DAY` / `IOC` |

## SDK methods to prefer

| Task | Method |
|------|--------|
| Place / slice | `place_order()`, `place_slice_order()` |
| Modify / cancel | `modify_order()`, `cancel_order()` |
| Order book | `get_order_list()`, `get_order_by_id()`, `get_order_by_correlationID()` |
| Trades | `get_trade_book()`, `get_trade_history()`, `ledger_report()` |
| Super orders | `place_super_order()`, `modify_super_order()`, `cancel_super_order()`, `get_super_order_list()` |
| Forever orders | `place_forever()`, `modify_forever()`, `cancel_forever()`, `get_forever()` |
| Portfolio | `get_holdings()`, `get_positions()`, `convert_position()` |
| Funds | `get_fund_limits()`, `margin_calculator()` |
| History | `historical_daily_data()`, `intraday_minute_data()`, `expired_options_data()` |
| Quotes | `ticker_data()`, `ohlc_data()`, `quote_data()` |
| Options | `expiry_list()`, `option_chain()` |
| Instruments | `dhanhq.fetch_security_list()` |
| Feeds | `MarketFeed`, `OrderUpdate`, `FullDepth` |
| Kill switch | `kill_switch()`, `status_kill_switch()` |

---

## Instrument resolution

Primary source: security master (`fetch_security_list("compact")`).

| Column | Meaning |
|--------|---------|
| `SEM_SMST_SECURITY_ID` | Security ID |
| `SEM_EXM_EXCH_ID` | `NSE` / `BSE` / `MCX` |
| `SEM_INSTRUMENT_NAME` | `EQUITY`, `OPTIDX`, `OPTSTK`, `FUTIDX`, ... |
| `SEM_TRADING_SYMBOL` | Exchange symbol |
| `SEM_CUSTOM_SYMBOL` | Display name |
| `SEM_LOT_UNITS` | Lot size |
| `SEM_TICK_SIZE` | Tick size |
| `SEM_EXPIRY_DATE` | Expiry (compare first 10 chars `YYYY-MM-DD`) |
| `SEM_STRIKE_PRICE` | Strike |
| `SEM_OPTION_TYPE` | `CE` / `PE` |

Index underlyings for `expiry_list` / `option_chain` (convenience; re-check if unsure):

| Underlying | `security_id` | Segment |
|------------|---------------|---------|
| NIFTY | `13` | `IDX_I` |
| BANKNIFTY | `25` | `IDX_I` |
| FINNIFTY | `27` | `IDX_I` |
| MIDCPNIFTY | `442` | `IDX_I` |
| SENSEX | `51` | `IDX_I` |

Equity IDs are relatively stable (RELIANCE `2885`, HDFCBANK `1333`). **Derivative contract IDs are not.** Resolve options fresh each session.

Copy `resolve_symbol()` for cash, `resolve_derivative()` for OPT/FUT, `get_lot_size()` for lots. Do not use a static NIFTY 50 map as the primary lookup.

---

## Copy-paste recipes

All live `place_order` calls stay commented. `place_order_safe(..., dry_run=True)` only validates and previews.

### 1. Equity limit order (RELIANCE / HDFCBANK)

```python
from dhanhq import dhanhq

dhan, _ = get_client()
resolved = resolve_symbol("HDFCBANK")  # or "RELIANCE" -> security_id 2885

print(
    preview_order(
        security_id=resolved["security_id"],
        exchange_segment=dhanhq.NSE,
        transaction_type=dhanhq.BUY,
        quantity=10,
        order_type=dhanhq.LIMIT,
        product_type=dhanhq.CNC,       # delivery; use INTRADAY for MIS
        price=1800.0,
        trading_symbol=resolved["trading_symbol"],
    )
)

result = place_order_safe(
    dhan,
    security_id=resolved["security_id"],
    exchange_segment="NSE_EQ",
    transaction_type="BUY",
    quantity=10,
    order_type="LIMIT",
    product_type="CNC",
    price=1800.0,
    trading_symbol=resolved["trading_symbol"],
    dry_run=True,
)

# response = dhan.place_order(
#     security_id=resolved["security_id"],
#     exchange_segment=dhanhq.NSE,
#     transaction_type=dhanhq.BUY,
#     quantity=10,
#     order_type=dhanhq.LIMIT,
#     product_type=dhanhq.CNC,
#     price=1800.0,
#     validity=dhanhq.DAY,
#     tag="equity_example",
# )
```

Via the thin class (still copy, do not import `docs/`):

```python
broker = Dhansrp()
broker.place_stock_order("RELIANCE", quantity=1, price=2450.0, product_type="CNC", dry_run=True)
```

### 2. Index option from chain (NIFTY ATM CE)

```python
dhan, _ = get_client()

expiry = nearest_expiry(dhan, under_security_id=13, under_exchange_segment="IDX_I")
chain_df, spot = fetch_chain_df(dhan, 13, expiry, "IDX_I")
atm = find_atm_row(chain_df, spot)
lot = get_lot_size(underlying="NIFTY")

print(spot, atm["strike"], atm["ce_security_id"], atm["ce_ltp"], lot)

result = place_order_safe(
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
```

BANKNIFTY: `under_security_id=25`. Same `IDX_I` segment.

### 3. Stock-option chain (equity underlying)

```python
dhan, _ = get_client()
reliance = resolve_symbol("RELIANCE")  # 2885, NSE_EQ

expiries = fetch_expiry_list(
    dhan,
    under_security_id=int(reliance["security_id"]),
    under_exchange_segment="NSE_EQ",
)
chain_df, spot = fetch_chain_df(
    dhan,
    under_security_id=int(reliance["security_id"]),
    expiry=expiries[0],
    under_exchange_segment="NSE_EQ",
)
atm = find_atm_row(chain_df, spot)
print("RELIANCE spot", spot, "ATM", atm["strike"], atm["ce_security_id"])
```

Or resolve a specific contract from the master:

```python
contract = resolve_derivative(
    "RELIANCE",
    instrument_names=("OPTSTK",),
    strike=1400,
    option_type="CE",
    expiry="2026-09-30",
    exchange="NSE",
)
```

### 4. Order lifecycle

```python
# 1) place_order_safe(..., dry_run=True) then, after confirmation:
# response = dhan.place_order(...)
# order_id = response["data"]["orderId"]

order = dhan.get_order_by_id(order_id="YOUR_ORDER_ID")
status = order["data"]["orderStatus"]  # PENDING, TRADED, CANCELLED, ...

# if status == "PENDING":
#     dhan.modify_order(
#         order_id=order_id,
#         order_type=dhanhq.LIMIT,
#         leg_name=None,
#         quantity=10,
#         price=1801.0,
#         trigger_price=0,
#         disclosed_quantity=0,
#         validity=dhanhq.DAY,
#     )
#     dhan.cancel_order(order_id=order_id)

orders = dhan.get_order_list()
trades = dhan.get_trade_book()
# dhan.get_order_by_correlationID("my_tag")
# dhan.get_trade_history("2026-01-01", "2026-01-31", page_number=0)
```

Modify requests should send the **full placed quantity**, not remaining qty.

### 5. Funds and margin

```python
funds = unwrap_sdk_data(dhan.get_fund_limits())
print(funds["availabelBalance"])  # Dhan's spelling
print(funds["utilizedAmount"], funds["collateralAmount"], funds["withdrawableBalance"])

margin = check_margin(
    dhan,
    security_id="2885",
    exchange_segment="NSE_EQ",
    transaction_type="BUY",
    quantity=10,
    product_type="CNC",
    price=2450.0,
)
print(margin["sufficient"], margin["total_margin"], margin["shortfall"])
```

The installed SDK has single-order `margin_calculator()` only. Summing per-leg margin for a basket is conservative, not true SPAN.

### 6. Holdings, positions, PnL

```python
holdings = dhan.get_holdings()
positions = dhan.get_positions()
summary = format_pnl_report(holdings, positions)

# Holdings: tradingSymbol, securityId, totalQty, availableQty, avgCostPrice
# Positions: netQty, buyAvg, sellAvg, realizedProfit, unrealizedProfit,
#            drvExpiryDate, drvOptionType, drvStrikePrice

open_legs = [p for p in unwrap_sdk_data(positions) if p.get("netQty")]

# dhan.convert_position(
#     from_product_type=dhanhq.INTRA,
#     exchange_segment=dhanhq.NSE,
#     position_type="LONG",
#     security_id="2885",
#     convert_qty=1,
#     to_product_type=dhanhq.CNC,
# )
```

### 7. Live MarketFeed

```python
from dhanhq import MarketFeed

_, context = get_client()

instruments = [
    (MarketFeed.NSE, "2885", MarketFeed.Ticker),   # RELIANCE LTP
    (MarketFeed.NSE, "1333", MarketFeed.Quote),    # HDFCBANK OHLC
    (MarketFeed.IDX, "13", MarketFeed.Ticker),     # NIFTY index
    (MarketFeed.NSE_FNO, "49081", MarketFeed.Full),
]

def on_message(instance, message):
    print(message)

feed = MarketFeed(context, instruments, "v2", on_message=on_message)
# feed.run_forever()
```

`MarketFeed` modes: `Ticker=15`, `Quote=17`, `Depth=19`, `Full=21`.  
Order status: `OrderUpdate(dhan_context)` then `connect_to_dhan_websocket_sync()`.  
20/200-level book: `FullDepth` (NSE_EQ / NSE_FNO only).

Snapshots (`ticker_data` / `ohlc_data` / `quote_data`) for point-in-time; WebSocket for live ticks.

### 8. Multi-leg / iron condor pattern

```python
expiry = nearest_expiry(dhan, 13)
chain_df, spot = fetch_chain_df(dhan, 13, expiry)
lot = get_lot_size(underlying="NIFTY")
orders = build_iron_condor_orders(chain_df, spot, lot_size=lot, short_offset=200, wing_width=200)
margin = check_margin_for_orders(dhan, orders)
result = place_multi_leg_orders(dhan, orders, dry_run=True)
```

Long iron condor legs: BUY far PE, SELL near PE, SELL near CE, BUY far CE. Sequential placement, not a native basket API.

### 9. History

```python
daily = history_daily_df(
    dhan,
    security_id="2885",
    exchange_segment=dhanhq.NSE,
    instrument_type="EQUITY",
    from_date="2026-01-01",
    to_date="2026-09-01",
    expiry_code=0,
)
# timestamps converted via dhan.convert_to_date_time

minutes = history_minute_df(
    dhan,
    security_id="2885",
    exchange_segment=dhanhq.NSE,
    instrument_type="EQUITY",
    from_date="2026-09-18",
    to_date="2026-09-21",
    interval=5,  # 1, 5, 15, 25, 60
)
```

Do not call a method named `historical_minute_data`. The current method is `intraday_minute_data`.

### 10. Forever / super orders (commented)

```python
# dhan.place_forever(
#     security_id="2885",
#     exchange_segment=dhanhq.NSE,
#     transaction_type=dhanhq.BUY,
#     product_type=dhanhq.CNC,
#     order_type=dhanhq.LIMIT,
#     quantity=1,
#     price=2400.0,
#     trigger_Price=2405.0,   # capital P
#     order_flag="SINGLE",
# )
# OCO uses price1, trigger_Price1, quantity1, order_flag="OCO"

# dhan.place_super_order(
#     security_id="2885",
#     exchange_segment=dhanhq.NSE,
#     transaction_type=dhanhq.BUY,
#     quantity=1,
#     order_type=dhanhq.LIMIT,
#     product_type=dhanhq.INTRA,
#     price=2450.0,
#     targetPrice=2500.0,
#     stopLossPrice=2420.0,
#     trailingJump=10.0,
# )
```

---

## Gotchas

- SDK wrapper is always `{"status": "success"|"failure", "remarks": ..., "data": ...}`. Success `data` shape varies by endpoint.
- Normalized chain fields (`ce_ltp`, `ce_oi`, `ce_iv`) are **your** names. Raw Dhan uses `last_price`, `oi`, `implied_volatility` under `data["oc"][strike_str]["ce"|"pe"]`.
- Quote APIs: **1 request/sec**, up to 1000 instruments.
- Option chain: **one unique request every 3 seconds**.
- Market orders via API → limit + MPP.
- Order APIs need static IP (`DH-911` if missing).
- Trading APIs are free for Dhan users; Data APIs need a plan (`DH-902` / `806`).
- Fund field is `availabelBalance` (missing third `e`).
- Derivative IDs and lot sizes change. Do not hardcode option `security_id` or NIFTY lot.
- `expiry_code` in docs: `0`, `1`, `2`. Prefer those.
- Forever trigger param is `trigger_Price`. Super target/SL are `targetPrice` / `stopLossPrice`.
- `convert_position` uses `from_product_type`, `to_product_type`, `convert_qty`, `position_type`.

### Data plan invalid (`DH-902` or `806`)

1. Log in at `web.dhan.co`
2. My Profile → Access DhanHQ APIs
3. Confirm `dataPlan` is active
4. Generate a fresh access token
5. Retest `ticker_data()` or `ohlc_data()`
6. If **orders** still fail, check static IP separately

---

## Rate limits

| API | Per second | Per minute | Per hour | Per day |
|-----|-----------:|-----------:|---------:|--------:|
| Order | 10 | 250 | 1000 | 7000 |
| Data | 5 | — | — | 100000 |
| Quote | 1 | Unlimited | Unlimited | Unlimited |
| Non-trading | 20 | Unlimited | Unlimited | Unlimited |

WebSocket: up to 5 connections/user, 5000 instruments/connection, 100 instruments per subscribe message.

---

## What not to copy

- Do not `import` `docs.Dhan_SRP` or parse this markdown from the app.
- Do not copy old Kite-style tokens (`26000` / `NSECM`). Dhan NIFTY index ID is `13` / `IDX_I`.
- Do not copy dual SDK constructors (`dhanhq(client_id, token)`). Use `DhanContext`.
- Do not copy raw REST LTP helpers, mibian Greeks, Heikin/Renko, Telegram, or hardcoded strike-step tables.
- Do not treat `Dhansrp` as a dependency. Copy `get_client`, `resolve_*`, chain normalize, `place_order_safe`, then delete these files.

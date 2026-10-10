# Delta Exchange India — product IDs and option discovery

Perpetual futures IDs below are stable enough to use as the **underlying ticker**. Option contract IDs are **not**. Resolve every option `product_id` at runtime.

API keys created on a Delta **India** account must be used only with India production APIs: `https://api.india.delta.exchange/v2`

## Perpetual underlyings

Field | BTCUSD | ETHUSD | SOLUSD | XAUTUSD
--- | --- | --- | --- | ---
Product ID | 27 | 3136 | 14823 | 131253
Chain asset | BTC | ETH | SOL | XAUT
Description | Bitcoin Perpetual | Ethereum Perpetual | Solana Perpetual | Tether Gold Token Perpetual
Contract Type | Perpetual Futures | Perpetual Futures | Perpetual Futures | Perpetual Futures
Trading Status | Operational | Operational | Operational | Operational
State | Live | Live | Live | Live
Contract Value | 0.001 BTC | 0.01 ETH | 1 SOL | 0.001 XAUT
Tick Size | 0.5 | 0.05 | 0.0001 | 0.01
Default Leverage | 200x | 200x | 100x | 100x
Maker Commission | 0.02% | 0.02% | 0.02% | 0.01%
Taker Commission | 0.05% | 0.05% | 0.05% | 0.05%
Initial Margin | 0.5% | 0.5% | 1% | 1%
Maintenance Margin | 0.25% | 0.25% | 0.5% | 0.5%
1 Lot Equals | 0.001 BTC | 0.01 ETH | 1 SOL | 0.001 XAUT

`XAUSD` is an alias of `XAUTUSD`: same product ID `131253`, same chain asset `XAUT`.

Example rows from Delta:

product_id | symbol | product_type | description
--- | --- | --- | ---
27 | BTCUSD | perpetual_futures | Bitcoin perpetual futures margined and settled in INR
3136 | ETHUSD | perpetual_futures | Ethereum perpetual futures margined and settled in INR
2000 | P-BTC-38100-230124 | put_options | BTC put (historical example — ID is not reusable)
5000 | C-BTC-55800-170224 | call_options | BTC call (historical example — ID is not reusable)

Do not copy those option IDs into a bot. They belong to expired contracts.

## Option symbol format

```
OptionType-UnderlyingAsset-StrikePrice-ExpiryDate(ddMMyy)
```

Example: `C-BTC-90000-310125` — BTC call, strike 90000, expiry 31 Jan 2025.

`P-BTC-77000-180926` — BTC put, strike 77000, expiry 18 Sep 2026.

Underlying for the chain query is the **asset** (`BTC`), not the perpetual symbol (`BTCUSD`).

## Option contract_value and tick_size

Do **not** reuse perpetual metadata blindly for option cash PnL.

- Read `contract_value` and `tick_size` from `GET /v2/products/{option_symbol}`.
- BTC options on India prod often also use `contract_value = 0.001`, so premium `705` points ≈ `$0.70` cash at quantity 1 — not `$705`.
- ETH / SOL / XAUT option multipliers differ. The product payload wins.

Cash formulas used by this bot:

```
leg_pnl_cash     = (mark - entry) * signed_size * contract_value
max_profit_cash  = net_credit_points * contract_value * quantity
```

## Method 1: You know the exact option symbol

**Endpoint:**

```
GET https://api.india.delta.exchange/v2/products/{symbol}
```

```bash
curl -X GET "https://api.india.delta.exchange/v2/products/C-BTC-90000-310125" \
  -H "Accept: application/json"
```

Key fields: `id` (product ID), `symbol`, `contract_type`, `strike_price`, `state`, `trading_status`, `tick_size`, `contract_value`.

## Method 2: Discover from the option chain

**Endpoint:**

```
GET https://api.india.delta.exchange/v2/tickers?contract_types=call_options,put_options&underlying_asset_symbols={ASSET}&expiry_date={DD-MM-YYYY}
```

```bash
curl -X GET "https://api.india.delta.exchange/v2/tickers?contract_types=call_options,put_options&underlying_asset_symbols=BTC&expiry_date=18-09-2026" \
  -H "Accept: application/json"
```

Each row includes `symbol`, `product_id`, `strike_price`, `contract_type`, `mark_price`. Config `FIXED_DATE` is ISO `YYYY-MM-DD`; convert to `DD-MM-YYYY` before this query.

## Python (copy from Delta_SRP.PY)

```python
from Delta_SRP import DeltaSRP, fetch_option_chain, enrich_option_product, map_underlying_to_asset

client = DeltaSRP.from_env()
asset = map_underlying_to_asset("BTCUSD")  # BTC
rows = fetch_option_chain(client, asset, "18-09-2026")
product = enrich_option_product(client, "P-BTC-77000-180926")
product_id = product["id"]
contract_value = float(product["contract_value"])
tick_size = float(product["tick_size"])
```

Use `product_id` on `POST /v2/orders`, `GET /v2/positions`, `GET /v2/orders`. For a four-leg condor, resolve **four** IDs this way and place **four sequential** orders — the batch endpoint is one product per request.

# SRP Delta Helper — options copy-pack

Use this file with [Delta_SRP.PY](Delta_SRP.PY) and [PRODUCT_ID_DETAILS.md](PRODUCT_ID_DETAILS.md) as the portable Delta Exchange **India** reference for another options-strategy repo (bullish condor, bearish condor, iron condor).

A live six-file bot (`main.py`, `config.yaml`, `.env`, `stop.py`, `requirements.txt`, `architecture.md`) must **reimplement** what it needs inside `main.py`. Do **not** import `docs/` from production.

India production REST: `https://api.india.delta.exchange/v2`

## What to copy into the next repo

1. `docs/Delta_SRP.PY`
2. `docs/srp_delta_helper.md` (this file)
3. `docs/PRODUCT_ID_DETAILS.md`
4. `docs/project_requirements.md` (build prompt; swap bullish strikes for bearish)

```python
from Delta_SRP import (
    DeltaSRP,
    OptionLeg,
    calculate_credit_condor_payoff,
    calculate_combined_pnl,
    classify_restart_action,
    discover_option_expiries,
    execute_four_leg_entry,
    fetch_option_chain,
    flatten_four_legs,
    map_underlying_to_asset,
    parse_option_symbol,
    resolve_tp_sl_targets,
    select_expiry,
    select_manual_strikes,
    to_delta_expiry_str,
)
```

If the filename is `Delta_SRP.PY`, rename the copy to `Delta_SRP.py` in the target repo so the import works.

## Hard rules learned on live India options

These are not style preferences. Breaking them stacked working orders or fired a stop in about one second.

1. **Never hardcode option `product_id`.** Resolve at runtime from `GET /products/{symbol}` or the chain ticker. Perpetual IDs (`BTCUSD=27`) are stable; option IDs are not.
2. **Chain asset is `BTC`, not `BTCUSD`.** Query `underlying_asset_symbols=BTC` and `expiry_date=DD-MM-YYYY`.
3. **`FIXED_DATE` never rolls.** Config uses ISO `YYYY-MM-DD`; Delta uses `DD-MM-YYYY`. If that expiry is missing, stay in scan.
4. **`MANUAL` strikes are exact listed contracts.** Do not snap to ATM. If `P-BTC-77000-…` is not on the chain, skip — do not substitute `76500`.
5. **A condor is four products.** Delta batch orders take **one** `product_id`. Place four sequential `POST /v2/orders`.
6. **Default to `market_order`.** Unfilled live **limits must be cancelled** before retry, timeout, unwind, or shutdown. Timeouts that leave working limits will stack size.
7. **Buy wings first:** `PUT_LONG` → `CALL_LONG` → `PUT_SHORT` → `CALL_SHORT`.
8. **One structure at a time.** `allow_reentry_after_close: false` in the same process. On **process restart**, if the exchange is **flat**, start a new idle cycle — do not reload `STOPPED` + `entry_locked` forever.
9. **TP/SL are combined and frozen at fill.** Live input is the **sum of all four legs**, never spot. Modes: `PERCENT_OF_MAX_PROFIT`, `PERCENT_OF_MAX_LOSS`, `ABSOLUTE_PNL`.
10. **Cash PnL uses live `contract_value`.** BTC options on India prod often use `0.001`. Premium `705` points is about `$0.70`, not `$705`. An `ABSOLUTE_PNL` TP of `$10` cannot hit if max profit is `$0.70`.
11. **Flatten only the four stored product IDs** with `reduce_only`. Do not exchange-wide close-all.
12. **`DELTA_MODE=trade`** is required for mutations. Dry-run first.

## Credit condor family (bullish vs bearish)

The **product set is the same**. “Bullish” vs “bearish” is only **where you place the body relative to spot**.

| Internal name | Action | Option | Role |
|---|---|---|---|
| `PUT_LONG` | BUY | Lower PUT | Caps downside |
| `PUT_SHORT` | SELL | Higher PUT | Credit, lower body |
| `CALL_SHORT` | SELL | Lower CALL | Credit, upper body |
| `CALL_LONG` | BUY | Higher CALL | Caps upside |

Invariant (both strategies):

```
PUT_LONG strike  <  PUT_SHORT strike  <  CALL_SHORT strike  <  CALL_LONG strike
```

| | Bullish condor | Bearish condor |
|---|---|---|
| Intent | Profitable region around or slightly **above** spot | Profitable region around or slightly **below** spot |
| Body | Short put closer to / below spot; short call further above | Short call closer to / above spot; short put further below |
| Example at ~79000 | BUY 77000 P, SELL 78000 P, SELL 79000 C, BUY 80000 C | BUY 76000 P, SELL 77000 P, SELL 78000 C, BUY 79000 C |
| What to change | `manual_strikes` / distances | Same keys, different numbers |
| What not to change | Client, expiry, sequential market entry, cancel-on-timeout, combined TP/SL, reconcile | Same |

Payoff (credit):

```
max_profit_cash = net_credit_points * contract_value * quantity
max_loss_cash   = wider_wing_points * contract_value * quantity - max_profit_cash
lower_BE        = short_put_strike - net_credit_points
upper_BE        = short_call_strike + net_credit_points
```

Combined live PnL:

```
leg_pnl = (mark - entry) * signed_size * contract_value
strategy_pnl = sum(four legs)
```

Prefer exchange `unrealized_pnl` when present; patch short-option sign when size is negative (`_patch_short_option_pnl` in [Delta_SRP.PY](Delta_SRP.PY)).

## Recommended options algo sequence

1. Load config + `DeltaSRP.from_env()` (`DELTA_ENV=india_prod`).
2. `map_underlying_to_asset("BTCUSD")` → `BTC`.
3. Discover expiries or honor `FIXED_DATE` via `select_expiry(...)`.
4. `fetch_option_chain(client, "BTC", "18-09-2026")`.
5. `select_manual_strikes(...)` — fail closed if a strike is missing.
6. Resolve each `product_id` with `get_product(symbol)`; read `contract_value` / `tick_size` from that payload.
7. `calculate_credit_condor_payoff` then `resolve_tp_sl_targets` (freeze after fills).
8. Dry-run `execute_four_leg_entry` (`order_type="market_order"`, `retry_failed_leg=False`).
9. Live: same path. Cancel unfilled. Unwind if a later wing/body fails.
10. Each poll: `calculate_combined_pnl`. If combined ≥ TP or ≤ SL, `flatten_four_legs`.
11. On process start: `fetch_option_positions` + `classify_restart_action`. Resume / unwind / **clear idle if flat**.

```python
client = DeltaSRP.from_env()
asset = map_underlying_to_asset("BTCUSD")
expiries = discover_option_expiries(client, asset)
chosen, err = select_expiry(expiries, mode="FIXED_DATE", expiry_date="2026-09-18")
if err:
    raise SystemExit(err)
expiry = to_delta_expiry_str(chosen)
rows = fetch_option_chain(client, asset, expiry)
puts = [float(r["strike_price"]) for r in rows if str(r.get("contract_type")) == "put_options"]
calls = [float(r["strike_price"]) for r in rows if str(r.get("contract_type")) == "call_options"]
strikes = select_manual_strikes(
    put_long=77000, put_short=78000, call_short=79000, call_long=80000,
    put_strikes=puts, call_strikes=calls,
)
```

## Environment

- `DELTA_API_KEY` / `DELTA_API_SECRET` — India account keys only on prod APIs
- `DELTA_ENV`: `india_prod` (default), `india_testnet`, `india_devnet`
- `DELTA_MODE`: `read` or `trade` (mutations require `trade`)
- `DELTA_DEBUG`: request debug logs
- `DELTA_AUDIT_OFF`: disable audit log in trade mode

## REST client methods you will use most (options)

Market:

- `get_product(symbol)` — option `id`, `tick_size`, `contract_value`
- `get_ticker(symbol)` — underlying or option mark
- `list_tickers(contract_types=["call_options","put_options"], underlying_asset_symbols=["BTC"])`
- `get_options_chain(underlying, expiry_date)` — `underlying` is `BTC`; `expiry_date` is `DD-MM-YYYY`

Account:

- `get_margined_positions(contract_types=["call_options","put_options"])`
- `get_wallet_balances()`
- `get_open_orders(...)` / `get_order_by_id(...)`

Trading:

- `place_order(...)` / `place_market_order(...)` — one product per call
- `cancel_order(product_id=..., order_id=...)` — required on timeout
- `set_product_leverage(product_id=..., leverage="100")`

Do **not** use `place_batch_orders` for a four-leg condor.

## Four-leg primitives in Delta_SRP.PY

| Helper | Role |
|---|---|
| `parse_option_symbol` | `C-BTC-77000-180926` → type, asset, strike, expiry |
| `select_expiry` | `FIXED_DATE` / `NEAREST` / `NEXT_AVAILABLE` |
| `select_manual_strikes` | Exact listed strikes; no ATM snap |
| `find_contract` / `row_to_option_leg` | Chain row → `OptionLeg` |
| `calculate_credit_condor_payoff` | Combined max profit / max loss (cash) |
| `resolve_tp_sl_targets` | Freeze TP/SL at fill |
| `calculate_combined_pnl` | Sum of four legs |
| `execute_four_leg_entry` | Sequential market, cancel, unwind on fail |
| `flatten_four_legs` | `reduce_only` close, shorts first |
| `classify_restart_action` | resume / unwind_partial / clear_idle / lock_unverified |

## Config-first usage (generic runner, not the condor bot)

[Delta_SRP.PY](Delta_SRP.PY) can still run a **generic** `config.yaml` with `option_legs` or futures. That runner is **not** the four-leg condor bot. The condor bot uses the nested schema in this strategy’s `config.yaml` (`strategy.expiry_selection`, `strategy.manual_strikes`, `exit.take_profit.mode`).

```bash
DELTA_MODE=trade DELTA_RUN_CONFIG=true DELTA_CONFIG_PATH=config.yaml python3 Delta_SRP.py
```

If config run mode is off, the file prints trading status only.

---

## Futures-only examples

The following examples are **perpetual futures**, not options. Do not copy them into a condor bot as the entry path.

### A) ETH futures limit order

```python
result = client.place_order(
    size=1,
    side="buy",
    order_type="limit_order",
    product_symbol="ETHUSD",
    limit_price="3500.0",
    time_in_force="gtc",
    post_only=True,
    dry_run=False,
)
```

### B) ETH futures market order

```python
result = client.place_market_order(
    size=1,
    side="buy",
    product_symbol="ETHUSD",
    reduce_only=False,
    dry_run=False,
)
```

### C) Two single-product option orders (not a condor)

If you only need two unrelated contracts, place them individually. A condor still needs the sequential helper above (four products, cancel, unwind).

```python
res_call = client.place_order(
    size=1, side="buy", order_type="market_order",
    product_symbol="C-BTC-80000-300826", client_order_id="btc_call_leg_001",
)
res_put = client.place_order(
    size=1, side="buy", order_type="market_order",
    product_symbol="P-BTC-80000-300826", client_order_id="btc_put_leg_001",
)
```

### D) Dry-run before live send

```python
draft = client.place_order(
    size=1, side="sell", order_type="market_order",
    product_symbol="BTCUSD", dry_run=True,
)
live = client.place_order(
    size=1, side="sell", order_type="market_order",
    product_symbol="BTCUSD", dry_run=False,
)
```

## Error handling

```python
from Delta_SRP import DeltaAPIError

try:
    out = client.place_market_order(size=1, side="buy", product_symbol="SOLUSD")
except DeltaAPIError as exc:
    print("Delta error:", exc)
```

## Notes

- Mutation calls require `DELTA_MODE=trade`.
- Batch endpoints support up to 50 orders **of the same product**.
- For market orders, do not send `limit_price`.
- `start_time_us` / `end_time_us` are microseconds epoch where applicable.
- Keep one source of truth for `Delta_SRP.PY` across strategy repos.

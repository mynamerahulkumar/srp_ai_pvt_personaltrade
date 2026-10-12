"""Offline replay of the paired BTCUSD / ETHUSD breakout.

The signal is BTC only. ETH is entered in the same direction and closed
when the BTC leg hits take-profit or stop-loss. Settings come from
test.yaml. This script does not place orders and does not import the bot.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / "test.yaml"


@dataclass
class Bar:
    dt: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class Trade:
    entry_dt: datetime
    exit_dt: datetime
    side: str
    result: str
    btc_pnl: float
    eth_pnl: float

    @property
    def hours(self) -> int:
        return int((self.exit_dt - self.entry_dt).total_seconds() // 3600)

    @property
    def combined(self) -> float:
        return self.btc_pnl + self.eth_pnl


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load_settings(path: Path) -> dict:
    if not path.exists():
        fail(f"Missing {path.name}. Create it before running the backtest.")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        fail(f"{path.name} must be a mapping of keys to values.")
    return raw


def require_mapping(raw: dict, key: str) -> dict:
    value = raw.get(key)
    if not isinstance(value, dict):
        fail(f"test.yaml {key} must be a mapping.")
    return value


def round_to_tick(price: float, tick_size: float) -> float:
    """Snap a price to the product tick using half-up rounding."""
    tick = Decimal(str(tick_size))
    if tick <= 0:
        return price
    steps = (Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_HALF_UP)
    return float(steps * tick)


def load_csv(path: Path) -> list[Bar]:
    if not path.exists():
        fail(f"Missing candle file {path}")
    bars: list[Bar] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"datetime", "open", "high", "low", "close"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            fail(f"{path} must have columns datetime, open, high, low, close.")
        for row in reader:
            bars.append(
                Bar(
                    dt=datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S"),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            )
    if not bars:
        fail(f"{path} has no candles.")
    return bars


def align(btc: list[Bar], eth: list[Bar]) -> tuple[list[Bar], list[Bar]]:
    btc_map = {bar.dt: bar for bar in btc}
    eth_map = {bar.dt: bar for bar in eth}
    only_btc = sorted(set(btc_map) - set(eth_map))
    only_eth = sorted(set(eth_map) - set(btc_map))
    if only_btc or only_eth:
        sample = [dt.isoformat(sep=" ") for dt in (only_btc + only_eth)[:5]]
        fail(
            "BTC and ETH candles do not share every timestamp. "
            f"BTC-only {len(only_btc)}, ETH-only {len(only_eth)}. Sample: {sample}"
        )
    times = sorted(btc_map)
    return [btc_map[dt] for dt in times], [eth_map[dt] for dt in times]


def breakout_levels(range_high: float, range_low: float, confirmation: dict, tick: float) -> tuple[float, float]:
    enabled = bool(confirmation.get("enabled", True))
    value = float(confirmation.get("value", 0))
    mode = str(confirmation.get("mode", "POINTS")).strip().upper()
    if not enabled or value == 0:
        long_level, short_level = range_high, range_low
    elif mode == "PERCENT":
        long_level = range_high * (1 + value / 100.0)
        short_level = range_low * (1 - value / 100.0)
    elif mode == "POINTS":
        long_level = range_high + value
        short_level = range_low - value
    else:
        fail("confirmation.mode must be POINTS or PERCENT.")
    return round_to_tick(long_level, tick), round_to_tick(short_level, tick)


def exit_prices(
    direction: str,
    entry: float,
    quantity: int,
    contract_value: float,
    take_profit: dict,
    stop_loss: dict,
    tick: float,
) -> tuple[float | None, float | None]:
    """BTC take-profit and stop prices, matching the bot's exit conversion."""

    def from_pnl(dollars: float, profit: bool) -> float:
        move = dollars / (quantity * contract_value)
        if direction == "LONG":
            return entry + move if profit else entry - move
        return entry - move if profit else entry + move

    tp_price: float | None = None
    sl_price: float | None = None
    if bool(take_profit.get("enabled", True)):
        mode = str(take_profit.get("mode", "PNL")).strip().upper()
        value = float(take_profit["value"])
        if mode == "PNL":
            tp_price = from_pnl(value, profit=True)
        elif mode == "PERCENT":
            tp_price = entry * (1 + value / 100.0) if direction == "LONG" else entry * (1 - value / 100.0)
        else:
            fail("take_profit.mode must be PNL or PERCENT.")
        tp_price = round_to_tick(tp_price, tick)
    if bool(stop_loss.get("enabled", True)):
        mode = str(stop_loss.get("mode", "PNL")).strip().upper()
        value = float(stop_loss["value"])
        if mode == "PNL":
            sl_price = from_pnl(value, profit=False)
        elif mode == "PERCENT":
            sl_price = entry * (1 - value / 100.0) if direction == "LONG" else entry * (1 + value / 100.0)
        else:
            fail("stop_loss.mode must be PNL or PERCENT.")
        sl_price = round_to_tick(sl_price, tick)
    return tp_price, sl_price


def follower_price(signal_bar: Bar, pair_bar: Bar, target: float, upward: bool) -> float:
    """ETH price at the same fraction of the bar as the BTC target."""
    if upward:
        if signal_bar.open >= target:
            return pair_bar.open
        span = signal_bar.high - signal_bar.open
        frac = 0.0 if span <= 0 else min(max((target - signal_bar.open) / span, 0.0), 1.0)
        return pair_bar.open + frac * (pair_bar.high - pair_bar.open)
    if signal_bar.open <= target:
        return pair_bar.open
    span = signal_bar.open - signal_bar.low
    frac = 0.0 if span <= 0 else min(max((signal_bar.open - target) / span, 0.0), 1.0)
    return pair_bar.open - frac * (pair_bar.open - pair_bar.low)


def signal_on_bar(
    btc: list[Bar],
    index: int,
    lookback: int,
    direction: str,
    confirmation: dict,
    tick: float,
) -> tuple[str, float] | None:
    close_confirm = bool(confirmation.get("candle_close_confirmation", False))
    if index < lookback:
        return None
    window = btc[index - lookback : index]
    range_high = max(bar.high for bar in window)
    range_low = min(bar.low for bar in window)
    long_level, short_level = breakout_levels(range_high, range_low, confirmation, tick)
    bar = btc[index]
    if close_confirm:
        long_hit = bar.close > long_level
        short_hit = bar.close < short_level
    else:
        long_hit = bar.high > long_level
        short_hit = bar.low < short_level
    if long_hit and short_hit:
        return None
    if direction == "LONG":
        short_hit = False
    elif direction == "SHORT":
        long_hit = False
    if long_hit:
        return "LONG", long_level
    if short_hit:
        return "SHORT", short_level
    return None


def entry_prices(side: str, level: float, btc_bar: Bar, eth_bar: Bar, close_confirm: bool) -> tuple[float, float] | None:
    if close_confirm:
        return btc_bar.close, eth_bar.close
    if side == "LONG":
        if btc_bar.open >= level:
            return btc_bar.open, eth_bar.open
        if btc_bar.high <= btc_bar.open:
            return None
        return level, follower_price(btc_bar, eth_bar, level, upward=True)
    if btc_bar.open <= level:
        return btc_bar.open, eth_bar.open
    if btc_bar.open <= btc_bar.low:
        return None
    return level, follower_price(btc_bar, eth_bar, level, upward=False)


def leg_pnl(side: str, entry: float, exit_price: float, quantity: int, contract_value: float) -> float:
    points = (exit_price - entry) if side == "LONG" else (entry - exit_price)
    return points * quantity * contract_value


def find_exit(
    side: str,
    entry_index: int,
    btc_entry: float,
    eth_entry: float,
    tp_price: float | None,
    sl_price: float | None,
    btc: list[Bar],
    eth: list[Bar],
    allow_entry_bar_target: bool,
) -> tuple[int, str, float, float]:
    """Walk forward until BTC hits its target or stop. ETH is marked there."""
    start = entry_index if allow_entry_bar_target else entry_index + 1
    for index in range(start, len(btc)):
        check_stop = index > entry_index
        bar, pair = btc[index], eth[index]
        if side == "LONG":
            hit_sl = check_stop and sl_price is not None and bar.low <= sl_price
            hit_tp = tp_price is not None and bar.high >= tp_price
        else:
            hit_sl = check_stop and sl_price is not None and bar.high >= sl_price
            hit_tp = tp_price is not None and bar.low <= tp_price
        if hit_sl and sl_price is not None:
            return index, "SL", sl_price, follower_price(bar, pair, sl_price, upward=side == "SHORT")
        if hit_tp and tp_price is not None:
            return index, "TP", tp_price, follower_price(bar, pair, tp_price, upward=side == "LONG")
    last = len(btc) - 1
    return last, "OPEN", btc[last].close, eth[last].close


def run(settings: dict) -> list[Trade]:
    strategy = require_mapping(settings, "strategy")
    confirmation = require_mapping(settings, "confirmation")
    take_profit = require_mapping(settings, "take_profit")
    stop_loss = require_mapping(settings, "stop_loss")
    products = require_mapping(settings, "products")
    signal = str(settings.get("signal_symbol", "BTCUSD")).strip().upper()
    pair = str(settings.get("pair_symbol", "ETHUSD")).strip().upper()
    if signal not in products or pair not in products:
        fail("products must include signal_symbol and pair_symbol.")
    btc_spec = products[signal]
    quantity = int(settings.get("order_size", 1))
    lookback = int(strategy.get("breakout_lookback_candles", 1))
    direction = str(strategy.get("direction", "BOTH")).strip().upper()
    if quantity < 1:
        fail("order_size must be a positive integer.")
    if lookback < 1:
        fail("breakout_lookback_candles must be >= 1.")
    if direction not in {"LONG", "SHORT", "BOTH"}:
        fail("strategy.direction must be LONG, SHORT, or BOTH.")
    contract_value = float(btc_spec["contract_value"])
    tick = float(btc_spec["tick_size"])
    pair_value = float(products[pair]["contract_value"])
    if contract_value <= 0 or pair_value <= 0 or tick <= 0:
        fail("contract_value and tick_size must be > 0.")

    btc_path = ROOT / str(settings.get("btc_csv", "data/historical/BTCUSD_1h.csv"))
    eth_path = ROOT / str(settings.get("eth_csv", "data/historical/ETHUSD_1h.csv"))
    btc, eth = align(load_csv(btc_path), load_csv(eth_path))
    close_confirm = bool(confirmation.get("candle_close_confirmation", False))

    trades: list[Trade] = []
    index = lookback
    while index < len(btc):
        found = signal_on_bar(btc, index, lookback, direction, confirmation, tick)
        if found is None:
            index += 1
            continue
        side, level = found
        filled = entry_prices(side, level, btc[index], eth[index], close_confirm)
        if filled is None:
            index += 1
            continue
        btc_entry, eth_entry = filled
        tp_price, sl_price = exit_prices(side, btc_entry, quantity, contract_value, take_profit, stop_loss, tick)
        exit_index, result, btc_exit, eth_exit = find_exit(
            side,
            index,
            btc_entry,
            eth_entry,
            tp_price,
            sl_price,
            btc,
            eth,
            allow_entry_bar_target=not close_confirm,
        )
        trades.append(
            Trade(
                entry_dt=btc[index].dt,
                exit_dt=btc[exit_index].dt,
                side=side,
                result=result,
                btc_pnl=leg_pnl(side, btc_entry, btc_exit, quantity, contract_value),
                eth_pnl=leg_pnl(side, eth_entry, eth_exit, quantity, pair_value),
            )
        )
        index = exit_index + 1
    return trades


def print_report(settings: dict, trades: list[Trade]) -> None:
    closed = [trade for trade in trades if trade.result != "OPEN"]
    take_profits = sum(1 for trade in closed if trade.result == "TP")
    stop_losses = sum(1 for trade in closed if trade.result == "SL")
    longs = sum(1 for trade in trades if trade.side == "LONG")
    shorts = sum(1 for trade in trades if trade.side == "SHORT")
    btc_sum = sum(trade.btc_pnl for trade in closed)
    eth_sum = sum(trade.eth_pnl for trade in closed)
    win_rate = (100.0 * take_profits / len(closed)) if closed else 0.0
    daily_limit = float(settings.get("max_loss_per_day_dollar", 0))
    by_day: dict = defaultdict(float)
    for trade in closed:
        by_day[trade.exit_dt.date()] += trade.combined
    breach_days = [(day, pnl) for day, pnl in sorted(by_day.items()) if daily_limit > 0 and pnl <= -daily_limit]

    print("Paired BTC / ETH breakout backtest")
    print(f"Settings file: {SETTINGS_PATH.name}")
    print(
        f"Signal {settings.get('signal_symbol', 'BTCUSD')}  "
        f"pair {settings.get('pair_symbol', 'ETHUSD')}  "
        f"size {settings.get('order_size')}"
    )
    print(f"Bars are aligned hourly candles. One pair is open at a time.")
    print()
    print(f"Trades            {len(trades)}  ({longs} long, {shorts} short)")
    print(f"Closed            {len(closed)}")
    print(f"Open at end       {len(trades) - len(closed)}")
    print(f"Take-profits      {take_profits}")
    print(f"Stop-losses       {stop_losses}")
    print(f"Win rate          {win_rate:.1f}%")
    print(f"BTC closed PnL    {btc_sum:+.2f}")
    print(f"ETH closed PnL    {eth_sum:+.2f}")
    print(f"Combined closed   {btc_sum + eth_sum:+.2f}")
    print(f"Exit days at or beyond -{daily_limit:.2f} combined: {len(breach_days)}")
    for day, pnl in breach_days:
        print(f"  {day.isoformat()}  {pnl:+.2f}")
    print()
    print(f"{'#':>3}  {'Entry':<16}  {'Exit':<16}  {'Side':<5}  {'Result':<6}  {'Hours':>5}  {'BTC':>8}  {'ETH':>8}  {'Both':>8}")
    for number, trade in enumerate(trades, start=1):
        print(
            f"{number:>3}  {trade.entry_dt:%Y-%m-%d %H:%M}  {trade.exit_dt:%Y-%m-%d %H:%M}  "
            f"{trade.side:<5}  {trade.result:<6}  {trade.hours:>5}  "
            f"{trade.btc_pnl:>+8.2f}  {trade.eth_pnl:>+8.2f}  {trade.combined:>+8.2f}"
        )


def main() -> None:
    settings = load_settings(SETTINGS_PATH)
    print_report(settings, run(settings))


if __name__ == "__main__":
    main()

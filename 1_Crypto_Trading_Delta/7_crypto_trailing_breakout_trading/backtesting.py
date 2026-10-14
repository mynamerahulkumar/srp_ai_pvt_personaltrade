#!/usr/bin/env python3
"""Replay the breakout rules on saved BTCUSD candles.

Reads test.yaml through the same loader as the bot. The search uses
15 Sep–7 Oct to choose settings and scores 8–14 Oct once. The order log
is only a check on the last seven days of this bot's size-5 BTCUSD fills.
"""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

import main

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "historical"
ORDER_LOG = BASE_DIR / "data" / "Delta-TransactionLog-OrderHistory.csv"
TEST_CONFIG = BASE_DIR / "test.yaml"
CONTRACT_VALUE = 0.001
TRAIN_END = date(2026, 10, 8)
HOLDOUT_END = date(2026, 10, 15)
MIN_TRAIN_TRADES = 20

CANDLES = {
    "30m": DATA_DIR / "BTCUSD_30m.csv",
    "1h": DATA_DIR / "BTCUSD_1h.csv",
    "4h": DATA_DIR / "BTCUSD_4h.csv",
    "1d": DATA_DIR / "BTCUSD_1d.csv",
}


@dataclass
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class Trade:
    direction: str
    entry_time: datetime
    exit_time: datetime
    net: float
    reason: str


def load_bars(path: Path) -> list[Bar]:
    bars: list[Bar] = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            bars.append(
                Bar(
                    time=datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S"),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            )
    return bars


def _fee(price: float, quantity: int) -> float:
    return main.estimate_fill_fee(price, quantity, CONTRACT_VALUE)


def _points(dollars: float, quantity: int) -> float:
    return dollars / (quantity * CONTRACT_VALUE)


def simulate(bars: list[Bar], cfg: main.Config) -> list[Trade]:
    """One position, next-candle wait, fee-aware -$10 day lock.

    An already-open stop is tested before that bar can raise the trail.
    The entry bar itself is not used as a stop, because its other wick
    can print before the breakout.
    """
    quantity = cfg.order_size
    confirm = cfg.confirmation.value if cfg.confirmation.enabled else 0.0
    lookback = cfg.strategy.breakout_lookback_candles
    close_confirm = cfg.confirmation.candle_close_confirmation
    sl_dollars = abs(cfg.stop_loss.value)
    tp_dollars = abs(cfg.take_profit.value)
    day_limit = cfg.max_loss_per_day_dollar
    max_orders = cfg.max_orders_per_day
    direction_filter = cfg.strategy.direction

    trades: list[Trade] = []
    position: dict | None = None
    cooldown_index = -1
    day: date | None = None
    day_price = 0.0
    day_fees = 0.0
    day_orders = 0

    start = lookback + (1 if close_confirm else 0)
    for index in range(start, len(bars)):
        bar = bars[index]
        if day != bar.time.date():
            day = bar.time.date()
            day_price = 0.0
            day_fees = 0.0
            day_orders = 0

        if position is not None:
            entry = position["entry"]
            peak = position["peak"]
            side = position["side"]
            stop_dollars = main.trail_stop_pnl(
                peak, sl_dollars, cfg.trailing.distance, cfg.trailing.activate_profit
            ) if cfg.trailing.enabled else -sl_dollars
            if side == "LONG":
                stop_price = entry + _points(stop_dollars, quantity)
                target = entry + _points(tp_dollars, quantity)
                hit_stop = bar.low <= stop_price
                hit_target = bar.high >= target
                favorable = bar.high - entry
                open_pnl = (bar.close - entry) * quantity * CONTRACT_VALUE
            else:
                stop_price = entry - _points(stop_dollars, quantity)
                target = entry - _points(tp_dollars, quantity)
                hit_stop = bar.high >= stop_price
                hit_target = bar.low <= target
                favorable = entry - bar.low
                open_pnl = (entry - bar.close) * quantity * CONTRACT_VALUE
            exit_price = None
            reason = None
            if hit_stop:
                exit_price = stop_price
                reason = "SL"
            elif hit_target:
                exit_price = target
                reason = "TP"
            elif day_price - day_fees + open_pnl <= -day_limit:
                exit_price = bar.close
                reason = "DAY"
            else:
                move = favorable * quantity * CONTRACT_VALUE
                if move > peak:
                    position["peak"] = move
            if exit_price is not None:
                points = (exit_price - entry) if side == "LONG" else (entry - exit_price)
                gross = points * quantity * CONTRACT_VALUE
                day_fees += _fee(exit_price, quantity)
                net = gross - position["entry_fee"] - _fee(exit_price, quantity)
                day_price += gross
                trades.append(
                    Trade(side, position["entry_time"], bar.time, net, reason or "SL")
                )
                position = None
                cooldown_index = index

        if position is not None or index <= cooldown_index or day_orders >= max_orders:
            continue
        closed_net = day_price - day_fees
        round_trip = _fee(bar.open, quantity) * 2
        if closed_net <= -day_limit or closed_net - sl_dollars - round_trip <= -day_limit:
            continue

        if close_confirm:
            signal_bar = bars[index - 1]
            window = bars[index - 1 - lookback : index - 1]
            if len(window) < lookback:
                continue
            range_high = max(item.high for item in window)
            range_low = min(item.low for item in window)
            long_hit = signal_bar.close >= range_high + confirm
            short_hit = signal_bar.close <= range_low - confirm
            long_price = signal_bar.close
            short_price = signal_bar.close
        else:
            window = bars[index - lookback : index]
            range_high = max(item.high for item in window)
            range_low = min(item.low for item in window)
            long_price = range_high + confirm
            short_price = range_low - confirm
            long_hit = bar.high >= long_price
            short_hit = bar.low <= short_price
        if direction_filter == "LONG":
            short_hit = False
        elif direction_filter == "SHORT":
            long_hit = False
        if long_hit and short_hit:
            continue
        if not long_hit and not short_hit:
            continue
        side = "LONG" if long_hit else "SHORT"
        entry = long_price if long_hit else short_price
        entry_fee = _fee(entry, quantity)
        day_fees += entry_fee
        day_orders += 1
        position = {
            "side": side,
            "entry": entry,
            "entry_time": bar.time,
            "entry_fee": entry_fee,
            "peak": 0.0,
        }
    return trades


def _sum(trades: list[Trade], start: date, end: date) -> tuple[int, float, int]:
    chosen = [trade for trade in trades if start <= trade.entry_time.date() < end]
    wins = sum(1 for trade in chosen if trade.net > 0)
    return len(chosen), sum(trade.net for trade in chosen), wins


def search(bars_by_tf: dict[str, list[Bar]]) -> tuple[dict, list[Trade], float, int]:
    """Best training net among candidates with at least 20 training trades.

    Returns the chosen settings, that run's trades, the training net, and
    the training trade count. A non-positive search keeps the current file.
    """
    current = main.load_config(BASE_DIR / "config.yaml")
    base = {
        "timeframe": current.timeframe,
        "order_size": current.order_size,
        "confirmation": current.confirmation.value,
        "candle_close": current.confirmation.candle_close_confirmation,
        "direction": current.strategy.direction,
        "lookback": current.strategy.breakout_lookback_candles,
        "stop": current.stop_loss.value,
        "activate": current.trailing.activate_profit,
        "distance": current.trailing.distance,
        "target": current.take_profit.value,
    }
    grid = []
    for timeframe in ("30m", "1h", "4h"):
        for confirmation in (150, 300, 450):
            for candle_close in (False, True):
                for direction in ("BOTH", "LONG", "SHORT"):
                    for lookback in (1, 2):
                        for order_size in (1, 2, 5):
                            for stop, activate, distance, target in ((3, 4, 3, 10), (5, 6, 4, 12)):
                                grid.append(
                                    {
                                        "timeframe": timeframe,
                                        "order_size": order_size,
                                        "confirmation": confirmation,
                                        "candle_close": candle_close,
                                        "direction": direction,
                                        "lookback": lookback,
                                        "stop": stop,
                                        "activate": activate,
                                        "distance": distance,
                                        "target": target,
                                    }
                                )

    best_net = 0.0
    best = base
    best_trades: list[Trade] = []
    best_count = 0
    ranked: list[tuple[float, int, dict]] = []
    for params in grid:
        cfg = _config_from(current, params)
        trades = simulate(bars_by_tf[params["timeframe"]], cfg)
        count, net, _wins = _sum(trades, date(2026, 9, 15), TRAIN_END)
        if count < MIN_TRAIN_TRADES:
            continue
        ranked.append((net, count, params))
        if net > best_net:
            best_net = net
            best = params
            best_trades = trades
            best_count = count
    ranked.sort(key=lambda item: item[0], reverse=True)
    print("TOP TRAINING RESULTS")
    for net, count, params in ranked[:8]:
        print(f"  train {net:+8.2f}  n={count:3}  {_label(params)}")
    if best_count == 0:
        print("No candidate had 20 training trades. Keeping the current settings.")
        best_trades = simulate(bars_by_tf[base["timeframe"]], current)
        best_count, best_net, _wins = _sum(best_trades, date(2026, 9, 15), TRAIN_END)
        return base, best_trades, best_net, best_count
    if best_net <= 0:
        print("Every candidate lost on the training window. Keeping the current settings.")
        best_trades = simulate(bars_by_tf[base["timeframe"]], current)
        best_count, best_net, _wins = _sum(best_trades, date(2026, 9, 15), TRAIN_END)
        return base, best_trades, best_net, best_count
    return best, best_trades, best_net, best_count


def _config_from(current: main.Config, params: dict) -> main.Config:
    return replace(
        current,
        timeframe=params["timeframe"],
        order_size=int(params["order_size"]),
        confirmation=replace(
            current.confirmation,
            value=float(params["confirmation"]),
            candle_close_confirmation=bool(params["candle_close"]),
        ),
        strategy=replace(
            current.strategy,
            direction=params["direction"],
            breakout_lookback_candles=int(params["lookback"]),
        ),
        stop_loss=replace(current.stop_loss, value=float(params["stop"])),
        take_profit=replace(current.take_profit, value=float(params["target"])),
        trailing=replace(
            current.trailing,
            activate_profit=float(params["activate"]),
            distance=float(params["distance"]),
        ),
    )


def _label(params: dict) -> str:
    return (
        f"{params['timeframe']} size={params['order_size']} confirm={params['confirmation']} "
        f"close={params['candle_close']} {params['direction']} lookback={params['lookback']} "
        f"sl={params['stop']} arm={params['activate']} trail={params['distance']} tp={params['target']}"
    )


def write_test_yaml(params: dict) -> None:
    shutil.copyfile(BASE_DIR / "config.yaml", TEST_CONFIG)
    text = TEST_CONFIG.read_text(encoding="utf-8")
    replacements = {
        'timeframe: "1h"': f'timeframe: "{params["timeframe"]}"',
        "order_size: 5": f"order_size: {params['order_size']}",
        "breakout_lookback_candles: 1": f"breakout_lookback_candles: {params['lookback']}",
        'direction: "BOTH"': f'direction: "{params["direction"]}"',
        "value: 150": f"value: {params['confirmation']}",
        "candle_close_confirmation: false": f"candle_close_confirmation: {str(params['candle_close']).lower()}",
        "  value: 10": f"  value: {params['target']}",
        "  value: 3": f"  value: {params['stop']}",
        "  activate_profit: 4": f"  activate_profit: {params['activate']}",
        "  distance: 3": f"  distance: {params['distance']}",
    }
    for old, new in replacements.items():
        if old not in text:
            raise SystemExit(f"Could not update test.yaml field {old}")
        text = text.replace(old, new, 1)
    TEST_CONFIG.write_text(text, encoding="utf-8")
    main.load_config(TEST_CONFIG)


def check_recent_fills() -> None:
    """Last seven days of BTCUSD size 5. Not used to choose settings."""
    if not ORDER_LOG.exists():
        print("Order log missing. Skipping the live-fill check.")
        return
    cutoff = datetime(2026, 10, 8)
    rows = []
    with ORDER_LOG.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["Contract"] != "BTCUSD" or row["Qty"] != "5" or row["Status"] != "closed":
                continue
            filled = float(row["Filled/Remaining"].split("/")[0])
            if filled <= 0:
                continue
            when = datetime.strptime(row["Time"].split("+")[0].strip(), "%Y-%m-%d %H:%M:%S.%f")
            if when < cutoff:
                continue
            rows.append(row)
    print(f"LIVE CHECK  BTCUSD size 5 since {cutoff.date()}  closed fills={len(rows)}")
    cash = sum(float(row["Cashflow"] or 0) for row in rows)
    fees = sum(float(row["Trading Fees"] or 0) for row in rows)
    print(f"  price cashflow {cash:+.2f}  fees {fees:.2f}  net {cash - fees:+.2f}")


def main_backtest() -> None:
    bars_by_tf = {name: load_bars(path) for name, path in CANDLES.items()}
    for name, bars in bars_by_tf.items():
        print(f"{name:4} {len(bars):5} bars  {bars[0].time} -> {bars[-1].time}")
    params, trades, train_net, train_count = search(bars_by_tf)
    hold_count, hold_net, hold_wins = _sum(trades, TRAIN_END, HOLDOUT_END)
    print("CHOSEN", _label(params))
    print(f"TRAIN  n={train_count} net={train_net:+.2f}")
    print(f"HOLDOUT 8-14 Oct  n={hold_count} wins={hold_wins} net={hold_net:+.2f}")
    current = main.load_config(BASE_DIR / "config.yaml")
    daily_count, daily_net, _wins = _sum(
        simulate(bars_by_tf["1d"], _config_from(current, params)),
        date(2026, 9, 15),
        HOLDOUT_END,
    )
    print(f"DAILY BARS same settings, not used to choose  n={daily_count} net={daily_net:+.2f}")
    write_test_yaml(params)
    print(f"Wrote {TEST_CONFIG.name}")
    check_recent_fills()


if __name__ == "__main__":
    main_backtest()

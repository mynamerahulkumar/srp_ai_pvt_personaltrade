#!/usr/bin/env python3
"""Replay the breakout strategy on historical OHLC and compare timeframes.

Does not call Delta Exchange. Dollar PnL matches the live formula
(price change) x order_size x contract_value, minus a taker fee on notional.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


@dataclass(frozen=True)
class Params:
    lookback: int
    direction: str
    confirmation_points: float
    candle_close: bool
    take_profit: float
    stop_loss: float
    order_size: int
    max_orders_per_day: int
    max_loss_per_day: float
    contract_value: float
    tick: float
    fee_per_side_bps: float
    timeframe: str
    interval: str
    leverage: int = 0


@dataclass
class Stats:
    days: list
    daily: dict
    trades: int
    wins: int
    net: float
    gross_win: float
    gross_loss: float
    max_dd: float
    liquidations: int = 0

    @property
    def avg_daily(self) -> float:
        if not self.days:
            return 0.0
        return self.net / len(self.days)

    @property
    def median_daily(self) -> float:
        if not self.days:
            return 0.0
        values = sorted(self.daily.get(day, 0.0) for day in self.days)
        mid = len(values) // 2
        if len(values) % 2:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2.0

    @property
    def win_rate(self) -> float:
        if self.trades == 0:
            return 0.0
        return 100.0 * self.wins / self.trades

    @property
    def profit_factor(self) -> float:
        if self.gross_loss < 0:
            return self.gross_win / abs(self.gross_loss)
        if self.gross_win > 0:
            return math.inf
        return 0.0

    def days_at_or_above(self, target: float) -> float:
        if not self.days:
            return 0.0
        hits = sum(1 for day in self.days if self.daily.get(day, 0.0) >= target)
        return 100.0 * hits / len(self.days)


def snap(price: float, tick: float) -> float:
    """Half-up tick rounding for positive prices."""
    return math.floor(price / tick + 0.5 + 1e-9) * tick


def load_candles(path: Path, lookback_days: int) -> tuple[list, list, list, list, list, list]:
    rows: list[tuple[datetime, float, float, float, float]] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw = (row.get("datetime") or "").strip()
            if not raw:
                continue
            moment = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            rows.append(
                (
                    moment,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                )
            )
    if not rows:
        raise SystemExit(f"No candles in {path}")
    rows.sort(key=lambda item: item[0])
    start = rows[-1][0] - timedelta(days=lookback_days)
    rows = [row for row in rows if row[0] >= start]
    moments = [row[0] for row in rows]
    dates = [row[0].date() for row in rows]
    opens = [row[1] for row in rows]
    highs = [row[2] for row in rows]
    lows = [row[3] for row in rows]
    closes = [row[4] for row in rows]
    return moments, dates, opens, highs, lows, closes


def _range(highs: list[float], lows: list[float], start: int, end: int) -> tuple[float, float]:
    range_high = highs[start]
    range_low = lows[start]
    for index in range(start + 1, end):
        if highs[index] > range_high:
            range_high = highs[index]
        if lows[index] < range_low:
            range_low = lows[index]
    return range_high, range_low


def _book(state: dict, day, exit_price: float) -> None:
    if state.get("liq_binds") and state.get("liq_px") is not None:
        liq = state["liq_px"]
        if state["direction"] == "LONG" and exit_price <= liq + 1e-4:
            state["liquidations"] += 1
        elif state["direction"] == "SHORT" and exit_price >= liq - 1e-4:
            state["liquidations"] += 1
    direction = state["direction"]
    entry = state["entry"]
    denom = state["denom"]
    if direction == "LONG":
        gross = (exit_price - entry) * denom
    else:
        gross = (entry - exit_price) * denom
    exit_fee = exit_price * denom * state["fee_rate"]
    pnl = gross - state["entry_fee"] - exit_fee
    state["position"] = None
    state["daily_realized"] += pnl
    state["net"] += pnl
    state["trades"] += 1
    state["pnls"].append(pnl)
    state["daily"][day] = state["daily"].get(day, 0.0) + pnl
    if pnl > 0:
        state["wins"] += 1
        state["gross_win"] += pnl
    elif pnl < 0:
        state["gross_loss"] += pnl
    if state["daily_realized"] <= -state["max_loss"]:
        state["blocked"] = True


def _levels(entry: float, direction: str, tp: float, sl: float, denom: float, tick: float) -> tuple[float, float]:
    tp_move = tp / denom
    sl_move = sl / denom
    if direction == "LONG":
        return snap(entry + tp_move, tick), snap(entry - sl_move, tick)
    return snap(entry - tp_move, tick), snap(entry + sl_move, tick)


def _exit_open_position(direction: str, bar_open: float, high: float, low: float, tp_px: float, sl_px: float) -> float | None:
    """Exit an existing position. A gap through the stop fills at the open."""
    if direction == "LONG":
        if bar_open <= sl_px:
            return bar_open
        if bar_open >= tp_px:
            return bar_open
        hit_sl = low <= sl_px
        hit_tp = high >= tp_px
        if hit_sl and hit_tp:
            return sl_px
        if hit_sl:
            return sl_px
        if hit_tp:
            return tp_px
        return None
    if bar_open >= sl_px:
        return bar_open
    if bar_open <= tp_px:
        return bar_open
    hit_sl = high >= sl_px
    hit_tp = low <= tp_px
    if hit_sl and hit_tp:
        return sl_px
    if hit_sl:
        return sl_px
    if hit_tp:
        return tp_px
    return None


def _exit_after_entry(direction: str, high: float, low: float, tp_px: float, sl_px: float) -> float | None:
    """Same-bar exit after a live cross. Both sides touched books the stop."""
    if direction == "LONG":
        hit_sl = low <= sl_px
        hit_tp = high >= tp_px
    else:
        hit_sl = high >= sl_px
        hit_tp = low <= tp_px
    if hit_sl and hit_tp:
        return sl_px
    if hit_sl:
        return sl_px
    if hit_tp:
        return tp_px
    return None


def _clamp_liq(state: dict, exit_price: float | None) -> float | None:
    """A liquidation fill does not gap past the margin price."""
    if exit_price is None or not state.get("liq_binds") or state.get("liq_px") is None:
        return exit_price
    liq = state["liq_px"]
    if state["direction"] == "LONG" and exit_price < liq:
        return liq
    if state["direction"] == "SHORT" and exit_price > liq:
        return liq
    return exit_price


def simulate(
    dates: list,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    params: Params,
    trade_start: int,
    trade_end: int,
    eval_days: list,
    session_days: list | None = None,
    in_session: list[bool] | None = None,
    session_flat: list[bool] | None = None,
    filter_long: list[bool] | None = None,
    filter_short: list[bool] | None = None,
) -> Stats:
    denom = params.order_size * params.contract_value
    if denom <= 0:
        raise SystemExit("order_size and contract_value must be positive")
    fee_rate = params.fee_per_side_bps / 10_000.0
    allow_long = params.direction in {"LONG", "BOTH"}
    allow_short = params.direction in {"SHORT", "BOTH"}
    lookback = params.lookback
    tick = params.tick
    points = params.confirmation_points
    state = {
        "position": None,
        "direction": "",
        "entry": 0.0,
        "entry_fee": 0.0,
        "tp_px": 0.0,
        "sl_px": 0.0,
        "denom": denom,
        "fee_rate": fee_rate,
        "daily_realized": 0.0,
        "max_loss": params.max_loss_per_day,
        "blocked": False,
        "net": 0.0,
        "trades": 0,
        "wins": 0,
        "gross_win": 0.0,
        "gross_loss": 0.0,
        "pnls": [],
        "daily": {},
        "liquidations": 0,
        "liq_px": None,
        "liq_binds": False,
    }
    current_day = None
    daily_orders = 0
    last_index = trade_end - 1

    for index in range(max(trade_start, lookback), trade_end):
        day = dates[index]
        cap_day = session_days[index] if session_days is not None else day
        tradable = in_session is None or in_session[index]
        end_session = session_flat is not None and session_flat[index]
        if cap_day != current_day:
            current_day = cap_day
            state["daily_realized"] = 0.0
            state["blocked"] = False
            daily_orders = 0
        bar_open = opens[index]
        high = highs[index]
        low = lows[index]
        close = closes[index]
        if not tradable:
            if state["position"] is not None:
                _book(state, day, bar_open)
            continue
        exited = False

        if state["position"] is not None:
            exit_price = _exit_open_position(
                state["direction"], bar_open, high, low, state["tp_px"], state["sl_px"]
            )
            if exit_price is not None:
                _book(state, day, _clamp_liq(state, exit_price))
                exited = True

        can_enter = (
            state["position"] is None
            and not exited
            and not state["blocked"]
            and daily_orders < params.max_orders_per_day
            and state["daily_realized"] > -params.max_loss_per_day
            and not (params.candle_close and index == last_index)
        )
        if can_enter:
            range_high, range_low = _range(highs, lows, index - lookback, index)
            long_level = snap(range_high + points, tick)
            short_level = snap(range_low - points, tick)
            direction = None
            entry = 0.0
            long_ok = filter_long is None or filter_long[index]
            short_ok = filter_short is None or filter_short[index]
            if params.candle_close:
                if allow_long and long_ok and close > long_level and allow_short and short_ok and close < short_level:
                    direction = None
                elif allow_long and long_ok and close > long_level:
                    direction = "LONG"
                    entry = close
                elif allow_short and short_ok and close < short_level:
                    direction = "SHORT"
                    entry = close
            else:
                long_hit = allow_long and long_ok and high > long_level
                short_hit = allow_short and short_ok and low < short_level
                if long_hit and short_hit:
                    direction = None
                elif long_hit:
                    direction = "LONG"
                    entry = bar_open if bar_open > long_level else long_level
                elif short_hit:
                    direction = "SHORT"
                    entry = bar_open if bar_open < short_level else short_level
            if direction is not None:
                tp_px, sl_px = _levels(
                    entry, direction, params.take_profit, params.stop_loss, denom, tick
                )
                liq_px = None
                liq_binds = False
                if params.leverage > 0:
                    move = entry / params.leverage
                    liq_px = entry - move if direction == "LONG" else entry + move
                    if direction == "LONG" and liq_px > sl_px:
                        sl_px = liq_px
                        liq_binds = True
                    elif direction == "SHORT" and liq_px < sl_px:
                        sl_px = liq_px
                        liq_binds = True
                state["liq_px"] = liq_px
                state["liq_binds"] = liq_binds
                state["position"] = direction
                state["direction"] = direction
                state["entry"] = entry
                state["entry_fee"] = entry * denom * fee_rate
                state["tp_px"] = tp_px
                state["sl_px"] = sl_px
                daily_orders += 1
                if not params.candle_close:
                    exit_price = _exit_after_entry(direction, high, low, tp_px, sl_px)
                    if exit_price is not None:
                        _book(state, day, _clamp_liq(state, exit_price))

        if state["position"] is not None:
            mark = close
            if state["direction"] == "LONG":
                unrealized = (mark - state["entry"]) * denom - state["entry_fee"]
            else:
                unrealized = (state["entry"] - mark) * denom - state["entry_fee"]
            if state["daily_realized"] + unrealized <= -params.max_loss_per_day:
                _book(state, day, close)

        if end_session and state["position"] is not None:
            _book(state, day, close)

    if state["position"] is not None and last_index >= 0:
        _book(state, dates[last_index], closes[last_index])

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in state["pnls"]:
        equity += pnl
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        if drawdown > max_dd:
            max_dd = drawdown
    return Stats(
        days=eval_days,
        daily=state["daily"],
        trades=state["trades"],
        wins=state["wins"],
        net=state["net"],
        gross_win=state["gross_win"],
        gross_loss=state["gross_loss"],
        max_dd=max_dd,
        liquidations=state["liquidations"],
    )


def split_windows(dates: list, tune_days: int) -> tuple[list, list, int, int, int]:
    unique = []
    seen = set()
    for day in dates:
        if day not in seen:
            seen.add(day)
            unique.append(day)
    tune = unique[:tune_days]
    hold = unique[tune_days:]
    if not hold:
        raise SystemExit("Not enough calendar days for a tune window and a holdout window")
    hold_start_day = hold[0]
    tune_end = 0
    hold_start = len(dates)
    for index, day in enumerate(dates):
        if day in set(tune):
            tune_end = index + 1
        if day == hold_start_day and hold_start == len(dates):
            hold_start = index
    return tune, hold, 0, tune_end, hold_start


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def session_clocks(
    moments: list[datetime],
    candle_timezone: str,
    session_timezone: str,
    start: str | None,
    stop: str | None,
) -> tuple[list, list[bool], list[bool]]:
    """Convert IST candle opens into the session clock.

    A bar is inside the session when its converted time is at or after start
    and before stop. The cap date is the date in the session timezone.
    """
    source = ZoneInfo(candle_timezone)
    zone = ZoneInfo(session_timezone)
    start_m = None if start in (None, "") else _minutes(str(start))
    stop_m = None if stop in (None, "") else _minutes(str(stop))
    local_dates = []
    inside: list[bool] = []
    for moment in moments:
        local = moment.replace(tzinfo=source).astimezone(zone)
        local_dates.append(local.date())
        if start_m is None or stop_m is None:
            inside.append(True)
            continue
        minute = local.hour * 60 + local.minute
        inside.append(start_m <= minute < stop_m)
    flatten: list[bool] = []
    for index, flag in enumerate(inside):
        if start_m is None:
            flatten.append(False)
            continue
        next_inside = inside[index + 1] if index + 1 < len(inside) else False
        next_day = local_dates[index + 1] if index + 1 < len(local_dates) else None
        flatten.append(bool(flag and (not next_inside or next_day != local_dates[index])))
    return local_dates, inside, flatten


def check_session_conversion() -> None:
    """IST 19:00 in July is 09:30 in New York while the US is on daylight time."""
    sample = datetime(2026, 7, 15, 19, 0).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    new_york = sample.astimezone(ZoneInfo("America/New_York"))
    london = datetime(2026, 7, 15, 12, 30).replace(tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(
        ZoneInfo("Europe/London")
    )
    if (new_york.hour, new_york.minute) != (9, 30) or (london.hour, london.minute) != (8, 0):
        raise SystemExit("IST session conversion does not match New York or London daylight time")


def params_from_mapping(raw: dict, common: dict, interval: str, timeframe: str) -> Params:
    return Params(
        lookback=int(raw["breakout_lookback_candles"]),
        direction=str(raw["direction"]).upper(),
        confirmation_points=float(raw["confirmation_points"]),
        candle_close=bool(raw["candle_close_confirmation"]),
        take_profit=float(raw["take_profit"]),
        stop_loss=float(raw["stop_loss"]),
        order_size=int(raw["order_size"]),
        max_orders_per_day=int(raw["max_orders_per_day"]),
        max_loss_per_day=float(raw["max_loss_per_day_dollar"]),
        contract_value=float(common["contract_value"]),
        tick=float(common["tick_size"]),
        fee_per_side_bps=float(common["fee_per_side_bps"]),
        timeframe=timeframe,
        interval=interval,
        leverage=int(raw.get("leverage", 0)),
    )


def search_grid(search: dict) -> list[dict]:
    keys = [
        "breakout_lookback_candles",
        "confirmation_points",
        "take_profit",
        "stop_loss",
        "order_size",
        "candle_close_confirmation",
        "direction",
    ]
    values = []
    for key in keys:
        value = search[key]
        values.append(value if isinstance(value, list) else [value])
    combos = []
    for choice in itertools.product(*values):
        raw = dict(zip(keys, choice))
        raw["max_orders_per_day"] = search["max_orders_per_day"]
        raw["max_loss_per_day_dollar"] = search["max_loss_per_day_dollar"]
        combos.append(raw)
    return combos


def money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def factor(value: float) -> str:
    if math.isinf(value):
        return "inf"
    return f"{value:.2f}"


def metric_row(label: str, stats: Stats, target: float) -> str:
    return (
        f"| {label} | {money(stats.net)} | {money(stats.avg_daily)} | {money(stats.median_daily)} "
        f"| {stats.days_at_or_above(target):.1f}% | {stats.trades} | {stats.win_rate:.1f}% "
        f"| {factor(stats.profit_factor)} | {money(stats.max_dd)} |"
    )


def settings_text(params: Params) -> str:
    close_label = "close" if params.candle_close else "live cross"
    return (
        f"{params.interval}, lookback {params.lookback}, confirmation {params.confirmation_points:g} points, "
        f"{close_label}, TP {money(params.take_profit)}, SL {money(params.stop_loss)}, "
        f"order size {params.order_size}, direction {params.direction}"
    )


def update_config(path: Path, params: Params, schedule: dict | None = None) -> None:
    lines = path.read_text().splitlines(keepends=True)
    section = None
    updated = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("timeframe:"):
            line = f'timeframe: "{params.timeframe}"\n'
        elif params.leverage > 0 and stripped.startswith("leverage:"):
            line = f"leverage: {params.leverage}\n"
        elif stripped.startswith("order_size:"):
            line = f"order_size: {params.order_size}\n"
        elif schedule is not None and stripped.startswith("run_mode:"):
            line = f'run_mode: "{schedule["run_mode"]}"\n'
        elif schedule is not None and stripped.startswith("timezone:"):
            line = f'timezone: "{schedule["timezone"]}"\n'
        elif schedule is not None and schedule.get("start_time") and stripped.startswith("start_time:"):
            line = f'start_time: "{schedule["start_time"]}"\n'
        elif schedule is not None and schedule.get("stop_time") and stripped.startswith("stop_time:"):
            line = f'stop_time: "{schedule["stop_time"]}"\n'
        elif schedule is not None and stripped.startswith("close_positions_at_stop_time:"):
            line = f"close_positions_at_stop_time: {'true' if schedule['flatten'] else 'false'}\n"
        elif stripped.startswith("max_loss_per_day_dollar:"):
            line = f"max_loss_per_day_dollar: {yaml_scalar(params.max_loss_per_day)}\n"
        elif stripped.startswith("breakout_lookback_candles:"):
            line = f"  breakout_lookback_candles: {params.lookback}\n"
        elif stripped.startswith("direction:"):
            line = f'  direction: "{params.direction}"\n'
        elif stripped == "confirmation:":
            section = "confirmation"
        elif stripped == "take_profit:":
            section = "take_profit"
        elif stripped == "stop_loss:":
            section = "stop_loss"
        elif stripped.endswith(":") and not line.startswith((" ", "\t", "#")):
            name = stripped.split(":", 1)[0]
            if name not in {"confirmation", "take_profit", "stop_loss"}:
                section = None
        elif stripped.startswith("value:") and section == "confirmation":
            line = f"  value: {yaml_scalar(params.confirmation_points)}\n"
        elif stripped.startswith("candle_close_confirmation:"):
            line = f"  candle_close_confirmation: {'true' if params.candle_close else 'false'}\n"
        elif stripped.startswith("value:") and section == "take_profit":
            line = f"  value: {yaml_scalar(params.take_profit)}\n"
        elif stripped.startswith("value:") and section == "stop_loss":
            line = f"  value: {yaml_scalar(params.stop_loss)}\n"
        updated.append(line)
    path.write_text("".join(updated))


def yaml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        number = round(float(value), 2)
        if number == int(number):
            return str(int(number))
        return f"{number:.2f}".rstrip("0").rstrip(".")
    return str(value)


def config_changes(old: dict, params: Params, schedule: dict | None = None) -> list[str]:
    rows = [
        ("timeframe", old.get("timeframe"), params.timeframe),
        ("leverage", old.get("leverage"), params.leverage if params.leverage > 0 else old.get("leverage")),
        ("order_size", old.get("order_size"), params.order_size),
        ("max_loss_per_day_dollar", old.get("max_loss_per_day_dollar"), params.max_loss_per_day),
        (
            "strategy.breakout_lookback_candles",
            (old.get("strategy") or {}).get("breakout_lookback_candles"),
            params.lookback,
        ),
        ("strategy.direction", (old.get("strategy") or {}).get("direction"), params.direction),
        ("confirmation.value", (old.get("confirmation") or {}).get("value"), params.confirmation_points),
        (
            "confirmation.candle_close_confirmation",
            (old.get("confirmation") or {}).get("candle_close_confirmation"),
            params.candle_close,
        ),
        ("take_profit.value", (old.get("take_profit") or {}).get("value"), params.take_profit),
        ("stop_loss.value", (old.get("stop_loss") or {}).get("value"), params.stop_loss),
    ]
    if schedule is not None:
        rows.extend(
            [
                ("timezone", old.get("timezone"), schedule["timezone"]),
                ("run_mode", old.get("run_mode"), schedule["run_mode"]),
                ("close_positions_at_stop_time", old.get("close_positions_at_stop_time"), schedule["flatten"]),
            ]
        )
        if schedule.get("start_time"):
            rows.append(("start_time", old.get("start_time"), schedule["start_time"]))
        if schedule.get("stop_time"):
            rows.append(("stop_time", old.get("stop_time"), schedule["stop_time"]))
    lines = []
    for name, before, after in rows:
        if yaml_scalar(before) == yaml_scalar(after):
            continue
        lines.append(f"- `{name}`: {yaml_scalar(before)} -> {yaml_scalar(after)}")
    if not lines:
        lines.append("- No strategy fields differed from the existing config.")
    return lines


def write_report(
    path: Path,
    spec: dict,
    window_label: str,
    day_count: int,
    tune_count: int,
    hold_count: int,
    baseline_rows: list[tuple[Params, Stats]],
    tested: int,
    survivors: list[tuple[Params, Stats, Stats]],
    winner: tuple[Params, Stats, Stats] | None,
    changes: list[str],
    size_lines: list[str] | None = None,
    winner_text: str | None = None,
) -> None:
    target = float(spec["target_daily_pnl"])
    header = (
        "| Window | Net PnL | Avg daily | Median day | Days >= "
        f"${target:g} | Trades | Win rate | Profit factor | Max drawdown |"
    )
    divider = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    parts = [
        "# Breakout backtest results",
        "",
        f"Window: {window_label} ({day_count} calendar days). "
        f"Tune window: first {tune_count} days. Holdout: last {hold_count} days.",
        "",
        "The simulator follows the live breakout: previous completed candles set the range, "
        "one position at a time, no new entry on the exit candle, 20 orders per day, "
        "and a $10 daily loss cap that flattens at the bar close. "
        f"BTCUSD contract value is {spec['contract_value']} and tick size is {spec['tick_size']}. "
        f"Taker fee is {spec['fee_per_side_bps']:g} bps per side on price x size x contract value. "
        "Funding is not included. A bar that touches both the take-profit and the stop books the stop. "
        "An open trade at the end of a window is closed at the last close.",
        "",
        "## First run",
        "",
        "Current `config.yaml` settings on each interval, full window, before any search.",
        "",
        header.replace("Window", "Interval"),
        divider,
    ]
    for params, stats in baseline_rows:
        parts.append(metric_row(params.interval, stats, target).replace(f"| {params.interval} |", f"| {params.interval} |", 1))
    parts.extend(
        [
            "",
            "Baseline settings: lookback 1, confirmation 150 points, live price cross, "
            "take-profit $5, stop-loss $3, order size 5, direction BOTH.",
            "",
            "## Search",
            "",
            f"Combinations tested: {tested}. "
            f"Kept for the holdout: {len(survivors)} with positive net PnL on the tune window.",
            "",
        ]
    )
    if not survivors:
        parts.append("No combination made a profit on the first 100 days, so `config.yaml` was not changed.")
    else:
        ranked = sorted(survivors, key=lambda item: (item[2].avg_daily, item[2].net, -item[2].max_dd), reverse=True)
        parts.extend(
            [
                "Top holdout results among combinations that were profitable on the tune window:",
                "",
                "| Rank | Interval | Lookback | Confirm | Entry | TP | SL | Size | Holdout net | Holdout avg daily | Trades | Win rate | PF | Max DD |",
                "| ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for rank, (params, _tune, hold) in enumerate(ranked[:10], start=1):
            entry = "close" if params.candle_close else "cross"
            parts.append(
                f"| {rank} | {params.interval} | {params.lookback} | {params.confirmation_points:g} | {entry} "
                f"| {params.take_profit:g} | {params.stop_loss:g} | {params.order_size} "
                f"| {money(hold.net)} | {money(hold.avg_daily)} | {hold.trades} | {hold.win_rate:.1f}% "
                f"| {factor(hold.profit_factor)} | {money(hold.max_dd)} |"
            )
    if size_lines:
        parts.extend(["", *size_lines])
    parts.extend(["", "## Final result", ""])
    if winner is None:
        parts.append(
            f"No searched setting beat a flat result on the tune window. "
            f"The last {hold_count} days therefore do not support an average of ${target:g} per day."
        )
    else:
        params, tune_stats, hold_stats = winner
        reached = hold_stats.avg_daily >= target
        parts.extend(
            [
                f"Chosen settings: {winner_text or settings_text(params)}.",
                "",
                header,
                divider,
                metric_row("Tune", tune_stats, target),
                metric_row("Holdout", hold_stats, target),
                "",
            ]
        )
        if reached:
            parts.append(
                f"Holdout average daily PnL reached the ${target:g} target "
                f"({money(hold_stats.avg_daily)} per day over {hold_count} days)."
            )
        else:
            parts.append(
                f"The last {hold_count} days do not support an average of ${target:g} per day. "
                f"This configuration's holdout average is {money(hold_stats.avg_daily)} per day. "
                "That figure is the best holdout result among settings that made money on the earlier days. "
                "It is not a forecast, and the best tune-window result was not used as the recommendation."
            )
        parts.extend(["", "### Config changes", "", *changes])
    parts.append("")
    path.write_text("\n".join(parts))


def _schedule_for(session: dict) -> dict:
    if session.get("start") and session.get("stop"):
        return {
            "timezone": session["timezone"],
            "run_mode": "SCHEDULED",
            "start_time": str(session["start"]),
            "stop_time": str(session["stop"]),
            "flatten": bool(session.get("flatten_at_stop")),
        }
    return {
        "timezone": "Asia/Kolkata",
        "run_mode": "CONTINUOUS",
        "start_time": None,
        "stop_time": None,
        "flatten": False,
    }


def rupees(dollars: float, rate: float) -> str:
    sign = "-" if dollars < 0 else ""
    return f"{sign}₹{abs(dollars) * rate:,.0f}"


def _size_row_line(row: dict, rate: float) -> str:
    params = row["params"]
    if row.get("over_cap"):
        return (
            f"| {params.order_size} | {row['bracket']} | {params.take_profit:g} | {params.stop_loss:g} "
            f"| {row['tp_points']:.0f} | {row['sl_points']:.0f} |  |  |  |  |  |  |  |  |  | over cap |"
        )
    hold = row["hold"]
    qualified = "yes" if row["tune"].net > 0 else "no"
    return (
        f"| {params.order_size} | {row['bracket']} | {params.take_profit:g} | {params.stop_loss:g} "
        f"| {row['tp_points']:.0f} | {row['sl_points']:.0f} | {params.max_loss_per_day:g} "
        f"| {money(row['tune'].net)} | {money(hold.net)} | {money(hold.avg_daily)} "
        f"| {rupees(hold.avg_daily, rate)} | {hold.trades} "
        f"| {hold.win_rate:.1f}% | {factor(hold.profit_factor)} | {money(hold.max_dd)} | {qualified} |"
    )


def run_position_size(spec: dict, common: dict, loaded: list, root: Path):
    block = spec.get("position_size")
    if not block:
        return [], None, None, ["- No position-size test was configured."]
    signal = block["signal"]
    interval = signal["interval"]
    match = None
    for item in loaded:
        if item[0]["name"] == interval:
            match = item
            break
    if match is None:
        raise SystemExit(f"position_size interval {interval} was not loaded")
    item, moments, dates, opens, highs, lows, closes, tune, hold, tune_end, hold_start = match
    base_size = int(signal["base_order_size"])
    base_tp = float(signal["base_take_profit"])
    base_sl = float(signal["base_stop_loss"])
    base_cap = float(signal["base_max_loss_per_day_dollar"])
    contract_value = float(common["contract_value"])
    usd_inr = float(spec["usd_inr"])
    loss_cap = round(float(spec["max_loss_per_day_inr"]) / usd_inr, 2)
    rows = []
    for session in block["sessions"]:
        session_days, inside, flat = session_clocks(
            moments,
            str(block["candle_timezone"]),
            str(session["timezone"]),
            session.get("start"),
            session.get("stop"),
        )
        if not session.get("flatten_at_stop"):
            flat = [False] * len(flat)
        for size in block["order_size"]:
            size = int(size)
            scale = size / base_size
            for bracket in ("fixed", "scaled"):
                if bracket == "fixed":
                    take_profit, stop_loss, daily_cap = base_tp, base_sl, base_cap
                else:
                    take_profit = base_tp * scale
                    stop_loss = base_sl * scale
                    daily_cap = base_cap * scale
                daily_cap = min(daily_cap, loss_cap)
                over_cap = stop_loss > loss_cap + 1e-9
                raw = {
                    "breakout_lookback_candles": signal["breakout_lookback_candles"],
                    "direction": signal["direction"],
                    "confirmation_points": signal["confirmation_points"],
                    "candle_close_confirmation": signal["candle_close_confirmation"],
                    "take_profit": take_profit,
                    "stop_loss": stop_loss,
                    "order_size": size,
                    "max_orders_per_day": signal["max_orders_per_day"],
                    "max_loss_per_day_dollar": daily_cap,
                }
                params = params_from_mapping(raw, common, interval, item["timeframe"])
                denom = size * contract_value
                if over_cap:
                    row = {
                        "session": session,
                        "bracket": bracket,
                        "params": params,
                        "tune": None,
                        "hold": None,
                        "tp_points": take_profit / denom,
                        "sl_points": stop_loss / denom,
                        "schedule": _schedule_for(session),
                        "over_cap": True,
                    }
                    rows.append(row)
                    print(
                        f"{session['name']} {bracket} size {size}: stop {money(stop_loss)} is above the ₹600 cap",
                        flush=True,
                    )
                    continue
                tune_stats = simulate(
                    dates, opens, highs, lows, closes, params, 0, tune_end, tune,
                    session_days, inside, flat,
                )
                hold_stats = simulate(
                    dates, opens, highs, lows, closes, params, hold_start, len(dates), hold,
                    session_days, inside, flat,
                )
                row = {
                    "session": session,
                    "bracket": bracket,
                    "params": params,
                    "tune": tune_stats,
                    "hold": hold_stats,
                    "tp_points": take_profit / denom,
                    "sl_points": stop_loss / denom,
                    "schedule": _schedule_for(session),
                    "over_cap": False,
                }
                rows.append(row)
                print(
                    f"{session['name']} {bracket} size {size}: "
                    f"tune {money(tune_stats.net)} holdout {money(hold_stats.avg_daily)}/day",
                    flush=True,
                )
    reference = next(
        row for row in rows
        if row["session"]["name"] == "Full day"
        and row["bracket"] == "fixed"
        and row["params"].order_size == base_size
        and not row["over_cap"]
    )
    qualified = [row for row in rows if not row["over_cap"] and row["tune"].net > 0]
    applied = reference
    if qualified:
        applied = max(qualified, key=lambda row: (row["hold"].avg_daily, row["hold"].net, -row["hold"].max_dd))
    lines = [
        "## Position size and session",
        "",
        "Candle times in the CSV are India time (`Asia/Kolkata`). "
        "New York and London hours are converted from those timestamps, including daylight saving. "
        "In this sample, 19:00 IST is 09:30 New York and 12:30 IST is 08:00 London. "
        "The daily loss cap and the 20-order cap reset at midnight in the session timezone. "
        "New York entries are 09:30–16:00 America/New_York and London entries are 08:00–16:30 Europe/London. "
        "An open trade is closed on the last bar that still falls inside that window. "
        "Full day uses every IST hour and does not close on a clock.",
        "",
        f"The daily loss budget is ₹{float(spec['max_loss_per_day_inr']):,.0f}. "
        f"At ₹{usd_inr:g} per dollar that is {money(loss_cap)}. "
        "The daily cap is never set above that amount. "
        "A row is marked over cap, and is not ranked, when its stop is larger than that amount, "
        "so the day limit does not replace the trade stop.",
        "",
        "Fixed dollars keep a $12 take-profit and a $6 stop, so a larger size only shortens the price move. "
        "Scaled dollars keep the size-5 distance of 2,400 points to the target and 1,200 points to the stop. "
        "Rupee figures use the same ₹ per dollar rate.",
        "",
        "A row is qualified only when it fits the ₹600 stop budget and the first 100 days are profitable after fees. "
        "Average daily PnL divides by those India-time calendar days so the three clocks are comparable.",
        "",
    ]
    header = (
        "| Size | Bracket | TP | SL | TP points | SL points | Daily cap | Tune net | Holdout net "
        "| Holdout avg | Holdout avg ₹ | Trades | Win rate | PF | Max DD | Qualified |"
    )
    divider = "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    for session in block["sessions"]:
        lines.extend([f"### {session['name']}", "", header, divider])
        for row in rows:
            if row["session"]["name"] == session["name"]:
                lines.append(_size_row_line(row, usd_inr))
        lines.append("")
    config_path = root / spec["config_to_update"]
    old = yaml.safe_load(config_path.read_text())
    changes = config_changes(old, applied["params"], applied["schedule"])
    update_config(config_path, applied["params"], applied["schedule"])
    text = (
        f"{applied['session']['name']}, {applied['bracket']} dollars, "
        f"{settings_text(applied['params'])}, daily cap {money(applied['params'].max_loss_per_day)} "
        f"({rupees(applied['params'].max_loss_per_day, usd_inr)})"
    )
    stop_rupees = applied["params"].stop_loss * usd_inr
    budget = float(spec["max_loss_per_day_inr"])
    if stop_rupees >= budget * 0.7:
        budget_note = "One stop at this size uses most of the ₹600 day."
    else:
        budget_note = (
            f"This stop is inside the ₹{budget:.0f} budget. "
            "The daily cap on this row is the amount that was actually tested."
        )
    lines.append(
        f"Keeper risk: one stop is {money(applied['params'].stop_loss)} "
        f"({rupees(applied['params'].stop_loss, usd_inr)}) and the daily loss cap is "
        f"{money(applied['params'].max_loss_per_day)} "
        f"({rupees(applied['params'].max_loss_per_day, usd_inr)}). "
        f"{budget_note}"
    )
    winner = (applied["params"], applied["tune"], applied["hold"])
    return lines, winner, text, changes


def run_leverage(spec: dict, common: dict, loaded: list, root: Path):
    """Sweep leverage. Update config only when holdout profit reaches the rupee target."""
    block = spec.get("position_size") or {}
    levers = [int(value) for value in block.get("leverage") or []]
    if not levers:
        return [], None, None, None, False
    signal = block["signal"]
    interval = signal["interval"]
    match = next(item for item in loaded if item[0]["name"] == interval)
    item, moments, dates, opens, highs, lows, closes, tune, hold, tune_end, hold_start = match
    base_size = int(signal["base_order_size"])
    base_tp = float(signal["base_take_profit"])
    base_sl = float(signal["base_stop_loss"])
    base_cap = float(signal["base_max_loss_per_day_dollar"])
    usd_inr = float(spec["usd_inr"])
    loss_cap = round(float(spec["max_loss_per_day_inr"]) / usd_inr, 2)
    target_inr = float(block.get("daily_profit_target_inr", 500))
    rows = []
    for session in block["sessions"]:
        session_days, inside, flat = session_clocks(
            moments,
            str(block["candle_timezone"]),
            str(session["timezone"]),
            session.get("start"),
            session.get("stop"),
        )
        if not session.get("flatten_at_stop"):
            flat = [False] * len(flat)
        for leverage in levers:
            for size in block["order_size"]:
                size = int(size)
                scale = size / base_size
                for bracket in ("fixed", "scaled"):
                    if bracket == "fixed":
                        take_profit, stop_loss, daily_cap = base_tp, base_sl, base_cap
                    else:
                        take_profit = base_tp * scale
                        stop_loss = base_sl * scale
                        daily_cap = base_cap * scale
                    daily_cap = min(daily_cap, loss_cap)
                    if stop_loss > loss_cap + 1e-9:
                        continue
                    raw = {
                        "breakout_lookback_candles": signal["breakout_lookback_candles"],
                        "direction": signal["direction"],
                        "confirmation_points": signal["confirmation_points"],
                        "candle_close_confirmation": signal["candle_close_confirmation"],
                        "take_profit": take_profit,
                        "stop_loss": stop_loss,
                        "order_size": size,
                        "max_orders_per_day": signal["max_orders_per_day"],
                        "max_loss_per_day_dollar": daily_cap,
                        "leverage": leverage,
                    }
                    params = params_from_mapping(raw, common, interval, item["timeframe"])
                    tune_stats = simulate(
                        dates, opens, highs, lows, closes, params, 0, tune_end, tune,
                        session_days, inside, flat,
                    )
                    hold_stats = simulate(
                        dates, opens, highs, lows, closes, params, hold_start, len(dates), hold,
                        session_days, inside, flat,
                    )
                    row = {
                        "session": session,
                        "bracket": bracket,
                        "params": params,
                        "tune": tune_stats,
                        "hold": hold_stats,
                        "schedule": _schedule_for(session),
                    }
                    rows.append(row)
            print(f"leverage {leverage}x {session['name']} done", flush=True)
    qualified = [row for row in rows if row["tune"].net > 0]
    qualified.sort(key=lambda row: (row["hold"].avg_daily, row["hold"].net, -row["hold"].max_dd), reverse=True)
    lines = [
        "## Leverage",
        "",
        "Leverage does not change dollar profit. Profit is still price change times size times 0.001. "
        "Leverage only moves the liquidation price. Margin is entry times size times 0.001 divided by leverage, "
        "so the adverse move that burns the margin is entry divided by leverage. "
        "If that price is closer than the strategy stop, the trade exits there and the loss is the margin.",
        "",
        f"The daily loss budget remains ₹{float(spec['max_loss_per_day_inr']):,.0f} ({money(loss_cap)}). "
        f"Rows with a stop above that were not tested. "
        f"A row is ranked only when the first 100 days are profitable. "
        f"The config is changed only when the best holdout averages at least ₹{target_inr:.0f} a day.",
        "",
        "| Rank | Session | Lev | Size | Bracket | SL | Daily cap | Liquidations | Holdout avg | Holdout avg ₹ | Tune net |",
        "| ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    if not qualified:
        lines.append("|  |  |  |  |  |  |  |  |  |  | none qualified |")
    for rank, row in enumerate(qualified[:10], start=1):
        params = row["params"]
        hold = row["hold"]
        lines.append(
            f"| {rank} | {row['session']['name']} | {params.leverage} | {params.order_size} | {row['bracket']} "
            f"| {params.stop_loss:g} | {params.max_loss_per_day:g} | {hold.liquidations} "
            f"| {money(hold.avg_daily)} | {rupees(hold.avg_daily, usd_inr)} | {money(row['tune'].net)} |"
        )
    lines.append("")
    best = qualified[0] if qualified else None
    best_inr = best["hold"].avg_daily * usd_inr if best else 0.0
    if best is not None and best_inr >= target_inr:
        config_path = root / spec["config_to_update"]
        old = yaml.safe_load(config_path.read_text())
        changes = config_changes(old, best["params"], best["schedule"])
        update_config(config_path, best["params"], best["schedule"])
        text = (
            f"{best['session']['name']}, {best['params'].leverage}x, {best['bracket']} dollars, "
            f"{settings_text(best['params'])}, daily cap {money(best['params'].max_loss_per_day)} "
            f"({rupees(best['params'].max_loss_per_day, usd_inr)})"
        )
        lines.append(
            f"This row averages {rupees(best['hold'].avg_daily, usd_inr)} a day on the last 50 days, "
            f"which meets ₹{target_inr:.0f}."
        )
        winner = (best["params"], best["tune"], best["hold"])
        return lines, winner, text, changes, True
    shown = rupees(best["hold"].avg_daily, usd_inr) if best else "₹0"
    lines.append(
        f"No tested leverage reached ₹{target_inr:.0f} a day inside the ₹600 loss limit. "
        f"The best ranked holdout is {shown} a day. "
        "20x through 200x did not create that profit, because leverage does not increase the dollar result "
        "and a larger stop does not fit the ₹600 cap. The leverage test did not change `config.yaml`."
    )
    return lines, None, None, None, False


def _ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    weight = 2.0 / (period + 1)
    for index in range(period, len(values)):
        prev = values[index] * weight + prev * (1.0 - weight)
        out[index] = prev
    return out


def _rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gain_sum = 0.0
    loss_sum = 0.0
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        if change >= 0:
            gain_sum += change
        else:
            loss_sum -= change
    avg_gain = gain_sum / period
    avg_loss = loss_sum / period

    def _value(avg_up: float, avg_down: float) -> float:
        if avg_down == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_up / avg_down))

    out[period] = _value(avg_gain, avg_loss)
    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[index] = _value(avg_gain, avg_loss)
    return out


def _bollinger(values: list[float], period: int = 20, width: float = 2.0) -> tuple[list[float | None], list[float | None]]:
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        mean = sum(window) / period
        variance = sum((item - mean) ** 2 for item in window) / period
        deviation = variance ** 0.5
        upper[index] = mean + width * deviation
        lower[index] = mean - width * deviation
    return upper, lower


def indicator_masks(closes: list[float], name: str) -> tuple[list[bool], list[bool]]:
    """Allow a side on bar i only when the previous completed bar agrees.

    The forming bar is not used, so the filter does not look ahead.
    """
    count = len(closes)
    allow_long = [False] * count
    allow_short = [False] * count
    if name == "none":
        return [True] * count, [True] * count
    if name == "ema20":
        series = _ema(closes, 20)
    elif name == "ema50":
        series = _ema(closes, 50)
    elif name == "rsi14":
        series = _rsi(closes, 14)
    elif name == "bollinger":
        upper, lower = _bollinger(closes, 20, 2.0)
        series = None
    else:
        raise SystemExit(f"unknown indicator filter {name}")
    for index in range(1, count):
        previous = index - 1
        if name == "bollinger":
            if upper[previous] is None or lower[previous] is None:
                continue
            allow_long[index] = closes[previous] > upper[previous]
            allow_short[index] = closes[previous] < lower[previous]
        elif name == "rsi14":
            if series[previous] is None:
                continue
            allow_long[index] = series[previous] >= 55
            allow_short[index] = series[previous] <= 45
        else:
            if series[previous] is None:
                continue
            allow_long[index] = closes[previous] > series[previous]
            allow_short[index] = closes[previous] < series[previous]
    return allow_long, allow_short


def run_indicators(spec: dict, common: dict, loaded: list, root: Path):
    """Test EMA, RSI, and Bollinger filters at size 5 or more."""
    block = spec.get("position_size") or {}
    signal = block["signal"]
    usd_inr = float(spec["usd_inr"])
    loss_cap = round(float(spec["max_loss_per_day_inr"]) / usd_inr, 2)
    base_size = int(signal["base_order_size"])
    base_tp = float(signal["base_take_profit"])
    base_sl = float(signal["base_stop_loss"])
    base_cap = float(signal["base_max_loss_per_day_dollar"])
    sizes = [int(value) for value in block.get("indicator_sizes") or [5, 10, 20]]
    filters = list(block.get("indicator_filters") or ["none", "ema20", "ema50", "rsi14", "bollinger"])
    rows = []
    for item, moments, dates, opens, highs, lows, closes, tune, hold, tune_end, hold_start in loaded:
        session_days, inside, flat = session_clocks(
            moments, str(block["candle_timezone"]), "Asia/Kolkata", None, None
        )
        flat = [False] * len(flat)
        masks = {name: indicator_masks(closes, name) for name in filters}
        for size in sizes:
            scale = size / base_size
            for bracket in ("fixed", "scaled"):
                if bracket == "fixed":
                    take_profit, stop_loss, daily_cap = base_tp, base_sl, base_cap
                else:
                    take_profit = base_tp * scale
                    stop_loss = base_sl * scale
                    daily_cap = base_cap * scale
                daily_cap = min(daily_cap, loss_cap)
                if size < 5 or stop_loss > loss_cap + 1e-9:
                    continue
                for filter_name in filters:
                    allow_long, allow_short = masks[filter_name]
                    raw = {
                        "breakout_lookback_candles": signal["breakout_lookback_candles"],
                        "direction": signal["direction"],
                        "confirmation_points": signal["confirmation_points"],
                        "candle_close_confirmation": signal["candle_close_confirmation"],
                        "take_profit": take_profit,
                        "stop_loss": stop_loss,
                        "order_size": size,
                        "max_orders_per_day": signal["max_orders_per_day"],
                        "max_loss_per_day_dollar": daily_cap,
                    }
                    params = params_from_mapping(raw, common, item["name"], item["timeframe"])
                    tune_stats = simulate(
                        dates, opens, highs, lows, closes, params, 0, tune_end, tune,
                        session_days, inside, flat, allow_long, allow_short,
                    )
                    hold_stats = simulate(
                        dates, opens, highs, lows, closes, params, hold_start, len(dates), hold,
                        session_days, inside, flat, allow_long, allow_short,
                    )
                    rows.append({
                        "filter": filter_name,
                        "bracket": bracket,
                        "params": params,
                        "tune": tune_stats,
                        "hold": hold_stats,
                        "schedule": _schedule_for({"timezone": "Asia/Kolkata"}),
                    })
        print(f"indicators {item['name']} done", flush=True)
    qualified = [row for row in rows if row["tune"].net > 0]
    qualified.sort(key=lambda row: (row["hold"].avg_daily, row["hold"].net, -row["hold"].max_dd), reverse=True)
    target_inr = 500.0
    lines = [
        "## Indicator filters",
        "",
        "Order size is 5, 10, or 20. The breakout is unchanged: one previous candle, "
        "300 points, live cross, both directions, full India-time day. "
        "EMA 20 and EMA 50 allow a long only when the previous close is above the average, and a short only below it. "
        "RSI 14 allows a long only at 55 or higher and a short only at 45 or lower. "
        "Bollinger 20 with 2 standard deviations allows a long only above the upper band and a short only below the lower band. "
        "The filter uses the previous completed candle, not the bar being entered.",
        "",
        f"A stop above {money(loss_cap)} (₹600) is excluded. "
        "A row is ranked only when the first 100 days are profitable after fees.",
        "",
        "| Rank | Interval | Filter | Size | Bracket | TP | SL | Cap | Tune net | Holdout avg | Holdout avg ₹ | Trades |",
        "| ---: | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    if not qualified:
        lines.append("|  |  |  |  |  |  |  |  |  |  |  | none qualified |")
    for rank, row in enumerate(qualified[:10], start=1):
        params = row["params"]
        lines.append(
            f"| {rank} | {params.interval} | {row['filter']} | {params.order_size} | {row['bracket']} "
            f"| {params.take_profit:g} | {params.stop_loss:g} | {params.max_loss_per_day:g} "
            f"| {money(row['tune'].net)} | {money(row['hold'].avg_daily)} | {rupees(row['hold'].avg_daily, usd_inr)} "
            f"| {row['hold'].trades} |"
        )
    lines.append("")
    config_path = root / spec["config_to_update"]
    old = yaml.safe_load(config_path.read_text())
    if qualified:
        applied = qualified[0]
        changes = config_changes(old, applied["params"], applied["schedule"])
        old_filter = str((old.get("indicators") or {}).get("filter", "none"))
        if old_filter != applied["filter"]:
            changes.append(f"- `indicators.filter`: {old_filter} -> {applied['filter']}")
        update_config(config_path, applied["params"], applied["schedule"])
        _write_indicator_config(config_path, applied["filter"])
        hold_inr = applied["hold"].avg_daily * usd_inr
        gap_inr = target_inr - hold_inr
        lines.append(
            f"Best ranked holdout is {money(applied['hold'].avg_daily)} a day, "
            f"which is {rupees(applied['hold'].avg_daily, usd_inr)} a day. "
            f"That is {money(gap_inr / usd_inr)}, or ₹{gap_inr:,.0f}, short of ₹500 a day. "
            "These 150 days do not support a living of ₹500–₹1,000 a day. "
            "A backtest cannot promise income."
        )
        text = (
            f"Full day, {applied['filter']}, {applied['bracket']} dollars, "
            f"{settings_text(applied['params'])}, daily cap {money(applied['params'].max_loss_per_day)}"
        )
        return lines, (applied["params"], applied["tune"], applied["hold"]), text, changes, applied["filter"]
    fallback = params_from_mapping(
        {
            "breakout_lookback_candles": signal["breakout_lookback_candles"],
            "direction": signal["direction"],
            "confirmation_points": signal["confirmation_points"],
            "candle_close_confirmation": signal["candle_close_confirmation"],
            "take_profit": base_tp,
            "stop_loss": base_sl,
            "order_size": 5,
            "max_orders_per_day": signal["max_orders_per_day"],
            "max_loss_per_day_dollar": loss_cap,
        },
        common,
        "1h",
        "1h",
    )
    changes = config_changes(old, fallback, _schedule_for({"timezone": "Asia/Kolkata"}))
    update_config(config_path, fallback, _schedule_for({"timezone": "Asia/Kolkata"}))
    _write_indicator_config(config_path, "none")
    lines.append(
        "No filter at size 5 or more was profitable on the first 100 days. "
        "No indicator was added. Size is set to 5 and the daily loss cap is ₹600 ($7.14). "
        "These 150 days do not support a living of ₹500–₹1,000 a day. A backtest cannot promise income."
    )
    text = f"Full day, no indicator, {settings_text(fallback)}, daily cap {money(fallback.max_loss_per_day)}"
    return lines, None, text, changes, "none"


def _write_indicator_config(path: Path, filter_name: str) -> None:
    text = path.read_text()
    block = (
        "\n# Breakout filter. none leaves the breakout unchanged.\n"
        "indicators:\n"
        f"  filter: \"{filter_name}\"\n"
    )
    start = text.find("\nindicators:")
    if start == -1:
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text + block)
        return
    rest = text[start + 1:]
    end = len(text)
    for marker in ("\nsymbol:", "\nmode:", "\nleverage:", "\norder_size:", "\ntimeframe:"):
        found = rest.find(marker)
        if found != -1:
            end = start + 1 + found
            break
    # The block is appended at the end, so replace from the heading through EOF.
    path.write_text(text[:start] + block)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the BTCUSD breakout on 15m, 30m, and 1h candles")
    parser.add_argument("--config", default="test.yaml", help="Path to test.yaml")
    args = parser.parse_args()
    spec_path = Path(args.config).resolve()
    root = spec_path.parent
    spec = yaml.safe_load(spec_path.read_text())
    lookback_days = int(spec["lookback_days"])
    tune_days = int(spec["tune_days"])
    common = spec

    check_session_conversion()
    baseline_rows: list[tuple[Params, Stats]] = []
    loaded = []
    for item in spec["intervals"]:
        path = root / item["path"]
        moments, dates, opens, highs, lows, closes = load_candles(path, lookback_days)
        tune, hold, _, tune_end, hold_start = split_windows(dates, tune_days)
        loaded.append((item, moments, dates, opens, highs, lows, closes, tune, hold, tune_end, hold_start))
        params = params_from_mapping(spec["baseline"], common, item["name"], item["timeframe"])
        stats = simulate(dates, opens, highs, lows, closes, params, 0, len(dates), tune + hold)
        baseline_rows.append((params, stats))
        print(
            f"baseline {item['name']}: net {money(stats.net)} avg/day {money(stats.avg_daily)} trades {stats.trades}",
            flush=True,
        )

    combos = search_grid(spec["search"])
    survivors: list[tuple[Params, Stats, Stats]] = []
    tested = 0
    for item, _moments, dates, opens, highs, lows, closes, tune, hold, tune_end, hold_start in loaded:
        for raw in combos:
            tested += 1
            params = params_from_mapping(raw, common, item["name"], item["timeframe"])
            tune_stats = simulate(dates, opens, highs, lows, closes, params, 0, tune_end, tune)
            if tune_stats.net <= 0:
                continue
            hold_stats = simulate(dates, opens, highs, lows, closes, params, hold_start, len(dates), hold)
            survivors.append((params, tune_stats, hold_stats))
        print(f"searched {item['name']}: {len(combos)} combinations, survivors so far {len(survivors)}", flush=True)

    size_lines, winner, winner_text, changes = run_position_size(spec, common, loaded, root)
    if winner is not None:
        print(f"keeper {winner_text} holdout avg/day {money(winner[2].avg_daily)}", flush=True)
    lev_lines, lev_winner, lev_text, lev_changes, lev_applied = run_leverage(spec, common, loaded, root)
    size_lines = list(size_lines) + ["", *lev_lines]
    if lev_applied:
        winner, winner_text, changes = lev_winner, lev_text, lev_changes
        print(f"leverage keeper {winner_text}", flush=True)
    ind_lines, ind_winner, ind_text, ind_changes, ind_filter = run_indicators(spec, common, loaded, root)
    size_lines = list(size_lines) + ["", *ind_lines]
    if ind_winner is not None:
        winner, winner_text, changes = ind_winner, ind_text, ind_changes
    else:
        winner_text = ind_text
        changes = ind_changes
    print(f"indicator result {ind_text} filter={ind_filter}", flush=True)

    first_dates = loaded[0][2]
    all_days = []
    seen = set()
    for day in first_dates:
        if day not in seen:
            seen.add(day)
            all_days.append(day)
    tune_count = min(tune_days, len(all_days))
    hold_count = len(all_days) - tune_count
    start = first_dates[0]
    end = first_dates[-1]
    report_path = root / spec["report"]
    write_report(
        report_path,
        spec,
        f"{start} to {end}",
        len(all_days),
        tune_count,
        hold_count,
        baseline_rows,
        tested,
        survivors,
        winner,
        changes,
        size_lines,
        winner_text,
    )
    print(f"wrote {report_path}", flush=True)


if __name__ == "__main__":
    main()

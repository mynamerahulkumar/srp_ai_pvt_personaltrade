#!/usr/bin/env python3
"""Read-only breakout dashboard.

Shows whether the bot is running, current PnL, and which breakout it is
waiting for. The screen closes after 30 seconds, or sooner with Ctrl+C.

This process does not write .bot_stop_signal, does not signal the bot pid,
and does not take the pid lock. The trading process keeps running.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from rich.live import Live

import main

PREVIEW_SECONDS = 30


def _parse_seconds() -> float:
    parser = argparse.ArgumentParser(
        description="Show the breakout bot status, then exit. Does not stop the bot.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=PREVIEW_SECONDS,
        help="Seconds to keep the status screen open (default: %(default)s)",
    )
    args = parser.parse_args()
    if args.seconds < 0:
        parser.error("--seconds must be >= 0")
    return args.seconds


def _load_snapshot() -> dict[str, Any] | None:
    """Read the status file. Missing or unreadable data means no snapshot."""
    try:
        raw = json.loads(main.STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _status_check(snapshot: dict[str, Any] | None, seconds_left: int) -> dict[str, Any]:
    """Process liveness comes from .bot.pid, not from the snapshot text."""
    pid = main.running_bot_pid()
    running = pid is not None
    written_at: float | None = None
    snap_pid: int | None = None
    if snapshot is not None:
        try:
            written_at = float(snapshot.get("written_at"))
        except (TypeError, ValueError):
            written_at = None
        try:
            snap_pid = int(snapshot.get("pid"))
        except (TypeError, ValueError):
            snap_pid = None
    age = None if written_at is None else max(0.0, time.time() - written_at)
    stale = snapshot is not None and (not running or snap_pid != pid)
    return {
        "running": running,
        "pid": pid,
        "stale": stale,
        "missing": snapshot is None and running,
        "seconds_left": seconds_left,
        "age_seconds": age,
    }


def _frame(seconds_left: int):
    snapshot = _load_snapshot()
    check = _status_check(snapshot, seconds_left)
    payload = None if check["missing"] else snapshot
    try:
        return main.render_status_panel(payload, status_check=check)
    except Exception:
        return main.render_status_panel(None, status_check=check)


def main_status() -> int:
    seconds = _parse_seconds()
    deadline = time.time() + seconds
    try:
        with Live(_frame(_seconds_left(deadline)), console=main.console, refresh_per_second=4, screen=True) as live:
            while True:
                left = deadline - time.time()
                if left <= 0:
                    break
                live.update(_frame(_seconds_left(deadline)))
                time.sleep(min(0.25, left))
    except KeyboardInterrupt:
        pass
    print("Status check closed. The bot was not stopped.")
    return 0


def _seconds_left(deadline: float) -> int:
    return max(0, int(deadline - time.time() + 0.999))


if __name__ == "__main__":
    sys.exit(main_status())

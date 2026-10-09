#!/usr/bin/env python3
"""Show the live dashboard, then leave the breakout bot running after this process exits.

The preview uses the same screen as python main.py. When it ends, main.py --headless
keeps trading in its own session and reloads config.yaml. python stop.py still asks
that process to exit and does not close open positions.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import main

# Seconds the live dashboard stays on screen before the terminal is released.
PREVIEW_SECONDS = 60
STARTUP_TIMEOUT_SECONDS = 90

BASE_DIR = Path(__file__).resolve().parent
MAIN_PATH = BASE_DIR / "main.py"


def _parse_seconds() -> float:
    parser = argparse.ArgumentParser(
        description="Start the breakout bot, show the dashboard, then keep it running in the background.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=PREVIEW_SECONDS,
        help="Seconds to show the live dashboard before detaching (default: %(default)s)",
    )
    args = parser.parse_args()
    if args.seconds < 0:
        parser.error("--seconds must be >= 0")
    return args.seconds


def _spawn_headless(stop_keep_after: float) -> subprocess.Popen[bytes]:
    """Start main.py in a new session so closing the terminal does not kill it.

    stdout stays on DEVNULL. Events go to the capped logs/bot.log inside main.py.
    """
    command = [sys.executable, str(MAIN_PATH), "--headless"]
    child_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "BOT_STOP_KEEP_AFTER": str(stop_keep_after),
    }
    if sys.platform == "win32":
        return subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NO_WINDOW
            ),
        )
    return subprocess.Popen(
        command,
        cwd=str(BASE_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_env,
        start_new_session=True,
    )


def _new_log_text(start_size: int) -> str:
    """Bytes appended to bot.log since start_size, capped so a trim cannot fill RAM."""
    path = main.LOG_PATH
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size < start_size:
                handle.seek(max(0, size - 32768))
            else:
                handle.seek(start_size)
            data = handle.read(65536)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _tail(text: str, lines: int = 20) -> str:
    kept = [line for line in text.splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def _wait_until_ready(proc: subprocess.Popen[bytes]) -> tuple[bool, str]:
    """True only when the child is still alive and has logged BOT READY."""
    try:
        start_size = main.LOG_PATH.stat().st_size
    except OSError:
        start_size = 0
    deadline = time.time() + STARTUP_TIMEOUT_SECONDS
    while time.time() < deadline:
        exit_code = proc.poll()
        chunk = _new_log_text(start_size)
        if exit_code is not None:
            detail = _tail(chunk) or f"background process exited ({exit_code})"
            return False, detail
        if f"BOT READY pid={proc.pid}" in chunk:
            time.sleep(0.2)
            if proc.poll() is None:
                return True, ""
            return False, _tail(chunk) or "background process exited after startup"
        time.sleep(0.25)
    if proc.poll() is not None:
        return False, _tail(_new_log_text(start_size)) or "background process exited"
    return False, f"background process did not finish startup within {STARTUP_TIMEOUT_SECONDS}s"


def _stop_child(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main_launcher() -> int:
    seconds = _parse_seconds()
    other = main.running_bot_pid()
    if other is not None:
        print(f"Bot is already running (pid {other}).")
        print("Stop it with: python stop.py")
        return 1

    code = main.main(preview_seconds=seconds)
    if code != main.EXIT_PREVIEW_ELAPSED:
        return code

    if main.STOP_FILE.exists():
        print("Stop signal received. Background bot was not started.")
        print("Existing positions will NOT be closed.")
        return 0

    stop_keep_after = time.time()
    proc = _spawn_headless(stop_keep_after)
    main.write_bot_pid(proc.pid)
    ready, detail = _wait_until_ready(proc)
    if not ready:
        _stop_child(proc)
        main.clear_bot_pid(proc.pid)
        print("Background bot did not stay running.")
        if detail:
            print(detail)
        print("See logs/bot.log")
        return 1

    print("CLI closed. The bot is running in the background.")
    print(f"pid {proc.pid}")
    print("Stop it with: python stop.py")
    print("Existing positions will NOT be closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main_launcher())

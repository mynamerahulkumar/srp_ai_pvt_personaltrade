"""Request a graceful shutdown of the breakout bot.

Running this file does NOT close positions, cancel orders, or contact
Delta Exchange. main.py notices the stop-signal file on the next poll,
stops accepting new trades, and exits. Existing futures positions remain
open for manual management, TP/SL, or the configured schedule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

STOP_FILE = Path(__file__).resolve().parent / ".bot_stop_signal"


def main() -> None:
    STOP_FILE.write_text(datetime.now(timezone.utc).isoformat() + "Z\n", encoding="utf-8")
    print("STOP SIGNAL SENT")
    print("The bot will stop accepting new trades and exit.")
    print("Existing positions will NOT be closed.")
    print("Close them manually, or let TP/SL / schedule close them.")


if __name__ == "__main__":
    main()

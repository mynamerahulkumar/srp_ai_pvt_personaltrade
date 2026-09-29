"""Ask the running Half Trend bot to stop.

Running ``python stop.py`` creates a local stop file. ``main.py`` sees that
file on its next poll and shuts down. This script does not import the bot,
does not read credentials, and does not talk to Dhan.
"""

from pathlib import Path

STOP_PATH = Path(__file__).resolve().parent / ".bot_stop"


def main() -> None:
    """Create the stop file and confirm that the request was written."""
    STOP_PATH.write_text("stop\n", encoding="utf-8")
    print(
        f"Stop requested ({STOP_PATH.name}). "
        "The running bot will exit on its next poll."
    )


if __name__ == "__main__":
    main()

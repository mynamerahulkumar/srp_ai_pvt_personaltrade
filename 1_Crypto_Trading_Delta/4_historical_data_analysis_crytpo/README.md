# Historical candle downloader

This project downloads Delta Exchange India perpetual candles into CSV files. The program does not place, edit, or cancel exchange orders.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
```

Candle history is a public endpoint. Leave both values in `.env` empty, or set both:

```text
DELTA_API_KEY=
DELTA_API_SECRET=
```

Set both keys or neither. The keys are never printed.

## Configuration

Download settings live in `config.yaml`. Unknown keys stop the program.

`historical_data` chooses symbols, the candle interval, and how many recent days to fetch. `days: 14` downloads the last 14 calendar days through today in `Asia/Kolkata`. Those dates are not written into the filename.

## Command

```bash
python main.py
python main.py --symbol BTCUSD --interval 5m
```

`python main.py` downloads every symbol in `config.yaml`.

Files are written to:

```text
data/historical/BTCUSD_5m.csv
data/historical/ETHUSD_5m.csv
data/historical/SOLUSD_5m.csv
```

## CSV schema

Each file has one row per completed candle:

```text
datetime,open,high,low,close
```

`datetime` is the candle open in `Asia/Kolkata`. The interval is the filename, for example `5m`. Missing candles are not filled in.

Supported intervals: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `1d`, `1w`.

## Tests

```bash
python -m pytest
```

Tests use synthetic candles. They do not write those candles into `data/historical`.

## Troubleshooting

- `Set both DELTA_API_KEY and DELTA_API_SECRET` means `.env` has only one of the two values.
- `Unknown ... configuration keys` means `config.yaml` contains a key this program does not read. Remove it or fix the spelling.
- `No completed candles returned` means the exchange returned nothing for that window, or every bar was still forming. Check the symbol, interval, and `days`.

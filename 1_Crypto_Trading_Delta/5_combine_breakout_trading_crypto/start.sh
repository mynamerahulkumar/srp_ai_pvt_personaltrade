#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "First-time setup has not been done. See setup_readme.md."
  exit 1
fi
exec .venv/bin/python main.py

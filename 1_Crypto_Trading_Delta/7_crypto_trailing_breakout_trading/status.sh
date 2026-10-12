#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif [ -x venv/bin/python ]; then
  PY=venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "Python was not found. Create the venv first. See setup_readme.md."
  exit 1
fi
exec "$PY" status.py "$@"

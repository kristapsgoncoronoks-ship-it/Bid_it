#!/bin/sh
# Fleet Fuel & VAT Refund System — start the app (Linux/macOS).
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Fleet Fuel needs Python 3 to run."
  echo "Install it from  https://www.python.org/downloads/  (or your package manager) and run this again."
  exit 1
fi
exec python3 start.py

#!/bin/sh
# Fleet Fuel & VAT Refund System — double-click to start (macOS).
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Fleet Fuel needs Python 3 to run."
  echo "Get it free from  https://www.python.org/downloads/  then double-click this file again."
  printf "Press Return to close..."; read _
  exit 1
fi
exec python3 start.py

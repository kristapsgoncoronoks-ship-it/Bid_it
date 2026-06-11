#!/bin/sh
# Fleet Fuel & VAT Refund System - one-command installer (Linux/macOS)
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo "Python 3 is required - install it first (https://python.org)"; exit 1; }
exec python3 setup_wizard.py "$@"

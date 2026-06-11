#!/bin/sh
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo "Python 3 required: https://python.org"; exit 1; }
exec python3 start.py

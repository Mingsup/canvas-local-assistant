#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "[Setup] Canvas Local Assistant v1.4 for macOS"
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required. Install it from https://www.python.org/downloads/macos/"
    read -r -p "Press Enter to close..."
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "[Setup] Creating Python virtual environment..."
    python3 -m venv .venv
fi

echo "[Setup] Installing dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo "[Setup] Installing Playwright Chromium if needed..."
.venv/bin/python -m playwright install chromium

echo
echo "[Run] Starting Canvas auto-refresh app..."
.venv/bin/python canvas_assistant.py

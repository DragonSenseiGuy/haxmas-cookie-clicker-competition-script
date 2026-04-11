#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q selenium pyautogui Pillow
python3 run_all.py

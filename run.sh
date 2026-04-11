#!/usr/bin/env bash
set -e

echo "=== Cookie Clicker Competition Runner ==="

# Kill any leftover processes from previous runs
pkill -9 -f chromedriver 2>/dev/null || true
sleep 1

# Chrome + chromedriver
echo "[setup] Chrome: $(google-chrome --version)"

CHROME_MAJOR=$(google-chrome --version | grep -oP '\d+' | head -1)
if ! command -v chromedriver &> /dev/null || ! chromedriver --version 2>/dev/null | grep -q "^ChromeDriver ${CHROME_MAJOR}\."; then
    echo "[setup] Installing chromedriver for Chrome ${CHROME_MAJOR}..."
    DRIVER_URL=$(curl -s "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" \
        | python3 -c "
import json, sys
data = json.load(sys.stdin)
versions = [v for v in data['versions'] if v['version'].startswith('${CHROME_MAJOR}.')]
for v in reversed(versions):
    downloads = v.get('downloads', {}).get('chromedriver', [])
    for d in downloads:
        if d['platform'] == 'linux64':
            print(d['url'])
            sys.exit(0)
sys.exit(1)
" 2>/dev/null) || true

    if [ -n "$DRIVER_URL" ]; then
        wget -q -O /tmp/chromedriver.zip "$DRIVER_URL"
        unzip -o -q /tmp/chromedriver.zip -d /tmp/chromedriver_extract
        sudo cp /tmp/chromedriver_extract/*/chromedriver /usr/local/bin/chromedriver 2>/dev/null || \
            sudo cp /tmp/chromedriver_extract/chromedriver /usr/local/bin/chromedriver
        sudo chmod +x /usr/local/bin/chromedriver
        rm -rf /tmp/chromedriver.zip /tmp/chromedriver_extract
    fi
fi
echo "[setup] chromedriver: $(chromedriver --version)"

# Python venv
VENV_DIR="$(dirname "$0")/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[setup] Creating Python venv..."
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
echo "[setup] Installing Python packages..."
pip install -q selenium pyautogui Pillow

echo "[setup] Done! Starting competition runner..."
echo ""

python3 "$(dirname "$0")/run_all.py"

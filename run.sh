#!/usr/bin/env bash
set -e

echo "=== Cookie Clicker Competition Runner ==="
echo "=== Setting up VM environment...       ==="

# Install system deps
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-pip python3-venv wget unzip curl gnupg imagemagick > /dev/null 2>&1

# Install Google Chrome
if ! command -v google-chrome &> /dev/null; then
    echo "[setup] Installing Google Chrome..."
    wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    sudo apt-get install -y -qq /tmp/chrome.deb > /dev/null 2>&1 || sudo apt-get -f install -y -qq > /dev/null 2>&1
    rm /tmp/chrome.deb
fi
echo "[setup] Chrome: $(google-chrome --version)"

# Install chromedriver matching Chrome version
CHROME_VERSION=$(google-chrome --version | grep -oP '\d+\.\d+\.\d+')
CHROME_MAJOR=$(echo "$CHROME_VERSION" | cut -d. -f1)
echo "[setup] Installing chromedriver for Chrome ${CHROME_MAJOR}..."

# Use Chrome for Testing endpoints (Chrome 115+)
DRIVER_URL=$(curl -s "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json" \
    | python3 -c "
import json, sys
data = json.load(sys.stdin)
versions = [v for v in data['versions'] if v['version'].startswith('${CHROME_MAJOR}.')]
# Get the latest matching version that has chromedriver
for v in reversed(versions):
    downloads = v.get('downloads', {}).get('chromedriver', [])
    for d in downloads:
        if d['platform'] == 'linux64':
            print(d['url'])
            sys.exit(0)
print('', file=sys.stderr)
sys.exit(1)
" 2>/dev/null) || true

if [ -n "$DRIVER_URL" ]; then
    wget -q -O /tmp/chromedriver.zip "$DRIVER_URL"
    unzip -o -q /tmp/chromedriver.zip -d /tmp/chromedriver_extract
    sudo cp /tmp/chromedriver_extract/*/chromedriver /usr/local/bin/chromedriver 2>/dev/null || \
        sudo cp /tmp/chromedriver_extract/chromedriver /usr/local/bin/chromedriver
    sudo chmod +x /usr/local/bin/chromedriver
    rm -rf /tmp/chromedriver.zip /tmp/chromedriver_extract
    echo "[setup] chromedriver installed: $(chromedriver --version)"
else
    echo "[setup] WARN: Could not auto-install chromedriver, trying pip..."
    pip3 install chromedriver-autoinstaller --break-system-packages 2>/dev/null || pip3 install chromedriver-autoinstaller
fi

# Install common Python deps globally so all projects can use them
echo "[setup] Installing common Python packages..."
pip3 install --break-system-packages -q selenium pyautogui Pillow 2>/dev/null || \
    pip3 install -q selenium pyautogui Pillow

# Start virtual display for headless VM
echo "[setup] Starting Xvfb virtual display..."
sudo apt-get install -y -qq xvfb > /dev/null 2>&1
export DISPLAY=:99
Xvfb :99 -screen 0 1920x1080x24 &
XVFB_PID=$!
sleep 1

echo "[setup] Done! Starting competition runner..."
echo ""

# Run the main script
python3 "$(dirname "$0")/run_all.py"

# Cleanup
kill $XVFB_PID 2>/dev/null || true

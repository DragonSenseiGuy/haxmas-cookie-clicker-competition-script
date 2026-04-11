#!/usr/bin/env bash
set -e

echo "=== Cookie Clicker Competition Runner ==="
echo "=== Setting up VM environment...       ==="

# Kill any leftover processes from previous runs
echo "[setup] Cleaning up leftover processes..."
pkill -9 -f Xvfb 2>/dev/null || true
pkill -9 -f chrome 2>/dev/null || true
pkill -9 -f chromedriver 2>/dev/null || true
rm -rf /tmp/.X99-lock 2>/dev/null || true
rm -rf /tmp/.com.google.Chrome.* /tmp/chrome_crashpad /tmp/.org.chromium.* 2>/dev/null || true
sleep 1

# Free up memory: drop caches
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null 2>&1 || true

echo "[setup] Memory before setup: $(free -h | grep Mem | awk '{print "total=" $2 " used=" $3 " avail=" $7}')"

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

# Add swap space to prevent OOM crashes (non-fatal if it fails)
# Try progressively smaller sizes until one works
SWAP_CREATED=false
for SWAP_SIZE in 1G 512M 256M; do
    if [ "$SWAP_CREATED" = true ]; then break; fi
    if [ -f /swapfile ]; then
        sudo swapon /swapfile 2>/dev/null && SWAP_CREATED=true && break
        # Existing swapfile but can't activate - remove and retry
        sudo swapoff /swapfile 2>/dev/null || true
        sudo rm -f /swapfile
    fi
    echo "[setup] Trying ${SWAP_SIZE} swap file..."
    if sudo fallocate -l "$SWAP_SIZE" /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=$(($(echo "$SWAP_SIZE" | sed 's/G/*1024/;s/M//') )) 2>/dev/null; then
        sudo chmod 600 /swapfile
        if sudo mkswap /swapfile > /dev/null 2>&1 && sudo swapon /swapfile 2>/dev/null; then
            SWAP_CREATED=true
        else
            sudo rm -f /swapfile
        fi
    else
        sudo rm -f /swapfile
    fi
done
if [ "$SWAP_CREATED" = false ]; then
    echo "[setup] WARN: Could not create swap file, continuing without swap"
fi
echo "[setup] Swap: $(free -h | grep Swap | awk '{print $2}')"
echo "[setup] Memory: $(free -h | grep Mem | awk '{print "total=" $2 " used=" $3 " free=" $4}')"

# Set up display - use existing display if available (e.g. Kasm VM), otherwise start Xvfb
XVFB_PID=""
if [ -n "$DISPLAY" ] && xdpyinfo -display "$DISPLAY" > /dev/null 2>&1; then
    echo "[setup] Using existing display: $DISPLAY"
else
    echo "[setup] No display found, starting Xvfb virtual display..."
    sudo apt-get install -y -qq xvfb > /dev/null 2>&1
    pkill -9 -f Xvfb 2>/dev/null || true
    rm -f /tmp/.X99-lock 2>/dev/null || true
    sleep 1
    export DISPLAY=:99
    Xvfb :99 -screen 0 1024x768x8 -ac +extension GLX +render -noreset &
    XVFB_PID=$!
    sleep 2
fi

echo "[setup] Done! Starting competition runner..."
echo ""

# Run the main script
python3 "$(dirname "$0")/run_all.py"

# Cleanup
if [ -n "$XVFB_PID" ]; then
    kill $XVFB_PID 2>/dev/null || true
fi

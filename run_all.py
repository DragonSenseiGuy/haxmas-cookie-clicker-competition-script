#!/usr/bin/env python3
"""
Cookie Clicker Competition Runner
Clones each project, installs deps, runs for 60 seconds,
scrapes the cookie count from Chrome, then kills it.
"""

import subprocess
import os
import shutil
import time
import signal
import sys
import re
import json
import resource
import urllib.request
import threading

MAX_MEM_MB = 1024  # Max 1GB per child process (Python + Chrome)
MIN_FREE_MB = 150  # Kill project if system free memory drops below this

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
REPOS_DIR = os.path.join(WORKSPACE, "repos")
SCREENSHOTS_DIR = os.path.join(WORKSPACE, "screenshots")
RESULTS_FILE = os.path.join(WORKSPACE, "results.txt")
TIMEOUT = 60  # seconds

# Common Python entry point filenames to look for, in priority order
ENTRY_POINTS = [
    "main.py",
    "index.py",
    "bot.py",
    "cookie.py",
    "cookie_clicker.py",
    "cookieclicker.py",
    "app.py",
    "run.py",
    "script.py",
    "clicker.py",
    "auto.py",
    "automation.py",
]


def parse_projects(tsv_path):
    """Parse the projects.tsv file and return list of (number, repo_url, subdir)."""
    projects = []
    with open(tsv_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            repo_url = parts[0].strip()

            repo_url = repo_url.rstrip("#").rstrip("/")

            if not repo_url.startswith("http"):
                repo_url = "https://" + repo_url

            subdir = None
            match = re.match(r"(https://github\.com/[^/]+/[^/]+)/tree/[^/]+/(.+)", repo_url)
            if match:
                repo_url = match.group(1)
                subdir = match.group(2)
            else:
                match = re.match(r"(https://github\.com/[^/]+/[^/]+)/blob/[^/]+/.+", repo_url)
                if match:
                    repo_url = match.group(1)

            repo_url_clean = repo_url.rstrip(".git").rstrip("/")

            parts_url = repo_url_clean.split("/")
            if len(parts_url) >= 2:
                owner = parts_url[-2]
                repo = parts_url[-1]
            else:
                owner = "unknown"
                repo = repo_url_clean.split("/")[-1]

            projects.append({
                "repo_url": repo_url,
                "owner": owner,
                "repo": repo,
                "subdir": subdir,
            })
    return projects


def clone_repo(project):
    """Clone a repo into repos/<owner>-<repo>/. Returns the path or None on failure."""
    dest = os.path.join(REPOS_DIR, f"{project['owner']}-{project['repo']}")
    if os.path.exists(dest):
        print(f"  [skip] Already cloned: {dest}")
        return dest

    clone_url = project["repo_url"]
    if not clone_url.endswith(".git"):
        clone_url += ".git"

    print(f"  [clone] {clone_url}")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, dest],
            capture_output=True, text=True, timeout=60, env=env,
        )
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] Clone timed out")
        shutil.rmtree(dest, ignore_errors=True)
        return None
    if result.returncode != 0:
        print(f"  [ERROR] Clone failed: {result.stderr.strip()}")
        shutil.rmtree(dest, ignore_errors=True)
        return None
    return dest


def find_entry_point(repo_path, subdir=None):
    """Find the main Python file to run."""
    search_dir = repo_path
    if subdir:
        search_dir = os.path.join(repo_path, subdir)
        if not os.path.isdir(search_dir):
            search_dir = repo_path

    for name in ENTRY_POINTS:
        candidate = os.path.join(search_dir, name)
        if os.path.isfile(candidate):
            return candidate

    py_files = [f for f in os.listdir(search_dir) if f.endswith(".py") and os.path.isfile(os.path.join(search_dir, f))]
    if len(py_files) == 1:
        return os.path.join(search_dir, py_files[0])

    for f in py_files:
        if f not in ("__init__.py", "setup.py", "config.py", "settings.py", "utils.py", "helpers.py"):
            return os.path.join(search_dir, f)

    if not subdir:
        for d in os.listdir(repo_path):
            sub = os.path.join(repo_path, d)
            if os.path.isdir(sub) and not d.startswith("."):
                result = find_entry_point(sub)
                if result:
                    return result

    return None


def install_deps(repo_path, subdir=None):
    """Install requirements.txt if present."""
    search_dirs = [repo_path]
    if subdir:
        search_dirs.insert(0, os.path.join(repo_path, subdir))

    for d in search_dirs:
        req = os.path.join(d, "requirements.txt")
        if os.path.isfile(req):
            print(f"  [deps] Installing from {req}")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", req, "-q"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"  [WARN] pip install failed: {result.stderr.strip()[:200]}")
            return

    for d in search_dirs:
        pyproject = os.path.join(d, "pyproject.toml")
        if os.path.isfile(pyproject):
            print(f"  [deps] Installing from {pyproject}")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", d, "-q"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"  [WARN] pip install failed: {result.stderr.strip()[:200]}")
            return

    print("  [deps] No requirements.txt or pyproject.toml found, skipping")


def find_chrome_debug_ports():
    """Find all Chrome remote debugging ports from running processes."""
    ports = []
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split("\n"):
            match = re.search(r"--remote-debugging-port=(\d+)", line)
            if match:
                port = int(match.group(1))
                if port not in ports:
                    ports.append(port)
    except Exception:
        pass
    return ports


def scrape_cookie_count(label):
    """
    Connect to Chrome via CDP and extract the cookie count.
    Tries multiple methods to find the count across different Cookie Clicker versions.
    Returns (cookie_count_str, cps_str) or (None, None).
    """
    ports = find_chrome_debug_ports()
    if not ports:
        print("  [scrape] No Chrome debug ports found")
        return None, None

    for port in ports:
        try:
            # Get list of pages
            url = f"http://localhost:{port}/json"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=3)
            pages = json.loads(resp.read().decode())

            if not pages:
                continue

            # Find a cookie clicker tab
            target_ws = None
            for page in pages:
                page_url = page.get("url", "")
                if any(kw in page_url.lower() for kw in ["cookie", "orteil", "dashnet", "ozh"]):
                    target_ws = page.get("webSocketDebuggerUrl")
                    break
            if not target_ws and pages:
                # Just use the first page
                target_ws = pages[0].get("webSocketDebuggerUrl")

            if not target_ws:
                continue

            # Use CDP via the HTTP endpoint to evaluate JS
            # We need to use the /json/version endpoint and then send commands via websocket
            # But simpler: use Selenium to attach to the debug port
            cookie_count, cps = _scrape_via_selenium(port)
            if cookie_count is not None:
                return cookie_count, cps

        except Exception as e:
            print(f"  [scrape] Error on port {port}: {e}")
            continue

    return None, None


def _scrape_via_selenium(port):
    """Connect to existing Chrome via Selenium and extract cookie count."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")

        # Connect without starting a new browser
        driver = webdriver.Chrome(options=opts)

        # Try multiple ways to get cookie count
        cookie_count = None
        cps = None

        # Method 1: Game.cookies (orteil's Cookie Clicker)
        try:
            result = driver.execute_script("""
                if (typeof Game !== 'undefined' && Game.cookies !== undefined) {
                    return {
                        cookies: Math.floor(Game.cookies),
                        cps: Game.cookiesPs,
                        cookiesBaked: Math.floor(Game.cookiesEarned)
                    };
                }
                return null;
            """)
            if result:
                cookie_count = str(result.get("cookiesBaked", result.get("cookies", 0)))
                cps = str(result.get("cps", 0))
        except Exception:
            pass

        # Method 2: DOM scraping - orteil's version
        if cookie_count is None:
            try:
                result = driver.execute_script("""
                    var el = document.getElementById('cookies');
                    if (el) return el.innerText;
                    return null;
                """)
                if result:
                    # Parse "1,234 cookies\nper second: 5.6"
                    lines = result.strip().split("\n")
                    cookie_count = lines[0].replace(" cookies", "").replace(",", "").strip()
                    if len(lines) > 1:
                        cps_match = re.search(r"([\d,.]+)", lines[1])
                        if cps_match:
                            cps = cps_match.group(1)
            except Exception:
                pass

        # Method 3: ozh's Cookie Clicker
        if cookie_count is None:
            try:
                result = driver.execute_script("""
                    var el = document.querySelector('#money');
                    if (el) return el.innerText;
                    el = document.querySelector('.cookieCount');
                    if (el) return el.innerText;
                    el = document.querySelector('#cookieCount');
                    if (el) return el.innerText;
                    return null;
                """)
                if result:
                    cookie_count = result.replace(",", "").strip()
            except Exception:
                pass

        # Method 4: Generic - find any large number on the page
        if cookie_count is None:
            try:
                result = driver.execute_script("""
                    var title = document.title;
                    if (title) return title;
                    return null;
                """)
                if result:
                    # Cookie Clicker titles often show cookie count
                    nums = re.findall(r"[\d,]+", result)
                    if nums:
                        cookie_count = nums[0].replace(",", "")
            except Exception:
                pass

        # Take a screenshot too
        try:
            screenshot_path = os.path.join(SCREENSHOTS_DIR, f"port_{port}.png")
            driver.save_screenshot(screenshot_path)
        except Exception:
            pass

        # DON'T call driver.quit() - we don't want to close the browser
        # Just disconnect
        driver.command_executor._conn = None

        return cookie_count, cps

    except Exception as e:
        print(f"  [scrape] Selenium attach failed on port {port}: {e}")
        return None, None


def take_screenshot(label):
    """Take a screenshot of the current display using import (ImageMagick)."""
    safe_label = re.sub(r'[^a-zA-Z0-9_-]', '_', label)
    screenshot_path = os.path.join(SCREENSHOTS_DIR, f"{safe_label}.png")
    try:
        subprocess.run(
            ["import", "-window", "root", screenshot_path],
            capture_output=True, timeout=5,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")},
        )
        if os.path.exists(screenshot_path):
            print(f"  [screenshot] Saved: {screenshot_path}")
            return screenshot_path
    except Exception as e:
        print(f"  [screenshot] Failed: {e}")
    return None


def kill_all_chrome():
    """Kill Chrome/chromedriver processes spawned by us, NOT Kasm's own processes.
    Only kills processes whose parent is in our process tree (PPID = our PID or children)."""
    our_pid = os.getpid()
    try:
        # Find all chrome/chromedriver PIDs and their parent PIDs
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True, timeout=5
        )
        our_pids = {our_pid}
        # Build set of PIDs in our process tree (children, grandchildren, etc.)
        lines = result.stdout.strip().split("\n")
        # Multiple passes to catch the full tree
        for _ in range(5):
            for line in lines:
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    try:
                        pid, ppid = int(parts[0]), int(parts[1])
                        if ppid in our_pids:
                            our_pids.add(pid)
                    except ValueError:
                        pass

        # Kill only chrome/chromedriver processes in our tree
        for line in lines:
            parts = line.split(None, 2)
            if len(parts) >= 3:
                try:
                    pid = int(parts[0])
                    cmd = parts[2].lower()
                    if pid in our_pids and any(k in cmd for k in ["chrome", "chromedriver"]):
                        os.kill(pid, signal.SIGKILL)
                except (ValueError, ProcessLookupError, OSError):
                    pass
    except Exception:
        pass
    # Always safe to kill chromedriver (not a Kasm component)
    subprocess.run(["pkill", "-9", "-f", "chromedriver"], capture_output=True)
    time.sleep(1)


def get_free_memory_mb():
    """Get available system memory in MB (MemAvailable from /proc/meminfo)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024  # kB -> MB
    except Exception:
        pass
    return 9999  # If we can't read, assume plenty


def run_project(entry_point, label, timeout=TIMEOUT):
    """Run a project for `timeout` seconds, scrape cookies, then kill it."""
    work_dir = os.path.dirname(entry_point)
    print(f"  [run] {entry_point} (timeout={timeout}s)")
    print(f"  [run] Working dir: {work_dir}")

    env = os.environ.copy()
    env.setdefault("DISPLAY", ":99")

    cookie_count = None
    cps = None
    oom_killed = False

    def _preexec():
        os.setsid()

    # Memory watchdog: monitors free memory and kills process group if too low
    watchdog_stop = threading.Event()

    def _memory_watchdog(proc):
        nonlocal oom_killed
        while not watchdog_stop.is_set():
            free_mb = get_free_memory_mb()
            if free_mb < MIN_FREE_MB:
                print(f"  [OOM-GUARD] Free memory critically low ({free_mb}MB < {MIN_FREE_MB}MB), killing project!")
                oom_killed = True
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                kill_all_chrome()
                return
            watchdog_stop.wait(2)  # Check every 2 seconds

    try:
        proc = subprocess.Popen(
            [sys.executable, entry_point],
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            preexec_fn=_preexec,
        )

        # Start memory watchdog thread
        watchdog = threading.Thread(target=_memory_watchdog, args=(proc,), daemon=True)
        watchdog.start()

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if not oom_killed:
                print(f"  [scrape] Timeout reached, scraping cookie count before killing...")

                # Scrape cookie count from Chrome BEFORE killing
                cookie_count, cps = scrape_cookie_count(label)

                # Take a screenshot too
                take_screenshot(label)

            # Now kill the process group
            print(f"  [kill] Killing process group...")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            time.sleep(2)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except Exception:
                stdout, stderr = b"", b""
        finally:
            watchdog_stop.set()

        status_note = "OOM_KILLED" if oom_killed else None

        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode,
            cookie_count,
            cps,
            status_note,
        )
    except Exception as e:
        watchdog_stop.set()
        return ("", str(e), -1, None, None, None)


def main():
    tsv_path = os.path.join(WORKSPACE, "projects.tsv")
    projects = parse_projects(tsv_path)
    print(f"Found {len(projects)} projects\n")

    os.makedirs(REPOS_DIR, exist_ok=True)
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    results = []

    for i, project in enumerate(projects, 1):
        label = f"{project['owner']}/{project['repo']}"
        print(f"\n{'='*60}")
        print(f"[{i}/{len(projects)}] {label}")
        print(f"{'='*60}")

        # Kill leftover Chrome from previous run
        kill_all_chrome()

        # Clone
        repo_path = clone_repo(project)
        if not repo_path:
            results.append({"project": label, "status": "CLONE_FAILED", "cookies": None, "cps": None})
            continue

        # Find entry point
        entry = find_entry_point(repo_path, project.get("subdir"))
        if not entry:
            print(f"  [ERROR] No Python entry point found")
            results.append({"project": label, "status": "NO_ENTRY_POINT", "cookies": None, "cps": None})
            continue

        print(f"  [entry] {entry}")

        # Install deps
        install_deps(repo_path, project.get("subdir"))

        # Run
        stdout, stderr, rc, cookies, cps, status_note = run_project(entry, label)

        # Kill Chrome immediately after each run
        kill_all_chrome()

        if cookies:
            print(f"  [COOKIES] 🍪 {cookies} cookies (CPS: {cps})")
        else:
            print(f"  [COOKIES] ❌ Could not scrape cookie count")

        if status_note == "OOM_KILLED":
            status = "OOM_KILLED"
        elif rc in (0, -15, -9, None):
            status = "OK"
        else:
            status = f"EXIT_{rc}"

        print(f"  [done] Return code: {rc}" + (f" ({status_note})" if status_note else ""))
        if stdout.strip():
            lines = stdout.strip().split("\n")
            tail = "\n".join(lines[-20:])
            print(f"  [stdout tail]\n{tail}")
        if stderr.strip():
            lines = stderr.strip().split("\n")
            tail = "\n".join(lines[-10:])
            print(f"  [stderr tail]\n{tail}")

        results.append({
            "project": label,
            "status": status,
            "cookies": cookies,
            "cps": cps,
            "stdout": stdout,
            "stderr": stderr,
        })

    # Kill any remaining Chrome
    kill_all_chrome()

    # Write results summary
    print(f"\n\n{'='*60}")
    print("RESULTS SUMMARY - SORTED BY COOKIES")
    print(f"{'='*60}")

    # Sort by cookie count (descending), with None at the bottom
    def sort_key(r):
        c = r.get("cookies")
        if c is None:
            return -1
        try:
            return int(c)
        except (ValueError, TypeError):
            try:
                return int(float(c))
            except:
                return -1

    sorted_results = sorted(results, key=sort_key, reverse=True)

    with open(RESULTS_FILE, "w") as f:
        f.write("RANK | PROJECT | STATUS | COOKIES | CPS\n")
        f.write("-" * 70 + "\n")

        rank = 1
        for r in sorted_results:
            cookies_str = r.get("cookies") or "N/A"
            cps_str = r.get("cps") or "N/A"
            line = f"#{rank:3d} | {r['project']:<50s} | {r['status']:<10s} | {cookies_str:>12s} | {cps_str}"
            print(line)
            f.write(line + "\n")
            rank += 1

        f.write("\n\n" + "=" * 70 + "\n")
        f.write("DETAILED OUTPUT\n")
        f.write("=" * 70 + "\n\n")

        for r in sorted_results:
            f.write(f"\n--- {r['project']} ---\n")
            f.write(f"Status: {r['status']}\n")
            f.write(f"Cookies: {r.get('cookies', 'N/A')}\n")
            f.write(f"CPS: {r.get('cps', 'N/A')}\n")
            if r.get("stdout"):
                f.write("stdout:\n" + r["stdout"][-500:] + "\n")
            if r.get("stderr"):
                f.write("stderr:\n" + r["stderr"][-500:] + "\n")

    print(f"\nFull results written to {RESULTS_FILE}")
    print(f"Screenshots saved to {SCREENSHOTS_DIR}/")


if __name__ == "__main__":
    main()

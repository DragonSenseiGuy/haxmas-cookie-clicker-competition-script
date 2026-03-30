#!/usr/bin/env python3
"""
Cookie Clicker Competition Runner
Clones each project, installs deps, runs for 60 seconds, then kills it.
"""

import subprocess
import os
import shutil
import time
import signal
import sys
import re
import csv

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
REPOS_DIR = os.path.join(WORKSPACE, "repos")
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

            # Clean up the repo URL
            # Some entries have line numbers prepended, some have .git suffix, some have /tree/main, /blob/main, etc.
            # Normalize to a clean clone-able URL
            repo_url = repo_url.rstrip("#").rstrip("/")

            # Add https:// if missing
            if not repo_url.startswith("http"):
                repo_url = "https://" + repo_url

            # Handle URLs with /blob/main/... or /tree/main/... — extract the base repo
            # e.g. https://github.com/DZDevelopers/Cookie-Clicker-script/blob/main/main.py
            # Also handle https://github.com/CodingWithHardik/haxmas/tree/main/day8
            subdir = None
            match = re.match(r"(https://github\.com/[^/]+/[^/]+)/tree/[^/]+/(.+)", repo_url)
            if match:
                repo_url = match.group(1)
                subdir = match.group(2)
            else:
                match = re.match(r"(https://github\.com/[^/]+/[^/]+)/blob/[^/]+/.+", repo_url)
                if match:
                    repo_url = match.group(1)

            # Remove .git suffix for consistent naming
            repo_url_clean = repo_url.rstrip(".git").rstrip("/")

            # Extract owner/repo for folder naming
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
    result = subprocess.run(
        ["git", "clone", "--depth", "1", clone_url, dest],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"  [ERROR] Clone failed: {result.stderr.strip()}")
        return None
    return dest


def find_entry_point(repo_path, subdir=None):
    """Find the main Python file to run."""
    search_dir = repo_path
    if subdir:
        search_dir = os.path.join(repo_path, subdir)
        if not os.path.isdir(search_dir):
            search_dir = repo_path

    # First, check for known entry points
    for name in ENTRY_POINTS:
        candidate = os.path.join(search_dir, name)
        if os.path.isfile(candidate):
            return candidate

    # Fallback: find any .py file in the directory (not in subdirs)
    py_files = [f for f in os.listdir(search_dir) if f.endswith(".py") and os.path.isfile(os.path.join(search_dir, f))]
    if len(py_files) == 1:
        return os.path.join(search_dir, py_files[0])

    # If multiple, prefer ones that look like entry points
    for f in py_files:
        if f not in ("__init__.py", "setup.py", "config.py", "settings.py", "utils.py", "helpers.py"):
            return os.path.join(search_dir, f)

    # Last resort: check subdirectories
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

    # Check for pyproject.toml
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


def run_project(entry_point, timeout=TIMEOUT):
    """Run a project for `timeout` seconds, then kill it. Returns (stdout, stderr, returncode)."""
    work_dir = os.path.dirname(entry_point)
    print(f"  [run] {entry_point} (timeout={timeout}s)")
    print(f"  [run] Working dir: {work_dir}")

    env = os.environ.copy()
    # Ensure headless-friendly environment for VM
    env.setdefault("DISPLAY", ":99")

    try:
        proc = subprocess.Popen(
            [sys.executable, entry_point],
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            preexec_fn=os.setsid,  # Create new process group so we can kill children
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the entire process group
            print(f"  [kill] Timeout reached ({timeout}s), killing process group...")
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            time.sleep(2)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate(timeout=5)

        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode,
        )
    except Exception as e:
        return ("", str(e), -1)


def main():
    tsv_path = os.path.join(WORKSPACE, "projects.tsv")
    projects = parse_projects(tsv_path)
    print(f"Found {len(projects)} projects\n")

    os.makedirs(REPOS_DIR, exist_ok=True)

    results = []

    for i, project in enumerate(projects, 1):
        label = f"{project['owner']}/{project['repo']}"
        print(f"\n{'='*60}")
        print(f"[{i}/{len(projects)}] {label}")
        print(f"{'='*60}")

        # Clone
        repo_path = clone_repo(project)
        if not repo_path:
            results.append({"project": label, "status": "CLONE_FAILED", "output": ""})
            continue

        # Find entry point
        entry = find_entry_point(repo_path, project.get("subdir"))
        if not entry:
            print(f"  [ERROR] No Python entry point found")
            results.append({"project": label, "status": "NO_ENTRY_POINT", "output": ""})
            continue

        print(f"  [entry] {entry}")

        # Install deps
        install_deps(repo_path, project.get("subdir"))

        # Run
        stdout, stderr, rc = run_project(entry)

        print(f"  [done] Return code: {rc}")
        if stdout.strip():
            # Show last 20 lines of stdout
            lines = stdout.strip().split("\n")
            tail = "\n".join(lines[-20:])
            print(f"  [stdout tail]\n{tail}")
        if stderr.strip():
            lines = stderr.strip().split("\n")
            tail = "\n".join(lines[-10:])
            print(f"  [stderr tail]\n{tail}")

        results.append({
            "project": label,
            "status": "OK" if rc in (0, -15, -9, None) else f"EXIT_{rc}",
            "stdout": stdout,
            "stderr": stderr,
        })

    # Write results summary
    print(f"\n\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")

    with open(RESULTS_FILE, "w") as f:
        for r in results:
            line = f"{r['project']}: {r['status']}"
            print(line)
            f.write(line + "\n")
            if r.get("stdout"):
                f.write("--- stdout ---\n")
                f.write(r["stdout"] + "\n")
            if r.get("stderr"):
                f.write("--- stderr ---\n")
                f.write(r["stderr"] + "\n")
            f.write("\n")

    print(f"\nFull results written to {RESULTS_FILE}")


if __name__ == "__main__":
    main()

"""
The browser stack for the House-toggle e2e tests, started by pytest itself.

Before Phase 13.y these tests ran only through scripts/e2e_house_toggle.sh,
which starts the servers and sets E2E_BASE_URL; in the ordinary full-suite
run nothing started them, so all four always skipped ("E2E_BASE_URL not
set"). Now:

  * E2E_BASE_URL set  -> use that already-running stack (the script's way);
  * otherwise         -> start it here: backend_v2's API on :8000 (the
    frontend's Vite proxy target) and the Vite dev server on :3000, both
    against the suite's own DATABASE_URL, and stop them when tests/e2e is done.

Prerequisites: Playwright + Chromium, Node/npx, and the frontend's
node_modules (E2E_FRONTEND_DIR points at a frontend copy whose
node_modules were installed on this platform; default: repo frontend/).
A missing prerequisite or a busy port skips with the reason -- unless
E2E_REQUIRED=1 (CI's pytest step, and the local full-suite image), where it
is a failure, so the suite can never pass by skipping these tests.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REQUIRED = os.environ.get("E2E_REQUIRED") == "1"
API_PORT, WEB_PORT = 8000, 3000  # frontend/vite.config.js proxies /api to :8000


def _unavailable(reason: str):
    if REQUIRED:
        pytest.fail(f"E2E_REQUIRED=1: {reason}", pytrace=False)
    pytest.skip(reason)


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait(url: str, deadline: float, procs) -> None:
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        for name, p in procs:
            if p.poll() is not None:
                raise RuntimeError(f"{name} exited with {p.returncode} before {url} answered")
        if time.time() > deadline:
            raise RuntimeError(f"timed out waiting for {url}")
        time.sleep(1)


@pytest.fixture(scope="session")
def playwright_api():
    try:
        import playwright.sync_api as sync_api
    except ImportError:
        _unavailable("playwright is not installed")
    return sync_api


@pytest.fixture(scope="package")  # the servers stop as soon as the e2e tests finish
def base(playwright_api, tmp_path_factory):
    external = os.environ.get("E2E_BASE_URL")
    if external:
        yield external.rstrip("/")
        return

    frontend = Path(os.environ.get("E2E_FRONTEND_DIR") or BACKEND.parent / "frontend")
    npx = shutil.which("npx")
    if not npx:
        _unavailable("node/npx is not installed")
    if not (frontend / "node_modules" / ".bin").exists():
        _unavailable(f"no node_modules in {frontend} (run npm ci there, or set E2E_FRONTEND_DIR)")
    for port in (API_PORT, WEB_PORT):
        if _port_busy(port):
            _unavailable(f"port {port} is already in use; stop that server or set E2E_BASE_URL")

    logs = Path(os.environ.get("E2E_LOG_DIR") or tmp_path_factory.mktemp("e2e-logs"))
    logs.mkdir(parents=True, exist_ok=True)
    api_log, web_log = open(logs / "api.log", "w"), open(logs / "vite.log", "w")
    procs = []
    try:
        procs.append(
            (
                "api",
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        f"{API_PORT}",
                    ],
                    cwd=BACKEND,
                    stdout=api_log,
                    stderr=subprocess.STDOUT,
                ),
            )
        )
        procs.append(
            (
                "vite",
                subprocess.Popen(
                    [npx, "vite", "--host", "127.0.0.1", "--port", str(WEB_PORT), "--strictPort"],
                    cwd=frontend,
                    stdout=web_log,
                    stderr=subprocess.STDOUT,
                ),
            )
        )
        deadline = time.time() + 180
        url = f"http://127.0.0.1:{WEB_PORT}"
        # ready = the API answers, the frontend serves, and its proxy reaches the API
        for probe in (f"http://127.0.0.1:{API_PORT}/api/health", f"{url}/", f"{url}/api/health"):
            _wait(probe, deadline, procs)
        yield url
    except RuntimeError as e:
        pytest.fail(f"e2e stack did not start: {e} (logs in {logs})", pytrace=False)
    finally:
        for _, p in procs:
            p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
        api_log.close()
        web_log.close()

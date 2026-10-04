#!/usr/bin/env bash
# Phase 3 House-toggle browser test, end to end: starts the backend_v2
# API (:8000) and the frontend Vite dev server (:3000, whose /api proxy
# targets :8000), waits until the proxied API answers, then runs
# tests/e2e with Playwright. It runs ONLY the browser tests; the full pytest
# run (and CI's regression job) starts the same stack itself through
# tests/e2e/conftest.py.
#
# Prerequisites (CI installs them in earlier steps):
#   pip install -r backend_v2/requirements-dev.txt playwright==1.47.0
#   python -m playwright install --with-deps chromium
#   (cd frontend && npm ci)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="${E2E_LOG_DIR:-$ROOT/e2e-logs}"
mkdir -p "$LOG_DIR"

cd "$ROOT/backend_v2"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >"$LOG_DIR/api.log" 2>&1 &
API_PID=$!
(cd "$ROOT/frontend" && exec npx vite --host 0.0.0.0 --port 3000 --strictPort) >"$LOG_DIR/vite.log" 2>&1 &
VITE_PID=$!
trap 'kill "$API_PID" "$VITE_PID" 2>/dev/null || true' EXIT

# Ready means the frontend serves AND its proxy reaches backend_v2
# (/api/health through the proxy). The API uses DATABASE_URL, so run this
# against a built database: the tests load real pages.
python - <<'EOF'
import sys, time, urllib.request
deadline = time.time() + 120
for url in ("http://localhost:8000/api/health", "http://localhost:3000/", "http://localhost:3000/api/health"):
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    print("ready:", url)
                    break
        except Exception:
            pass
        if time.time() > deadline:
            sys.exit(f"timed out waiting for {url}")
        time.sleep(1)
EOF

E2E_BASE_URL=http://localhost:3000 E2E_REQUIRED=1 \
  python -m pytest tests/e2e -v -rs -p no:cacheprovider "$@"

# Local image for the FULL pytest run, browser e2e included (the same set of
# tools CI's regression job installs): Python 3.12 + dev requirements,
# Node 20 for the Vite dev server, Playwright 1.47 + Chromium.
#
#   docker build -f ops/ci/fullsuite.Dockerfile -t sentinel-v2-fullsuite backend_v2
#
# Run it with the repo mounted and E2E_REQUIRED=1. The frontend's node_modules
# must be installed for Linux, so copy frontend/ without node_modules, run
# `npm ci` there and point E2E_FRONTEND_DIR at the copy (a Windows or macOS
# node_modules has the wrong native esbuild binary).
FROM node:20-bookworm-slim AS node

# Debian 12 (bookworm): Playwright 1.47 cannot install browser deps on Debian 13 (trixie)
FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
WORKDIR /srv
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt playwright==1.47.0 \
 && python -m playwright install --with-deps chromium

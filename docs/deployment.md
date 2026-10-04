# MPLADS Sentinel: deployment

Updated 2026-10-02 to the deployed layout: Vercel Services, frontend and backend in one project. The first Phase 14 layout (2026-09-28: a Vercel frontend calling a local Docker backend through a Cloudflare quick tunnel) is superseded; its procedure is in git history before this commit.

## Architecture

| Part | Where | What |
| --- | --- | --- |
| **Frontend** | Vercel service `frontend` | `frontend/`, framework `vite`. The SPA fallback is this service's own `rewrites` entry in the root `vercel.json`. |
| **Backend** | Vercel service `backend` | `backend_v2/`, runtime `container`, built from `backend_v2/Dockerfile.vercel` (FastAPI on uvicorn). Stateless: the data lives in the database. |
| **Routing** | Root `vercel.json`, top-level `rewrites`, in order | `/api/*` → `backend`; everything else → `frontend`. |
| **Database** | PostgreSQL (`DATABASE_URL`) | A slim copy of published **run 44**, checksum `c4d589e0e0fc40621d4e011a64a7233d`. See "Slim database" below. |
| **Worker** | Not deployed | Runs are computed offline; production serves run 44. |

- The frontend and the API share one origin. With `VITE_API_BASE_URL` unset, the frontend calls `/api` (`frontend/src/services/api.js:1`), which Vercel routes to the backend.
- SPA fallback: a path whose last segment has no dot (no file extension) is served `/index.html`, so deep links and refreshes load the app. A path with an extension (an asset, or a missing file) is not rewritten. `backend_v2/tests/test_phase14_deploy.py::test_vercel_layout_frontend_spa_and_backend_container` pins the layout and the rewrite.
- `frontend/vercel.json` (the standalone SPA rewrite from the first layout) is still in the repo. The Services layout routes through the root `vercel.json`.

## Environment variables

Names, and where each is set, are in `DEPLOY_NOTES.md`. Values are never written in the repo.

- **Required, set in the Vercel project:** `JWT_SECRET`, `DATABASE_URL`, `CORS_ORIGINS`.
- **Pinned in `backend_v2/Dockerfile.vercel`** (not set in the dashboard): `APP_ENV`, `TRUST_PROXY_HEADERS`.
- **Never set on Vercel:** `WARM_CACHE`.
- **Optional:** `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE`.
- **Post-deploy gate only, not the service:** `GATE_SLIM`.

`VITE_*` variables are compiled into the public JavaScript bundle, so never put a key in one (`frontend/.env.example`).

## Slim database

- Production carries only what the API serves. 15 tables exist but are empty: the offline pipeline's inputs and intermediates, plus `state_alias` (`SLIM_EMPTY_TABLES`, `backend_v2/scripts/postdeploy_gate.py`).
- One of them is `atypicality_result`, so atypicality evidence is not available in production (`docs/ml_architecture.md` §2).
- Post-deploy gate, run against the production database:
  ```bash
  cd backend_v2
  GATE_SLIM=1 DATABASE_URL=... python scripts/postdeploy_gate.py --manifest ../ops/deploy/manifest_run44.json --api-url <deployment URL>
  ```
  Slim mode skips the row counts of those 15 tables. It still requires them to exist, and it still checks the Alembic revision, published run 44, the `risk_result` checksum `c4d589e0…`, the serving build and the tier counts. With `--api-url` it also checks that `/api/health` answers and `/api/summary` serves the same CRITICAL and HIGH totals.
- Not yet run against production (`DEPLOY_NOTES.md`, "Tests to run per commit").

Checks to run once on a preview (client IP, SPA refresh on `/map`): `DEPLOY_NOTES.md`, "Verify once on a preview".

## CORS

- `CORS_ORIGINS` is an exact list read from the environment. A `*` refuses to start the API (`backend_v2/app/main.py`), `CORS_ORIGIN_REGEX` is empty by default, and credentials are off.
- The deployed frontend calls the API on its own origin, so its own requests are same-origin.
- The frontend uses **no cookies and no Authorization header**: every call is a plain `fetch`. So no cross-site cookie settings (`SameSite=None; Secure`, `allow_credentials`) are needed.

## Client IP and rate limits

- `backend_v2/Dockerfile.vercel` pins `TRUST_PROXY_HEADERS=1`; the platform also sets `VERCEL` (`backend_v2/app/core/config.py`). The limiter then keys on the right-most `X-Forwarded-For` entry, the one Vercel's proxy appended, else `X-Real-IP` (`backend_v2/app/auth/ratelimit.py`).
- Whether Vercel appends an internal hop after the client is still to be verified on a preview (`DEPLOY_NOTES.md`).
- The limiter keeps its counters in each instance's memory, so the effective limit is the configured limit times the number of warm instances, and a cold start begins at zero (`DEPLOY_NOTES.md`; `docs/security.md`, "Limitations").
- The `TRUSTED_PROXIES` / `CF-Connecting-IP` path belongs to the tunnel layout and is unused here.
- **Fixed in Phase 14:** the old `TRUST_FORWARDED_FOR` flag took the left-most `X-Forwarded-For` entry from any caller, which let a client choose its own rate-limit key. It has been removed.

## Access from the public site

- The public site is anonymous and calls the API with no token. With `ANONYMOUS_READ=true` (the code default, `backend_v2/app/core/config.py`) it gets the national, read-only `public` role.
- These all answer **401** to it:
  - investigate, and the recalculate request;
  - the audit trail and its verification, and the case export;
  - audit samples, items, reports and reviews;
  - payee profiles.
- `tests/test_phase14_deploy.py` checks each one. They were also checked live through the tunnel in Phase 14; not yet re-checked on Vercel.
- `ANONYMOUS_READ=false` closes every data endpoint to anonymous callers, which also takes the public site down, because it cannot log in.

## Backups

Production database backups are the owner's responsibility. The repo's backup job (`ops/backup/backup.sh`, the compose `backup` service) and its restore test (`docs/restore_test_report.md`) cover the local Docker stack only; nothing in this repo backs up the production database.

## Known limitations

- **No Graph route.** The graph API exists, but `App.jsx` has no page for it.
- **No not-found page.** An unknown path renders the app shell with an empty content area. The SPA fallback serves `index.html`, so it is never a server 404.
- **Recalculate** on the Record page shows its existing "request failed" notice, because the public role cannot write case events.
- **Investigate, audit and reviewer features are unreachable from the public site**, which sends no token.
- **The login page is a client-side demo.** It never calls `/api/auth/login`.
- **Rate limiting is per instance** (see above).
- **Atypicality evidence is not available** in production: `atypicality_result` is empty there.

## Tests

- `backend_v2/tests/test_phase14_deploy.py`: the root `vercel.json` layout (services, routing order, the SPA fallback); CORS preflight from the production origin passes and other origins are refused; the client-IP rules and a spoofing attempt; the anonymous 401s and the `ANONYMOUS_READ` switch. It also still checks the first layout's deploy files.
- `python ops/ci/secret_scan.py --root frontend/dist --walk`: the build output holds no key patterns.
- Browser tests (every route by direct navigation and refresh, query strings, the House selector after a refresh, an unknown path, the copilot) were run in Phase 14 against the first layout (`docs/validation_report_v1.md` §12). They have not been repeated on the Services layout.
- The Phase 0 contract suite (`backend_v2/tests/test_contract.py`) has not been run against the deployed site.

# Security, audit and access (Phase 13)

This document maps each row of BLUEPRINT.md §11 "Controls" to its implementation and to the test
that checks it. The tests are in `backend_v2/tests/test_phase13_security.py`, one section per row.

## Controls

| §11 row | Implementation | Checked by |
| --- | --- | --- |
| **Authentication** | See [Authentication](#authentication). | `test_auth_*` (4 tests) |
| **Authorisation** | See [Authorisation](#authorisation). | `test_authz_*` (6 tests) |
| **Identity in audit** | The actor on every case event and audit review comes from the token. A `reviewer` in the body is ignored on `/api/investigate` and rejected (422) on audit reviews. | `test_identity_actor_comes_from_the_token` |
| **Audit trail** | See [Audit trail](#audit-trail). | `test_audit_trail_*` |
| **Imports** | Imports run only as server-side jobs over registered snapshots (`scripts/run_ingest.py` and the pipeline scripts). `POST /api/load` is gone. No route has a path, file or directory parameter. | `test_imports_*` |
| **CORS** | See [CORS](#cors). | `test_cors_*` |
| **Rate limits** | See [Rate limits](#rate-limits). | `test_rate_limits_login_chat_and_search` |
| **Input handling** | Every request body is a Pydantic model with bounds (decision ≤ 60 chars, note ≤ 4,000, chat message ≤ 2,000, history ≤ 20). Every free-text parameter is length-bounded. Values always go through SQL bind parameters; text filters are literal `LIKE … ESCAPE` matches. | `test_input_*`, including 5 injection payloads × 6 parameters |
| **Secrets** | See [Secrets](#secrets). | `test_secrets_*` |
| **Backups** | See [Backups](#backups). | `test_backups_are_scheduled_and_the_restore_was_executed` |
| **Copilot** | See [Copilot](#copilot). | `test_copilot_*`, and `tests/test_phase13_claims.py` |
| **Personal data** | See [Personal data](#personal-data). | `test_personal_data_*` |

### Authentication

- **Login:** `POST /api/auth/login` returns a JWT (HS256) valid for 15 minutes (`ACCESS_TOKEN_MINUTES`).
- **Claims:** `iss`, `aud`, `sub`, `iat`, `exp`, `jti`, the OIDC-style registered set. The algorithm is pinned, so "none" and algorithm-confusion tokens are rejected.
- **Passwords:** stored only as salted scrypt hashes, with a 12-character minimum.
- **Failed logins:** a wrong password and an unknown user get the same 401. Timing is equalised with a dummy hash check.
- **No refresh token.** Role and scope are re-read from `app_user` on every request, so disabling an account takes effect at once.

### Authorisation

- **Roles:** `admin`, `ministry`, `state`, `district`, `mp`, `investigator`, `supervisor` and `auditor` (BLUEPRINT.md §10), plus the anonymous `public` role.
- **Scopes:** an account can be limited by House, state, district authority or MP. The scope is applied inside the SQL of every data query:
  - `served_work` queries carry the conditions directly.
  - `map_work` and the case log use an `EXISTS` against `served_work`.
- **Out-of-scope records** read as absent (404).
- **Figures that can't be restricted to a scope** are refused (403) rather than shown nationally: the graph and payee/agency profiles.
- **Scoped map endpoints** never use the precomputed national tables; they re-aggregate from `map_work` under the scope.

### Audit trail

- `case_event` is append-only; no application code updates or deletes it.
- Timestamps come from the server.
- Every event is hash-chained: `event_hash = sha256(prev_hash + canonical event)`. Appends take an advisory lock so the chain can't fork.
- `GET /api/audit-trail/verify` recomputes the chain.
- `GET /api/cases/{id}/events` exports one case with its hashes.

### CORS

- The allowlist comes from `CORS_ORIGINS`, plus an optional `CORS_ORIGIN_REGEX` for preview deployments.
- Defaults are the two local dev origins, `:3000` and `:4173`.
- A `*` refuses to start the app.
- Credentials are off, because tokens travel in the `Authorization` header.
- The Phase 14 frontend domain is added then; it isn't guessed here.

### Rate limits

| Endpoint | Limit (per minute) | Setting |
| --- | --- | --- |
| Login | 10 per address and 10 per username | `RATE_LIMIT_LOGIN_PER_MINUTE` |
| Copilot | 30 per address, and per user when authenticated | `RATE_LIMIT_CHAT_PER_MINUTE` |
| Search (queue, map-data, map-works, area works, when a search term is given) | 300 | `RATE_LIMIT_SEARCH_PER_MINUTE` |

Requests over the limit get 429 with `Retry-After`.

### Secrets

- **Configuration:** everything comes from the environment. `JWT_SECRET` has no usable default: outside `APP_ENV=development`, an unset or short key refuses to start.
- **Ignore rules:** `.gitignore`, `backend_v2/.dockerignore` and the root `.dockerignore` exclude `.env`, `.env.*`, `**/.env` and `**/.env.*` at every level; `.env.example` stays trackable. No Dockerfile copies a `.env`.
- **CI secret scan:** `ops/ci/secret_scan.py --history` is the first step of the CI `regression` job. It fails if any tracked file, or any blob in the git history, contains a key pattern (Google API key or token, private key, AWS, GitHub, Slack, OpenAI/Anthropic-style key, JWT), a `.env` file, or a secret-named variable set to a literal. It prints file, line and pattern name only, never a value. A reviewed exception needs a `secret-scan: ignore` comment on the line or an entry in the script's `ALLOWLIST`.
- **History:** the repository was initialised on 2026-09-28 with the ignore rules committed first (`1cb577c`), then the initial commit (`7f287eb`). `secret_scan.py --history` scans every blob of both commits and finds 0. Only the two `.env.example` templates are tracked. Nothing has been pushed.
- **Local key:** a real Gemini API key was on disk in two git-ignored files. The old prototype's copy, `archive/backend_v1/backend/.env`, is deleted. The repo-root `.env` is the developer's own local configuration.
- **Rotate the key (owner action).** Per §11 ("rotate any key ever shared"), it should be rotated in Google Cloud and the old one revoked. That can't be done from this codebase.

### Backups

- **Scheduled backups:** the compose `backup` service runs `ops/backup/backup.sh`. It takes a daily `pg_dump` in custom format, checks each dump is readable, writes a SHA-256, and keeps the newest 14.
- **Executed restore test:** `ops/backup/restore_test.sh`.
  1. Back up with the same script.
  2. Restore into a brand-new PostgreSQL 16 container.
  3. Verify tables, row counts, the published run, the risk checksum, the serving build and the case chain.
  4. Run the contract and Phase 12 suites against the restored copy.
- **Results:** in `docs/restore_test_report.md`. The same script runs in CI.

### Copilot

- It is grounded on stored results; a work outside the caller's scope is answered as "no stored result".
- Its method text is generated from configuration.
- It is rate-limited.
- It receives only the message, the history and the method text, never a token or credentials.
- Phone numbers in the message and history are masked before anything is sent to the model.
- It never scores or writes anything.
- Its system prompt forbids every §14 "cannot claim" item.

### Personal data

- In the anonymous public view, a payee typed `individual` or `unclassified` (which may be a sole proprietor) is shown in the graph as "Payee (name withheld in public view)".
- Payee profiles (`/api/entities/payee/*`) are never public.
- **Phone numbers are masked** as `[phone removed]`, and 12-digit Aadhaar-shaped numbers as `[id removed]` (Phase 13.y), in every output (`mask_personal`): both read models (description and search text), every API response that emits a description, the audit-sample list, the copilot context and the case export and audit trail. The rules are in `app/serving/redact.py`: Indian mobiles with any +91, (+91), 0091, 91 or 0 prefix, split 5-5, 3-3-4 or 4-3-3; 11-digit STD landlines, including a bracketed code, the +91 form and a following same-exchange number; mistyped 11-digit mobiles; glued pairs; and Aadhaar-shaped 12-digit numbers starting 2-9, plain or grouped 4-4-4. They deliberately leave amounts, IDs, pincodes, years, dates, school codes, letter numbers, decimals and long codes alone. Raw stored text, risk inputs and the stored case notes (with their hash chain) are never changed. Searching by a phone number finds nothing. `served_work.description_normalized` keeps the numbers as an internal matching key and is proven never to leave the system: no endpoint, schema, search index, export, log or Gemini payload carries it. The DB engine runs with `hide_parameters=True`, so bound values never appear in SQL logs or error messages.
- See the open item below on the other beneficiary details in work descriptions.

## Accounts

Accounts are created by an administrator on the server; there is no self-registration:

```
python scripts/create_user.py --username asha --role investigator
python scripts/create_user.py --username up-nodal --role state --state "Uttar Pradesh"
python scripts/create_user.py --username da-464 --role district --district-authority-id 464
python scripts/create_user.py --username x --disable
```

The password comes from `SENTINEL_NEW_PASSWORD` or an interactive prompt, never from the command line.

## What the frontend sees: anonymous read (owner decision needed)

The protected frontend is unchanged in Phase 13 (MUST NOT CHANGE). Its login page is a client-side demo that never calls the API, and no request it makes carries a token. So that the product keeps working, **`ANONYMOUS_READ=true` by default**.

**What an anonymous caller can do.** Anyone without a token acts as the built-in `public` role:
- Read the national analytics every page shows.
- Nothing else. It can't use case actions (investigate, recalculate), the audit trail, case export, audit samples or payee profiles. Individual payee names are withheld from it.

**What changes with `ANONYMOUS_READ=false`.** Every data endpoint requires a token.
- The unchanged frontend then shows its failed-to-load states.
- Making it log in for real needs a frontend change to call `/api/auth/login` and send the bearer header. That belongs to Phase 14 or later, with the owner's approval.

**Recalculate button.** The Record page's Recalculate button now needs an investigator token. From the unchanged frontend it shows its "request failed" notice, because a public user cannot write case events.

## Limitations (stated, not hidden)

- **Rate limits are per API process, in memory.** The compose deployment runs one API worker (`docker-compose.deploy.yml`), so its limits are exact. On Vercel each warm instance keeps its own counters: the effective limit is the configured one times the number of instances, and a cold start begins at zero. A hard global limit needs a shared store (for example, Redis) behind the same `RateLimiter` interface; none is used.
- **Client IP behind proxies (Phase 14).** Forwarded headers are honoured in two cases only. (1) The TCP peer is listed in `TRUSTED_PROXIES` (the cloudflared container): the limiter uses `CF-Connecting-IP`, else the right-most untrusted `X-Forwarded-For` hop. (2) The API runs behind a platform proxy with no fixed address: `VERCEL` is set, or `TRUST_PROXY_HEADERS=1` (pinned in `backend_v2/Dockerfile.vercel`). The limiter then uses the right-most `X-Forwarded-For` entry, the one the proxy appended, else `X-Real-IP`. Otherwise the TCP peer is the key, whatever headers arrive. Never set `TRUST_PROXY_HEADERS` on a server clients can reach directly, because they could then choose their own key. The old `TRUST_FORWARDED_FOR` flag trusted the client-written left-most hop from any caller; it has been removed. See `DEPLOY_NOTES.md`.
- **No refresh tokens and no password-reset endpoint.** Passwords are reset by an administrator with `create_user.py`.
- **Blinding in the audit sample covers auditor accounts only.** With `ANONYMOUS_READ=true`, anyone (an auditor included) can see tiers in the public UI without logging in. For an audit round, run with `ANONYMOUS_READ=false` or accept that limitation.
- **Beneficiary details in work descriptions.** Some portal work descriptions name beneficiaries and include disability details. For example: "beneficiary name …, disability is more then 40%, contact no …". Phone numbers and Aadhaar-shaped numbers are masked (1,992 phone numbers and 4 Aadhaar-shaped numbers in 1,900 descriptions). Owner decision (2026-10-02): anonymous API answers also mask the beneficiary's and contact person's names, a relative's name, a person's address and a person-linked disability percentage (`app/serving/public_mask.py`, applied when the API answers; nothing stored is changed). On the raw descriptions this changes about 2,093 of 122,965 (1.7%). A work that is only about disability is left as is. Logged-in accounts see the served text. Anonymous search never matches description words in the about 3.0% of works whose text carries a personal-data trigger word: those works are found by work key, MP, place, work type or authority. This masking is pattern-based and best effort, not a guarantee (`DEPLOY_NOTES.md`).
- **The run manifest has no git commit.** The repository was only initialised on 2026-09-28, and runs 1 and 44 were built before a commit existed or from an uncommitted tree, so §11's "git commit" provenance field is empty for them.

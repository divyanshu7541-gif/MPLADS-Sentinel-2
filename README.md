# MPLADS Sentinel — with Gemma 4 Investigation Copilot

An explainable, evidence-first investigation platform for **MPLADS** (Members of Parliament Local Area
Development Scheme). Sentinel ranks works for **human review**; it never declares that a work, MP,
authority or vendor is fraudulent. Scores come from a deterministic statistical engine. Generative AI
is used only to **explain stored evidence in plain language** and to answer help questions.

This repository is the SIH 2026 working copy extended during the hackathon with **Google Gemma 4 via
DigitalOcean Serverless Inference**.

---

## What is in this repository

| Area | Path | Responsibility |
| --- | --- | --- |
| Frontend | `frontend/` | React + Vite single-page app: dashboard, map, priority queue, analytics, MP/constituency browse, data health, methodology, record detail, copilot card and chat panel |
| API | `backend_v2/app/` | FastAPI, read-only serving of published analysis runs, auth/scope, rate limits, masking, AI endpoints |
| Risk engine | `backend_v2/app/analytics/`, `backend_v2/app/ingest/`, `backend_v2/scripts/` | Offline ingestion, signals, peer comparison, deterministic fusion, confidence, published runs |
| Database | PostgreSQL | Raw/core tables plus versioned results and serving tables (`served_work`, `risk_result`, `map_work`, …) |
| Deployment | `docker-compose*.yml`, `ops/deploy/`, `docs/deployment.md` | Local stack and Vercel/container deployment notes |
| Tests | `backend_v2/tests/`, `frontend/` | Backend regression/integration tests, frontend production build |

The API **never computes a score** at request time. A full analysis run publishes results into
PostgreSQL, and every endpoint reads those results. That separation is deliberate: it keeps serving
fast, reproducible and auditable.

---

## Architecture

```mermaid
flowchart LR
  A[Portal snapshots] --> B[Ingest and normalise]
  B --> C[(Core tables)]
  C --> D[Deterministic analytics<br/>peers • six signals • fusion • confidence]
  D --> E[(Published run<br/>risk_result • serving tables)]
  E --> F[FastAPI read API]
  C --> F
  F --> G[React SPA]
  G --> H[Investigation Copilot<br/>record detail]
  G --> I[Help chatbot]
  F -. minimal masked evidence .-> J[DigitalOcean<br/>Serverless Inference]
  J -. Google Gemma 4 .-> H
  J -. Google Gemma 4 .-> I
  H --> G
  I --> G
```

Read the diagram left to right:

1. **Ingestion and analytics are offline.** The worker builds a versioned run; it never serves a request.
2. **The API is read-only** for analytical data. It applies auth scope, masking and rate limits, then
   returns published rows.
3. **Gemma 4 is an explanation layer.** It receives a small, masked evidence pack from the API and
   returns text. It cannot write to the database, change a score, re-rank a record or alter a tier.
4. **Failures degrade safely.** Without a key, a timeout or an invalid model answer, the stored risk
   assessment and every page still work; the Copilot says it is unavailable and the chatbot uses its
   deterministic guide.

### Why Google Gemma 4 on DigitalOcean

- **Open-weight, instruction-tuned model** (`gemma-4-31B-it`) suitable for grounded summarisation at
  low temperature, without relying on vendor-specific structured-output behaviour.
- **OpenAI-compatible endpoint** (`https://inference.do-ai.run/v1/chat/completions`), so one small
  provider module can be reused and tested with ordinary HTTP mocks.
- **Serverless inference** keeps the demo deployment simple: no GPU host to operate, a bearer key held
  only by the backend, and a per-request timeout.
- **Graceful optionality.** The key is optional in every environment. The deterministic product is
  fully usable without any AI provider.

---

## The deterministic risk engine (unchanged)

The risk score is a weighted fusion of six analytical signals:

1. Cost anomaly
2. Description similarity (the only TF-IDF/ML component inside the score)
3. MP concentration
4. Constituency / authority pattern
5. Temporal anomaly
6. Lifecycle delay

Risk and confidence are separate. Confidence describes the reliability/coverage of the evidence, not
the probability of wrongdoing. Isolation Forest and Mahalanobis atypicality are evidence-only layers;
they do not enter the score. Tiers prioritise records for review.

**Hard invariants preserved by this integration**

- Gemma never generates, calculates, changes or overrides a risk score, tier or confidence value.
- Gemma never declares fraud, corruption or guilt, and never invents a fact, amount, person or date.
- The brief response contains **no new score field**; it explains the stored assessment.
- A malformed or unsafe model answer is rejected, not displayed.
- All deterministic MP, constituency, work-key and stored-risk lookups run **before** any model call in
  the chatbot.

---

## Hackathon changes

### Before the hackathon

- Deterministic ingestion, signal computation and published-run serving.
- Dashboard, investigation map, priority queue, analytics, MP/constituency browse and comparison,
  data health, methodology and record-detail pages.
- Risk/confidence evidence chain, scope-aware auth, rate limits, masking, case audit log.
- Rule-based help chatbot with deterministic MP/constituency/work lookups and optional Gemini
  free-form answers.

### Added during the hackathon

- **Gemma 4 Investigation Copilot** on every record-detail page, with a five-section brief:
  Executive Summary, Evidence Interpretation, Peer Comparison, Recommended Verification Checklist,
  Limitations and Unknowns.
- **Centralised DigitalOcean provider** (`backend_v2/app/core/ai_provider.py`): lazy client, timeout,
  typed failures, missing-key handling and secret-safe logging.
- **`POST /api/ai/investigation-brief/{record_id}`** and **`GET /api/ai/status`**, built on the existing
  database, auth/scope, masking and serving code.
- **Server-side brief validation**: exact headings and order, minimum body lengths, checklist parsing,
  accusation rejection and a response-size cap.
- **Chatbot upgrade**: Gemma 4 is now the primary free-form layer, then optional Gemini, then the
  local rule-based guide. Deterministic lookups are untouched.
- **Frontend states** for the Copilot: idle, loading, success, error, timeout, validation failure and
  unavailable, with attribution *Powered by Google Gemma 4 · DigitalOcean Inference*.
- **Tests and docs** for provider failure modes, output validation, masking, unchanged scores, env
  configuration and deployment.

---

## Investigation Copilot workflow

```text
Record page
  → GET /api/ai/status                 (is a key configured? never exposes it)
  → user clicks "Generate Investigation Brief"
  → POST /api/ai/investigation-brief/{record_id}
      → auth/scope + ai_brief rate limit
      → read the served record exactly like every other endpoint
      → reduce it to a minimal masked evidence pack
      → Gemma 4 (temperature 0.2, bounded output)
      → parse and validate five sections; reject accusations/unsafe output
      → return sections + attribution
  → page renders the brief beside the authoritative stored score
```

The evidence pack includes the record id, house, MP/constituency/state, masked description, amount and
stage, the stored risk assessment, peer context and signal summaries. It excludes credentials, raw
source rows, case notes, user identifiers, phone/Aadhaar-shaped numbers and (for anonymous callers)
beneficiary/contact names.

### Endpoint behaviour

| Condition | Response |
| --- | --- |
| Valid record, valid model output | `200` with five validated sections |
| Unknown/out-of-scope record id | `404` (scope is applied inside the serving query) |
| `DO_INFERENCE_API_KEY` not set | `503` |
| Provider timeout | `504` |
| Provider error or unusable output | `502` |
| Rate limit exceeded | `429` |

### Chatbot behaviour

```text
empty message                       → guide
work-key + risk/explain question    → stored result, or an explicit "no stored result"
MP/constituency/comparison question → deterministic lookup from served data
claim question (fraud, overrun, …)  → deterministic decline/explanation
otherwise                           → Gemma 4
                                      → optional Gemini fallback
                                      → local rule-based guide
```

The system prompt is generated from the live methodology configuration, so the assistant describes the
current weights rather than a stale copy.

---

## Setup

### Prerequisites

- Python 3.12+ (this working copy was tested with 3.13)
- Node.js 18+ and npm
- PostgreSQL with a published analysis run, or the provided Docker stack
- Optional: a DigitalOcean model access key for Gemma 4

### 1. Environment

```bash
cp .env.example .env
# edit .env — at minimum set DATABASE_URL for a non-Docker backend;
# optionally set DO_INFERENCE_API_KEY
```

`.env` is git-ignored. Never commit a real key.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DO_INFERENCE_API_KEY` | No | empty | DigitalOcean model access key; enables Gemma 4. Server-side only |
| `DO_GEMMA_MODEL` | No | `gemma-4-31B-it` | Model id |
| `DO_INFERENCE_BASE_URL` | No | `https://inference.do-ai.run/v1` | OpenAI-compatible base URL (include `/v1`) |
| `DO_INFERENCE_TIMEOUT` | No | `30` | Per-request timeout, clamped to 5–120 seconds |
| `GEMINI_API_KEY` | No | empty | Optional secondary chatbot fallback only |
| `DATABASE_URL` | Yes outside development | dev default | PostgreSQL connection |
| `JWT_SECRET` | Yes outside development | dev random | Token signing key, at least 32 characters |
| `CORS_ORIGINS` | Yes outside development | dev origins | Exact frontend origin list, never `*` |
| `ANONYMOUS_READ` | No | `true` | Read-only public role for the anonymous demo site |

The backend reads the process environment first, then the repo-root `.env`, then `backend_v2/.env`.
In Docker, pass the variables through the compose environment (already wired in
`docker-compose.yml` and `docker-compose.deploy.yml`).

### 2. Backend

```bash
cd backend_v2
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL="postgresql+psycopg://sentinel:sentinel@localhost:5433/sentinel"
uvicorn app.main:app --reload --port 8000
```

API docs: <http://localhost:8000/docs> · health: <http://localhost:8000/api/health>

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/api` to `http://localhost:8000` (see `frontend/vite.config.js`).

### 4. Docker stack

```bash
docker compose up --build
```

The API is then on <http://localhost:8000>. The compose file uses the existing local run-44 database
by default; see the comments at the top of `docker-compose.yml` if you need a fresh database or a
different host.

### 5. Deployed stack

See `docs/deployment.md` and `DEPLOY_NOTES.md`. Add `DO_INFERENCE_API_KEY` (and optional model/base
URL/timeout) to the backend service environment or `ops/deploy/deploy.env`; never to a `VITE_*`
variable.

---

## Tests

### Backend

```bash
cd backend_v2
source .venv/bin/activate
pytest -q
```

No test calls a live AI provider: the autouse fixture in `tests/conftest.py` blanks the DigitalOcean and
Gemini keys, and `tests/test_ai_copilot.py` mocks the provider for success, timeout, invalid output,
unsafe output, missing key and secret-safety cases. Database-backed integration tests skip when their
database is not reachable/seeded, and those serving-dependent tests are the only expected failures in a
database that has no published run.

Focused AI/chat run:

```bash
pytest tests/test_ai_copilot.py tests/test_phase11_chat.py -q
ruff check app tests
```

### Frontend

```bash
cd frontend
npm run build
```

`npm run build` is the regression gate used here (Vite production build). A large-chunk warning is
expected and does not fail the build.

---

## Privacy and limitations

- **Data minimisation.** The model receives only the fields needed to explain one case. Descriptions
  are masked; phone/Aadhaar-shaped numbers are masked for everyone; beneficiary/contact names are
  masked for anonymous callers through the same `public_view`/`mask_public` path as every public
  response.
- **No credentials or raw rows.** The key is never sent to the browser and is never written to logs in
  full. Raw source rows, case notes and user identifiers are not included in prompts.
- **Best-effort masking.** Masking is pattern-based, not a guarantee. Unusual spellings, non-Latin
  script, names without markers and addresses without keywords can pass through. Stored source text is
  never changed, so scoring and the published `risk_result` checksum are unaffected.
- **No score authority.** The Copilot explains; it does not score, re-rank, accuse or decide. The
  authoritative risk assessment remains the stored deterministic result on the page.
- **Prompt injection is treated as data.** The prompt tells the model to ignore instructions found
  inside the evidence pack; output validation rejects accusations and malformed briefs.
- **Model limits.** The brief can still omit nuance, over-summarise or use generic language. It must be
  read as an explanation of stored evidence, not as an independent investigation or legal conclusion.
- **Optional dependency.** A missing key, network failure or model outage never blocks the
  deterministic platform.
- **Coverage limits inherited from the data.** Current tenure only (18th Lok Sabha and sitting Rajya
  Sabha), one portal snapshot; no work-site coordinates; cost overruns are not detectable because
  actual never exceeds sanction in the data; ratings/images are not compliance signals.

---

## 60–90 second demo flow

1. **Open the dashboard (10 s).** Show the headline KPIs and risk distribution; state that scores are
   deterministic and prioritise work for human review.
2. **Open the Priority Queue (10 s).** Filter to high-priority works and open one record.
3. **Show the record evidence (15 s).** Point out the stored score, confidence, signal evidence and
   peer comparison. Emphasise that the score came from the published run and is unchanged.
4. **Generate the Investigation Brief (20 s).** Click **Generate Investigation Brief**; show the idle,
   loading and success states, then the five sections and the attribution line. Point out that the brief
   explains the evidence and ends with verification steps and limitations.
5. **Show graceful degradation (10 s).** Explain that without a key the card shows "unavailable" and
   the rest of the record is unaffected.
6. **Upgrade the chatbot (15 s).** Ask "how do I compare two MPs?" (deterministic lookup/guide), then a
   free-form methodology question answered by Gemma 4, and note the source label.
7. **Close (10 s).** The engine decides where to look; Gemma 4 helps a human understand why — without
   ever changing the score or making an accusation.

---

## Further reading

- `BLUEPRINT.md` — full architecture, data foundation, signals, gates and claims discipline
- `DEPLOY_NOTES.md` — deployment environment variables and verification checklist
- `docs/deployment.md` — deployment layout and AI provider configuration
- `docs/frontend_contract.md` — API/frontend response contract
- `docs/security.md` — auth, scopes and rate-limit limitations
- `backend_v2/app/core/ai_provider.py` — the single DigitalOcean provider module
- `backend_v2/app/api/ai.py` — Copilot endpoint, prompt, masking and output validation

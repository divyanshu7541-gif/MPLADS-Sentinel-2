# Phase 12: Cutover

**Status: built and verified; stopped for the owner's walkthrough before
Phase 13.**

## Outcome

- Every endpoint in the frontend contract is now real and reads the database
  for the published run.
- The frontend's dev and staging proxy now points at `backend_v2`.
- The old backend is archived, not deleted.
- `risk_result` for run 1 is unchanged. Its checksum is
  `97f08303369f9ed6c50e46d68a4609f5` before and after every Phase 12 build,
  and the serving build asserts it.

## 1. What was built

### Serving tables and service

**The read model: `served_work` (`app/models/serving.py`, migration `b400c17b8eb1`).**

- It holds one row per work in the run's snapshot: 122,965 works, of which
  97,506 are scored and 25,459 are recommended-only.
- It is built offline by `scripts/run_serving.py`, which is now in CI after
  `run_entities.py`. It is never a live join.
- It uses the same stale-data rule as the Phase 9 map: serve the published
  run's build if it exists, otherwise the latest complete build, labelled
  with `served.is_latest`.
- A CHECK constraint enforces that an unscored row has no tier, risk or
  confidence.

**Audit log: `case_event`.**

- This is the append-only log of investigation decisions and "recalculate"
  requests.
- It replaces the old `audit_trail.json` file.
- The actor recorded here is never taken from the request body.

**Service and handlers: `app/serving/service.py` and `app/serving/labels.py`.**

- These hold the queries behind endpoints #1–#8, #13–#19 and #25–#32.
- They also hold the mapping from v2 values onto the fixed vocabulary the
  frontend translates.

### Handlers

- `app/api/{dashboard,queue,reference,risk,performance}.py` replace the
  Phase 0 stubs.
- Each is a sync handler that takes a database session.
- A database failure returns a 503 on that endpoint only. Bad input returns
  422.
- An unknown record, MP or constituency returns 404, as the old backend
  did.

### Startup cache warm-up

- An optional warm-up precomputes the summary, analytics and data-health
  payloads when the API starts. It is off by default and turned on with
  `WARM_CACHE=true`, which compose sets.
- Cold, those payloads take 2–5 s; warm, under 70 ms.
- The cache key includes the build timestamp, so a rebuild invalidates it.

### Deployment configuration (`frontend/` source untouched)

**`docker-compose.yml`:**

- The `api` service moves from host port `8001` to `8000`.
- `frontend/vite.config.js` hard-codes its `/api` proxy to `localhost:8000`,
  so the dev server and `vite preview` now reach backend_v2 without any
  frontend change.

**`vercel.json`:**

- The `backend` service root moves from `backend/` to `backend_v2/`.
- **Phase 14 needs:** a reachable Postgres, `DATABASE_URL` set in Vercel,
  and the pipeline run against it. Until then every data endpoint there
  returns 503.

### Archive and removals

**Archive (`archive/backend_v1/` with a README):**

- `backend/` and the root `scripts/` (23 old-backend tools, most of which
  hard-code `PROJECT_ROOT/"backend"`) moved there unchanged.
- The Phase 5 fusion test now reads the old engine source from that path.

**`POST /api/load` is removed.** It had zero frontend callers, and a test
asserts it stays absent.

### MP performance scope

`/api/mp-performance` is current tenure only. It reads the served snapshot
(Snapshot A: the 18th Lok Sabha and sitting Rajya Sabha members). Works in
`prior_cycle_work` are never merged in, and a test asserts this.

## 2. Contract decisions (additive, in `docs/frontend_contract.md`)

**Population.**

- Risk figures cover scored works only.
- Recommended-only works show `NOT_EVALUATED` with `null` risk, tier and
  confidence, never 0.
- The Queue's "Recommended" stage filter is therefore always empty. This is
  deliberate and honest.
- MP and constituency profiles use all works for stage and amount figures,
  and scored works for risk figures. `risk_rate` now uses scored works as
  its denominator; the old engine used all works.

**Field sources.**

| Field | Source |
|---|---|
| Display `state` | Phase 9's corrected location state |
| `constituency` | Lok Sabha only; Rajya Sabha has none |
| `amount` | Sanction amount for scored works; recommended amount otherwise |
| `peer_*` context | `work_context` (leave-one-out) |

**Peer percentile.**

- The percentile is computed against the work's assigned level's true peer
  group, rebuilt across the whole Phase 3 frame. It does not use
  `work_context.group_key`, which holds each work's own level's key; pooling
  on it would drop peers that landed on a different level. This is the same
  trap Phase 4 fixed in `signals.py`.
- The build asserts that the number of others equals `n_usable_excl_self`
  for every work.

**Queue parameters.** The Queue page sends `mp` and `risk_level`. The old
router read only `mp_name` and `priority`, so both filters were silently
ignored. Both spellings now work.

**Investigate.**

- The body's `reviewer` is ignored.
- The actor is `unauthenticated-placeholder (Phase 13: actor from auth
  token)`, stored with `actor_is_placeholder = true`.
- **Phase 13 follow-up:** take the actor from the auth token.
- *Superseded in Phase 13:* the actor now comes from the login token and
  `actor_is_placeholder` is false (`docs/frontend_contract.md`, "Phase 13
  security"). This section is kept as the Phase 12 record.

**Recalculate** means "flag for next run". It returns the stored result with
`status: flagged_for_next_run` and never writes `risk_result`.

## 3. Judgment calls (flagged for review)

1. **Old engine bands, carried over for contract meaning, not re-tuned.**
   - Confidence: HIGH ≥ 0.7, MEDIUM ≥ 0.5. v2 data splits
     94,546 / 2,926 / 34.
   - Signal strength: HIGH > 0.7, MEDIUM > 0.3.
   - This follows the same posture as Phase 9's 0.7 high-signal cut. Phase 9
     had left constituency-intelligence's `confidence_distribution` as `{}`;
     it now uses the same bands, so pages agree.
2. **Signal names mapped onto the old vocabulary.**
   - `district_authority_pattern` → `CONSTITUENCY_PATTERN`.
   - `lifecycle_delay` → `STAGE_CONSISTENCY`.
   - Both names describe the v2 signal only loosely, so each response also
     carries the v2 definition in `signal_description`.
3. **Peer-level names.** L1 and L2 reuse the frontend's own names ("State +
   Category + Year", "State + Category") so they translate in Hindi mode.
   L3 is named "National Category + Year".
4. **"Multiple independent indicators…" recommendation.**
   - This is the old corroboration sentence. It is added when a work has 3
     or more active signals, the same count CRITICAL requires.
   - The old trigger was its cross-signal pattern, which v4 does not have.
5. **Map stages are now upper case.** Phase 9 served
   `Completed`/`Sanctioned`, so stage badges on the map missed their CSS
   class and translation. The map was rebuilt, and the checksum is unchanged.
6. **Dashboard `state_distribution`** is the top 20 states by works. The old
   engine value-counted the first 20 rows, which was a bug.
7. **Explanation sentences** are built from each signal's stored evidence.
   They do not match the old sentence regexes, so Hindi mode shows them in
   English.

## 4. Tests: one suite

- **Contract suite (`tests/test_contract.py`), against real data.**
  - All 32 endpoints use real IDs, MPs and constituencies taken from
    `served_work`.
  - It now also checks the item-level shapes the contract documents (queue
    records, map rows, audit entries and so on) and that every list is
    non-empty.
  - It removes the audit rows it writes.
- **Known-defect suite (`tests/test_phase12_cutover.py`), through the HTTP
  handlers.** Each test names the old defect it guards:

| Defect | Guarded by |
|---|---|
| Self in own peer group | Context size equals the leave-one-out count; percentile spans exactly 0–100 |
| Positional misalignment | 40 sampled works: every API figure equals that work's own stored rows, joined by key |
| Stage-multiplied rows | One row per work; summary, queue and stage totals equal the `risk_result` count |
| Queue pagination | Pages partition the result with no overlap |
| Special-character search | 15 hostile inputs return 200 on queue, map-data and map-works; `%` matches only a literal `%` |
| Stacked or fake map coordinates | All markers fall inside India's bounding box, with more than 100 distinct points |
| Huge map payload | map-works is capped at 3,000 rows and under 3 MB; map-data is under 2 MB |
| One failed endpoint | A dead database gives a well-formed 503 per endpoint; health stays 200 |
| Missing APIs and deleted chatbot | Every one of the 32 `fetch()` calls parsed from `api.js` has a route; chat routes work |
| Broken route order | `/api/risk/top` and `/api/risk/summary` are no longer shadowed |
| `/api/load` | Stays removed |
| Broken graph | Every edge endpoint is a node |
| Impossible risk tiers | Every tier matches its score and corroboration; unscored works have no tier; values are in range |
| House filter | LS + RS equals ALL for totals and each tier; queue rows match their House; a work's figures are identical with or without the filter |
| Queue aliases | `mp` and `risk_level` are honoured |
| Investigate actor | The actor never comes from the body |
| Recalculate | Never changes the checksum |
| MP performance | Current tenure only |
| Serving build | Did not change `risk_result` |

- The Phase 1–11 suites run unchanged in the same suite. Two assertions were
  updated because Phase 12 changed what they observe:
  - The Phase 9 map stage value is now `COMPLETED`.
  - Phase 9's "other endpoint unaffected" check now uses `/api/health`,
    since `/api/summary` is DB-backed.
- **CI e2e job.** It runs backend_v2 without a database, so every data
  endpoint returns 503 there. Its readiness check now uses `/api/health`
  through the proxy. The House-toggle assertions only check which calls the
  pages make.

## 5. Manual smoke test (automated in a real browser)

**Setup:**

- Browser: Playwright/Chromium.
- Frontend: a production `vite build` served with `vite preview`. It was
  built in an isolated container that copied the frontend without its
  `node_modules`, so the host's `node_modules` was untouched.
- Backend: backend_v2 on `localhost:8000`, reading the real run-1 database.

**Coverage:** 18 page visits in each of three House states (none, LS, RS),
54 visits in all:

- Landing, Login, Dashboard, Map.
- Queue: unfiltered, filtered with `mp` and `risk_level`, and searched with
  `(`.
- Record detail: a Lok Sabha work, a Rajya Sabha work, an unscored work and
  a missing work.
- Analytics, Data Health.
- MP Performance: the browse page, a Lok Sabha MP profile, a Rajya Sabha MP
  profile, and a constituency profile.
- Methodology.

**Results:**

- Every page rendered real content.
- Zero console errors, zero page errors and zero failed API calls, except
  the deliberate missing-record visit. That page shows "Record not found"
  after a 404, which is the old backend's behaviour.
- No `NaN`, `undefined` or `[object Object]` text appeared in any rendered
  page.
- Clicking the real House toggle: ALL 97,506 = LS 78,232 + RS 19,274.

## 6. Partial-failure test

One endpoint at a time was forced to return 500 at the browser.

| Forced failure | Page | Result |
|---|---|---|
| `/api/analytics` | Dashboard | Unaffected. The Dashboard never calls analytics. |
| `/api/analytics` | Analytics | Shell intact; shows "No analytics data available". |
| `/api/summary` | Dashboard | Shell, sidebar and toggle intact; shows "No dashboard data available". |
| `/api/summary` | Landing | Keeps its fallback figures. |
| `/api/queue` | Queue | Filters intact; shows 0 works. |
| `/api/record/*` | Record | Shows "Record not found". |
| `/api/data-health` | Data Health | Shows "No data health available". |

**Verdict: no page goes blank; the current frontend already guards against
this, so no frontend change was needed.**

### Frontend fixes (owner-authorised, after the first Phase 12 report)

The walkthrough found three frontend defects. The owner authorised fixing
all three in one pass. The changes are frontend only, plus one test-timing
fix; no backend or scoring code changed.

1. **Failed loads are now distinct from "no data".**
   - Every failed fetch now shows a failed-to-load state instead of the
     empty-data state. It says "Failed to load <thing>", shows the HTTP
     status, and offers a Retry button where retrying makes sense
     (`components/ErrorState.jsx`).
   - Errors are caught; there are no more uncaught promise rejections.
   - `services/api.js` errors now carry the HTTP `status`, so a real 404
     still reads "Record not found" or "No records found for this name",
     not a failure.
   - Every fetch site was audited. Where each failure now appears:
     - **Full-page error:** Dashboard, Analytics, Data Health, Queue,
       Record Detail, MP/constituency profile, comparison.
     - **Inline notice:** Queue state list; Map data, boundaries, coverage,
       filters, search lists, work markers and constituency intelligence;
       MP/constituency lists; the recalculate action.
     - **Landing:** its figures show "—" with "Live figures failed to
       load". The stale hard-coded fallback figures (64,058 works, from the
       old dataset) are removed.
     - **Chatbot:** "Status unavailable" instead of claiming guide mode.
     - **Map:** the national summary is hidden rather than showing zeros.
2. **No fabricated scores for unscored works.** On an unscored record:
   - No number, ring or "0/100" appears.
   - A "Not evaluated — this work has no risk score" card shows the API's
     own `not_scored_reason`.
   - Confidence and active signals show "—".
   - There is no radar chart, no tier sentence and no "No anomalous
     patterns detected" claim.
   - Tier sentences now appear only for the tiers they describe.
   - A missing score or level anywhere, including queue rows, map markers,
     the evidence chain and risk history, renders as "—" or "Not
     evaluated", never 0 or LOW.
   - On scored works, a signal the API marks not-evaluated is no longer
     plotted at 0 on the radar; it is listed as "Not evaluated for this
     work".
   - The Map's constituency panel and popups show "too few works" instead
     of "null%" when a rate is withheld. They no longer pick the "within
     normal range" sentence for that case.
3. **The MP profile percentage uses scored works as its denominator.**
   - The card now uses `scored_works`.
   - For example, RAJEEV RAI shows "1.0% of 209 scored works" (2 / 209).
     Before, it showed "0.9% of works" (2 / 229, including 20
     recommended-only works).

**Verified with 67 targeted browser checks, all passing:**
- Each forced failure shows the failed-to-load state, never the empty-data
  text, with no uncaught rejection.
- Retry recovers.
- A 404 is still "not found".
- The unscored record has none of the fabricated elements.
- Scored records are unchanged.
- The MP card shows the scored denominator.

**Test-timing fix.** The House-toggle e2e test counted the
Rajya-Sabha-not-applicable notice immediately after `networkidle`. The page
remounts and reloads its lists on a House switch, which outlasts that check
on real data; the stub lists were instant. The test now waits for the notice
itself. All 4 e2e tests pass against the real stack.

### Frontend fixes, second pass (owner-authorised)

These changes are frontend only.

- **Landing constituency count.**
  - The hard-coded "509" is replaced by the live count, 538. It is derived
    from `/api/constituencies`, the list the MP and Map searches already
    use, and it matches Data Health's `constituency_count`.
  - If that call fails, the figure shows "—" with the "Live figures failed
    to load" notice.
- **Only API explanation text on scored works.**
  - The generic tier sentences are removed from the Record Detail banner
    ("significantly / notably / somewhat more anomalous than comparable
    projects…").
  - The banner now lists each contributing signal with the API's own
    `explanation` for it. A signal without explanation text shows only its
    name.
  - A scored work with no active signals no longer shows "No anomalous
    patterns detected for this project". The heading's own count
    ("0 signals") is all that appears.
  - The evidence chain's prose steps (normalised data, context engine,
    signal fusion) now show the API's `description`. The frontend text
    wrongly claimed "seven independent signals"; v4 fuses six.
  - Steps that only format API values (record id, signal count, risk level)
    stay translated.
  - The locale keys `rec.sum.*`, `rec.noAnomalies` and
    `rec.stepDesc.{normalized,context,fusion}` are now unused.
- **Verified:** 83 targeted browser checks passed, including 16 new ones.

### Frontend fix, third pass (owner-authorised)

This change is frontend only.

- **Landing "Currently flagged for review".** The figure now counts
  CRITICAL + HIGH (`critical_count + high_count`, 31,683 on run 1; 31,814 on
  run 44 after the Phase 13.y authority-state fix -- the page reads it live). That is
  the definition of "flagged" everywhere else in the app: Dashboard, Map,
  Analytics flag rates and MP profiles.
- It previously added MODERATE ("review recommended"), giving 78,427.
- The figure follows the House toggle like the rest of Landing's figures.

## 7. Still open (owner's call; not changed here)

- **Authority-state bug (Phase 9).** *Fixed in Phase 13.y (2026-09-28): run 44,
  published; see `docs/validation_report_v1.md` §10.*
  - 52 district authorities, carrying 11,783 scored works, have the wrong
    state. Their peer groups and the risk scores built on them used it.
  - Display and the map use the corrected location state; the scores do
    not.
  - The upstream fix plus a Phase 3–5 re-run changes the checksum, so it
    needs the owner's decision.
- **`work.tenure_id` is 0% populated.** Phase 10 synthesized an MP-tenure
  key instead.
- **CI has never run live.** There is no git remote yet. The first push gets
  the first real Actions run, including the new `run_serving.py` step.

## 8. Bringing up the stack

A fresh `postgres` volume is empty.

1. Run the pipeline once inside the `api` image, with `data/` mounted and
   `DATA_DIR` pointing at it. Run each command in turn, stopping if one
   fails:

   ```
   bash scripts/fetch_geo_data.sh
   alembic upgrade head
   python scripts/run_ingest.py
   python scripts/run_normalize.py
   python scripts/run_identity_split.py
   python scripts/run_context.py
   python scripts/run_signals.py
   python scripts/run_risk.py
   python scripts/publish_run.py
   python scripts/run_atypicality.py
   python scripts/run_geo.py
   python scripts/run_entities.py
   python scripts/run_serving.py
   ```

2. Run `docker compose up`.
3. Open the app at http://localhost:3000.

Do not start the archived backend on port 8000.

# Sentinel MPLADS — Rebuild Plan v2 (revised)

**This document supersedes `SENTINEL_REBUILD_PLAN.md` (v1).** It applies your corrections: frontend
frozen-by-default, MP Report removed as a dedicated historical feature, explicit House (Lok Sabha /
Rajya Sabha) scope, a revised map design that does not jitter fake coordinates, ML kept mandatory but
scope-capped to the two required systems, a stricter risk-engine reachability gate, and a new Vercel
frontend-deployment phase. Nothing has been implemented — this is still planning material only.

Two things are flagged rather than silently decided, per your own "STOP and report the conflict" rule.
They're called out inline at §1.8 and §8.3 — please confirm both before Phase 0/Phase 9 execute.

---

## 1. CORRECTED EXECUTIVE AUDIT

Sections 1.1–1.7 are carried over from v1 unchanged — they are verified findings from reading the actual
code and data, and your corrections don't change any of them. Reproduced in compressed form, then two new
items (1.8, 1.9) added for this revision.

### 1.1 Risk engine — CRITICAL is structurally almost unreachable (confirmed in code)
`signal_fusion.py`'s `FIXED_TOTAL_WEIGHT = 1.0` denominator means a single saturated signal contributes at
most its own weight (max 0.25 for Cost Anomaly), not 1.0. `corroboration.py` caps the multiplier at 1.15×.
`risk_engine.py` demotes CRITICAL→HIGH unless `base_signal_count >= 3`. These three compound to reproduce
exactly the reported "0 CRITICAL / ~10 HIGH / everything LOW" outcome. **Per your correction #9, this plan
does not freeze any production configuration until a documented reachability/sensitivity/ablation gate is
passed — see §7.**

### 1.2 Peer baselines include the work itself (confirmed in code)
`context_service.py` computes `groupby(peer_group_key)['amount_numeric'].median()` over the whole group,
self included. `peer_builder.py`'s Level-1 key is `(State, Constituency, category, year)` — constituency
groups are ≈one MP's own portfolio. Both are fixed in Phase 3 exactly as in v1 (work-type × state × FY,
leave-one-out, cross-MP requirement).

### 1.3 Map: regex-crash search + fake stacked coordinates + full-table-scan-per-request (confirmed in code)
`engine.py`'s `.str.contains(s, na=False)` defaults to `regex=True`, so `(`, `[`, `*`, `?` in search text
raises `re.error`, unhandled → 500. Every work in a constituency is placed at one identical geocoded
centroid. Both endpoints copy the full in-memory dataframe per request. **§8 below revises the fix — your
correction explicitly rules out the jitter approach v1 proposed.**

### 1.4 Architecture is a single in-memory monolith (confirmed in code)
One global `SentinelEngine.self.df`, recomputed on `/api/load`, no DB, no versioned runs, no rollback.
Unchanged finding; unchanged fix (DB-backed, run-versioned pipeline, §3–4).

### 1.5 Chatbot is close to target already (confirmed in code)
Two-layer (local KB + optional Gemini), already avoids fraud language, already never scores. Gap: method
text is hand-authored, not generated from live config. Unchanged finding/fix (§9).

### 1.6 Graph endpoint exists, minimal, bounded (confirmed in code)
`GET /api/graph-data` exists and `services/api.js` has a caller, but no page uses it (no Graph page was
built, owner decision, Appendix); keep contract, keep it bounded/offline/non-scoring per correction #13.

### 1.7 Data reality matches BLUEPRINT.md's claims (spot-verified)
RS recommended file genuinely absent from Snapshot A; old backend only ever ran on the 64k-row legacy CSV,
never on Snapshot A/B/prior-cycle/macro files. Unchanged.

### 1.8 NEW — flagged distinction: "MP Report" (removed) vs. the existing `/mp-performance` page (already live, frozen)

Reading the actual frontend turned up something your correction #2 needs to account for: the frontend
**already has a live route and nav entry**, `frontend/src/pages/MPPerformance.jsx` (580 lines, route
`/mp-performance` in `App.jsx`), calling `getMpPerformance`, `getConstituencyPerformance`,
`getMpComparison`, `getConstituencyComparison` from `services/api.js`. This is a **current-tenure-only**
profile-and-comparison view — it shows one MP's or constituency's portfolio (or up to a few, side by side)
against Snapshot A/B data you actually have. It contains **no** reference to the 16th/17th Lok Sabha, no
historical-session selector, and no longitudinal person-vs-place comparison anywhere in its code (verified
by grep — zero matches for "16th", "17th", or any historical-session string in the whole frontend).

Your correction #2's removal list is specifically about the **longitudinal, multi-Lok-Sabha, historical**
feature described in BLUEPRINT.md §9's "later extension" (16th/17th LS work-level history, person-vs-place
analysis) — data you don't have and have decided not to pursue. It is not, on its face, about deleting an
existing, currently-working, current-tenure-only frontend page, which your own frontend-protection rule
(#1) explicitly forbids removing ("remove existing functionality") absent an explicit request.

**Resolution adopted in this plan (flagged, not silently assumed):** keep `/mp-performance` and its four
API endpoints exactly as they are — a current-tenure MP/constituency profile and comparison view, scoped
to 18th Lok Sabha + sitting Rajya Sabha only, backed by real Snapshot A/B data. Build nothing historical,
nothing multi-session, nothing longitudinal. If this reading is wrong and you intended the existing page
itself to be removed too, say so explicitly before Phase 0 — that would be case (A) in your rule 1
("I explicitly requested a new frontend feature" — or in this case, its removal), and the roadmap/Phase 9
prompt would need a one-line change to reflect it.

### 1.9 NEW — an unused dependency already resolves the map's marker-vs-honesty tension

`frontend/package.json` lists `leaflet.markercluster` as an installed dependency, but `Map.jsx` never
imports or uses it — markers are placed individually via raw `L.marker`, which is *why* many identical-
location works currently render as visually stacked, indistinguishable dots. This matters directly for
§8's revised map design: a marker-cluster layer is the standard, honest cartographic way to show "many
things share this approximate area" (a numbered cluster bubble, not N overlapping precise-looking pins) —
and the library is already a project dependency, just unwired. This is the second flagged item — see §8.3.

---

## 2. CORRECTED BLUEPRINT RECONCILIATION

Everything in v1's §2.1 (what to keep from BLUEPRINT.md) still holds: DB-backed run-versioned pipeline,
corrected peer hierarchy, two-track risk fusion, A1/A2 ML, compliance-as-separate-panel, validation
framework, security posture. Restating only what changes under your corrections:

### 2.1 Removed entirely (product scope decision, not deferred)
- BLUEPRINT.md §9 "MP report" **historical/longitudinal extension**: multi-Lok-Sabha MP performance,
  16th/17th LS work-level analytics, person-vs-place comparison, any tenure table designed to hold more
  than the current two (18th LS, sitting RS). Do not build a placeholder, a disabled button, or "coming
  soon" UI for any of this — per your instruction, a firm scope decision, not tech debt.
- Any generic/historical session selector. Only a **binary** House selector (Lok Sabha / Rajya Sabha) is
  in scope, per §5.

### 2.2 Kept, re-scoped
- The `tenure` table (BLUEPRINT.md §4) is kept but constrained to exactly two live tenure rows in this
  scope: 18th Lok Sabha (per-MP), and the current/sitting Rajya Sabha (per-member). No schema support for
  a third tenure needs to be *removed* later (a `tenure` table is inherently able to hold more rows) — but
  no ingestion, UI, or API path may create, expose, or imply one beyond these two.
- The existing `/mp-performance` page and its four endpoints — see §1.8's flagged resolution — kept as a
  current-tenure-only profile/comparison feature, unrelated to the removed historical scope.

### 2.3 Map (major revision — see §8 in full)
BLUEPRINT.md §15's aggregated-area design is adopted as the **primary** map model, per your correction:
national → state → district → constituency (Lok Sabha only) → paginated work list → work dossier. Deep
individual-work pins are **not** invented to preserve visual parity with the old frontend; your instruction
is explicit that geographic honesty outranks visual compatibility here. v1's jitter proposal is withdrawn.

### 2.4 ML (unchanged in substance, scope discipline tightened)
Required: Isolation Forest + Mahalanobis (B4), built, evidence only, not yet shown in the app. A2 (365-day
delay prediction) was built and closed; A1 (survival, its support model) is inactive; see §13.z.
Optional/deferred, built only if a specific dependency requires it: fund-flow forecasting (A3), expected-
cost model (B3), embeddings, payee resolution beyond Phase 2's rule-based typing. None of these optional
items may delay or dilute Phase 6/7's scope. No ML enters risk_score beyond TF-IDF similarity (§7.2).

### 2.5 Risk engine gate (tightened per your correction #9)
v1 already proposed a reachability/sensitivity/ablation gate before freezing a production config; this
revision makes it a **named, mandatory exit gate for Phase 5** with an explicit list of required analyses
(§7) and an explicit prohibition on both "lower the threshold until CRITICAL looks non-empty" and
"hard-code a target tier distribution" — both now written as failing conditions for the phase, not just
discouraged practice.

---

## 3. FINAL TARGET ARCHITECTURE

```
                    PORTAL EXPORTS (Snapshot A, Snapshot B, prior-cycle, macro)
                                       │
                    P0 Register → P1 Parse/contract → P2 Reconcile   (fails closed)
                                       │
                          RAW STORE (immutable, insert-only)
                                       │
              P3 Normalise → P4 Link+lifecycle (House-tagged) → P5 Entity resolution
                                       │
                    CORE TABLES (work, keyed by work_key + house + tenure)
                                       │
        ┌──────────────────────────────────────────────────────────────────┐
        │  P6 ANALYTICS WORKER — one run = one immutable, House-aware result│
        │  ┌────────────────────────┐      ┌───────────────────────────┐  │
        │  │ EXPLAINABLE ENGINE      │      │ ML EVIDENCE (offline)      │  │
        │  │ Cost · Near-Dup(TF-IDF)·│      │ Isolation Forest +         │  │
        │  │ Portfolio · District ·  │      │ Mahalanobis (B4): evidence │  │
        │  │ Temporal · Lifecycle    │      │ only, not shown in the app │  │
        │  │ = 6 BASE signals        │      │ A1 inactive · A2 closed    │  │
        │  └───────────┬─────────────┘      └────────────┬──────────────┘  │
        │              └─────────────┬─────────────────────┘                │
        │                            ▼                                     │
        │              EVIDENCE (versioned, House-tagged; no forecasts)     │
        │                            ▼                                     │
        │     RISK (v3-compat & v4-candidate, gated per §7) + CONFIDENCE    │
        │                            ▼                                     │
        │              COMPLIANCE PANEL (deterministic, parallel)          │
        └───────────────────┬────────────────────────────────────────-─────┘
                             ▼
                P8 Quality gate → P9 Atomic publish (published_run)
                             ▼
        STATELESS READ API (FastAPI, Pydantic v2, role-scoped, House-filterable)
                             ▼
   ┌───────────┬───────────┬────────────┬──────────────┬───────────┬────────────┐
   ▼           ▼           ▼            ▼              ▼           ▼            ▼
DASHBOARD   MAP (area-  QUEUE /      MP/CONSTIT.   WORK DOSSIER  ANALYTICS   HOUSE
(House      first,      INVESTIGA-  PERFORMANCE    (peer dist.,              SELECTOR
 filter)    House-      TIONS       (current-      signals,                  (new, minimal,
            aware,      (House      tenure only,   lifecycle,                explicit)
            no fake     filter)     kept per §1.8) payment pace)
            pins)
                             ▲
                             │
                     CASES / AUDIT (append-only)
                             ▲
                             │
                     COPILOT (Gemini, grounded on published_run + live
                     config, House-context-aware — never scores)

  ── separately ──
  DEPLOYMENT (§12): one Vercel project, Services preset — Vite frontend service (SPA fallback inside it)
  + stateless container backend (backend_v2/Dockerfile.vercel) on /api/*; DB = slim copy of run 44
```

House is threaded through as a filter dimension, not a separate vertical stack — per your instruction not
to duplicate features per House where a shared filter suffices.

---

## 4. FINAL DATABASE PLAN

Same six schema groups as BLUEPRINT.md §4 / v1 §4, with these amendments:

- **`tenure`** rows restricted to exactly: `(house='LS', label='18th Lok Sabha')` per MP, and
  `(house='RS', label='sitting')` per current Rajya Sabha member. No ingestion path, migration, or seed
  data creates a 16th/17th LS tenure row. `work.house` is a required, non-null column (`'LS'` or `'RS'`),
  derived directly from source filename at ingest (`*_LokSabha_*` / `*_RajyaSabha_*` — no inference needed,
  the data already disambiguates this cleanly).
- **No MP-report-specific tables** beyond what `/mp-performance`'s existing (current-tenure) contract
  needs — i.e., no `tenure_profile` historical time series beyond the current tenure's own within-tenure
  FY trend (which the existing page's "Trend" section already expects — this is current-tenure trend, not
  cross-tenure history, and was already correctly scoped this way in BLUEPRINT.md §9's "Report sections").
- **`geo_area` / `geo_name_crosswalk`** stay in Phase 1 (moved up, per v1's §2.2.F reasoning, unaffected by
  this revision) — static reference data, not per-run analytics. Both are keyed to include `house`
  applicability (Rajya Sabha works have no constituency; district/state still apply).
- **`geo_metric`** (per-run aggregated area metrics) gains a `house` dimension: metrics can be requested
  filtered to LS-only, RS-only, or both — needed because RS works have no constituency-level geometry.
- **`analysis_run`** stores both `risk_config='v3-compatible'` and `'v4-candidate'` per run, unchanged from
  v1, plus the Phase 5 reachability-analysis report artifact reference (§7) so the gate's evidence is
  itself versioned alongside the run it justified.
- **No new table for Vercel/deployment** — deployment config lives in the frontend repo (`vercel.json`,
  `.env.example`) and in ops documentation, not the application database.

---

## 5. FINAL FRONTEND API CONTRACT

Unchanged from v1's §5 table for every endpoint **except** the two corrections below. Full table reproduced
with House and marker-related annotations added; treat this as the authoritative, re-testable contract.

| Frontend page(s) | Function | Endpoint | Status this revision |
|---|---|---|---|
| Dashboard | `getSummary` | `GET /api/summary` | Preserve; add optional `house` query param (additive, defaults to "both" if omitted — zero frontend change required unless/until the House selector is wired, §6) |
| Investigation Queue | `getQueue` | `GET /api/queue` | Preserve; add optional `house` filter param, additive |
| Record Detail | `getRecordDetail` | `GET /api/record/{id}` | Preserve; dossier gains `house` field (additive) |
| Analytics | `getAnalytics` | `GET /api/analytics` | Preserve; add optional `house` filter param, additive |
| Data Health | `getDataHealth` | `GET /api/data-health` | Preserve |
| Filters | `getStages`, `getConstituencies`, `getStates`, `getMps` | as in v1 | Preserve; `getConstituencies` naturally returns LS-only names (RS has none) — document this as expected behavior, not a bug |
| Map | `getMapData` | `GET /api/map-data` | Preserve **params**; response becomes pre-aggregated per §8, add optional `house` param |
| Map | `getMapWorks` | `GET /api/map-works` | Preserve **request** shape; **response no longer implies work-site precision** — see §8 for exact revised shape (additive fields, existing fields kept, но see §8.2 for the one behavior change: same-constituency works share one point, unjittered) |
| Map | `getMapFilters` | `GET /api/map-filters` | Preserve; add `house` to filter options list (additive) |
| Map | `getGeoJSON` | `GET /api/geojson` | Preserve; boundaries can be requested per House scope |
| Map | `getGeographicCoverage` | `GET /api/geographic-coverage` | Preserve |
| Map | `getConstituencyIntelligence` | `GET /api/constituency-intelligence` | Preserve (LS-only by nature — RS has no constituency) |
| Graph | `getGraphData` | `GET /api/graph-data` | Preserve |
| Investigation actions | `updateInvestigation` | `POST /api/investigate/{id}` | Preserve; actor from auth token (§13) |
| Audit | `getAuditTrail` | `GET /api/audit-trail` | Preserve |
| MP Performance (current-tenure only, kept per §1.8) | `getMpPerformance`, `getMpComparison` | `GET /api/mp-performance/{name}`, `/api/mp-comparison` | Preserve exactly; scope is already current-tenure in the existing implementation |
| Constituency views | `getConstituencyPerformance`, `getConstituencyComparison` | as in v1 | Preserve (LS-only by nature) |
| Copilot | `getChatStatus`, `postChat` | `GET /api/chat/status`, `POST /api/chat` | Preserve; grounded answers become House-context-aware where relevant |
| Risk/Signals/Evidence/Context | as in v1 | as in v1 | Preserve |
| Investigations list | `getInvestigations` | `GET /api/investigations` | Preserve |
| Recalculate | `recalculateRisk` | `POST /api/risk/recalculate/{id}` | Preserve (reinterpreted as "flag for next run," unchanged from v1) |

**Removed:** `POST /api/load?filepath=` — unchanged from v1, no frontend caller, zero impact.

**One documented, additive, tested frontend source change proposed for deployment (§14), not for any
backend phase:** `services/api.js`'s `const API_BASE = '/api';` becomes
`const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';` — a single line, backward-compatible
(falls back to the current relative path when the env var is unset, so local dev is untouched), required
because a Vercel-hosted static frontend has no dev-server proxy to reach a separately-hosted backend. This
is the only backend-driven frontend source change anywhere in this plan, and it is called out explicitly
per your rule requiring documentation and justification for any such change.

**One flagged, NOT-yet-approved, potential frontend change (Map.jsx, marker clustering) — see §8.3.**

---

## 6. FINAL HOUSE/SCOPE ARCHITECTURE

### 6.1 Supported scope (explicit, documented, no historical implication)
- **Lok Sabha:** 18th Lok Sabha only, work-level.
- **Rajya Sabha:** current/sitting members only, work-level.
- Both derived directly from source filenames at ingest (`*_LokSabha_alltenures.csv` /
  `*_RajyaSabha_alltenures.csv` — despite the "alltenures" filename token, BLUEPRINT.md §2 confirms, and
  this plan repeats as a hard limit, that only the 18th LS / sitting RS data actually exists in these
  files; "alltenures" is a portal export naming artifact, not a signal that more tenures are present).
- No 16th/17th Lok Sabha UI, button, placeholder, disabled state, or "coming soon" screen anywhere.

### 6.2 House as a shared filter, not a duplicated feature
A single `house` query parameter (`LS` / `RS` / omitted-for-both) threaded through: `/api/summary`,
`/api/queue`, `/api/analytics`, `/api/map-data`, `/api/map-works`, `/api/map-filters`, `/api/graph-data`,
and the Copilot's grounding context. No page is forked into an LS-only and RS-only version; the existing
page renders whatever the shared filter returns. This directly follows your instruction not to duplicate
functionality per House.

### 6.3 Frontend House selector — explicit, minimal, requested addition
The current frontend has **no** House selector anywhere (verified: zero matches for "House" as a UI
concept in any page). Per your correction #3, this is an *explicitly requested* new feature (falls under
exception A of your frontend rule, not exception B) — build it as a small, shared control (e.g., a
two-button toggle `[ LOK SABHA ] [ RAJYA SABHA ]`, plus an implicit "both" default state matching current
unfiltered behavior) placed once in the shared app layout/header, wired to the `house` query param above,
with **no navigation restructuring** and **no per-page redesign**. Scoped precisely in Phase 3's Claude
Code prompt (§10) as the one frontend addition in that phase.

### 6.4 Constituency-only features under RS
`getConstituencyIntelligence`, `getConstituencyPerformance/Comparison`, and the map's constituency drill-
down level are inherently Lok-Sabha-only (Rajya Sabha members have no constituency). This is not a gap to
fill — document it plainly in each response (`constituency: null` / an explicit "not applicable for Rajya
Sabha" state, not a silent empty result the frontend might mistake for a loading or error state) and in
the UI copy the House selector's minimal implementation must include (e.g., disabling or relabeling a
constituency filter when RS is selected — still a small, explicit, justified part of the same minimal
addition, not a separate redesign).

---

## 7. FINAL ML ARCHITECTURE

Unchanged in method from v1 §6; restated with your scope-discipline correction applied.

### 7.1 Required in this build (status 2026-10-02: see §13.z)
1. **Multivariate atypicality — Isolation Forest + Mahalanobis distance (B4, co-primary)**, exactly as in
   v1 §6.1: small interpretable feature vector (log-amount, peer deviation ratio, lag days, payment/payee
   counts, description length), no MP/payee identity, no `risk_score` as a feature, fixed seed, per-feature
   Mahalanobis contribution stored for explainability, Isolation Forest as a comparator, both evidence-only. Built; not yet shown in the app.
2. **365-day delay early warning (A2)**, as in v1 §6.2: day-90/day-180 checkpoints, only information dated
   at-or-before the checkpoint, out-of-time split, Brier score + calibration against a baseline. **Built and
   closed (§13.z):** worse than the base rate at day 90, no better at day 180; no registry row, nothing stored.
3. **Survival/completion-time model (A1)** was built only as supporting infrastructure for A2. With A2 closed,
   A1 is an inactive experiment (registered for run 1 only); it feeds no signal, forecast or page, and
   `forecast_result` is empty. No ML enters `risk_score` beyond the TF-IDF similarity in §7.2.

### 7.2 Explicitly deferred, built only if a specific dependency requires it
Fund-flow forecasting (A3), expected-cost model (B3), duplicate-detection embeddings (B1's embedding
upgrade — the base TF-IDF version stays part of the *explainable* Near-Duplicate signal, Phase 4; it is the
"ML-based similarity" in the owner-approved framing "explainable statistical scoring with ML-based similarity and
atypicality evidence" (2026-10-01) and the only ML input to risk_score),
payee-resolution ML beyond Phase 2's rule-based typing. **None of these may be started before Phase 6/7 are
complete and accepted**, and none may be substituted in place of either required system.

### 7.3 Staging and leakage rules
Unchanged from v1 §6.3/§6.4: no ML layer (B4, A1, A2 or any deferred item) enters `risk_score` without a
pre-registered study clearing gate G6, and the TF-IDF similarity inside Near-Duplicate (§7.2) is the only ML
in the score; no MP/payee identity or `risk_score` as a feature; out-of-time splits only; censoring correctly
excludes not-yet-resolved works from *training labels* while still allowing them to receive a live A2 *prediction*.

---

## 8. FINAL MAP ARCHITECTURE (revised per your correction #5)

### 8.1 Primary model: aggregated, area-first, exactly as you specified
```
NATIONAL → STATE → DISTRICT → CONSTITUENCY (Lok Sabha only) → PAGINATED WORK LIST → WORK DOSSIER
```
Real area geometry (BLUEPRINT.md §15's boundary-source candidates, licence-checked) wherever available;
pre-aggregated `geo_metric` per run; the API never scans the works table to draw the map (fixes the
confirmed full-table-scan-per-request defect). This is the **authoritative** way to reach an individual
work: drill down to an area, page through its work list, open the dossier — not click a pin that implies a
precise site.

### 8.2 Individual markers: bounded, honest, unjittered, and now behavior-changed per your instruction

Your correction is explicit that jitter must not be added "simply because the old frontend expects
markers," and that geographic honesty outranks visual compatibility. Applying that:

- `getMapWorks`' **request shape is preserved exactly** (same params: `state`, `constituency`, `priority`/
  `risk_level`, `stage`, `search`, `limit`) — the frontend's existing calls keep working unmodified.
- **Response behavior changes:** works sharing a constituency now share **one identical, unjittered**
  coordinate (the constituency's real or best-available representative point) — no synthetic offset is
  invented. Every returned work object gains an additive field, `location_precision:
  "approximate_constituency_level"` (never omitted, never ambiguous), and the response gains a top-level
  `note` field with fixed, honest copy (e.g., "Points show the constituency, not the individual work
  site — open a work from the list for details") that the frontend can display or ignore without breaking
  either way (additive field, zero-diff if unused).
- This is **not** a return to the old system's stacking problem, because the *old* system's fault was
  never disclosing this and never bounding volume; here, volume is bounded by (a) keeping the existing
  proportional-stratified-by-state sampling logic when a filtered result exceeds the response limit, and
  (b) — see §8.3 — proposing that the frontend render same-point works as a single clustered, countable
  bubble rather than as overlapping raw pins, which is the standard honest way to display "many things
  share this approximate area."
- The endpoint is never allowed to return tens of thousands of individual markers regardless of filter, per
  your explicit instruction — the existing sampling cap and its per-state quota logic (already reasonably
  designed in the old code, per v1's audit) is kept and enforced as a hard limit, not a soft default.
- Markers remain **optional** — the existing `showWorkMarkers` toggle in `Map.jsx` is untouched; users who
  never enable it never see individual points at all, only the aggregated area layer.

### 8.3 FLAGGED, NOT YET APPROVED: enable the already-installed marker-cluster library

Per §1.9's finding, `leaflet.markercluster` is already an installed-but-unused frontend dependency. Wiring
it into `Map.jsx`'s existing marker layer (so that many works sharing one constituency point render as a
single numbered cluster bubble, not N overlapping dots) is, in this plan's judgment, the cleanest way to
satisfy simultaneously: "must be bounded," "must not create a cloud of synthetic-looking points," "must not
be presented as precise coordinates," and "preserve the backend contract where possible" — all stated as
requirements in your correction, which otherwise sit in some tension with each other once jitter is ruled
out. **This is a frontend source change** (wiring an existing dependency into `Map.jsx`'s marker rendering
path) and per your own rule 1, it needs your explicit sign-off before Phase 9 executes it, even though it
uses a library already sitting in `package.json`. Phase 9's prompt (§10) is written to implement the
backend half unconditionally, and to **STOP and ask for confirmation** before touching `Map.jsx`, rather
than assuming approval. If you'd rather markers stay exactly as individually-rendered dots (just fixed to
be unjittered/honest/labelled/capped, per §8.2, with no frontend touch at all), say so and Phase 9 skips
the cluster-wiring step entirely — either choice is fully compatible with the rest of this plan.

### 8.4 Map regression test suite (expanded per your correction #5's explicit list)
Special characters (`(`, `[`, `*`, `?`, and an unbalanced `((`) on every search-capable param; empty search;
malformed/invalid search input generally; large-result-set requests (confirm capping, not truncation-with-
no-notice); aggregation correctness (every area's totals sum to its parent's total, and to the House-scoped
national total); pagination of the work list behind a drill-down; payload-size budgets; stale-data handling
(what the map shows if the latest run's aggregation job hasn't completed — must show the last published
run, labelled with its date, never a blank or half-updated view); loading and error states for every one of
the six map endpoints, individually and in combination (one endpoint failing must not blank the whole page).

---

## 9. FINAL GRAPH / COPILOT ARCHITECTURE

### 9.1 Graph — unchanged from v1 §Phase 10 in substance
Bounded, exploratory, offline-computed per run, relational (not a graph database), never a risk-score
input, never "evidence of wrongdoing by association." `GET /api/graph-data` contract preserved exactly.
House-scoping applies here too (a graph view can be filtered to LS-only, RS-only, or both, via the same
shared `house` param — no separate graph feature per House).

### 9.2 Copilot — unchanged from v1 §Phase 11 in substance, House-context added
Two-layer design (local KB + optional Gemini) preserved exactly; `GET /api/chat/status` and `POST /api/chat`
contracts preserved exactly. Methodology text generated from live engine config (weights, thresholds,
signal definitions, House-scoping rules), not hand-authored, closing the confirmed drift risk. When a
query concerns a specific work, the copilot fetches and describes the actual stored `risk_result`/
`forecast_result` row — including its `house` — and fails closed (states it has no stored result) rather
than estimating one. Never computes a score, never claims fraud, never invents a fraud probability — same
hard rules as v1, restated because they are non-negotiable regardless of what else changes.

---

## 10. FINAL PHASED ROADMAP

Your suggested structure, adopted with the MP Report branch removed and a dedicated Phase 14 added for
Vercel/deployment (kept separate from Phase 13's security/testing hardening so neither gets diluted).

| # | Phase | Key content (delta vs. v1) |
|---|---|---|
| 0 | Architecture + frontend API contract | Unchanged in method; contract table now includes House annotations (§5) |
| 1 | Database + ingestion + provenance | Unchanged; `work.house` derived at ingest; `tenure` restricted to two rows (§4) |
| 2 | Normalization + entity resolution | Unchanged from v1 |
| 3 | House scope + context/peer engine | **Merged phase**: House selector (minimal, explicit frontend addition, §6.3) built alongside the corrected peer engine (unchanged math from v1 §Phase 3), since both are prerequisites for every downstream signal/query being House-aware from the start |
| 4 | Explainable anomaly signals | Unchanged from v1, all six signals now carry `house` |
| 5 | Risk + confidence + compliance | **Reachability gate is now a hard, named exit condition** (§7 of this doc / §11 below) — not just "run both configs and report," but an explicit pass/fail gate before either config may be called the production default |
| 6 | Isolation Forest / multivariate ML | Unchanged from v1 Phase 6 |
| 7 | Survival + 365-day delay prediction | Unchanged from v1 Phase 7, with A1 explicitly scoped as A2's supporting model only (§7.1) |
| 8 | ML validation + explainability + model registry | Unchanged from v1 Phase 8 |
| 9 | Geo/map backend | **Revised**: no jitter; unjittered shared constituency points + honesty labelling (§8.2); marker-cluster frontend wiring is a flagged, confirm-before-executing step (§8.3), not an assumed default |
| 10 | Graph + entity analytics | Unchanged from v1 Phase 10, House-scoped |
| 11 | Copilot/chatbot | Unchanged from v1 Phase 11, House-context added |
| 12 | API integration with the protected frontend | Unchanged in method from v1 Phase 12; contract table now includes House params; MP-performance cutover confirmed current-tenure-only (§1.8) |
| 13 | End-to-end testing + hardening | Unchanged from v1 Phase 13 |
| 14 | **NEW — Vercel deployment** | New phase; since 2026-10-02 frontend + container backend as Vercel Services; see §12 and Phase 14 prompt below |

Cut line if time is short (unchanged principle from BLUEPRINT.md §13, restated): never drop reconciliation,
property tests, authentication, the audit trail, or the Phase 5 reachability gate. Phase 14 (deployment) is
the first candidate to defer if the screening deadline is tight and a local/dev deployment is acceptable
for the demo — flag this to the team as a real trade-off, not a silent slip.

---

## 11. RISK ENGINE GATE — explicit requirement for Phase 5 (your correction #9, made concrete)

Before Phase 5 may declare either configuration a "production default," it must produce, as a committed
artifact (not just console output):

1. **Reachability analysis**: for the fixed weights, what is the mathematically maximum possible
   `risk_score` given realistic (not synthetic-maximal) signal co-occurrence rates observed in the real
   Snapshot A data? Show the math, not just a sampled distribution.
2. **Score distribution**: histogram of `risk_score` under both configs on real data, with tier cut lines
   overlaid.
3. **Active-signal overlap**: for works that DO reach HIGH/CRITICAL, which signals co-occur, and how often
   — is CRITICAL, if reached at all, reached by genuinely independent corroboration or by one dominant
   signal plus noise?
4. **Sensitivity analysis**: perturb weights ~20%, report top-1,000 overlap (BLUEPRINT.md §12's method).
5. **Ablation**: remove each of the 6 base signals + the corroboration component in turn, report the effect
   on tier counts and top-1,000 overlap.
6. **Tier-boundary tests**: hand-built fixtures at each threshold boundary (e.g., a work engineered to land
   at exactly 0.849 vs 0.850) confirming the tier assignment is stable and explainable, not an artifact of
   floating-point noise.
7. **Missing-signal behaviour**: confirm a work with N of 6 signals ineligible (not "scored zero") is
   handled via confidence, never treated as if the missing signals actively scored zero risk.
8. **Explicit written justification** for whichever config (if either) is proposed as the default —
   citing 1–7, not asserting it.

**Automatic failing conditions for this phase** (per your correction, made literal): the phase fails if (a)
`RISK_THRESHOLDS` values were changed with no other change, purely to increase the CRITICAL count, or (b) a
target tier distribution was decided before running 1–7 and the analysis was used to justify a
pre-decided number rather than to inform an undecided one. Both are to be treated as review-blocking, not
just discouraged.

---

## 12. VERCEL DEPLOYMENT — architecture note

**Updated 2026-10-02 to the deployed layout.** The original note (frontend only on Vercel, backend on a
persistent server) is superseded. The Phase 14 prompt below keeps its original text, with a note.

One Vercel project using the **Services** preset, defined by the root `vercel.json`:
1. **`frontend` service**: `frontend/`, framework `vite`. Its own `rewrites` entry is the SPA fallback:
   any path whose last segment has no dot (no file extension) gets `/index.html`, so every client-side
   route survives direct navigation and refresh. A path with an extension (an asset or a missing file)
   is not rewritten.
2. **`backend` service**: `backend_v2/`, runtime `container`, built from `backend_v2/Dockerfile.vercel`
   (FastAPI on uvicorn). Stateless: the data lives in the database. Rate-limit counters are per instance.
3. **Routing** (top-level `rewrites`, in order): `/api/*` goes to `backend`, everything else goes to
   `frontend`. With `VITE_API_BASE_URL` unset, the frontend calls `/api` on its own origin.
4. **Database**: PostgreSQL holding a **slim copy of published run 44** (checksum
   `c4d589e0e0fc40621d4e011a64a7233d`). It carries only what the API serves. The offline pipeline's
   input and intermediate tables exist but are empty (`SLIM_EMPTY_TABLES` in
   `backend_v2/scripts/postdeploy_gate.py`). `GATE_SLIM=1` runs the post-deploy gate in this mode.
5. **Worker / pipeline**: not deployed. Runs are computed offline, and production serves run 44.

Environment variable names (required in the Vercel project, pinned in `Dockerfile.vercel`, never set on
Vercel, optional) are in `DEPLOY_NOTES.md`. Values never appear in the repo. `DEPLOY_NOTES.md` also
lists the checks to run once on a preview.

Carried over from the original note: `services/api.js` reads `import.meta.env.VITE_API_BASE_URL`,
falling back to `/api`; `frontend/.env.example` documents it as public; CORS is an exact origin list,
never `*`. `frontend/vercel.json` (the standalone SPA rewrite from the first layout) is still in the
repo, but the Services layout routes through the root `vercel.json`.

The first Phase 14 layout (2026-09-28: Vercel frontend, local Docker backend behind a Cloudflare quick
tunnel, `docker-compose.deploy.yml`) is superseded; `docs/deployment.md` describes the current layout.

---

## 13. CLAUDE CODE PROMPTS — ONE PER PHASE (0–14)

Format for every phase, per your requirement: **MAY CHANGE / MUST NOT CHANGE / TESTS / ACCEPTANCE CRITERIA /
REGRESSION CHECKS / STOP CONDITION.** Copy one block at a time; do not proceed to the next until you've
tested and approved the current one.

---

### PHASE 0 — Architecture + frontend API contract

```
Phase 0 of the Sentinel MPLADS rebuild. Architecture scaffolding and contract freezing ONLY — no business
logic, no signals, no risk scoring, no ML, no House logic yet.

INSPECT FIRST (read-only):
- backend/app/api/routes.py and frontend/src/services/api.js — the exact API contract to preserve.
- backend/app/core/config.py for the current provisional weights/thresholds (reference only).
- frontend/src/App.jsx for the exact list of client-side routes (needed later for Phase 14, useful context
  now).

MAY CHANGE / CREATE:
- A new backend skeleton under backend_v2/ (FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic), separate from
  the old backend/app/, which remains the live reference implementation until Phase 12's cutover.
- docker-compose.yml with services: api, worker (stub, no logic), postgres, and a static server for the
  existing frontend/ mounted UNMODIFIED.
- CI config (lint, empty pytest placeholder, and the frontend contract test suite below).
- docs/frontend_contract.md — generated by actually reading services/api.js line by line, listing every
  endpoint (~35), its method, params, and response shape, WITH a `house` column noting which endpoints will
  later accept a House filter (per this plan's §5/§6 — mark as "planned," not implemented yet).

MUST NOT CHANGE:
- Anything under frontend/.
- backend/app/ (old backend; leave running as-is).

TESTS:
- The frontend contract test suite (generated from docs/frontend_contract.md) runs against STUB responses
  and passes — asserting route existence, method, params, and top-level response shape only, not real data.
- `docker compose up` brings up all four services healthy.

ACCEPTANCE CRITERIA:
- Fresh clone + `docker compose up` gives a running (empty) new API, running Postgres, and the OLD
  frontend still servable and pointed at the OLD backend — the live product is unchanged.
- docs/frontend_contract.md lists all ~35 endpoints from services/api.js, verified against the file, not
  from memory.

REGRESSION CHECKS: none yet (first phase).

STOP CONDITION: stop after this phase regardless of test results. I will review docs/frontend_contract.md
and the CI setup before authorizing Phase 1.
```

---

### PHASE 1 — Database + ingestion + provenance

```
Phase 1: ingestion pipeline, provenance, and static geo reference data. No signals, no risk scoring, no ML,
no House-aware querying yet (House is TAGGED at ingest in this phase; House-aware FILTERING is Phase 3).

INSPECT FIRST:
- data/raw/snapshot_a/*, data/raw/snapshot_b/*, data/raw/prior_cycle/*, data/raw/macro/*,
  data/MANIFEST.sha256 — read every file's header and first 20 rows before writing any parser.
- BLUEPRINT.md §2 control totals table and §4/§5 pipeline stage specs.

MAY CHANGE / CREATE (all under backend_v2/, none under frontend/ or backend/app/):
- Alembic migrations: source_snapshot, raw_file, raw_row, control_total, import_reject, state,
  state_alias, district_authority, activity_type, person, tenure (restricted per this plan's §4 to exactly
  two live rows: 18th LS per-MP, sitting RS per-member — no schema restriction needed against a THIRD row
  existing later, but no seed/ingest path may create one now), constituency, geo_area,
  geo_name_crosswalk.
- work.house column: derived directly and only from source filename (`*_LokSabha_*` → 'LS',
  `*_RajyaSabha_*` → 'RS') — never inferred from constituency presence/absence or any other heuristic.
- P0 Register (hash + provenance metadata), P1 Parse + contract check (pandera schemas per file, handle
  the prior-cycle file's semicolon delimiter, split footer rows before parsing), P2 Reconcile (body sum vs
  footer total vs BLUEPRINT.md §2's control totals — ANY mismatch stops the run).
- Geo reference load: state names/aliases, district keys parsed from IDA names, one boundary source from
  BLUEPRINT.md §15 loaded into geo_area with recorded licence/version, geo_name_crosswalk built with
  unmatched names explicitly listed, not dropped.

MUST NOT CHANGE:
- frontend/, backend/app/.

TESTS:
- Reconciliation: every one of the 9 core files' body sum matches its footer total, and matches
  BLUEPRINT.md's control totals table within rounding tolerance.
- Confirm works_recommended_RajyaSabha is ABSENT from snapshot_a — assert this as EXPECTED, not a failure.
- Idempotency: re-running P0-P2 on the same files reproduces the same row counts and output hash.
- House-tagging test: every ingested work row has house ∈ {'LS','RS'}, derived solely from its source
  filename, verified against a manifest of which file each test row came from.
- Geo crosswalk match-rate: reported, not hard-failed on, in the provenance output.

ACCEPTANCE CRITERIA:
- All 9 core files reconcile.
- raw_row is complete and insert-only.
- import_reject has zero rows for the 9 core files.
- geo_area has at least national + state-level polygons with recorded source/licence.
- Every work row is correctly House-tagged.

REGRESSION CHECKS: re-run Phase 0's contract suite (should be unaffected — this phase touches no API layer
yet).

STOP CONDITION: stop after this phase. I will inspect the reconciliation report, the reject log, and a
sample of House-tagged rows before authorizing Phase 2.
```

---

### PHASE 2 — Normalization + entity resolution

```
Phase 2: normalized core schema, entity resolution (payees, agencies, MPs), still no risk scoring or ML.

INSPECT FIRST:
- BLUEPRINT.md §2 "Verified facts" and §5 P3-P5 stage specs.
- Old backend/app/services/data_service.py and core/config.py's WORK_CATEGORY_KEYWORDS — REFERENCE ONLY;
  the new activity-type parsing must use the official ACTIVITY_NAME code-prefix parsing BLUEPRINT.md §2
  describes, not keyword matching.

MAY CHANGE / CREATE (backend_v2/ only):
- Migrations: work, work_state, payment, allocation, calamity_consent, prior_cycle_work, macro_reference,
  payee, payee_alias, implementing_agency.
- P3 Normalise: parse ACTIVITY_NAME into activity_type (~115 types); district key from IDA name; date
  normalisation; state alias mapping.
- P4 Link + derive lifecycle: join recommended/sanctioned/completed/payments on work_key; derive lifecycle
  from file membership + dates (NEVER from the portal's own stale WORK_STAGE field — store it separately
  as raw_stage, never read by downstream logic); log (don't hide) the 361 sanctioned-but-unrecommended
  works as a referential note.
- P5 Entity resolution: payee dedup by portal ID (never auto-merge names across IDs — 1,045 names are
  shared by multiple IDs); agency typing rules; MP roster join by normalised name; fuzzy/unmatched names
  queued for manual review, never auto-merged.

MUST NOT CHANGE:
- frontend/, backend/app/.

TESTS:
- Parse-rate test: activity type + district key parse at ~100% on Snapshot A (BLUEPRINT.md's measured
  figure) — investigate any material shortfall.
- Roster join test: MP name-matching rate (blueprint states 100% on Snapshot A — verify).
- Uniqueness test: work_key unique across both Houses.
- Payee alias test: no alias silently merges two distinct portal IDs.
- No-positional-merge test: grep the new code for `.iloc[` used inside a merge/join context and fail if
  found — every join must be by explicit key column (targets the "positional misalignment" defect class).

ACCEPTANCE CRITERIA:
- Every completed work has a matching sanction record; every payment matches a sanctioned work.
- Lifecycle status is derived from file membership/dates only; raw_stage is stored but never consumed
  downstream in this or any later phase.

REGRESSION CHECKS: Phase 0 contract suite; Phase 1's reconciliation + House-tagging tests still pass on the
now-normalized data.

STOP CONDITION: stop after this phase. I will spot-check normalized records against raw source rows before
authorizing Phase 3.
```

---

### PHASE 3 — House scope + context/peer engine

```
Phase 3 combines two prerequisites every downstream phase depends on: (1) the corrected peer/context
engine (fixing the two confirmed bugs — self-inclusion in baselines, constituency-keyed Level-1 groups),
and (2) the House selector — the ONE explicitly-requested new frontend feature in this entire rebuild.

INSPECT FIRST:
- OLD buggy implementation for reference (what NOT to do): backend/app/context/peer_builder.py (Level-1 =
  constituency-keyed; `transform('count')` includes self) and context/context_service.py (group median/MAD
  computed including self).
- BLUEPRINT.md §6 "Peer hierarchy" table.
- frontend/src/App.jsx (route list) and any shared layout/header component, to find the single best place
  to add a House toggle without restructuring navigation.

MAY CHANGE / CREATE:
Backend (backend_v2/ only):
- peer_group, work_context tables (run-scoped: level used, n_usable EXCLUDING self, distinct-MP count,
  median, scale).
- Peer levels: Level 1 = work type × state × sanction FY (≥15 usable peers excluding self, ≥3 distinct
  other MPs, no MP >50% of group); Level 2 = work type × state (all FYs); Level 3 = work type × national ×
  FY; Refinement = work type × district × FY (only when ≥3 MPs and ≥15 peers).
- Leave-one-out median/MAD/IQR computed correctly (median/MAD are not linearly decomposable — implement a
  real leave-one-out transform or an exact tested correction formula, not an approximation).
- `house` as a first-class, indexed filter dimension on every context/peer query (peer groups themselves
  are NOT segmented by House — a work-type × state × FY peer group can and should include both Houses'
  works where applicable, since that's the more statistically valid comparison; House only filters which
  works are DISPLAYED/queried downstream, not how peer baselines are constructed).
- New API params: `house=LS|RS` (optional, omitted = both) added to /api/summary, /api/queue,
  /api/analytics, /api/map-data, /api/map-works, /api/map-filters, /api/graph-data — additive, backward
  compatible (omitting the param preserves current unfiltered behavior exactly).

Frontend (frontend/ — the ONE explicitly-approved touch-point in this phase, keep it minimal):
- A small, shared two-button House toggle (`[ LOK SABHA ] [ RAJYA SABHA ]`, both-unselected = current
  default/unfiltered behavior), added ONCE to the shared app layout/header — not duplicated per page, not
  a navigation restructure. Wire it to append `?house=LS` / `?house=RS` (or omit) to the relevant API
  calls already made by each existing page. Do not redesign, restyle, or restructure any page to
  accommodate it — it is a filter control, not a new section.
- Where constituency-scoped UI exists (map drill-down, constituency performance/comparison), show a small,
  explicit "not applicable for Rajya Sabha" state when RS is selected, rather than a silent empty result.

MUST NOT CHANGE:
- Anything else in frontend/ — no other page restructuring, no restyling, no navigation changes beyond
  adding the one shared toggle.
- backend/app/ (old backend, still the live reference until Phase 12).

TESTS:
- Known-defect regression test: construct a peer group of size 4 where one record's amount is 100x the
  others; prove leave-one-out actually changes that record's OWN peer_median relative to an
  (intentionally, for the test) self-inclusive calculation — the two must differ.
- Coverage test: reproduce BLUEPRINT.md §6's measured Level-1 coverage on Snapshot A (92.7% ≥10 peers,
  90.5% ≥3 distinct MPs) within documented tolerance.
- Property test: row-order shuffling never changes any work's peer_median/mad/iqr.
- Property test: no peer group's single MP contributes >50% of the group's works.
- Grep-based test: no peer-level construction in the new code keys Level 1 on constituency alone.
- House filter test: `house=RS` on every listed endpoint returns only Rajya Sabha works; `house=LS` only
  Lok Sabha; omitted returns both, IDENTICAL to the pre-Phase-3 unfiltered response (backward-compat proof).
- Frontend House toggle test (manual + automated where feasible): selecting RS on the Queue/Analytics/Map
  pages filters results without any other visual change to those pages; constituency-scoped UI shows the
  explicit not-applicable state under RS.

ACCEPTANCE CRITERIA:
- No work's peer baseline includes its own amount (proven, not asserted).
- Peer groups are never constituency-keyed at Level 1.
- Every House-filterable endpoint behaves identically to its pre-Phase-3 self when `house` is omitted.
- The House toggle is the ONLY frontend change in this phase, and it does not alter any other visual or
  structural aspect of any existing page.

REGRESSION CHECKS: Phase 0 contract suite (must show the `house` param as newly-documented-and-optional,
not a breaking change); Phase 1/2 reconciliation and normalization tests still pass.

STOP CONDITION: stop after this phase. I will review the coverage report AND manually test the House
toggle in the browser before authorizing Phase 4.
```

---

### PHASE 4 — Explainable anomaly signals

```
Phase 4: the six BASE signals (cost anomaly, near-duplicate, portfolio concentration, district-authority
pattern, temporal anomaly, lifecycle delay) on top of Phase 3's corrected, House-aware peer engine. No
fusion, no risk score, no ML.

INSPECT FIRST:
- BLUEPRINT.md §6 "Signal catalogue" table and "Cost baseline method."
- Old backend/app/features/*.py for reference on what to keep (robust-distance approach in spirit) and
  what NOT to reuse (peer-reliability coupled into the score itself — BLUEPRINT.md §6 moves this to
  confidence only).

MAY CHANGE / CREATE (backend_v2/ only):
- signal_result rows (run, work, signal): score, tail percentile, direction, is_base=true, eligibility
  flag, evidence jsonb (baseline, observed value, peer count, level).
- Cost anomaly (25% provisional weight): robust z on log scale against Level-1 peers; direction always
  stored; reliability/dispersion terms computed but stored separately, feeding Phase 5's confidence, never
  multiplying this signal's own score.
- Near-duplicate (20%): char n-gram TF-IDF cosine, same district authority + MP or type, amount/date
  proximity, national phrase-frequency discount. One row per work — a work's own recommended/sanctioned/
  completed rows must never be flagged as near-duplicates of each other.
- Portfolio concentration (10%): MP's work-type share within own portfolio, standardised residual vs peer
  MPs in-state.
- District-authority pattern (10%): authority's type share/amount ratio vs other authorities in-state
  (replaces old constituency-name grouping — correctly handles RS members, who have no constituency).
- Temporal anomaly (10%): burst detection with national batch days removed first.
- Lifecycle delay (10%): age-since-sanction vs peer completion distribution; payment share ahead of
  completion vs peers.

MUST NOT CHANGE:
- frontend/, backend/app/.

TESTS:
- Unit tests per signal: peer groups of size 2/3/4, all-identical amounts, missing amounts (eligibility=
  false, never score=0), single-MP groups (should not reach Level 1).
- Property test: raising a work's amount above its peer median never lowers its cost anomaly score.
- Property test: every signal_result row attaches to the correct (run, work) — no positional misalignment,
  checked for all six signals, not just cost.
- Batch-day exclusion test: synthetic single-day burst does not fire the temporal signal once flagged as a
  batch day.
- Stage-multiplied-rows test: same work_key's recommended + sanctioned + completed rows are NOT flagged as
  near-duplicates of each other (explicit fixture for this named defect class).
- House-neutrality test: confirm signal scores for a given work are unaffected by the `house` query filter
  applied at read time (House filters WHICH works are returned, not how any individual work's own signal
  was computed — this is a critical distinction to test explicitly, since getting it backwards would
  silently make House filtering change risk conclusions, which it must never do).

ACCEPTANCE CRITERIA:
- All six signals produce (score, eligibility) for every work, no missing pairs.
- No signal's score formula includes any confidence/reliability term (code-inspection test, not just unit
  test — grep for peer_reliability/n_usable/peer_group_size used as a multiplicand of the returned score).

REGRESSION CHECKS: Phase 0 contract suite; Phase 3's House-filter and peer-engine tests still pass.

STOP CONDITION: stop after this phase. I will spot-check signal explanations on known-anomalous and
known-normal works, across both Houses, before authorizing Phase 5.
```

---

### PHASE 5 — Risk + confidence + compliance (reachability-gated)

```
Phase 5: fuse the six base signals into risk scores, compute confidence separately, run the compliance
panel, and — this phase's hard requirement — clear the reachability/sensitivity/ablation gate (this plan's
§11) BEFORE either scoring configuration may be called a production default. Do not skip straight to
picking a config.

INSPECT FIRST:
- OLD buggy fusion for reference (what NOT to blindly copy): backend/app/risk/signal_fusion.py (fixed 1.0
  denominator), risk/corroboration.py (1.15x cap), risk/risk_engine.py (MIN_CRITICAL_SIGNALS=3 demotion).
- BLUEPRINT.md §6 "Fusion and corroboration" and this plan's §11 in full — §11 is this phase's actual exit
  criteria, read it before writing any fusion code.

MAY CHANGE / CREATE (backend_v2/ only):
- risk_result, compliance_result tables, keyed by (run, work, config_name) so v3-compatible and
  v4-candidate are both stored per run, never overwriting each other.
- v3-compatible config: faithfully reproduces the old additive-pattern-plus-multiplier formula (as a true
  "before" baseline) but with the CONFIRMED bugs already fixed upstream in Phase 3/4 (leave-one-out peers,
  no self-inclusion) — the fusion math itself stays as-is for this config, deliberately, so it's a fair
  comparison point.
- v4-candidate config: corroboration as multiplier ONLY (no separate additive Pattern weight double-
  counting the same evidence); base weights renormalised over the 6 base signals summing to 1.0.
- Confidence: peer reliability, dispersion, field completeness, data-quality flags — separate table/column
  from risk_score. A signal with missing inputs is "not evaluated" (lowers confidence), never silently 0.
- Tiers: LOW 0-39 / MODERATE 40-64 / HIGH 65-84 / CRITICAL 85-100 as the PROVISIONAL starting point for
  both configs — not yet frozen; CRITICAL requires ≥3 active base signals as a provisional default, subject
  to this phase's gate.
- Compliance panel: C1-C9 from BLUEPRINT.md §6's table, run separately from risk_score, House-tagged.
- THE REQUIRED GATE ARTIFACT (this plan's §11, items 1-8): a committed report (markdown or notebook output
  checked into the repo, not just console logs) containing the reachability analysis, score distributions
  for both configs, active-signal overlap, sensitivity (~20% weight perturbation, top-1000 overlap),
  ablation (each signal + corroboration removed in turn), tier-boundary fixture tests, missing-signal
  behaviour confirmation, and an explicit written justification for whichever config (if either) is
  proposed as default.

MUST NOT CHANGE:
- frontend/, backend/app/.
- Do NOT adjust RISK_THRESHOLDS or MIN_CRITICAL_SIGNALS-equivalent values as a way to produce a
  "better-looking" tier distribution — any threshold change in this phase must be justified BY the gate
  report's analysis, not made first and rationalized after.

TESTS:
- Known-defect regression test: construct a fixture work with 5 of 6 base signals at score=0.9. Compute
  what the OLD formula would have produced (documented in the test's docstring) and assert v4-candidate
  produces a materially different, higher, defensible score for this clearly-multi-signal-anomalous case.
- Property test: re-running fusion with the same inputs/config/seed reproduces the same output hash.
- Property test: v4-candidate's base weights (excluding corroboration) sum to exactly 1.0.
- Compliance test: reproduce BLUEPRINT.md §6's C1-C9 baseline findings (0 violations C1-C4, ~11.9% over-
  one-year C5, etc.) within tolerance.
- Confidence-not-zero test: a work with one ineligible signal has lower confidence than an otherwise-
  identical fully-eligible work, while the ineligible signal is excluded from — not zero-valued inside —
  the weighted-sum denominator in v4-candidate.
- THE GATE ITSELF, as an automated check: CI fails if risk_result exists for a run but no corresponding
  gate-report artifact (§11 items 1-8) is present and dated at or before that run.

ACCEPTANCE CRITERIA:
- Both configs computed and stored for every work, every House.
- The gate report exists, is complete (all 8 items from §11), and is legible to a non-specialist reviewer.
- The known-defect regression test passes with documented before/after numbers.
- No threshold was changed without the gate report's analysis preceding and justifying it.

REGRESSION CHECKS: Phase 0 contract suite; Phase 3/4 tests.

STOP CONDITION: stop after this phase regardless of which config the report favors. I will review the gate
report in full — this directly answers whether the CRITICAL-reachability problem is actually fixed — before
authorizing Phase 6. Do not let me see a "chosen" config without the report; if the report is inconclusive,
say so plainly rather than picking one anyway.
```

---

### PHASE 6 — Isolation Forest / multivariate ML

```
Phase 6: the first REQUIRED ML system — multivariate atypicality (Isolation Forest + Mahalanobis distance),
evidence-only, never entering risk_score in this phase or any phase in this roadmap.

INSPECT FIRST:
- BLUEPRINT.md §7 row B4 and "Leakage and validity rules."
- This plan's §7.1 item 1 for the exact (co-primary, not secondary) framing.

MAY CHANGE / CREATE (backend_v2/ only):
- model_version registry entry (algorithm, feature-spec hash, training snapshot, seed).
- Feature vector: log(amount), peer deviation ratio (Phase 3/4 output), days recommendation→sanction, days
  sanction→completion-or-censored-age, payment count, distinct payee count, description length. Hard
  assertion in code (not just a comment) that MP identity, payee identity, and risk_score never appear in
  this feature matrix.
- Robust Mahalanobis distance (primary): robust covariance estimator; overall distance + per-feature
  contribution stored.
- Isolation Forest (co-primary comparator): fixed seed; document the contamination default used and why
  (never tuned to produce a "nice" flagged percentage).
- New evidence table (separate from signal_result — this is ML evidence, not a base signal; must never be
  counted by Phase 5's base_signal_count or corroboration logic).

MUST NOT CHANGE:
- frontend/, backend/app/, risk_result, signal_result schemas from Phase 4/5.

TESTS:
- Leakage assertion test: attempt to inject MP/payee identity or risk_score into the feature matrix and
  confirm the code raises rather than silently including it.
- Determinism test: same input + seed reproduces identical scores for both methods.
- Missing-data test: a work with a missing feature (e.g. no payments yet) is handled via an explicit
  "not evaluated" path, never silent-zero imputation.
- Orthogonality report (report, not pass/fail): correlation between this layer's scores and Phase 4's
  cost_anomaly_score on real data, both Houses — feeds Phase 8's validation.

ACCEPTANCE CRITERIA:
- Both scores computed for every eligible work, registered in model_version, never written into
  risk_result, never counted as a base signal.
- Feature vector verifiably excludes MP/payee identity and risk_score.

REGRESSION CHECKS: Phase 0 contract suite; risk_result schema is byte-identical to Phase 5's output
(proves this phase didn't cross gate G6).

STOP CONDITION: stop after this phase. I will review the orthogonality report before authorizing Phase 7.
```

---

### PHASE 7 — Survival + 365-day delay prediction

> **Current state (2026-10-02).** The prompt below is kept as written. Per §13.z and
> `docs/ml_architecture.md` §3, A2 was built and closed and A1 is an inactive experiment.
> `forecast_result` has 0 rows and no `horizon` column, and no A1/A2 prediction is stored. No ML enters
> `risk_score` beyond the TF-IDF similarity inside Near-Duplicate.

```
Phase 7: the second REQUIRED ML system. The survival model (A1) is built ONLY as supporting infrastructure
for the 365-day delay model (A2) — do not productize A1 as an independent deliverable or dashboard surface
beyond what A2 needs to cite it.

INSPECT FIRST:
- BLUEPRINT.md §7 rows A1, A2, and "Leakage and validity rules" (rule 3 especially).
- Phase 2's lifecycle derivation (sanction date, completion date, censoring status) — consume this
  directly, do not re-derive lifecycle dates independently.

MAY CHANGE / CREATE (backend_v2/ only):
- model_version entries for A1 and A2.
- A1: Kaplan-Meier / Cox / accelerated-failure-time on sanction→completion time. Events = observed
  completions; right-censored = open works, censored at current age as of snapshot date. Real dates only,
  no imputation.
- A2: target = P(not complete within 365 days of sanction), scored at day-90 and day-180 checkpoints, using
  ONLY information dated on or before each checkpoint (payments to date, stage transitions to date, A1's
  predicted survival curve as of that point). Hard, testable assertion that no feature's underlying date
  postdates its checkpoint.
- Out-of-time split: sort by sanction date, train earlier window, validate later window — never random
  (batch-date effects make random splits misleadingly optimistic here).
- forecast_result rows (run, work, model, horizon), House-tagged. (2026-10-02: not built; forecast_result is empty and has no horizon column, §13.z)

MUST NOT CHANGE:
- frontend/, backend/app/.

TESTS:
- Leakage test (most important in this phase): fixture where a payment happens at sanction-date+120; assert
  it does NOT appear in the day-90 feature vector.
- Censoring test: a work sanctioned <365 days before the snapshot date is excluded from TRAINING-label
  construction (still censored, outcome unknown) but CAN still receive a live A2 prediction at inference
  time — test both halves of this rule explicitly, they are not the same assertion.
- Out-of-time validation metrics: concordance index (A1), Brier score + calibration curve (A2), both
  against a seasonal-naive baseline (e.g., peer-group median completion time) — report, not hard-fail yet.

ACCEPTANCE CRITERIA:
- A1/A2 predictions stored for every eligible work, both Houses. (2026-10-02: not met by decision; A2 closed, A1 inactive, nothing stored, §13.z)
- Leakage and censoring tests pass with fixtures specific enough to have caught a real bug.
- Validation metrics report is readable by a non-ML-specialist reviewer.

REGRESSION CHECKS: Phase 0 contract suite; Phase 6's leakage/determinism tests still pass (proves this
phase didn't accidentally share state with Phase 6's feature pipeline in a way that reintroduces leakage).

STOP CONDITION: stop after this phase. I will review the validation metrics report before authorizing
Phase 8.
```

---

### PHASE 8 — ML validation + explainability + model registry

```
Phase 8: consolidate validation across Phase 6 (B4) and Phase 7 (A1/A2), finalize the registry and
explainability surfaces, and formally WRITE (not clear) gate G6 — the pre-registered-study requirement
before any ML layer could ever enter risk_score.

INSPECT FIRST:
- Phase 6/7's validation reports.
- BLUEPRINT.md §7 "Entry into the risk score (gate G6)" and §12 "What each ML layer must show."

MAY CHANGE / CREATE (backend_v2/ only):
- One consolidated validation report artifact combining B4's ablation/orthogonality result, A1's
  concordance/calibration, A2's Brier score/calibration, all against their baselines.
- Finalized model_version rows for all three models (complete metadata).
- Explainability surfaces: per-feature Mahalanobis contribution attached to each evidence row; A1/A2
  predictions always shown with their confidence interval, never a bare number.
- A markdown gate-G6 criteria document (not code): pre-registered study requirement, non-redundancy
  threshold vs existing base signals, reviewer face-validity requirement — a gate DEFINITION, not a
  decision. No ML layer enters risk_score in this phase.

MUST NOT CHANGE:
- frontend/, backend/app/, and specifically: risk_result must not gain any new ML-derived column in this
  phase. If you find yourself wanting to add one, STOP — that would be crossing gate G6 without its
  required pre-registered study.

TESTS:
- Registry completeness test: every model has non-null algorithm, feature-spec hash, training snapshot
  reference, at least one recorded metric.
- Regression test: risk_result's schema is byte-identical to Phase 5's output.

ACCEPTANCE CRITERIA:
- One consolidated, human-readable validation report covering all ML layers.
- Gate G6 criteria written down, reviewable, NOT met/waived by this phase.

REGRESSION CHECKS: Phase 0 contract suite.

STOP CONDITION: stop after this phase. I will review the validation report and the gate G6 document before
authorizing Phase 9.
```

---

### PHASE 9 — Geo/map backend

```
Phase 9: the map's backend, revised per this plan's §8 — no jitter, no fake work-site precision, an
aggregated area-first model as primary, and a bounded, honestly-labelled, unjittered marker layer as
secondary/optional. This phase has a mandatory STOP-and-confirm sub-step before touching any frontend file.

INSPECT FIRST (the most important inspection step in this roadmap):
- frontend/src/pages/Map.jsx in full — understand exactly what it renders (the L.geoJSON polygon layer,
  the L.marker work-marker layer behind the showWorkMarkers toggle, search box, constituency drill-down
  panel). This is what must keep working from the frontend's point of view.
- frontend/src/services/api.js's getMapData, getMapWorks, getMapFilters, getGeoJSON,
  getGeographicCoverage, getConstituencyIntelligence — the contract to preserve.
- frontend/package.json — confirm `leaflet.markercluster` is listed as a dependency and confirm (via grep)
  that Map.jsx does NOT currently import or use it.
- OLD backend's core/engine.py get_constituency_map_data and get_map_works — the two confirmed bugs you
  are fixing: unescaped-regex `.str.contains` (crashes on special characters) and exact-centroid stacking
  (every work in a constituency at the identical point). Also read its stratified-by-state sampling logic
  — KEEP its behavior, re-implement it against DB-backed data instead of a pandas full-table copy.
- This plan's §8 in full before writing any code.

MAY CHANGE / CREATE (backend_v2/ only, unconditionally):
- geo_metric table populated per published run: pre-aggregated counts/rates/amounts per geo_area
  (state/district/constituency), House-scoped, small-number suppression below a minimum work count. Backs
  getMapData and getGeographicCoverage — pre-computed, never a live full-table scan.
- getMapWorks backend fix: replace EVERY `.str.contains(user_input, ...)` call (search AND constituency
  params — verify there are no other call sites with this bug pattern anywhere in the new backend by
  grepping for `.str.contains(` used with unescaped user input) with literal/trigram matching (`pg_trgm` or
  `re.escape()`-wrapped). Special characters must return a normal (possibly empty) result, never a 500.
- getMapWorks response: works sharing a constituency share ONE identical, unjittered coordinate (no
  synthetic offset). Every work object gains `location_precision: "approximate_constituency_level"`
  (additive field). Response gains a top-level `note` field with fixed honest copy (additive field). KEEP
  the existing proportional-stratified-by-state sampling logic when a filtered result exceeds the limit,
  re-implemented against DB-backed aggregates rather than a pandas copy.
- getGeoJSON: serve boundary polygons from geo_area (Phase 1), versioned/cached.
- getConstituencyIntelligence: source from geo_metric + risk_result, House-aware (explicit "not applicable
  for Rajya Sabha" response when queried for an RS-only context, rather than a silent empty result).

MAY CHANGE — FRONTEND, BUT ONLY AFTER YOU CONFIRM (do not implement this half until you respond to the STOP
below):
- IF, and only if, you confirm you want the already-installed `leaflet.markercluster` wired into
  Map.jsx's existing marker layer (rendering same-point works as a numbered cluster bubble instead of
  overlapping raw dots) — implement that, as the smallest possible diff to Map.jsx, using the
  already-installed dependency, no other visual/structural change to the page. If you instead confirm you
  want markers to stay as individually-rendered (now unjittered, labelled, capped) dots with NO frontend
  change at all, skip this step entirely and leave Map.jsx untouched.

MUST NOT CHANGE (regardless of the confirmation above):
- Any part of Map.jsx other than the one marker-cluster wiring decision above.
- backend/app/.

TESTS (this map regression suite is permanent, re-run in Phase 12/13 too):
- Special-character test: `(`, `[`, `*`, `?`, `((`, `a(b`, and a normal string all return 200 (results or
  empty list, never 500) on getMapData's search param AND getMapWorks' search AND constituency params.
- Empty-search and invalid-search-input tests: both handled gracefully, documented behavior.
- No-fake-precision test: assert every returned work marker's coordinate exactly equals its constituency's
  representative point (not a jittered variant) — i.e., prove NO synthetic offset was introduced.
- Bounded-payload test: an unfiltered getMapWorks response never exceeds the documented limit/sampling cap,
  at 3x the current data volume (BLUEPRINT.md's stated performance-test multiplier).
- No-full-scan test: getMapData/getMapWorks are backed by indexed/pre-aggregated lookups, not a sequential
  scan — assert via query plan or timing.
- Aggregation-correctness test: every area's totals sum to its parent's total and to the House-scoped
  national total.
- Pagination test: the work list behind a drill-down paginates correctly.
- Stale-data test: if the latest run's aggregation isn't ready, the map shows the last published run,
  labelled with its date — never blank or half-updated.
- Loading/error-state test: each of the six map endpoints failing individually does not break the others'
  display.
- Contract regression test: re-run Phase 0's suite for all six map endpoints — shapes unchanged except the
  documented additive fields (`location_precision`, `note`).
- IF the marker-cluster frontend change was confirmed and implemented: a manual test that overlapping
  works now render as a single numbered cluster, and that the cluster is clearly NOT presented as a precise
  location (visual/copy check, not just a passing unit test).

ACCEPTANCE CRITERIA:
- All backend tests above pass, unconditionally.
- No individual work marker is ever jittered, randomly offset, or otherwise presented as more precise than
  its actual constituency-level source.
- Map.jsx is either fully untouched, or touched only per the explicitly-confirmed marker-cluster decision
  above — nothing else about the page changed.

REGRESSION CHECKS: Phase 0 contract suite (full map section); Phase 3's House-filter tests, now also
verified against map endpoints.

STOP CONDITION #1 (before writing any frontend code): STOP here and ask me directly — "keep markers as
individual unjittered dots, or wire in leaflet.markercluster for clustered display?" — before proceeding
with whichever half of the frontend section above applies. Do not assume an answer.
STOP CONDITION #2 (after implementation): stop after this phase. Test the live map yourself — search for
`(Test)`, `Some[thing]`, `a*b` without the page breaking, and confirm no page displays a false sense of
work-level precision — before I authorize Phase 10.
```

---

### PHASE 10 — Graph + entity analytics

```
Phase 10: bounded, exploratory graph/entity analytics (MPs, works, payees, authorities) and payee/agency/
district profile metrics. Explicitly NOT a scoring input, NOT a graph database.

INSPECT FIRST:
- BLUEPRINT.md §8 in full.
- Old backend's GET /api/graph-data and core/engine.py's get_graph_data() — preserve this existing,
  frontend-called contract.

MAY CHANGE / CREATE (backend_v2/ only):
- Payee typing (private firm / statutory-government body / manufacturer / individual) via rules first,
  ambiguous cases queued for review.
- entity_metric rows per BLUEPRINT.md §8: payee concentration (Herfindahl + permutation null within
  district/type/FY strata), payee price position, payee reach, repeated identical payments, multi-payee
  works, payment-ahead-of-completion, district authority profile, implementing agency profile
  (minimum-n gated). All House-scoped where relevant.
- Graph computed OFFLINE per run (not a live graph database): nodes (MPs, works, payees, authorities),
  edges (recommends/pays/executes). No community detection used as a scoring input.
- Every entity_metric response includes its denominator, interval, and peer definition — enforced at the
  response-model level.

MUST NOT CHANGE:
- frontend/. GET /api/graph-data's response shape must match what the frontend's graph consumer already
  expects — read that consumer's code before finalizing the schema.

TESTS:
- No-guilt-by-association test: risk_result's schema has no payee-derived column, ever.
- Permutation-null test: a payee whose raw Herfindahl is high but whose local market is naturally
  concentrated (e.g., only 2 payees exist in that stratum) is NOT flagged as unusual.
- Wording test: every entity_metric response includes denominator + interval + peer definition, none null.
- Contract regression test: GET /api/graph-data matches Phase 0's recorded shape.

ACCEPTANCE CRITERIA:
- Payee typing precedes any concentration metric.
- entity_metric never feeds risk_result (schema test above).
- Existing frontend graph consumer works unchanged against the new backend. (2026-10-02: no frontend graph consumer exists; no Graph page was built, owner decision. The contract test covers the endpoint.)

REGRESSION CHECKS: Phase 0 contract suite; Phase 3 House-filter tests extended to graph/entity endpoints.

STOP CONDITION: stop after this phase. I will review sample payee profiles (a high-volume low-value payee,
and a naturally-concentrated small market) before authorizing Phase 11.
```

---

### PHASE 11 — Copilot/chatbot

```
Phase 11: preserve the chatbot's frontend contract exactly; close the confirmed gap (methodology text is
hand-written, not generated from live config); add House-context awareness.

INSPECT FIRST:
- backend/app/api/chat.py in FULL — a well-designed two-layer system (local KB + optional Gemini,
  graceful fallback, key never exposed to frontend) that already avoids fraud language and never scores.
  Preservation plus config-grounding, not a rewrite.
- frontend/src/services/api.js's getChatStatus and postChat — contract to preserve exactly.
- BLUEPRINT.md §11's chatbot-grounding rule.

MAY CHANGE / CREATE (backend_v2/ only):
- A "methodology renderer": generates the chatbot's methodology text from the live SIGNAL_WEIGHTS/
  RISK_THRESHOLDS/signal definitions/config_name/House-scoping rules, replacing the hand-authored
  methodology sections of the old SYSTEM_PROMPT. KEEP the hand-authored navigation/page-map sections as-is
  (they describe UI, not scoring logic, and don't drift the same way).
- A code-level (not just prompt-level) guard: any "explain this work" query fetches the actual stored
  risk_result/forecast_result row (including its house) first and fails closed ("no stored result found")
  if the fetch fails — never lets the LLM guess.

MUST NOT CHANGE:
- frontend/. GET /api/chat/status and POST /api/chat shapes must match Phase 0's recorded contract exactly.

TESTS:
- Config-drift test: change a weight/threshold in a TEST fixture config, regenerate methodology text,
  assert the chatbot's stated text reflects the change.
- No-invented-score test: simulate a missing risk_result row; assert the chatbot responds with an explicit
  "no stored result" message, never a fabricated/estimated score.
- Fraud-language test: adversarial prompts trying to elicit "fraud"/"fraudulent"/"guilty"/a fraud
  probability — assert consistent decline/redirect.
- Contract regression test: both chat endpoints match Phase 0's recorded shapes.
- Fallback test: with no Gemini key configured, local KB still answers correctly.
- House-context test: ask about a specific RS work and confirm the answer correctly reflects house='RS'
  context (e.g., never implies a constituency exists for it).

ACCEPTANCE CRITERIA:
- Methodology text demonstrably generated from live config, not a static string.
- Chatbot never computes/estimates/invents a risk/confidence/ML number.
- Frontend contract for both chat endpoints unchanged.

REGRESSION CHECKS: Phase 0 contract suite.

STOP CONDITION: stop after this phase. I will ask the chatbot real methodology questions, across both
Houses, and compare against the actual Phase 5 config before authorizing Phase 12.
```

---

### PHASE 12 — API integration with the protected frontend (cutover)

```
Phase 12: wire every preserved endpoint to real, DB-backed, published-run data through the full frontend
contract, and cut the live frontend over from the old backend to the new one.

INSPECT FIRST:
- This plan's §5 (Final Frontend API Contract) table — your endpoint-by-endpoint checklist.
- Phase 0's docs/frontend_contract.md and its generated test suite.

MAY CHANGE / CREATE (backend_v2/ only, plus deployment/env config, NOT frontend source):
- Real (not stub) handlers for every endpoint in §5, reading from published_run and its associated tables.
- GET /api/queue: real server-side pagination, exact param names/shapes confirmed from api.js — do not
  guess.
- POST /api/investigate/{id}: actor from auth token once auth exists (Phase 13 finalizes auth — if reached
  here first, use a clearly-labeled placeholder actor and flag it explicitly as a Phase 13 follow-up; do
  NOT accept actor/reviewer name from the request body).
- Remove POST /api/load entirely (confirmed zero frontend callers).
- Point the frontend's dev/staging static server at the new backend's base URL (a deployment config change,
  not a frontend source change).
- Decommission backend/app/ — archive it (branch or clearly-labeled folder), do not delete outright; it
  remains available as forensic reference.
- Confirm the `/mp-performance` cutover is current-tenure-only (per §1.8's flagged resolution) — if that
  resolution was NOT what you intended, this is the phase to say so before the old backend is archived.

MUST NOT CHANGE:
- frontend/ source files. (An API base URL environment variable is deployment config, not a source change,
  and is in scope; any .jsx file is not.)

TESTS:
- Full Phase 0 contract suite, now against REAL data, all ~35 endpoints — any shape deviation from Phase
  0's recorded contract is a blocking failure.
- Full known-defect regression suite accumulated across Phases 1-11 (self-in-peer-group, positional
  misalignment, stage-multiplied rows, special-character map search, stacked/fake map coordinates, huge
  map payload, one-failed-endpoint-blanks-dashboard, deleted chatbot route, frontend-calling-missing-API,
  broken graph endpoint, impossible risk tiers, House-filter correctness) run together as one suite.
- Manual smoke test: every frontend page (Dashboard, Map, Investigation Queue, Analytics, Record Detail,
  Data Health, MP Performance, Methodology, Landing, Login) against the new backend — no console errors,
  no blank/broken sections, both House filter states.
- Partial-failure test: simulate one endpoint failing (e.g., /api/analytics returns 500) and confirm the
  Dashboard does NOT go fully blank. If the CURRENT frontend code doesn't already guard against this, STOP
  and report it as a frontend fragility issue rather than silently patching frontend error-handling code
  without approval.

ACCEPTANCE CRITERIA:
- Every §5 endpoint is real, DB-backed, passes its contract test against real data.
- Full known-defect regression suite passes.
- The live product is usable end to end, both House filter states, with no page rendering worse than the
  old backend's equivalent (improvements like the map no longer crashing on special-character search are
  expected and correct).

REGRESSION CHECKS: as above — this phase IS the regression gate for everything built so far.

STOP CONDITION: stop after this phase. Do a full manual walkthrough yourself before authorizing Phase 13 —
treat this cutover with production-release caution.
```

---

### PHASE 13 — End-to-end testing + hardening

```
Phase 13: security hardening, performance validation at scale, backup/restore testing, final validation
report assembly.

INSPECT FIRST:
- BLUEPRINT.md §11 (Security, audit, provenance — full Controls table) and §12 (Validation framework).
- The full test suite accumulated across all prior phases.

MAY CHANGE / CREATE (backend_v2/ only, plus ops/docs):
- Authentication: token-based login (JWT/OIDC-compatible), password hashing, short-lived tokens.
  Authorisation: roles + state/district/House scopes enforced in DB queries, not just route decorators.
- CORS: explicit origin allowlist (confirmed, from Phase 14's Vercel domain once known — coordinate with
  Phase 14 rather than guessing an origin here).
- Rate limits on login, copilot, search endpoints.
- Backups: scheduled DB backups with an ACTUALLY EXECUTED restore test (not just a written procedure).
- Load/performance test: full pipeline (P0-P9) against the full real data volume; report runtime against
  BLUEPRINT.md §5's target, extrapolated to 3x per §12's stated multiplier.
- Randomised audit-sample scaffold: audit_sample/audit_review tables and tier-stratified sampling logic
  (~300 works: up to 30 CRITICAL, 100 HIGH, 100 MODERATE, 70 LOW, reviewers blind to tier) — the actual
  human review is outside this codebase's scope, but the mechanism must exist and be runnable.
- Final validation report: data reconciliation (Phase 1), known-defect suite results (all phases), Phase
  5's stability/sensitivity/ablation gate report, Phase 8's ML validation summary, and every BLUEPRINT.md
  §14 "cannot claim" item verified true of THIS implementation, not asserted true in general.

MUST NOT CHANGE:
- frontend/.

TESTS:
- Full security checklist per BLUEPRINT.md §11, one test/check per row.
- Restore-from-backup test: actually restore into a fresh instance, confirm the published run is intact.
- Full accumulated regression suite (everything from Phases 0-12), all green, run as one CI job.
- Claims-discipline test: grep/scan the final codebase and API responses to confirm none of BLUEPRINT.md
  §14's "cannot claim" items (cost-overrun detection, fraud detection/probability, any ML in the score
  before gate G6 beyond the TF-IDF similarity inside Near-Duplicate (approved framing, 2026-10-01:
  "explainable statistical scoring with ML-based similarity and atypicality evidence"),
  historical/multi-Lok-Sabha MP analysis) appear anywhere in generated text, UI copy
  sourced from the backend, or possible chatbot outputs.

ACCEPTANCE CRITERIA:
- Security checklist fully passes.
- Restore-from-backup executed and verified, not just documented.
- Validation report complete, versioned, every limitation independently verified true.
- Full regression suite passes as a single CI job.

REGRESSION CHECKS: everything accumulated, Phases 0-12.

STOP CONDITION: stop after this phase. Review the full validation report before authorizing Phase 14.
```

---

### PHASE 13.z — Vendor and ML compliance audit (recorded 2026-10-02)

Not a prompt: this records an audit run on 2026-09-29, after Phase 13.y. Full report:
`docs/ml_vendor_compliance_audit.md`. Current ML summary: `docs/ml_architecture.md`.

- **Scope:** vendor, payee, payment and agency data, and every ML layer, traced against this plan and
  BLUEPRINT.md on published run 44 (63 requirements, V1–V25 and M1–M38).
- **Outcome: the ML work does not change scores.** No score-changing (category B) fix was made, and the
  risk_result checksum stayed `c4d589e0e0fc40621d4e011a64a7233d`. The only ML in `risk_score` is the
  TF-IDF similarity inside Near-Duplicate (§7.2).
- **A2 (365-day early warning): closed.** Out of time it is significantly worse than the base rate at
  day 90 and no better at day 180. No registry row; `forecast_result` is empty. A1 stays an inactive
  experiment.
- **Atypicality (B4, robust Mahalanobis + Isolation Forest): evidence only**, not yet shown in the app.
  A refit reproduces the stored scores exactly.
- **Gate G6: not met** by any layer. The criteria are written (`docs/gate_g6_criteria.md`); thresholds
  are the owner's.
- **Written by or after it:** `docs/gate_g6_criteria.md`, `docs/model_cards.md`,
  `docs/phase8_ml_validation_report_retrospective.md`, `docs/drift_report_run1_vs_run44.md`,
  `docs/ml_architecture.md`, `docs/gate_g6_study_protocol_template.md`.

---

### PHASE 14 — Vercel deployment

> **Current state (2026-10-02).** The prompt below is kept as written. Its scope ("frontend only",
> "no Vercel-hosted backend logic of any kind", the single SPA-rewrite `vercel.json`) describes the first
> layout and is superseded by the Vercel Services layout in §12: a Vite frontend service with its own SPA
> fallback, a stateless container backend built from `backend_v2/Dockerfile.vercel`, `/api/*` routed to
> the backend, and a production database that is a slim copy of run 44. Environment variable names and
> the preview checks still to run: `DEPLOY_NOTES.md`. Current deployment doc: `docs/deployment.md`.

```
Phase 14: prepare the existing, UNMODIFIED (except for the one confirmed line in services/api.js) React +
Vite frontend for production deployment on Vercel. Backend/worker/Postgres are NOT touched or redesigned
for Vercel in this phase — they stay on their persistent container/server deployment, unchanged.

INSPECT FIRST:
- frontend/package.json (confirm build script is `vite build`, output is Vite's default `dist/`).
- frontend/vite.config.js (confirm the current dev-only `/api` proxy to localhost:8000 — this does not
  exist in a static production build, which is why the API base URL change below is required).
- frontend/src/App.jsx (confirm the full route list: /, /login, /dashboard, /map, /queue,
  /record/:recordId, /analytics, /mp-performance, /data-health, /methodology — every one of these must
  survive direct navigation and a browser refresh under Vercel's static hosting).
- frontend/src/services/api.js (confirm the exact current line: `const API_BASE = '/api';`).

MAY CHANGE / CREATE:
- `vercel.json` (new file) with an SPA rewrite: `{"rewrites": [{"source": "/(.*)", "destination":
  "/index.html"}]}` — and nothing else added speculatively; do not restate build command or output
  directory unless Vercel's auto-detection for this Vite project is confirmed to fail during this phase's
  own testing.
- ONE line in `frontend/src/services/api.js`: `const API_BASE = '/api';` becomes
  `const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';` — this is the only frontend source
  change in this entire phase (and in this entire rebuild's deployment work), it is additive/backward-
  compatible (unset env var preserves current behavior exactly), and it is required because a Vercel-
  hosted static frontend has no dev-server proxy to reach the separately-hosted backend.
- `.env.example` at the frontend root documenting `VITE_API_BASE_URL` (e.g.,
  `VITE_API_BASE_URL=https://api.yourdomain.example`), with an explicit comment that only `VITE_`-prefixed
  variables are ever exposed to the client bundle and nothing secret belongs behind this prefix.
- A `docs/deployment.md` documenting: FRONTEND → Vercel (static Vite build); BACKEND → persistent
  container/server (FastAPI + worker); DATABASE → PostgreSQL (not Vercel); WORKER → persistent worker
  environment (not Vercel); exact Vercel project settings used (build command, output directory, if
  auto-detection needed any override); the required backend CORS allowlist entries (the Vercel production
  domain and Vercel's preview-deployment domain pattern — no wildcard combined with credentials); and
  step-by-step deployment instructions a teammate could follow without you present.

MUST NOT CHANGE:
- Any other frontend source file.
- The backend, worker, or database architecture — no serverless/edge functions, no Vercel-hosted backend
  logic of any kind. If you find yourself reaching for a Vercel serverless function to solve a problem,
  STOP — that is out of scope for this phase by explicit instruction.

TESTS (every one of these must be run against the actual deployed Vercel preview, not just locally):
- Direct navigation to each of the 9 client-side routes (listed above) loads correctly, no 404.
- Browser REFRESH on each of those 9 routes loads correctly, no 404 (this is the test the SPA rewrite
  exists for — a passing "direct navigation" test alone does not prove the rewrite works, refresh must be
  tested explicitly).
- Login, Dashboard, Map, Investigation Queue, Work Dossier (Record Detail), Analytics, Graph (not built, owner decision; skip), Copilot/
  Chatbot, and the House selector (Phase 3) all function against the deployed backend, reachable via the
  configured `VITE_API_BASE_URL`.
- CORS test: confirm the backend accepts requests from the actual deployed Vercel domain (and a preview-
  deployment URL, if you generate one) and rejects an arbitrary unlisted origin.
- Env-var-fallback test: with `VITE_API_BASE_URL` unset, a local dev build still uses `/api` exactly as
  before this phase — proves zero regression to the existing local development workflow.
- Secrets-hygiene check: confirm `.env` (actual values) is git-ignored and only `.env.example` (placeholder
  values) is committed; confirm no non-`VITE_`-prefixed secret is referenced anywhere in frontend source.

ACCEPTANCE CRITERIA:
- All 9 routes survive both direct navigation and refresh under the deployed Vercel build.
- All named features (Login through House selector) work end to end against the real deployed backend.
- Exactly one frontend source line changed in this entire phase, and it's the documented, justified,
  backward-compatible API_BASE line above — nothing else in frontend/ differs from Phase 13's state.
- docs/deployment.md is complete enough that a teammate unfamiliar with this project could deploy both
  halves (Vercel frontend + persistent backend) from it alone.

REGRESSION CHECKS: the full Phase 0 contract suite, run one more time against the production-deployed
combination (Vercel frontend + persistent backend) rather than the local dev combination used in every
prior phase — this is the first time the actual deployment topology is under test, and it can surface
issues (CORS, env var wiring) that a local test never would.

STOP CONDITION: stop after this phase. This is the last phase in the roadmap. Anything beyond this point —
ML entry into risk_score via a future gate-G6 study, additional snapshot ingestion cadence, multilingual
UI, or any other "way forward" item — is future work, out of scope for this rebuild, and should only begin
as a new, explicitly-scoped phase.
```

---

## Appendix — updated known-defect checklist

Unchanged from v1's appendix, with additions reflecting this revision's own decisions:

| Defect / decision | Status | Where addressed |
|---|---|---|
| (all v1 known-defects: fixed-denominator risk math, self-inclusive peer baselines, constituency-keyed peer Level 1, regex-crash map search, stacked map markers, full-table-scan map, client-suppliable filepath endpoint, in-memory monolith, chatbot config drift) | Unchanged from v1 | Same phases as v1, renumbered per §10's table |
| Jitter-based marker fix (v1's proposal) | **Withdrawn this revision** — replaced by unjittered shared points + honest labelling + optional clustering | Phase 9, §8.2/§8.3 |
| MP Report historical/multi-Lok-Sabha feature | **Removed from scope entirely** (product decision, not deferred) | §2.1 |
| Existing `/mp-performance` current-tenure page | **Kept** — distinct from the removed feature, flagged explicitly for your confirmation | §1.8 |
| House (LS/RS) selector | **New, explicitly requested, minimal, shared-filter addition** | Phase 3, §6 |
| Deployment to Vercel | **New phase.** First layout frontend-only (one documented source line); superseded 2026-10-02 by Vercel Services (frontend + container backend) | Phase 14, §12 |
| Map cluster colours, tier legend and layout | **Added after Phase 9** (commits 9ffe10f, feb5ccb, 7aa70d2). Phase 9's "Map.jsx touched only for clustering" no longer holds | Phase 9, §8.3 |
| Methodology page rebuilt | **Added 2026-10-02** (commits ea2697e, 161d370, a8473e4; Hindi bd4f275 awaits owner review): nine pipeline steps, six signals, evidence-only ML set apart | §7, §13.z |
| Graph page | **Not built (owner decision).** `/api/graph-data` is kept and tested; no page calls it | §1.6, Phase 10, Phase 14 |
| Data freshness | The deployed app serves a **fixed snapshot: published run 44** (source data as of 2026-08-30). No pipeline runs in production. Docs only; the UI wording is unchanged | §12 |
| Landing copy | **Changed 2026-10-02.** 845fabc: step 3 describes the six-signal engine (en, hi). 2fd2864: the stale seven-check strings were removed from the 10 other locales, which now show the English text | §13.z |
| Login page | **Changed 2026-10-02.** f53b6f8: the demo credentials are labelled "Demo access for evaluation (not a real account)". Login stays a client-side demo | Phase 14 |
| Dashboard footer | **Changed 2026-10-02.** 3dd4359: shows "Data as of {date}" from `served.data_as_of` instead of the model/run label | §12, "Data freshness" row |
| Record page | **Changed 2026-10-02.** c26c4d8: the Recalculate notice says it needs a case-reviewer sign-in (Recalculate stays sign-in-only, owner decision). d6501c1: friendly Source-tab labels, no raw `ACTIVITY_NAME` | Phase 14, `DEPLOY_NOTES.md` |
| Sidebar footer | **Changed 2026-10-02.** 6623e2b: reads "MPLADS Sentinel · SIH 2026" (no "v2.0") in all 12 locales | Phase 14 |

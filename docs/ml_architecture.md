# ML architecture

Written 2026-10-02. This page summarises what machine learning exists in MPLADS Sentinel, where it runs and what it may affect. Every figure carries its source as `file:line`; a figure that could not be cited is left out. `b/` means `backend_v2/`.

## In one paragraph

- **No ML model's output enters `risk_score`.** That covers the evidence-only models and the experimental ones.
- **The only ML technique on the scoring path is TF-IDF cosine similarity**, inside the `near_duplicate` base signal (`SENTINEL_REBUILD_PLAN_v2.md:322-324`; `b/app/analytics/signals.py:54-55`).
- **Isolation Forest and robust Mahalanobis are evidence only, and are not shown in the app.**
- **A1** (Cox completion time) is an **inactive** experiment.
- **A2** (365-day logistic early warning) is **closed**. Out of time it was **worse than the base rate** at day 90 and no better at day 180.
- **Gate G6**, the only route by which any ML layer could enter the score, **is not met** (`docs/gate_g6_criteria.md:4`).
- **An atypicality score is not a fraud probability.** It says how unusual a work's combination of features is, nothing more. "Fraud" has no ground truth here, so no model is trained to predict it (`BLUEPRINT.md:359`). The model card puts it this way: "'unusual' is not 'wrong'" (`docs/model_cards.md:19`).

## 1. Production ML (in the score)

| Item | Where | Source |
| --- | --- | --- |
| The score fuses six base signals, no others | `BASE_SIGNALS` | `b/app/analytics/fusion.py:44-51` |
| TF-IDF character n-gram cosine similarity, inside `near_duplicate` | `TfidfVectorizer`, `cosine_similarity` | `b/app/analytics/signals.py:28,54-55,384-389` |
| Pinned as the only ML in the score (not yet run) | `test_fact_no_ml_in_the_score_beyond_tfidf_similarity` | `b/tests/test_phase13_claims.py:447` |
| No ML library or ML table referenced by `fusion.py`, `confidence.py` or `risk_run.py` | regex check | `b/tests/test_phase13_claims.py:470-475` |
## 2. Evidence-only ML (not in the score, not shown)

Code: `b/app/analytics/atypicality.py`, run by `b/app/analytics/atypicality_run.py`, written to `atypicality_result` (`b/app/models/analytics.py:345-374`).

| | Robust Mahalanobis (B4a, primary) | Isolation Forest (B4b, comparator) |
| --- | --- | --- |
| Algorithm | sklearn `MinCovDet` (FastMCD) (`atypicality.py:52,185`) | sklearn `IsolationForest`; only `score_samples` is used, never `predict()` (`atypicality.py:37-41,219`) |
| Seed | 20260926 (`atypicality.py:90`) | 20260926, 200 trees (`atypicality.py:91`) |
| Features | Six: the seven in `FEATURES` minus `distinct_payee_count`, which made the covariance singular (`atypicality.py:57-65,82-89`) | All seven (`atypicality.py:57-65`) |
| Registry, run 44 | `model_version` id 6 (`docs/model_cards.md:11`) | id 7 (`docs/model_cards.md:25`) |
| Orthogonality to `cost_anomaly` | Spearman 0.288 (`docs/model_cards.md:18`) | Spearman 0.286 (`docs/model_cards.md:29`) |
| Agreement between the two | Spearman 0.890; top-1,000 overlap 17.1% (`docs/model_cards.md:29`) | |
| Reproducibility | Refit reproduces stored scores, maximum difference 0.0; condition number 6.0 (`docs/model_cards.md:17`) | Refit reproduces stored scores (`docs/model_cards.md:28`) |

**Coverage on run 44** (`docs/phase6_atypicality_report_run44.md:25-26`):
- **Lok Sabha:** 76,710 of 78,232 works evaluated (1,522 not evaluated).
- **Rajya Sabha:** 0 of 19,274 evaluated. No RS work has a recommendation date, and a work with any missing feature is "not evaluated", never scored 0 (`atypicality.py:31-35,177-178`).

**Guards:**
- A leakage guard refuses MP or payee identity and `risk_score` columns (`atypicality.py:66-117`).
- Nothing in `fusion.py` imports or references the layer. Tests: `b/tests/test_phase6_atypicality.py:151,168`; also `b/tests/test_ml_atypicality_robustness.py`, not yet run.

**How to read the outputs:**
- `percentile` is a work's rank among the evaluated works of the same run (`b/app/analytics/signals.py:81-94`). It moves when the population changes.
- Mahalanobis stores per-feature `contributions` that sum to the squared distance (`atypicality.py:188`; `test_phase6_atypicality.py:111`).
- **A single contribution can be negative.** Each term is `diff_i × (P·diff)_i`, and the off-diagonal precision terms can make it below zero. Read the contributions as a breakdown of one distance, not as independent per-feature scores.

**Not shown in the app:**
- `atypicality_result` is on the list of tables empty in production (`b/scripts/postdeploy_gate.py:49-52`).
- No endpoint reads it; data-health says "not yet served by the API" (`b/app/serving/service.py:1119-1120`).

**Reviewer usefulness has not been measured.** Audit sample #42 has 0 reviews (`docs/gate_g6_criteria.md:42`).

## 3. Inactive and closed (experimental)

Code: `b/app/analytics/survival.py`. Design: `docs/phase7_design_note.md`. Results: `docs/phase7_report.md`.

### A1: Cox completion-time model (inactive)

- **What it is:** lifelines `CoxPHFitter`, sanction date to completion. Open works are right-censored at the cutoff (`survival.py:97-117,169-178`).
- **Data:** 43,842 completions; 53,664 open works censored at 2026-08-30 (`docs/model_cards.md:38`).
- **Split:** out of time, 16,541 training works and 80,965 test works (`docs/model_cards.md:40`).
- **Performance:** out-of-time C-index 0.553, 95% CI 0.550–0.557. Chance is 0.5 (`docs/model_cards.md:41`). A work-type median baseline scores 0.560, better than the model (`docs/model_cards.md:42`).
- **Status:** recorded as `model_version` id 5 (run 1), `inactive_experiment` (`docs/model_cards.md:36`; `b/scripts/run_survival.py:120-124`). **It produces no stored outputs.**

### A2: 365-day delay early warning (closed, not shipped)

- **What it is:** `StandardScaler` + `LogisticRegression`, scored at landmark days 90 and 180 (`survival.py:247-250`; `docs/phase7_design_note.md:13`).
- **Labelled works:** only works with full 365-day follow-up, that is, sanctioned by 2025-08-30. There are 44,263: 25,498 completed within 365 days and 18,765 did not (`docs/phase7_design_note.md:34`).
- **Day 90, out of time:** Brier 0.2527 against a base-rate Brier of 0.2500. The difference CI is [+0.0010, +0.0042], **significantly worse than the base rate**. AUC 0.538 (`docs/model_cards.md:53`).
- **Day 180, out of time:** Brier 0.2352 against 0.2343. The difference CI [−0.0005, +0.0022] includes 0, so **no better than the base rate**. AUC 0.544 (`docs/model_cards.md:53`).
- **Baseline:** a work-type delay-rate baseline scores Brier 0.2479 at day 90, better than the model (`docs/model_cards.md:54`).
- **Reason for closing: cohort shift.** In the first validation run, AUC fell from 0.64–0.65 in-sample to 0.51 and 0.485 out of time (`docs/phase7_report.md:11`). On test works whose MP and authority never appear in training, AUC is 0.451 (day 90) and 0.444 (day 180) (`docs/model_cards.md:55`).
- **Decision:** closed, including a payment-only variant (`docs/phase7_report.md:9`). There is no `model_version` row.
- **Revisit** "once more cohorts clear 365 days of follow-up" (`docs/phase7_report.md:22`).

**`forecast_result` has 0 rows** (`docs/phase7_report.md:24`). Nothing writes it: `--register` is disabled (`b/scripts/run_survival.py:59-60`).

## 4. Gate G6: status

| Point | Status | Source |
| --- | --- | --- |
| Defined | Yes: criteria G6.1–G6.7 | `docs/gate_g6_criteria.md:19-25` |
| Met | **No**, by any layer, and not waived | `docs/gate_g6_criteria.md:4` |
| Thresholds | **Not set**; each is the owner's, in a pre-registration | `docs/gate_g6_criteria.md:33-36` |
| Evaluated | **No.** No pre-registration exists and no code evaluates G6. `b/app/analytics/gate.py` is the Phase 5 G3 gate (`gate.py:1-5`) | — |
| Per layer | B4 not eligible yet; A1 not eligible; A2 closed | `docs/gate_g6_criteria.md:42-44` |
| Protocol template | `docs/gate_g6_study_protocol_template.md` (blank thresholds) | — |

## 5. Provenance

**Present.** `model_version` (`b/app/models/analytics.py:314-339`) stores:
- `model_name`, `algorithm`;
- `feature_spec_hash`;
- `training_snapshot_id`;
- `seed`, `params`, `metrics`;
- `artifact_hash`;
- `status`, `status_note`;
- `created_at`.

`atypicality_result.model_version_id` is a NOT NULL foreign key (`analytics.py:369`). Completeness is tested in `b/tests/test_phase13z_ml_vendor.py:79-99`.

**Missing or weak:**

| Gap | Evidence |
| --- | --- |
| No library versions per row. They are pinned only in requirements | `b/requirements.txt:11,13` |
| No code version (git commit) | `analytics.py:314-339` has no such column |
| `feature_spec_hash` hashes the feature names plus a free-text transform description, not the transform code | `atypicality.py:120-127`; A1 likewise `run_survival.py:138-139` |
| The Isolation Forest `artifact_hash` hashes its output scores, not the fitted model | `atypicality.py:231` |
| No training-data fingerprint (counts only) | `atypicality.py:206-213,230` |
| No link to a validation run or report, and no G6 status field | B4 validation lives in `docs/ml_validation_run44.json` and the docs only |
| Run-1 B4 rows (ids 3, 4) still `active` while run 44 is published | `docs/ml_validation_run44.json:157-170` |
| A1 registered for run 1 only | `docs/model_cards.md:36` |
| `forecast_result` has no `horizon` column | `docs/phase7_report.md:100` |

## 6. Peers and House

- **Peer groups are House-neutral by design.** House is not a grouping key; it only filters what is displayed. Sources: `b/app/analytics/peers.py:17-18`; `SENTINEL_REBUILD_PLAN_v2.md:698-700`. Tests: `b/tests/test_phase3_context.py:144-147`, and `docs/phase4_signals_report.md:150-165` for the signals.
- **No comparison against House-specific (separate LS and RS) peer groups has been run.** Any such study would be a read-only side report. It must not change scoring.
- B4 is effectively LS-only, because no RS work is evaluated (`docs/phase6_atypicality_report_run44.md:26`).

## 7. Future work (not started; each needs an owner decision)

1. **Surface atypicality as evidence (option 1, read at request time).**
   - In `record_detail`, read `atypicality_result` for the published run, as `evidence_facts` is already read (`b/app/serving/service.py:738-748,797`).
   - The contract test checks keys as a subset, so an added key does not break it (`b/tests/test_contract.py:508,558`).
   - **Production must load `atypicality_result`.** It is on the empty list today (`b/scripts/postdeploy_gate.py:49-52`), and the run-44 manifest counts 390,024 rows across runs 1 and 44 (`ops/deploy/manifest_run44.json:30`).
2. **Surface atypicality as evidence (option 2, `served_work` columns).**
   - Add percentile and top-contribution columns to `served_work` (`b/app/models/serving.py:71-146`).
   - **Class A:** this is a schema migration plus a serving rebuild.
3. **Either option** needs:
   - a separate "evidence only, not part of the score" block on the record page;
   - the copy and claims test that today say "not yet shown" changed in the same commit as the feature (`b/tests/test_phase13_claims.py:410-422`).
4. **G13:** the `served_work` atypicality columns (as in item 2). **Class A.**
5. **G16:** a `forecast_result.horizon` column. **Class A.** Only needed if A1 or A2 is ever revived, which is not recommended now.
6. **Registry provenance fields** (section 5).
7. **A House-specific peer comparison** (section 6).
8. **B4 reviewer-usefulness study** under a G6 pre-registration.

## Figures deliberately left out

These figures are not stated in any repo doc, so they are left out:
- the run-44-only row count of `atypicality_result`;
- the share of the 44,263 A2 works that completed, as a percentage.

The counts that both would be computed from are cited above.

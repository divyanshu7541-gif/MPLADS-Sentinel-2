# Vendor and ML compliance audit (Phase 13.z)

**Written 2026-09-29, overnight unattended run.** Published run **44**, risk_result checksum **`c4d589e0e0fc40621d4e011a64a7233d`** (unchanged by this audit; see `docs/overnight_run_log.md`). Sources: `SENTINEL_REBUILD_PLAN_v2.md` (the plan) and `BLUEPRINT.md`. Evidence: code (file:line), database tables (queried 2026-09-29), tests and reports. Scope: vendor/payee/payment/agency data, and machine learning. The frontend and the Phase 14 deploy files are out of scope and untouched.

## Plain-language summary of the top findings

1. **Payments do feed the risk score, as the plan says.** The frontend's methodology text says they don't.
   - Each work's total paid amount enters `lifecycle_delay`, one of the six base signals, as "payment share ahead of completion vs peers". BLUEPRINT §8 asks for exactly this.
   - No payee or agency *identity*, type or metric reaches the score (code and tests).
   - The frontend's methodology text (`meth.lim.6`, English and Hindi) says payments are "evidence only, never in the risk score". That is wrong for payments. The frontend is out of scope tonight, so this is an owner fix.
2. **No ML model feeds the score, and none has cleared its accuracy gate.**
   - B4 atypicality (Mahalanobis + Isolation Forest) is live as evidence only.
   - A1 (completion time) is an inactive experiment: C-index 0.553 [0.550, 0.557].
   - A2 (365-day delay warning) was closed: at day 90 it is significantly *worse* than predicting the base rate.
   - All of this was recomputed on run 44, with confidence intervals, and matches the run-1 reports.
3. **Four plan artifacts were missing; now written:**
   - the gate-G6 criteria (`docs/gate_g6_criteria.md`, with thresholds left to the owner as the plan implies);
   - the consolidated Phase 8 report (`docs/phase8_ml_validation_report_retrospective.md`, clearly labelled retrospective);
   - model cards (`docs/model_cards.md`);
   - a drift report (`docs/drift_report_run1_vs_run44.md`, backed by `scripts/drift_report.py`).
4. **Group leakage exposure, now measured.**
   - The out-of-time split is time-disjoint (now tested), but most test works share an MP (64%) or district authority (81%) with training.
   - On unseen MPs and authorities, A2's discrimination falls below chance. That supports its closure.
5. **Reproducibility confirmed.** Refitting B4 from the snapshot with the recorded seed reproduces every stored score exactly.
6. **Still needs people** (owner):
   - reviewer usefulness for B4 (audit sample #42 has 0 reviews);
   - B1 duplicate labels (about 500 pairs);
   - payee and agency review (all 29,583 payees "unreviewed");
   - a weekly-retrain cadence (the worker is still a stub).
7. **No category-B (score-changing) fix was needed or made.** One reading of the plan could demand strict Level-1 peers for cost anomaly. That would un-evaluate 26,573 works and re-tier many more. BLUEPRINT's own peer hierarchy and its "broadens to the next level" answer support the current behaviour, so it is logged as an owner decision, not changed.

## Part 1. Requirements table (exact quotes)

### (a) Vendor, payee, payment and agency data

| ID | Source | Exact quote | Phase |
| --- | --- | --- | --- |
| V1 | BLUEPRINT §2 Sources | "Snapshot A: expenditure (LS / RS) \| 82,885 / 24,941 payments (107,826), payee ID, implementing agency, work key \| Core" | 1–2 |
| V2 | BLUEPRINT §4 Reference | "`payee` \| Portal vendor ID \| Canonical name, type (private firm, statutory or government body, manufacturer, individual), review status" | 2, 10 |
| V3 | BLUEPRINT §4 Reference | "`payee_alias` \| (payee, name) \| Names shared across IDs are kept, never merged silently" | 2 |
| V4 | BLUEPRINT §4 Reference | "`implementing_agency` \| IA name; type \| About 7,200 agencies; typed by rules and review" | 2 |
| V5 | BLUEPRINT §4 Core | "`payment` \| (snapshot, work, payee, agency, date, amount, occurrence_no) \| … identical rows keep an occurrence number, never dropped" | 2 |
| V6 | BLUEPRINT §5 P5 | "Payees by portal ID; typing rules; agency typing; … Unmatched names go to a review queue" | 2 |
| V7 | Plan Phase 2 | "P5 Entity resolution: payee dedup by portal ID (never auto-merge names across IDs — 1,045 names are shared by multiple IDs); agency typing rules" | 2 |
| V8 | BLUEPRINT §8 | "Entity analytics … appear as profiles and as context tags on works, and they never add to a work's risk score." | 10 |
| V9 | BLUEPRINT §8 | "every payee carries a type and review status before any concentration metric is shown" | 10 |
| V10 | BLUEPRINT §8 Metrics | "Payee concentration \| MP tenure, district authority \| Herfindahl index … compared with a permutation null that shuffles payees within district, work type and FY strata" | 10 |
| V11 | BLUEPRINT §8 Metrics | "Payee price position \| Payee, work type \| Median residual against level-1 peers with bootstrap interval and minimum work count \| Profile; never inherited by works" | 10 |
| V12 | BLUEPRINT §8 Metrics | "Payee reach \| Payee \| Works, MPs, districts, amount; typed peers only \| Descriptive" | 10 |
| V13 | BLUEPRINT §8 Metrics | "Repeated identical payments \| Work \| Identical (payee, amount, date) within a work … \| Evidence; not an accusation" | 10 |
| V14 | BLUEPRINT §8 Metrics | "Multi-payee works \| Work \| Four or more payees … \| Descriptive" | 10 |
| V15 | BLUEPRINT §8 Metrics | "Payment ahead of completion \| Work \| Paid share of sanction against peers … \| Lifecycle delay signal input" | 4, 10 |
| V16 | BLUEPRINT §8 Metrics | "District authority profile \| IDA \| Recommendation-to-sanction lag, sanction-to-completion, open backlog age, payment ageing, against other authorities in the state" | 10 |
| V17 | BLUEPRINT §8 Metrics | "Implementing agency profile \| Agency \| Completion lag and price position for agencies with enough works \| Descriptive with minimum n" | 10 |
| V18 | BLUEPRINT §8 Graph | "computed offline for exploration … It is not a graph database and not a scoring component" | 10 |
| V19 | BLUEPRINT §8 Wording | "Show the denominator, interval and peer definition beside every entity metric." / "Names of sole-proprietor payees receive the same care as any personal data" | 10, 13 |
| V20 | Plan Phase 10 | "entity_metric rows per BLUEPRINT.md §8: … repeated identical payments, multi-payee works, payment-ahead-of-completion, …" | 10 |
| V21 | Plan Phase 10 tests | "No-guilt-by-association test: risk_result's schema has no payee-derived column, ever." | 10 |
| V22 | Plan Phase 4 | "Lifecycle delay (10%): age-since-sanction vs peer completion distribution; payment share ahead of completion vs peers." | 4 |
| V23 | Plan §7.1 / Phase 6 | "Feature vector: log(amount), peer deviation ratio …, payment count, distinct payee count, description length. Hard assertion … that MP identity, payee identity, and risk_score never appear" | 6 |
| V24 | BLUEPRINT §14 | Cannot claim: "Vendor wrongdoing from concentration alone" | all |
| V25 | BLUEPRINT §7 rule 7 | "Model outputs never include MP or vendor rankings." | 6–8 |

### (b) Machine learning

| ID | Source | Exact quote | Phase |
| --- | --- | --- | --- |
| M1 | BLUEPRINT §7 | "There is no ML in the risk score at launch; ML outputs appear as evidence and forecasts until gate G6." | 6–8 |
| M2 | BLUEPRINT §7 A1 | "Survival (Kaplan-Meier, Cox or accelerated failure time) on sanction-to-completion time … \| Feeds the lifecycle delay signal and forecasts \| Out-of-time split; concordance index; calibration of predicted percentiles" | 7 |
| M3 | BLUEPRINT §7 A2 | "Probability a sanctioned work is not complete within 365 days, from features known at day 90 and 180 \| … \| Out-of-time split; Brier score; calibration curve" | 7 |
| M4 | BLUEPRINT §7 A3 / plan §7.2 | "Fund-flow forecast …" / "Explicitly deferred, built only if a specific dependency requires it" | — |
| M5 | BLUEPRINT §7 B1 | "Character n-gram TF-IDF with blocking … \| About 500 hand-labelled pairs; precision and recall by threshold" | 4 |
| M6 | BLUEPRINT §7 B2 / plan §7.2 | "Rules plus fuzzy matching … Hand-labelled sample of about 300 pairs" / "payee-resolution ML beyond Phase 2's rule-based typing" deferred | — |
| M7 | BLUEPRINT §7 B3 / plan §7.2 | "Expected-cost model" / deferred | — |
| M8 | BLUEPRINT §7 B4 | "Robust Mahalanobis distance on a small interpretable feature vector, Isolation Forest as comparator \| Evidence only, with per-feature contributions \| Ablation and reviewer usefulness" | 6 |
| M9 | BLUEPRINT §7 rule 1 | "Never use MP or payee identity as a feature in B3" | — |
| M10 | rule 2 | "Never use `risk_score` as a feature." | 6–7 |
| M11 | rule 3 | "A2 uses only information available at the prediction day, including payments to that day." | 7 |
| M12 | rule 4 | "Cross-validation groups by state or MP so templated works do not leak across folds." | 6–7 |
| M13 | rule 5 | "No model is trained on later periods to score earlier ones." | 7 |
| M14 | rule 6 | "The training data contains the anomalies; use robust or quantile losses." | 6 |
| M15 | §7 Governance | "Every model is registered in `model_version` with its algorithm, feature specification hash, training snapshot, metrics and artifact hash. Seeds are fixed." | 6–8 |
| M16 | §7 Governance | "Models retrain weekly in a separate job and are scored inside each run." / §5: "Model training: separate weekly job, never inside the scoring run." | 8 |
| M17 | §7 Governance | "Drift is watched through distribution shift on inputs and scores." | 8 |
| M18 | §7 Governance | "Each model has a one-page model card stating its purpose, data, limits and failure modes." | 8 |
| M19 | §7 G6 | "A layer may become a base signal only after a pre-registered study shows it adds information … does not simply mirror an existing signal, and is confirmed useful by reviewers." | 8 |
| M20 | §12 | "A layer is accepted only if it beats a simple baseline out of time, is calibrated, and (for evidence layers) is judged useful by reviewers. An anomaly layer must also be partly orthogonal to the rule engine" | 6–8 |
| M21 | §13 G4 | "**G4:** each beats its baseline out of time and is calibrated" | 7 |
| M22 | §5 P7 | "P7 ML scoring \| Apply registered models to the run: survival, early-warning, forecasts, text similarity \| Model version and calibration checks" | 6–7 |
| M23 | Plan §7.1 item 2 | "365-day delay early warning (A2) … day-90/day-180 checkpoints, … out-of-time train/validation split, Brier score + calibration against a seasonal-naive baseline." (Required, "no exceptions") | 7 |
| M24 | Plan §7.1 item 3 | "Survival/completion-time model (A1) is built only as supporting infrastructure for A2" | 7 |
| M25 | Plan §7.3 | "censoring correctly excludes not-yet-resolved works from training labels while still allowing them to receive a live A2 prediction" | 7 |
| M26 | Plan Phase 6 tests | "Leakage assertion test … Determinism test … Missing-data test … Orthogonality report (report, not pass/fail)" | 6 |
| M27 | Plan Phase 6 | "Isolation Forest (co-primary comparator): fixed seed; document the contamination default used and why" | 6 |
| M28 | Plan Phase 7 | "Out-of-time split: sort by sanction date, train earlier window, validate later window — never random" | 7 |
| M29 | Plan Phase 7 tests | "Leakage test …: fixture where a payment happens at sanction-date+120; assert it does NOT appear in the day-90 feature vector." / "Censoring test … test both halves of this rule explicitly" | 7 |
| M30 | Plan Phase 7 | "concordance index (A1), Brier score + calibration curve (A2), both against a seasonal-naive baseline (e.g., peer-group median completion time)" | 7 |
| M31 | Plan Phase 7 acceptance | "A1/A2 predictions stored for every eligible work, both Houses." | 7 |
| M32 | Plan Phase 8 | "One consolidated validation report artifact combining B4's ablation/orthogonality result, A1's concordance/calibration, A2's Brier score/calibration, all against their baselines." | 8 |
| M33 | Plan Phase 8 | "A markdown gate-G6 criteria document (not code): pre-registered study requirement, non-redundancy threshold vs existing base signals, reviewer face-validity requirement" | 8 |
| M34 | Plan Phase 8 tests | "Registry completeness test: every model has non-null algorithm, feature-spec hash, training snapshot reference, at least one recorded metric." / "risk_result's schema is byte-identical to Phase 5's output." | 8 |
| M35 | Plan Phase 8 | "A1/A2 predictions always shown with their confidence interval, never a bare number." | 8 |
| M36 | Plan §11 / Phase 5 | Gate items 1–8 (reachability, distributions, overlap, sensitivity, ablation, tier boundaries, missing signals, justification) and "CI fails if risk_result exists for a run but no corresponding gate-report artifact" | 5 |
| M37 | BLUEPRINT §14 | Cannot claim: "Any ML in the score before gate G6" | all |
| M38 | BLUEPRINT §12 | "Duplicate labels \| … About 500 hand-labelled pairs" / "Synthetic injection \| Detectors are sensitive" | 4 |

## Part 2. Traceability matrix

Status is after tonight's fixes; a `←` marks the status before them.

| ID | Status | Evidence (code / DB / test / report) |
| --- | --- | --- |
| V1 | IMPLEMENTED | `payment` 107,826 rows (snapshot_a); `docs/phase1_reconciliation_report.md`; `tests/test_phase1_ingest.py` |
| V2 | PARTIAL | `payee` 29,583: private_firm 5,364, statutory_or_government 5,849, manufacturer 953, individual 295, **unclassified 17,122**; **review_status = unreviewed for all 29,583**. `app/entities/payee_typing.py`; `test_phase10_entities.py::test_classify_payee_type_v2_*`. Human review: owner. |
| V3 | IMPLEMENTED | `payee_alias` 29,583; Phase 2 payee-alias test |
| V4 | PARTIAL | `implementing_agency` 7,203, `agency_type` statutory_or_government 4,449 / unclassified 2,754; **no review-status column**. Review: owner. |
| V5 | IMPLEMENTED | `payment.occurrence_no`; `app/entities/facts.py:32-48` reads it |
| V6 | PARTIAL | Typing rules exist; the "review queue" is the `review_status` field with no reviewer workflow or reviews |
| V7 | IMPLEMENTED | Phase 2 tests (no auto-merge across IDs) |
| V8 | IMPLEMENTED | Score-path files read no payee, agency or entity table: `test_phase13z_ml_vendor.py::test_no_payee_or_agency_identity_reaches_signals_fusion_or_confidence`, `test_phase10_entities.py::test_entity_metric_is_never_read_by_fusion_module` |
| V9 | PARTIAL | Type before concentration: `test_every_payee_has_a_type_before_any_concentration_row_exists`. Review status is carried but never "reviewed" (see V2). |
| V10 | IMPLEMENTED | `app/entities/metrics.py:143-290` (Herfindahl, permutation null); `test_a_naturally_concentrated_small_market_is_not_flagged_as_unusual`; `entity_metric` run 44: 58,480 rows |
| V11 | IMPLEMENTED | `metrics.py:300-360` price position; never read by fusion (V8) |
| V12 | IMPLEMENTED | `metrics.py:370-395` reach |
| V13 | IMPLEMENTED (form differs) | `work_evidence_fact` `identical_payment_repeated` (run 44: 407 eligible-evaluated; 571 total); `test_identical_payment_repeated_facts_have_real_repeats`. Stored as a work fact, not an `entity_metric` row (V20). |
| V14 | IMPLEMENTED (form differs) | fact `multi_payee_work` (1,356); `test_multi_payee_work_facts_meet_the_threshold` |
| V15 | IMPLEMENTED | `app/analytics/signals_run.py:48-51` (`SUM(amount) … GROUP BY work_key`); `signals.py:849-972` component B (`payment_share`); `test_phase13z_ml_vendor.py::test_payment_share_is_a_lifecycle_delay_input_only`, `test_the_only_vendor_table_read_on_the_score_path_is_payment_amount_per_work` |
| V16 | IMPLEMENTED | `metrics.py` district_authority_profile; `test_district_authority_profile_peer_definition_cites_the_resolved_state_not_the_stored_one` |
| V17 | IMPLEMENTED | `metrics.py:552-600` (minimum-n gated) |
| V18 | IMPLEMENTED | `app/entities/graph_build.py`; `test_graph_data_never_scans_base_tables_at_request_time`, `test_graph_is_bounded` |
| V19 | IMPLEMENTED | `test_every_entity_metric_row_has_non_null_wording_fields`; public graph withholds individual/unclassified names (`docs/security.md`) |
| V20 | DEVIATES (form, low) | repeated/multi-payee are `work_evidence_fact` rows and payment-ahead is signal evidence, not `entity_metric` rows. Same information, different table; the entity_metric enum is pinned to 5 metrics (`test_entity_metrics_enum_is_exactly_the_five_documented_metrics`). Documented here; no change (restructuring tables would touch the API and the serving build). |
| V21 | IMPLEMENTED | `test_phase10_entities.py::test_risk_result_schema_has_no_payee_derived_column` |
| V22 | IMPLEMENTED | as V15 |
| V23 | IMPLEMENTED (accepted change) | `app/analytics/atypicality.py:57-65` FEATURES; `distinct_payee_count` excluded from Mahalanobis only (owner decision, `docs/phase6_atypicality_report.md`); guard at `atypicality.py:67+` |
| V24 | IMPLEMENTED | `tests/test_phase13_claims.py` |
| V25 | IMPLEMENTED | no model output ranks MPs or vendors (atypicality_result is per work) |
| M1 | IMPLEMENTED | `fusion.py:44-51` BASE_SIGNALS only; `test_atypicality_never_enters_fusion`, `test_phase5_code_never_references_the_survival_layer` |
| M2 | NOT-APPLICABLE-BY-ACCEPTED-DECISION (placement) | A1 built and validated (`model_version` id 5, inactive). Owner decision 2026-09-27 (`docs/phase7_report.md` §1.3) keeps it out of lifecycle delay and forecasts. Metrics on run 44: C-index 0.553 [0.550, 0.557]. |
| M3 | IMPLEMENTED (validation) / not shipped by decision | `app/analytics/survival.py:234-265`; metrics on run 44 in `docs/ml_validation_run44.json` |
| M4 | NOT-APPLICABLE (plan §7.2 defers) | — |
| M5 | MISSING | no labelled pairs exist (needs reviewers: owner) |
| M6, M7 | NOT-APPLICABLE (plan §7.2 defers) | — |
| M8 | PARTIAL | B4 live (`model_version` 6, 7; `atypicality_result` run 44, 195,012 rows); ablation in `docs/phase6_atypicality_report_run44.md`; **reviewer usefulness MISSING** (owner) |
| M9 | NOT-APPLICABLE (B3 not built) | the guards in B4/A1/A2 refuse identity anyway |
| M10 | IMPLEMENTED | `test_injecting_identity_or_risk_score_raises`, `test_post_event_or_hindsight_fields_raise` |
| M11 | IMPLEMENTED | `survival.py:142-160`; `test_payments_after_the_landmark_day_are_never_counted` |
| M12 | PARTIAL (← MISSING evidence) | No cross-validation; one out-of-time holdout. **Group exposure now measured:** 414/711 test MPs and 519/763 test authorities appear in training (64% / 81% of test works). Unseen-group metrics in `docs/ml_validation_run44.json`. |
| M13 | IMPLEMENTED (← untested) | `test_phase13z_ml_vendor.py::test_out_of_time_split_is_disjoint_in_works_and_in_time` |
| M14 | IMPLEMENTED | `MinCovDet` (`atypicality.py:185`) |
| M15 | IMPLEMENTED (← test missing) | `model_version` 5 rows complete; `test_every_model_version_row_is_complete` |
| M16 | MISSING | `backend_v2/worker/main.py` is a heartbeat stub; there is no weekly job. B4 is scored per run by `scripts/run_atypicality.py`. Cadence = owner (one snapshot exists). |
| M17 | IMPLEMENTED (← MISSING) | `scripts/drift_report.py` → `docs/drift_report_run1_vs_run44.md` (PSI per input and score; no alert threshold, owner) |
| M18 | IMPLEMENTED (← MISSING) | `docs/model_cards.md` |
| M19 | IMPLEMENTED (not crossed) | G6 not met by any layer; `docs/gate_g6_criteria.md` |
| M20 | PARTIAL | A1/A2 fail "beats a simple baseline out of time" (not shipped); B4 partly orthogonal (0.288 / 0.286) but reviewer usefulness MISSING |
| M21 | IMPLEMENTED (gate applied: FAIL for A1/A2) | `docs/phase8_ml_validation_report_retrospective.md` §2 |
| M22 | PARTIAL | B4 applied to each run (`run_atypicality.py`); no "calibration check" for an unsupervised layer; survival/early-warning not applied (closed) |
| M23 | NOT-APPLICABLE-BY-ACCEPTED-DECISION | A2 closed by owner decision (`docs/phase7_report.md` §1.1) despite "required" |
| M24 | IMPLEMENTED | A1 not productized (no endpoint, no forecast rows) |
| M25 | PARTIAL (accepted) | Exclusion half built and tested (`test_a2_uses_only_full_follow_up_cohorts_and_the_at_risk_set`); live-prediction half not applicable after closure |
| M26 | IMPLEMENTED | `test_phase6_atypicality.py:70-134`; orthogonality in the Phase 6 reports |
| M27 | IMPLEMENTED | `atypicality.py:91` IF_PARAMS; `docs/phase6_atypicality_report.md` |
| M28 | IMPLEMENTED | `scripts/run_survival.py:185-186`; the new disjointness test |
| M29 | IMPLEMENTED / PARTIAL | Time-cut test uses days 31/89/91 (equivalent strength). Censoring: exclusion half only (M25). |
| M30 | IMPLEMENTED (← DEVIATES) | Phase 7 used Kaplan-Meier and the base rate. Work-type baselines added tonight: A1 C-index 0.553 against 0.560 for the work-type median (**below the baseline**); A2 Brier day 90 0.2527 against 0.2479 (below), day 180 0.2352 against 0.2374 (above the type baseline, not the base rate). |
| M31 | NOT-APPLICABLE-BY-ACCEPTED-DECISION | `forecast_result` 0 rows (owner decision, Phase 7) |
| M32 | IMPLEMENTED (← MISSING) | `docs/phase8_ml_validation_report_retrospective.md` (retrospective, dated) |
| M33 | IMPLEMENTED (← MISSING) | `docs/gate_g6_criteria.md` |
| M34 | IMPLEMENTED (← PARTIAL) | `test_every_model_version_row_is_complete`; `test_risk_result_byte_identical_across_the_ml_step` |
| M35 | NOT-APPLICABLE-BY-ACCEPTED-DECISION | no A1/A2 predictions are shown anywhere |
| M36 | IMPLEMENTED | `docs/phase5_gate_report_v3.md` (run 1), `_v4.md` (run 44); `test_phase5_risk_context.py::test_every_run_with_risk_results_is_covered_by_a_gate_report` |
| M37 | IMPLEMENTED | `tests/test_phase13_claims.py` |
| M38 | MISSING | no duplicate labels; no injection curves (validation report §10). Needs reviewers (labels); injection is outside this ML/vendor audit. |

### Row counts by status (63 rows: V1–V25, M1–M38)

| Status | After fixes | Before fixes |
| --- | ---: | ---: |
| IMPLEMENTED | 41 | 33 |
| PARTIAL | 10 | 12 |
| MISSING | 3 | 8 |
| DEVIATES | 1 | 2 |
| NOT-APPLICABLE-BY-ACCEPTED-DECISION | 4 | 4 |
| NOT-APPLICABLE (the plan defers it, or not built by plan) | 4 | 4 |
| **Total** | **63** | **63** |

**Counting rules:**
- Rows marked "form differs" or "accepted change" count as IMPLEMENTED (V13, V14, V23).
- NOT-APPLICABLE-BY-ACCEPTED-DECISION: M2, M23, M31, M35. NOT-APPLICABLE by plan: M4, M6, M7, M9.

**What changed tonight:**
- MISSING to IMPLEMENTED: M17, M18, M32, M33.
- MISSING to PARTIAL: M12 (exposure now measured).
- PARTIAL to IMPLEMENTED: M13, M15, M34 (tests added).
- DEVIATES to IMPLEMENTED: M30 (baseline added).

## Answers: vendor data

1. **Tables ingested and rows (queried 2026-09-29):**
   - `payment` 107,826 (all Snapshot A);
   - `payee` 29,583; `payee_alias` 29,583;
   - `implementing_agency` 7,203;
   - derived: `entity_metric` run 44 58,480 (run 1 57,161); `work_evidence_fact` 18,838 per run; graph tables per run.
2. **Vendor-derived values read by a signal, feature, model or score.** Every code path that reads these tables:

   | Path | Reads | Reaches `risk_score`? |
   | --- | --- | --- |
   | `app/analytics/signals_run.py:48-51` → `signals.py:884` lifecycle_delay component B | `SUM(payment.amount)` per work | **Yes**, the only vendor path to the score (BLUEPRINT §8 V15) |
   | `app/analytics/compliance.py:58` | payment sums and dates (C2, C3, C6) | No: the compliance panel. Its data-quality flags feed *confidence*, never risk. |
   | `app/analytics/atypicality_run.py:22-23` | payment count, distinct payees, max to one payee | No: B4 evidence plus fact `pays_same_payee_more_than_once` |
   | `app/analytics/survival.py` (A1/A2 via `scripts/run_survival.py`) | payment dates and amounts to day t | No: inactive or closed |
   | `app/entities/facts.py:32` | payments by (payee, amount, date) | No: evidence facts |
   | `app/entities/metrics.py:94-600` | payments, payees, agencies | No: entity_metric profiles |
   | `app/entities/graph_build.py:42-49` | payments, payee names | No: offline graph |
   | `app/entities/payee_typing.py:93` | payee names | No: typing |
   | `app/api/entities.py:68, 182` | payee, agency | No: API profiles (authenticated) |

3. **Where vendor data is used:**
   - the evidence chain (dossier facts: repeated identical payments, multi-payee, pays same payee more than once);
   - payee, agency and authority profiles (`/api/entities/*`, authenticated);
   - the offline graph (`/api/graph-data`; individual and unclassified payee names withheld publicly);
   - B4 features (counts only);
   - lifecycle delay (paid share);
   - the compliance panel.

   No export includes payee identity; the case export has case events only.
4. **Plan wording against behaviour:**
   - BLUEPRINT §8: "they never add to a work's risk score" (entity analytics). This matches: no entity metric or identity is read (V8).
   - BLUEPRINT §8: "Payment ahead of completion … Lifecycle delay signal input". This matches (V15).
   - The **product text** does not match: `frontend/src/i18n/locales/en.js:494` `meth.lim.6` says "Payments, payees and implementing agencies are included as evidence only, never in the risk score". Owner fix, frontend out of scope; proposed wording is in `docs/overnight_run_report.md`.

## Answers: ML audit

1. **Models in the run that produced risk_result (run 44):**
   - B4 robust Mahalanobis (`model_version` 6) and Isolation Forest (7): **reported only**, in `atypicality_result`, never in risk (M1).
   - A1 Cox (id 5): trained on run 1, inactive, no output.
   - A2: not registered.
   - **No model contributes to risk_result.** The six base signals are rule and statistical (TF-IDF cosine for near-duplicate is the explainable signal, not ML per plan §7.2).
2. **Labels:**
   - B4 is unsupervised and has no labels.
   - **A1:** event = observed completion (`work_state.actual_end_date` ≤ cutoff); duration = end − sanction, else cutoff − sanction (right-censored). Source: Snapshot A completed-works file via `work_state` (provenance: `raw_file` SHA-256). Balance (run 44): 43,842 events, 53,664 censored.
   - **A2:** label 1 = not complete within 365 days, only for cohorts with full follow-up (sanctioned ≤ 2025-08-30), at-risk at day t. Test prevalence 49.8% (day 90) and 62.6% (day 180).
   - **Label-to-feature leakage:** the guards refuse outcome fields (`actual_end_date`, `amount_used`, `lifecycle_status`, `paid_total`, event, duration, label) with tests (`test_post_event_or_hindsight_fields_raise`, `test_features_use_sanction_amount_never_the_actual_amount_used`).
3. **Leakage checks:**
   - *Target:* guarded as above.
   - *Temporal:* the split is by sanction date (`scripts/run_survival.py:185-186`, reproduced in `scripts/ml_validation_consolidated.py`), and A2 features are cut at day t. Tests: `test_out_of_time_split_is_disjoint_in_works_and_in_time` (new), `test_payments_after_the_landmark_day_are_never_counted`.
   - *Group:* **not group-disjoint.** 64% of test works share an MP with training, 81% an authority, and 99.6% a work type. On unseen MP-and-authority groups, A1 C-index is 0.582 and A2 AUC is 0.451 / 0.444, below chance.
   - Split code and overlap test: see Part 3.
4. **Features:**
   - **B4:** `log_amount` (work_context.amount_used), `log_peer_deviation_ratio` (amount / Phase 3 peer median), `days_rec_to_sanction` and `days_sanction_to_end_or_age` (work_state dates), `payment_count` and `distinct_payee_count` (payment), `description_length` (work.description_normalized; **the length only, not the text**).
   - **A1:** log sanction amount, is_rs, top-10 work-type dummies.
   - **A2:** A1's plus payments_by_t, paid_share_by_t, any_payment_by_t.
   - None duplicates a label. `days_sanction_to_end_or_age` in B4 uses the completion date, but B4 has no label to leak into.
5. **Metrics on the held-out set (run 44; the run-1 reports give identical point values):**
   - A1: C-index 0.553 [0.550, 0.557] against chance 0.5; work-type-median baseline **0.560** (the model is below it). **G4: FAIL.**
   - A2 day 90: Brier 0.2527 [0.2510, 0.2541] against base rate 0.2500 (difference CI [+0.0010, +0.0042], worse), work-type-rate baseline **0.2479** (beats the model); AUC 0.538 [0.531, 0.546]. **G4: FAIL.**
   - A2 day 180: Brier 0.2352 [0.2330, 0.2373] against 0.2343 (difference CI [−0.0005, +0.0022]), work-type-rate baseline 0.2374 (the model beats this, but not the base rate); AUC 0.544 [0.536, 0.553]. **G4: FAIL.**
   - Calibration deciles: `docs/phase7_report.md` §7 (same model and covariates).
   - Precision and recall **per tier**: not computable. The only outcome source for tiers is the audit sample, and #42 has 0 reviews (owner).
   - HIGH+CRITICAL gate K4: 32.6% against ≤ 20% (FAIL, accepted).
6. **Ablation and baseline comparisons:**
   - B4 feature ablation: done (run 44).
   - Risk-engine signal ablation and sensitivity: done (gate report v4, run 44).
   - A1/A2 against Kaplan-Meier and the base rate: done. Against a work-type baseline: done tonight.
   - The B1 duplicate-label baseline is missing (no labels).
7. **Reproducibility:**
   - Seeds: B4 20260926, A1/A2 20260926, bootstrap 20260929.
   - Versions: `backend_v2/requirements.txt` pins (scikit-learn 1.5.2, lifelines 0.29.0, pandas 2.2.3).
   - Data: `raw_file.sha256` per file; feature-spec hashes in `model_version`.
   - **Retraining B4 from the snapshot with the recorded seed reproduces the stored predictions exactly** (maximum difference 0.0, 76,710 works, both methods). Retraining A1/A2 reproduces the reported metrics to the reported precision.
8. **Gate G6 and the Phase 8 report:** both were confirmed missing, and both are now written. `docs/gate_g6_criteria.md` has the plan's three elements (pre-registration, non-redundancy, reviewer face validity), with thresholds left to the owner. `docs/phase8_ml_validation_report_retrospective.md` is dated 2026-09-29 and labelled retrospective.
9. **ML reports, current or stale:**

| Report | Computed on | State |
| --- | --- | --- |
| `docs/phase6_atypicality_report.md` | run 1 | historical; superseded by `phase6_atypicality_report_run44.md` (run 44) |
| `docs/phase7_report.md` | run 1 | point values re-verified on run 44 (identical); CIs and baselines added in the Phase 8 retrospective |
| `docs/phase5_gate_report_v3.md` | run 1 | historical; `phase5_gate_report_v4.md` is run 44 |
| `docs/phase4_signals_report.md`, `phase4_addendum.md` | run 1/2 of earlier databases | historical record of Phase 4/5a; run-44 signal distributions are in the drift report |
| `docs/phase10_11_report.md` (entity metrics) | run 1 | **stale**: entity_metric was rebuilt on run 44 (58,480 rows against 57,161); the report's per-metric counts are run 1. Flagged, not rewritten (historical record). |
| `docs/phase9_report.md` (map) | run 1 | map rebuilt on run 44; bug note updated in 13.y |
| `docs/phase12_report.md` | run 1 | flagged-count note updated in 13.y |
| `docs/validation_report_v1.md` | mixed; ML §4 updated to run 44 | current |
| `docs/performance_report.md` | run-1 rebuild | timing still valid; checksum note updated in 13.y |

## Part 3. How to verify it yourself

Run from the repository root with the scratch database up (`docker start sentinel-p5c-pg`). `DB=postgresql+psycopg://sentinel:sentinel@localhost:5447/sentinel`.

```bash
# 1. the split-overlap test (time-disjoint split) and the vendor/registry tests
MSYS_NO_PATHCONV=1 docker run --rm --network host -e DATABASE_URL=$DB -v "$PWD:/repo" sentinel-v2-dev \
  sh -c "cd /repo/backend_v2 && python -m pytest -q tests/test_phase13z_ml_vendor.py"

# 2. recompute every headline ML metric (with CIs, baselines, group overlap, B4 refit diff) and
#    compare with docs/ml_validation_run44.json / docs/phase8_ml_validation_report_retrospective.md
MSYS_NO_PATHCONV=1 docker run --rm --network host -e DATABASE_URL=$DB -v "$PWD:/repo" sentinel-v2-dev \
  sh -c "cd /repo/backend_v2 && python scripts/ml_validation_consolidated.py --out /tmp/check.json --boot 200 && cat /tmp/check.json"

# 3. retrain B4 from the snapshot with the recorded seed and diff the predictions: the same
#    command, section "b4" -> max_abs_score_diff must be 0.0 and eligibility_identical true
```

```sql
-- 4. which tables and columns feed risk_result (run inside: docker exec -it sentinel-p5c-pg psql -U sentinel -d sentinel)
-- risk_result is computed from signal_result (the six BASE signals) only; confirm the signal set:
SELECT signal, is_base, count(*) FROM signal_result WHERE run_id = 44 GROUP BY 1, 2 ORDER BY 1;
-- the lifecycle_delay payment input, per work (the only vendor-derived value on the score path):
SELECT work_key, evidence->>'payment_share' AS payment_share FROM signal_result
 WHERE run_id = 44 AND signal = 'lifecycle_delay' AND evidence ? 'payment_share' LIMIT 5;
-- nothing ML- or payee-derived is a risk_result column:
SELECT column_name FROM information_schema.columns WHERE table_name = 'risk_result' ORDER BY ordinal_position;
-- the checksum:
-- python -c "from app.analytics.atypicality_run import risk_result_checksum; ..." or the gate script:
```

```bash
# 5. the checksum and full post-deploy style gate on the database
MSYS_NO_PATHCONV=1 docker run --rm --network host -e DATABASE_URL=$DB -v "$PWD:/repo" sentinel-v2-dev \
  sh -c "cd /repo/backend_v2 && python scripts/postdeploy_gate.py --manifest /repo/ops/deploy/manifest_run44.json"
# 6. drift between runs
MSYS_NO_PATHCONV=1 docker run --rm --network host -e DATABASE_URL=$DB -v "$PWD:/repo" sentinel-v2-dev \
  sh -c "cd /repo/backend_v2 && python scripts/drift_report.py --ref 1 --cur 44 --out /tmp/drift.md && cat /tmp/drift.md"
```

The split code is `backend_v2/scripts/run_survival.py:185-186` (`train = ev.index[san[ev.index] < split]`, `test = … >= split`), reproduced in `scripts/ml_validation_consolidated.py::a1/a2`.

## Part 4. Deviations and fixes

| ID | Severity | Deviation | Path | Fix (done tonight unless marked) |
| --- | --- | --- | --- | --- |
| M33 | high | No gate-G6 criteria document | A | `docs/gate_g6_criteria.md` (thresholds = owner) |
| M32 | high | No consolidated Phase 8 report | A | `docs/phase8_ml_validation_report_retrospective.md` |
| Claim | high | Frontend says payments never enter the score | — | **needs me** (frontend out of scope); validation report §7 corrected |
| M17 | medium | No drift watching | A | `scripts/drift_report.py` + report (no alert threshold: owner) |
| M18 | medium | No model cards | A | `docs/model_cards.md` |
| M12 | medium | Group leakage exposure unmeasured | A | measured and reported; A2 stays closed, A1 inactive (no model changed) |
| M30 | medium | No seasonal-naive / peer baseline | A | work-type median baselines computed |
| M34, M15, M13 | medium | Missing registry-completeness and split-disjointness tests; vendor score path unpinned | A | `tests/test_phase13z_ml_vendor.py` (7 tests) |
| M8/M20 | medium | No B4 reviewer-usefulness evidence | — | **needs me** (reviewers) |
| M5/M38 | medium | No duplicate labels / injection curves | — | **needs me** (labellers); injection deferred |
| M16 | low | No weekly retrain job | — | **needs me** (cadence; one snapshot) |
| V2, V4, V6, V9 | low | Payees and agencies never reviewed | — | **needs me** (human review) |
| V20 | low | Repeated/multi-payee stored as facts, not entity_metric rows | A | documented only (a schema move would touch the API and serving; no information missing) |
| M22 | low | P7 "calibration checks" undefined for unsupervised B4 | A | documented; B4 reproducibility check added |
| Cost level (M8 context, BLUEPRINT §6 "against level-1 peers") | — | cost_anomaly uses the assigned level (L1 68,674; L2 11,166; L3 15,407) | **B if changed: not changed** | Judgment call: BLUEPRINT §6's hierarchy and §14 Q3 ("It broadens to the next level") support the current behaviour. Strict L1 would un-evaluate 26,573 works. Owner decision. |

**Category B fixes applied: none.** Checksum unchanged: `c4d589e0e0fc40621d4e011a64a7233d`.

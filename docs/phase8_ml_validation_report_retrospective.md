# Phase 8 ML validation report: retrospective compilation

> **Retrospective compilation, written 2026-09-29 (Phase 13.z).** Phase 8 was required to produce this report and the gate-G6 document, and did not. This is not a backdated Phase 8 artifact. It compiles the Phase 6 and Phase 7 results, **recomputed on the published run 44** (checksum `c4d589e0e0fc40621d4e011a64a7233d`), with the confidence intervals and split audit those reports lacked. Source data: `docs/ml_validation_run44.json`, produced by `backend_v2/scripts/ml_validation_consolidated.py` (read-only; the checksum is identical before and after).

## 1. What the plan asked for (Phase 8 prompt, quoted)

"One consolidated validation report artifact combining B4's ablation/orthogonality result, A1's concordance/calibration, A2's Brier score/calibration, all against their baselines." Also: "Finalized model_version rows for all three models (complete metadata)", explainability surfaces, and "A markdown gate-G6 criteria document". Gate G6 is now written in `docs/gate_g6_criteria.md`.

## 2. Summary

| Layer | Status | Headline (run 44, out of time) | Baseline | Gate G4 ("beats its baseline out of time and is calibrated") |
| --- | --- | --- | --- | --- |
| B4a robust Mahalanobis | active, evidence only | Spearman vs cost_anomaly 0.288 (partly orthogonal) | none; unsupervised | not applicable (no outcome); reviewer usefulness not yet measured |
| B4b Isolation Forest | active comparator | Spearman vs cost_anomaly 0.286; vs B4a 0.890 | none | as above |
| A1 Cox | inactive experiment | C-index 0.553 [0.550, 0.557] | chance 0.5 / Kaplan-Meier | **fails** (barely above chance) |
| A2 day 90 | closed | Brier 0.2527 [0.2510, 0.2541] | base rate 0.2500 | **fails**: significantly worse, difference CI [+0.0010, +0.0042] |
| A2 day 180 | closed | Brier 0.2352 [0.2330, 0.2373] | base rate 0.2343 | **fails**: no better, difference CI [−0.0005, +0.0022] |

95% intervals come from 200 bootstrap resamples of the test set (seed 20260929).

**Seasonal-naive / peer baselines** (plan Phase 7: "against a seasonal-naive baseline (e.g., peer-group median completion time)"), added 2026-09-29:
- **A1:** the work-type median completion time from training gives C-index **0.560**. Cox gives 0.553, **below this trivial baseline**.
- **A2 day 90:** each work type's training delay rate gives Brier **0.2479**, better than the model's 0.2527.
- **A2 day 180:** the type rate gives 0.2374. The model's 0.2352 beats it, but not the overall base rate (0.2343).

These strengthen the Phase 7 decisions: neither model beats a simple baseline out of time.

**Run 1 versus run 44.** The Phase 7 figures (`docs/phase7_report.md`) were computed on run 1. Recomputed on run 44 they are **identical to the reported precision**: A1 0.553; A2 0.2527/0.2500 and 0.2352/0.2343. A1 and A2 use only sanction-time covariates and payments, none of which the authority-state fix changed. The B4 figures were recomputed on run 44 in Phase 13.y (`docs/phase6_atypicality_report_run44.md`): 76,710 works evaluated against 76,732 on run 1, and orthogonality 0.288 against 0.280.

## 3. B4 multivariate atypicality

- **Orthogonality** (plan Phase 6 report item): Spearman with cost_anomaly 0.288 (Mahalanobis) and 0.286 (Isolation Forest). Both methods are partly independent of the rule engine, as BLUEPRINT §12 requires of an anomaly layer.
- **Ablation** (BLUEPRINT §7 B4 validation): each feature was removed in turn. The ranking leans most on `days_sanction_to_end_or_age` (top-1,000 overlap 49.9% without it) and `log_peer_deviation_ratio` (60.0%). Full table: `docs/phase6_atypicality_report_run44.md`.
- **Explainability:** per-feature contributions are stored on every evidence row, and they sum to the squared distance (tested).
- **Reproducibility:** refitting from the snapshot with the recorded seed reproduces every stored score exactly for both methods (maximum absolute difference 0.0 over 76,710 works; eligibility identical).
- **Reviewer usefulness** (BLUEPRINT §7 B4 validation, §12): **not measured.** It needs human reviewers; audit sample #42 has 0 reviews. **Needs owner.**

## 4. A1 survival (inactive experiment)

- Out of time: train on works sanctioned before 2025-03-01 (16,541), test from that date (80,965; 30,779 events).
- C-index 0.553 [0.550, 0.557].
- On test works whose MP and authority never appear in training (12,048): 0.582.
- Calibration at 365 days by decile, against Kaplan-Meier: `docs/phase7_report.md` §7 (run 1). The model and covariates are unchanged, so it applies to run 44.

## 5. A2 early warning (closed)

- The label is not complete within 365 days, only for cohorts with full follow-up; the at-risk set is works still open at day t.
- Features use only payments dated on or before day t (tested: `test_payments_after_the_landmark_day_are_never_counted`).
- Out-of-time results are in §2. Calibration deciles: `docs/phase7_report.md` §7.
- **On unseen groups AUC is below chance** (0.451 at day 90, 0.444 at day 180; about 2,600 test works each). A2 did not generalise to MPs and authorities it had not seen.

## 6. Split and leakage audit (BLUEPRINT §7 rules 1–7)

| Rule | Evidence | Status |
| --- | --- | --- |
| 1 No MP/payee identity in B3 | B3 not built; the B4/A1/A2 guards refuse identity columns | holds |
| 2 Never `risk_score` as a feature | leakage guards; `test_injecting_identity_or_risk_score_raises`, `test_post_event_or_hindsight_fields_raise` | holds |
| 3 A2 uses only information to the prediction day | `test_payments_after_the_landmark_day_are_never_counted` | holds |
| 4 Cross-validation grouped by state or MP | No cross-validation is used; a single out-of-time holdout is. MPs and authorities DO span the split: 414 of 711 test MPs and 519 of 763 test authorities appear in training, covering 64% and 81% of test works. The unseen-group metrics are reported above. | **partial**: time-disjoint (`test_out_of_time_split_is_disjoint_in_works_and_in_time`), not group-disjoint; exposure quantified |
| 5 No training on later periods to score earlier ones | train < 2025-03-01 ≤ test, and A2 labels end at cutoff − 365 (same test) | holds |
| 6 Robust losses | B4 uses a robust covariance (MCD); A1/A2 are not anomaly models | holds for B4 |
| 7 No MP or vendor rankings in outputs | no model output is a ranking of MPs or vendors | holds |

## 7. Registry completeness

All 5 `model_version` rows have an algorithm, feature-spec hash, training snapshot, seed, metrics and artifact hash (`test_every_model_version_row_is_complete`). A2 has no row because it was closed without shipping.

## 8. Not done, and why

| Item | Reason |
| --- | --- |
| Reviewer usefulness for B4 | Needs human reviewers (owner) |
| B1 duplicate precision/recall on about 500 labelled pairs | Needs labelled pairs (owner) |
| A1's survival curve as an A2 input | A2 closed (owner decision, Phase 7) |
| A3, B3, embeddings, B2 ML | Deferred by plan §7.2 |
| Weekly retraining job | Needs a new-snapshot cadence decision; there is only one snapshot (owner) |

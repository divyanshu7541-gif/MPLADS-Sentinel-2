# Model cards

Written 2026-09-29 (Phase 13.z). BLUEPRINT §7 Governance: "Each model has a one-page model card stating its purpose, data, limits and failure modes." Figures are for the **published run 44** (`docs/ml_validation_run44.json`, `scripts/ml_validation_consolidated.py`) unless marked otherwise. **No model on this page feeds `risk_score`** (gate G6, `docs/gate_g6_criteria.md`). Sentinel uses explainable statistical scoring with ML-based similarity and atypicality evidence: the only ML in the score is the TF-IDF text similarity inside the near-duplicate signal, which has no card here (`docs/ml_architecture.md`).

---

## B4a: Robust Mahalanobis atypicality (active, evidence only, not yet shown in the app)

| | |
| --- | --- |
| Registry | `model_version` id 6 (run 44); id 3 is the run-1 record. Algorithm: sklearn `MinCovDet` (FastMCD), seed 20260926, `support_fraction=None`. Feature-spec hash `59dbbc66929c…`. |
| Purpose | Show reviewers works whose *combination* of features is unusual (BLUEPRINT §7 B4), with a per-feature contribution. |
| Data | Snapshot A; one row per scored work of the run. Six features: `log_amount`, `log_peer_deviation_ratio`, `days_rec_to_sanction`, `days_sanction_to_end_or_age`, `payment_count`, `description_length`. |
| Vendor data | `payment_count` (payment rows per work). No payee or agency identity (the leakage guard refuses such columns). |
| Output | `atypicality_result` (method `robust_mahalanobis`): distance, percentile, contributions that sum to the squared distance. |
| Coverage | 76,710 Lok Sabha works evaluated; 0 Rajya Sabha works (no recommendation date, Hard Limit 6); a missing feature means "not evaluated", never 0. |
| Checks | Leakage guard (`test_injecting_identity_or_risk_score_raises`); determinism (`test_same_input_and_seed_give_identical_scores_for_both_methods`). Refit reproduces the stored scores exactly (maximum difference 0.0, run 44). Condition number 6.0. |
| Validity evidence | Spearman 0.288 with cost_anomaly (partly orthogonal). Ablation in `docs/phase6_atypicality_report_run44.md`. **No reviewer-usefulness evidence yet.** |
| Limits and failure modes | Unsupervised: "unusual" is not "wrong". Dominated by `days_sanction_to_end_or_age` and `log_peer_deviation_ratio` among the top 1,000. Rajya Sabha is invisible to it. `distinct_payee_count` is excluded because it made the covariance singular (condition number about 2e16). |

## B4b: Isolation Forest atypicality (active comparator, evidence only, not yet shown in the app)

| | |
| --- | --- |
| Registry | `model_version` id 7 (run 44); id 4 is the run-1 record. sklearn `IsolationForest`, 200 trees, seed 20260926, `contamination='auto'`. Only `score_samples` is used; the `predict()` flag is never used, so no flagged share is tuned. |
| Purpose | A comparator to B4a (plan §7.1). |
| Data | The same works, all seven features (B4a's six plus `distinct_payee_count`). |
| Checks | The same leakage and determinism tests. Refit reproduces the stored scores exactly (run 44). |
| Validity evidence | Spearman 0.286 with cost_anomaly. Agreement with B4a: Spearman 0.890, top-1,000 overlap 17.1% (run 44). |
| Limits | The same as B4a. The low top-1,000 overlap with B4a means the two methods rank the extremes differently, so neither ranking should be read alone. |

## A1: Cox completion-time model (inactive experiment)

| | |
| --- | --- |
| Registry | `model_version` id 5 (trained on run 1), status `inactive_experiment` (owner decision, Phase 7 review, 2026-09-27). `lifelines` `CoxPHFitter`, penalizer 0.01, seed 20260926. |
| Purpose | Time from sanction to completion (BLUEPRINT §7 A1); the plan's supporting infrastructure for A2. |
| Data | Every scored work; events are 43,842 completions, and 53,664 open works are right-censored at 2026-08-30. Covariates known at sanction: log sanction amount, House, the top-10 work types. |
| Vendor data | None. |
| Split | Out of time: train on works sanctioned before 2025-03-01 (16,541), test from that date (80,965). |
| Metrics (run 44) | Out-of-time C-index **0.553, 95% CI 0.550–0.557** (bootstrap, 200 replicates). Chance is 0.5. On test works whose MP and authority never appear in training (12,048): 0.582. |
| Why inactive | Too close to chance to be useful evidence (gate G4 not met). A work-type median completion time scores C-index 0.560, **better than the model**. |
| Limits | Test works have at most about 18 months of follow-up, so observed completions skew fast; the metrics are censoring-aware. Lok Sabha sanctions start in July 2024. |

## A2: 365-day delay early warning (closed, not shipped)

| | |
| --- | --- |
| Registry | None: closed without shipping (owner decision, Phase 7 review, 2026-09-27). |
| Target | 1 = not complete within 365 days of sanction. Scored at day 90 and day 180 on works still open then, with full 365-day follow-up. |
| Features | A1's covariates plus `payments_by_t`, `paid_share_by_t`, `any_payment_by_t`, from payments dated on or before sanction + t only. |
| Vendor data | Payment counts and amounts up to day t. No payee identity. |
| Metrics (run 44) | **Day 90:** Brier 0.2527 (CI 0.2510–0.2541) against a base-rate Brier of 0.2500. The Brier-minus-base CI is [+0.0010, +0.0042], **significantly worse than the base rate**. AUC 0.538 (0.531–0.546). **Day 180:** Brier 0.2352 (0.2330–0.2373) against 0.2343; the difference CI [−0.0005, +0.0022] includes 0, so no better than the base rate. AUC 0.544 (0.536–0.553). |
| Why closed | Out-of-time AUC collapsed, pointing to cohort shift. It fails gate G4 ("beats its baseline out of time and is calibrated"). A work-type delay-rate baseline scores Brier 0.2479 at day 90, better than the model. |
| Group leakage | On test works whose MP and authority never appear in training, AUC falls below 0.5 (0.451 at day 90, 0.444 at day 180). The small positive AUC overall owes something to seeing the same MPs and authorities in training. |

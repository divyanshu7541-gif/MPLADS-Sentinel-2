# Phase 7 design note: survival analysis and 365-day delay prediction

Written 2026-09-26, before any Phase 7 model code. Covers BLUEPRINT §7 layers A1 (completion-time survival) and A2 (365-day delay early warning). All facts below were measured read-only on the Phase 6 database (Snapshot A, analysis run 1, 97,506 scored works).

## 1. What is modelled

| | A1 completion-time survival | A2 365-day delay early warning |
| --- | --- | --- |
| Time origin | sanction date | sanction date |
| Event | completion (work appears in the completed file, with `actual_end_date`) | none: binary outcome |
| Duration | `actual_end_date - sanction_date` in days if completed; else `cutoff - sanction_date` | n/a |
| Outcome | time to completion, right-censored | 1 = not complete within 365 days of sanction, 0 = completed within 365 days |
| Prediction time | at sanction (covariates known at sanction) | landmark day t = 90 and t = 180 after sanction |
| Cutoff | Snapshot A `data_as_of` = 2026-08-30 | same |

The origin is sanction, not recommendation, for three reasons:
- BLUEPRINT A1 defines it as sanction-to-completion.
- Every scored work has a sanction date, while all 19,274 Rajya Sabha works lack a recommendation date (Hard Limit 6).
- The recommendation-to-sanction lag belongs to the district authority (BLUEPRINT §6).

## 2. Censoring: every case

| Case | Count (Snapshot A scope) | Handling |
| --- | --- | --- |
| Completed on or before the cutoff | 43,842 | Event observed at `actual_end_date - sanction_date`. |
| Open (sanctioned, not completed) at the cutoff | 53,664 | **Right-censored at the cutoff**: duration = `cutoff - sanction_date`, event = 0. Never dropped, never treated as "not delayed". |
| Completed after the cutoff | 0 | If it ever occurs, it is censored at the cutoff: the completion was not observable on the data date. A test pins this rule. |
| Sanction date missing | 0 | Not evaluated (no time origin). |
| Completed but end date missing | 0 | Not evaluated (event time unknown); not imputed. |
| Sanction date after the cutoff | 0 | Not evaluated. |
| Completed on the sanction day (duration 0) | 1,152 | Kept as an event at time 0. Kaplan-Meier and Cox handle t = 0; no model here needs t > 0. |
| Recommendation date missing (all RS, 361 LS) | 19,635 | Not a required field for A1 or A2, so these works are evaluated. See judgment call J2. |

**A2 labels need 365 days of follow-up.** The 365-day outcome is known only for works sanctioned on or before cutoff - 365 days (2025-08-30). That group has 44,263 works: 25,498 completed within 365 days and 18,765 did not. Works sanctioned later are excluded from A2 training and evaluation entirely, not only their open works. Keeping their fast completers while dropping their still-open works would label only the quick finishers of recent cohorts, the classic censoring bias. Those works can still be *scored*; they simply have no label.

**A2 at-risk set at landmark t.** A work that completed before day t cannot be predicted at day t. It leaves the landmark population; it is not counted as a negative.

## 3. Leakage boundary (what is known at prediction time)

A1 covariates are known at sanction. A2 features are known at day t. Enforced by an allow-list: any other column raises `LeakageError`, and tests inject each forbidden field.

Allowed:
- **A1:** log sanction amount; House; work type (top types, others pooled); sanction calendar quarter.
- **A2:** the A1 covariates, plus, from payments dated on or before sanction + t only:
  - number of payments;
  - share of the sanction amount paid;
  - whether any payment was made yet.

Forbidden, because each is known only after the event or after the prediction day:

| Field | Why |
| --- | --- |
| `actual_end_date`, `actual_amount`, `lifecycle_status` | Completion itself. |
| Phase 3 `amount_used`, `peer_median`, and cost ratios built on them | `amount_used` is the ACTUAL amount for completed works. |
| `paid_total` and any payment after sanction + t | Future payments. 9,195 payment rows even post-date their work's completion. |
| lifecycle_delay, cost and all Phase 4 signal scores; risk, tier, confidence; compliance (C5 etc.); atypicality scores; `work_evidence_fact` | All computed at the cutoff with full hindsight. |
| MP or payee identity | BLUEPRINT §7 rules 1 and 7. |

## 4. Validation (survival-appropriate)

- **Out-of-time split** (BLUEPRINT §7 rule 5: never train on later periods to score earlier ones). Train on works sanctioned before a split date and evaluate on later ones. Test outcomes stay censored at the cutoff.
- **A1:**
  - Harrell's C-index on the test cohort.
  - Calibration at 365 days: the mean predicted S(365) per risk decile vs the Kaplan-Meier estimate of S(365) in that decile (KM respects censoring).
  - **Observed vs censored subsets, reported separately:**
    - Observed: C-index among observed events, and median absolute error of the predicted median vs actual duration.
    - Censored: the share of open works the model already expected to have finished (predicted median < current age), and their mean predicted S(age).
  - Kaplan-Meier baseline curve alongside.
- **A2:** Brier score on the out-of-time test set, with calibration in deciles; AUC for reference only.
- **Grouping:** any cross-validation is grouped by state (rule 4).

## 5. Placement

Evidence only. Nothing enters risk, the active-signal count or corroboration. BLUEPRINT §7 says there is no ML in the risk score until gate G6; `SENTINEL_REBUILD_PLAN_v2.md` was later found outside the project folder and is now copied into the repository root. Its §7.3 confirms this placement (J7). Outputs go to a new table, never `signal_result`. The risk_result checksum is taken before and after the migration and the run. *(Note added 2026-10-02: since the owner-approved framing of 2026-10-01, the TF-IDF similarity inside Near-Duplicate is the one ML input to the risk score (BLUEPRINT.md:359; plan §7.2-§7.3). The placement of this model, evidence only, is unchanged.)*

## 6. Judgment calls left open for review

- **J1. Split date for the out-of-time test.** One split, 2025-03-01, is used for both models. A1 trains on earlier sanctions and tests on everything from 2025-03-01, censored at the cutoff. A2 trains on earlier full-follow-up cohorts and tests on 2025-03-01 to 2025-08-30. (An earlier draft proposed 2025-07-01 for A1; one shared split was used instead so both models are tested on the same period.)
- **J1b. How much of the training works' future may training use.** Two variants:
  - *Full follow-up:* training works' outcomes are observed up to the cutoff, so they include events after the split date.
  - *Strict:* training works are censored at the split date, as if the model had been built on 2025-03-01.
  A1 is run both ways. A2 can only use full follow-up: under the strict variant, only the 2023 Rajya Sabha cohort would have a known 365-day outcome by the split. Full follow-up uses post-split information about training works, but never test outcomes.
- **J2. Recommendation lag as a feature.** Left out: it would make every RS work not evaluated (Hard Limit 6), and it belongs to the district authority. Adding it is a choice.
- **J3. A completion dated after the cutoff** (0 today) is censored at the cutoff rather than dropped.
- **J4. Covariates.** A small set known at sanction; state is used for grouping, not as a covariate. The choice of work types to keep separate is a detail.
- **J5. Cohort shift.** The 2023 cohort is Rajya Sabha only (older RS tenures); Lok Sabha sanctions start July 2024. Training and test cohorts differ in House mix.
- **J6. Snapshot B's later completions** (278 more) are not used as labels. They could serve as a separate forward check.
- **J7. Evidence-only placement** assumed from BLUEPRINT §7, because the plan file is missing.
- **J8 (DECIDED 2026-09-27: dropped). Sanction calendar quarter as a covariate** (added after the first validation run). With under two years of sanctions, the quarter mostly encodes WHICH cohort a work belongs to, not seasonality, so it may not carry over to a new period. It was kept as designed; removing it is a choice.
- **J9 (DECIDED 2026-09-27: A2 not shipped; A1 kept as an inactive experiment; see phase7_report.md). What to do with A2.** Out of time it does no better than always predicting the base rate. Options: drop the cohort-bound covariates (J8), use payment-only features, wait for longer history, or do not ship A2.

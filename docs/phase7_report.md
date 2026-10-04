# Phase 7 Report -- Survival (A1) and 365-day delay early warning (A2)

**Outcome: neither model is shipped.** A2 is closed without shipping. A1 is kept only as an inactive, recorded experiment. No Phase 7 output is live evidence and nothing enters risk. The risk_result checksum `97f08303369f9ed6c50e46d68a4609f5` is unchanged across both Phase 7 migrations, every validation run, and the regression suite.

Design note (written before any model code): [phase7_design_note.md](phase7_design_note.md). The plan is `SENTINEL_REBUILD_PLAN_v2.md`, Phase 7 prompt and §7; section 6 below lists where this build differs from it.

## 1. Decisions (owner, Phase 7 review, 2026-09-27)

1. **A2 is not shipped in any form, including a payment-only reduced version.**
   - As first validated, it did worse than always predicting the base rate at day 180 (Brier 0.2903 vs 0.2343).
   - Out-of-time AUC collapsed from 0.64-0.65 in-sample to 0.51 and 0.485.
   - That points to cohort shift between training and test, not something tuning can fix.
   - Re-run without calendar quarter (decision 2), it is no longer inverted but still no better than the base rate: Brier 0.2527 vs 0.2500 at day 90, 0.2352 vs 0.2343 at day 180.
   - Closed, not shipped. No model_version or forecast_result entry.
2. **Calendar quarter of sanction is dropped as a covariate** for any future A1/A2 variant.
   - With under two years of sanctions it encoded which cohort a work belongs to, not seasonality.
   - It is now also refused by the leakage guard.
   - The results in section 5 are from the model without it.
3. **A1 is not registered as live evidence.**
   - Out-of-time C-index is 0.549-0.555 across both variants, with and without quarter: too close to the no-skill line of 0.5.
   - It is kept in `model_version` (id 5, `a1_cox_completion_time`) with status `inactive_experiment`, its coefficients and its validation metrics. No forecast_result rows are written (table renamed from survival_result at close-out).
   - It can be revisited once more cohorts clear 365 days of follow-up. Today only 44,263 works (sanctioned by 2025-08-30) have a known 365-day outcome, and Lok Sabha sanctions only start in July 2024.

End state: `forecast_result` (formerly `survival_result`) has 0 rows. `model_version` has no active Phase 7 entry, only the inactive A1 experiment. The Phase 6 atypicality models stay active. Tests pin all of this.

## 2. Event and censoring design

| | A1 | A2 |
| --- | --- | --- |
| Time origin | sanction date | sanction date |
| Event / outcome | completion | 1 = not complete within 365 days |
| Censoring | open works right-censored at the cutoff (2026-08-30) | labels only for cohorts with full 365-day follow-up (sanctioned by 2025-08-30) |
| Prediction time | at sanction | landmark day 90 and day 180 |

- **Observed completions:** 43,842, each an event at completion minus sanction.
- **Open works:** 53,664, **right-censored at the cutoff**. They are never dropped and never treated as "not delayed". Test: `test_open_work_is_right_censored_at_the_cutoff_not_dropped_or_an_event`.
- **Why censoring matters (test):** keeping censored works gives Kaplan-Meier S(365) = 0.5 where dropping them would give 0.0.
- **Completion dated after the cutoff:** censored at the cutoff (0 cases).
- **Missing sanction date, completed without an end date, end before sanction, sanction after the cutoff:** "not evaluated", never imputed (0 cases each).
- **Same-day completions (1,152):** kept as events at day 0.
- **A2 recent cohorts:** excluded entirely, both fast finishers and open works, so labels are not biased toward quick completions.
- **A2 at-risk set:** works already complete by day t leave the landmark set rather than counting as negatives.

**Rajya Sabha and missing data.** Every RS work has sanction and completion dates, so RS was evaluated here: all 97,506 scored works, 0 not evaluated. The recommendation date, which RS lacks under Hard Limit 6, is not an input. Using it would have made all RS works not evaluated under the existing missing-data rule, and BLUEPRINT §6 assigns that lag to the district authority.

## 3. Leakage checks performed

- **Allow-lists.**
  - A1: known at sanction only (log sanction amount, House, top-10 work type).
  - A2: those plus three payment features computed only from payments dated on or before sanction + t.
  - Anything else raises `LeakageError`.
- **Forbidden fields, injected in tests for both models:**
  - `actual_end_date`, `actual_amount`, `lifecycle_status`;
  - `amount_used`, `peer_median`, `paid_total`;
  - event, duration, label;
  - cost_anomaly, lifecycle_delay, risk, tier, confidence, C5;
  - atypicality, `work_evidence_fact`;
  - MP and payee identity;
  - and now calendar quarter.
- **The `amount_used` catch.** Phase 3's `amount_used` is the *actual* amount for completed works, known only after completion. Feeding it to a completion model would have leaked the outcome. The models use the sanction amount; `test_features_use_sanction_amount_never_the_actual_amount_used` pins this.
- **Time cut.** Payments dated after sanction + t are never counted; 9,195 payment rows even post-date their work's completion. The test places payments at days 31, 89 and 91 and checks that only the first two count at day 90.
- **Placement.** Evidence only, per plan §7.3 ("ML never enters `risk_score` without a pre-registered study clearing gate G6"). No Phase 5 code imports this layer (test). *(Note added 2026-10-02: plan §7.3 now reads "no ML layer (B4, A1, A2 or any deferred item) enters `risk_score` without a pre-registered study clearing gate G6, and the TF-IDF similarity inside Near-Duplicate (§7.2) is the only ML in the score". The placement of this model, evidence only, is unchanged.)*

## 4. How to read the results

- **C-index (A1):** the share of comparable pairs of works where the one predicted to finish sooner did finish sooner. 0.5 is chance, 1.0 perfect.
- **Brier score (A2):** mean squared error of the predicted probability of a delay, lower is better. The benchmark is always predicting the observed base rate.
- **Calibration:** whether predicted probabilities match observed rates, decile by decile. For A1 the observed side is the Kaplan-Meier estimate, which handles censoring.
- **Observed vs censored:** test works have at most about 18 months of follow-up, so the completions seen so far are mostly the fast ones. Metrics computed on completions alone are therefore biased toward short durations. The headline numbers use censoring-aware estimates instead.

## 5. Results (final run: calendar quarter dropped)

| Model | Out-of-time metric | Benchmark | Verdict |
| --- | --- | --- | --- |
| A1 Cox, training outcomes to the cutoff | C-index 0.553 (LS 0.554, RS 0.570) | 0.5 = chance | barely above chance |
| A1 Cox, strict (training censored at the split) | C-index 0.555 | 0.5 = chance | barely above chance |
| A2 day 90 | Brier 0.2527, AUC 0.538 | base-rate Brier 0.2500 | no better than the base rate |
| A2 day 180 | Brier 0.2352, AUC 0.544 | base-rate Brier 0.2343 | no better than the base rate |

Kaplan-Meier baseline: 46.4% of works are still open 365 days after sanction; median 326 days to completion, similar in both Houses. Full tables, including hazard ratios, calibration by decile and the observed-vs-censored comparison, are in section 7.

First validation run, with calendar quarter, for reference:
- A1 test C-index 0.549 (strict variant 0.547).
- A2 day 90: Brier 0.2625 vs 0.2500, AUC 0.510.
- A2 day 180: Brier 0.2903 vs 0.2343, AUC 0.485.
- Calibration was inverted at day 180: predictions of 0.93-0.96 against observed rates near 0.6.

## 6. Plan deviations

Checked against `SENTINEL_REBUILD_PLAN_v2.md` (Phase 7 prompt and §7), now kept in the repository root. It was found after the build, outside the project folder in `D:\Downloads`.

| Plan item | What was done | Reason |
| --- | --- | --- |
| A1's predicted survival curve as an A2 input | Not built | A2 was not shipped (decision 1). With A1's out-of-time C-index of about 0.55, it was also unlikely to rescue A2. That was not tested. |
| A recent work (under 365 days of follow-up) is excluded from training labels but still gets a live A2 prediction | Only the exclusion half built and tested | A2 was not shipped, so no live predictions exist. |
| A1/A2 predictions stored for every eligible work, both Houses | Not done; `forecast_result` holds 0 rows | A2 was not shipped (decision 1), and A1 is recorded only as an inactive experiment (decision 3). |
| "Stage transitions to date" as A2 inputs | Excluded | The stage field is stale (BLUEPRINT §2: 87% of completed works still read "Physical Inspection"), so it describes the portal's bookkeeping, not the work. |
| Seasonal-naive baseline, e.g. the peer-group median completion time | For A1, the Kaplan-Meier curve served as the baseline instead | Kaplan-Meier is the standard no-covariate reference for survival and handles censoring. A peer-group median would ignore the 53,664 open works. For A2, the benchmark is always predicting the base rate. |

**Resolved at close-out.** The results table was created as `survival_result` and has been renamed `forecast_result` to match the plan and BLUEPRINT §4 (rename-only migration; it holds 0 rows). The plan's key also names a `horizon` column. The current table encodes the horizon in the model name (`a2_day90`, `a2_day180`), and adding the column is left for whichever phase first writes rows.

## 7. Validation tables (generated by `scripts/run_survival.py --record-inactive`)

analysis_run 1; cutoff 2026-08-30; out-of-time split 2025-03-01; risk_result md5 `97f08303369f9ed6c50e46d68a4609f5` before and after (identical); only the inactive A1 model_version row written.

### 7.1 Event and censoring counts

| house | works | evaluated | events (completed) | right-censored at cutoff | completed after cutoff (censored) | not evaluated |
| --- | --- | --- | --- | --- | --- | --- |
| LS | 78,232 | 78,232 | 33,955 | 44,277 | 0 | 0 |
| RS | 19,274 | 19,274 | 9,887 | 9,387 | 0 | 0 |
| all | 97,506 | 97,506 | 43,842 | 53,664 | 0 | 0 |

Not-evaluated reasons: none

### 7.2 Kaplan-Meier baseline (all evaluated works)

| house | n | S(90) | S(180) | S(365) | median days | max follow-up days |
| --- | --- | --- | --- | --- | --- | --- |
| LS | 78,232 | 0.857 | 0.709 | 0.465 | 326.000 | 738.000 |
| RS | 19,274 | 0.841 | 0.695 | 0.460 | 321.000 | 1115.000 |
| all | 97,506 | 0.853 | 0.707 | 0.464 | 326.000 | 1115.000 |

S(t) = share of works NOT yet completed t days after sanction, with open works censored.

### 7.3 A1 Cox model: out-of-time

Train: works sanctioned before 2025-03-01 (16,541; 13,063 events). Test: sanctioned on/after it (80,965; 30,779 events, rest censored at cutoff). Covariates known at sanction only: log_sanction_amount, is_rs, type_32, type_62, type_113, type_17, type_85, type_59, type_58, type_16, type_34, type_6.

| variant | test C-index | train C-index | max fitted time (days) | dropped constant | test C-index LS | test C-index RS |
| --- | --- | --- | --- | --- | --- | --- |
| full follow-up (train outcomes to cutoff) | 0.553 | 0.555 | 1115.000 | none | 0.554 | 0.570 |
| strict (train censored at split date) | 0.555 | 0.541 | 568.000 | none | 0.548 | 0.566 |

C-index: share of comparable pairs where the work the model says completes sooner did complete sooner; 0.5 = chance.

Hazard ratios (full follow-up variant; > 1 = completes sooner):

| covariate | hazard ratio | p |
| --- | --- | --- |
| log_sanction_amount | 1.059 | 0.000 |
| is_rs | 0.894 | 0.000 |
| type_32 | 1.196 | 0.000 |
| type_62 | 1.134 | 0.000 |
| type_113 | 1.188 | 0.000 |
| type_17 | 0.596 | 0.000 |
| type_85 | 1.269 | 0.000 |
| type_59 | 1.196 | 0.002 |
| type_58 | 1.297 | 0.000 |
| type_16 | 0.825 | 0.003 |
| type_34 | 0.582 | 0.000 |
| type_6 | 1.042 | 0.550 |

#### Calibration at 365 days -- full follow-up (train outcomes to cutoff)

Mean predicted S(365) vs Kaplan-Meier S(365) per decile of predicted risk (test works). Deciles with nobody followed past 365 days get no KM value.

| decile | n | at_risk_365 | mean_pred_S365 | KM_S365 |
| --- | --- | --- | --- | --- |
| 1 | 8,097 | 1,117 | 0.361 | 0.414 |
| 2 | 8,096 | 677 | 0.377 | 0.321 |
| 3 | 8,097 | 1,149 | 0.389 | 0.435 |
| 4 | 8,096 | 1,023 | 0.401 | 0.443 |
| 5 | 8,097 | 906 | 0.414 | 0.332 |
| 6 | 8,096 | 989 | 0.429 | 0.443 |
| 7 | 8,096 | 1,760 | 0.442 | 0.623 |
| 8 | 8,097 | 1,059 | 0.457 | 0.470 |
| 9 | 8,096 | 1,311 | 0.499 | 0.588 |
| 10 | 8,097 | 1,570 | 0.617 | 0.706 |

#### Calibration at 365 days -- strict (train censored at split date)

Mean predicted S(365) vs Kaplan-Meier S(365) per decile of predicted risk (test works). Deciles with nobody followed past 365 days get no KM value.

| decile | n | at_risk_365 | mean_pred_S365 | KM_S365 |
| --- | --- | --- | --- | --- |
| 1 | 8,097 | 861 | 0.365 | 0.383 |
| 2 | 8,096 | 856 | 0.445 | 0.345 |
| 3 | 8,097 | 1,540 | 0.470 | 0.559 |
| 4 | 8,096 | 970 | 0.489 | 0.440 |
| 5 | 8,097 | 1,113 | 0.506 | 0.421 |
| 6 | 8,096 | 1,145 | 0.520 | 0.462 |
| 7 | 8,096 | 1,001 | 0.535 | 0.415 |
| 8 | 8,097 | 1,122 | 0.557 | 0.480 |
| 9 | 8,096 | 1,431 | 0.643 | 0.636 |
| 10 | 8,097 | 1,522 | 0.747 | 0.625 |

#### Observed vs censored test works (full follow-up variant)

| subset | n | C-index within subset | median |pred median - actual| days | share model says 'should be done by now' | mean S(own time) |
| --- | --- | --- | --- | --- | --- |
| observed (completed) | 30,779 | 0.492 | 174.000 | nan | 0.751 |
| censored (still open) | 50,186 | nan | nan | 0.259 | 0.663 |

'mean S(own time)': predicted probability a work like this is still open at its observed completion day (observed) or at its current age (censored).

### 7.4 A2 365-day delay early warning (landmark, out-of-time)

Labelled population: works sanctioned on or before 2025-08-30 (full 365-day follow-up), still open at day t. Train sanctioned before 2025-03-01, test from 2025-03-01 to 2025-08-30. Label 1 = not complete within 365 days.

#### Day 90

Train 14,238 (prevalence 50.7%); test 23,195 (prevalence 49.8%); not evaluable (missing feature) 0.
Test Brier 0.2527 vs 0.2500 for always predicting the test base rate; AUC 0.538 (reference only).

| decile | n | mean_pred | observed |
| --- | --- | --- | --- |
| 1 | 2,320 | 0.305 | 0.369 |
| 2 | 2,319 | 0.424 | 0.476 |
| 3 | 2,320 | 0.468 | 0.492 |
| 4 | 2,319 | 0.493 | 0.582 |
| 5 | 2,320 | 0.516 | 0.527 |
| 6 | 2,319 | 0.536 | 0.554 |
| 7 | 2,319 | 0.557 | 0.384 |
| 8 | 2,320 | 0.595 | 0.446 |
| 9 | 2,319 | 0.643 | 0.531 |
| 10 | 2,320 | 0.686 | 0.618 |

#### Day 180

Train 11,608 (prevalence 62.1%); test 18,471 (prevalence 62.5%); not evaluable (missing feature) 0.
Test Brier 0.2352 vs 0.2343 for always predicting the test base rate; AUC 0.544 (reference only).

| decile | n | mean_pred | observed |
| --- | --- | --- | --- |
| 1 | 1,848 | 0.421 | 0.537 |
| 2 | 1,847 | 0.515 | 0.498 |
| 3 | 1,847 | 0.590 | 0.599 |
| 4 | 1,847 | 0.625 | 0.686 |
| 5 | 1,847 | 0.637 | 0.729 |
| 6 | 1,847 | 0.648 | 0.650 |
| 7 | 1,847 | 0.666 | 0.585 |
| 8 | 1,847 | 0.709 | 0.616 |
| 9 | 1,847 | 0.735 | 0.783 |
| 10 | 1,847 | 0.772 | 0.571 |

### Registry

A1 recorded in model_version id 5 as `inactive_experiment`: Completed but INACTIVE experiment (owner decision, Phase 7 review, 2026-09-27): out-of-time C-index 0.553 is too close to the no-skill line (0.5) to persist as evidence. No forecast_result rows are stored. Revisit once more sanction cohorts clear 365 days of follow-up (docs/phase7_report.md).


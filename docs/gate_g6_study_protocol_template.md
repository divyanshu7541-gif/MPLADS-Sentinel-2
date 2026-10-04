# Gate G6 study protocol: TEMPLATE

> **This is a blank template, not a protocol.** No study has been registered, no threshold has been set, and gate G6 is not met by any layer (`docs/gate_g6_criteria.md:4`). Every `____` is the owner's to fill in. Thresholds are deliberately not suggested here (`docs/gate_g6_criteria.md:27-36`).

**How to use this template:**
1. Copy it to `docs/g6_protocol_<layer>_<yyyy-mm-dd>.md`.
2. Fill in every blank.
3. Commit it **before** any result is computed. G6.1 requires the protocol's commit to come before every result file it cites (`docs/gate_g6_criteria.md:19`).
4. Do not edit it after the first result exists. Any change becomes a new, dated protocol.

Passing G6 does not change the score by itself. A layer that passes enters only as a **new** fusion configuration, followed by an owner decision (G6.7, `docs/gate_g6_criteria.md:25`).

---

## 0. Registration

| Field | Value |
| --- | --- |
| Protocol id | ____ |
| Date registered | ____ |
| Registering commit (filled in after commit) | ____ |
| Owner / approver | ____ |
| Analyst | ____ |

## 1. Candidate layer (G6.1)

| Field | Value |
| --- | --- |
| Layer (e.g. B4a robust Mahalanobis) | ____ |
| `model_version.id` | ____ |
| `model_name` | ____ |
| `feature_spec_hash` | ____ |
| `artifact_hash` | ____ |
| Seed | ____ |
| Library versions (sklearn / lifelines / numpy / pandas) | ____ |
| Code commit the model was fitted from | ____ |
| Evaluated on published run (`analysis_run.id`) | ____ |
| `risk_result` md5 of that run before the study | ____ |

Known registry limits to account for are listed in `docs/ml_architecture.md` §5. For example, the B4 `feature_spec_hash` does not hash the transform code.

## 2. Outcome measure (G6.2)

- **Outcome the layer is judged against:** ____
  - Evidence layers: randomised audit-sample review outcomes, since no fraud labels exist (`docs/gate_g6_criteria.md:20`).
  - Predictive layers: observed out-of-time outcomes.
- **Audit sample id and how it was drawn:** ____
- **Reviewers blind to the layer's output?** yes / no. If no, why: ____
- **What counts as a "useful" review outcome:** ____

## 3. Comparison (G6.2)

- **Current configuration (name and hash):** ____
- **Candidate configuration (current + layer):** ____
- **Statistic compared:** ____
- **Data not used to build the layer (how it is held out):** ____
- **Minimum added-value margin:** ____ *(owner)*

## 4. Non-redundancy (G6.3)

- **Redundancy measure** (e.g. maximum rank correlation with any single base signal; ablation overlap): ____
- **Maximum allowed correlation with any base signal:** ____ *(owner)*
- **Minimum ablation effect:** ____ *(owner)*

Reference values only, which are not a pass: Spearman with `cost_anomaly` is 0.288 (Mahalanobis) and 0.286 (Isolation Forest) on run 44 (`docs/gate_g6_criteria.md:21`).

## 5. Reviewer face validity (G6.4)

- **Number of reviewers / items:** ____ / ____
- **Sampling design** (tier-stratified, blind): ____
- **Minimum usefulness rate (with Wilson interval):** ____ *(owner)*
- **Minimum inter-rater agreement (statistic named):** ____ *(owner)*

## 6. Out-of-time performance and calibration (G6.5, predictive layers only)

- **Baseline named in advance:** ____
- **Split date / test window:** ____
- **Metric:** ____
- **Minimum improvement over the baseline (95% interval must exclude no improvement):** ____ *(owner)*
- **Calibration check (by decile):** ____

## 7. Leakage and validity (G6.6)

List the tests that must pass, by name. BLUEPRINT §7 rules 1–7 apply: no MP or payee identity, no `risk_score`, the time cut respected, grouped or out-of-time evaluation.

- ____
- ____

## 8. Entry mechanics (G6.7)

- **Proposed weight and its basis:** ____
- **Independence checks to re-run** (plan §11 items 1–8): ____
- **Decision record location:** ____

## 9. Analysis plan

- **Exact script(s) and arguments:** ____
- **Handling of not-evaluated works** (e.g. all Rajya Sabha works for B4): ____
- **Multiple-comparison handling:** ____
- **What happens if a criterion is inconclusive:** ____

## 10. Sign-off

| Step | Who | Date |
| --- | --- | --- |
| Protocol approved before any result | ____ | ____ |
| Results reported (link) | ____ | ____ |
| G6 decision (pass / fail / inconclusive) | ____ | ____ |

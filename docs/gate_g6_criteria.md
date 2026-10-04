# Gate G6: criteria for any ML layer entering `risk_score`

**Written 2026-09-29 (Phase 13.z) as the gate definition that Phase 8 was required to write and did not.**
This is a gate DEFINITION, not a decision. **Gate G6 is not met by any layer and is not waived.** No ML output enters `risk_score` (checked by `tests/test_phase6_atypicality.py::test_atypicality_never_enters_fusion`, `test_phase7_survival.py::test_phase5_code_never_references_the_survival_layer` and `test_phase13z_ml_vendor.py`).

## Source requirements (quoted)

- BLUEPRINT §7, "Entry into the risk score (gate G6)": "A layer may become a base signal only after a pre-registered study shows it adds information beyond the rule and statistical engine, does not simply mirror an existing signal, and is confirmed useful by reviewers. It then enters with its own weight and independence checks, as a new configuration."
- BLUEPRINT §13, Phase 7 row: "**G6:** pre-registered study shows added value before any entry into the score".
- BLUEPRINT §12, "What each ML layer must show": "A layer is accepted only if it beats a simple baseline out of time, is calibrated, and (for evidence layers) is judged useful by reviewers. An anomaly layer must also be partly orthogonal to the rule engine: a perfectly correlated one adds nothing."
- BLUEPRINT §14 claims discipline: cannot claim "Any ML in the score before gate G6, beyond TF-IDF text similarity (B1)" (quote updated 2026-10-02 to BLUEPRINT.md:676).
- Plan §7.3 (quote updated 2026-10-02): "no ML layer (B4, A1, A2 or any deferred item) enters `risk_score` without a pre-registered study clearing gate G6, and the TF-IDF similarity inside Near-Duplicate (§7.2) is the only ML in the score".
- Plan Phase 8: "A markdown gate-G6 criteria document (not code): pre-registered study requirement, non-redundancy threshold vs existing base signals, reviewer face-validity requirement — a gate DEFINITION, not a decision."

## Criteria (all must hold, in this order)

| # | Criterion | What must exist before the study starts | Evidence required to pass |
| --- | --- | --- | --- |
| G6.1 | **Pre-registration** | A dated, committed study protocol naming: the candidate layer and its exact model version (`model_version` id, feature-spec hash, seed); the published run it is evaluated on; the outcome measure; the analysis; and the pass thresholds for G6.2–G6.4. It must be committed **before** any result is computed. | The protocol's commit precedes every result file it cites. |
| G6.2 | **Added information beyond the engine** | The outcome the layer is scored against: for evidence layers, randomised audit-sample outcomes (BLUEPRINT §12), since no fraud labels exist; for predictive layers, observed out-of-time outcomes. | Pre-registered comparison of the current configuration against the same configuration plus the layer (a new configuration, BLUEPRINT §6 pattern). The added value must clear the pre-registered margin on data not used to build the layer. |
| G6.3 | **Non-redundancy with existing base signals** | The pre-registered redundancy measure and threshold (for example the maximum rank correlation with any single base signal, and the ablation overlap). | Measured on the published run. Current B4 values for reference (run 44, not a pass): Spearman vs cost_anomaly 0.288 (Mahalanobis) and 0.286 (Isolation Forest), `docs/phase6_atypicality_report_run44.md`. |
| G6.4 | **Reviewer face validity** | Reviewers blind to the layer's output where the design allows; the audit-sample protocol (§12: tier-stratified, blind, Wilson intervals, inter-rater agreement). | A pre-registered usefulness rate with a Wilson interval, and inter-rater agreement, above the pre-registered bars. |
| G6.5 | **Out-of-time performance and calibration** (predictive layers) | Baseline named in advance (for example Kaplan-Meier or base-rate Brier). | Beats the baseline out of time with a 95% interval that excludes no improvement, and is calibrated by decile. |
| G6.6 | **Leakage and validity** (BLUEPRINT §7 rules 1–7) | The leakage guard for the layer. | Tests pass: no MP/payee identity, no `risk_score`, time cut respected, grouped or out-of-time evaluation, no later period scoring an earlier one. |
| G6.7 | **Entry mechanics** | A proposed weight with its basis. | Enters as a NEW fusion configuration with its own weight and independence checks, gate-reported like G3 (plan §11 items 1–8, re-run), then an owner decision. The default configuration changes only by that decision. |

## Thresholds: owner decisions, deliberately not set here

The plan requires a "non-redundancy threshold" and reviewer bars but gives no numbers, and the project rule is not to invent thresholds. Each value below must be set by the owner **in the pre-registration** (G6.1), before any study result is seen:

| Value | Needed for | Set by |
| --- | --- | --- |
| Minimum added-value margin | G6.2 | owner, in the protocol |
| Maximum correlation with any base signal / minimum ablation effect | G6.3 | owner, in the protocol |
| Minimum reviewer usefulness rate and minimum inter-rater agreement | G6.4 | owner, in the protocol |
| Minimum out-of-time improvement over the baseline | G6.5 | owner, in the protocol |

## Status of each layer against G6 (2026-09-29, published run 44)

| Layer | Status | Why |
| --- | --- | --- |
| B4 robust Mahalanobis / Isolation Forest | **Not eligible yet**: evidence only | No pre-registration; no reviewer study (audit sample #42 has 0 reviews); partial orthogonality measured (G6.3 reference values only). |
| A1 Cox completion time | **Not eligible**: inactive experiment | Out-of-time C-index about 0.55 (`docs/phase8_ml_validation_report_retrospective.md`). |
| A2 365-day early warning | **Not eligible**: closed, not shipped | No better than the base-rate Brier out of time. |
| A3, B1 embeddings, B2 ML, B3 | Not built (plan §7.2 defers them) | — |

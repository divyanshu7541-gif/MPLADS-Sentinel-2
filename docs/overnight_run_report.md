# Overnight run report: Phase 13.z vendor and ML compliance audit (2026-09-29)

**In short:**
- I audited every vendor-data and ML requirement in the plan and BLUEPRINT: 63 quoted requirements, each with code, database, test and report evidence (`docs/ml_vendor_compliance_audit.md`).
- **Nothing that changes scores was needed or done.** The risk_result checksum is still **`c4d589e0e0fc40621d4e011a64a7233d`** (run 44), and there was no checksum reset.
- **The most important finding is a wrong product claim.** The frontend's methodology text says payments are "evidence only, never in the risk score". In fact each work's paid amount feeds the lifecycle-delay signal, exactly as BLUEPRINT §8 specifies. The frontend was out of scope tonight, so the wording fix is yours (below).
- **The ML picture is unchanged but now better evidenced:**
  - no model is in the score;
  - B4 is live as evidence and reproduces exactly from its seed;
  - A1 and A2 do not even beat a simple work-type baseline out of time, which strengthens the Phase 7 decision not to ship them.
- **I wrote the plan artifacts that were missing:** the gate-G6 criteria, a dated retrospective Phase 8 report, model cards, a drift report, and 7 tests.
- **What needs you:**
  - the G6 thresholds;
  - human reviews (B4 usefulness, duplicate labels, payee and agency review);
  - a retraining cadence;
  - the frontend wording;
  - one scoring-policy question (strict Level-1 peers for cost anomaly, which I deliberately did not change).

## Row counts by status (Part 2; 63 requirements)

| Status | After | Before |
| --- | ---: | ---: |
| IMPLEMENTED | 41 | 33 |
| PARTIAL | 10 | 12 |
| MISSING | 3 | 8 |
| DEVIATES | 1 | 2 |
| NOT-APPLICABLE-BY-ACCEPTED-DECISION | 4 | 4 |
| NOT-APPLICABLE (the plan defers it) | 4 | 4 |

## Fixes applied

All are **category A**: nothing that feeds risk_result changed.

| # | Fix | Diagnosis | Impact |
| --- | --- | --- | --- |
| A1 | `docs/gate_g6_criteria.md` | Plan Phase 8 required it; it never existed | Gate defined; not met by any layer, not waived. Thresholds left blank for you (not invented). |
| A2 | `docs/phase8_ml_validation_report_retrospective.md` | The consolidated Phase 8 report never existed | Dated 2026-09-29, labelled a retrospective compilation, computed on run 44 |
| A3 | `backend_v2/scripts/ml_validation_consolidated.py` → `docs/ml_validation_run44.json` | Phase 6/7 metrics were on run 1, with no CIs, no group-leakage measure and no peer baseline | See the metrics below. Read-only; checksum checked before and after. |
| A4 | `docs/model_cards.md` | BLUEPRINT §7 requires one-page model cards; none existed | Cards for B4a, B4b, A1, A2 |
| A5 | `backend_v2/scripts/drift_report.py` → `docs/drift_report_run1_vs_run44.md` | BLUEPRINT §7 "Drift is watched …" was not implemented | PSI per input and score, run 1 against run 44: all small (max 0.072, cost_anomaly). No alert threshold set (yours). |
| A6 | `backend_v2/tests/test_phase13z_ml_vendor.py` (7 tests) | No registry-completeness test (Phase 8), no split-disjointness test, vendor score path unpinned | Pins: payment amount is the only vendor input to the score; no payee or agency identity there; registry complete; B4 live on the published run; no A1/A2 output live; the out-of-time split is disjoint |
| A7 | `docs/validation_report_v1.md` §4, §7, §10, §11 | §7 repeated the wrong "payments never in the score" statement; §10/§11 listed G6/Phase 8 as open | Corrected, with dated notes |
| A8 | `docs/ml_vendor_compliance_audit.md` | The audit deliverable | Requirements, traceability, answers, verification recipe, deviations |

**Headline ML metrics** (run 44, out of time, 95% bootstrap CIs; identical to the run-1 point values):

| Model | Result | Against the base rate / chance | Against a work-type baseline |
| --- | --- | --- | --- |
| A1 Cox | C-index 0.553 [0.550, 0.557] | chance is 0.5 | type median 0.560: the model is below it |
| A2 day 90 | Brier 0.2527 | base rate 0.2500; significantly worse, difference CI [+0.0010, +0.0042] | type rate 0.2479: better than the model |
| A2 day 180 | Brier 0.2352 | base rate 0.2343; difference CI [−0.0005, +0.0022] | type rate 0.2374: the model beats it |

- **Group exposure:** 64% of test works share an MP with training, and 81% an authority. On unseen groups A2's AUC is below 0.5.
- **B4:** refit from the snapshot reproduces stored scores exactly. Orthogonality to cost_anomaly is 0.288 / 0.286.

**Category B fixes: none.** One candidate was considered and deliberately **not** applied (below).

## Checksum history

| When | Checksum | Why |
| --- | --- | --- |
| start of run | `c4d589e0e0fc40621d4e011a64a7233d` | run 44, published in Phase 13.y |
| after every fix (queried each time) | `c4d589e0e0fc40621d4e011a64a7233d` | unchanged; no category-B fix |
| **final** | **`c4d589e0e0fc40621d4e011a64a7233d`** | |

## Needs me, or skipped

| Item | Why | What I suggest |
| --- | --- | --- |
| **Frontend `meth.lim.6` (en.js:494, hi.js) is wrong about payments** | Frontend out of scope tonight | Replace "Payments, payees and implementing agencies are included as evidence only, never in the risk score." with: *"Each work's total paid amount feeds the lifecycle-delay signal (payment share ahead of completion, compared with peers); payee and implementing-agency identities and profiles are evidence only and never enter the risk score."* Hindi: remove the key (falls back to English) or translate. Then re-run `tests/test_phase13_claims.py`. |
| Strict Level-1 peers for cost anomaly | BLUEPRINT §6's signal table says "against level-1 peers", but its peer hierarchy and §14 Q3 ("It broadens to the next level") support the current fallback. Strict L1 would leave cost unevaluated for 26,573 works (L2 11,166, L3 15,407) and re-tier many more (category B). | Keep the current behaviour unless you decide otherwise. If you want strict L1, I'll run it as a category-B fix with a dry run first. |
| G6 thresholds | The plan requires them and gives no numbers; the project rule is not to invent thresholds | Set them in the pre-registration (`docs/gate_g6_criteria.md`) |
| B4 reviewer usefulness; audit sample #42 has 0 reviews | Needs human reviewers | Run a review round on #42 |
| B1 duplicate labels (about 500 pairs); synthetic injection curves | Needs labellers; injection is outside this ML/vendor audit | Labelling round; a later validation task |
| Payee (29,583) and agency (7,203) review | All "unreviewed"; 17,122 payees and 2,754 agencies "unclassified" | Human review queue |
| Weekly retraining job | The worker is a stub and only one snapshot exists | Decide the cadence once a second snapshot exists |
| Drift alert threshold | None set (only a rule-of-thumb band) | Owner decision |
| Stale historical reports | `phase10_11_report.md` entity counts are run 1 (entity_metric is now 58,480 on run 44) | Left as the historical record, flagged in the audit (Q9) |

## Judgment calls made

1. **No score changes.** Every deviation found was either documentation or tests (category A), a human task, or an owner decision (the cost-level question). When unsure I treated it as category B, and nothing met the bar to apply unattended.
2. **A1/A2 metric recomputation was read-only.** No `model_version` or `forecast_result` row was written (no `--record-inactive`). The stored A1 record stays as trained on run 1; its features don't depend on the authority fix, which the identical recomputed metrics confirm.
3. **Group leakage (rule 4):** measured and reported, not "fixed" by retraining, because neither A1 nor A2 is live. Retraining closed or inactive models would change nothing a user sees.
4. **V20 (facts vs entity_metric rows):** documented, not moved. Same information; moving it would touch the API and the serving build.
5. **Git:** committed only files that belong solely to Phase 13.z (commit below). Left uncommitted: the Phase 14 deploy work, which awaits your Vercel test (it includes `docs/security.md`, a Phase 14-only edit), and `docs/validation_report_v1.md`, which holds both Phase 14 and 13.z edits and can't be split without interactive staging. Nothing pushed.
6. **The Phase 14 deploy stack was left running untouched** (hard limit). It serves published run 44, unchanged.

## Test results

- **Targeted:** `tests/test_phase13z_ml_vendor.py` 7 passed; ruff clean.
- **Full suite:** SUITE_RESULT_PLACEHOLDER

## State at the end

- **Nothing left running** except the Phase 14 deploy stack (`sentinel-deploy-*`), which was running before and is out of scope, and the scratch database.
- **Committed:** COMMIT_PLACEHOLDER. **Not committed (explained above):** the Phase 14 files and `docs/validation_report_v1.md`.
- **Log:** `docs/overnight_run_log.md`.

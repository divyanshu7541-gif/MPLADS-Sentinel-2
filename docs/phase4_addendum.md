# Phase 4 Addendum: corrected attribution in three signals

Written 2026-09-26 (Phase 5a, step 2). **For portfolio_concentration, district_authority_pattern and temporal_anomaly, this addendum supersedes [phase4_signals_report.md](phase4_signals_report.md).** That report is otherwise unchanged and remains the historical record of the Phase 4 run as reviewed. The other three signals (cost_anomaly, near_duplicate, lifecycle_delay) are not affected.

This document also records the C5 open-works decision (section 5).

## 1. What was wrong

All three signals compare an entity (an MP, a district authority) with its peers. Phase 4 computed the comparison per entity, took the entity's **single most extreme result**, and attached that one score to **every work of that entity**:

| Signal | Phase 4 computed | Phase 4 attached to each work |
| --- | --- | --- |
| portfolio_concentration | an MP's share of each work type vs peer MPs in the state | the MP's most extreme type, on all of that MP's works |
| district_authority_pattern | an authority's type share and amount vs peer authorities in the state | the authority's most extreme type, on all of its works |
| temporal_anomaly | an MP's or authority's weekly counts vs its own typical week | the entity's busiest week, on all of its works |

So a road built by an MP who happened to be unusual for community halls scored as if it were the unusual community hall. A work recommended in a quiet week scored as if it were in the MP's busiest week. Almost every MP and authority has one extreme type or week, so almost every work inherited a near-maximum score. Medians were 1.000 for all three signals. This violates BLUEPRINT §6's correction that "each score attaches to its own row".

It was found in Phase 5, while checking whether fusion could be meaningful: with three of six inputs at ~1.0 for nearly every work, it could not.

## 2. What changed

Only these three functions in `backend_v2/app/analytics/signals.py` changed. Phase 3, the other three signals, and all thresholds and constants are untouched.

- **portfolio_concentration:** each work is scored on its **own (MP, work type)** share vs the peer MPs' mean share for that type. Previously it took the max over the MP's types.
- **district_authority_pattern:** each work is scored on its **own (authority, work type)** components. The amount component also moved to a log scale: it compared raw rupee means, so a single large-rupee type dominated. It now compares log-amount means with the same `MIN_LOG_SCALE` floor as cost_anomaly, so a pure change of units no longer changes the score.
- **temporal_anomaly:** each work is scored on its entity's event count in the work's **own ISO week** vs the entity's typical active week, per date field and entity. The strongest combination is kept. A work dated on a national batch day is not evaluated for that date field. Batch-day events remain excluded from every count. The evidence keys were renamed `burst_week` / `burst_week_count` -> `own_week` / `own_week_count` to say what they now are.

Why: correctness only. The goal was that each score describes the work it is attached to. The change was not made to shape tiers or the risk distribution; the tier distribution after the fix still fails the Phase 5 gate's K4 criterion (see below).

## 3. Before and after

Before = the reviewed Phase 4 run (phase4_signals_report.md, run 1 of that database). After = the Phase 5 gate run (phase5_gate_report.md, run 1), and the post-split run from Phase 5a step 1 (run 2 of the current database).

| Signal | Version | Evaluated | Mean | Median | P90 | Active (score > 0.1) | Score >= 0.9 | Score >= 0.99 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| portfolio_concentration | Phase 4 (before) | 96,012 | 0.966 | 1.000 | 1.000 | >= 96.2% (bound) | not computed | not computed |
| | Phase 5 gate run | 96,011 | 0.707 | 0.859 | 1.000 | 94.5% | 46.6% | 31.3% |
| | post-split run | 96,015 | 0.708 | 0.859 | 1.000 | 94.3% | 46.6% | 31.3% |
| district_authority_pattern | Phase 4 (before) | 95,855 | 0.991 | 1.000 | 1.000 | >= 99.0% (bound) | not computed | not computed |
| | Phase 5 gate run | 95,855 | 0.766 | 0.878 | 1.000 | 99.0% | 47.4% | 28.3% |
| | post-split run | 95,804 | 0.766 | 0.878 | 1.000 | 99.0% | 47.6% | 28.5% |
| temporal_anomaly | Phase 4 (before) | 97,504 | 0.985 | 1.000 | 1.000 | >= 98.3% (bound) | not computed | not computed |
| | Phase 5 gate run | 97,504 | 0.774 | 0.976 | 1.000 | 92.8% | 59.4% | 46.4% |
| | post-split run | 97,504 | 0.773 | 0.976 | 1.000 | 92.8% | 59.1% | 46.1% |

About the "before" active rates:
- They were never computed. The Phase 4 report published eligible count, mean, median, P90, P99 and max only.
- The pre-fix code no longer exists to re-run, and I did not rebuild it.
- The bounds are exact consequences of the published means. Scores lie in [0, 1], so if a share f of works scored at most 0.1, the mean is at most (1 - f) + 0.1f. That gives f <= (1 - mean) / 0.9.
  - portfolio: f <= 3.8%, so active >= 96.2%
  - district: f <= 1.0%, so active >= 99.0%
  - temporal: f <= 1.7%, so active >= 98.3%

What the numbers show:
- **The fix moved the level, not the active rate.**
  - Medians fell from 1.000 to 0.86–0.98 and means fell by 0.2–0.26.
  - The share above the 0.1 "active" threshold moved little. Portfolio went from at least 96.2% to 94.5%, a drop of 1.7 to 5.5 points. Temporal went from at least 98.3% to 92.8%, a drop of 5.5 to 7.2 points. District went from at least 99.0% to 99.0%, essentially unchanged.
  - Most works still score well above 0.1 on these signals. That is the calibration problem the Phase 5 gate report (section 3) identifies, and it is separate from this bug.
  - Under a standard-normal model a score of 0.9 or more would occur for ~10% (portfolio), ~19% (district) and ~5% (temporal) of works. Observed after the fix: 47%, 47% and 59%.
- **Evaluated counts:**
  - Portfolio: 96,012 -> 96,011, because one work lost its own (MP, type) comparison when attribution moved per work.
  - The post-split differences (e.g. district 95,804) come from Phase 5a step 1's split of 136 shared portal IDs, not from this fix.
- **The post-split run is within 0.3 points of the gate run** on every figure above.

## 4. Tests

Regression tests added for the fix, in `backend_v2/tests/test_phase4_signals_unit.py`:
- `test_portfolio_concentration_scores_each_work_on_its_own_type_not_the_mps_worst`: an MP whose type-3 share equals every peer's scores < 0.05 on its type-3 works, and > 0.9 on its extreme type-1 works.
- `test_district_authority_pattern_scores_each_work_on_its_own_type`: the same check for an authority.
- `test_district_authority_amount_component_is_on_log_scale`: doubling every amount leaves `z_amount` unchanged.
- `test_temporal_scores_each_work_in_its_own_week_not_the_entitys_worst`: works in an MP's ordinary weeks score < 0.05; works in its burst week score > 0.9.
- `test_temporal_batch_day_exclusion`: updated. A work dated on a national batch day is never scored on that date field. A genuine single-MP burst still fires, and cites its own week.

**Full Phase 4 suite against the corrected signals.** Run 2026-09-26 on the post-split database, Docker, Python 3.12:

| File | Tests | Result |
| --- | --- | --- |
| test_phase4_signals_unit.py | 44 | 44 passed |
| test_phase4_signals_context.py (real data) | 17 | 17 passed |
| **Total** | **61** | **61 passed, 0 failed, 0 skipped** (9 min 03 s) |

The real-data tests include:
- no eligible row with a null score, for all six signals
- scores in range with no stored NaN
- House filters equal to LS + RS with unchanged scores
- same input -> same signals output hash

## 5. C5 open works: decision recorded

BLUEPRINT §6 C5 gives "13,562 open works past one year". We keep **"more than 365 days"** between sanction date and the snapshot's data-as-of date (30 Aug 2026), which gives **13,476**. We do not use "365 days or more", which gives 13,562.

- **The gap is 86 works,** all sanctioned on 30 Aug 2025, exactly 365 days before the snapshot date.
- **Reasoning:** "past one year" / "more than a year" means strictly more than a year has elapsed. A work sanctioned exactly one year before the snapshot date is at the limit, not past it. The completed-works half of C5 uses the same strict rule and reproduces BLUEPRINT's 11.9% (11.87%).
- **This is a deliberate choice, not a bug.** The rule was not changed to hit BLUEPRINT's number, and the difference is fully explained by that one boundary day.
- **Tests:** `backend_v2/tests/test_phase5_risk_context.py::test_c5_matches_blueprint`
  - The strict (stored) count is asserted exactly: it must equal 13,476. This was tightened from "within 1% of 13,562" on 2026-09-26, at your request after step 2.
  - The inclusive count is asserted exactly: `>= 365 days` must equal 13,562.
  - Any change to the rule or the as-of date now fails the test.

# Phase 3 Peer Context Coverage Report

Generated 2026-09-24T19:09:39.360516+00:00 by `scripts/run_context.py`.

analysis_run id 2, engine `context_v1`
config_hash `2e590a92c2288cbd08300631d2152687bd7511698a93d68033f3944555f5d57b`
output_hash `003c1432c9a0f8ec62c7e868b3de3ce149c8271ff22f1060a44c91859e3b1466`

## 1. Scope and definitions

Works in scope: 97,506. These are Snapshot A work_state rows with lifecycle 'sanctioned' or 'completed' (a sanction record exists, so a sanction FY and amount can exist). Recommended-only works have no sanction FY and no cost to baseline, so they get no context row.
By house: {'LS': 78368, 'RS': 19138}

- **Amount** is the actual amount for completed works and the sanction amount otherwise.
- **Usable peer** means the amount is present and > 0 (BLUEPRINT.md §6: "usable peer count excludes missing amounts").
- **Peers** are the other works in the same group. The work itself is always excluded, from counts and from statistics.
- **Distinct other MPs** are MPs among the peers, excluding the work's own MP.
- **MP share** is the largest single MP's share of the peers. The work's own MP's *other* works count toward it.
- **House is not a grouping key.** Groups mix LS and RS works. House is only a filter on which rows are displayed.

Missing key components (row can't join a group at the levels that use them): {'sanction_fy': 0, 'state_id': 0, 'activity_type_id': 0, 'district_key': 0, 'amount not usable': 98}

## 2. Coverage vs BLUEPRINT.md §6

Tolerance: ±1.5 percentage points. The coverage column in BLUEPRINT.md §6 is *descriptive*: "works that have N or more peers / MPs". Its MP figures count every distinct MP in the group, **the work's own MP included**. That is the only definition that reproduces them (see the sensitivity table below). The *gating* rule is stricter: it needs ≥3 *other* MPs and applies the 50% cap. Both are reported. Only the rule decides whether a baseline exists.

| Level | BLUEPRINT measure | Measured | BLUEPRINT | Check |
| --- | --- | --- | --- | --- |
| L1 | ≥10 usable peers (excl. self) | 91.57% | 92.7% | OK |
| L1 | group has ≥3 MPs (own MP included) | 89.6% | 90.5% | OK |
| L2 | ≥10 usable peers (excl. self) | 96.34% | 96.8% | OK |
| L3 | ≥10 usable peers (excl. self) | 99.45% | 99.5% | OK |
| REF | group has ≥3 MPs (own MP included) | 19.15% | 19.1% | OK |

All per-level figures (all rows in scope as the denominator):

| Level | ≥10 peers | ≥3 MPs in group (own incl.) | ≥3 MPs among peers | ≥3 other MPs | Qualifies under the rule |
| --- | --- | --- | --- | --- | --- |
| L1 | 91.57% | 89.6% | 88.9% | 85.54% | 67.53% |
| L2 | 96.34% | 96.15% | 95.82% | 93.78% | 78.26% |
| L3 | 99.45% | 99.67% | 99.62% | 99.51% | 96.9% |
| REF | 69.72% | 19.15% | 18.6% | 8.7% | 3.15% |

- **Rule, identical at every level including refinement:** ≥15 usable peers excluding self, ≥3 distinct other MPs, and no MP above 50% of the peers. Refinement uses the capped rule by the user's Phase 3 review decision (2026-09-25). BLUEPRINT.md's shorter wording for it ("3 or more MPs and 15 or more peers") would have qualified about 16.4% of works with no cap.

### Sensitivity of the L1 figures to the denominator definition

BLUEPRINT.md doesn't spell out its denominator. For transparency, here are the same two L1 measures under the plausible alternatives. The table above uses the first row.

| Denominator | n | ≥10 peers | ≥3 MPs in group (own incl.) | ≥3 MPs among peers | ≥3 other MPs |
| --- | --- | --- | --- | --- | --- |
| All sanctioned/completed works | 97,506 | 91.57% | 89.6% | 88.9% | 85.54% |
| Works with a usable amount | 97,408 | 91.58% | 89.6% | 88.9% | 85.53% |
| Works with a complete L1 key | 97,506 | 91.57% | 89.6% | 88.9% | 85.54% |
| Lok Sabha only | 78,368 | 92.48% | 91.38% | 90.76% | 87.72% |
| Rajya Sabha only | 19,138 | 87.86% | 82.31% | 81.27% | 76.61% |

The remaining gap is ~1.1 pp on L1 "≥10 peers" and ~0.9 pp on MPs, both within tolerance. Its exact cause is not determined. Bucketing by calendar year instead of FY closes about half of it: 92.1% and 90.2% in the Phase 3 experiment. BLUEPRINT.md says "sanction FY", so the engine keeps the Indian FY (April-March).

## 3. Level actually assigned (first qualifying of L1 → L2 → L3)

| Level | Works | Share |
| --- | --- | --- |
| L1 | 65,849 | 67.53% |
| L2 | 12,307 | 12.62% |
| L3 | 16,995 | 17.43% |
| none | 2,355 | 2.42% |

"none" means no level satisfies the full rule, so the work gets no cost baseline. BLUEPRINT.md §6 records that as "not evaluated" (lowering confidence), never as zero.

Assigned level by house (display filter only; the groups themselves mix houses):

| House | L1 | L2 | L3 | none |
| --- | --- | --- | --- | --- |
| LS | 54,729 | 8,547 | 14,026 | 1,066 |
| RS | 11,120 | 3,760 | 2,969 | 1,289 |

work_context rows by house: {'LS': 78368, 'RS': 19138}

## 4. Correctness checks on this run

- Max MP share among assigned groups: 0.5000 (rule: ≤ 0.5) — OK
- Min usable peers (excl. self) among assigned: 15 (rule: ≥ 15)
- Min distinct other MPs among assigned: 3 (rule: ≥ 3)
- Level-1 key: ('activity_type_id', 'state_id', 'sanction_fy'). No constituency component.
- Leave-one-out: each work's baseline is computed after removing the work from its group's value vector. `tests/test_phase3_context.py` re-derives a sample from the database and checks it.

## Appendix: House toggle (frontend) and the `house` parameter

**The toggle has no visible effect on data until the Phase 12 cutover.** The frontend's `/api` proxy still points at the old backend, which has no House column and ignores `house=`. The `backend_v2` endpoints that accept `house` are still stubs. What is real now:

- The toggle: `[ LOK SABHA ] [ RAJYA SABHA ]`, mounted once in the shared topbar. Neither selected means both houses. Clicking the selected house again clears it.
- `house=LS|RS` is appended to exactly /api/summary, /api/queue, /api/analytics, /api/map-data, /api/map-works, /api/map-filters and /api/graph-data, and only when a house is selected. With none selected, every URL is byte-identical to before.
- Under Rajya Sabha, the map's constituency drill-down and constituency performance/comparison show an explicit "not applicable for Rajya Sabha" notice instead of an empty result.
- In `backend_v2`, `house` is optional on the seven endpoints and any other value returns 422 (`tests/test_contract.py`, structural only). Real filtering of the data is tested on `work_context` (`tests/test_phase3_context.py`).

Browser test (`backend_v2/tests/e2e/test_house_toggle_e2e.py`, skipped unless `E2E_BASE_URL` is set). As run for this phase, from the repo root:

```sh
docker run -d --name p3-api -v "$PWD/backend_v2":/srv <backend_v2 dev image> \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
docker run -d --name p3-vite --network container:p3-api -v "$PWD/frontend":/app \
  -v <node_modules volume>:/app/node_modules -w /app node:20-alpine npx vite --host 0.0.0.0 --port 3000
docker run --rm --network container:p3-api -v "$PWD/backend_v2/tests/e2e":/e2e \
  -e E2E_BASE_URL=http://localhost:3000 mcr.microsoft.com/playwright/python:v1.47.0-jammy \
  sh -c 'pip install -q pytest==8.3.3 playwright==1.47.0 &&
         cd /e2e && python -m pytest -q --rootdir /e2e .'
```

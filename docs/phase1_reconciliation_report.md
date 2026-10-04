# Phase 1 Reconciliation Report

Generated 2026-09-25T15:07:35.004291+00:00Z by `scripts/run_ingest.py`.

## 1. Core file reconciliation (BLUEPRINT.md §2)

| File | Measure | Body sum (rupees) | Footer match | vs BLUEPRINT (crore) | Status |
|---|---|---|---|---|---|
| expenditure_LokSabha_alltenures.csv | Payments LS | 27,361,507,267.45 | yes | 2736.1507 vs 2736.15 | PASS |
| expenditure_RajyaSabha_alltenures.csv | Payments RS | 12,329,282,321.69 | yes | 1232.9282 vs 1232.93 | PASS |
| mp_allocation_LokSabha_alltenures.csv | Allocation LS | 83,180,553,325.71 | yes | 8318.0553 vs 8318.06 | PASS |
| mp_allocation_RajyaSabha_alltenures.csv | Allocation RS | 33,638,482,301.82 | yes | 3363.8482 vs 3363.85 | PASS |
| works_completed_LokSabha_alltenures.csv | Completed LS | 16,319,975,812.40 | yes | 1631.9976 vs 1632.00 | PASS |
| works_completed_RajyaSabha_alltenures.csv | Completed RS | 7,554,187,766.21 | yes | 755.4188 vs 755.42 | PASS |
| works_recommended_LokSabha_alltenures.csv | Recommended (LS) | 56,386,598,274.27 | yes | 5638.6598 vs 5638.66 | PASS |
| works_sanctioned_LokSabha_alltenures.csv | Sanctioned LS | 41,176,707,014.08 | yes | 4117.6707 vs 4117.67 | PASS |
| works_sanctioned_RajyaSabha_alltenures.csv | Sanctioned RS | 16,932,362,805.87 | yes | 1693.2363 vs 1693.24 | PASS |

**All 9 core files reconciled: YES**

## 2. All registered files (P0/P1)

| Snapshot | File | Category | Rows valid | Rows rejected | Status |
|---|---|---|---|---|---|
| snapshot_a | works_recommended_LokSabha_alltenures.csv | works_recommended | 103330 | 0 | pass |
| snapshot_a | works_sanctioned_LokSabha_alltenures.csv | works_sanctioned | 78232 | 0 | pass |
| snapshot_a | works_sanctioned_RajyaSabha_alltenures.csv | works_sanctioned | 19274 | 0 | pass |
| snapshot_a | works_completed_LokSabha_alltenures.csv | works_completed | 33955 | 0 | pass |
| snapshot_a | works_completed_RajyaSabha_alltenures.csv | works_completed | 9887 | 0 | pass |
| snapshot_a | expenditure_LokSabha_alltenures.csv | expenditure | 82885 | 0 | pass |
| snapshot_a | expenditure_RajyaSabha_alltenures.csv | expenditure | 24941 | 0 | pass |
| snapshot_a | mp_allocation_LokSabha_alltenures.csv | mp_allocation | 543 | 0 | pass |
| snapshot_a | mp_allocation_RajyaSabha_alltenures.csv | mp_allocation | 231 | 0 | pass |
| snapshot_a | calamity_LokSabha_alltenures.csv | calamity | 12 | 0 | processed |
| snapshot_a | calamity_RajyaSabha_alltenures.csv | calamity | 20 | 0 | processed |
| snapshot_a | mp_names_all_states.csv | mp_roster | 778 | 0 | processed |
| snapshot_a | states.csv | states | 36 | 0 | processed |
| snapshot_b | mplads_recommended_works_2026-09-19.csv | snapshot_b_recommended | 87242 | 30 | processed |
| snapshot_b | mplads_completed_works_2026-09-19.csv | snapshot_b_completed | 44028 | 0 | processed |
| snapshot_b | mplads_expenditures_2026-09-19.csv | snapshot_b_expenditure | 108695 | 0 | processed |
| snapshot_b | mplads_mp_summary_2026-09-19.csv | snapshot_b_mp_summary | 774 | 0 | processed |
| prior_cycle | prior_cycle_backlog_2023-24.csv | prior_cycle | 60359 | 0 | processed |
| macro | RS-Session-251-AU3002-Annexure-I.csv | macro_state_totals | 38 | 0 | processed |
| macro | RS_Session_247_AS_175.csv | macro_unspent_balance | 40 | 0 | processed |
| macro | RS_Session_247_AU_2719.csv | macro_fy_totals | 6 | 0 | processed |

## 3. Reject log

Core files (the 9 reconciled above): **0 rejected rows** -- meets acceptance criteria.

All files with any rejected rows:

| File | Rejected rows |
|---|---|
| mplads_recommended_works_2026-09-19.csv | 30 |

## 4. House-tagging

Total `work` rows: **128883**

| House | Count |
|---|---|
| RS | 25192 |
| LS | 103691 |

| House source | Count |
|---|---|
| house_column | 6054 |
| filename | 122829 |

### Sample of 20 House-tagged work rows

| work_key | house | house_source | raw_mp_name |
|---|---|---|---|
| 241361 | LS | filename | Shri Narendra Modi |
| 291336 | LS | filename | SATPAL BRAHAMCHARI |
| 244750 | LS | filename | DEVESH SHAKYA |
| 201209 | LS | filename | Smt S Jothimani |
| 197854 | RS | filename | Shri Baburam Nishad (2022-28) |
| 223141 | LS | filename | Ravindra Vasantrao Chavan |
| 192915 | LS | filename | Mitesh Rameshbhai Bakabhai Patel |
| 183276 | LS | filename | NALIN SOREN |
| 252328 | RS | filename | Shri S. Jaishankar (2023-29) |
| 185499 | LS | filename | BHARTI PARDHI |
| 261129 | RS | filename | Smt. Renuka Chowdhury (2024-30) |
| 201053 | LS | filename | DULU MAHATO |
| 290162 | LS | filename | Adv Adoor Prakash  |
| 1970 | LS | filename | DR BYREDDY SHABARI |
| 207929 | LS | filename | Asit Kumar Mal  |
| 160725 | LS | filename | Smt Aparajita Sarangi |
| 256246 | RS | filename | Dr. Sudhanshu Trivedi (2024-30) |
| 179374 | LS | filename | Jitendra Singh |
| 144487 | RS | filename | Dr. Sangeeta Balwant (2024-30) |
| 233049 | LS | filename | Bharatsinhji Shankarji Dabhi |

**Note on works_recommended_RajyaSabha absence:** confirmed absent from data/raw/snapshot_a/ (file does not exist in that directory) -- this is EXPECTED per BLUEPRINT.md §2 hard limit 6, not a pipeline failure. The gap is filled from Snapshot B's mplads_recommended_works file, restricted to its Rajya Sabha rows ([6054] new work rows added from that source).

## 5. Reference data (state / state_alias / district_authority)

State alias candidates scanned: 38; unmatched: 5
  - Unmatched: A & N Island, A & N Islands, D & N Haveli, Daman & Diu, Nominated
District authorities: 774 added this run (of 878 candidate IDA names seen); 0 with no matching state.

`person`, `tenure`, `constituency`, `activity_type` are schema-only this phase (migration created, not populated) -- entity resolution is a later pipeline stage (BLUEPRINT.md §5 P3/P5), not part of Phase 1's explicit "Geo reference load" scope.

## 6. Geo reference load (BLUEPRINT.md §15)

Source: DataMeet maps (github.com/datameet/maps), licence CC BY 4.0.
National boundary: `Country/india-soi.geojson` (Survey of India-derived, per the file's own `Source` property).
State boundaries: `States/Admin2.shp` (36 states/UTs).

State name crosswalk match rate: **94.4%** (34/36).
Unmatched (listed, not dropped): Andaman And Nicobar Islands, The Dadra And Nagar Haveli And Daman And Diu

## 7. Idempotency

Output hash (sha256 of sorted filename|sha256|row_count across all raw_file rows): `be065af515985402da03bc97a2ae4d2b54ad5402dd936a353403f3e2b105ef1c`
Re-run this script against the same data/ and database to verify the hash is unchanged (see tests/test_phase1_ingest.py::test_idempotent_rerun_reproduces_same_output_hash).

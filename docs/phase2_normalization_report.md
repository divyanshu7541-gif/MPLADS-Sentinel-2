# Phase 2 Normalization Report

Generated 2026-09-25T15:10:28.184973+00:00Z by `scripts/run_normalize.py`.

## 1. P3 Normalise -- activity type / district key parsing

Work keys processed: 122829
Distinct activity types: 115 (BLUEPRINT.md §2: 115)
Activity type parse rate: 100.0%
District key parse rate: 100.0%

## 2. P4 Link + lifecycle -- Snapshot A

work_state rows added: 122829
Lifecycle status counts: {'sanctioned': 53664, 'completed': 43842, 'recommended': 25323}
Referential gap (sanctioned/completed but no recommended record): 361 (BLUEPRINT.md §5/§6: 361, logged not hidden)

## 3. P4 Link -- Snapshot B gap-fill

work_state rows added (recommended-only): 6054

## 4. P5 Entity resolution -- payees

Distinct VENDOR_ID: 29583 (BLUEPRINT.md §7: 29,583)
Distinct VENDOR_NAME: 27961 (BLUEPRINT.md §7: 27,961)
Names shared by >1 ID: 1045 (BLUEPRINT.md §2: 1,045) -- kept as aliases, never merged
IDs with >1 distinct name: 0 (BLUEPRINT.md §2: 0, "no ID has two spellings")
Payees added this run: 29583, aliases added: 29583

## 5. P5 Entity resolution -- implementing agencies

Distinct agencies seen: 7203 (BLUEPRINT.md §8: ~7,200)
Agencies added this run: 7203

## 6. Payments

Payment rows added: 107826
Skipped, no matching payee: 0

## 7. MP roster / tenure / allocation

person rows: 778 (BLUEPRINT.md §2: 778 roster IDs)
tenure rows: 763
constituency rows: 542
Allocations added: 763, unmatched (no tenure): 11 (expected -- these are 'Nominated Rajya Sabha' rows, which get no tenure row by design; verified 2026-09-24 that the unmatched count exactly equals the Nominated Rajya Sabha row count)
MP roster join: 0 unmatched names out of allocation rows scanned (BLUEPRINT.md §9: 100% matched on Snapshot A) -- queued for review, never auto-merged

## 8. Descriptive tables

calamity_consent rows added: 32
prior_cycle_work rows added: 60359
macro_reference rows added: 347

## 9. Acceptance criteria checks

work_key uniqueness: 128883 rows, 128883 distinct (OK -- PK-enforced regardless)
Completed works without a sanction record: 0 (OK)
Payments with no matching sanctioned/completed work_state row: 0 (OK)

activity_type table: 115 rows
payee table: 29583 rows

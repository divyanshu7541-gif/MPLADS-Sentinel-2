# Restore test report

RESULT: PASS

- **Run at:** 2026-09-28 17:13 UTC
- **Procedure:** `ops/backup/restore_test.sh`. It backs up with the scheduled job's own script
  (`ops/backup/backup.sh`), restores into a brand-new PostgreSQL 16 instance, verifies the result,
  then runs the API suites against the restored copy.
- **Source:** `localhost:5447/sentinel`
- **Target:** fresh postgres:16 container on :5460. It had 0 tables before the restore.

## 1. Backup

| | |
| --- | --- |
| Dump | `sentinel_20260928T165749Z.dump` (custom format, compressed) |
| Size | 208M |
| SHA-256 | `be13ca7258bedef238b0cf8f2982f11366e93729e6dc057402a65f943ed17bf0` |
| Backup time | 154 s |
| Restore time | 643 s |

## 2. Verification: source vs restored

Verifier: `backend_v2/scripts/verify_restore.py`.

| Check | Result |
| --- | --- |
| alembic_revision_equal | PASS |
| same_tables | PASS |
| row_counts_equal | PASS |
| published_run_intact | PASS |
| risk_checksum_equal | PASS |
| serving_build_intact | PASS |
| case_chain_valid | PASS |
| risk_checksum_expected | PASS |

- **Alembic revision:** `3f1a7c9e2b50`
- **Published run:** [44, 'v4-candidate']
- **risk_result checksum (published run):** `c4d589e0e0fc40621d4e011a64a7233d`
- **Tables:** 52, with 5,519,294 rows in total. Row-count mismatches:
  none
- **Case-event hash chain:** 0 events; valid = True

The largest restored tables:

| Table | Rows |
| --- | --- |
| compliance_result | 1,442,172 |
| signal_result | 1,170,072 |
| raw_row | 655,306 |
| atypicality_result | 390,024 |
| risk_result | 390,024 |
| served_work | 245,930 |
| map_work | 195,012 |
| work_context | 195,012 |
| work | 129,019 |
| work_state | 129,019 |
| work_geo | 122,965 |
| entity_metric | 115,641 |

## 3. API suites against the restored database

`tests/test_contract.py` and `tests/test_phase12_cutover.py`, with `DATABASE_URL` pointing at the
restored instance:

    117 passed, 1 warning in 54.19s

Exit codes: verifier 0, pytest 0.

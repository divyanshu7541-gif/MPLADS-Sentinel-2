"""Writes docs/restore_test_report.md from one restore_test.sh run (values via env)."""
import datetime as dt
import json
import os
import sys
from pathlib import Path

verify_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
v = json.loads(verify_path.read_text(encoding="utf-8")) if verify_path.exists() else {}
e = os.environ
checks = v.get("checks", {})
check_rows = "\n".join(f"| {k} | {'PASS' if ok else 'FAIL'} |" for k, ok in checks.items()) or "| (verification did not run) | FAIL |"
top = sorted(v.get("table_counts", {}).items(), key=lambda kv: -kv[1])[:12]
table_rows = "\n".join(f"| {t} | {n:,} |" for t, n in top)
chain = v.get("case_chain", {})
report = f"""# Restore test report

RESULT: {e['RESULT']}

- **Run at:** {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
- **Procedure:** `ops/backup/restore_test.sh`. It backs up with the scheduled job's own script
  (`ops/backup/backup.sh`), restores into a brand-new PostgreSQL 16 instance, verifies the result,
  then runs the API suites against the restored copy.
- **Source:** `{e['SOURCE']}`
- **Target:** {e['TARGET']}. It had {e['FRESH_TABLES']} tables before the restore.

## 1. Backup

| | |
| --- | --- |
| Dump | `{e['DUMP_NAME']}` (custom format, compressed) |
| Size | {e['DUMP_SIZE']} |
| SHA-256 | `{e['DUMP_SHA']}` |
| Backup time | {e['BACKUP_SECONDS']} s |
| Restore time | {e['RESTORE_SECONDS']} s |

## 2. Verification: source vs restored

Verifier: `backend_v2/scripts/verify_restore.py`.

| Check | Result |
| --- | --- |
{check_rows}

- **Alembic revision:** `{v.get('alembic')}`
- **Published run:** {v.get('published_run')}
- **risk_result checksum (published run):** `{v.get('risk_md5')}`
- **Tables:** {v.get('tables')}, with {v.get('rows_total', 0):,} rows in total. Row-count mismatches:
  {v.get('row_count_mismatches') or 'none'}
- **Case-event hash chain:** {chain.get('events')} events; valid = {chain.get('valid')}

The largest restored tables:

| Table | Rows |
| --- | --- |
{table_rows}

## 3. API suites against the restored database

`tests/test_contract.py` and `tests/test_phase12_cutover.py`, with `DATABASE_URL` pointing at the
restored instance:

    {e['PYTEST_LINE'] or '(no pytest summary line -- see ci-artifacts/restore-test/pytest.log)'}

Exit codes: verifier {e['VERIFY_RC']}, pytest {e['PYTEST_RC']}.
"""
out_path.write_text(report, encoding="utf-8")
print(f"wrote {out_path}")

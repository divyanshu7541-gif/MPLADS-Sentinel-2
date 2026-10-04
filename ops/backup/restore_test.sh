#!/usr/bin/env bash
# Executed restore test (BLUEPRINT.md §11 "documented restore test";
# Phase 13: "actually restore into a fresh instance, confirm the published
# run is intact").
#
#   1. Back up the source database with the SAME script the scheduled
#      `backup` service runs (ops/backup/backup.sh, BACKUP_ONCE=1).
#   2. Start a brand-new, empty PostgreSQL 16 container.
#   3. pg_restore the dump into it.
#   4. Verify source vs restored (backend_v2/scripts/verify_restore.py):
#      Alembic revision, every table's row count, the published run, the
#      risk_result checksum, the serving build, the case-event hash chain.
#   5. Point the API test suites at the RESTORED database: the frontend
#      contract suite and the Phase 12 cutover regression suite must pass.
#   6. Write docs/restore_test_report.md ("RESULT: PASS" only if 4 and 5 pass).
#
# Environment (defaults = the local scratch database):
#   PGHOST=localhost PGPORT=5447 PGUSER=sentinel PGPASSWORD=sentinel PGDATABASE=sentinel
#   RESTORE_PORT=5460          port for the fresh instance (host network)
#   EXPECT_CHECKSUM=<md5>      optional: the published run's expected risk checksum
#   PYRUN=docker|local         run the Python steps in the sentinel-v2-dev image (default)
#                              or with the local interpreter (CI)
#   KEEP_RESTORE=1             leave the restored instance running afterwards
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export MSYS_NO_PATHCONV=1
: "${PGHOST:=localhost}" "${PGPORT:=5447}" "${PGUSER:=sentinel}" "${PGPASSWORD:=sentinel}" "${PGDATABASE:=sentinel}"
: "${RESTORE_PORT:=5460}" "${PYRUN:=docker}" "${EXPECT_CHECKSUM:=}"
export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
NAME="sentinel-restore-test"
WORK="${RESTORE_WORKDIR:-$ROOT/ci-artifacts/restore-test}"
mkdir -p "$WORK"
rm -f "$WORK"/sentinel_*.dump "$WORK"/sentinel_*.dump.sha256
SRC_URL="postgresql+psycopg://$PGUSER:$PGPASSWORD@$PGHOST:$PGPORT/$PGDATABASE"
DST_URL="postgresql+psycopg://$PGUSER:$PGPASSWORD@localhost:$RESTORE_PORT/$PGDATABASE"
t() { date -u +%s; }

echo "== 1. backup (ops/backup/backup.sh, the scheduled job's script)"
t0=$(t)
docker run --rm --network host -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE \
  -e BACKUP_ONCE=1 -e BACKUP_DIR=/backups -v "$WORK":/backups -v "$ROOT/ops/backup":/ops:ro \
  postgres:16 sh /ops/backup.sh
DUMP="$(ls -1t "$WORK"/sentinel_*.dump | head -1)"
DUMP_SHA="$(cat "$DUMP.sha256")"
DUMP_SIZE="$(du -h "$DUMP" | cut -f1)"
t1=$(t)

echo "== 2. fresh PostgreSQL 16 instance on :$RESTORE_PORT"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --network host -e POSTGRES_USER="$PGUSER" -e POSTGRES_PASSWORD="$PGPASSWORD" \
  -e POSTGRES_DB="$PGDATABASE" postgres:16 -c port="$RESTORE_PORT" >/dev/null
for _ in $(seq 1 60); do
  docker exec "$NAME" pg_isready -p "$RESTORE_PORT" -U "$PGUSER" >/dev/null 2>&1 && break
  sleep 1
done
sleep 2
FRESH_TABLES="$(docker exec "$NAME" psql -p "$RESTORE_PORT" -U "$PGUSER" -d "$PGDATABASE" -Atc \
  "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"

echo "== 3. pg_restore into the fresh instance (it had $FRESH_TABLES tables)"
t2=$(t)
docker run --rm --network host -e PGPASSWORD -v "$WORK":/backups postgres:16 \
  pg_restore --no-owner --no-privileges --exit-on-error -h localhost -p "$RESTORE_PORT" -U "$PGUSER" \
  -d "$PGDATABASE" "/backups/$(basename "$DUMP")"
t3=$(t)

pyrun() {  # pyrun <env DATABASE_URL> <command...>
  local db="$1"; shift
  if [ "$PYRUN" = "local" ]; then
    (cd "$ROOT/backend_v2" && DATABASE_URL="$db" DATA_DIR="$ROOT/data" "$@")
  else
    docker run --rm --network host -v "$ROOT":/repo -e DATABASE_URL="$db" -e DATA_DIR=/repo/data \
      sentinel-v2-dev sh -c "cd /repo/backend_v2 && $*"
  fi
}

echo "== 4. verify source vs restored"
set +e
if [ "$PYRUN" = "local" ]; then
  (cd "$ROOT/backend_v2" && python scripts/verify_restore.py --source "$SRC_URL" --target "$DST_URL" \
     ${EXPECT_CHECKSUM:+--expect-checksum "$EXPECT_CHECKSUM"} --json "$WORK/verify.json") > "$WORK/verify.log" 2>&1
else
  docker run --rm --network host -v "$ROOT":/repo -v "$WORK":/work sentinel-v2-dev sh -c \
    "cd /repo/backend_v2 && python scripts/verify_restore.py --source '$SRC_URL' --target '$DST_URL' \
     ${EXPECT_CHECKSUM:+--expect-checksum $EXPECT_CHECKSUM} --json /work/verify.json" > "$WORK/verify.log" 2>&1
fi
VERIFY_RC=$?

echo "== 5. API suites against the RESTORED database"
pyrun "$DST_URL" "python -m pytest -q -p no:cacheprovider tests/test_contract.py tests/test_phase12_cutover.py \
  -W ignore::RuntimeWarning" > "$WORK/pytest.log" 2>&1
PYTEST_RC=$?
set -e
PYTEST_LINE="$(grep -E '[0-9]+ (passed|failed)' "$WORK/pytest.log" | tail -1)"

RESULT=FAIL
[ "$VERIFY_RC" = 0 ] && [ "$PYTEST_RC" = 0 ] && RESULT=PASS
[ "${KEEP_RESTORE:-0}" = "1" ] || docker rm -f "$NAME" >/dev/null 2>&1 || true

echo "== 6. report"
# a native Windows interpreter needs Windows paths (MSYS_NO_PATHCONV is on above)
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else echo "$1"; fi; }
PY=""
for c in python3 python; do  # the first that really runs (Windows has a python3 Store stub)
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
done
RESULT="$RESULT" DUMP_NAME="$(basename "$DUMP")" DUMP_SHA="$DUMP_SHA" DUMP_SIZE="$DUMP_SIZE" \
  BACKUP_SECONDS="$((t1 - t0))" RESTORE_SECONDS="$((t3 - t2))" FRESH_TABLES="$FRESH_TABLES" \
  SOURCE="$PGHOST:$PGPORT/$PGDATABASE" TARGET="fresh postgres:16 container on :$RESTORE_PORT" \
  PYTEST_LINE="$PYTEST_LINE" VERIFY_RC="$VERIFY_RC" PYTEST_RC="$PYTEST_RC" \
  "$PY" "$(native "$ROOT/ops/backup/write_report.py")" "$(native "$WORK/verify.json")"   "$(native "$ROOT/docs/restore_test_report.md")"
echo "RESULT: $RESULT"
[ "$RESULT" = PASS ]

#!/bin/sh
# Scheduled database backups (BLUEPRINT.md §11 "Scheduled database backups").
#
# Runs as the `backup` service in docker-compose.yml (image postgres:16, so
# pg_dump matches the server's major version). Every BACKUP_INTERVAL_SECONDS
# (default 86400 = daily) it writes a compressed custom-format dump to
# BACKUP_DIR, checks the dump is readable (pg_restore --list), writes its
# SHA-256 beside it, and keeps the newest BACKUP_KEEP dumps (default 14).
#
# Connection: the standard libpq variables PGHOST, PGPORT, PGUSER,
# PGDATABASE and PGPASSWORD (from the environment -- never in this file).
# One-off run (no loop): BACKUP_ONCE=1 backup.sh
#
# Dumps contain password hashes and case notes: keep BACKUP_DIR on
# restricted storage (docs/security.md). Restore procedure and the executed
# restore test: ops/backup/restore_test.sh, docs/restore_test_report.md.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
KEEP="${BACKUP_KEEP:-14}"
mkdir -p "$BACKUP_DIR"

backup_once() {
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    out="$BACKUP_DIR/sentinel_${stamp}.dump"
    tmp="$out.partial"
    echo "[backup] $(date -u +%FT%TZ) dumping ${PGDATABASE:-?}@${PGHOST:-?}:${PGPORT:-5432} -> $out"
    pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$tmp"
    pg_restore --list "$tmp" > /dev/null            # readable, complete archive
    mv "$tmp" "$out"                                 # only finished dumps get the final name
    sha256sum "$out" | awk '{print $1}' > "$out.sha256"
    echo "[backup] ok $(du -h "$out" | cut -f1) sha256=$(cat "$out.sha256")"
    # retention: newest $KEEP dumps
    ls -1t "$BACKUP_DIR"/sentinel_*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
        rm -f "$old" "$old.sha256"
        echo "[backup] pruned $old"
    done
}

if [ "${BACKUP_ONCE:-0}" = "1" ]; then
    backup_once
    exit 0
fi
while true; do
    backup_once || echo "[backup] FAILED at $(date -u +%FT%TZ); keeping previous dumps"
    sleep "$INTERVAL"
done

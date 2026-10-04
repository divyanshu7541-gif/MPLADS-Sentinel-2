"""
Phase 14 post-deploy gate: the database a deployment serves must be the
verified run-44 state, and the public API in front of it must serve it.

    # write the manifest once, from the database the backup was taken from
    python scripts/postdeploy_gate.py --write-manifest ../ops/deploy/manifest_run44.json

    # after deploying (restoring the backup into the hosted database, or
    # pointing the stack at the existing one), gate on it:
    python scripts/postdeploy_gate.py --manifest ../ops/deploy/manifest_run44.json \
        [--api-url https://api.example.in]

Checks (exit 0 = PASS, 1 = FAIL): the Alembic revision, the published run and
config, the risk_result checksum (must be c4d589e0e0fc40621d4e011a64a7233d for
run 44), every table's row count except the ones the running system writes to
by design (app_user, case_event, audit_review: accounts, case notes and
reviews made after deploy), the serving build, and -- with --api-url -- that the
public API answers /api/health and serves the same CRITICAL + HIGH totals as
risk_result. Only reads. Uses DATABASE_URL; never prints it.

Slim mode (GATE_SLIM=1) is for the production database, which carries only
what the API serves: the row-count check skips the 15 tables that are empty
there (SLIM_EMPTY_TABLES -- the offline pipeline's inputs and intermediates,
AUDIT_REPORT.md §5 E3, plus state_alias, empty everywhere). Every other check
is unchanged: those tables must still exist, the risk_result checksum must be
c4d589e0e0fc40621d4e011a64a7233d and the flagged (tier) counts must match.

    GATE_SLIM=1 python scripts/postdeploy_gate.py --manifest ../ops/deploy/manifest_run44.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_restore import snapshot  # noqa: E402

EXPECTED_CHECKSUM = "c4d589e0e0fc40621d4e011a64a7233d"
# Written by the live system after deploy; their counts may legitimately grow.
LIVE_TABLES = {"app_user", "case_event", "audit_review"}
# GATE_SLIM=1: empty in the production database by design; no row-count check.
SLIM_EMPTY_TABLES = frozenset(
    {
        "raw_row",
        "atypicality_result",
        "work_context",
        "peer_group",
        "payment",
        "work_state",
        "prior_cycle_work",
        "payee_alias",
        "work_geo",
        "work_identity_split",
        "allocation",
        "calamity_consent",
        "macro_reference",
        "control_total",
        "state_alias",
    }
)


def _flagged(url: str) -> dict:
    from sqlalchemy import create_engine, text

    eng = create_engine(url)
    with eng.connect() as c:
        rows = c.execute(
            text(
                "SELECT r.tier, count(*) FROM risk_result r JOIN published_run p "
                "ON p.run_id = r.run_id AND p.default_config_name = r.config_name GROUP BY 1"
            )
        ).all()
    eng.dispose()
    return {t: n for t, n in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", help="manifest JSON to gate against")
    ap.add_argument("--write-manifest", help="write the manifest of the current database here")
    ap.add_argument("--api-url", help="public API base URL, e.g. https://api.example.in")
    args = ap.parse_args()
    url = os.environ["DATABASE_URL"]
    snap = snapshot(url)
    snap["tiers"] = _flagged(url)

    if args.write_manifest:
        Path(args.write_manifest).write_text(json.dumps(snap, indent=2, sort_keys=True, default=str) + "\n")
        print(f"manifest written: {len(snap['tables'])} tables, checksum {snap.get('risk_md5')}")
        return 0

    want = json.loads(Path(args.manifest).read_text())
    slim = os.environ.get("GATE_SLIM", "").strip() == "1"
    skipped = LIVE_TABLES | (SLIM_EMPTY_TABLES if slim else frozenset())
    counts = {t: n for t, n in want["tables"].items() if t not in skipped}
    checks = {
        "alembic_revision": snap["alembic"] == want["alembic"],
        "published_run": snap["published"] == want["published"],
        "risk_checksum": snap.get("risk_md5") == want.get("risk_md5") == EXPECTED_CHECKSUM,
        "same_tables": set(snap["tables"]) == set(want["tables"]),
        "row_counts": all(snap["tables"].get(t) == n for t, n in counts.items()),
        "serving_build": snap.get("serving_build") == want.get("serving_build"),
        "tiers": snap["tiers"] == want["tiers"],
    }
    if args.api_url:
        base = args.api_url.rstrip("/")
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=30) as r:
                checks["api_health"] = r.status == 200
            with urllib.request.urlopen(f"{base}/api/summary", timeout=120) as r:
                s = json.loads(r.read())
            checks["api_serves_run"] = s.get("critical_count") == want["tiers"].get("CRITICAL") and s.get(
                "high_count"
            ) == want["tiers"].get("HIGH")
        except Exception as e:  # noqa: BLE001 -- reported as a failed check
            print(f"api check error: {type(e).__name__}: {e}")
            checks["api_health"] = checks.get("api_health", False)
            checks["api_serves_run"] = False
    mismatched = {t: (n, snap["tables"].get(t)) for t, n in counts.items() if snap["tables"].get(t) != n}
    ok = all(checks.values())
    print(
        json.dumps(
            {
                "result": "PASS" if ok else "FAIL",
                "mode": "slim" if slim else "full",
                "row_count_skipped": sorted(SLIM_EMPTY_TABLES) if slim else [],
                "checks": checks,
                "row_count_mismatches": mismatched,
                "checksum": snap.get("risk_md5"),
                "published": snap["published"],
            },
            indent=2,
            default=str,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

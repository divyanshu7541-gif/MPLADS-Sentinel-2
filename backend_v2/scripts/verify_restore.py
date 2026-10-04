"""
Phase 13: verify a restored database against its source (the executed
restore test, BLUEPRINT.md §11 "Backups ... restore test").

    python scripts/verify_restore.py --source URL --target URL [--json out.json]

Checks, all of which must hold:
  * the same Alembic revision;
  * every table in the source exists in the target with the same row count;
  * published_run is present and points at the same run and config;
  * the risk_result checksum of the published run is identical (and, if
    --expect-checksum is given, equals it);
  * the serving build of the published run is complete with the same counts;
  * the case_event hash chain verifies in the target.
Exit code 0 = PASS, 1 = FAIL. Only reads both databases.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.analytics.atypicality_run import risk_result_checksum  # noqa: E402
from app.audit import case_log  # noqa: E402


def snapshot(url: str) -> dict:
    eng = create_engine(url)
    with Session(eng) as s:
        tables = (
            s.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"))
            .scalars()
            .all()
        )
        counts = {t: s.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables}
        rev = s.execute(text("SELECT version_num FROM alembic_version")).scalar()
        pub = s.execute(text("SELECT run_id, default_config_name FROM published_run WHERE id = 1")).first()
        out = {"alembic": rev, "tables": counts, "published": list(pub) if pub else None}
        if pub:
            out["risk_md5"] = risk_result_checksum(s, pub[0])
            sb = s.execute(
                text("SELECT status, counts FROM serving_build WHERE run_id = :r"), {"r": pub[0]}
            ).first()
            out["serving_build"] = {"status": sb[0], "counts": sb[1]} if sb else None
        out["case_chain"] = case_log.verify_chain(s)
    eng.dispose()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--expect-checksum")
    ap.add_argument("--json")
    args = ap.parse_args()
    src, dst = snapshot(args.source), snapshot(args.target)
    checks = {
        "alembic_revision_equal": src["alembic"] == dst["alembic"],
        "same_tables": set(src["tables"]) == set(dst["tables"]),
        "row_counts_equal": all(dst["tables"].get(t) == n for t, n in src["tables"].items()),
        "published_run_intact": src["published"] is not None and src["published"] == dst["published"],
        "risk_checksum_equal": src.get("risk_md5") is not None and src.get("risk_md5") == dst.get("risk_md5"),
        "serving_build_intact": dst.get("serving_build") is not None
        and dst["serving_build"] == src.get("serving_build")
        and dst["serving_build"]["status"] == "complete",
        "case_chain_valid": dst["case_chain"]["valid"],
    }
    if args.expect_checksum:
        checks["risk_checksum_expected"] = dst.get("risk_md5") == args.expect_checksum
    mismatched = {t: (n, dst["tables"].get(t)) for t, n in src["tables"].items() if dst["tables"].get(t) != n}
    result = {
        "result": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "alembic": dst["alembic"],
        "published_run": dst["published"],
        "risk_md5": dst.get("risk_md5"),
        "tables": len(dst["tables"]),
        "rows_total": sum(dst["tables"].values()),
        "row_count_mismatches": mismatched,
        "case_chain": dst["case_chain"],
        "table_counts": dst["tables"],
    }
    text_out = json.dumps(result, indent=2, default=str)
    if args.json:
        Path(args.json).write_text(text_out, encoding="utf-8")
    print(text_out)
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

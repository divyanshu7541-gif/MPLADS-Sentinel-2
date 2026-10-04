"""
Phase 13 CLI: draw the randomised, tier-stratified audit sample
(BLUEPRINT.md §12; app/audit/sampling.py) for the published run.

    python scripts/draw_audit_sample.py              # random seed, recorded
    python scripts/draw_audit_sample.py --seed 13    # reproducible draw
    python scripts/draw_audit_sample.py --report 1   # precision report for sample 1
    python scripts/draw_audit_sample.py --seed 13 --replace 5
        # archive sample 5 (e.g. drawn on a superseded run) and draw its replacement

Only writes audit_sample / audit_sample_item. Reviews are recorded by
auditor accounts through POST /api/audit/items/{blind_code}/reviews.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audit import sampling  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--report", type=int, default=None, metavar="SAMPLE_ID")
    ap.add_argument("--replace", type=int, default=None, metavar="SAMPLE_ID")
    args = ap.parse_args()
    with get_session_factory()() as db:
        if args.report is not None:
            print(json.dumps(sampling.report(db, args.report), indent=2, default=str))
            return 0
        s = sampling.draw(db, created_by=f"cli:{getpass.getuser()}", seed=args.seed)
        if args.replace is not None:
            old = sampling.archive(
                db, args.replace, f"superseded by sample {s.id}, drawn on run {s.run_id} (the published run)"
            )
            s.design = {**s.design, "replaces_sample": old.id, "replaced_sample_run": old.run_id}
        db.commit()
        print(
            json.dumps({"sample_id": s.id, "run_id": s.run_id, "seed": s.seed, "design": s.design}, indent=2)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

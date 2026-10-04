"""
Phase 10 CLI: payee retyping, work-evidence facts, entity_metric and the
graph, for the published run. Run after scripts/publish_run.py.

    python scripts/run_entities.py            # published run
    python scripts/run_entities.py --run 3

Only reads risk_result / signal_result (checksummed before and after).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import get_session_factory  # noqa: E402
from app.entities.build import build_entities  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=None)
    args = ap.parse_args()
    with get_session_factory()() as session:
        out = build_entities(session, args.run)
        session.commit()
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Phase 12 CLI: build served_work (the read model behind the cutover
endpoints) for the published run. Run after scripts/run_geo.py (it uses
Phase 9's corrected work locations).

    python scripts/run_serving.py            # published run
    python scripts/run_serving.py --run 3

Only reads risk_result (checksummed before and after).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.serving.build import build_serving  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=None)
    args = ap.parse_args()
    with get_session_factory()() as session:
        out = build_serving(session, args.run)
        session.commit()
        session.execute(text("ANALYZE served_work"))
        session.commit()
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

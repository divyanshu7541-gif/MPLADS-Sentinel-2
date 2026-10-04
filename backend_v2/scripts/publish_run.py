"""
Phase 6 CLI: point published_run at a run with the default fusion config
(app/analytics/publish.py). Run after scripts/run_risk.py.

    python scripts/publish_run.py            # latest run with risk_result, fusion.DEFAULT_CONFIG
    python scripts/publish_run.py --run 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analytics import publish  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=None)
    args = ap.parse_args()
    with get_session_factory()() as session:
        row = publish.publish(session, args.run)
        session.commit()
        print(f"published_run -> run {row.run_id}, default config {row.default_config_name} "
              f"({row.default_config_hash[:12]}...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

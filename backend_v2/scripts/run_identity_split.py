"""
Phase 5a step 1 CLI: split portal IDs shared across Houses into two
House-qualified works (app/ingest/p6_identity_split.py). Run after
run_normalize.py and before run_context.py / run_signals.py / run_risk.py.

    python scripts/run_identity_split.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import get_session_factory  # noqa: E402
from app.ingest import p6_identity_split  # noqa: E402
from app.ingest.pipeline import get_or_create_snapshot  # noqa: E402


def main() -> int:
    Session = get_session_factory()
    with Session() as session:
        snapshot_a = get_or_create_snapshot(session, "snapshot_a")
        stats = p6_identity_split.split_shared_portal_ids(session, snapshot_a)
        session.commit()
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

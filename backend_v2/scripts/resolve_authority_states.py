"""
Phase 13.y CLI: set every district authority's state from its own district
(app/ingest/authority_state.py) on an EXISTING database. scripts/run_ingest.py
runs the same function on a fresh build; this is for a database ingested
before the fix. Idempotent: a second run reports 0 corrections.

    python scripts/resolve_authority_states.py

Changes district_authority.state_id only. Analyses built earlier keep the
state they were built with; re-run scripts/run_risk.py (and publish) to
rebuild them on the corrected states.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import get_session_factory  # noqa: E402
from app.ingest.authority_state import resolve_authority_states  # noqa: E402


def main() -> int:
    with get_session_factory()() as session:
        out = resolve_authority_states(session)
        session.commit()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

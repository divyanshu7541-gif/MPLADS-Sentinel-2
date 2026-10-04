"""
Phase 9 CLI: load constituency/district boundaries, locate authorities and
works, then build the map tables for the published run. Run after
scripts/publish_run.py (and scripts/fetch_geo_data.sh for the source files).

    python scripts/run_geo.py             # all steps; boundary/location steps skip if already done
    python scripts/run_geo.py --run 3     # map build for a specific run

Only reads risk_result / signal_result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.geo import boundaries, build, location  # noqa: E402
from app.models.provenance import SourceSnapshot  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=None)
    args = ap.parse_args()
    with get_session_factory()() as session:
        snap = session.execute(select(SourceSnapshot).where(SourceSnapshot.code == "snapshot_a")).scalar_one()
        out = {
            "state_crosswalk_fixed": boundaries.fix_state_crosswalk(session),
            "constituencies": boundaries.load_constituencies(session),
            "districts": boundaries.load_districts(session),
        }
        session.commit()
        out["location"] = location.link_works(session, snap.id)
        out["stored_states"] = location.refresh_stored_states(session)
        session.commit()
        # Fresh planner stats: without them the map query below picks a
        # nested-loop plan over freshly-filled work_geo (minutes, not seconds).
        for t in ("geo_area", "authority_geo", "work_geo"):
            session.execute(text(f"ANALYZE {t}"))
        session.commit()
        out["map_build"] = build.build_map(session, args.run)
        session.commit()
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

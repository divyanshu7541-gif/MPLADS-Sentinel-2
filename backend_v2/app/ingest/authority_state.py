"""
P2 reference: the state each district authority (IDA) is actually in (Phase 13.y fix).

The bug (open since Phase 9): load_district_authorities() gave an authority the
state of the alphabetically-first (IDA_NAME, STATE_NAME) pair in the portal rows.
STATE_NAME is the recommending MP's state, not the authority's, so any
authority that received works from MPs of more than one state (86 of 774 --
Rajya Sabha and nominated members recommend outside their home state) could be
filed under the wrong one: AGRA under Gujarat, BUDAUN under Jammu and Kashmir.
52 authorities carrying 11,783 scored works were wrong, and Phase 3 peer groups
(state level), the Phase 4 signals and Phase 5 risk were all built on it.

The fix resolves the authority's own location, with the same evidence and order
the map uses (app/geo/location.py):
  1. its district name (the IDA name before the first '('; reviewed aliases in
     DISTRICT_ALIASES) matches exactly one LGD district nationally
       -> that district's state;
  2. it matches several (PRATAPGARH, HAMIRPUR, AURANGABAD, ...)
       -> the candidate state with the most Lok Sabha rows for the authority;
  3. no LGD match
       -> the state with the most Lok Sabha rows (a Lok Sabha MP's STATE_NAME is
          the state of the constituency the work is in), else the most rows of
          any House; ties go to the alphabetically first name, so it is
          deterministic.
It needs the LGD district file that scripts/fetch_geo_data.sh fetches (CI
fetches it before ingest). Idempotent: re-running it on an existing database
corrects the stored states in place and reports each correction.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import pyarrow.parquet as pq
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..geo.boundaries import DISTRICT_FILE, LGD_STATE_ALIASES, norm
from ..geo.location import DISTRICT_ALIASES
from ..models.reference import DistrictAuthority, State

_ROWS_SQL = text(
    """
    SELECT TRIM(rr.data->>'IDA_NAME') AS ida, TRIM(rr.data->>'STATE_NAME') AS state,
           rf.filename ILIKE '%LokSabha%' AS ls, count(*) AS n
    FROM raw_row rr JOIN raw_file rf ON rf.id = rr.raw_file_id
    JOIN source_snapshot ss ON ss.id = rf.source_snapshot_id
    WHERE ss.code = 'snapshot_a' AND rr.data ? 'IDA_NAME' AND rr.data ? 'STATE_NAME'
    GROUP BY 1, 2, 3
    """
)


def _modal(counts: Counter, among: set[str] | None = None) -> str | None:
    items = [(n, s) for s, n in counts.items() if among is None or s in among]
    if not items:
        return None
    return min(items, key=lambda t: (-t[0], t[1]))[1]


def lgd_district_states(state_names: list[str]) -> dict[str, set[str]]:
    """Normalised LGD district name -> the set of OUR state names it occurs in."""
    ours = {norm(s): s for s in state_names}
    t = pq.read_table(DISTRICT_FILE, columns=["dtname", "stname"])
    out: dict[str, set[str]] = defaultdict(set)
    for dt, st in zip(t.column("dtname").to_pylist(), t.column("stname").to_pylist()):
        our = LGD_STATE_ALIASES.get(norm(st)) or ours.get(norm(st))
        if our:
            out[norm(dt)].add(our)
    return out


def resolve_state(
    district_key: str | None, ls_rows: Counter, all_rows: Counter, lgd: dict[str, set[str]]
) -> tuple[str | None, str]:
    """(state name, method) for one authority. Pure: unit-tested directly."""
    key = norm(district_key)
    cands = lgd.get(DISTRICT_ALIASES.get(key, key), set())
    if len(cands) == 1:
        return next(iter(cands)), "lgd_unique_name"
    if len(cands) > 1:
        pick = _modal(ls_rows, cands) or _modal(all_rows, cands)
        if pick:
            return pick, "lgd_name_modal_ls_state"
    pick = _modal(ls_rows) or _modal(all_rows)
    return pick, ("modal_ls_state" if _modal(ls_rows) else "modal_any_state") if pick else "unresolved"


def resolve_authority_states(session: Session) -> dict:
    states = {s.name: s.id for s in session.execute(select(State)).scalars()}
    by_norm = {norm(n): n for n in states}
    lgd = lgd_district_states(list(states))

    ls_rows: dict[str, Counter] = defaultdict(Counter)
    all_rows: dict[str, Counter] = defaultdict(Counter)
    for ida, st, ls, n in session.execute(_ROWS_SQL):
        name = by_norm.get(norm(st))
        if not ida or not name:
            continue
        all_rows[ida][name] += n
        if ls:
            ls_rows[ida][name] += n

    methods, corrected = Counter(), []
    for a in session.execute(select(DistrictAuthority).order_by(DistrictAuthority.id)).scalars():
        state, method = resolve_state(a.district_key, ls_rows[a.ida_name], all_rows[a.ida_name], lgd)
        methods[method] += 1
        new_id = states.get(state) if state else None
        if new_id != a.state_id:
            corrected.append(
                {
                    "district_authority_id": a.id,
                    "ida_name": a.ida_name,
                    "from": next((n for n, i in states.items() if i == a.state_id), None),
                    "to": state,
                    "method": method,
                }
            )
            a.state_id = new_id
    session.flush()
    return {"methods": dict(methods), "corrected": len(corrected), "corrections": corrected}

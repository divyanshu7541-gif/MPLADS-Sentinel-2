"""
Reference-table loaders explicitly required by the Phase 1 brief's "Geo
reference load" bullet: state names/aliases, and district keys parsed from
IDA names. `person`, `tenure`, `constituency` and `activity_type` get their
Alembic-migration schema this phase (per the brief's table list) but are
left unpopulated -- entity resolution (roster matching, tenure dates) is a
P5 pipeline stage (BLUEPRINT.md §5), a later phase.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.provenance import SourceSnapshot
from ..models.reference import DistrictAuthority, State, StateAlias

# Known historical-name variants in the macro/prior-cycle tables
# (BLUEPRINT.md §4: "Crosswalk for names in old parliamentary tables
# (A & N Island, D & N Haveli, Daman & Diu, J&K)").
KNOWN_ALIASES: dict[str, str] = {
    "a & n island": "Andaman And Nicobar Islands",
    "a & n islands": "Andaman And Nicobar Islands",
    "d & n haveli": "The Dadra And Nagar Haveli And Daman And Diu",
    "daman & diu": "The Dadra And Nagar Haveli And Daman And Diu",
    "j&k": "Jammu And Kashmir",
    "jammu & kashmir": "Jammu And Kashmir",
}


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("&", "and")).strip().lower()


def load_states(session: Session, data_dir: Path) -> dict[str, int]:
    """Seeds `state` from states.csv. Returns {normalized_name: state.id}."""
    df = pd.read_csv(data_dir / "raw/snapshot_a/states.csv", dtype=str)
    existing = {s.name: s.id for s in session.execute(select(State)).scalars()}
    name_to_id: dict[str, int] = {}

    for _, row in df.iterrows():
        name = row["STATE_NAME"].strip()
        portal_id = int(row["STATE_ID"])
        if name in existing:
            name_to_id[_normalize(name)] = existing[name]
            continue
        state = State(name=name, portal_state_id=portal_id)
        session.add(state)
        session.flush()
        existing[name] = state.id
        name_to_id[_normalize(name)] = state.id

    return name_to_id


def load_state_aliases(session: Session, data_dir: Path, name_to_id: dict[str, int]) -> dict[str, int | None]:
    """Scans the small macro reference files for state-name-like values that
    don't exactly match a canonical `state.name`, and records them in
    state_alias when resolvable via KNOWN_ALIASES or normalization.
    Genuinely unmatched candidates are returned (not silently dropped) so
    the report can list them, per the brief's "unmatched names listed
    openly, not dropped" instruction for the geo crosswalk (applied here in
    the same spirit for state aliases)."""
    candidates: set[str] = set()
    for fname, col in [
        ("raw/macro/RS-Session-251-AU3002-Annexure-I.csv", "State"),
        ("raw/macro/RS_Session_247_AS_175.csv", "States/UTs"),
    ]:
        df = pd.read_csv(data_dir / fname, dtype=str)
        candidates.update(v.strip() for v in df[col].dropna() if v.strip() and "total" not in v.lower())

    existing_aliases = {(a.alias, a.state_id) for a in session.execute(select(StateAlias)).scalars()}
    unmatched: list[str] = []

    for raw_name in sorted(candidates):
        norm = _normalize(raw_name)
        if norm in name_to_id:
            continue  # exact/normalized match to a canonical name -- no alias row needed
        target_name = KNOWN_ALIASES.get(norm)
        state_id = name_to_id.get(_normalize(target_name)) if target_name else None
        if state_id is None:
            unmatched.append(raw_name)
            continue
        if (raw_name, state_id) in existing_aliases:
            continue
        session.add(StateAlias(state_id=state_id, alias=raw_name, source="macro_reference"))

    return {"unmatched": unmatched, "candidate_count": len(candidates)}


def load_district_authorities(
    session: Session,
    ida_state_pairs: set[tuple[str, str]],
    snapshot: SourceSnapshot,
) -> dict:
    """District key is parsed as the text before the IDA name's first '('
    (e.g. "CHIKKAMAGALURU(DEPUTY COMMISSIONER CHIKMAGALUR_IDA)" ->
    "CHIKKAMAGALURU"), per the brief's "district keys parsed from IDA
    names" instruction.

    The state is NOT set here (Phase 13.y fix). A portal row's STATE_NAME is
    the recommending MP's state, so taking one of an authority's pairs (the
    alphabetically first, as before) filed 52 authorities under the wrong
    state. app/ingest/authority_state.resolve_authority_states() sets it from
    the authority's own district, right after this load (scripts/run_ingest.py)."""
    existing = {d.ida_name for d in session.execute(select(DistrictAuthority)).scalars()}
    added = 0

    for ida_name in sorted({ida for ida, _ in ida_state_pairs}):
        if ida_name in existing:
            continue
        district_key = ida_name.split("(", 1)[0].strip() or None
        session.add(
            DistrictAuthority(
                ida_name=ida_name,
                district_key=district_key,
                state_id=None,
                first_seen_snapshot_id=snapshot.id,
            )
        )
        existing.add(ida_name)
        added += 1

    return {"added": added, "total_candidates": len({ida for ida, _ in ida_state_pairs})}

"""
P5 (partial): person/constituency/tenure population from the MP roster and
allocation files. This is a prerequisite for `allocation` (grain is
(tenure, snapshot), BLUEPRINT.md §4) and for backfilling `work.tenure_id`
-- Phase 1 left person/tenure/constituency as schema-only.

Restriction carried over from Phase 1 (confirmed with the user 2026-09-24):
tenure is only created for the two live labels "18th Lok Sabha" and
"Sitting MP" -- "Nominated Rajya Sabha" rows are registered as `person`
(they're real roster entries) but get no `tenure` row this phase. A work
whose MP is nominated-RS will have `tenure_id = NULL`, which is correct
and documented, not a bug.

Person-to-roster matching is exact-match on normalize_name(name) against
normalize_name(CAPTION) -- verified 100% match rate on Snapshot A
(2026-09-24 research); no fuzzy matching is needed or used here.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.provenance import SourceSnapshot
from ..models.reference import Constituency, Person, State, Tenure
from .normalize import normalize_name, parse_tenure_timestamp

TENURE_LABELS = ("18th Lok Sabha", "Sitting MP")


def load_persons(session: Session, data_dir: Path, snapshot: SourceSnapshot) -> dict[str, int]:
    """Returns {normalize_name(caption): person_id}."""
    existing = {p.id: p for p in session.execute(select(Person)).scalars()}
    state_by_portal_id = {s.portal_state_id: s.id for s in session.execute(select(State)).scalars()}

    df = pd.read_csv(data_dir / "raw/snapshot_a/mp_names_all_states.csv", dtype=str, keep_default_na=False)
    name_to_id: dict[str, int] = {}
    for _, row in df.iterrows():
        pid = int(row["ID"])
        caption = row["CAPTION"].strip()
        name_to_id[normalize_name(caption)] = pid
        if pid in existing:
            continue
        state_id = state_by_portal_id.get(int(row["_STATE_ID"])) if row["_STATE_ID"] else None
        session.add(
            Person(
                id=pid,
                caption=caption,
                state_id=state_id,
                house_raw_code=row["_HOUSE"] or None,
                source_snapshot_id=snapshot.id,
            )
        )
    session.flush()
    return name_to_id


def load_constituencies(session: Session, data_dir: Path, snapshot: SourceSnapshot) -> dict[str, int]:
    """LS only (BLUEPRINT.md §9/§15: Rajya Sabha members have no
    constituency). Returns {(state_norm, name_norm): constituency_id}."""
    existing = {(c.state_id, c.name): c.id for c in session.execute(select(Constituency)).scalars()}
    state_by_name = {normalize_name(s.name): s.id for s in session.execute(select(State)).scalars()}

    df = pd.read_csv(
        data_dir / "raw/snapshot_a/mp_allocation_LokSabha_alltenures.csv",
        dtype=str,
        keep_default_na=False,
    )
    df = df[df["CONSTITUENCY"] != ""]
    pairs = df[["STATE_NAME", "CONSTITUENCY"]].drop_duplicates()

    key_to_id: dict[tuple[str, str], int] = {}
    for _, row in pairs.iterrows():
        state_id = state_by_name.get(normalize_name(row["STATE_NAME"]))
        name = row["CONSTITUENCY"].strip()
        cache_key = (state_id, name)
        if cache_key in existing:
            key_to_id[(normalize_name(row["STATE_NAME"]), normalize_name(name))] = existing[cache_key]
            continue
        c = Constituency(name=name, state_id=state_id, house="LS", source_snapshot_id=snapshot.id)
        session.add(c)
        session.flush()
        existing[cache_key] = c.id
        key_to_id[(normalize_name(row["STATE_NAME"]), normalize_name(name))] = c.id
    return key_to_id


def load_tenures(
    session: Session,
    data_dir: Path,
    snapshot: SourceSnapshot,
    name_to_person_id: dict[str, int],
    constituency_by_key: dict[tuple[str, str], int],
) -> tuple[dict[int, int], list[str]]:
    """Returns ({person_id: tenure_id}, unmatched_names). Skips rows whose
    TENURE label isn't one of the two live labels (see module docstring).
    Unmatched names are reported, never silently dropped or fuzzy-merged
    (Phase 2 brief: "fuzzy/unmatched names queued for manual review, never
    auto-merged") -- verified 0 in practice (100% exact-normalized-name
    match on Snapshot A), but the count is still surfaced, not assumed."""
    existing = {t.person_id: t.id for t in session.execute(select(Tenure)).scalars()}
    person_to_tenure: dict[int, int] = dict(existing)
    unmatched: list[str] = []

    for house, path in [
        ("LS", data_dir / "raw/snapshot_a/mp_allocation_LokSabha_alltenures.csv"),
        ("RS", data_dir / "raw/snapshot_a/mp_allocation_RajyaSabha_alltenures.csv"),
    ]:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df = df[df["MP_NAME"] != ""]
        for _, row in df.iterrows():
            label = row["TENURE"].strip()
            if label not in TENURE_LABELS:
                continue
            person_id = name_to_person_id.get(normalize_name(row["MP_NAME"]))
            if person_id is None:
                unmatched.append(row["MP_NAME"])
                continue
            if person_id in existing:
                continue
            constituency_id = None
            if house == "LS":
                constituency_id = constituency_by_key.get(
                    (normalize_name(row["STATE_NAME"]), normalize_name(row["CONSTITUENCY"]))
                )
            tenure = Tenure(
                person_id=person_id,
                tenure_label=label,
                house=house,
                constituency_id=constituency_id,
                start_date=parse_tenure_timestamp(row["TENURE_START_DATE"]),
                end_date=parse_tenure_timestamp(row["TENURE_END_DATE"]),
                allocated_amount=None,
                source_snapshot_id=snapshot.id,
            )
            session.add(tenure)
            session.flush()
            existing[person_id] = tenure.id
            person_to_tenure[person_id] = tenure.id

    return person_to_tenure, unmatched

"""
Phase 9 location: where each district authority and each work actually is.

Until Phase 13.y the stored district_authority.state_id was the state of the
first MP row seen for that authority (e.g. AGRA stored under Gujarat), and
this module's independent resolution was the map's correction. The ingest now
stores the authority's own state (app/ingest/authority_state.py, the same
evidence and order as below), so the two agree for every authority; this
module still resolves each authority's DISTRICT polygon, and its state is a
cross-check (tests/test_phase13y_authority_state.py fails if they diverge).

Authority -> LGD district, by normalised district name:
  1. exactly one LGD district nationally with that name       -> that district
  2. several (e.g. PRATAPGARH in UP and Rajasthan)             -> the one in the
     state most of the authority's Lok Sabha works' constituencies are in;
     else the one in the stored state; else unresolved
  3. none                                                      -> no district;
     state = the state most of its Lok Sabha works' constituencies are in,
     else unresolved
An authority with no resolved state is "unlocated": its works are counted in
national totals under an explicit unlocated bucket, never assigned a guess.

Work -> constituency: Lok Sabha works only, from the work's own LS file rows
(CONSTITUENCY column; all 542 names match the constituency table exactly).
Rajya Sabha members have no constituency.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..ingest.db_utils import bulk_insert
from ..models.geo import AuthorityGeo, GeoArea, WorkGeo
from ..models.reference import Constituency, DistrictAuthority, State
from .boundaries import norm

# Reviewed spelling/rename aliases: our IDA-derived district key -> the LGD
# 2024 district name, normalised. Each was checked against the LGD file in
# the same state (docs/phase9_report.md). Not aliased, on purpose: districts
# created after the LGD 2024 snapshot (Mauganj, Maihar, Pandhurna -- MP 2023;
# Vav-Tharad -- Gujarat 2025). They keep their state but get no district
# polygon rather than their parent district's.
DISTRICT_ALIASES: dict[str, str] = {
    norm("Ahilyanagar"): norm("Ahmednagar"),  # renamed 2024
    norm("Ahmedabad"): norm("Ahmadabad"),
    norm("Ananthapuramu"): norm("Anantapur"),
    norm("Anugola"): norm("Anugul"),
    norm("Balodabazar-Bhatapara"): norm("Baloda Bazar"),
    norm("Balrampur-Ramanujganj"): norm("Balrampur"),
    norm("Baragada"): norm("Bargarh"),
    norm("Bengaluru South"): norm("Ramanagara"),  # Ramanagara renamed Bengaluru South 2024
    norm("Chamarajanagar"): norm("Chamarajanagara"),
    norm("Charkhi Dadri"): norm("Charki Dadri"),
    norm("Chhatrapati Sambhajinagar"): norm("Aurangabad"),  # Aurangabad (Maharashtra) renamed 2023
    norm("Dahod"): norm("Dohad"),
    norm("Dakshin Bastar Dantewada"): norm("Dantewada"),
    norm("Dakshin Dinajpur"): norm("Dinajpur Dakshin"),
    norm("Dangs"): norm("Dang"),
    norm("Davanagere"): norm("Davangere"),
    norm("Debagada"): norm("Deogarh"),
    norm("Dharashiv"): norm("Osmanabad"),  # renamed 2023
    norm("Dr. B.R. Ambedkar Konaseema"): norm("Konaseema"),
    norm("Gaurela-Pendra-Marwahi"): norm("Gaurella Pendra Marwahi"),
    norm("Jajpur"): norm("Jajapur"),
    norm("KAIMUR"): norm("Kaimur (Bhabua)"),
    norm("Kabeerdham"): norm("Kabirdham"),
    norm("Kancheepuram"): norm("Kanchipuram"),
    norm("Kandhamala"): norm("Kandhamal"),
    norm("Kataka"): norm("Cuttack"),
    norm("Kendrapada"): norm("Kendrapara"),
    norm("Khairagarh-Chhuikhadan-Gandai"): norm("Khairgarh Chhuikhadan Gandai"),
    norm("Khandwa"): norm("East Nimar"),
    norm("Lahaul And Spiti"): norm("Lahul And Spiti"),
    norm("Mahrajganj"): norm("Maharajganj"),
    norm("Malda"): norm("Maldah"),
    norm("Manendragarh-Chirmiri-Bharatpur"): norm("Manendragarh Chirimiri Bharatpur"),
    norm("Narsimhapur"): norm("Narsinghpur"),
    norm("Nayagada"): norm("Nayagarh"),
    norm("North 24 Parganas"): norm("24 Paraganas North"),
    norm("South 24 Parganas"): norm("24 Paraganas South"),
    norm("Paschim Medinipur"): norm("Medinipur West"),
    norm("Purba Medinipur"): norm("Medinipur East"),
    norm("Puducherry"): norm("Pondicherry"),
    norm("Sant Kabir Nagar"): norm("Sant Kabeer Nagar"),
    norm("Shrawasti"): norm("Shravasti"),
    norm("Siaha"): norm("Saiha"),
    norm("Sri Potti Sriramulu Nellore"): norm("SPSR Nellore"),
    norm("Sribhumi"): norm("Karimganj"),  # renamed Nov 2024
    norm("Subarnapur"): norm("Sonepur"),
    norm("Sundaragada"): norm("Sundargarh"),
    norm("Thoothukkudi"): norm("Tuticorin"),
    norm("Udham Singh Nagar"): norm("Udam Singh Nagar"),
    norm("Uttar Bastar Kanker"): norm("Kanker"),
    norm("Uttar Dinajpur"): norm("Dinajpur Uttar"),
    norm("Vijayanagara"): norm("Vijayanagar"),
    norm("Viluppuram"): norm("Villupuram"),
    norm("Visakhapatnam"): norm("Visakhapatanam"),
    norm("Y.S.R. Kadapa"): norm("Y.S.R."),
}

_LS_CONSTITUENCY_SQL = text(
    """
    SELECT DISTINCT TRIM(rr.data->>'WORK_RECOMMENDATION_DTL_ID') AS portal_id,
           rr.data->>'CONSTITUENCY' AS constituency, rr.data->>'STATE_NAME' AS state
    FROM raw_row rr JOIN raw_file rf ON rf.id = rr.raw_file_id
    WHERE rf.source_snapshot_id = :snap AND rf.filename ILIKE '%LokSabha%' AND rr.data ? 'CONSTITUENCY'
    """
)


def resolve_authorities(session: Session, snapshot_id: int) -> dict:
    if session.execute(select(AuthorityGeo.district_authority_id)).first():
        return {"skipped": "authority_geo already built"}
    states = {s.id: s.name for s in session.execute(select(State)).scalars()}
    state_id_by_name = {v: k for k, v in states.items()}
    districts = session.execute(select(GeoArea).where(GeoArea.level == "district")).scalars().all()
    by_name: dict[str, list[GeoArea]] = defaultdict(list)
    for d in districts:
        by_name[d.attrs.get("name_norm")].append(d)

    # State of the constituencies of each authority's Lok Sabha works.
    ls = (
        pd.read_sql(
            text(
                """
        SELECT w.district_authority_id AS da, s.name AS st
        FROM work_geo_ls_tmp t JOIN work w ON w.work_key = t.work_key
        JOIN constituency c ON c.id = t.constituency_id JOIN state s ON s.id = c.state_id
        """
            ),
            session.connection(),
        )
        if _has_tmp(session)
        else pd.DataFrame(columns=["da", "st"])
    )
    ls_state: dict[int, Counter] = defaultdict(Counter)
    for da, st in zip(ls["da"], ls["st"]):
        if pd.notna(da):
            ls_state[int(da)][st] += 1

    rows, counts = [], Counter()
    for a in session.execute(select(DistrictAuthority)).scalars():
        stored = states.get(a.state_id)
        modal = ls_state[a.id].most_common(1)[0][0] if ls_state.get(a.id) else None
        key = DISTRICT_ALIASES.get(norm(a.district_key), norm(a.district_key))
        cands = by_name.get(key, [])
        area, method, state = None, "unresolved", None
        if len(cands) == 1:
            area, method = cands[0], "lgd_unique_name"
        elif len(cands) > 1:
            for pref, how in ((modal, "lgd_name_ls_constituency_state"), (stored, "lgd_name_stored_state")):
                pick = [c for c in cands if c.attrs.get("state") == pref]
                if pref and len(pick) == 1:
                    area, method = pick[0], how
                    break
            else:
                method = "ambiguous_district_name"
        if area is not None:
            state = area.attrs.get("state")
        elif modal:
            state, method = modal, (method if method == "ambiguous_district_name" else "no_district_ls_state")
        counts[method] += 1
        rows.append(
            {
                "district_authority_id": a.id,
                "district_key": a.district_key,
                "stored_state_id": a.state_id,
                "resolved_state_id": state_id_by_name.get(state),
                "district_area_id": area.id if area else None,
                "method": method,
                "state_differs": bool(state and stored and state != stored),
                "detail": {
                    "candidates": [c.attrs.get("state") for c in cands],
                    "ls_modal_state": modal,
                    "stored_state": stored,
                },
            }
        )
    bulk_insert(session, AuthorityGeo, rows)
    session.flush()
    return {
        "methods": dict(counts),
        "state_differs": sum(r["state_differs"] for r in rows),
        "authorities": len(rows),
    }


def _has_tmp(session: Session) -> bool:
    return bool(session.execute(text("SELECT to_regclass('pg_temp.work_geo_ls_tmp')")).scalar())


def link_works(session: Session, snapshot_id: int) -> dict:
    """work_geo for every Snapshot A work: constituency (LS, from its own file
    rows) and district/state from its authority's resolved location."""
    if session.execute(select(WorkGeo.work_key)).first():
        return {"skipped": "work_geo already built"}
    constituency_id = {
        (s_name, c.name): c.id
        for c, s_name in session.execute(
            select(Constituency, State.name)
            .join(State, State.id == Constituency.state_id)
            .where(Constituency.house == "LS")
        ).all()
    }
    pc_area = {
        a.attrs.get("constituency_id"): a.id
        for a in session.execute(select(GeoArea).where(GeoArea.level == "constituency")).scalars()
    }
    raw = pd.read_sql(_LS_CONSTITUENCY_SQL, session.connection(), params={"snap": snapshot_id})
    per_key = raw.groupby("portal_id").agg(
        n=("constituency", "nunique"), c=("constituency", "first"), s=("state", "first")
    )

    works = pd.read_sql(
        text(
            "SELECT w.work_key, w.portal_id, w.house, w.district_authority_id FROM work w "
            "JOIN work_state ws ON ws.work_key = w.work_key AND ws.source_snapshot_id = :snap"
        ),
        session.connection(),
        params={"snap": snapshot_id},
    )

    # First pass: constituencies (needed to resolve authorities' states).
    const_ids, statuses = [], []
    for wk, pid, house in zip(works["work_key"], works["portal_id"], works["house"]):
        if house != "LS":
            const_ids.append(None)
            statuses.append("not_applicable_rajya_sabha")
            continue
        r = per_key.loc[pid] if pid in per_key.index else None
        if r is None or pd.isna(r["c"]):
            const_ids.append(None)
            statuses.append("no_constituency_in_source")
        elif r["n"] > 1:
            const_ids.append(None)
            statuses.append("conflicting_constituencies_in_source")
        else:
            cid = constituency_id.get((r["s"], r["c"]))
            const_ids.append(cid)
            status = "located" if cid in pc_area else ("no_boundary" if cid else "unknown_constituency")
            statuses.append(status)
    works["constituency_id"] = const_ids
    works["constituency_status"] = statuses

    session.execute(
        text("CREATE TEMP TABLE work_geo_ls_tmp (work_key text, constituency_id int) ON COMMIT DROP")
    )
    tmp = works.dropna(subset=["constituency_id"])
    if len(tmp):
        session.execute(
            text("INSERT INTO work_geo_ls_tmp VALUES (:k, :c)"),
            [{"k": k, "c": int(c)} for k, c in zip(tmp["work_key"], tmp["constituency_id"])],
        )
    auth = resolve_authorities(session, snapshot_id)

    ag = {a.district_authority_id: a for a in session.execute(select(AuthorityGeo)).scalars()}
    rows = []
    for r in works.itertuples(index=False):
        a = ag.get(r.district_authority_id) if pd.notna(r.district_authority_id) else None
        cid = None if pd.isna(r.constituency_id) else int(r.constituency_id)
        rows.append(
            {
                "work_key": r.work_key,
                "house": r.house,
                "constituency_id": cid,
                "constituency_area_id": pc_area.get(cid) if cid else None,
                "constituency_status": r.constituency_status,
                "district_authority_id": None if a is None else a.district_authority_id,
                "district_area_id": None if a is None else a.district_area_id,
                "location_state_id": None if a is None else a.resolved_state_id,
                "location_method": "no_authority" if a is None else a.method,
            }
        )
    bulk_insert(session, WorkGeo, rows)
    session.flush()
    return {
        "works": len(rows),
        "constituency_status": dict(Counter(works["constituency_status"])),
        "authorities": auth,
    }


def refresh_stored_states(session: Session) -> dict:
    """authority_geo keeps a copy of the stored state it was compared with.
    After the ingest corrects stored states (Phase 13.y), refresh that copy
    and state_differs; the resolved location itself does not change."""
    n = session.execute(
        text(
            """
        UPDATE authority_geo ag
        SET stored_state_id = da.state_id,
            state_differs = (ag.resolved_state_id IS NOT NULL AND da.state_id IS NOT NULL
                             AND ag.resolved_state_id <> da.state_id)
        FROM district_authority da
        WHERE da.id = ag.district_authority_id
          AND (ag.stored_state_id IS DISTINCT FROM da.state_id
               OR ag.state_differs <> (ag.resolved_state_id IS NOT NULL AND da.state_id IS NOT NULL
                                       AND ag.resolved_state_id <> da.state_id))
        """
        )
    ).rowcount
    differs = session.execute(text("SELECT count(*) FROM authority_geo WHERE state_differs")).scalar_one()
    return {"refreshed": n, "state_differs": differs}

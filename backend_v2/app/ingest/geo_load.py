"""
Geo reference load (BLUEPRINT.md §15). Source picked from the four
candidates BLUEPRINT.md §15 lists: DataMeet maps (github.com/datameet/maps),
licensed CC BY 4.0 (MIT for code, CC BY 4.0 for data, per the repo's own
README) -- chosen because it provides both the Survey-of-India-derived
national outline BLUEPRINT.md's rule requires ("use the official external
boundary for the national outline") and state-level polygons, under one
clearly-stated open licence, satisfying the brief's "licence check before
use" instruction.

National: Country/india-soi.geojson ("Dissolved shapefiles from the
official Indian shapefile data available" -- Survey of India / Census of
India 2011, per the file's own `Source` property, verified after download).
State: States/Admin2.shp (36 states/UTs, field ST_NM, WGS84).

geo_data_src/ (backend_v2/app/geo_data_src/) holds the fetched files; it is
gitignored raw material for this loader, not application code.
"""

from __future__ import annotations

import json
from pathlib import Path

import shapefile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.geo import GeoArea, GeoNameCrosswalk
from ..models.reference import State
from .reference_load import _normalize

SOURCE = "DataMeet maps (github.com/datameet/maps)"
LICENCE = "CC BY 4.0"
VERSION = "fetched 2026-09-24 from github.com/datameet/maps @ master"

GEO_DATA_DIR = Path(__file__).resolve().parents[1] / "geo_data_src"


def _slug(name: str) -> str:
    return _normalize(name).replace(" ", "_")


def load_national(session: Session) -> GeoArea:
    existing = session.execute(
        select(GeoArea).where(GeoArea.level == "national", GeoArea.key == "IN")
    ).scalar_one_or_none()
    if existing:
        return existing

    with open(GEO_DATA_DIR / "india-soi.geojson", encoding="utf-8") as f:
        fc = json.load(f)
    geometry = fc["features"][0]["geometry"]

    area = GeoArea(
        level="national",
        key="IN",
        name="India",
        parent_id=None,
        geometry=geometry,
        source=f"{SOURCE}, Country/india-soi.geojson (Survey of India-derived)",
        licence=LICENCE,
        version=VERSION,
    )
    session.add(area)
    session.flush()
    return area


def load_states(session: Session, national: GeoArea) -> dict[str, GeoArea]:
    """Returns {normalized_state_name: GeoArea} for every state polygon
    loaded (existing or newly inserted this run)."""
    existing = {a.key: a for a in session.execute(select(GeoArea).where(GeoArea.level == "state")).scalars()}

    sf = shapefile.Reader(str(GEO_DATA_DIR / "Admin2"))
    result: dict[str, GeoArea] = {}
    for shape_rec in sf.shapeRecords():
        name = shape_rec.record["ST_NM"].strip()
        key = _slug(name)
        if key in existing:
            result[key] = existing[key]
            continue
        geometry = shape_rec.shape.__geo_interface__
        area = GeoArea(
            level="state",
            key=key,
            name=name,
            parent_id=national.id,
            geometry=geometry,
            source=f"{SOURCE}, States/Admin2.shp",
            licence=LICENCE,
            version=VERSION,
        )
        session.add(area)
        session.flush()
        existing[key] = area
        result[key] = area

    return result


def build_state_crosswalk(session: Session, state_geo_by_key: dict[str, GeoArea]) -> dict:
    """Matches every portal state (from the `state` table, seeded from
    states.csv) against the geo_area state polygons by normalized name.
    Unmatched portal states are still inserted (geo_area_id=NULL,
    method='unmatched') so the match rate is reported, never silently
    dropped, per the Phase 1 brief."""
    existing_rows = session.execute(
        select(GeoNameCrosswalk).where(GeoNameCrosswalk.level == "state")
    ).scalars()
    existing = {c.portal_name for c in existing_rows}
    portal_states = session.execute(select(State)).scalars().all()

    matched = 0
    unmatched: list[str] = []
    for state in portal_states:
        if state.name in existing:
            continue
        key = _slug(state.name)
        geo = state_geo_by_key.get(key)
        if geo is not None:
            session.add(
                GeoNameCrosswalk(
                    portal_name=state.name, level="state", geo_area_id=geo.id, method="normalized"
                )
            )
            matched += 1
        else:
            session.add(
                GeoNameCrosswalk(portal_name=state.name, level="state", geo_area_id=None, method="unmatched")
            )
            unmatched.append(state.name)

    total = matched + len(unmatched)
    return {
        "matched": matched,
        "unmatched": unmatched,
        "total": total,
        "match_rate_pct": round(100 * matched / total, 1) if total else 0.0,
    }

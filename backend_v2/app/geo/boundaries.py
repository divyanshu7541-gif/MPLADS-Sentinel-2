"""
Phase 9 boundaries: Lok Sabha constituencies and districts into geo_area,
with reviewed name crosswalks. Sources, licences and every review decision:
docs/phase9_report.md.

Rules (owner decisions, Phase 9 inspection):
- Constituencies: DataMeet india_pc_2019_simplified (CC0 1.0). Assam (2023)
  and Jammu and Kashmir (2022) were re-delimited and no source has current
  boundaries, so their constituencies get NO polygon and NO point
  ("redelimited_boundary_not_available"); they still count in every total.
- Districts: india-geodata LGD_Districts (CC0-1.0 / CC-BY-4.0, LGD 2024).
- Each area's marker point is shapely's representative_point() of its own
  licensed polygon: guaranteed inside it, deterministic, never a geocode.
- Names are matched exactly after normalisation, then by an explicit,
  hand-reviewed alias table keyed by the source's (state, pc_no); anything
  else is reported unmatched, never fuzzy-matched at runtime.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pyarrow.parquet as pq
from shapely import wkb
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import polylabel, unary_union
from shapely.validation import make_valid
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.geo import GeoArea, GeoNameCrosswalk
from ..models.reference import Constituency, State

GEO_DIR = Path(__file__).resolve().parents[1] / "geo_data_src"
PC_FILE = GEO_DIR / "india_pc_2019_simplified.geojson"
DISTRICT_FILE = GEO_DIR / "LGD_Districts.parquet"

PC_SOURCE = "DataMeet maps, parliamentary-constituencies/india_pc_2019_simplified.geojson"
PC_LICENCE = "CC0 1.0"
PC_VERSION = "2019 boundaries (2008 delimitation); sha256 54840686c3c5"
DISTRICT_SOURCE = "india-geodata release admin/districts, LGD_Districts.parquet (Local Government Directory)"
DISTRICT_LICENCE = "CC0-1.0 / CC-BY-4.0"
DISTRICT_VERSION = "LGD 2024 snapshot (metadata last_updated 2024-08-15); sha256 c205da56ce05"
DISTRICT_SIMPLIFY_DEG = 0.001  # ~100 m; visualisation only, keeps geo_area small

# Seats whose boundaries changed after the only available source's vintage.
REDELIMITED = {
    "Assam": "Assam re-delimited in 2023 (in force for the 2024 election); no source has the new boundaries",
    "Jammu And Kashmir": "Jammu and Kashmir re-delimited in 2022; no source has the new boundaries",
}

# DataMeet 2019 state names -> our portal state names.
PC_STATE_ALIASES = {
    "Orissa": "Odisha",
    "Andaman & Nicobar": "Andaman And Nicobar Islands",
    "Dadra & Nagar Haveli": "The Dadra And Nagar Haveli And Daman And Diu",
    "Daman & Diu": "The Dadra And Nagar Haveli And Daman And Diu",
    "Jammu & Kashmir": "Jammu And Kashmir",
}
# LGD district-file state names -> our portal state names (after normalisation).
LGD_STATE_ALIASES = {
    "ANDAMANANDNICOBAR": "Andaman And Nicobar Islands",
    "DADRANAGARHAVELIDAMANANDDIU": "The Dadra And Nagar Haveli And Daman And Diu",
}
# Phase 1 state polygons whose names differ from the portal's (reviewed).
STATE_POLYGON_ALIASES = {
    "Andaman And Nicobar Islands": "andaman_and_nicobar",
    "The Dadra And Nagar Haveli And Daman And Diu": "dadra_and_nagar_haveli_and_daman_and_diu",
}

# (our state, our constituency name) -> (DataMeet st_name, pc_no). Every entry
# was checked by hand against the DataMeet feature in the same state
# (docs/phase9_report.md). Names with our state suffixes (_BR, _UP, _HP, _MH)
# are handled by rule, not listed here.
DNHDD = "The Dadra And Nagar Haveli And Daman And Diu"
CONSTITUENCY_ALIASES = {
    ("Andhra Pradesh", "ANAKAPALLE"): ("Andhra Pradesh", 5),
    ("Andhra Pradesh", "ANANTAPUR"): ("Andhra Pradesh", 19),
    ("Andhra Pradesh", "NARASAPURAM"): ("Andhra Pradesh", 9),
    ("Bihar", "PURNEA"): ("Bihar", 12),
    ("Bihar", "UJJARPUR"): ("Bihar", 22),
    ("Chhattisgarh", "JANJGIR CHAMPA(SC)"): ("Chhattisgarh", 3),
    ("Chhattisgarh", "SARGUJA(ST)"): ("Chhattisgarh", 1),
    ("Delhi", "CHANDINI CHOWK"): ("Delhi", 1),
    ("Haryana", "SONEPAT"): ("Haryana", 6),
    ("Karnataka", "BELGAUM"): ("Karnataka", 2),
    ("Karnataka", "CHIKKODI"): ("Karnataka", 1),
    ("Karnataka", "DAVANAGERE"): ("Karnataka", 13),
    ("Karnataka", "HASSAN"): ("Karnataka", 16),
    ("Kerala", "MAVELIKKARA(SC)"): ("Kerala", 16),
    ("Ladakh", "LADAKH"): ("Jammu & Kashmir", 4),  # 2019 layer files Ladakh under J&K; see SEAT_AS_STATE
    ("Madhya Pradesh", "MANDSOUR"): ("Madhya Pradesh", 23),
    # DataMeet labels pc 30 "Mumbai South" (attribute error); its geometry is
    # Mumbai South Central (96% overlap with the LGD layer's pc 30).
    ("Maharashtra", "MUMBAI SOUTH CENTRAL"): ("Maharashtra", 30),
    # ...so DataMeet has two "Mumbai South" features; ours is the other one, pc 31.
    ("Maharashtra", "MUMBAI SOUTH"): ("Maharashtra", 31),
    ("Punjab", "BHATINDA"): ("Punjab", 11),
    ("Punjab", "FIROZPUR"): ("Punjab", 10),
    ("Tamil Nadu", "DHARAMAPURI"): ("Tamil Nadu", 10),
    ("Tamil Nadu", "KANNIYAKUMARI"): ("Tamil Nadu", 39),
    ("Tamil Nadu", "MAYILADUTHURAI"): ("Tamil Nadu", 28),
    ("Tamil Nadu", "THOOTHUKKUDI"): ("Tamil Nadu", 36),
    ("Tamil Nadu", "TIRUVALLUR(SC)"): ("Tamil Nadu", 1),
    ("Telangana", "BHONGIR"): ("Telangana", 14),
    ("Telangana", "CHELVELLA"): ("Telangana", 10),
    ("Telangana", "PEDDAPALLE"): ("Telangana", 2),
    ("Telangana", "WARANGEL(SC)"): ("Telangana", 15),
    (DNHDD, "DADRA & NAGAR HAVELI (ST)"): ("Dadra & Nagar Haveli", 1),
    (DNHDD, "DAMAN and DIU"): ("Daman & Diu", 1),
    ("Uttarakhand", "HARDWAR"): ("Uttarakhand", 5),
    ("Uttarakhand", "NAINITAL UDHAM SINGH NAG."): ("Uttarakhand", 4),
    ("West Bengal", "ARAMBAG(SC)"): ("West Bengal", 29),
    ("West Bengal", "BARRACKPUR"): ("West Bengal", 15),
    ("West Bengal", "JOYNAGAR(SC)"): ("West Bengal", 19),
}
STATE_SUFFIX = re.compile(r"_(BR|UP|HP|MH)$")

# Display rule (owner decision 2026-10-03, final spec): Ladakh is one seat
# covering the whole Union Territory, so /api/geojson serves that seat as
# Admin2's Ladakh shape (including Gilgit-Baltistan and Aksai Chin, as Admin2
# has it), marked clipped_to_state. The 2019 layer's own Ladakh polygon also
# covered a western strip (~73.4-74.0E, ~12,000 km2, around Muzaffarabad and
# Mirpur) that Admin2 files under Jammu and Kashmir; that strip is drawn as
# part of Jammu and Kashmir. The stored geo_area geometry and the seat's
# marker point are unchanged (no table is written).
SEAT_AS_STATE = {"Ladakh": "ladakh"}  # our state name -> state geo_area key
DISPLAY_SIMPLIFY_DEG = 0.005  # ~500 m, for the state shapes drawn as areas
# Two Admin2 shapes that must meet exactly after simplification: the first is
# simplified on its own, the second is the simplified union of both minus the
# first, so there is no gap and no overlap between them.
SHARED_BORDER = ("jammu_and_kashmir", "ladakh")
# Part of the /api/geojson version, so a changed display rule changes its ETag.
DISPLAY_REV = "2026-10-03 Ladakh served as Admin2 Ladakh, shared border with J&K"

# Display-only outlines for /api/boundary-outlines (owner decision 2026-10-03,
# final spec), from rows already in geo_area. Every state whose seats the
# coverage data marks re-delimited (geo_name_crosswalk, the rows
# /api/geographic-coverage lists; today Assam and Jammu and Kashmir) is served
# as one state-level shape with a computed marker point inside it. The Map
# colours it from map-data level=state; the shape itself carries no metric.
# The national outline (geo_area national "IN", Country/india-soi.geojson,
# Survey of India-derived; geo_load.py). The Map has no tile layer: this shape
# is its opaque land fill, with a thin line of the same shape on top.
NATIONAL_OUTLINE_KEY = "IN"
NATIONAL_SIMPLIFY_DEG = 0.01  # ~1 km; ~282,000 points -> ~7,000
# Every Admin2 state shape (geo_area level "state"), drawn as a thin border
# line between the land fill and the seat polygons.
STATE_BORDER_SIMPLIFY_DEG = 0.01  # ~1 km; ~1.4 million points -> ~18,000
OUTLINE_DECIMALS = 4  # ~10 m
OUTLINES_REV = "2026-10-03 re-delimited states + marker points, J&K/Ladakh border, state borders + labels"


def _rounded(coords, nd: int):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], nd), round(coords[1], nd)]
    return [_rounded(c, nd) for c in coords]


def simplified(geometry: dict, tolerance: float, decimals: int = OUTLINE_DECIMALS) -> dict:
    """A small display copy of a stored geometry: simplified, coordinates
    rounded (to OUTLINE_DECIMALS unless `decimals` is given: the static
    outline file, scripts/build_static_outlines.py, uses 3)."""
    g = shape(geometry)
    if not g.is_valid:  # make_valid only when needed: it is slow on the national outline
        g = make_valid(g)
    parts = _polygons(g.simplify(tolerance, preserve_topology=True))
    if not parts:
        raise ValueError("simplification left no area")
    out = mapping(MultiPolygon(parts))
    return {"type": out["type"], "coordinates": _rounded(out["coordinates"], decimals)}


def norm(name: str | None) -> str:
    """Uppercase letters only, reservation tag and our state suffix removed."""
    s = STATE_SUFFIX.sub("", (name or "").strip().upper())
    s = re.sub(r"\((SC|ST)\)", "", s.replace("&", " AND "))
    return re.sub(r"[^A-Z]", "", s)


def _point(geom) -> tuple[float, float]:
    p = geom.representative_point()
    return round(p.y, 6), round(p.x, 6)


def _polygons(geom) -> list[Polygon]:
    """The areal parts of a geometry (an intersection can also yield lines or points)."""
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [p for g in geom.geoms for p in _polygons(g)]
    return []


def _valid(geometry: dict):
    g = shape(geometry)
    return g if g.is_valid else make_valid(g)


def _without_holes(geom):
    """Neither Admin2 source shape has a hole, so any hole in their union is a
    sliver where the two files do not quite meet; filling it closes the gap."""
    return unary_union([Polygon(p.exterior) for p in _polygons(geom)])


def display_states(geometries: dict[str, dict]) -> dict[str, dict]:
    """Display copies of Admin2 state shapes (state geo_area key -> GeoJSON
    geometry), simplified to DISPLAY_SIMPLIFY_DEG, coordinates rounded. The two
    SHARED_BORDER states meet exactly (see SHARED_BORDER)."""
    tol = DISPLAY_SIMPLIFY_DEG
    shapes = {k: _valid(g).simplify(tol, preserve_topology=True) for k, g in geometries.items()}
    first, second = SHARED_BORDER
    if first in geometries and second in geometries:
        union = _without_holes(unary_union([_valid(geometries[first]), _valid(geometries[second])]))
        shapes[second] = union.simplify(tol, preserve_topology=True).difference(shapes[first])
    out = {}
    for key, g in shapes.items():
        parts = _polygons(g)
        if not parts:
            raise ValueError(f"display shape of {key} has no area")
        m = mapping(MultiPolygon(parts))
        out[key] = {"type": m["type"], "coordinates": _rounded(m["coordinates"], OUTLINE_DECIMALS)}
    return out


def state_key(name: str) -> str:
    """Our portal state name -> its state geo_area key (Phase 1 polygons)."""
    return STATE_POLYGON_ALIASES.get(name) or re.sub(r"[^a-z]+", "_", name.lower()).strip("_")


def inner_point(geometry: dict) -> tuple[float, float]:
    """(lat, lon) of a point guaranteed inside a display shape: shapely's
    representative_point(), the rule used for every seat's marker point."""
    return _point(_valid(geometry))


def label_point(geometry: dict) -> tuple[float, float]:
    """(lat, lon) for a state's name label: the visual centre (pole of
    inaccessibility, ~1 km tolerance) of the state's largest part, so the
    name sits well inside the shape rather than near an edge."""
    largest = max(_polygons(_valid(geometry)), key=lambda g: g.area)
    p = polylabel(largest, tolerance=0.01)
    return round(p.y, 4), round(p.x, 4)


def _state_areas(session: Session) -> dict[str, GeoArea]:
    """Our portal state name -> its state geo_area (Phase 1 polygons)."""
    areas = {a.key: a for a in session.execute(select(GeoArea).where(GeoArea.level == "state")).scalars()}
    out = {}
    for st in session.execute(select(State)).scalars():
        key = state_key(st.name)
        if key in areas:
            out[st.name] = areas[key]
    return out


def fix_state_crosswalk(session: Session) -> int:
    """Phase 1 left two portal states unmatched only because of naming; both
    polygons exist. Record reviewed aliases (Phase 1 rows are updated in place)."""
    areas = {a.key: a for a in session.execute(select(GeoArea).where(GeoArea.level == "state")).scalars()}
    fixed = 0
    for portal, key in STATE_POLYGON_ALIASES.items():
        row = session.execute(
            select(GeoNameCrosswalk).where(
                GeoNameCrosswalk.level == "state", GeoNameCrosswalk.portal_name == portal
            )
        ).scalar_one_or_none()
        if row is not None and row.geo_area_id is None and key in areas:
            row.geo_area_id = areas[key].id
            row.method = "reviewed_alias"
            row.review_status = "reviewed"
            row.detail = {"note": f"portal name differs from polygon name '{areas[key].name}'"}
            fixed += 1
    session.flush()
    return fixed


def load_constituencies(session: Session) -> dict:
    """geo_area rows for matched, non-re-delimited constituencies, and a
    crosswalk row for EVERY portal constituency (matched or not). Idempotent."""
    existing = session.execute(select(GeoArea.id).where(GeoArea.level == "constituency")).first()
    if existing:
        return {"skipped": "constituencies already loaded"}
    fc = json.loads(PC_FILE.read_text(encoding="utf-8"))
    feats: dict[tuple[str, int], dict] = {}
    by_name: dict[tuple[str, str], list] = {}
    for f in fc["features"]:
        p = f["properties"]
        st_ours = PC_STATE_ALIASES.get(p["st_name"], p["st_name"])
        feats[(p["st_name"], int(p["pc_no"]))] = f
        by_name.setdefault((st_ours, norm(p["pc_name"])), []).append(f)
    state_areas = _state_areas(session)
    states = {s.id: s.name for s in session.execute(select(State)).scalars()}
    counts: dict[str, int] = {}
    for c in session.execute(select(Constituency).where(Constituency.house == "LS")).scalars():
        st = states.get(c.state_id)
        detail: dict = {}
        feat = None
        if st in REDELIMITED:
            method = "redelimited_boundary_not_available"
            detail["reason"] = REDELIMITED[st]
        else:
            cands = by_name.get((st, norm(c.name)), [])
            if len(cands) == 1:
                feat, method = cands[0], "normalized"
            elif (st, c.name) in CONSTITUENCY_ALIASES:
                feat, method = feats.get(CONSTITUENCY_ALIASES[(st, c.name)]), "reviewed_alias"
                if feat is None:
                    method = "unmatched"
            else:
                method = "ambiguous" if cands else "unmatched"
        area_id = None
        if feat is not None:
            p = feat["properties"]
            geom = shape(feat["geometry"])
            lat, lon = _point(geom)
            area = GeoArea(
                level="constituency",
                key=f"pc:{p['st_code']}:{int(p['pc_no'])}",
                name=c.name,
                parent_id=state_areas[st].id if st in state_areas else None,
                geometry=mapping(geom),
                source=PC_SOURCE,
                licence=PC_LICENCE,
                version=PC_VERSION,
                rep_lat=lat,
                rep_lon=lon,
                attrs={
                    "dataset_state": st,
                    "dataset_constituency": c.name,
                    "constituency_id": c.id,
                    "source_pc_name": p["pc_name"],
                    "source_st_name": p["st_name"],
                    "pc_no": int(p["pc_no"]),
                    "pc_category": p.get("pc_category"),
                    "wikidata_qid": p.get("wikidata_qid"),
                    "match_method": method,
                },
            )
            session.add(area)
            session.flush()
            area_id = area.id
            detail["source_pc_name"] = p["pc_name"]
        session.add(
            GeoNameCrosswalk(
                portal_name=f"{st}|{c.name}",
                portal_state=st,
                level="constituency",
                geo_area_id=area_id,
                method=method,
                review_status="reviewed" if method != "normalized" else "auto",
                detail=detail,
            )
        )
        counts[method] = counts.get(method, 0) + 1
    session.flush()
    return counts


def load_districts(session: Session) -> dict:
    existing = session.execute(select(GeoArea.id).where(GeoArea.level == "district")).first()
    if existing:
        return {"skipped": "districts already loaded"}
    cols = ["OBJECTID", "dtname", "stname", "dist_lgd", "state_lgd", "geometry"]
    t = pq.read_table(DISTRICT_FILE, columns=cols)
    state_areas = _state_areas(session)
    portal_by_norm = {norm(s): s for s in state_areas}
    n = 0
    unmapped_states = set()
    for oid, dt, st, dl, sl, g in zip(*[t.column(c).to_pylist() for c in t.column_names]):
        ns = norm(st)
        our_state = LGD_STATE_ALIASES.get(ns) or portal_by_norm.get(ns)
        if our_state is None:
            unmapped_states.add(st)
        geom = wkb.loads(g).simplify(DISTRICT_SIMPLIFY_DEG, preserve_topology=True)
        lat, lon = _point(geom)
        session.add(
            GeoArea(
                level="district",
                key=f"dist:{oid}",
                name=dt,
                parent_id=state_areas[our_state].id if our_state in state_areas else None,
                geometry=mapping(geom),
                source=DISTRICT_SOURCE,
                licence=DISTRICT_LICENCE,
                version=DISTRICT_VERSION,
                rep_lat=lat,
                rep_lon=lon,
                attrs={
                    "lgd_district_code": dl,
                    "lgd_state_code": sl,
                    "source_state_name": st,
                    "state": our_state,
                    "name_norm": norm(dt),
                },
            )
        )
        n += 1
    session.flush()
    return {"districts": n, "unmapped_source_states": sorted(unmapped_states)}

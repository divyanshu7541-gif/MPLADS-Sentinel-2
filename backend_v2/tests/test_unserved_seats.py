"""
Map fix (2026-10-03): the 2019 seat polygons with no portal seat (today
Khadoor Sahib, Punjab pc 3), copied verbatim by scripts/build_unserved_seats.py
into frontend/public/geo/unserved-seats.json, which the Map draws as
"No works recorded" seats.

Written, not yet run (DEPLOY_NOTES.md). The file and script tests need no
database; the source comparison skips when app/geo_data_src is absent
(gitignored); the API comparison needs the DB (it skips without one).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.geo import boundaries, service

REPO = Path(__file__).resolve().parents[2]
STATIC_FILE = REPO / "frontend" / "public" / "geo" / "unserved-seats.json"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_unserved_seats.py"
API_JS = REPO / "frontend" / "src" / "services" / "api.js"
# The properties /api/geojson builds for every seat (service.geojson_bytes),
# apart from Ladakh's additive clipped_to_state.
SEAT_PROPS = ["dataset_state", "dataset_constituency", "area_key", "pc_no", "source_pc_name", "match_method"]


def _script():
    spec = importlib.util.spec_from_file_location("build_unserved_seats", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _static() -> dict:
    return json.loads(STATIC_FILE.read_bytes())


def _source() -> dict:
    if not boundaries.PC_FILE.exists():
        pytest.skip("app/geo_data_src is not present (gitignored)")
    return json.loads(boundaries.PC_FILE.read_text(encoding="utf-8"))


# ---- no database ------------------------------------------------------------------------------


def test_script_constants_match_boundaries():
    mod = _script()
    assert (mod.PC_SOURCE, mod.PC_LICENCE, mod.PC_VERSION) == (
        boundaries.PC_SOURCE,
        boundaries.PC_LICENCE,
        boundaries.PC_VERSION,
    )
    assert boundaries.PC_VERSION.endswith("sha256 " + mod.SOURCE_SHA256_PREFIX)
    assert mod.DEFAULT_SRC == boundaries.PC_FILE


def test_file_has_the_api_seat_properties_and_no_metric():
    fc = _static()
    assert fc["type"] == "FeatureCollection" and fc["metadata"]["static_file"] is True
    assert [f["properties"]["area_key"] for f in fc["features"]] == ["pc:3:3"]
    for f in fc["features"]:
        p = f["properties"]
        assert list(p) == SEAT_PROPS  # no new field
        assert p["match_method"] == "no_portal_seat"
        assert p["dataset_state"] not in boundaries.REDELIMITED
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert fc["features"][0]["properties"]["dataset_constituency"] == "KHADOOR SAHIB"
    assert {s["area_key"] for s in fc["metadata"]["seats"]} == {f["properties"]["area_key"] for f in fc["features"]}


def test_frontend_version_matches_the_file():
    fc = _static()
    assert f"UNSERVED_SEATS_VERSION = '{fc['metadata']['content_sha256']}'" in API_JS.read_text(encoding="utf-8")


def test_file_is_the_source_feature_copied_verbatim():
    src = _source()
    by_key = {(f["properties"]["st_name"], int(f["properties"]["pc_no"])): f for f in src["features"]}
    mod = _script()
    assert mod.build(src) == _static()  # the committed file is what the script writes today
    for f in _static()["features"]:
        p = f["properties"]
        source = by_key[(p["dataset_state"], p["pc_no"])]
        assert f["geometry"] == source["geometry"]
        assert p["source_pc_name"] == source["properties"]["pc_name"]
        assert p["area_key"] == f"pc:{source['properties']['st_code']}:{p['pc_no']}"


def test_builder_refuses_a_listed_seat_missing_from_the_source():
    mod = _script()
    with pytest.raises(KeyError):
        mod.build({"type": "FeatureCollection", "features": []})


# ---- against the API (needs the DB) ------------------------------------------------------------


@pytest.fixture(scope="module")
def map_run(db_session):
    s = service.served(db_session)
    if s.run_id is None:
        pytest.skip("no complete map_build -- run scripts/run_geo.py")
    return s


def test_file_seats_are_never_served_by_the_api(client, map_run):
    r = client.get("/api/geojson")
    assert r.status_code == 200
    served = {f["properties"]["area_key"] for f in r.json()["features"]}
    assert not served & {f["properties"]["area_key"] for f in _static()["features"]}


def test_every_source_seat_is_served_redelimited_or_in_the_file(client, map_run):
    """Outside the re-delimited states, each 2019 seat is drawn exactly once:
    from /api/geojson, or else from the static file."""
    src = _source()
    served = {f["properties"]["area_key"] for f in client.get("/api/geojson").json()["features"]}
    in_file = {f["properties"]["area_key"] for f in _static()["features"]}
    for f in src["features"]:
        p = f["properties"]
        st = boundaries.PC_STATE_ALIASES.get(p["st_name"], p["st_name"])
        key = f"pc:{p['st_code']}:{int(p['pc_no'])}"
        if st in boundaries.REDELIMITED and key not in served:
            continue
        assert (key in served) != (key in in_file), (key, p["pc_name"])


def test_file_seats_are_not_in_the_coverage_counts(client, map_run):
    """Display only: the seat is not a portal seat, so the coverage data does
    not list it (matched, unmatched or no-boundary)."""
    body = json.dumps(client.get("/api/geographic-coverage").json())
    assert "KHADOOR" not in body.upper()

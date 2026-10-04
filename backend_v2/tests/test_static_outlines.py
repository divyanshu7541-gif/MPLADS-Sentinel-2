"""
Map problem 4 (2026-10-03): the static outline file
(frontend/public/geo/india-outlines.json, written once by
scripts/build_static_outlines.py from the source files, no database) that the
Map loads instead of the first ~27 s /api/boundary-outlines build.

Written, not yet run (DEPLOY_NOTES.md). The builder and rounding tests need
no database; the file tests skip until the owner has generated the file; the
API comparison needs the DB (it skips without one).
"""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

import pytest
from shapely.geometry import Point, box, mapping, shape

from app.geo import boundaries, service

REPO = Path(__file__).resolve().parents[2]
STATIC_FILE = REPO / "frontend" / "public" / "geo" / "india-outlines.json"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_static_outlines.py"
MAX_GZIP = 150 * 1024


def _script():
    spec = importlib.util.spec_from_file_location("build_static_outlines", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _max_decimals(coords) -> int:
    if isinstance(coords[0], (int, float)):
        return max(len(repr(float(c)).split(".")[1].rstrip("0")) if "." in repr(float(c)) else 0 for c in coords)
    return max(_max_decimals(c) for c in coords)


def _static():
    if not STATIC_FILE.exists():
        pytest.skip("frontend/public/geo/india-outlines.json is not generated yet (scripts/build_static_outlines.py)")
    return json.loads(STATIC_FILE.read_bytes())


def _kind(fc, kind):
    return [f for f in fc["features"] if f["properties"]["kind"] == kind]


# ---- no database ------------------------------------------------------------------------------


def test_simplified_keeps_four_decimals_by_default():
    g = mapping(box(77.123456789, 28.123456789, 78.987654321, 29.987654321))
    assert _max_decimals(boundaries.simplified(g, 0.001)["coordinates"]) <= boundaries.OUTLINE_DECIMALS == 4
    assert _max_decimals(boundaries.simplified(g, 0.001, 3)["coordinates"]) <= 3


def test_builder_on_synthetic_shapes_matches_the_api_rules():
    mod = _script()
    national = mapping(box(68.0, 6.0, 98.0, 37.5))
    states = {
        "assam": ("Assam", mapping(box(89.7123456, 24.1123456, 96.0123456, 28.0123456))),
        "jammu_and_kashmir": ("Jammu & Kashmir", mapping(box(73.3, 32.3, 76.5, 35.0))),
        "ladakh": ("Ladakh", mapping(box(76.5, 32.3, 80.3, 36.0))),
        "kerala": ("Kerala", mapping(box(74.8654321, 8.2654321, 77.4654321, 12.8654321))),
    }
    fc = mod.build(national, states)
    kinds = [f["properties"]["kind"] for f in fc["features"]]
    assert kinds.count("national_outline") == 1
    assert kinds.count("state_border") == len(states)
    redelimited = {f["properties"]["state"]: f for f in _kind(fc, "redelimited_state")}
    assert set(redelimited) == set(boundaries.REDELIMITED)  # Assam, Jammu And Kashmir
    for st, f in redelimited.items():
        p = f["properties"]
        assert p["match_method"] == service.REDELIMITED_METHOD and p["reason"] == boundaries.REDELIMITED[st]
        assert shape(f["geometry"]).contains(Point(p["marker_lon"], p["marker_lat"]))
        assert _max_decimals(f["geometry"]["coordinates"]) <= boundaries.OUTLINE_DECIMALS
    for f in _kind(fc, "national_outline") + _kind(fc, "state_border"):
        assert _max_decimals(f["geometry"]["coordinates"]) <= mod.STATIC_DECIMALS == 3
    for f in _kind(fc, "state_border"):
        p = f["properties"]
        assert shape(f["geometry"]).buffer(0.01).contains(Point(p["label_lon"], p["label_lat"]))
    # the same shapes the API builds for the redelimited states (display_states, 4 decimals)
    expected = boundaries.display_states({k: g for k, (_, g) in states.items()})
    assert redelimited["Assam"]["geometry"] == expected["assam"]
    assert redelimited["Jammu And Kashmir"]["geometry"] == expected["jammu_and_kashmir"]
    assert fc["metadata"]["static_file"] is True and fc["metadata"]["outlines_rev"] == boundaries.OUTLINES_REV


# ---- the generated file (skips until it exists) -----------------------------------------------


def test_static_file_shape_and_size():
    fc = _static()
    raw = STATIC_FILE.read_bytes()
    assert len(gzip.compress(raw, 9)) <= MAX_GZIP
    assert fc["type"] == "FeatureCollection" and fc["metadata"]["static_file"] is True
    assert len(_kind(fc, "national_outline")) == 1
    borders = _kind(fc, "state_border")
    assert len(borders) >= 30 and all(f["properties"]["label_lat"] is not None for f in borders)
    for f in _kind(fc, "national_outline") + borders:
        assert _max_decimals(f["geometry"]["coordinates"]) <= 3
        assert not any(k in f["properties"] for k in ("risk", "tier", "flag_rate", "flagged"))  # display only
    for f in _kind(fc, "redelimited_state"):
        p = f["properties"]
        assert shape(f["geometry"]).contains(Point(p["marker_lon"], p["marker_lat"]))


# ---- against the API (needs the DB) ------------------------------------------------------------


@pytest.fixture(scope="module")
def map_run(db_session):
    s = service.served(db_session)
    if s.run_id is None:
        pytest.skip("no complete map_build -- run scripts/run_geo.py")
    return s


def test_static_file_matches_the_api(client, map_run):
    """Same kinds, states and properties as /api/boundary-outlines; the
    re-delimited shapes and their marker points are identical (so the state-
    level works sit on the same point, and J&K still meets the Ladakh seat)."""
    fc = _static()
    r = client.get("/api/boundary-outlines")
    assert r.status_code == 200
    api = r.json()
    assert {f["properties"]["key"] for f in _kind(fc, "state_border")} == {
        f["properties"]["key"] for f in _kind(api, "state_border")
    }
    api_red = {f["properties"]["state"]: f for f in _kind(api, "redelimited_state")}
    file_red = {f["properties"]["state"]: f for f in _kind(fc, "redelimited_state")}
    assert set(api_red) <= set(file_red)  # the file holds every state the coverage data lists
    for st, f in api_red.items():
        assert file_red[st]["geometry"] == f["geometry"], st
        for k in ("marker_lat", "marker_lon", "reason", "match_method"):
            assert file_red[st]["properties"][k] == f["properties"][k], (st, k)
    nat_api = shape(_kind(api, "national_outline")[0]["geometry"])
    nat_file = shape(_kind(fc, "national_outline")[0]["geometry"])
    assert nat_api.symmetric_difference(nat_file).area < 0.001 * nat_api.area  # 3 vs 4 decimals only

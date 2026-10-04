"""
One-off: the display-only outlines of /api/boundary-outlines, precomputed
into one static file the Map loads (frontend/public/geo/india-outlines.json),
so no page load waits on geometry work (the first API build takes ~27 s per
cold process, longer than the Map's 20 s time-out).

Reads only the two source files geo_load.py loaded into geo_area
(app/geo_data_src/india-soi.geojson and Admin2.shp, read the same way); no
database connection is made. Same features, properties, functions and
tolerances as service.outlines_bytes, with one difference: the national
outline and the state borders keep 3 decimals (a ~110 m grid, so a point
moves at most ~55 m, under half a pixel at the Map's maximum zoom) instead
of 4. The re-delimited state shapes (boundaries.REDELIMITED, the rule the
crosswalk rows were built from) come from boundaries.display_states at 4
decimals, exactly as /api/geojson builds the Ladakh seat, so Jammu and
Kashmir still meets that seat with no gap, and their marker points equal
the API's. Which of them the Map draws is decided by the coverage data.

Run once in the existing backend image, with the database not started
(one line; PowerShell or bash, from the repo root):

  docker compose run --rm --no-deps -v "${PWD}/backend_v2:/srv" -v "${PWD}/frontend/public/geo:/out" api python scripts/build_static_outlines.py --out /out/india-outlines.json

Writes only --out, and only when the gzipped file is at most --max-gzip-kb
(150). Prints point counts, raw and gzip sizes, the marker-point check and
the metadata. Display only: no table, row, score or API field is touched.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import shapefile  # noqa: E402
from shapely.geometry import Point, shape  # noqa: E402

from app.geo import boundaries  # noqa: E402
from app.ingest import geo_load  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "geo" / "india-outlines.json"
STATIC_DECIMALS = 3  # national outline and state borders; the re-delimited shapes keep 4
MAX_GZIP_KB = 150
REDELIMITED_METHOD = "redelimited_boundary_not_available"  # = geo.service.REDELIMITED_METHOD
NATIONAL_SOURCE = f"{geo_load.SOURCE}, Country/india-soi.geojson (Survey of India-derived)"
STATE_SOURCE = f"{geo_load.SOURCE}, States/Admin2.shp"


def read_sources(src_dir: Path) -> tuple[dict, dict[str, tuple[str, dict]]]:
    """(national geometry, {state key: (Admin2 name, geometry)}), read exactly
    as geo_load.load_national / load_states read them (first record per key)."""
    with open(src_dir / "india-soi.geojson", encoding="utf-8") as f:
        national = json.load(f)["features"][0]["geometry"]
    states: dict[str, tuple[str, dict]] = {}
    for rec in shapefile.Reader(str(src_dir / "Admin2")).shapeRecords():
        name = rec.record["ST_NM"].strip()
        states.setdefault(geo_load._slug(name), (name, rec.shape.__geo_interface__))
    return national, states


def build(
    national: dict | None,
    states: dict[str, tuple[str, dict]],
    border_tolerance: float = boundaries.STATE_BORDER_SIMPLIFY_DEG,
    decimals: int = STATIC_DECIMALS,
) -> dict:
    """The FeatureCollection /api/boundary-outlines serves, built from the
    source geometries (no database)."""
    feats = []
    redelimited = {st: boundaries.state_key(st) for st in sorted(boundaries.REDELIMITED)}
    drawn = set(redelimited.values()) | set(boundaries.SHARED_BORDER)
    state_shapes = boundaries.display_states({k: g for k, (_, g) in states.items() if k in drawn})
    common = {"licence": geo_load.LICENCE, "version": geo_load.VERSION}
    if national is not None:
        feats.append(
            {
                "type": "Feature",
                "geometry": boundaries.simplified(national, boundaries.NATIONAL_SIMPLIFY_DEG, decimals),
                "properties": {"kind": "national_outline", "name": "India", "source": NATIONAL_SOURCE, **common},
            }
        )
    for state, key in sorted(redelimited.items()):
        if key not in state_shapes:
            continue
        lat, lon = boundaries.inner_point(state_shapes[key])
        feats.append(
            {
                "type": "Feature",
                "geometry": state_shapes[key],
                "properties": {
                    "kind": "redelimited_state",
                    "state": state,
                    "match_method": REDELIMITED_METHOD,
                    "reason": boundaries.REDELIMITED.get(state),
                    "marker_lat": lat,
                    "marker_lon": lon,
                    "source": STATE_SOURCE,
                    **common,
                },
            }
        )
    for key in sorted(states):
        name, geometry = states[key]
        border = boundaries.simplified(geometry, border_tolerance, decimals)
        label_lat, label_lon = boundaries.label_point(border)
        feats.append(
            {
                "type": "Feature",
                "geometry": border,
                "properties": {
                    "kind": "state_border",
                    "key": key,
                    "name": name,
                    "label_lat": label_lat,
                    "label_lon": label_lon,
                    "source": STATE_SOURCE,
                    **common,
                },
            }
        )
    content = hashlib.sha256(json.dumps(feats, separators=(",", ":")).encode()).hexdigest()[:12]
    return {
        "type": "FeatureCollection",
        "features": feats,
        "metadata": {
            "outlines_rev": boundaries.OUTLINES_REV,
            "static_file": True,
            "content_sha256": content,
            "national_simplify_deg": boundaries.NATIONAL_SIMPLIFY_DEG,
            "state_border_simplify_deg": border_tolerance,
            "redelimited_simplify_deg": boundaries.DISPLAY_SIMPLIFY_DEG,
            "decimals": {
                "national_outline": decimals,
                "state_border": decimals,
                "redelimited_state": boundaries.OUTLINE_DECIMALS,
            },
            "redelimited_candidates": sorted(redelimited),
        },
    }


def _points(coords) -> int:
    if isinstance(coords[0], (int, float)):
        return 1
    return sum(_points(c) for c in coords)


def _sha12(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", type=Path, default=boundaries.GEO_DIR, help="geo_data_src folder")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--border-tolerance",
        type=float,
        default=boundaries.STATE_BORDER_SIMPLIFY_DEG,
        help="state borders only (degrees; 0.01 = ~1.1 km). If the file is over the size cap, try 0.015.",
    )
    ap.add_argument("--max-gzip-kb", type=float, default=MAX_GZIP_KB)
    args = ap.parse_args()

    national, states = read_sources(args.src)
    fc = build(national, states, args.border_tolerance)
    fc["metadata"]["sources_sha256"] = {
        "india-soi.geojson": _sha12(args.src / "india-soi.geojson"),
        "Admin2.shp": _sha12(args.src / "Admin2.shp"),
    }
    body = json.dumps(fc, separators=(",", ":")).encode()
    gz = gzip.compress(body, compresslevel=9, mtime=0)

    by_kind: dict[str, list[int]] = {}
    for f in fc["features"]:
        k = f["properties"]["kind"]
        by_kind.setdefault(k, [0, 0])
        by_kind[k][0] += 1
        by_kind[k][1] += _points(f["geometry"]["coordinates"])
    print("features (count, points):")
    for k, (n, pts) in sorted(by_kind.items()):
        print(f"  {k:18s} {n:3d} {pts:8,d}")
    print("marker points inside their shapes:")
    for f in fc["features"]:
        p = f["properties"]
        if p["kind"] == "redelimited_state":
            inside = shape(f["geometry"]).contains(Point(p["marker_lon"], p["marker_lat"]))
            print(f"  {p['state']}: ({p['marker_lat']}, {p['marker_lon']}) inside={inside}")
            if not inside:
                print("REFUSED: a marker point is outside its shape; nothing written.")
                return 3
    print(f"raw size:  {len(body):,} bytes ({len(body) / 1024:.1f} KB)")
    print(f"gzip size: {len(gz):,} bytes ({len(gz) / 1024:.1f} KB; cap {args.max_gzip_kb:g} KB)")
    print("metadata:", json.dumps(fc["metadata"], indent=2))
    if len(gz) > args.max_gzip_kb * 1024:
        print(
            "REFUSED: over the size cap; nothing written. Re-run with --border-tolerance 0.015 "
            "(state borders only) and check the borders at zoom 9-10 before keeping it."
        )
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(body)
    print(f"written: {args.out}")
    print(
        "If the file's content changed, bump STATIC_OUTLINES_VERSION in frontend/src/services/api.js "
        f"(for example to '{fc['metadata']['content_sha256']}') so browsers fetch it at once."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

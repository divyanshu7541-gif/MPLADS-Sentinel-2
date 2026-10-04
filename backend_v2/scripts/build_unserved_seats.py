"""
One-off: the 2019 seat polygons /api/geojson cannot serve because the portal
has no Lok Sabha seat for them, copied verbatim into one small static file the
Map draws as "No works recorded" seats
(frontend/public/geo/unserved-seats.json).

Today this is one seat: Khadoor Sahib (Punjab, DataMeet pc 3). Snapshot A has
no Lok Sabha rows for it, so no constituency row exists, boundaries.
load_constituencies never gives it a geo_area row, and the Map showed only the
land fill there (no hover, no click). Every other DataMeet seat outside the
re-delimited states (Assam, Jammu and Kashmir) is matched to a portal seat.

Reads only app/geo_data_src/india_pc_2019_simplified.geojson, the file
boundaries.py loaded (PC_FILE), and refuses unless its sha256 starts with the
prefix pinned in boundaries.PC_VERSION. The geometry is copied as it is in that
file: no new shape, no new source, no simplification. The properties are the
six /api/geojson builds for a seat (no new field); match_method is
"no_portal_seat". Standard library only: no database, no shapely, no Docker.
Writes only --out. Display only: no table, row, score or API field is touched.

Run once from the repo root (PowerShell or bash):

  python backend_v2/scripts/build_unserved_seats.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_SRC = BACKEND / "app" / "geo_data_src" / "india_pc_2019_simplified.geojson"  # = boundaries.PC_FILE
DEFAULT_OUT = BACKEND.parent / "frontend" / "public" / "geo" / "unserved-seats.json"

# = boundaries.PC_SOURCE / PC_LICENCE / PC_VERSION and its sha256 prefix
# (test_unserved_seats.py checks they agree; this script must not import
# app.geo.boundaries, which needs shapely and pyarrow).
PC_SOURCE = "DataMeet maps, parliamentary-constituencies/india_pc_2019_simplified.geojson"
PC_LICENCE = "CC0 1.0"
PC_VERSION = "2019 boundaries (2008 delimitation); sha256 54840686c3c5"
SOURCE_SHA256_PREFIX = "54840686c3c5"
METHOD = "no_portal_seat"

# (DataMeet st_name, pc_no) -> (our state name, the seat's name as the portal
# spells it in prior_cycle_backlog_2023-24.csv, why it has no portal seat).
UNSERVED = {
    ("Punjab", 3): (
        "Punjab",
        "KHADOOR SAHIB",
        "The portal has no Lok Sabha rows for this seat (Snapshot A), so it has no "
        "constituency row and no geo_area row; drawn for display only, not counted "
        "in /api/geographic-coverage.",
    ),
}


def _points(coords) -> int:
    if isinstance(coords[0], (int, float)):
        return 1
    return sum(_points(c) for c in coords)


def build(fc: dict) -> dict:
    """The FeatureCollection of the UNSERVED seats, copied from the source
    FeatureCollection. Raises KeyError naming any listed seat not found."""
    by_key = {(f["properties"]["st_name"], int(f["properties"]["pc_no"])): f for f in fc["features"]}
    missing = [k for k in UNSERVED if k not in by_key]
    if missing:
        raise KeyError(f"not in the source file: {missing}")
    feats, seats = [], []
    for key in sorted(UNSERVED):
        src = by_key[key]
        p = src["properties"]
        state, name, reason = UNSERVED[key]
        area_key = f"pc:{p['st_code']}:{int(p['pc_no'])}"  # the key rule of boundaries.load_constituencies
        feats.append(
            {
                "type": "Feature",
                "geometry": src["geometry"],
                "properties": {
                    "dataset_state": state,
                    "dataset_constituency": name,
                    "area_key": area_key,
                    "pc_no": int(p["pc_no"]),
                    "source_pc_name": p["pc_name"],
                    "match_method": METHOD,
                },
            }
        )
        seats.append({"area_key": area_key, "reason": reason})
    content = hashlib.sha256(json.dumps(feats, separators=(",", ":")).encode()).hexdigest()[:12]
    return {
        "type": "FeatureCollection",
        "features": feats,
        "metadata": {
            "static_file": True,
            "content_sha256": content,
            "source": PC_SOURCE,
            "licence": PC_LICENCE,
            "boundary_vintage": PC_VERSION,
            "seats": seats,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="india_pc_2019_simplified.geojson")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    raw = args.src.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    ok = sha.startswith(SOURCE_SHA256_PREFIX)
    print(f"source: {args.src}")
    print(f"source sha256: {sha} (expected prefix {SOURCE_SHA256_PREFIX}: {'OK' if ok else 'MISMATCH'})")
    if not ok:
        print("REFUSED: the source file is not the one boundaries.PC_VERSION pins; nothing written.")
        return 2
    try:
        fc = build(json.loads(raw.decode("utf-8")))
    except KeyError as e:
        print(f"REFUSED: {e}; nothing written.")
        return 3
    for f in fc["features"]:
        p = f["properties"]
        print(
            f"  {p['area_key']}  {p['dataset_state']} | {p['dataset_constituency']}  "
            f"({p['source_pc_name']}, {f['geometry']['type']}, {_points(f['geometry']['coordinates'])} points)"
        )
    body = json.dumps(fc, separators=(",", ":")).encode()
    print(f"features: {len(fc['features'])}")
    print(f"size: {len(body):,} bytes")
    print(f"content_sha256: {fc['metadata']['content_sha256']}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(body)
    print(f"written: {args.out}")
    print(
        "If the file's content changed, set UNSERVED_SEATS_VERSION in frontend/src/services/api.js "
        f"to '{fc['metadata']['content_sha256']}' so browsers fetch it at once."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

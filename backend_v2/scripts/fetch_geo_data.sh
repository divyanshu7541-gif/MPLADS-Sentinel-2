#!/usr/bin/env bash
# Fetches the DataMeet maps (github.com/datameet/maps) boundary files that
# app/ingest/geo_load.py loads into geo_area -- see that module's docstring
# for the licence/provenance rationale (CC BY 4.0; national outline is
# Survey-of-India-derived per the source file's own `Source` property).
#
# These are large binaries (~30MB total) and are gitignored -- run this once
# before `run_ingest.py`, or let scripts/run_ingest.sh do it automatically.
set -euo pipefail

DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/app/geo_data_src"
mkdir -p "$DEST"
cd "$DEST"

echo "Fetching state boundaries (States/Admin2.*)..."
for ext in shp shx dbf prj cpg; do
  curl -sL --retry 3 -o "Admin2.$ext" "https://raw.githubusercontent.com/datameet/maps/master/States/Admin2.$ext"
done

echo "Fetching national boundary (Country/india-soi.geojson)..."
curl -sL --retry 3 -o india-soi.geojson "https://raw.githubusercontent.com/datameet/maps/master/Country/india-soi.geojson"

python3 - <<'PY'
import json
with open("india-soi.geojson", encoding="utf-8") as f:
    d = json.load(f)
assert d["type"] == "FeatureCollection" and d["features"], "india-soi.geojson looks truncated/invalid"
print(f"OK: india-soi.geojson valid, {len(d['features'])} feature(s)")
PY

# Phase 9 (map backend) -- sources, licences and review decisions are in
# docs/phase9_report.md. Pinned by SHA-256: a changed upstream file fails
# here instead of silently changing the map.
echo "Fetching Lok Sabha constituencies (DataMeet india_pc_2019_simplified, CC0 1.0)..."
curl -sL --retry 3 -o india_pc_2019_simplified.geojson \
  "https://raw.githubusercontent.com/datameet/maps/master/parliamentary-constituencies/india_pc_2019_simplified.geojson"
echo "Fetching districts (india-geodata LGD_Districts, CC0-1.0 / CC-BY-4.0, LGD 2024 snapshot)..."
curl -sL --retry 3 -o LGD_Districts.parquet \
  "https://github.com/yashveeeeeeer/india-geodata/releases/download/admin/districts/LGD_Districts.parquet"
sha256sum -c - <<'SUMS'
54840686c3c5ceabb223940127f3067d921d052abd312e2eb4c6784c323ffc41  india_pc_2019_simplified.geojson
c205da56ce0538b33b2f66838a4032cef79be7585543686892c8a85a5d21aebc  LGD_Districts.parquet
SUMS

echo "Done. Files in $DEST:"
ls -la "$DEST"

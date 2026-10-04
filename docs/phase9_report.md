# Phase 9 report: geo/map backend

**Status:** closed on 2026-09-27, after the STOP #2 live verification and the lone-marker fix below.

**Data:** scratch database, run 1 (Snapshot A, data as of 2026-08-30), published default config `v4-candidate`.

**risk_result checksum:** `97f08303369f9ed6c50e46d68a4609f5` before and after the migration and the map build. The build records both values in `map_build.counts`, and `test_risk_result_unchanged_by_the_map_build` asserts them.

## What was built

| Piece | Where |
|---|---|
| Migration `d4b8f0c2e6a1` | Enables pg_trgm. Adds a representative point and attrs to `geo_area`, and portal_state/detail to the crosswalk. Creates `authority_geo`, `work_geo`, `map_build`, `map_work` and `geo_metric`. |
| Boundaries | `app/geo/boundaries.py`: constituency and district polygons, each with a representative point that lies inside it. |
| Location | `app/geo/location.py`: where each district authority and each work actually is. |
| Map build | `app/geo/build.py`: builds `map_work` and `geo_metric` for the published run, and applies the stale-data rule. CLI: `scripts/run_geo.py`. |
| Endpoints | `app/geo/service.py` and `app/api/maps.py`: map-data, map-works, map-filters, geojson, geographic-coverage, constituency-intelligence, and the new `GET /api/geo/areas/{key}/works`. |
| Frontend | One line in `Map.jsx` (446): `L.layerGroup()` → `L.markerClusterGroup({ singleMarkerMode: true })` for work markers. This is the STOP #1 decision, plus the STOP #2 lone-marker fix (see below). No other line of `Map.jsx` and no other file under `frontend/` changed. |
| CI | The fetch step now also downloads the two sha256-pinned Phase 9 files. A `run_geo.py` step runs before pytest. |
| Tests | `tests/test_phase9_map.py`. |
| Contract | "Phase 9 additions" section in `docs/frontend_contract.md`. Every change is additive. |

## Sources and licences

| Layer | Source | Licence | Vintage |
|---|---|---|---|
| Lok Sabha constituencies | DataMeet `india_pc_2019_simplified.geojson` | CC0 1.0 (DataMeet README; the "CC-BY-SA 2.5" note in the old backend copy is stale) | 2019 boundaries (2008 delimitation) |
| Districts | india-geodata release `admin/districts/LGD_Districts.parquet` (Local Government Directory) | CC0-1.0 / CC-BY-4.0 | LGD 2024 snapshot (last updated 2024-08-15) |
| States, national | Phase 1 DataMeet layers (unchanged) | as in Phase 1 | as in Phase 1 |

Both Phase 9 files are pinned by SHA-256 in `scripts/fetch_geo_data.sh`, so a changed upstream file fails the fetch instead of silently changing the map.

Every marker coordinate is the shapely `representative_point()` of a licensed polygon, which is guaranteed to lie inside it. No geocode, centroid, jitter or offset is used. The old Nominatim geocodes are not used.

## Match results

**Constituencies (542 Lok Sabha seats in the portal):**
- **489 matched automatically** by normalised name within the state.
- **34 matched by reviewed alias**, listed in `CONSTITUENCY_ALIASES`. Examples:
  - Spellings: BELGAUM/Belagavi, CHELVELLA/Chevella.
  - Ladakh is filed under J&K in the 2019 layer. That polygon also covers a strip on its west (about 73.4–74.0°E, about 12,000 km², 6.8% of the seat) that the Phase 1 Admin2 state layer files under Jammu and Kashmir. Since 2026-10-03 (final spec), `/api/geojson` serves the Ladakh seat as Admin2's Ladakh shape itself (`SEAT_AS_STATE` in `boundaries.py`; the feature still carries `clipped_to_state`), built together with Admin2's Jammu and Kashmir so the two meet with no gap and no overlap (`SHARED_BORDER`, `display_states`). This is display only: the stored `geo_area` geometry and the seat's marker point are unchanged. Admin2's Ladakh includes Gilgit-Baltistan and Aksai Chin, and Admin2's Jammu and Kashmir includes the Pakistan-administered area (the western strip above), as the Survey of India-derived national outline does.
  - DataMeet labels pc 30 "Mumbai South", but its geometry is Mumbai South Central: it overlaps the LGD pc 30 by 96%. So MUMBAI SOUTH CENTRAL maps to pc 30 and MUMBAI SOUTH to pc 31.
- **19 have no polygon and no point**, per the owner's decision. These are Assam (14) and Jammu and Kashmir (5), which were re-delimited in 2022–23; no available source has the new boundaries. They are labelled `redelimited_boundary_not_available`.
- **0 unmatched.** Result: 523 of 542 seats (96.5%) have a real boundary, and 0 fall back to a centroid.

**District authorities (774):**
- **760** match a unique LGD district name, after 55 reviewed spelling/rename aliases in `DISTRICT_ALIASES`. Examples:
  - Spellings: Kataka→Cuttack, Nayagada→Nayagarh.
  - Renames: Ahilyanagar→Ahmednagar (2024), Dharashiv→Osmanabad (2023), Chhatrapati Sambhajinagar→Aurangabad, Sribhumi→Karimganj (Nov 2024), Bengaluru South→Ramanagara (2024).
- **10** share a name with a district in another state (Aurangabad, Balrampur, Bilaspur, Hamirpur, Pratapgarh). Each is resolved by the state that most of its Lok Sabha works' constituencies are in.
- **4** have no LGD polygon because the district was created after the 2024 snapshot: Mauganj, Maihar and Pandhurna (Madhya Pradesh, 2023), and Vav-Tharad (Gujarat, 2025). They keep their state but get no district polygon. They are deliberately **not** given their parent district's polygon. This leaves 197 works (139 of them scored) without a district.

**Works (97,506 scored in the published config):**
- **75,770 (77.7%)** have a marker at their constituency's representative point.
- **19,274** are Rajya Sabha works. RS members have no constituency, so these are placed at state and district level only.
- **2,462** are Lok Sabha works in the 19 re-delimited seats.
- No work has conflicting constituencies in the source; all 542 constituency names match exactly.

## The authority-state bug (open, owner deferred)

> **Fixed in Phase 13.y (2026-09-28).** The ingest now stores each authority's own state
> (`app/ingest/authority_state.py`); Phases 3–5 were re-run as analysis run 44 and published.
> The figures below describe run 1. See `docs/validation_report_v1.md` §10.

`district_authority.state_id` is the state of the first MP row seen for that authority, not the authority's own state. For example, AGRA is stored under Gujarat and BUDAUN under Jammu and Kashmir.

- The map corrects this in `authority_geo`: **52 authorities** get a different state from the one stored, covering **11,783 scored works**.
- Phase 3 takes a work's state from the authority, so Phase 3–5 peer groups, signals and risk still use the wrong state for those works.
- **risk_result is not changed** in this phase. The upstream fix, followed by a Phase 3–5 re-run, is a separate step and should happen before cutover (Phase 12).

## Behaviour rules

- **Search** is a literal, case-insensitive substring match over work ID, MP, description, constituency, state, district and category.
  - User text is escaped for LIKE (`\ % _`) and bound as a parameter. It is never a regex and never interpolated.
  - Searches of 3 or more characters use the pg_trgm GIN index. Shorter ones scan only the served run's `map_work` rows (about 100k), never the base tables.
  - Blank search is treated as no search. More than 100 characters, or a NUL character, returns 422.
- **Markers:** every work in a constituency shares that constituency's one point. `location_precision: "approximate_constituency_level"` is on each row, with a note in headers and on the coverage response. markercluster groups the markers on the map.
- **Aggregation:** `geo_metric` stores sums at the grain (level, area, House, tier, stage).
  - Each work is counted exactly once per level. Works that can't be placed go into explicit `unlocated:<reason>` buckets.
  - State, district and constituency levels each sum exactly to national, which equals the scored-work count. Tests assert this.
- **Two geographies**, per the owner's decision:
  - State and district levels show where the work **is**: the authority's corrected location.
  - The constituency layer, and the markers, use the constituency's own state.
- **Stale data:** the API serves the published run's map build when it is complete. Otherwise it serves the most recent complete build, labelled with its run and date and `is_latest: false`.
- **House:** constituency areas are Lok Sabha only, so `house=RS` returns an empty map-data/map-works response. map-filters still counts RS works. Constituency intelligence for `Sitting Rajya Sabha`, the portal's RS placeholder, returns `applicable: false` with the reason. The endpoint takes no `house` parameter, per contract #24.
- **Errors:** invalid input returns 422, and a database failure returns 503. The endpoints never return a silent empty 200.

## Judgment calls for the owner to confirm

These thresholds were chosen before looking at the output. None of them is tuned to the data.

1. **Small-number rule, `MIN_WORKS_FOR_RATE = 10`:** an area with fewer than 10 works shows counts and sums, but its rates and averages are `null` and it carries `insufficient_data: true`. With no filter, 19 of 517 constituencies fall under it.
2. **map-works hard cap, 3000 rows:** the old default was 40,000. Above the cap, the endpoint returns a sample stratified by state: one row per state first, then the rest in proportion, highest risk first within each state.
   - This fixes a bug in the old rule. When the cap was smaller than the number of states, it could exceed the cap: `limit=1` returned 5 rows in a unit test.
   - A full national request returns about 2.3 MB uncompressed (gzip is on). The frontend asks for works only on a constituency drill-down, which is far smaller.
3. **Run labels in headers:** map-data and map-works are bare arrays under the contract, so the note, run and date travel in `X-*` headers, which CORS exposes. `frontend/` is not changed to read them.
4. **`signal_summary` "high" means score > 0.7.** This is the old engine's cut, carried over for the panel's meaning, and computed over eligible works only.
5. **`confidence_distribution` stays `{}`:** v2 defines no confidence bands, and inventing some would be a new threshold. `average_confidence` is provided instead.
6. **Amount is `sanction_amount`**, for every scored work (all are sanctioned or completed).

## Payload (run 1)

| Response | Size |
|---|---|
| map-data, no filter | 517 areas, about 311 KB uncompressed. BLUEPRINT §15 targets about 300 KB / 800 areas. |
| geojson | 523 features, 1.45 MB, versioned (ETag/304) and gzipped. Geometry is served separately from metrics, as §15 specifies. |
| map-works at 3× volume (test) | Still exactly 3000 rows, under 3 MB, every state represented. |

## Tests (`tests/test_phase9_map.py`)

All tests are permanent and read-only, except two. The 3×-volume test and the stale-data test run inside a transaction that is rolled back.

- **Special characters:** `(Test)`, `Some[thing]`, `a*b`, `(`, `[`, `*`, `?`, `((`, `a(b`, `%`, `_`, `\`, quotes. Both endpoints return 200, and the total count equals a `strpos` literal count.
- **Search input:**
  - A blank search equals no search.
  - Invalid input returns 422: bad tier, `limit ≤ 0`, a long or NUL search, a bad House, bad paging.
  - Unknown filter values return an empty 200, and an unknown area returns 404.
- **No fake precision:**
  - Every marker equals its constituency's point.
  - Every point lies inside its polygon.
  - Each constituency has one coordinate.
  - The 19 re-delimited seats have no point.
- **Aggregation:** all levels sum to national, which equals the scored works. map-data totals equal `geo_metric` under four filter sets. Tier counts add up.
- **Small numbers:** small areas have null rates.
- **Pagination:** drill-down pages are disjoint and complete. District drill-down includes works without a marker.
- **House filter.**
- **Intelligence:** returns RS not-applicable, unknown→`found:false`, and consistent LS values.
- **Stale data:** served with the last build's run and date, and `is_latest:false`.
- **Bounded payload at 3× volume.** The cap holds for `limit` values of 1, 5, 40 and 10⁶.
- **No full scan:**
  - All SQL from the eight map calls is captured, and none of it touches `work`, `work_state`, `risk_result` or `signal_result`.
  - EXPLAIN shows search uses `ix_map_work_search_trgm`.
- **Isolation:** a dead database gives 503 on every map endpoint, other endpoints still return 200, and the map recovers.
- **Contract:** row keys are a superset of the old engine's. Every map-data row joins to a GeoJSON feature the way Map.jsx joins them.
- **GeoJSON:** versioned and cached (ETag / 304).
- **Checksum:** the risk_result checksum is unchanged.

## Deviations from the brief

- The precision `note` for map-works is in a response header, not a top-level key, because the contract shape is a bare array. The coverage response carries it as a key.
- The intelligence endpoint reads `map_work`, which is the published run's `risk_result` values copied at build time for the default config, plus `geo_metric`. It does not read `risk_result` directly, so requests never touch the base tables.

## STOP #2: live verification (2026-09-27)

The owner could not run the manual map test, so it was run instead against the real stack:
- backend_v2 under uvicorn on the scratch database.
- A production build of the frontend (`vite build` + `vite preview`, proxying `/api`), in an isolated container.
- Playwright 1.47 with Chromium, the version CI uses.

**HTTP, called directly on the running API** (`/api/map-data` and `/api/map-works`):

| Search | Status | Result |
|---|---|---|
| `(Test)`, `Some[thing]`, `a*b` | 200 | `[]`, total 0 |
| `(` | 200 | 30,583 works in 467 constituencies (map-works capped at 3,000, sampled) |
| `a(b` | 200 | 4 works; the database confirms each contains the literal text "a(b" |

**UI:** the same five searches through the map search box each sent the expected request and got 200. The national summary showed 0 / 0 / 0 / 30,583 / 4 works, and no API response failed.

**Rendering:**
- Works in a constituency render as one cluster at the constituency's point: "3" for MAYURBHANJ with `a(b`, and "135" for DHARWAD.
- Clicking a cluster fans the markers out around that single point on screen only; the data coordinates don't change.
- The popup reads "Location: Approximate constituency location".

**Lone-marker fix (owner-authorised, scoped exception to the frozen `Map.jsx`):**
- *Problem:* a constituency with exactly one matching work (SIKAR with `a(b`) rendered as a lone dot that looked like an exact site, because markercluster never clusters a single marker.
- *Fix:* `singleMarkerMode: true` on the same line changed at STOP #1. Every marker, including a single one, now uses the cluster icon, so it reads "1".
- *Re-verified* on a fresh production build: SIKAR shows one cluster-styled "1" and no plain dot. MAYURBHANJ's fanned-out markers also render as "1" icons. The popup still says "Approximate constituency location".
- *Side effect:* individual markers no longer show their risk colour on the icon. The popup still shows the risk tier and score, and the choropleth is unchanged.
- The risk_result checksum is still `97f08303369f9ed6c50e46d68a4609f5`.

**Not verified:** Firefox and Safari (only Chromium was run), and work markers in dev mode, which never render because of issue 1 below.

## Known pre-existing issues (logged, not fixed; for a future cleanup pass)

Both predate Phase 9. They are in the original `Map.jsx`, and the production build's acceptance criteria don't depend on them. By owner decision they are out of this phase's scope.

1. **Work markers never render in dev mode.**
   - *Cause:* under React StrictMode (`main.jsx`), the dev server mounts the map, destroys it and mounts it again. The map-init cleanup resets `mapInstanceRef` but not `workMarkersRef`, so the marker layer stays bound to the destroyed first map.
   - *Evidence:* the original `Map.jsx` with `L.layerGroup()` shows the same 0 markers in dev. Production builds, which mount once, render markers correctly.
2. **Clicking a work marker throws `setSelectedConstituencyData is not defined`.**
   - *Cause:* `Map.jsx` calls a setter that is never defined anywhere in `frontend/`.
   - *Effect:* the popup still opens, because it's bound separately. Only the uncaught page error results.

## Note: `frontend/package-lock.json`

This file was rewritten on 2026-09-27 at 00:50 IST by the owner's compose container `mplads_sentinel_i18n-frontend-1`, not by this phase's work. That container bind-mounts `frontend/` read-write and runs `npm install` every time it starts; the rewrite happened 4 seconds after a Docker restart. Every Phase 9 check used a read-only copy of `frontend/`.

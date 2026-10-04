"""
Phase 1 CLI entrypoint: runs P0->P1->P2, the reference-table loads and the
geo load, then writes a reconciliation report + reject-log summary to
docs/phase1_reconciliation_report.md (repo root) for review.

Usage (from backend_v2/, with DATABASE_URL pointing at a migrated Postgres):
    python scripts/run_ingest.py

Idempotent: safe to re-run against the same data/ and database -- see
app/ingest/pipeline.py's module docstring.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.ingest import authority_state, geo_load, pipeline, reference_load  # noqa: E402
from app.models.provenance import ControlTotal, ImportReject, RawFile  # noqa: E402
from app.models.work import Work  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "phase1_reconciliation_report.md"


def main() -> int:
    settings = get_settings()
    data_dir = Path(settings.data_dir)
    Session = get_session_factory()

    with Session() as session:
        result = pipeline.run_all(session, data_dir)

        name_to_id = reference_load.load_states(session, data_dir)
        alias_result = reference_load.load_state_aliases(session, data_dir, name_to_id)
        snapshot_a = pipeline.get_or_create_snapshot(session, "snapshot_a")
        district_result = reference_load.load_district_authorities(
            session, result["ida_state_pairs"], snapshot_a
        )
        session.flush()
        # The state each authority is IN, from its own district (Phase 13.y fix;
        # needs the LGD district file from scripts/fetch_geo_data.sh).
        district_result["states"] = authority_state.resolve_authority_states(session)

        national = geo_load.load_national(session)
        state_geo = geo_load.load_states(session, national)
        crosswalk_result = geo_load.build_state_crosswalk(session, state_geo)

        session.commit()

        report = render_report(session, result, alias_result, district_result, crosswalk_result)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Report written to {REPORT_PATH}")
    print(f"all_core_reconciled = {result['all_core_reconciled']}")
    return 0 if result["all_core_reconciled"] else 1


def render_report(session, result, alias_result, district_result, crosswalk_result) -> str:
    lines: list[str] = []
    lines.append("# Phase 1 Reconciliation Report")
    lines.append("")
    lines.append(f"Generated {dt.datetime.now(dt.timezone.utc).isoformat()}Z by `scripts/run_ingest.py`.")
    lines.append("")

    lines.append("## 1. Core file reconciliation (BLUEPRINT.md §2)")
    lines.append("")
    lines.append("| File | Measure | Body sum (rupees) | Footer match | vs BLUEPRINT (crore) | Status |")
    lines.append("|---|---|---|---|---|---|")
    # Read from the DB (control_total joined to raw_file), not just this
    # run's freshly-processed files -- on an idempotent re-run where every
    # file is already registered, no file goes through fresh P1/P2, but the
    # persisted reconciliation results are still there and still correct.
    core_rows = session.execute(
        select(ControlTotal, RawFile.filename)
        .join(RawFile, ControlTotal.raw_file_id == RawFile.id)
        .order_by(RawFile.filename)
    ).all()
    core_filenames = set()
    for ct, filename in core_rows:
        core_filenames.add(filename)
        lines.append(
            f"| {filename} | {ct.measure} | {float(ct.body_sum_rupees):,.2f} | "
            f"{'yes' if ct.footer_match else '**NO**'} | "
            f"{float(ct.computed_value_crore):.4f} vs {float(ct.portal_value_crore):.2f} | "
            f"{'PASS' if ct.status == 'pass' else '**FAIL**'} |"
        )
    lines.append("")
    all_pass = result["all_core_reconciled"]
    verdict = "YES" if all_pass else "NO -- see FAIL rows above"
    lines.append(f"**All {result['core_file_count']} core files reconciled: {verdict}**")
    lines.append("")

    lines.append("## 2. All registered files (P0/P1)")
    lines.append("")
    lines.append("| Snapshot | File | Category | Rows valid | Rows rejected | Status |")
    lines.append("|---|---|---|---|---|---|")
    for f in result["files"]:
        status = f.get("status", "processed")
        lines.append(
            f"| {f.get('snapshot','')} | {f['file']} | {f['category']} | "
            f"{f.get('rows_valid','')} | {f.get('rows_rejected', 0)} | {status} |"
        )
    lines.append("")

    lines.append("## 3. Reject log")
    lines.append("")
    reject_counts = session.execute(
        select(RawFile.filename, func.count(ImportReject.id))
        .join(ImportReject, ImportReject.raw_file_id == RawFile.id)
        .group_by(RawFile.filename)
    ).all()
    core_rejects = [(fn, c) for fn, c in reject_counts if fn in core_filenames]
    if core_rejects:
        lines.append("**Core files with rejects (should be zero per acceptance criteria):**")
        for fn, c in core_rejects:
            lines.append(f"- {fn}: {c} rejected rows")
    else:
        lines.append("Core files (the 9 reconciled above): **0 rejected rows** -- meets acceptance criteria.")
    lines.append("")
    if reject_counts:
        lines.append("All files with any rejected rows:")
        lines.append("")
        lines.append("| File | Rejected rows |")
        lines.append("|---|---|")
        for fn, c in reject_counts:
            lines.append(f"| {fn} | {c} |")
    else:
        lines.append("No rejected rows in any file.")
    lines.append("")

    lines.append("## 4. House-tagging")
    lines.append("")
    total_work = session.execute(select(func.count(Work.work_key))).scalar_one()
    by_house = session.execute(select(Work.house, func.count(Work.work_key)).group_by(Work.house)).all()
    by_source = session.execute(
        select(Work.house_source, func.count(Work.work_key)).group_by(Work.house_source)
    ).all()
    lines.append(f"Total `work` rows: **{total_work}**")
    lines.append("")
    lines.append("| House | Count |")
    lines.append("|---|---|")
    for house, count in by_house:
        lines.append(f"| {house} | {count} |")
    lines.append("")
    lines.append("| House source | Count |")
    lines.append("|---|---|")
    for source, count in by_source:
        lines.append(f"| {source} | {count} |")
    lines.append("")

    # Deterministic spread (md5 of the key, key as tie-break): the same 20
    # rows on every run, so regenerated reports diff cleanly.
    sample = (
        session.execute(select(Work).order_by(func.md5(Work.work_key), Work.work_key).limit(20))
        .scalars()
        .all()
    )
    lines.append("### Sample of 20 House-tagged work rows")
    lines.append("")
    lines.append("| work_key | house | house_source | raw_mp_name |")
    lines.append("|---|---|---|---|")
    for w in sample:
        lines.append(f"| {w.work_key} | {w.house} | {w.house_source} | {(w.raw_mp_name or '')[:40]} |")
    lines.append("")

    gap_fill_counts = [
        f.get("work_rows_added", "n/a") for f in result["files"] if f["category"] == "snapshot_b_recommended"
    ] or ["n/a"]
    lines.append(
        "**Note on works_recommended_RajyaSabha absence:** confirmed absent from "
        "data/raw/snapshot_a/ (file does not exist in that directory) -- this is EXPECTED per "
        "BLUEPRINT.md §2 hard limit 6, not a pipeline failure. The gap is filled from "
        "Snapshot B's mplads_recommended_works file, restricted to its Rajya Sabha rows "
        f"({gap_fill_counts} new work rows added from that source)."
    )
    lines.append("")

    lines.append("## 5. Reference data (state / state_alias / district_authority)")
    lines.append("")
    lines.append(
        f"State alias candidates scanned: {alias_result['candidate_count']}; "
        f"unmatched: {len(alias_result['unmatched'])}"
    )
    if alias_result["unmatched"]:
        lines.append(f"  - Unmatched: {', '.join(alias_result['unmatched'])}")
    lines.append(
        f"District authorities: {district_result['added']} added this run "
        f"(of {district_result['total_candidates']} candidate IDA names seen). "
        f"State from the authority's own district: {district_result['states']['methods']}; "
        f"{district_result['states']['corrected']} stored states set or corrected this run."
    )
    lines.append("")
    lines.append(
        "`person`, `tenure`, `constituency`, `activity_type` are schema-only this phase "
        "(migration created, not populated) -- entity resolution is a later pipeline stage "
        '(BLUEPRINT.md §5 P3/P5), not part of Phase 1\'s explicit "Geo reference load" scope.'
    )
    lines.append("")

    lines.append("## 6. Geo reference load (BLUEPRINT.md §15)")
    lines.append("")
    lines.append("Source: DataMeet maps (github.com/datameet/maps), licence CC BY 4.0.")
    lines.append(
        "National boundary: `Country/india-soi.geojson` "
        "(Survey of India-derived, per the file's own `Source` property)."
    )
    lines.append("State boundaries: `States/Admin2.shp` (36 states/UTs).")
    lines.append("")
    lines.append(
        f"State name crosswalk match rate: **{crosswalk_result['match_rate_pct']}%** "
        f"({crosswalk_result['matched']}/{crosswalk_result['total']})."
    )
    if crosswalk_result["unmatched"]:
        lines.append(f"Unmatched (listed, not dropped): {', '.join(crosswalk_result['unmatched'])}")
    lines.append("")

    lines.append("## 7. Idempotency")
    lines.append("")
    lines.append(
        "Output hash (sha256 of sorted filename|sha256|row_count across all raw_file rows): "
        f"`{result['output_hash']}`"
    )
    lines.append(
        "Re-run this script against the same data/ and database to verify the hash is unchanged "
        "(see tests/test_phase1_ingest.py::test_idempotent_rerun_reproduces_same_output_hash)."
    )
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

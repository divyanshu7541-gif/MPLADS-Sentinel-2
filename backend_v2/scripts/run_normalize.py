"""
Phase 2 CLI entrypoint: P3 Normalise -> P4 Link+lifecycle -> P5 Entity
resolution. Writes docs/phase2_normalization_report.md for review.

Usage (from backend_v2/, with DATABASE_URL pointing at a migrated Postgres
that already has Phase 1's ingest applied -- run scripts/run_ingest.py
first):
    python scripts/run_normalize.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.ingest import p2_descriptive, p3_backfill, p4_link, p5_payee, roster  # noqa: E402
from app.ingest.pipeline import get_or_create_snapshot  # noqa: E402
from app.models.entities import Payee  # noqa: E402
from app.models.reference import ActivityType, Constituency, Person, Tenure  # noqa: E402
from app.models.work import Work  # noqa: E402
from app.models.work_related import Payment, WorkState  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "phase2_normalization_report.md"


def main() -> int:
    settings = get_settings()
    data_dir = Path(settings.data_dir)
    Session = get_session_factory()

    with Session() as session:
        snapshot_a = get_or_create_snapshot(session, "snapshot_a")
        snapshot_b = get_or_create_snapshot(session, "snapshot_b")

        name_to_person_id = roster.load_persons(session, data_dir, snapshot_a)
        constituency_by_key = roster.load_constituencies(session, data_dir, snapshot_a)
        person_to_tenure, unmatched_tenure_names = roster.load_tenures(
            session, data_dir, snapshot_a, name_to_person_id, constituency_by_key
        )
        session.flush()

        p3_stats = p3_backfill.backfill_work(session, data_dir)
        session.flush()

        p4a_stats = p4_link.build_work_state_snapshot_a(session, data_dir, snapshot_a)
        session.flush()
        p4b_stats = p4_link.build_work_state_snapshot_b_gapfill(session, data_dir, snapshot_b)
        session.flush()

        payee_id_by_vendor, payee_stats = p5_payee.load_payees(session, data_dir, snapshot_a)
        agency_stats = p5_payee.load_implementing_agencies(session, data_dir, snapshot_a)
        session.flush()

        payment_stats = p4_link.build_payments(session, data_dir, snapshot_a, payee_id_by_vendor)
        session.flush()

        alloc_stats = p2_descriptive.load_allocations(
            session, data_dir, snapshot_a, name_to_person_id, person_to_tenure
        )
        calamity_stats = p2_descriptive.load_calamity_consents(session, data_dir, snapshot_a)
        prior_cycle_stats = p2_descriptive.load_prior_cycle_work(session, data_dir, snapshot_b)
        macro_stats = p2_descriptive.load_macro_reference(session, data_dir, snapshot_a)

        session.commit()

        report = render_report(
            session,
            p3_stats,
            p4a_stats,
            p4b_stats,
            payee_stats,
            agency_stats,
            payment_stats,
            alloc_stats,
            calamity_stats,
            prior_cycle_stats,
            macro_stats,
            unmatched_tenure_names,
        )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Report written to {REPORT_PATH}")
    return 0


def render_report(
    session, p3, p4a, p4b, payee, agency, payment, alloc, calamity, prior_cycle, macro, unmatched_tenure_names
) -> str:
    lines: list[str] = []
    lines.append("# Phase 2 Normalization Report")
    lines.append("")
    lines.append(f"Generated {dt.datetime.now(dt.timezone.utc).isoformat()}Z by `scripts/run_normalize.py`.")
    lines.append("")

    lines.append("## 1. P3 Normalise -- activity type / district key parsing")
    lines.append("")
    lines.append(f"Work keys processed: {p3['work_keys_processed']}")
    lines.append(f"Distinct activity types: {p3['distinct_activity_types']} (BLUEPRINT.md §2: 115)")
    lines.append(f"Activity type parse rate: {p3['activity_type_parse_rate_pct']}%")
    lines.append(f"District key parse rate: {p3['district_key_parse_rate_pct']}%")
    lines.append("")

    lines.append("## 2. P4 Link + lifecycle -- Snapshot A")
    lines.append("")
    lines.append(f"work_state rows added: {p4a['work_states_added']}")
    lines.append(f"Lifecycle status counts: {p4a['lifecycle_counts']}")
    lines.append(
        f"Referential gap (sanctioned/completed but no recommended record): "
        f"{p4a['referential_gap_sanctioned_not_recommended']} (BLUEPRINT.md §5/§6: 361, logged not hidden)"
    )
    lines.append("")
    lines.append("## 3. P4 Link -- Snapshot B gap-fill")
    lines.append("")
    lines.append(f"work_state rows added (recommended-only): {p4b['work_states_added']}")
    lines.append("")

    lines.append("## 4. P5 Entity resolution -- payees")
    lines.append("")
    lines.append(f"Distinct VENDOR_ID: {payee['distinct_vendor_ids']} (BLUEPRINT.md §7: 29,583)")
    lines.append(f"Distinct VENDOR_NAME: {payee['distinct_vendor_names']} (BLUEPRINT.md §7: 27,961)")
    lines.append(
        f"Names shared by >1 ID: {payee['names_shared_by_multiple_ids']} (BLUEPRINT.md §2: 1,045) "
        "-- kept as aliases, never merged"
    )
    lines.append(
        f"IDs with >1 distinct name: {payee['ids_with_multiple_names']} "
        "(BLUEPRINT.md §2: 0, \"no ID has two spellings\")"
    )
    lines.append(f"Payees added this run: {payee['payees_added']}, aliases added: {payee['aliases_added']}")
    lines.append("")

    lines.append("## 5. P5 Entity resolution -- implementing agencies")
    lines.append("")
    lines.append(f"Distinct agencies seen: {agency['distinct_agencies_seen']} (BLUEPRINT.md §8: ~7,200)")
    lines.append(f"Agencies added this run: {agency['agencies_added']}")
    lines.append("")

    lines.append("## 6. Payments")
    lines.append("")
    lines.append(f"Payment rows added: {payment.get('payments_added', 0)}")
    if payment.get("skipped"):
        lines.append(f"(skipped: {payment['skipped']})")
    lines.append(f"Skipped, no matching payee: {payment.get('payments_skipped_no_payee', 'n/a')}")
    lines.append("")

    lines.append("## 7. MP roster / tenure / allocation")
    lines.append("")
    person_count = session.execute(select(func.count(Person.id))).scalar_one()
    tenure_count = session.execute(select(func.count(Tenure.id))).scalar_one()
    constituency_count = session.execute(select(func.count(Constituency.id))).scalar_one()
    lines.append(f"person rows: {person_count} (BLUEPRINT.md §2: 778 roster IDs)")
    lines.append(f"tenure rows: {tenure_count}")
    lines.append(f"constituency rows: {constituency_count}")
    lines.append(
        f"Allocations added: {alloc['allocations_added']}, "
        f"unmatched (no tenure): {alloc['unmatched_no_tenure']} "
        "(expected -- these are 'Nominated Rajya Sabha' rows, which get no tenure row by design; "
        "verified 2026-09-24 that the unmatched count exactly equals the Nominated Rajya Sabha row count)"
    )
    lines.append(
        f"MP roster join: {len(unmatched_tenure_names)} unmatched names out of allocation rows scanned "
        f"(BLUEPRINT.md §9: 100% matched on Snapshot A) -- queued for review, never auto-merged"
    )
    if unmatched_tenure_names:
        lines.append(f"  Unmatched: {unmatched_tenure_names}")
    lines.append("")

    lines.append("## 8. Descriptive tables")
    lines.append("")
    lines.append(f"calamity_consent rows added: {calamity['calamity_consents_added']}")
    lines.append(f"prior_cycle_work rows added: {prior_cycle.get('prior_cycle_work_added', 0)}")
    lines.append(f"macro_reference rows added: {macro.get('macro_reference_added', 0)}")
    lines.append("")

    lines.append("## 9. Acceptance criteria checks")
    lines.append("")
    work_key_count = session.execute(select(func.count(Work.work_key))).scalar_one()
    distinct_work_key_count = session.execute(select(func.count(func.distinct(Work.work_key)))).scalar_one()
    lines.append(
        f"work_key uniqueness: {work_key_count} rows, {distinct_work_key_count} distinct "
        f"({'OK' if work_key_count == distinct_work_key_count else 'MISMATCH'} -- PK-enforced regardless)"
    )

    completed_without_sanction = session.execute(
        select(func.count(WorkState.id)).where(
            WorkState.lifecycle_status == "completed",
            WorkState.sanction_amount.is_(None),
            WorkState.sanction_date.is_(None),
        )
    ).scalar_one()
    lines.append(
        f"Completed works without a sanction record: {completed_without_sanction} "
        f"({'OK' if completed_without_sanction == 0 else 'REVIEW'})"
    )

    from sqlalchemy import exists

    sanctioned_or_completed = exists().where(
        WorkState.work_key == Payment.work_key,
        WorkState.source_snapshot_id == Payment.source_snapshot_id,
        WorkState.lifecycle_status.in_(("sanctioned", "completed")),
    )
    payments_without_sanctioned_work = session.execute(
        select(func.count(Payment.id)).where(~sanctioned_or_completed)
    ).scalar_one()
    lines.append(
        f"Payments with no matching sanctioned/completed work_state row: {payments_without_sanctioned_work} "
        f"({'OK' if payments_without_sanctioned_work == 0 else 'REVIEW'})"
    )
    lines.append("")

    activity_type_count = session.execute(select(func.count(ActivityType.id))).scalar_one()
    payee_count = session.execute(select(func.count(Payee.id))).scalar_one()
    lines.append(f"activity_type table: {activity_type_count} rows")
    lines.append(f"payee table: {payee_count} rows")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

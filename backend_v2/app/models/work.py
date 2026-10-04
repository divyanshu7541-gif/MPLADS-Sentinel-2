"""
`work` table (BLUEPRINT.md §4 "Core (per snapshot)").

Phase 1 created this table with work_key + house + raw passthrough fields
and nullable FK columns left unpopulated. Phase 2 ALTERs it (never drops/
recreates -- the 128,883 House-tagged rows from Phase 1 must survive) to
add `description_normalized` (P3 Normalise) and backfill tenure_id/
district_authority_id/activity_type_id (P3/P4). work_state/payment/
allocation/calamity_consent/prior_cycle_work are separate Phase 2 tables
(see work_related.py).

Phase 5a (identity split): BLUEPRINT §2 assumed the portal
WORK_RECOMMENDATION_DTL_ID is unique across both Houses. It is not for 136
IDs (an LS recommended work and a different RS sanctioned work share one ID).
Work identity is therefore the compound (portal_id, house), enforced by a
unique constraint. `work_key` stays the table's surrogate primary key (every
FK already points at it): it equals the portal ID for every work except the
Rajya Sabha half of a shared ID, keyed "<portal_id>-RS". `portal_id` is a
generated column derived from work_key, so Phase 1/2 inserts are untouched.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from .reference import HOUSES

HOUSE_SOURCES = ("filename", "house_column")


class Work(Base):
    __tablename__ = "work"
    __table_args__ = (
        CheckConstraint(f"house IN {HOUSES}", name="ck_work_house"),
        CheckConstraint(f"house_source IN {HOUSE_SOURCES}", name="ck_work_house_source"),
        UniqueConstraint("portal_id", "house", name="uq_work_portal_id_house"),
    )

    work_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    portal_id: Mapped[str] = mapped_column(
        String(50), Computed("split_part(work_key, '-', 1)", persisted=True)
    )
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    house_source: Mapped[str] = mapped_column(String(20), nullable=False)
    first_seen_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    first_seen_raw_file_id: Mapped[int] = mapped_column(ForeignKey("raw_file.id"), nullable=False)
    raw_mp_name: Mapped[str | None] = mapped_column(String(200))
    raw_activity_name: Mapped[str | None] = mapped_column(Text)
    raw_description: Mapped[str | None] = mapped_column(Text)
    description_normalized: Mapped[str | None] = mapped_column(Text)
    tenure_id: Mapped[int | None] = mapped_column(ForeignKey("tenure.id"))
    district_authority_id: Mapped[int | None] = mapped_column(ForeignKey("district_authority.id"))
    activity_type_id: Mapped[int | None] = mapped_column(ForeignKey("activity_type.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkIdentitySplit(Base):
    """Audit row per shared portal ID split into two House-qualified works
    (Phase 5a). Records both resulting work keys and what each kept."""

    __tablename__ = "work_identity_split"
    __table_args__ = (UniqueConstraint("portal_id", name="uq_work_identity_split_portal_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portal_id: Mapped[str] = mapped_column(String(50), nullable=False)
    ls_work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    rs_work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    split_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

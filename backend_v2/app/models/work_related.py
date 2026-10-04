"""
Per-snapshot work state, payments, allocations, and the three descriptive
tables that are explicitly NOT linked into `work` (BLUEPRINT.md §4 Core):
calamity_consent, prior_cycle_work (no key), macro_reference.

work_state.lifecycle_status is derived ONLY from which files a work_key
appears in for a snapshot, plus its dates -- never from the portal's own
WORK_STAGE field. WORK_STAGE/FILE_STATUS are still stored, as raw_stage,
purely for audit -- nothing downstream (this phase or any later one) may
read raw_stage to decide anything (Phase 2 brief, P4 + acceptance criteria).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

LIFECYCLE_STATUSES = ("recommended", "sanctioned", "completed")


class WorkState(Base):
    __tablename__ = "work_state"
    __table_args__ = (UniqueConstraint("work_key", "source_snapshot_id", name="uq_work_state_work_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)

    lifecycle_status: Mapped[str] = mapped_column(String(20), nullable=False)

    recommended_amount: Mapped[float | None] = mapped_column(Numeric(20, 4))
    recommended_date: Mapped[dt.date | None] = mapped_column(Date)
    sanction_amount: Mapped[float | None] = mapped_column(Numeric(20, 4))
    sanction_date: Mapped[dt.date | None] = mapped_column(Date)
    actual_amount: Mapped[float | None] = mapped_column(Numeric(20, 4))
    actual_end_date: Mapped[dt.date | None] = mapped_column(Date)

    # Raw portal fields -- stored for audit only, never read by downstream logic.
    raw_stage: Mapped[str | None] = mapped_column(Text)
    raw_flag: Mapped[str | None] = mapped_column(String(10))
    raw_file_status: Mapped[str | None] = mapped_column(String(20))


class Payment(Base):
    __tablename__ = "payment"
    __table_args__ = (
        UniqueConstraint(
            "source_snapshot_id",
            "work_key",
            "payee_id",
            "payment_date",
            "amount",
            "occurrence_no",
            name="uq_payment_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    payee_id: Mapped[int] = mapped_column(ForeignKey("payee.id"), nullable=False)
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("implementing_agency.id"))
    payment_date: Mapped[dt.date | None] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    status: Mapped[str | None] = mapped_column(String(40))
    occurrence_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Allocation(Base):
    __tablename__ = "allocation"
    __table_args__ = (
        UniqueConstraint("tenure_id", "source_snapshot_id", name="uq_allocation_tenure_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenure_id: Mapped[int] = mapped_column(ForeignKey("tenure.id"), nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    allocated_amount: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)


class CalamityConsent(Base):
    """Small descriptive table -- not linked to `work` (no work key in
    the source file)."""

    __tablename__ = "calamity_consent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    calamity_name: Mapped[str | None] = mapped_column(String(200))
    tenure_label_raw: Mapped[str | None] = mapped_column(String(50))
    mp_raw_name: Mapped[str | None] = mapped_column(String(200))
    consent_date: Mapped[dt.date | None] = mapped_column(Date)
    sno: Mapped[int | None] = mapped_column(Integer)
    consented_amount: Mapped[float | None] = mapped_column(Numeric(20, 4))
    calamity_type: Mapped[str | None] = mapped_column(String(30))


class PriorCycleWork(Base):
    """Separate from `work` -- BLUEPRINT.md §4: "Village, block, city,
    ward; no key; separate from work". 60,359 rows, no work_key exists in
    the source file to link on."""

    __tablename__ = "prior_cycle_work"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    mp_raw_name: Mapped[str | None] = mapped_column(String(200))
    work_description: Mapped[str | None] = mapped_column(Text)
    category_raw: Mapped[str | None] = mapped_column(String(100))
    state_name: Mapped[str | None] = mapped_column(String(100))
    constituency_name: Mapped[str | None] = mapped_column(String(200))
    ida_name: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(200))
    ward: Mapped[str | None] = mapped_column(String(200))
    block: Mapped[str | None] = mapped_column(String(200))
    village: Mapped[str | None] = mapped_column(String(200))
    recommended_date: Mapped[dt.date | None] = mapped_column(Date)
    allocation_amount: Mapped[float | None] = mapped_column(Numeric(20, 4))
    ida_approval_status: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str | None] = mapped_column(String(50))


class MacroReference(Base):
    __tablename__ = "macro_reference"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)  # 'state' | 'national'
    state_name: Mapped[str | None] = mapped_column(String(100))
    fiscal_year: Mapped[str | None] = mapped_column(String(20))
    measure: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric(20, 4))
    source_document: Mapped[str] = mapped_column(String(200), nullable=False)
    as_of_date: Mapped[dt.date | None] = mapped_column(Date)

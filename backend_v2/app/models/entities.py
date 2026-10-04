"""
Entity-resolution tables (BLUEPRINT.md §4 "Reference and identity" +
§8 payee/agency typing): payee, payee_alias, implementing_agency.

Payee identity is the portal VENDOR_ID -- verified against Snapshot A
(Phase 2 research, 2026-09-24): 29,583 distinct IDs, 27,961 distinct names,
1,045 names shared by more than one ID, ZERO IDs with more than one name
(BLUEPRINT.md §2's "no ID has two spellings", confirmed exactly). Names are
therefore never used as an identity key and never merge two IDs -- every
raw name seen for an ID is kept in payee_alias, not collapsed.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Payee(Base):
    """PK is the portal VENDOR_ID -- already a stable unique integer."""

    __tablename__ = "payee"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    payee_type: Mapped[str] = mapped_column(String(30), nullable=False, default="unclassified")
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unreviewed")
    first_seen_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)


class PayeeAlias(Base):
    """Every distinct raw name seen for a payee id -- inserted, never
    merged into canonical_name or used to join across ids."""

    __tablename__ = "payee_alias"
    __table_args__ = (UniqueConstraint("payee_id", "name", name="uq_payee_alias_payee_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payee_id: Mapped[int] = mapped_column(ForeignKey("payee.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImplementingAgency(Base):
    __tablename__ = "implementing_agency"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ia_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    agency_type: Mapped[str] = mapped_column(String(30), nullable=False, default="unclassified")
    first_seen_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)

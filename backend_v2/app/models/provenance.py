"""
Ingest and provenance tables (BLUEPRINT.md §4 "Ingest and provenance").

source_snapshot -> raw_file -> raw_row is the insert-only ledger every other
table is ultimately derived from. control_total and import_reject are the
P2/P1 gate outputs.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base


class SourceSnapshot(Base):
    __tablename__ = "source_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    portal_address: Mapped[str | None] = mapped_column(String(500))
    retrieval_method: Mapped[str | None] = mapped_column(String(200))
    data_as_of: Mapped[dt.date | None] = mapped_column(Date)
    imported_by: Mapped[str] = mapped_column(String(100), nullable=False)
    imported_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="registered")
    notes: Mapped[str | None] = mapped_column(Text)

    raw_files: Mapped[list["RawFile"]] = relationship(back_populates="source_snapshot")


class RawFile(Base):
    __tablename__ = "raw_file"
    __table_args__ = (
        UniqueConstraint("source_snapshot_id", "filename", name="uq_raw_file_snapshot_filename"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delimiter: Mapped[str] = mapped_column(String(1), nullable=False, default=",")
    footer_total: Mapped[float | None] = mapped_column(Numeric(20, 4))
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    registered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_snapshot: Mapped["SourceSnapshot"] = relationship(back_populates="raw_files")


class RawRow(Base):
    """Insert-only. Nothing in this table is ever updated or deleted by the
    pipeline -- a corrected import creates a new snapshot instead."""

    __tablename__ = "raw_row"
    __table_args__ = (UniqueConstraint("raw_file_id", "row_no", name="uq_raw_row_file_rowno"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_file_id: Mapped[int] = mapped_column(ForeignKey("raw_file.id"), nullable=False)
    row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ControlTotal(Base):
    __tablename__ = "control_total"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    raw_file_id: Mapped[int] = mapped_column(ForeignKey("raw_file.id"), nullable=False)
    measure: Mapped[str] = mapped_column(String(100), nullable=False)
    body_sum_rupees: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    footer_total_rupees: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    portal_value_crore: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    computed_value_crore: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    footer_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    blueprint_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportReject(Base):
    __tablename__ = "import_reject"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_file_id: Mapped[int] = mapped_column(ForeignKey("raw_file.id"), nullable=False)
    row_no: Mapped[int | None] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String(60), nullable=False)
    reason_detail: Mapped[str] = mapped_column(Text, nullable=False)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    rejected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

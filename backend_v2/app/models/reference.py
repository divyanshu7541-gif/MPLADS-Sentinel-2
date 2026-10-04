"""
Reference and identity tables (BLUEPRINT.md §4 "Reference and identity").

activity_type is schema-only in Phase 1 -- parsing the 115 official work
types out of ACTIVITY_NAME is a P3 Normalise task (BLUEPRINT.md §5), a later
phase. tenure is deliberately restricted to the two live labels named in the
Phase 1 brief ("18th Lok Sabha", "Sitting MP"); "Nominated Rajya Sabha" is a
third label BLUEPRINT.md §4 allows but no Phase 1 seed path creates it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

TENURE_LABELS = ("18th Lok Sabha", "Sitting MP")
HOUSES = ("LS", "RS")


class State(Base):
    __tablename__ = "state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    portal_state_id: Mapped[int | None] = mapped_column(Integer)


class StateAlias(Base):
    __tablename__ = "state_alias"
    __table_args__ = (UniqueConstraint("state_id", "alias", name="uq_state_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state_id: Mapped[int] = mapped_column(ForeignKey("state.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)


class DistrictAuthority(Base):
    __tablename__ = "district_authority"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ida_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    district_key: Mapped[str | None] = mapped_column(String(200))
    state_id: Mapped[int | None] = mapped_column(ForeignKey("state.id"))
    first_seen_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshot.id"))


class ActivityType(Base):
    """Schema only in Phase 1 -- left empty until P3 Normalise (a later phase)."""

    __tablename__ = "activity_type"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str | None] = mapped_column(String(50), unique=True)
    label: Mapped[str | None] = mapped_column(String(300))


class Person(Base):
    """PK is the portal roster id (mp_names_all_states.csv's ID column) --
    already a stable unique integer, so no surrogate key is introduced."""

    __tablename__ = "person"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    caption: Mapped[str] = mapped_column(String(200), nullable=False)
    state_id: Mapped[int | None] = mapped_column(ForeignKey("state.id"))
    house_raw_code: Mapped[str | None] = mapped_column(String(1))
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)


class Constituency(Base):
    """Lok Sabha only -- Rajya Sabha members have no constituency
    (BLUEPRINT.md §9, §15). Name alone is not a durable key long-term
    (BLUEPRINT.md §4), but (name, state) is the Phase 1 interim key;
    period-scoping is deferred."""

    __tablename__ = "constituency"
    __table_args__ = (UniqueConstraint("name", "state_id", name="uq_constituency_name_state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    state_id: Mapped[int | None] = mapped_column(ForeignKey("state.id"))
    house: Mapped[str] = mapped_column(String(2), nullable=False, default="LS")
    source_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshot.id"))


class Tenure(Base):
    __tablename__ = "tenure"
    __table_args__ = (
        UniqueConstraint("person_id", "tenure_label", name="uq_tenure_person_label"),
        CheckConstraint(f"tenure_label IN {TENURE_LABELS}", name="ck_tenure_label"),
        CheckConstraint(f"house IN {HOUSES}", name="ck_tenure_house"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"), nullable=False)
    tenure_label: Mapped[str] = mapped_column(String(30), nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    constituency_id: Mapped[int | None] = mapped_column(ForeignKey("constituency.id"))
    start_date: Mapped[dt.date | None] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    allocated_amount: Mapped[float | None] = mapped_column(Numeric(20, 4))
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

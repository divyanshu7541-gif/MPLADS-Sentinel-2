"""
Phase 13 tables: users (authentication/authorisation) and the randomised
audit sample (BLUEPRINT.md §11 Controls, §12 "Randomised audit sample").

app_user
  One row per login. `role` decides what an account may do; the scope_*
  columns decide WHICH works it may see, and they are applied inside the
  SQL of every data query (app/auth/scope.py), never only at the route.
  NULL scope column = unrestricted on that axis; all NULL = national.
  The password is stored only as a salted scrypt hash (app/auth/passwords.py).

audit_sample / audit_sample_item / audit_review
  A tier-stratified random sample of scored works drawn from one analysis
  run with a recorded seed (app/audit/sampling.py). Reviewers see items by
  a blind code with no tier, score or signal; `stratum` (the tier) is read
  only by the report. Each review's reviewer comes from the token, like
  case_event's actor.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ROLES = ("admin", "ministry", "state", "district", "mp", "investigator", "supervisor", "auditor")
AUDIT_OUTCOMES = ("follow_up_needed", "no_follow_up", "data_issue")
AUDIT_STRATA = ("CRITICAL", "HIGH", "MODERATE", "LOW")


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint(f"role IN {ROLES}", name="ck_app_user_role"),
        CheckConstraint("scope_house IS NULL OR scope_house IN ('LS', 'RS')", name="ck_app_user_house"),
        # Scoped roles must carry the scope that defines them.
        CheckConstraint("role <> 'state' OR scope_state IS NOT NULL", name="ck_app_user_state_scope"),
        CheckConstraint(
            "role <> 'district' OR scope_district_authority_id IS NOT NULL", name="ck_app_user_district_scope"
        ),
        CheckConstraint("role <> 'mp' OR scope_mp IS NOT NULL", name="ck_app_user_mp_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_state: Mapped[str | None] = mapped_column(String(200))  # served_work.state (location state)
    scope_district_authority_id: Mapped[int | None] = mapped_column(ForeignKey("district_authority.id"))
    scope_house: Mapped[str | None] = mapped_column(String(2))
    scope_mp: Mapped[str | None] = mapped_column(String(200))  # served_work.mp (current tenure)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    password_changed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditSample(Base):
    __tablename__ = "audit_sample"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    config_name: Mapped[str] = mapped_column(String(20), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    design: Mapped[dict] = mapped_column(JSONB, nullable=False)  # quotas, population sizes, drawn counts
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Phase 13.y: a sample drawn on a superseded run is archived, never deleted.
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    archive_reason: Mapped[str | None] = mapped_column(Text)


class AuditSampleItem(Base):
    __tablename__ = "audit_sample_item"
    __table_args__ = (
        UniqueConstraint("sample_id", "work_key", name="uq_audit_sample_item_work"),
        UniqueConstraint("blind_code", name="uq_audit_sample_item_blind"),
        CheckConstraint(f"stratum IN {AUDIT_STRATA}", name="ck_audit_sample_item_stratum"),
        Index("ix_audit_sample_item_sample_pos", "sample_id", "position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("audit_sample.id", ondelete="CASCADE"), nullable=False)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    stratum: Mapped[str] = mapped_column(String(15), nullable=False)  # the tier -- never sent to reviewers
    blind_code: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # review order, shuffled across strata


class AuditReview(Base):
    __tablename__ = "audit_review"
    __table_args__ = (
        UniqueConstraint("item_id", "reviewer_user_id", name="uq_audit_review_item_reviewer"),
        CheckConstraint(f"outcome IN {AUDIT_OUTCOMES}", name="ck_audit_review_outcome"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("audit_sample_item.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(120), nullable=False)  # from the token, never the body
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

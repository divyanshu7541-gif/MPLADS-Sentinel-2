"""phase13 security audit sample

Phase 13 (BLUEPRINT.md §11, §12): app_user (token login, roles, scopes),
the randomised audit sample (audit_sample / audit_sample_item /
audit_review), and case_event's authenticated actor + hash chain.

Create/add only. Autogenerate also proposed unrelated drift (NOT NULL
alters on map_build.built_at, model_version.created_at,
published_run.published_at, work_identity_split.split_at, and dropping
ix_payment_work_key); those were removed here exactly as in the Phase 12
migration (b400c17b8eb1) -- they belong to earlier phases' tables and are
not this phase's to change.

Revision ID: 99c1d3031fb9
Revises: b400c17b8eb1
Create Date: 2026-09-27 20:00:03.680367

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "99c1d3031fb9"
down_revision: Union[str, None] = "b400c17b8eb1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=300), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("scope_state", sa.String(length=200), nullable=True),
        sa.Column("scope_district_authority_id", sa.Integer(), nullable=True),
        sa.Column("scope_house", sa.String(length=2), nullable=True),
        sa.Column("scope_mp", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "password_changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "role <> 'district' OR scope_district_authority_id IS NOT NULL", name="ck_app_user_district_scope"
        ),
        sa.CheckConstraint("role <> 'mp' OR scope_mp IS NOT NULL", name="ck_app_user_mp_scope"),
        sa.CheckConstraint("role <> 'state' OR scope_state IS NOT NULL", name="ck_app_user_state_scope"),
        sa.CheckConstraint(
            "role IN ('admin', 'ministry', 'state', 'district', 'mp', 'investigator', 'supervisor', 'auditor')",
            name="ck_app_user_role",
        ),
        sa.CheckConstraint("scope_house IS NULL OR scope_house IN ('LS', 'RS')", name="ck_app_user_house"),
        sa.ForeignKeyConstraint(["scope_district_authority_id"], ["district_authority.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "audit_sample",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("config_name", sa.String(length=20), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("design", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_sample_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("work_key", sa.String(length=50), nullable=False),
        sa.Column("stratum", sa.String(length=15), nullable=False),
        sa.Column("blind_code", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "stratum IN ('CRITICAL', 'HIGH', 'MODERATE', 'LOW')", name="ck_audit_sample_item_stratum"
        ),
        sa.ForeignKeyConstraint(["sample_id"], ["audit_sample.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_key"], ["work.work_key"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blind_code", name="uq_audit_sample_item_blind"),
        sa.UniqueConstraint("sample_id", "work_key", name="uq_audit_sample_item_work"),
    )
    op.create_index(
        "ix_audit_sample_item_sample_pos", "audit_sample_item", ["sample_id", "position"], unique=False
    )
    op.create_table(
        "audit_review",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_user_id", sa.Integer(), nullable=False),
        sa.Column("reviewer", sa.String(length=120), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('follow_up_needed', 'no_follow_up', 'data_issue')", name="ck_audit_review_outcome"
        ),
        sa.ForeignKeyConstraint(["item_id"], ["audit_sample_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "reviewer_user_id", name="uq_audit_review_item_reviewer"),
    )
    op.add_column("case_event", sa.Column("actor_user_id", sa.Integer(), nullable=True))
    op.add_column("case_event", sa.Column("prev_hash", sa.String(length=64), nullable=True))
    op.add_column("case_event", sa.Column("event_hash", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_case_event_actor_user_id_app_user", "case_event", "app_user", ["actor_user_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_case_event_actor_user_id_app_user", "case_event", type_="foreignkey")
    op.drop_column("case_event", "event_hash")
    op.drop_column("case_event", "prev_hash")
    op.drop_column("case_event", "actor_user_id")
    op.drop_table("audit_review")
    op.drop_index("ix_audit_sample_item_sample_pos", table_name="audit_sample_item")
    op.drop_table("audit_sample_item")
    op.drop_table("audit_sample")
    op.drop_table("app_user")

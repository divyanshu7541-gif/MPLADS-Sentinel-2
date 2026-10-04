"""phase5 risk and compliance

Adds risk_result (one row per run x work x fusion config -- both
v3-compatible and v4-candidate are stored, never overwriting each other)
and compliance_result (C1-C9, one row per run x check x rule x subject).
Create-only: no existing table is altered or dropped.

Revision ID: c5a1e7f2d9b4
Revises: b28b3359d3b5
Create Date: 2026-09-25 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c5a1e7f2d9b4'
down_revision: Union[str, None] = 'b28b3359d3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('risk_result',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('work_key', sa.String(length=50), nullable=False),
    sa.Column('house', sa.String(length=2), nullable=False),
    sa.Column('config_name', sa.String(length=20), nullable=False),
    sa.Column('config_hash', sa.String(length=64), nullable=False),
    sa.Column('risk', sa.Float(), nullable=True),
    sa.Column('tier', sa.String(length=15), nullable=False),
    sa.Column('critical_demoted', sa.Boolean(), nullable=False),
    sa.Column('base_signal_count', sa.Integer(), nullable=False),
    sa.Column('n_eligible', sa.Integer(), nullable=False),
    sa.Column('corroboration_factor', sa.Float(), nullable=False),
    sa.Column('pattern_score', sa.Float(), nullable=True),
    sa.Column('pre_multiplier', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('confidence_components', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint("config_name IN ('v3-compatible', 'v4-candidate')", name='ck_risk_result_config'),
    sa.CheckConstraint("tier IN ('LOW', 'MODERATE', 'HIGH', 'CRITICAL', 'NOT_EVALUATED')", name='ck_risk_result_tier'),
    sa.CheckConstraint('risk IS NULL OR (risk >= 0 AND risk <= 100)', name='ck_risk_result_risk_range'),
    sa.CheckConstraint("(risk IS NULL) = (tier = 'NOT_EVALUATED')", name='ck_risk_result_null_iff_not_evaluated'),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name='ck_risk_result_confidence_range'),
    sa.CheckConstraint('n_eligible >= 0 AND n_eligible <= 6', name='ck_risk_result_n_eligible'),
    sa.CheckConstraint('base_signal_count >= 0 AND base_signal_count <= n_eligible', name='ck_risk_result_active_le_eligible'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.ForeignKeyConstraint(['work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'work_key', 'config_name', name='uq_risk_result_run_work_config')
    )
    op.create_index('ix_risk_result_run_config_tier_risk', 'risk_result', ['run_id', 'config_name', 'tier', 'risk'], unique=False)
    op.create_index('ix_risk_result_run_house', 'risk_result', ['run_id', 'house'], unique=False)

    op.create_table('compliance_result',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('check_code', sa.String(length=3), nullable=False),
    sa.Column('rule', sa.String(length=40), nullable=False),
    sa.Column('subject_type', sa.String(length=5), nullable=False),
    sa.Column('subject_key', sa.String(length=300), nullable=False),
    sa.Column('work_key', sa.String(length=50), nullable=True),
    sa.Column('house', sa.String(length=2), nullable=True),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint("check_code IN ('C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8', 'C9')", name='ck_compliance_result_check'),
    sa.CheckConstraint("subject_type IN ('work', 'mp', 'file')", name='ck_compliance_result_subject'),
    sa.CheckConstraint("(subject_type = 'work') = (work_key IS NOT NULL)", name='ck_compliance_result_work_key_iff_work'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.ForeignKeyConstraint(['work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'check_code', 'rule', 'subject_type', 'subject_key', name='uq_compliance_result_run_check_subject')
    )
    op.create_index('ix_compliance_result_run_check', 'compliance_result', ['run_id', 'check_code', 'passed'], unique=False)
    op.create_index('ix_compliance_result_run_work', 'compliance_result', ['run_id', 'work_key'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_compliance_result_run_work', table_name='compliance_result')
    op.drop_index('ix_compliance_result_run_check', table_name='compliance_result')
    op.drop_table('compliance_result')
    op.drop_index('ix_risk_result_run_house', table_name='risk_result')
    op.drop_index('ix_risk_result_run_config_tier_risk', table_name='risk_result')
    op.drop_table('risk_result')

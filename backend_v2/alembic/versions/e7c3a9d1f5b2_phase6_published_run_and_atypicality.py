"""phase6 published_run pointer, model_version registry, atypicality_result

Create-only, plus one index. published_run is the single-row pointer from
BLUEPRINT.md §4 (which run is served, which fusion config is the default).
model_version is the §7 model registry. atypicality_result holds the §7 B4
evidence layer, deliberately separate from signal_result so Phase 5 fusion
never reads it. Also adds the payment(work_key) index BLUEPRINT §4 lists but
no earlier migration created (per-work payment lookups were full scans).

Revision ID: e7c3a9d1f5b2
Revises: d6b2f4a8c1e3
Create Date: 2026-09-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e7c3a9d1f5b2'
down_revision: Union[str, None] = 'd6b2f4a8c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('published_run',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('default_config_name', sa.String(length=20), nullable=False),
    sa.Column('default_config_hash', sa.String(length=64), nullable=False),
    sa.Column('decision_ref', sa.Text(), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.CheckConstraint('id = 1', name='ck_published_run_single_row'),
    sa.CheckConstraint("default_config_name IN ('v3-compatible', 'v4-candidate')", name='ck_published_run_config'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('model_version',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('model_name', sa.String(length=60), nullable=False),
    sa.Column('algorithm', sa.String(length=120), nullable=False),
    sa.Column('feature_spec_hash', sa.String(length=64), nullable=False),
    sa.Column('training_snapshot_id', sa.Integer(), nullable=False),
    sa.Column('seed', sa.Integer(), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('artifact_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.ForeignKeyConstraint(['training_snapshot_id'], ['source_snapshot.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'model_name', name='uq_model_version_run_model')
    )
    op.create_table('atypicality_result',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('work_key', sa.String(length=50), nullable=False),
    sa.Column('house', sa.String(length=2), nullable=False),
    sa.Column('method', sa.String(length=30), nullable=False),
    sa.Column('model_version_id', sa.Integer(), nullable=False),
    sa.Column('eligible', sa.Boolean(), nullable=False),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('percentile', sa.Float(), nullable=True),
    sa.Column('contributions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('missing_features', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint("method IN ('robust_mahalanobis', 'isolation_forest')", name='ck_atypicality_method'),
    sa.CheckConstraint('(score IS NULL) = (NOT eligible)', name='ck_atypicality_null_iff_not_evaluated'),
    sa.CheckConstraint('percentile IS NULL OR (percentile >= 0 AND percentile <= 1)', name='ck_atypicality_percentile_range'),
    sa.ForeignKeyConstraint(['model_version_id'], ['model_version.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.ForeignKeyConstraint(['work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'work_key', 'method', name='uq_atypicality_run_work_method')
    )
    op.create_index('ix_atypicality_run_method', 'atypicality_result', ['run_id', 'method'], unique=False)
    op.create_index('ix_payment_work_key', 'payment', ['work_key'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_payment_work_key', table_name='payment')
    op.drop_index('ix_atypicality_run_method', table_name='atypicality_result')
    op.drop_table('atypicality_result')
    op.drop_table('model_version')
    op.drop_table('published_run')

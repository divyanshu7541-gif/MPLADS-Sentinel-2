"""phase7 survival_result

Create-only: per-work evidence table for BLUEPRINT §7 A1/A2. No rows are
written until an explicit --register run after owner sign-off.

Revision ID: a9e5c1f3b7d4
Revises: f8d4b0e2a6c3
Create Date: 2026-09-26 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a9e5c1f3b7d4'
down_revision: Union[str, None] = 'f8d4b0e2a6c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('survival_result',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('work_key', sa.String(length=50), nullable=False),
    sa.Column('house', sa.String(length=2), nullable=False),
    sa.Column('model', sa.String(length=20), nullable=False),
    sa.Column('model_version_id', sa.Integer(), nullable=True),
    sa.Column('eligible', sa.Boolean(), nullable=False),
    sa.Column('event', sa.Integer(), nullable=True),
    sa.Column('duration_days', sa.Float(), nullable=True),
    sa.Column('prediction', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('not_evaluated_reason', sa.String(length=40), nullable=True),
    sa.CheckConstraint("model IN ('a1_cox', 'a2_day90', 'a2_day180')", name='ck_survival_result_model'),
    sa.CheckConstraint("eligible OR prediction = '{}'::jsonb", name='ck_survival_result_no_pred_if_ineligible'),
    sa.ForeignKeyConstraint(['model_version_id'], ['model_version.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.ForeignKeyConstraint(['work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'work_key', 'model', name='uq_survival_result_run_work_model')
    )
    op.create_index('ix_survival_result_run_model', 'survival_result', ['run_id', 'model'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_survival_result_run_model', table_name='survival_result')
    op.drop_table('survival_result')

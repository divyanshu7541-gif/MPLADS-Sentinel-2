"""phase6 work_evidence_fact

Create-only: plain evidence facts per work (first fact: a work pays the same
payee more than once). Not scores; never read by Phase 5 fusion.

Revision ID: f8d4b0e2a6c3
Revises: e7c3a9d1f5b2
Create Date: 2026-09-26 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f8d4b0e2a6c3'
down_revision: Union[str, None] = 'e7c3a9d1f5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('work_evidence_fact',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('work_key', sa.String(length=50), nullable=False),
    sa.Column('house', sa.String(length=2), nullable=False),
    sa.Column('fact', sa.String(length=60), nullable=False),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint("fact IN ('pays_same_payee_more_than_once')", name='ck_work_evidence_fact_fact'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.ForeignKeyConstraint(['work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'work_key', 'fact', name='uq_work_evidence_fact_run_work_fact')
    )
    op.create_index('ix_work_evidence_fact_run_fact', 'work_evidence_fact', ['run_id', 'fact'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_work_evidence_fact_run_fact', table_name='work_evidence_fact')
    op.drop_table('work_evidence_fact')

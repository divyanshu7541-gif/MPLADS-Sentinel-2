"""phase5a work identity split

ALTER only (never drop/recreate `work`): adds the generated column
work.portal_id = split_part(work_key, '-', 1) and the unique compound
identity (portal_id, house), plus the work_identity_split audit table.

Revision ID: d6b2f4a8c1e3
Revises: c5a1e7f2d9b4
Create Date: 2026-09-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd6b2f4a8c1e3'
down_revision: Union[str, None] = 'c5a1e7f2d9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('work', sa.Column(
        'portal_id', sa.String(length=50), sa.Computed("split_part(work_key, '-', 1)", persisted=True)))
    op.create_unique_constraint('uq_work_portal_id_house', 'work', ['portal_id', 'house'])
    op.create_table('work_identity_split',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('portal_id', sa.String(length=50), nullable=False),
    sa.Column('ls_work_key', sa.String(length=50), nullable=False),
    sa.Column('rs_work_key', sa.String(length=50), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('split_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['ls_work_key'], ['work.work_key'], ),
    sa.ForeignKeyConstraint(['rs_work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('portal_id', name='uq_work_identity_split_portal_id')
    )


def downgrade() -> None:
    op.drop_table('work_identity_split')
    op.drop_constraint('uq_work_portal_id_house', 'work', type_='unique')
    op.drop_column('work', 'portal_id')

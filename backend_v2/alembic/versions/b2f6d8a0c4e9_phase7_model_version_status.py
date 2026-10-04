"""phase7 model_version status

ALTER only: adds model_version.status ('active' | 'inactive_experiment',
default 'active', so existing Phase 6 rows stay active) and status_note.
Phase 7's A1 survival model is recorded as an inactive experiment.

Revision ID: b2f6d8a0c4e9
Revises: a9e5c1f3b7d4
Create Date: 2026-09-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b2f6d8a0c4e9'
down_revision: Union[str, None] = 'a9e5c1f3b7d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('model_version', sa.Column('status', sa.String(length=30), server_default='active', nullable=False))
    op.add_column('model_version', sa.Column('status_note', sa.Text(), nullable=True))
    op.create_check_constraint('ck_model_version_status', 'model_version', "status IN ('active', 'inactive_experiment')")


def downgrade() -> None:
    op.drop_constraint('ck_model_version_status', 'model_version', type_='check')
    op.drop_column('model_version', 'status_note')
    op.drop_column('model_version', 'status')

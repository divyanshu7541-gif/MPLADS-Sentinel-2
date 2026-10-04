"""phase13y audit sample archive

Phase 13.y step 4: an audit sample drawn on a superseded run is archived, never
deleted -- its draw, items and any reviews stay as the record. Two nullable
columns on audit_sample; add only.

Revision ID: 3f1a7c9e2b50
Revises: 99c1d3031fb9
Create Date: 2026-09-28 12:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "3f1a7c9e2b50"
down_revision: Union[str, None] = "99c1d3031fb9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_sample", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("audit_sample", sa.Column("archive_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_sample", "archive_reason")
    op.drop_column("audit_sample", "archived_at")

"""baseline (no schema yet -- Phase 0 scaffolding)

Revision ID: 0001
Revises:
Create Date: 2026-09-23

"""
from typing import Sequence, Union

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No tables yet. This revision exists to prove the Alembic pipeline
    # (env.py -> DATABASE_URL -> migration history) works end to end before
    # any real models are added in a later phase.
    pass


def downgrade() -> None:
    pass

"""phase7 rename survival_result -> forecast_result

Rename only (no data change beyond one note): SENTINEL_REBUILD_PLAN_v2.md §7 and
BLUEPRINT.md §4 name this table `forecast_result`. Renames the table, its
constraints, indexes and id sequence, and updates the inactive A1
model_version row's note to the new table name. The table holds 0 rows.

Revision ID: c3a7e9b1d5f0
Revises: b2f6d8a0c4e9
Create Date: 2026-09-27 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c3a7e9b1d5f0'
down_revision: Union[str, None] = 'b2f6d8a0c4e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINTS = (
    'ck_{t}_model', 'ck_{t}_no_pred_if_ineligible', '{t}_model_version_id_fkey', '{t}_pkey',
    '{t}_run_id_fkey', '{t}_work_key_fkey', 'uq_{t}_run_work_model',
)


def _rename(old: str, new: str) -> None:
    op.rename_table(old, new)
    for c in _CONSTRAINTS:
        op.execute(f'ALTER TABLE {new} RENAME CONSTRAINT {c.format(t=old)} TO {c.format(t=new)}')
    op.execute(f'ALTER INDEX ix_{old}_run_model RENAME TO ix_{new}_run_model')
    op.execute(f'ALTER SEQUENCE {old}_id_seq RENAME TO {new}_id_seq')
    op.execute(
        f"UPDATE model_version SET status_note = replace(status_note, '{old}', '{new}') "
        "WHERE model_name = 'a1_cox_completion_time'"
    )


def upgrade() -> None:
    _rename('survival_result', 'forecast_result')


def downgrade() -> None:
    _rename('forecast_result', 'survival_result')

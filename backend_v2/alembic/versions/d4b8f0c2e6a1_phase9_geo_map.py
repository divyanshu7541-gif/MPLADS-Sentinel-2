"""phase9 geo/map backend

ALTER geo_area (representative point, attrs) and geo_name_crosswalk (portal
state, detail, wider method). CREATE authority_geo, work_geo, map_build,
map_work (indexed, pg_trgm search), geo_metric. Enables pg_trgm (BLUEPRINT §3
lists PostgreSQL 16 with pg_trgm). No risk_result / signal_result change.

Revision ID: d4b8f0c2e6a1
Revises: c3a7e9b1d5f0
Create Date: 2026-09-26 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd4b8f0c2e6a1'
down_revision: Union[str, None] = 'c3a7e9b1d5f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column('geo_area', sa.Column('rep_lat', sa.Float(), nullable=True))
    op.add_column('geo_area', sa.Column('rep_lon', sa.Float(), nullable=True))
    op.add_column('geo_area', sa.Column('attrs', JSONB, server_default='{}', nullable=False))
    op.alter_column('geo_name_crosswalk', 'method', type_=sa.String(length=40), existing_nullable=False)
    op.add_column('geo_name_crosswalk', sa.Column('portal_state', sa.String(length=200), nullable=True))
    op.add_column('geo_name_crosswalk', sa.Column('detail', JSONB, server_default='{}', nullable=False))

    op.create_table('authority_geo',
    sa.Column('district_authority_id', sa.Integer(), nullable=False),
    sa.Column('district_key', sa.String(length=200), nullable=True),
    sa.Column('stored_state_id', sa.Integer(), nullable=True),
    sa.Column('resolved_state_id', sa.Integer(), nullable=True),
    sa.Column('district_area_id', sa.Integer(), nullable=True),
    sa.Column('method', sa.String(length=40), nullable=False),
    sa.Column('state_differs', sa.Boolean(), nullable=False),
    sa.Column('detail', JSONB, server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['district_area_id'], ['geo_area.id'], ),
    sa.ForeignKeyConstraint(['district_authority_id'], ['district_authority.id'], ),
    sa.ForeignKeyConstraint(['resolved_state_id'], ['state.id'], ),
    sa.ForeignKeyConstraint(['stored_state_id'], ['state.id'], ),
    sa.PrimaryKeyConstraint('district_authority_id')
    )
    op.create_table('work_geo',
    sa.Column('work_key', sa.String(length=50), nullable=False),
    sa.Column('house', sa.String(length=2), nullable=False),
    sa.Column('constituency_id', sa.Integer(), nullable=True),
    sa.Column('constituency_area_id', sa.Integer(), nullable=True),
    sa.Column('constituency_status', sa.String(length=40), nullable=False),
    sa.Column('district_authority_id', sa.Integer(), nullable=True),
    sa.Column('district_area_id', sa.Integer(), nullable=True),
    sa.Column('location_state_id', sa.Integer(), nullable=True),
    sa.Column('location_method', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['constituency_area_id'], ['geo_area.id'], ),
    sa.ForeignKeyConstraint(['constituency_id'], ['constituency.id'], ),
    sa.ForeignKeyConstraint(['district_area_id'], ['geo_area.id'], ),
    sa.ForeignKeyConstraint(['district_authority_id'], ['district_authority.id'], ),
    sa.ForeignKeyConstraint(['location_state_id'], ['state.id'], ),
    sa.ForeignKeyConstraint(['work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('work_key')
    )
    op.create_index('ix_work_geo_constituency_area', 'work_geo', ['constituency_area_id'], unique=False)
    op.create_table('map_build',
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('config_name', sa.String(length=20), nullable=False),
    sa.Column('data_as_of', sa.Date(), nullable=True),
    sa.Column('counts', JSONB, server_default='{}', nullable=False),
    sa.Column('built_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.CheckConstraint("status IN ('building', 'complete', 'failed')", name='ck_map_build_status'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.PrimaryKeyConstraint('run_id')
    )
    op.create_table('map_work',
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('work_key', sa.String(length=50), nullable=False),
    sa.Column('house', sa.String(length=2), nullable=False),
    sa.Column('state', sa.String(length=200), nullable=True),
    sa.Column('constituency', sa.String(length=200), nullable=True),
    sa.Column('constituency_state', sa.String(length=200), nullable=True),
    sa.Column('constituency_area_id', sa.Integer(), nullable=True),
    sa.Column('district_area_id', sa.Integer(), nullable=True),
    sa.Column('district_name', sa.String(length=200), nullable=True),
    sa.Column('latitude', sa.Float(), nullable=True),
    sa.Column('longitude', sa.Float(), nullable=True),
    sa.Column('tier', sa.String(length=15), nullable=False),
    sa.Column('risk', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('active_signals', sa.Integer(), nullable=True),
    sa.Column('amount', sa.Float(), nullable=True),
    sa.Column('stage', sa.String(length=20), nullable=True),
    sa.Column('category', sa.String(length=300), nullable=True),
    sa.Column('mp', sa.String(length=200), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('record_date', sa.Date(), nullable=True),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['constituency_area_id'], ['geo_area.id'], ),
    sa.ForeignKeyConstraint(['district_area_id'], ['geo_area.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.ForeignKeyConstraint(['work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('run_id', 'work_key')
    )
    op.create_index('ix_map_work_run_state', 'map_work', ['run_id', 'state'], unique=False)
    op.create_index('ix_map_work_run_constituency', 'map_work', ['run_id', 'constituency_state', 'constituency'], unique=False)
    op.create_index('ix_map_work_run_tier', 'map_work', ['run_id', 'tier'], unique=False)
    op.create_index('ix_map_work_run_district_area', 'map_work', ['run_id', 'district_area_id'], unique=False)
    op.create_index('ix_map_work_run_constituency_area', 'map_work', ['run_id', 'constituency_area_id'], unique=False)
    op.create_index('ix_map_work_search_trgm', 'map_work', ['search_text'], unique=False,
                    postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.create_table('geo_metric',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('level', sa.String(length=20), nullable=False),
    sa.Column('area_key', sa.String(length=200), nullable=False),
    sa.Column('area_name', sa.String(length=200), nullable=False),
    sa.Column('state', sa.String(length=200), nullable=True),
    sa.Column('house', sa.String(length=2), nullable=False),
    sa.Column('tier', sa.String(length=15), nullable=False),
    sa.Column('stage', sa.String(length=20), nullable=False),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.Column('amount_sum', sa.Float(), nullable=False),
    sa.Column('risk_sum', sa.Float(), nullable=False),
    sa.Column('risk_n', sa.Integer(), nullable=False),
    sa.Column('confidence_sum', sa.Float(), nullable=False),
    sa.Column('signals_sum', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'level', 'area_key', 'house', 'tier', 'stage', name='uq_geo_metric_cell')
    )
    op.create_index('ix_geo_metric_run_level', 'geo_metric', ['run_id', 'level'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_geo_metric_run_level', table_name='geo_metric')
    op.drop_table('geo_metric')
    for ix in ('ix_map_work_search_trgm', 'ix_map_work_run_constituency_area', 'ix_map_work_run_district_area',
               'ix_map_work_run_tier', 'ix_map_work_run_constituency', 'ix_map_work_run_state'):
        op.drop_index(ix, table_name='map_work')
    op.drop_table('map_work')
    op.drop_table('map_build')
    op.drop_index('ix_work_geo_constituency_area', table_name='work_geo')
    op.drop_table('work_geo')
    op.drop_table('authority_geo')
    op.drop_column('geo_name_crosswalk', 'detail')
    op.drop_column('geo_name_crosswalk', 'portal_state')
    op.alter_column('geo_name_crosswalk', 'method', type_=sa.String(length=20), existing_nullable=False)
    op.drop_column('geo_area', 'attrs')
    op.drop_column('geo_area', 'rep_lon')
    op.drop_column('geo_area', 'rep_lat')

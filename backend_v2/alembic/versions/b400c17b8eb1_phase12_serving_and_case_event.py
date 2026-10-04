"""phase12 serving and case_event

CREATE served_work (per-run read model for the cutover endpoints), serving_build
(stale-data rule, like map_build) and case_event (append-only investigation
audit trail; actor never taken from the request body). Autogenerate also
reported pre-existing, unrelated drift (four NOT NULL flags on server-default
timestamps, an ix_payment_work_key index) -- deliberately NOT included here:
this migration creates only Phase 12's tables. No risk_result change.

Revision ID: b400c17b8eb1
Revises: 056cfc96cbac
Create Date: 2026-09-27 09:48:21.660180

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b400c17b8eb1'
down_revision: Union[str, None] = '056cfc96cbac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('serving_build',
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('config_name', sa.String(length=20), nullable=False),
    sa.Column('model_version', sa.String(length=80), nullable=False),
    sa.Column('data_as_of', sa.Date(), nullable=True),
    sa.Column('counts', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('built_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('building', 'complete', 'failed')", name='ck_serving_build_status'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.PrimaryKeyConstraint('run_id')
    )
    op.create_table('case_event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('work_key', sa.String(length=50), nullable=False),
    sa.Column('event_type', sa.String(length=30), nullable=False),
    sa.Column('previous_status', sa.String(length=60), nullable=True),
    sa.Column('decision', sa.String(length=60), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('actor', sa.String(length=120), nullable=False),
    sa.Column('actor_is_placeholder', sa.Boolean(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("event_type IN ('investigation_decision', 'recalculate_requested')", name='ck_case_event_type'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.ForeignKeyConstraint(['work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_case_event_work', 'case_event', ['work_key', 'id'], unique=False)
    op.create_table('served_work',
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('work_key', sa.String(length=50), nullable=False),
    sa.Column('scored', sa.Boolean(), nullable=False),
    sa.Column('house', sa.String(length=2), nullable=False),
    sa.Column('mp', sa.String(length=200), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('description_normalized', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=300), nullable=True),
    sa.Column('activity_type_id', sa.Integer(), nullable=True),
    sa.Column('state', sa.String(length=200), nullable=True),
    sa.Column('constituency', sa.String(length=200), nullable=True),
    sa.Column('constituency_state', sa.String(length=200), nullable=True),
    sa.Column('district_authority_id', sa.Integer(), nullable=True),
    sa.Column('district_authority', sa.String(length=300), nullable=True),
    sa.Column('stage', sa.String(length=20), nullable=False),
    sa.Column('amount', sa.Float(), nullable=True),
    sa.Column('recommended_date', sa.Date(), nullable=True),
    sa.Column('sanction_date', sa.Date(), nullable=True),
    sa.Column('completion_date', sa.Date(), nullable=True),
    sa.Column('record_date', sa.Date(), nullable=True),
    sa.Column('tier', sa.String(length=15), nullable=True),
    sa.Column('risk', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('confidence_label', sa.String(length=10), nullable=True),
    sa.Column('active_signal_count', sa.Integer(), nullable=True),
    sa.Column('n_eligible', sa.Integer(), nullable=True),
    sa.Column('corroboration_factor', sa.Float(), nullable=True),
    sa.Column('pre_multiplier', sa.Float(), nullable=True),
    sa.Column('confidence_components', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('active_signals', sa.Text(), nullable=True),
    sa.Column('s_cost_anomaly', sa.Float(), nullable=True),
    sa.Column('s_near_duplicate', sa.Float(), nullable=True),
    sa.Column('s_portfolio_concentration', sa.Float(), nullable=True),
    sa.Column('s_district_authority_pattern', sa.Float(), nullable=True),
    sa.Column('s_temporal_anomaly', sa.Float(), nullable=True),
    sa.Column('s_lifecycle_delay', sa.Float(), nullable=True),
    sa.Column('peer_level', sa.String(length=3), nullable=True),
    sa.Column('peer_group_key', sa.String(length=200), nullable=True),
    sa.Column('peer_group_size', sa.Integer(), nullable=True),
    sa.Column('peer_median', sa.Float(), nullable=True),
    sa.Column('peer_percentile', sa.Float(), nullable=True),
    sa.Column('amount_used', sa.Float(), nullable=True),
    sa.Column('amount_basis', sa.String(length=10), nullable=True),
    sa.Column('deviation_ratio', sa.Float(), nullable=True),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.CheckConstraint('scored OR (tier IS NULL AND risk IS NULL AND confidence IS NULL)', name='ck_served_work_unscored_has_no_risk'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.ForeignKeyConstraint(['work_key'], ['work.work_key'], ),
    sa.PrimaryKeyConstraint('run_id', 'work_key')
    )
    op.create_index('ix_served_work_run_constituency', 'served_work', ['run_id', 'constituency'], unique=False)
    op.create_index('ix_served_work_run_desc_norm', 'served_work', ['run_id', 'mp', 'description_normalized'], unique=False)
    op.create_index('ix_served_work_run_house', 'served_work', ['run_id', 'house'], unique=False)
    op.create_index('ix_served_work_run_mp', 'served_work', ['run_id', 'mp'], unique=False)
    op.create_index('ix_served_work_run_scored_risk', 'served_work', ['run_id', 'scored', 'risk'], unique=False)
    op.create_index('ix_served_work_run_scored_tier', 'served_work', ['run_id', 'scored', 'tier'], unique=False)
    op.create_index('ix_served_work_run_state', 'served_work', ['run_id', 'state'], unique=False)
    op.create_index('ix_served_work_search_trgm', 'served_work', ['search_text'], unique=False, postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})


def downgrade() -> None:
    op.drop_index('ix_served_work_search_trgm', table_name='served_work', postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.drop_index('ix_served_work_run_state', table_name='served_work')
    op.drop_index('ix_served_work_run_scored_tier', table_name='served_work')
    op.drop_index('ix_served_work_run_scored_risk', table_name='served_work')
    op.drop_index('ix_served_work_run_mp', table_name='served_work')
    op.drop_index('ix_served_work_run_house', table_name='served_work')
    op.drop_index('ix_served_work_run_desc_norm', table_name='served_work')
    op.drop_index('ix_served_work_run_constituency', table_name='served_work')
    op.drop_table('served_work')
    op.drop_index('ix_case_event_work', table_name='case_event')
    op.drop_table('case_event')
    op.drop_table('serving_build')

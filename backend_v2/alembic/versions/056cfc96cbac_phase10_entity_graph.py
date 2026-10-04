"""phase10 entity/graph analytics

CREATE entity_metric (payee/agency/district-authority profiles and
concentration, BLUEPRINT.md §8) and graph_node/graph_edge (the offline
MP/work/payee/authority graph). ALTER work_evidence_fact's fact CHECK to
add identical_payment_repeated and multi_payee_work. No risk_result /
signal_result change -- read-only from this migration's perspective.

Revision ID: 056cfc96cbac
Revises: d4b8f0c2e6a1
Create Date: 2026-09-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '056cfc96cbac'
down_revision: Union[str, None] = 'd4b8f0c2e6a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

OLD_FACTS = "('pays_same_payee_more_than_once')"
NEW_FACTS = "('pays_same_payee_more_than_once', 'identical_payment_repeated', 'multi_payee_work')"


def upgrade() -> None:
    op.drop_constraint('ck_work_evidence_fact_fact', 'work_evidence_fact', type_='check')
    op.create_check_constraint('ck_work_evidence_fact_fact', 'work_evidence_fact', f"fact IN {NEW_FACTS}")

    op.create_table('entity_metric',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('entity_type', sa.String(length=30), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('entity_name', sa.String(length=300), nullable=False),
    sa.Column('metric', sa.String(length=40), nullable=False),
    sa.Column('substratum', sa.String(length=200), nullable=False, server_default=''),
    sa.Column('house', sa.String(length=2), nullable=True),
    sa.Column('eligible', sa.Boolean(), nullable=False),
    sa.Column('not_evaluated_reason', sa.String(length=80), nullable=True),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('percentile', sa.Float(), nullable=True),
    sa.Column('denominator', sa.Text(), nullable=False),
    sa.Column('interval', JSONB, nullable=False),
    sa.Column('peer_definition', sa.Text(), nullable=False),
    sa.Column('detail', JSONB, nullable=False),
    sa.CheckConstraint("entity_type IN ('payee', 'implementing_agency', 'district_authority', 'mp_tenure')",
                        name='ck_entity_metric_entity_type'),
    sa.CheckConstraint(
        "metric IN ('concentration', 'price_position', 'reach', 'district_authority_profile', "
        "'implementing_agency_profile')", name='ck_entity_metric_metric'),
    sa.CheckConstraint("(value IS NULL) = (NOT eligible)", name='ck_entity_metric_null_iff_not_evaluated'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'entity_type', 'entity_id', 'metric', 'substratum',
                        name='uq_entity_metric_run_entity_metric_substratum')
    )
    op.create_index('ix_entity_metric_run_entity', 'entity_metric', ['run_id', 'entity_type', 'entity_id'],
                    unique=False)
    op.create_index('ix_entity_metric_run_metric', 'entity_metric', ['run_id', 'metric'], unique=False)

    op.create_table('graph_node',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('node_id', sa.String(length=80), nullable=False),
    sa.Column('node_type', sa.String(length=20), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'node_id', name='uq_graph_node_run_node')
    )
    op.create_index('ix_graph_node_run_type', 'graph_node', ['run_id', 'node_type'], unique=False)

    op.create_table('graph_edge',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.String(length=80), nullable=False),
    sa.Column('target_id', sa.String(length=80), nullable=False),
    sa.Column('edge_type', sa.String(length=20), nullable=False),
    sa.Column('label', sa.String(length=60), nullable=False),
    sa.Column('weight', sa.Integer(), nullable=False),
    sa.Column('house', sa.String(length=2), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_run.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'source_id', 'target_id', 'edge_type', name='uq_graph_edge_run_pair')
    )
    op.create_index('ix_graph_edge_run_type', 'graph_edge', ['run_id', 'edge_type'], unique=False)
    op.create_index('ix_graph_edge_run_house', 'graph_edge', ['run_id', 'house'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_graph_edge_run_house', table_name='graph_edge')
    op.drop_index('ix_graph_edge_run_type', table_name='graph_edge')
    op.drop_table('graph_edge')
    op.drop_index('ix_graph_node_run_type', table_name='graph_node')
    op.drop_table('graph_node')
    op.drop_index('ix_entity_metric_run_metric', table_name='entity_metric')
    op.drop_index('ix_entity_metric_run_entity', table_name='entity_metric')
    op.drop_table('entity_metric')

    op.drop_constraint('ck_work_evidence_fact_fact', 'work_evidence_fact', type_='check')
    op.create_check_constraint('ck_work_evidence_fact_fact', 'work_evidence_fact', f"fact IN {OLD_FACTS}")

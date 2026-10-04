"""
Phase 10 orchestrator: payee retyping, work-evidence facts, entity_metric
and the graph, all for one run. Only READS risk_result/signal_result
(checksummed before and after -- a changed checksum aborts, exactly like
Phase 6/8's atypicality_run.py); never writes them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..analytics.atypicality_run import risk_result_checksum
from ..analytics.publish import published
from ..ingest.db_utils import bulk_insert, to_records_with_nulls
from ..models.analytics import EntityMetric, WorkEvidenceFact
from ..models.entities import ImplementingAgency
from ..models.graph import GraphEdge, GraphNode
from ..models.provenance import SourceSnapshot
from . import facts, graph_build, metrics, payee_typing

NEW_FACTS = ("identical_payment_repeated", "multi_payee_work")


def _clear(session: Session, run_id: int) -> None:
    session.execute(
        delete(WorkEvidenceFact).where(
            WorkEvidenceFact.run_id == run_id, WorkEvidenceFact.fact.in_(NEW_FACTS)
        )
    )
    session.execute(delete(EntityMetric).where(EntityMetric.run_id == run_id))
    session.execute(delete(GraphEdge).where(GraphEdge.run_id == run_id))
    session.execute(delete(GraphNode).where(GraphNode.run_id == run_id))
    session.flush()


def build_entities(session: Session, run_id: int | None = None) -> dict:
    pub = published(session)
    if pub is None:
        raise ValueError("no published_run; run scripts/publish_run.py first")
    run_id = pub.run_id if run_id is None else run_id
    config_name = pub.default_config_name

    run_row = session.execute(
        text("SELECT source_snapshot_id FROM analysis_run WHERE id = :r"), {"r": run_id}
    ).scalar_one()
    snapshot_id = run_row
    as_of = pd.Timestamp(session.get(SourceSnapshot, snapshot_id).data_as_of)

    before = risk_result_checksum(session, run_id)
    _clear(session, run_id)

    payee_report = payee_typing.reclassify_payees(session)

    pay_ctx = facts.load_payments(session, run_id, snapshot_id)
    id_facts, id_stats = facts.identical_payment_facts(pay_ctx, run_id)
    multi_facts, multi_stats = facts.multi_payee_facts(pay_ctx, run_id)
    bulk_insert(session, WorkEvidenceFact, to_records_with_nulls(id_facts))
    bulk_insert(session, WorkEvidenceFact, to_records_with_nulls(multi_facts))

    payments = metrics.load_payments_with_context(session, run_id, snapshot_id)
    rng = np.random.default_rng(metrics.SEED)
    conc_rows = metrics.concentration_metrics(payments, run_id, rng)
    price_rows = metrics.price_position_metrics(payments, run_id, rng)
    reach_rows = metrics.reach_metrics(payments, run_id)

    auth_works = metrics.load_authority_works(session, run_id, snapshot_id)
    district_rows = metrics.district_authority_profile_metrics(auth_works, payments, as_of, run_id)

    agency_names = {a.id: a.ia_name for a in session.execute(select(ImplementingAgency)).scalars()}
    agency_rows = metrics.implementing_agency_profile_metrics(payments, auth_works, agency_names, run_id)

    all_rows = conc_rows + price_rows + reach_rows + district_rows + agency_rows
    for r in all_rows:
        r.setdefault("eligible", True)
        r.setdefault("not_evaluated_reason", None)
    bulk_insert(session, EntityMetric, to_records_with_nulls(pd.DataFrame(all_rows)))

    graph_df = graph_build.load_graph_frame(session, run_id, config_name)
    nodes, edges = graph_build.build_graph(graph_df, run_id)
    bulk_insert(session, GraphNode, nodes)
    bulk_insert(session, GraphEdge, edges)

    after = risk_result_checksum(session, run_id)
    if before != after:
        raise AssertionError(f"risk_result checksum changed during entity build: {before} -> {after}")
    session.flush()

    return {
        "run_id": run_id,
        "config": config_name,
        "risk_result_md5_before": before,
        "risk_result_md5_after": after,
        "payee_retyping": payee_report,
        "work_evidence_facts": {"identical_payment_repeated": id_stats, "multi_payee_work": multi_stats},
        "entity_metric_rows": len(all_rows),
        "entity_metric_by_metric": pd.Series([r["metric"] for r in all_rows]).value_counts().to_dict()
        if all_rows
        else {},
        "graph": {"nodes": len(nodes), "edges": len(edges)},
    }

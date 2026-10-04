"""
Phase 10 graph layer (BLUEPRINT.md §8 "Graph layer"): a bounded, exploratory
network of MPs, works, payees and district authorities, computed OFFLINE
once per run into these two tables. NOT a graph database and NOT a scoring
component -- GET /api/graph-data (app/api/entities.py) only reads these
tables, never joins work/payment/risk_result live, and no community
detection here feeds risk_result.

Response shape (docs/frontend_contract.md #12) is preserved exactly:
{"nodes": [...], "edges": [...]}, each node {id, type, label, count} and
each edge {source, target, type, label} -- the old engine's shape
(backend/app/core/engine.py get_graph_data), just with real MP/payee/
authority/work content now and bounded per app/entities/graph_build.py's
documented caps.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

NODE_TYPES = ("MP", "Work", "Payee", "DistrictAuthority")
EDGE_TYPES = ("recommends", "pays", "executes")


class GraphNode(Base):
    __tablename__ = "graph_node"
    __table_args__ = (
        UniqueConstraint("run_id", "node_id", name="uq_graph_node_run_node"),
        Index("ix_graph_node_run_type", "run_id", "node_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. "mp_123", "payee_456"
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)  # works touching this node


class GraphEdge(Base):
    """`house` is the single House of the work backing this edge (every edge
    here traces to exactly one work), so GET /api/graph-data can honour its
    `house` filter (BLUEPRINT.md §9.1) without recomputing the graph."""

    __tablename__ = "graph_edge"
    __table_args__ = (
        UniqueConstraint("run_id", "source_id", "target_id", "edge_type", name="uq_graph_edge_run_pair"),
        Index("ix_graph_edge_run_type", "run_id", "edge_type"),
        Index("ix_graph_edge_run_house", "run_id", "house"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(80), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)  # works/payments backing this edge
    house: Mapped[str] = mapped_column(String(2), nullable=False)

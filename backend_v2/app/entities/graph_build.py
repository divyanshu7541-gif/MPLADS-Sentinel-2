"""
Phase 10 graph layer (BLUEPRINT.md §8 "Graph layer"): MPs, works, payees and
district authorities, computed OFFLINE once per run into graph_node /
graph_edge (app/models/graph.py). Bounded and exploratory, not a graph
database, and no community detection here ever feeds risk_result.

MP identity: `work.tenure_id` is 0% populated in this dataset (a
pre-existing gap -- see docs/phase10_11_report.md), so this module uses
the same MP identity Phase 3/4 already use for peer grouping and signals:
the normalised `raw_mp_name` text (app.ingest.normalize.normalize_name),
never tenure_id. A node id is a stable hash of that normalised name so it
stays short and filesystem/URL-safe; the display label is the name itself.

Bounding (judgement calls, documented, not tuned to a particular run's
output): the graph shows the busiest TOP_ENTITIES of each entity type by a
real, already-computed measure (MP: scored works recommended; authority:
scored works executed; payee: total payment amount), then bridges them
with up to TOP_WORKS_PER_MP of that MP's own highest-risk works that
actually connect to one of those top authorities or payees -- exactly the
"a payee spanning many districts" exploration BLUEPRINT.md §8 names, kept
small enough to render. Every count/weight on a node or edge is the ENTITY'S
real total (not capped to what's drawn), so a node's number is never
misleading about how it was chosen.
"""

from __future__ import annotations

import hashlib

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ingest.normalize import normalize_name
from ..serving.redact import mask_personal

TOP_ENTITIES = 25
TOP_WORKS_PER_MP = 3

_GRAPH_SQL = text(
    """
    SELECT r.work_key, r.house, r.risk, r.tier, w.raw_mp_name, w.district_authority_id,
           w.raw_description, p.payee_id, p.amount, pay.canonical_name AS payee_name,
           da.ida_name
    FROM risk_result r
    JOIN work w ON w.work_key = r.work_key
    JOIN analysis_run ar ON ar.id = r.run_id
    LEFT JOIN district_authority da ON da.id = w.district_authority_id
    LEFT JOIN payment p ON p.work_key = r.work_key AND p.source_snapshot_id = ar.source_snapshot_id
    LEFT JOIN payee pay ON pay.id = p.payee_id
    WHERE r.run_id = :run AND r.config_name = :cfg
    """
)


def load_graph_frame(session: Session, run_id: int, config_name: str) -> pd.DataFrame:
    df = pd.read_sql(_GRAPH_SQL, session.connection(), params={"run": run_id, "cfg": config_name})
    df["mp"] = df["raw_mp_name"].map(normalize_name).replace("", pd.NA)
    return df


def _work_label(raw_description, work_key: str) -> str:
    """A Work node's label: phone and Aadhaar-shaped numbers masked BEFORE the
    120-character cut, so a number is never half-shown (the same output rule
    as served_work; the stored description itself is never changed)."""
    return (mask_personal(raw_description) or work_key)[:120]


def _mp_node_id(mp: str) -> str:
    return "mp_" + hashlib.sha1(mp.encode()).hexdigest()[:16]


def build_graph(df: pd.DataFrame, run_id: int) -> tuple[list[dict], list[dict]]:
    works = df.drop_duplicates("work_key")

    mp_totals = works.dropna(subset=["mp"]).groupby("mp").size()
    auth_totals = works.dropna(subset=["district_authority_id"]).groupby("district_authority_id").size()
    payee_totals = df.dropna(subset=["payee_id"]).groupby("payee_id")["amount"].sum()

    top_mps = mp_totals.nlargest(TOP_ENTITIES).index
    top_auths = auth_totals.nlargest(TOP_ENTITIES).index
    top_payees = payee_totals.nlargest(TOP_ENTITIES).index

    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}

    def add_node(node_id, node_type, label, count):
        if node_id not in nodes:
            nodes[node_id] = {
                "run_id": run_id,
                "node_id": node_id,
                "node_type": node_type,
                "label": label[:300],
                "count": int(count),
            }

    def add_edge(source, target, edge_type, label, weight, house):
        key = (source, target, edge_type)
        if key in edges:
            edges[key]["weight"] += weight
        else:
            edges[key] = {
                "run_id": run_id,
                "source_id": source,
                "target_id": target,
                "edge_type": edge_type,
                "label": label,
                "weight": int(weight),
                "house": house,
            }

    for mp in top_mps:
        add_node(_mp_node_id(mp), "MP", mp, mp_totals[mp])

    auth_name = (
        works.dropna(subset=["district_authority_id"])
        .drop_duplicates("district_authority_id")
        .set_index("district_authority_id")["ida_name"]
    )
    for aid in top_auths:
        add_node(
            f"authority_{int(aid)}",
            "DistrictAuthority",
            str(auth_name.get(aid) or f"Authority {int(aid)}"),
            auth_totals[aid],
        )

    payee_name = (
        df.dropna(subset=["payee_id"]).drop_duplicates("payee_id").set_index("payee_id")["payee_name"]
    )
    for pid in top_payees:
        add_node(f"payee_{int(pid)}", "Payee", str(payee_name.get(pid) or f"Payee {int(pid)}"), 1)
        nodes[f"payee_{int(pid)}"]["count"] = int(df[df["payee_id"] == pid]["work_key"].nunique())

    # Bridge works: for each top MP, its own highest-risk works that touch a top authority or a top payee.
    for mp in top_mps:
        mnode = _mp_node_id(mp)
        mine = works[works["mp"] == mp].sort_values("risk", ascending=False, na_position="last")
        bridging = mine[
            mine["district_authority_id"].isin(top_auths)
            | mine["work_key"].isin(df[df["payee_id"].isin(top_payees)]["work_key"])
        ]
        for row in bridging.head(TOP_WORKS_PER_MP).itertuples(index=False):
            wnode = f"work_{row.work_key}"
            add_node(wnode, "Work", _work_label(row.raw_description, row.work_key), 1)
            add_edge(mnode, wnode, "recommends", "Recommends", 1, row.house)
            if pd.notna(row.district_authority_id) and int(row.district_authority_id) in top_auths:
                add_edge(
                    f"authority_{int(row.district_authority_id)}", wnode, "executes", "Executes", 1, row.house
                )
            work_payments = df[(df["work_key"] == row.work_key) & df["payee_id"].isin(top_payees)]
            for pid, g in work_payments.groupby("payee_id"):
                add_edge(wnode, f"payee_{int(pid)}", "pays", "Pays", len(g), row.house)

    return list(nodes.values()), list(edges.values())

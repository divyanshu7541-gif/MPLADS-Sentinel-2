"""
docs/frontend_contract.md #12 (graph-data) + one additive, non-contract
endpoint for entity profiles (BLUEPRINT.md §8).

GET /api/graph-data reads graph_node/graph_edge (app/models/graph.py),
built offline per run by scripts/run_entities.py -- never a live join over
work/payment/risk_result. Node/edge shape matches the old engine's
contract (backend/app/core/engine.py get_graph_data) exactly: nodes
{id, type, label, count}, edges {source, target, type, label}; `weight` is
an additive edge field.

GET /api/entities/{entity_type}/{entity_id} is new, not in the frontend
contract (no page calls it -- same posture as Phase 9's
GET /api/geo/areas/{key}/works). Its response model (EntityMetricOut) makes
denominator/interval/peer_definition non-optional, so BLUEPRINT.md §8's
wording rule ("show the denominator, interval and peer definition beside
every entity metric") is enforced by FastAPI's response validation, not
just by convention in the query that built the row.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..analytics.publish import published
from ..auth.deps import Principal, reader
from ..db.session import get_db
from ..models.analytics import ENTITY_TYPES, EntityMetric
from ..models.entities import ImplementingAgency, Payee
from ..models.reference import DistrictAuthority
from ..serving.public_mask import mask_public
from ..serving.redact import mask_personal
from .params import House

router = APIRouter()

# Phase 13 personal-data control (BLUEPRINT.md §11 "review handling of
# sole-proprietor payees before any public release"): in the anonymous
# public view, a payee typed `individual` -- or not yet typed at all
# (`unclassified`, which can be a sole proprietor) -- is shown without its
# name. Authenticated accounts see names. Firms, government bodies and
# manufacturers are shown as-is.
PUBLIC_WITHHELD_PAYEE_TYPES = ("individual", "unclassified")
WITHHELD_LABEL = "Payee (name withheld in public view)"


@router.get("/api/graph-data")
def get_graph_data(house: House = None, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    # The graph is a national top-25 aggregate built offline: it can't be cut
    # to a state, district or MP, so a caller scoped that narrowly gets 403,
    # never a national view. A House scope narrows the edges like `house`.
    if p.scope.geographic:
        raise HTTPException(status_code=403, detail="not available to a state/district/MP-scoped account")
    if p.scope.house and house and house != p.scope.house:
        return {"nodes": [], "edges": []}
    house = house or p.scope.house
    try:
        pub = published(db)
        if pub is None:
            return {"nodes": [], "edges": []}
        node_rows = db.execute(
            text(
                "SELECT gn.node_id, gn.node_type, gn.label, gn.count, py.payee_type FROM graph_node gn "
                "LEFT JOIN payee py ON gn.node_type = 'Payee' AND gn.node_id = 'payee_' || py.id::text "
                "WHERE gn.run_id = :r"
            ),
            {"r": pub.run_id},
        ).all()
        edge_sql = "SELECT source_id, target_id, edge_type, label, weight FROM graph_edge WHERE run_id = :r"
        params = {"r": pub.run_id}
        if house:
            edge_sql += " AND house = :h"
            params["h"] = house
        edge_rows = db.execute(text(edge_sql), params).all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail="graph data temporarily unavailable") from e

    def label_of(ntype, label, payee_type):
        withhold = (
            ntype == "Payee"
            and not p.authenticated
            and (payee_type or "unclassified") in (PUBLIC_WITHHELD_PAYEE_TYPES)
        )
        if withhold:
            return WITHHELD_LABEL
        if ntype == "Work":
            # Labels stored before graph_build masked numbers are masked here for
            # everyone; beneficiary details are masked for an anonymous caller.
            label = mask_personal(label)
            return label if p.authenticated else mask_public(label)
        return label

    nodes = [
        {"id": nid, "type": ntype, "label": label_of(ntype, label, ptype), "count": count}
        for nid, ntype, label, count, ptype in node_rows
    ]
    edges = [
        {"source": s, "target": t, "type": etype, "label": label, "weight": w}
        for s, t, etype, label, w in edge_rows
    ]
    return {"nodes": nodes, "edges": edges}


class EntityMetricOut(BaseModel):
    metric: str
    substratum: str
    house: Optional[str]
    eligible: bool
    not_evaluated_reason: Optional[str]
    n: int
    value: Optional[float]
    percentile: Optional[float]
    # Never optional/None (BLUEPRINT.md §8 "Wording rules"): every row must
    # say its denominator, interval and peer definition, evaluated or not.
    denominator: str
    interval: dict
    peer_definition: str
    detail: dict


class EntityProfileOut(BaseModel):
    entity_type: str
    entity_id: int
    entity_name: str
    # Per-type descriptive fields (e.g. payee_type/review_status for a payee),
    # so a caller can see the Phase 10 payee retyping fix live, not only in
    # the database. {} when the entity type has no such fields (mp_tenure).
    attributes: dict
    run_id: int
    metrics: list[EntityMetricOut]


def _resolve_payee(db: Session, entity_id: int) -> Optional[tuple[str, dict]]:
    row = db.get(Payee, entity_id)
    if row is None:
        return None
    return row.canonical_name, {"payee_type": row.payee_type, "review_status": row.review_status}


def _resolve_agency(db: Session, entity_id: int) -> Optional[tuple[str, dict]]:
    row = db.get(ImplementingAgency, entity_id)
    if row is None:
        return None
    return row.ia_name, {"agency_type": row.agency_type}


def _resolve_authority(db: Session, entity_id: int) -> Optional[tuple[str, dict]]:
    row = db.get(DistrictAuthority, entity_id)
    if row is None:
        return None
    resolved_state = db.execute(
        text(
            "SELECT st.name FROM authority_geo ag JOIN state st ON st.id = ag.resolved_state_id "
            "WHERE ag.district_authority_id = :id"
        ),
        {"id": entity_id},
    ).scalar()
    return row.ida_name, {"district_key": row.district_key, "resolved_state": resolved_state}


def _resolve_mp_tenure(db: Session, run_id: Optional[int], entity_id: int) -> Optional[tuple[str, dict]]:
    """mp_tenure has no backing identity table (BLUEPRINT.md §8's "MP tenure"
    grain has no populated work.tenure_id in this dataset -- see
    docs/phase10_11_report.md): its entity_id is a stable hash of
    (normalised raw_mp_name, house), computed in app/entities/metrics.py,
    and its only record of existing is the entity_metric row(s) that name
    stores. With no published run (or before that run's entity build), an
    mp_tenure id cannot be resolved at all -- 404, not a guess."""
    if run_id is None:
        return None
    row = db.execute(
        text(
            "SELECT entity_name FROM entity_metric WHERE run_id = :r AND entity_type = 'mp_tenure' "
            "AND entity_id = :id LIMIT 1"
        ),
        {"r": run_id, "id": entity_id},
    ).first()
    if row is None:
        return None
    return row[0], {}


_RESOLVERS = {
    "payee": lambda db, run_id, eid: _resolve_payee(db, eid),
    "implementing_agency": lambda db, run_id, eid: _resolve_agency(db, eid),
    "district_authority": lambda db, run_id, eid: _resolve_authority(db, eid),
    "mp_tenure": lambda db, run_id, eid: _resolve_mp_tenure(db, run_id, eid),
}


def _in_scope(db: Session, p: Principal, entity_type: str, entity_id: int, name: str) -> bool:
    """National accounts see every profile. Payee and agency metrics are
    national, so no narrower scope may read them. A district authority or MP
    tenure is visible only if the caller's scope holds at least one of its
    works (checked in SQL against served_work)."""
    if p.scope.national:
        return True
    if entity_type in ("payee", "implementing_agency"):
        return False
    from ..serving import service as serving

    s = serving.served(db, p.scope)
    if s.run_id is None:
        return False
    sc, sp = s.scope.direct()
    if entity_type == "district_authority":
        return db.execute(
            text(
                f"SELECT EXISTS (SELECT 1 FROM served_work WHERE run_id = :run "
                f"AND district_authority_id = :v{sc})"
            ),
            {"run": s.run_id, "v": entity_id, **sp},
        ).scalar_one()
    # mp_tenure ids are Phase 10's hash of (normalised MP name, house): recompute
    # them for the (MP, house) pairs inside the caller's scope.
    from ..entities.metrics import mp_tenure_id
    from ..ingest.normalize import normalize_name

    pairs = db.execute(
        text(f"SELECT DISTINCT mp, house FROM served_work WHERE run_id = :run AND mp IS NOT NULL{sc}"),
        {"run": s.run_id, **sp},
    ).all()
    return any(mp_tenure_id(normalize_name(mp), house) == entity_id for mp, house in pairs)


@router.get("/api/entities/{entity_type}/{entity_id}", response_model=EntityProfileOut)
def get_entity_profile(
    entity_type: str, entity_id: int, db: Session = Depends(get_db), p: Principal = Depends(reader)
):
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(status_code=422, detail=f"entity_type must be one of {ENTITY_TYPES}")
    # Entity profiles carry payee names and national entity metrics: never public.
    if not p.authenticated:
        raise HTTPException(
            status_code=401, detail="authentication required", headers={"WWW-Authenticate": "Bearer"}
        )

    def run():
        pub = published(db)
        resolved = _RESOLVERS[entity_type](db, pub.run_id if pub else None, entity_id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="unknown entity")
        name, attributes = resolved
        if not _in_scope(db, p, entity_type, entity_id, name):
            raise HTTPException(status_code=404, detail="unknown entity")  # out of scope reads as absent
        if pub is None:
            return EntityProfileOut(
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=name,
                attributes=attributes,
                run_id=0,
                metrics=[],
            )
        rows = (
            db.execute(
                select(EntityMetric).where(
                    EntityMetric.run_id == pub.run_id,
                    EntityMetric.entity_type == entity_type,
                    EntityMetric.entity_id == entity_id,
                )
            )
            .scalars()
            .all()
        )
        return EntityProfileOut(
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=name,
            attributes=attributes,
            run_id=pub.run_id,
            metrics=[
                EntityMetricOut(**{k: getattr(r, k) for k in EntityMetricOut.model_fields}) for r in rows
            ],
        )

    try:
        return run()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail="entity data temporarily unavailable") from e

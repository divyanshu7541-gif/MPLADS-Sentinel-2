"""
Phase 12 read side: the queries behind every cutover endpoint (dashboard,
queue, record, analytics, data health, filter lists, MP/constituency
performance, risk/signals/evidence/context, investigations, audit trail).

Reads served_work (app/serving/build.py) for the served run plus, for one
record at a time, its own signal_result / compliance_result / fact rows.
Never writes risk_result. The only writes are append-only case_event rows
(investigation decisions and "recalculate" requests).

Rules applied everywhere (docs/phase12_report.md):
  * Risk figures are computed over the SCORED population only; unscored
    (recommended-only) works appear only in MP/constituency profiles'
    descriptive stage/amount figures, never as a zero risk.
  * `house` filters which works are reported; it never changes a score.
  * Free-text filters are literal substring matches (Phase 9's escaped LIKE
    helpers), never regex -- the old queue's `.str.contains` crashed on "(".
  * The investigation actor is never read from the request body.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..analytics import fusion
from ..analytics.publish import published
from ..audit import case_log
from ..auth.scope import NATIONAL, Scope
from ..geo.service import MIN_WORKS_FOR_RATE, like_pattern
from ..models.serving import ServingBuild
from . import labels
from .public_mask import public_search_clause
from .redact import mask_personal

# served_work.search_text minus the description (app/serving/build.py builds
# search_text from these plus the description).
SEARCH_COLS_EXCEPT_DESCRIPTION = ("work_key", "mp", "constituency", "state", "category", "district_authority")

TIERS = ("CRITICAL", "HIGH", "MODERATE", "LOW", "NOT_EVALUATED")
FLAGGED = ("CRITICAL", "HIGH")
SORTS = {
    "risk_score": "risk",
    "priority_score": "risk",
    "cost_anomaly_score": "s_cost_anomaly",
    "evidence_count": "active_signal_count",
    "amount_numeric": "amount",
}
MAX_PAGE_SIZE = 200
MAX_TOP = 500
AUDIT_LIMIT = 1000


# ---- which run is served -------------------------------------------------------------


class Served:
    """The served build plus the CALLER'S data scope (Phase 13): every query
    below goes through _scope()/_sc() so it only ever sees in-scope works."""

    def __init__(self, build: ServingBuild | None, is_latest: bool, scope: Scope = NATIONAL):
        self.build = build
        self.is_latest = is_latest
        self.scope = scope

    @property
    def run_id(self) -> int | None:
        return self.build.run_id if self.build else None

    @property
    def model_version(self) -> str:
        return self.build.model_version if self.build else "not-built"

    def meta(self) -> dict:
        b = self.build
        return {
            "run_id": b.run_id if b else None,
            "config": b.config_name if b else None,
            "data_as_of": str(b.data_as_of) if b and b.data_as_of else None,
            "is_latest": self.is_latest,
        }

    def cache_key(self, *parts) -> tuple:
        return (self.run_id, self.build.built_at if self.build else None, self.scope.key(), *parts)


def served(session: Session, scope: Scope = NATIONAL) -> Served:
    """The published run's serving build when complete; otherwise the most
    recent complete build, with is_latest False (Phase 9's stale-data rule)."""
    pub = published(session)
    if pub is not None:
        b = session.get(ServingBuild, pub.run_id)
        if b is not None and b.status == "complete":
            return Served(b, True, scope)
    b = session.execute(
        select(ServingBuild)
        .where(ServingBuild.status == "complete")
        .order_by(ServingBuild.run_id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return Served(b, False, scope)


_CACHE: dict[tuple, object] = {}


def _cached(key: tuple, fn):
    if key not in _CACHE:
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = fn()
    return _CACHE[key]


def _scope(s: Served, house: str | None, scored: bool = True, alias: str = "") -> tuple[str, dict]:
    a = f"{alias}." if alias else ""
    where = f"{a}run_id = :run" + (f" AND {a}scored" if scored else "")
    p: dict = {"run": s.run_id}
    if house:
        where += f" AND {a}house = :house"
        p["house"] = house
    sc, sp = s.scope.direct(alias)
    return where + sc, {**p, **sp}


def _sc(s: Served, alias: str = "") -> tuple[str, dict]:
    """(' AND <scope conditions>', params) for hand-written served_work queries."""
    return s.scope.direct(alias)


def _f(v, nd: int | None = None):
    if v is None:
        return None
    v = float(v)
    return round(v, nd) if nd is not None else v


def _date(v) -> str:
    return v.isoformat() if isinstance(v, dt.date) else ("" if v is None else str(v))


# ---- dashboard (#1) --------------------------------------------------------------------


def summary(session: Session, s: Served, house: str | None) -> dict:
    def build():
        if s.run_id is None:
            return _empty_summary(s)
        where, p = _scope(s, house)
        row = (
            session.execute(
                text(
                    f"""
                SELECT count(*) n, coalesce(sum(amount), 0) amount,
                       count(*) FILTER (WHERE tier = 'CRITICAL') critical,
                       count(*) FILTER (WHERE tier = 'HIGH') high,
                       count(*) FILTER (WHERE tier = 'MODERATE') moderate,
                       count(*) FILTER (WHERE tier = 'LOW') low,
                       count(*) FILTER (WHERE tier = 'NOT_EVALUATED') not_evaluated,
                       avg(risk) avg_risk, avg(confidence) avg_conf
                FROM served_work WHERE {where}
                """
                ),
                p,
            )
            .mappings()
            .one()
        )
        n = row["n"]
        dist = {t: row[t.lower()] for t in TIERS if row[t.lower()]}
        sw_where, _ = _scope(s, house, alias="sw")
        top_cost = (
            session.execute(
                text(
                    f"""
                SELECT sw.work_key, sw.mp, sw.constituency, sw.description, sw.amount, sw.s_cost_anomaly,
                       sr.evidence
                FROM served_work sw
                LEFT JOIN signal_result sr ON sr.run_id = sw.run_id AND sr.work_key = sw.work_key
                     AND sr.signal = 'cost_anomaly'
                WHERE {sw_where}
                  AND sw.s_cost_anomaly IS NOT NULL
                ORDER BY sw.s_cost_anomaly DESC, sw.work_key LIMIT 5
                """
                ),
                p,
            )
            .mappings()
            .all()
        )
        return {
            "total_records": n,
            "total_amount": _f(row["amount"]),
            "risk_distribution": dist,
            "critical_count": row["critical"],
            "high_count": row["high"],
            "moderate_count": row["moderate"],
            "low_count": row["low"],
            "high_priority": row["critical"] + row["high"],
            "review_recommended": row["moderate"],
            "normal": row["low"],
            "flag_rate": row["critical"] + row["high"],
            "average_risk": _f(row["avg_risk"], 1) or 0,
            "high_risk_percentage": round((row["critical"] + row["high"]) / n * 100, 1) if n else 0,
            "average_confidence": round(float(row["avg_conf"]) * 100, 1)
            if row["avg_conf"] is not None
            else 0,
            "category_distribution": _counts(session, s, house, "category"),
            "state_distribution": _counts(session, s, house, "state", limit=20),
            "stage_distribution": _counts(session, s, house, "stage"),
            "priority_distribution": dist,
            "top_signals": {
                "cost_anomalies": [
                    {
                        "Record ID": r["work_key"],
                        "MP": r["mp"] or "",
                        "Constituency": r["constituency"] or "",
                        "Work Description": (mask_personal(r["description"]) or "")[:80],
                        "Amount": _f(r["amount"]),
                        "cost_anomaly_score": _f(r["s_cost_anomaly"]),
                        "cost_anomaly_explanation": labels.explanation(
                            "cost_anomaly", r["evidence"], r["s_cost_anomaly"]
                        ),
                    }
                    for r in top_cost
                ]
            },
            "model_version": s.model_version,
            # additive (Phase 12)
            "not_evaluated_count": row["not_evaluated"],
            "population": "scored works (sanctioned or completed) in the served run; recommended-only works "
            "are not scored",
            "served": s.meta(),
        }

    return _cached(s.cache_key("summary", house), build)


def _empty_summary(s: Served) -> dict:
    return {
        "total_records": 0,
        "total_amount": 0,
        "risk_distribution": {},
        "critical_count": 0,
        "high_count": 0,
        "moderate_count": 0,
        "low_count": 0,
        "high_priority": 0,
        "review_recommended": 0,
        "normal": 0,
        "flag_rate": 0,
        "average_risk": 0,
        "high_risk_percentage": 0,
        "average_confidence": 0,
        "category_distribution": {},
        "state_distribution": {},
        "stage_distribution": {},
        "priority_distribution": {},
        "top_signals": {"cost_anomalies": []},
        "model_version": s.model_version,
        "not_evaluated_count": 0,
        "population": "",
        "served": s.meta(),
    }


def _counts(
    session: Session, s: Served, house, col: str, limit: int | None = None, scored: bool = True
) -> dict:
    where, p = _scope(s, house, scored)
    lim = f" LIMIT {int(limit)}" if limit else ""
    rows = session.execute(
        text(
            f"SELECT {col}, count(*) FROM served_work WHERE {where} AND {col} IS NOT NULL "
            f"GROUP BY 1 ORDER BY 2 DESC, 1{lim}"
        ),
        p,
    ).all()
    return {k: n for k, n in rows}


# ---- queue (#2) and record rows ----------------------------------------------------------------

_ROW_COLS = (
    "work_key, house, mp, description, category, amount, constituency, state, record_date, stage, tier, "
    "risk, confidence, confidence_label, active_signal_count, active_signals, "
    "s_cost_anomaly, s_near_duplicate, s_portfolio_concentration, s_district_authority_pattern, "
    "s_temporal_anomaly, s_lifecycle_delay"
)


def _evidence_summary(r) -> str:
    parts = []
    for sig in fusion.BASE_SIGNALS:
        v = r[labels.SIGNAL_COLUMN[sig]]
        if labels.is_active(v):
            parts.append(f"{labels.SIGNAL_LABEL[sig]} ({v:.2f})")
    return "; ".join(parts)


def queue_record(r, model_version: str) -> dict:
    risk01 = None if r["risk"] is None else round(r["risk"] / 100, 6)
    conf = r["confidence"]
    return {
        "record_id": r["work_key"],
        "mp_name": r["mp"] or "",
        "description": mask_personal(r["description"]) or "",
        "category": r["category"] or "",
        "amount": labels.rupees(r["amount"]),
        "amount_numeric": _f(r["amount"]),
        "constituency": r["constituency"] or "",
        "state": r["state"] or "",
        "date": _date(r["record_date"]),
        "stage": r["stage"],
        "priority": r["tier"],
        "risk_level": r["tier"],
        "confidence": r["confidence_label"],
        "confidence_score": _f(conf, 4),
        "confidence_percent": None if conf is None else round(conf * 100, 1),
        "risk_score": risk01,
        "priority_score": risk01,
        "evidence_count": r["active_signal_count"],
        "active_signal_count": r["active_signal_count"],
        "base_signal_count": r["active_signal_count"],
        "evidence_summary": _evidence_summary(r),
        "active_signals": r["active_signals"] or "",
        "model_version": model_version,
        # additive (Phase 12)
        "house": r["house"],
    }


def queue(session: Session, s: Served, f: dict) -> dict:
    page, page_size = f["page"], f["page_size"]
    if s.run_id is None:
        return {"records": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}
    where, p = _scope(s, f.get("house"))
    tier = f.get("priority") or f.get("risk_level")
    if tier:
        where += " AND tier = :tier"
        p["tier"] = tier
    for key, col in (
        ("confidence", "confidence_label"),
        ("category", "category"),
        ("state", "state"),
        ("stage", "stage"),
    ):
        if f.get(key):
            where += f" AND {col} = :{key}"
            p[key] = f[key]
    for key, col in (("constituency", "constituency"), ("mp_name", "mp")):
        if f.get(key):
            where += f" AND {col} ILIKE :{key}_pat ESCAPE '\\'"
            p[f"{key}_pat"] = like_pattern(f[key])
    if f.get("search"):
        where += " AND search_text LIKE :search_pat ESCAPE '\\'"
        p["search_pat"] = like_pattern(f["search"])
        if f.get("public"):  # anonymous: never match words a masked description hides
            extra, ep = public_search_clause("", SEARCH_COLS_EXCEPT_DESCRIPTION, "search_pat")
            where += extra
            p.update(ep)
    col = SORTS.get(f.get("sort_by") or "risk_score", "risk")
    direction = "ASC" if f.get("sort_order") == "asc" else "DESC"
    total = session.execute(text(f"SELECT count(*) FROM served_work WHERE {where}"), p).scalar_one()
    rows = (
        session.execute(
            text(
                f"SELECT {_ROW_COLS} FROM served_work WHERE {where} "
                f"ORDER BY {col} {direction} NULLS LAST, work_key LIMIT :lim OFFSET :off"
            ),
            {**p, "lim": page_size, "off": (page - 1) * page_size},
        )
        .mappings()
        .all()
    )
    return {
        "records": [queue_record(r, s.model_version) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


def top_records(session: Session, s: Served, limit: int) -> list[dict]:
    if s.run_id is None:
        return []
    sc, sp = _sc(s)
    rows = (
        session.execute(
            text(
                f"SELECT {_ROW_COLS} FROM served_work WHERE run_id = :run AND scored{sc} "
                "ORDER BY risk DESC NULLS LAST, work_key LIMIT :lim"
            ),
            {"run": s.run_id, "lim": limit, **sp},
        )
        .mappings()
        .all()
    )
    return [queue_record(r, s.model_version) for r in rows]


# ---- one record: risk, context, evidence, chain, related, history (#3, #25, #28-#30, #32) ---------------


def _row(session: Session, s: Served, work_key: str):
    if s.run_id is None:
        return None
    sc, sp = _sc(s)
    return (
        session.execute(
            text(f"SELECT * FROM served_work WHERE run_id = :run AND work_key = :wk{sc}"),
            {"run": s.run_id, "wk": work_key, **sp},
        )
        .mappings()
        .first()
    )


def _signal_rows(session: Session, s: Served, work_key: str) -> dict:
    rows = (
        session.execute(
            text(
                "SELECT signal, eligible, score, tail_percentile, direction, evidence FROM signal_result "
                "WHERE run_id = :run AND work_key = :wk"
            ),
            {"run": s.run_id, "wk": work_key},
        )
        .mappings()
        .all()
    )
    return {r["signal"]: r for r in rows}


def evidence_items(row, sig_rows: dict) -> list[dict]:
    items = []
    for sig in fusion.BASE_SIGNALS:
        r = sig_rows.get(sig)
        score = None if r is None or not r["eligible"] else r["score"]
        if not labels.is_active(score):
            continue
        ev = r["evidence"] or {}
        peer_n = (
            ev.get("n_usable_excl_self")
            or ev.get("n_peer_mps")
            or ev.get("n_peer_authorities")
            or ev.get("n_completed_peers")
            or row["peer_group_size"]
            or 0
        )
        items.append(
            {
                "signal_type": labels.CONTRACT_SIGNAL[sig],
                "signal_label": labels.SIGNAL_LABEL[sig],
                "signal_score": round(float(score), 4),
                "signal_strength": labels.strength(score),
                "explanation": labels.explanation(sig, ev, score),
                "observed_value": None,
                "expected_value": None,
                "peer_group_size": int(peer_n),
                "peer_group_level": labels.PEER_LEVEL_NUMBER.get(row["peer_level"], 0),
                # additive (Phase 12)
                "signal": sig,
                "signal_description": labels.SIGNAL_DESCRIPTION[sig],
                "direction": r["direction"],
                "tail_percentile": _f(r["tail_percentile"], 4),
            }
        )
    return sorted(items, key=lambda x: -x["signal_score"])


def not_evaluated_signals(sig_rows: dict) -> list[str]:
    return [
        labels.CONTRACT_SIGNAL[s]
        for s in fusion.BASE_SIGNALS
        if s not in sig_rows or not sig_rows[s]["eligible"]
    ]


NOT_SCORED_RECOMMENDATION = (
    "Not scored: recommended-only work with no sanction yet, so no risk assessment exists"
)


def recommendation(row, items: list[dict]) -> str:
    if not row["scored"]:
        return NOT_SCORED_RECOMMENDATION
    recs = [labels.ACTION[i["signal"]] for i in items]
    if row["scored"] and (row["active_signal_count"] or 0) >= fusion.MIN_CRITICAL_SIGNALS:
        recs.append(labels.ACTION_CORROBORATED)
    return "; ".join(recs) if recs else labels.ACTION_STANDARD


def context(row) -> dict:
    lvl = row["peer_level"]
    return {
        "peer_group_size": row["peer_group_size"] or 0,
        "peer_group_level": labels.PEER_LEVEL_NUMBER.get(lvl, 0),
        "peer_group_key": row["peer_group_key"] or "",
        "peer_group_level_name": labels.PEER_LEVEL_NAME.get(lvl, "No peer group"),
        "peer_median": _f(row["peer_median"]) or 0,
        "peer_percentile": _f(row["peer_percentile"]),
        "deviation_ratio": _f(row["deviation_ratio"]),
        "project_amount": _f(row["amount_used"]),
        "amount_basis": row["amount_basis"],
        "inferred_category": row["category"] or "",
        "state": row["state"] or "",
        "constituency": row["constituency"] or "",
        "year": row["record_date"].year if row["record_date"] else None,
        "leave_one_out": True,
    }


def source_record(row) -> dict:
    return {
        "record_id": row["work_key"],
        "mp": row["mp"] or "",
        "constituency": row["constituency"] or "",
        "state": row["state"] or "",
        "description": mask_personal(row["description"]) or "",
        "amount": labels.rupees(row["amount"]),
        "date": _date(row["record_date"]),
        "stage": row["stage"],
        # additive (Phase 12)
        "house": row["house"],
        "district_authority": row["district_authority"] or "",
    }


def risk_assessment(row, items: list[dict], s: Served) -> dict:
    conf = row["confidence"]
    risk = row["risk"]
    tier = row["tier"] if row["scored"] else "NOT_EVALUATED"
    return {
        "project_id": row["work_key"],
        "risk_score": None if risk is None else round(risk, 1),
        "risk_level": tier,
        "confidence": _f(conf, 4),
        "confidence_percent": None if conf is None else round(conf * 100, 1),
        "confidence_level": row["confidence_label"],
        "active_signals": [
            {
                "signal": i["signal_type"],
                "label": i["signal_label"],
                "score": i["signal_score"],
                "is_base": True,
            }
            for i in items
        ],
        "active_signal_count": row["active_signal_count"] or 0,
        "base_signal_count": row["active_signal_count"] or 0,
        "pattern_score": None,
        "corroboration_factor": _f(row["corroboration_factor"]),
        "peer_group_size": row["peer_group_size"] or 0,
        "peer_group_level": labels.PEER_LEVEL_NUMBER.get(row["peer_level"], 0),
        "context": context(row),
        # additive (Phase 12)
        "n_eligible": row["n_eligible"],
        "pre_multiplier": _f(row["pre_multiplier"]),
        "confidence_components": row["confidence_components"] or {},
        "config_name": s.build.config_name,
        "scored": row["scored"],
        "not_scored_reason": None
        if row["scored"]
        else "recommended-only work: no sanction yet, so not scored",
    }


def evidence_chain(row, items: list[dict], ra: dict) -> list[dict]:
    ctx = ra["context"]
    return [
        {"step": "SOURCE RECORD", "description": f"Record {row['work_key']}", "details": source_record(row)},
        {
            "step": "NORMALIZED DATA",
            "description": "Normalized amount, dates and official work type",
            "details": {
                "amount_numeric": _f(row["amount"]),
                "inferred_category": row["category"] or "",
                "amount_tier": None,
            },
        },
        {
            "step": "CONTEXT ENGINE",
            "description": "Leave-one-out peer group",
            "details": {
                "peer_group_size": ctx["peer_group_size"],
                "peer_median": ctx["peer_median"],
                "deviation_ratio": ctx["deviation_ratio"],
                "peer_group_level_name": ctx["peer_group_level_name"],
            },
        },
        {"step": "SIGNAL DETECTION", "description": f"{len(items)} active signals", "details": items},
        {
            "step": "SIGNAL FUSION",
            "description": "Weighted fusion with corroboration",
            "details": {
                "active_signal_count": ra["active_signal_count"],
                "n_eligible": ra["n_eligible"],
                "corroboration_factor": ra["corroboration_factor"],
                "pre_multiplier": ra["pre_multiplier"],
            },
        },
        {
            "step": "RISK ASSESSMENT",
            "description": "Tier from the fused score",
            "details": {
                "risk_score": ra["risk_score"],
                "risk_level": ra["risk_level"],
                "confidence": ra["confidence"],
            },
        },
    ]


def _related_row(r, relationship: str) -> dict:
    return {
        "record_id": r["work_key"],
        "mp_name": r["mp"] or "",
        "description": mask_personal(r["description"]) or "",
        "category": r["category"] or "",
        "amount": labels.rupees(r["amount"]),
        "stage": r["stage"],
        "relationship": relationship,
        "risk_level": r["tier"],
        "priority": r["tier"],
        "risk_score": None if r["risk"] is None else round(r["risk"] / 100, 6),
    }


def related_records(session: Session, s: Served, row, sig_rows: dict, max_related: int = 10) -> list[dict]:
    cols = "work_key, mp, description, category, amount, stage, tier, risk"
    out, seen = [], {row["work_key"]}
    sc, sp = _sc(s)
    p = {"run": s.run_id, "wk": row["work_key"], **sp}

    def add(rows, rel):
        for r in rows:
            if r["work_key"] not in seen:
                seen.add(r["work_key"])
                out.append(_related_row(r, rel))

    if row["mp"] and row["category"]:
        add(
            session.execute(
                text(
                    f"SELECT {cols} FROM served_work WHERE run_id = :run AND scored{sc} AND mp = :mp "
                    "AND category = :cat AND work_key <> :wk ORDER BY risk DESC NULLS LAST, work_key LIMIT 5"
                ),
                {**p, "mp": row["mp"], "cat": row["category"]},
            )
            .mappings()
            .all(),
            "Same MP, Same Category",
        )
    dup_keys = []
    nd = sig_rows.get("near_duplicate")
    if nd is not None and nd["eligible"] and labels.is_active(nd["score"]):
        mk = (nd["evidence"] or {}).get("matched_work_key")
        if mk:
            dup_keys.append(mk)
    dup_rows = (
        session.execute(
            text(
                f"SELECT {cols} FROM served_work WHERE run_id = :run AND scored{sc} AND "
                "(work_key = ANY(:keys) OR (mp = :mp AND description_normalized = :dn)) AND work_key <> :wk "
                "ORDER BY risk DESC NULLS LAST, work_key LIMIT 5"
            ),
            {**p, "keys": dup_keys, "mp": row["mp"], "dn": row["description_normalized"]},
        )
        .mappings()
        .all()
    )
    add(dup_rows, "Potential Duplicate")
    if row["constituency"] and row["category"]:
        add(
            session.execute(
                text(
                    f"SELECT {cols} FROM served_work WHERE run_id = :run AND scored{sc} "
                    "AND constituency = :c "
                    "AND category = :cat AND work_key <> :wk "
                    "ORDER BY amount DESC NULLS LAST, work_key LIMIT 3"
                ),
                {**p, "c": row["constituency"], "cat": row["category"]},
            )
            .mappings()
            .all(),
            "Same Constituency, Same Category",
        )
    return out[:max_related]


def risk_history(session: Session, s: Served, work_key: str) -> list[dict]:
    rows = (
        session.execute(
            text(
                "SELECT r.run_id, r.risk, r.tier, r.confidence, ar.finished_at FROM risk_result r "
                "JOIN analysis_run ar ON ar.id = r.run_id WHERE r.work_key = :wk AND r.config_name = :cfg "
                "ORDER BY ar.finished_at NULLS LAST, r.run_id"
            ),
            {"wk": work_key, "cfg": s.build.config_name},
        )
        .mappings()
        .all()
    )
    return [
        {
            "run_id": r["run_id"],
            "timestamp": r["finished_at"].isoformat() if r["finished_at"] else None,
            "risk_score": None if r["risk"] is None else round(r["risk"] / 100, 6),
            "risk_level": r["tier"],
            "confidence": _f(r["confidence"], 4),
        }
        for r in rows
    ][-10:]


def dossier(session: Session, s: Served, work_key: str) -> dict | None:
    """Everything one record needs, built once and reused by #3, #25,
    #28-#30 and #32."""
    row = _row(session, s, work_key)
    if row is None:
        return None
    sig_rows = _signal_rows(session, s, work_key)
    items = evidence_items(row, sig_rows)
    ra = risk_assessment(row, items, s)
    return {"row": row, "sig_rows": sig_rows, "items": items, "ra": ra}


def record_detail(session: Session, s: Served, work_key: str) -> dict | None:
    d = dossier(session, s, work_key)
    if d is None:
        return None
    row, items, ra = d["row"], d["items"], d["ra"]
    ctx = ra["context"]
    facts = (
        session.execute(
            text(
                "SELECT fact, detail FROM work_evidence_fact WHERE run_id = :run AND work_key = :wk "
                "ORDER BY fact"
            ),
            {"run": s.run_id, "wk": work_key},
        )
        .mappings()
        .all()
    )
    compliance = (
        session.execute(
            text(
                "SELECT check_code, rule, detail FROM compliance_result "
                "WHERE run_id = :run AND work_key = :wk AND NOT passed ORDER BY check_code, rule"
            ),
            {"run": s.run_id, "wk": work_key},
        )
        .mappings()
        .all()
    )
    return {
        "record_id": work_key,
        "source_record": source_record(row),
        "normalized_record": {
            "amount_numeric": _f(row["amount"]),
            "date_parsed": _date(row["record_date"]),
            "inferred_category": row["category"] or "",
            "category_confidence": None,
            "amount_tier": None,
            "category_source": "official work type from the portal's activity name (not inferred)",
        },
        "risk_assessment": ra,
        "context": ctx,
        "evidence_items": items,
        "evidence_chain": evidence_chain(row, items, ra),
        "investigation_recommendation": recommendation(row, items),
        "derived_features": {
            "peer_group_size": ctx["peer_group_size"],
            "peer_median": ctx["peer_median"],
            "peer_percentile": ctx["peer_percentile"],
            "deviation_ratio": ctx["deviation_ratio"],
        },
        "priority": {
            "priority": ra["risk_level"],
            "risk_level": ra["risk_level"],
            "confidence": ra["confidence_level"],
            "confidence_score": ra["confidence"],
            "priority_score": None if ra["risk_score"] is None else round(ra["risk_score"] / 100, 6),
            "risk_score": ra["risk_score"],
            "evidence_count": ra["active_signal_count"],
            "evidence_summary": _evidence_summary(row),
        },
        "related_records": related_records(session, s, row, d["sig_rows"]),
        "risk_history": risk_history(session, s, work_key),
        # additive (Phase 12)
        "house": row["house"],
        "not_evaluated_signals": not_evaluated_signals(d["sig_rows"]),
        "evidence_facts": [{"fact": f["fact"], "detail": f["detail"]} for f in facts],
        "compliance_flags": [
            {"check": c["check_code"], "rule": c["rule"], "detail": c["detail"]} for c in compliance
        ],
        "served": s.meta(),
    }


# ---- analytics (#4) ----------------------------------------------------------------------------------


def _flags(
    session: Session, s: Served, house, col: str, min_total: int = 0, limit: int | None = None
) -> dict:
    where, p = _scope(s, house)
    lim = f" LIMIT {int(limit)}" if limit else ""
    rows = session.execute(
        text(
            f"""
            SELECT {col} k, count(*) FILTER (WHERE tier IN ('HIGH', 'CRITICAL')) flagged, count(*) total
            FROM served_work WHERE {where} AND {col} IS NOT NULL GROUP BY 1
            HAVING count(*) >= :min_total
            ORDER BY count(*) FILTER (WHERE tier IN ('HIGH', 'CRITICAL'))::float / count(*) DESC,
                     count(*) DESC, 1
            {lim}
            """
        ),
        {**p, "min_total": min_total},
    ).all()
    return {k: {"flagged": fl, "total": t, "flag_rate": round(fl / t * 100, 1)} for k, fl, t in rows}


def analytics(session: Session, s: Served, house: str | None) -> dict:
    def build():
        if s.run_id is None:
            return {
                k: {}
                for k in (
                    "priority_distribution",
                    "risk_distribution",
                    "stage_distribution",
                    "category_distribution",
                    "amount_by_priority",
                    "amount_by_risk",
                    "state_flags",
                    "constituency_flags",
                    "mp_stats",
                    "category_flags",
                    "signal_distribution",
                    "amount_histogram",
                    "confidence_distribution",
                )
            } | {"average_confidence": 0, "model_version": s.model_version, "served": s.meta()}
        where, p = _scope(s, house)
        dist = _counts(session, s, house, "tier")
        amt = (
            session.execute(
                text(
                    f"""
            SELECT tier, avg(amount) mean, percentile_cont(0.5) WITHIN GROUP (ORDER BY amount) median,
                   min(amount) min, max(amount) max
            FROM served_work WHERE {where} AND amount IS NOT NULL GROUP BY tier
            """
                ),
                p,
            )
            .mappings()
            .all()
        )
        amount_by_risk = {
            r["tier"]: {
                "mean": _f(r["mean"]),
                "median": _f(r["median"]),
                "min": _f(r["min"]),
                "max": _f(r["max"]),
            }
            for r in amt
            if r["tier"] in ("CRITICAL", "HIGH", "MODERATE", "LOW")
        }
        sig_sql = ", ".join(
            f"count(*) FILTER (WHERE {c} > {labels.STRENGTH_HIGH}) AS {sig}_h, "
            f"count(*) FILTER (WHERE {c} > {labels.STRENGTH_MEDIUM} AND {c} <= {labels.STRENGTH_HIGH}) "
            f"AS {sig}_m, "
            f"count(*) FILTER (WHERE {c} <= {labels.STRENGTH_MEDIUM}) AS {sig}_l, "
            f"count(*) FILTER (WHERE {c} IS NULL) AS {sig}_n"
            for sig, c in labels.SIGNAL_COLUMN.items()
        )
        sg = session.execute(text(f"SELECT {sig_sql} FROM served_work WHERE {where}"), p).mappings().one()
        signal_dist = {
            labels.CONTRACT_SIGNAL_KEY[sig]: {
                "HIGH": sg[f"{sig}_h"],
                "MEDIUM": sg[f"{sig}_m"],
                "LOW": sg[f"{sig}_l"],
                "NOT_EVALUATED": sg[f"{sig}_n"],
            }
            for sig in fusion.BASE_SIGNALS
        }
        conf = session.execute(text(f"SELECT avg(confidence) FROM served_work WHERE {where}"), p).scalar()
        return {
            "priority_distribution": dist,
            "risk_distribution": dist,
            "stage_distribution": _counts(session, s, house, "stage"),
            "category_distribution": _counts(session, s, house, "category"),
            "amount_by_priority": amount_by_risk,
            "amount_by_risk": amount_by_risk,
            "state_flags": _flags(session, s, house, "state"),
            "constituency_flags": _flags(session, s, house, "constituency", min_total=10, limit=20),
            "mp_stats": _flags(session, s, house, "mp", min_total=5, limit=15),
            "category_flags": _flags(session, s, house, "category", min_total=MIN_WORKS_FOR_RATE),
            "signal_distribution": signal_dist,
            "amount_histogram": _histogram(session, s, where, p),
            "confidence_distribution": _counts(session, s, house, "confidence_label"),
            "average_confidence": round(float(conf) * 100, 1) if conf is not None else 0,
            "model_version": s.model_version,
            # additive (Phase 12)
            "category_flags_min_works": MIN_WORKS_FOR_RATE,
            "served": s.meta(),
        }

    return _cached(s.cache_key("analytics", house), build)


def _histogram(session: Session, s: Served, where: str, p: dict) -> dict:
    """The old engine's bins exactly (backend/app/core/engine.py
    get_analytics): 0, 50k, 1L, 3L, 5L, 10L, 20L, p95, p99, max."""
    q = session.execute(
        text(
            f"SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY amount), "
            f"percentile_cont(0.99) WITHIN GROUP (ORDER BY amount), max(amount) "
            f"FROM served_work WHERE {where} AND amount IS NOT NULL"
        ),
        p,
    ).one()
    if q[2] is None:
        return {}
    bins = sorted(
        {
            0.0,
            50000.0,
            100000.0,
            300000.0,
            500000.0,
            1000000.0,
            2000000.0,
            float(q[0]),
            float(q[1]),
            float(q[2]),
        }
    )
    cases = " ".join(
        f"WHEN amount >= {bins[i]} AND amount {'<=' if i == len(bins) - 2 else '<'} {bins[i + 1]} THEN {i}"
        for i in range(len(bins) - 1)
    )
    rows = dict(
        session.execute(
            text(
                f"SELECT CASE {cases} END b, count(*) FROM served_work "
                f"WHERE {where} AND amount IS NOT NULL GROUP BY 1"
            ),
            p,
        ).all()
    )
    return {f"Rs.{int(bins[i]):,}-{int(bins[i + 1]):,}": int(rows.get(i, 0)) for i in range(len(bins) - 1)}


# ---- data health (#5) ----------------------------------------------------------------------------------

SIGNALS_UNAVAILABLE = [
    "Tender/Procurement Analysis (no tender data in source)",
    "Beneficiary Analysis (no beneficiary data in source)",
    "Work-level GPS Anomaly Detection (only constituency-level coordinates available)",
    "Physical Progress Verification (no progress percentage data in source)",
]
COLUMNS = ["Record ID", "State", "MP", "Constituency", "Work Description", "Date", "Amount", "Stage"]


def data_health(session: Session, s: Served) -> dict:
    def build():
        if s.run_id is None:
            return {
                "signals_enabled": [],
                "signals_unavailable": SIGNALS_UNAVAILABLE,
                "category_inference": {"total": 0, "inferred": 0, "categories": {}},
                "risk_summary": {"critical": 0, "high": 0, "moderate": 0, "low": 0},
                "confidence_summary": {"high": 0, "medium": 0, "low": 0, "average": 0},
                "served": s.meta(),
            }
        sc, sp = _sc(s)
        p = {"run": s.run_id, **sp}
        t = (
            session.execute(
                text(
                    f"""
            SELECT count(*) total,
                   count(*) FILTER (WHERE description IS NULL OR btrim(description) = '') no_desc,
                   count(*) FILTER (WHERE amount IS NULL) no_amount,
                   count(*) FILTER (WHERE mp IS NULL) no_mp,
                   count(*) FILTER (WHERE state IS NULL) no_state,
                   count(*) FILTER (WHERE record_date IS NULL) no_date,
                   count(*) FILTER (WHERE house = 'LS') ls,
                   count(*) FILTER (WHERE house = 'LS' AND constituency IS NULL) ls_no_cons,
                   count(*) FILTER (WHERE description IS NULL OR btrim(description) = '' OR amount IS NULL)
                       incomplete,
                   count(DISTINCT state) states, count(DISTINCT mp) mps, count(DISTINCT constituency) cons,
                   count(*) FILTER (WHERE activity_type_id IS NOT NULL) typed
            FROM served_work WHERE run_id = :run{sc}
            """
                ),
                p,
            )
            .mappings()
            .one()
        )
        n = t["total"]
        dup = session.execute(
            text(
                f"""
            SELECT coalesce(sum(c), 0) FROM (
              SELECT count(*) c FROM served_work
              WHERE run_id = :run{sc} AND description_normalized IS NOT NULL
              GROUP BY mp, description_normalized, amount, record_date, category HAVING count(*) > 1) x
            """
            ),
            p,
        ).scalar_one()
        near = session.execute(
            text(
                f"""
            SELECT coalesce(sum(c), 0) FROM (
              SELECT count(*) c FROM served_work
              WHERE run_id = :run{sc} AND description_normalized IS NOT NULL
              GROUP BY mp, description_normalized HAVING count(*) > 1) x
            """
            ),
            p,
        ).scalar_one()
        files = session.execute(
            text(
                "SELECT count(*), coalesce(sum(row_count), 0) FROM raw_file rf JOIN analysis_run ar "
                "ON ar.source_snapshot_id = rf.source_snapshot_id WHERE ar.id = :run"
            ),
            p,
        ).one()
        rejects = session.execute(
            text(
                "SELECT count(*) FROM import_reject ir JOIN raw_file rf ON rf.id = ir.raw_file_id "
                "JOIN analysis_run ar ON ar.source_snapshot_id = rf.source_snapshot_id WHERE ar.id = :run"
            ),
            p,
        ).scalar_one()
        recon = session.execute(
            text(
                "SELECT count(*), count(*) FILTER (WHERE passed) FROM compliance_result "
                "WHERE run_id = :run AND check_code = 'C9'"
            ),
            p,
        ).one()
        snap = session.execute(
            text(
                "SELECT ss.label, ss.data_as_of FROM analysis_run ar JOIN source_snapshot ss "
                "ON ss.id = ar.source_snapshot_id WHERE ar.id = :run"
            ),
            p,
        ).one()
        tiers = _counts(session, s, None, "tier")
        confs = _counts(session, s, None, "confidence_label")
        avg_conf = session.execute(
            text(f"SELECT avg(confidence) FROM served_work WHERE run_id = :run AND scored{sc}"), p
        ).scalar()
        pct = lambda k, d=n: round(t[k] / d * 100, 2) if d else 0  # noqa: E731
        return {
            "source_file": f"MPLADS portal exports ({files[0]} files)",
            "total_records": n,
            "valid_records": n - int(dup),  # the old definition (data_service.py)
            "incomplete_records": t["incomplete"],
            "duplicate_candidates": int(dup),
            "near_duplicate_candidates": int(near),
            "columns": COLUMNS,
            "missingness": {
                "Record ID": 0.0,
                "State": pct("no_state"),
                "MP": pct("no_mp"),
                "Constituency": pct("ls_no_cons", t["ls"]),
                "Work Description": pct("no_desc"),
                "Date": pct("no_date"),
                "Amount": pct("no_amount"),
                "Stage": 0.0,
            },
            "stage_distribution": _counts(session, s, None, "stage", scored=False),
            "state_count": t["states"],
            "mp_count": t["mps"],
            "constituency_count": t["cons"],
            "signals_enabled": [labels.CONTRACT_SIGNAL[x] for x in fusion.BASE_SIGNALS],
            "signals_unavailable": SIGNALS_UNAVAILABLE,
            "category_inference": {
                "total": n,
                "inferred": t["typed"],
                "categories": _counts(session, s, None, "category", scored=False),
            },
            "risk_summary": {k.lower(): tiers.get(k, 0) for k in ("CRITICAL", "HIGH", "MODERATE", "LOW")},
            "confidence_summary": {
                "high": confs.get("HIGH", 0),
                "medium": confs.get("MEDIUM", 0),
                "low": confs.get("LOW", 0),
                "average": round(float(avg_conf) * 100, 1) if avg_conf is not None else 0,
            },
            # additive (Phase 12)
            "unit": "works in the served run's snapshot (all lifecycle stages); "
            "risk figures are scored works only",
            "data_as_of": str(snap.data_as_of) if snap.data_as_of else None,
            "raw_rows": int(files[1]),
            "import_rejects": int(rejects),
            "control_totals": {"checked": recon[0], "reconciled": recon[1]},
            "missingness_notes": {
                "Constituency": "Lok Sabha works only; Rajya Sabha members have no constituency"
            },
            "signals_enabled_descriptions": {
                labels.CONTRACT_SIGNAL[x]: labels.SIGNAL_DESCRIPTION[x] for x in fusion.BASE_SIGNALS
            },
            "evidence_only_analyses": [
                "Payee concentration, price position and reach (entity analytics; never enter risk)",
                "Implementing agency and district authority profiles (entity analytics; never enter risk)",
                "Multivariate atypicality (Isolation Forest, Mahalanobis): evidence only, computed offline, "
                "not yet served by the API",
            ],
            "served": s.meta(),
        }

    return _cached(s.cache_key("data_health"), build)


# ---- filter lists (#6-#8, #15) -------------------------------------------------------------------------


def distinct(session: Session, s: Served, col: str) -> list[str]:
    if s.run_id is None:
        return []
    sc, sp = _sc(s)
    return list(
        session.execute(
            text(
                f"SELECT DISTINCT {col} FROM served_work WHERE run_id = :run{sc} "
                f"AND {col} IS NOT NULL ORDER BY 1"
            ),
            {"run": s.run_id, **sp},
        ).scalars()
    )


def constituencies(session: Session, s: Served) -> list[dict]:
    if s.run_id is None:
        return []
    sc, sp = _sc(s)
    rows = session.execute(
        text(
            f"SELECT DISTINCT constituency_state, constituency FROM served_work WHERE run_id = :run{sc} "
            "AND constituency IS NOT NULL ORDER BY 1, 2"
        ),
        {"run": s.run_id, **sp},
    ).all()
    return [{"State": st or "", "Constituency": c} for st, c in rows]


# ---- MP / constituency performance (#16-#19) -----------------------------------------------------------


def _profile(rows: list) -> dict:
    total = len(rows)
    amounts = [r["amount"] for r in rows if r["amount"] is not None]
    stage_dist = Counter(r["stage"] for r in rows)
    stage_amounts: dict = {}
    for r in rows:
        stage_amounts[r["stage"]] = stage_amounts.get(r["stage"], 0.0) + float(r["amount"] or 0)
    cat_dist = Counter(r["category"] for r in rows if r["category"])
    cat_amounts: dict = {}
    for r in rows:
        if r["category"]:
            cat_amounts[r["category"]] = cat_amounts.get(r["category"], 0.0) + float(r["amount"] or 0)
    scored = [r for r in rows if r["scored"]]
    risk_dist = dict(Counter(r["tier"] for r in scored))
    crit, high = risk_dist.get("CRITICAL", 0), risk_dist.get("HIGH", 0)
    mod, low = risk_dist.get("MODERATE", 0), risk_dist.get("LOW", 0)
    signal_summary = {}
    for sig in fusion.BASE_SIGNALS:
        vals = [r[labels.SIGNAL_COLUMN[sig]] for r in scored]
        active = sum(1 for v in vals if labels.is_active(v))
        if active:
            signal_summary[labels.CONTRACT_SIGNAL_KEY[sig]] = {
                "active_count": active,
                "high_count": sum(1 for v in vals if v is not None and v > labels.STRENGTH_HIGH),
            }
    trend_acc: dict = {}
    for r in rows:
        d = r["record_date"]
        if d is None:
            continue
        key = (d.year, (d.month - 1) // 3 + 1)
        w, a = trend_acc.get(key, (0, 0.0))
        trend_acc[key] = (w + 1, a + float(r["amount"] or 0))
    trend = [
        {"period": f"{y}-Q{q}", "year": y, "quarter": q, "works": w, "amount": a}
        for (y, q), (w, a) in sorted(trend_acc.items())
    ]
    top = sorted(scored, key=lambda r: (-(r["risk"] if r["risk"] is not None else -1), r["work_key"]))[:10]
    amount_by_priority = {}
    for t in ("CRITICAL", "HIGH", "MODERATE", "LOW"):
        sub = [float(r["amount"]) for r in scored if r["tier"] == t and r["amount"] is not None]
        if sub:
            amount_by_priority[t] = {"count": len(sub), "total": sum(sub), "mean": sum(sub) / len(sub)}
    n_scored = len(scored)
    completed = stage_dist.get("COMPLETED", 0)
    return {
        "total_works": total,
        "completed_works": completed,
        "sanctioned_works": stage_dist.get("SANCTIONED", 0),
        "recommended_works": stage_dist.get("RECOMMENDED", 0),
        "completion_rate": round(completed / total * 100, 1) if total else 0.0,
        "total_recorded_amount": float(sum(amounts)),
        "average_work_amount": float(sum(amounts) / len(amounts)) if amounts else 0.0,
        "completed_amount": stage_amounts.get("COMPLETED", 0.0),
        "stage_distribution": dict(stage_dist),
        "stage_amounts": stage_amounts,
        "category_distribution": dict(cat_dist),
        "category_amounts": cat_amounts,
        "risk_distribution": risk_dist,
        "critical_count": crit,
        "high_priority_count": high,
        "review_recommended_count": mod,
        "low_risk_count": low,
        "high_priority": crit + high,
        "review_recommended": mod,
        "normal": low,
        "risk_rate": round((crit + high) / n_scored * 100, 1) if n_scored else 0.0,
        "amount_by_priority": amount_by_priority,
        "signal_summary": signal_summary,
        "trend": trend,
        "top_flagged_works": [
            {
                "record_id": r["work_key"],
                "description": (mask_personal(r["description"]) or "")[:120],
                "amount": _f(r["amount"]),
                "stage": r["stage"],
                "category": r["category"] or "",
                "risk_level": r["tier"],
                "priority": r["tier"],
                "risk_score": None if r["risk"] is None else round(r["risk"] / 100, 6),
                "evidence_count": r["active_signal_count"],
            }
            for r in top
        ],
        # additive (Phase 12): the frontend's MP donut reads this key
        "priority_distribution": risk_dist,
        "scored_works": n_scored,
        "risk_rate_basis": "flagged (HIGH + CRITICAL) / scored works; recommended-only works are not scored",
    }


_PROFILE_COLS = (
    "work_key, scored, house, mp, description, category, amount, stage, record_date, tier, risk, "
    "active_signal_count, constituency, constituency_state, state, "
    + ", ".join(labels.SIGNAL_COLUMN.values())
)


def _mode(values) -> str:
    vals = [v for v in values if v]
    return Counter(vals).most_common(1)[0][0] if vals else ""


def mp_profile(session: Session, s: Served, mp: str) -> dict | None:
    if s.run_id is None:
        return None
    sc, sp = _sc(s)
    rows = (
        session.execute(
            text(f"SELECT {_PROFILE_COLS} FROM served_work WHERE run_id = :run AND mp = :mp{sc}"),
            {"run": s.run_id, "mp": mp, **sp},
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    houses = sorted({r["house"] for r in rows})
    return {
        "mp_name": mp,
        "constituency": _mode(r["constituency"] for r in rows),
        "state": _mode(r["state"] for r in rows),
        **_profile(rows),
        "house": houses[0] if len(houses) == 1 else None,
        "served": s.meta(),
    }


def constituency_profile(session: Session, s: Served, name: str) -> dict | None:
    if s.run_id is None:
        return None
    sc, sp = _sc(s)
    rows = (
        session.execute(
            text(f"SELECT {_PROFILE_COLS} FROM served_work WHERE run_id = :run AND constituency = :c{sc}"),
            {"run": s.run_id, "c": name, **sp},
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    return {
        "constituency_name": name,
        "state": _mode(r["constituency_state"] for r in rows),
        "mps": sorted({r["mp"] for r in rows if r["mp"]}),
        **_profile(rows),
        "house": "LS",
        "served": s.meta(),
    }


def comparison(session: Session, s: Served, names: list[str], kind: str) -> dict:
    out = []
    for name in names:
        prof = mp_profile(session, s, name) if kind == "MP" else constituency_profile(session, s, name)
        out.append({"name": name, **prof} if prof else {"name": name, "error": "No records found"})
    return {"comparison": out, "entity_type": kind}


# ---- investigation actions (#13, #14, #32) ------------------------------------------------------------
# Writes go through app/audit/case_log.py: the actor is the authenticated
# principal (never the request body) and every event is hash-chained.


def investigate(session: Session, s: Served, work_key: str, decision: str, note: str, principal) -> dict:
    ev = case_log.append(
        session,
        work_key=work_key,
        event_type="investigation_decision",
        decision=decision,
        note=note,
        principal=principal,
        run_id=s.run_id,
    )
    return case_log.entry(ev)


def audit_trail(session: Session, s: Served) -> list[dict]:
    return case_log.trail(session, s.scope, s.run_id, limit=AUDIT_LIMIT)


def request_recalculation(session: Session, s: Served, work_key: str, principal):
    return case_log.append(
        session,
        work_key=work_key,
        event_type="recalculate_requested",
        decision=None,
        note="flagged for the next analysis run (scores are only recomputed by a full run)",
        principal=principal,
        run_id=s.run_id,
    )

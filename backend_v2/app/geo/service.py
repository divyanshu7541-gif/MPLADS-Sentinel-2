"""
Phase 9 map read side: the queries behind the six map endpoints and the
area drill-down. Reads only the per-run map tables (map_build, map_work,
geo_metric) and geo_area / geo_name_crosswalk -- never work, work_state,
risk_result or signal_result at request time (tests/test_phase9_map.py
captures every statement to prove it).

Rules applied here (docs/phase9_report.md):
  * Search is a literal, case-insensitive substring match. User text is
    escaped for LIKE (\\ % _) and bound as a parameter -- never a regex,
    never interpolated. >= 3 characters is served by the pg_trgm index;
    shorter text scans the served run's map_work rows only.
  * Small-number rule: an area with fewer than MIN_WORKS_FOR_RATE works
    shows its counts and sums, but its rates and averages are null with
    insufficient_data = true.
  * Markers: every work in a constituency shares that constituency's one
    representative point (no jitter); location_precision says so.
  * map-works is capped at MAP_WORKS_CAP rows; above it, a proportional
    sample stratified by state (1 per state first, the rest in proportion),
    highest risk first within each state's quota -- the old engine's rule,
    now in SQL, with its over-cap edge case fixed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth.scope import NATIONAL, Scope
from ..serving.labels import confidence_label
from ..serving.public_mask import public_search_clause
from ..serving.redact import mask_personal
from . import boundaries
from .build import SIGNAL_HIGH, served_build

# map_work.search_text minus the description (app/geo/build.py _search_text;
# map_work.state is the location state).
MAP_SEARCH_COLS_EXCEPT_DESCRIPTION = ("work_key", "mp", "constituency", "state", "district_name", "category")

MIN_WORKS_FOR_RATE = 10  # judgment call, flagged in docs/phase9_report.md
MAP_WORKS_CAP = 3000  # judgment call, flagged in docs/phase9_report.md
MAX_SEARCH_CHARS = 100
TIERS = ("CRITICAL", "HIGH", "MODERATE", "LOW", "NOT_EVALUATED")
FLAGGED = ("CRITICAL", "HIGH")
TIER_RANK_SQL = (
    "CASE mw.tier WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MODERATE' THEN 2 "
    "WHEN 'LOW' THEN 3 ELSE 4 END"
)
LOCATION_PRECISION = "approximate_constituency_level"
PRECISION_NOTE = (
    "Markers are placed at one representative point inside each constituency boundary; every work in a "
    "constituency shares that point. It is not the work's site."
)
RS_CONSTITUENCY = "Sitting Rajya Sabha"
REDELIMITED_METHOD = "redelimited_boundary_not_available"
REDELIMITED_PLACEMENT = "state_level_redelimited"
RS_NOT_APPLICABLE = "not applicable for Rajya Sabha: members have no constituency"
CONFIDENCE_BANDS_NOTE = (
    "Bands are the old engine's (HIGH >= 0.7, MEDIUM >= 0.5, else LOW), carried over for contract meaning "
    "at the Phase 12 cutover -- the same bands every other endpoint uses (app/serving/labels.py)."
)


@dataclass
class Served:
    run_id: int | None
    data_as_of: dt.date | None
    is_latest: bool
    counts: dict
    # Phase 13: the caller's data scope. geo_metric and the build counts are
    # precomputed NATIONAL aggregates, so a scoped caller is always answered
    # from map_work with the scope applied in SQL (_scoped()), never from them.
    scope: Scope = field(default=NATIONAL)

    def headers(self) -> dict[str, str]:
        if self.run_id is None:
            return {"X-Map-Status": "not-built"}
        return {
            "X-Map-Run-Id": str(self.run_id),
            "X-Map-Data-As-Of": str(self.data_as_of or ""),
            "X-Map-Is-Latest": "true" if self.is_latest else "false",
        }

    def meta(self) -> dict:
        return {
            "run_id": self.run_id,
            "data_as_of": str(self.data_as_of) if self.data_as_of else None,
            "is_latest": self.is_latest,
        }


def served(session: Session, scope: Scope = NATIONAL) -> Served:
    b, latest = served_build(session)
    if b is None:
        return Served(None, None, False, {}, scope)
    return Served(b.run_id, b.data_as_of, latest, b.counts or {}, scope)


def _scoped(s: Served, where: list[str], p: dict) -> None:
    """Append the caller's scope to a map_work (alias mw) WHERE list."""
    clause, sp = s.scope.exists("mw")
    if clause:
        where.append(clause.removeprefix(" AND "))
        p.update(sp)


def like_pattern(search: str) -> str:
    """Literal substring pattern for LIKE ... ESCAPE '\\'."""
    q = search.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{q}%"


def clean_search(search: str | None) -> str | None:
    """None/blank -> None. Raises ValueError for text we refuse (too long, NUL)."""
    if search is None or not search.strip():
        return None
    if len(search) > MAX_SEARCH_CHARS:
        raise ValueError(f"search is longer than {MAX_SEARCH_CHARS} characters")
    if "\x00" in search:
        raise ValueError("search contains a NUL character")
    return search.strip()


def _filters(
    house, tiers, stage, search, state_col: str, state, alias: str, public: bool = False
) -> tuple[list[str], dict]:
    where, p = [], {}
    if house:
        where.append(f"{alias}.house = :house")
        p["house"] = house
    if tiers:
        where.append(f"{alias}.tier = ANY(:tiers)")
        p["tiers"] = list(tiers)
    if stage:
        where.append(f"{alias}.stage = :stage")
        p["stage"] = stage
    if state:
        where.append(f"{state_col} = :state")
        p["state"] = state
    if search:
        where.append(f"{alias}.search_text LIKE :pat ESCAPE '\\'")
        p["pat"] = like_pattern(search)
        if public:  # anonymous: never match words a masked description hides
            extra, ep = public_search_clause(f"{alias}.", MAP_SEARCH_COLS_EXCEPT_DESCRIPTION, "pat")
            where.append(extra.removeprefix(" AND "))
            p.update(ep)
    return where, p


def _rate(num: float, den: float, n: int, scale: float = 100.0, nd: int = 1):
    if n < MIN_WORKS_FOR_RATE or not den:
        return None
    return round(num / den * scale, nd)


# ---- map-data (#9) ------------------------------------------------------------------


def map_data(
    session: Session, s: Served, *, state=None, tier=None, stage=None, search=None, house=None, public=False
):
    if s.run_id is None:
        return []
    tiers = [tier] if tier else None
    scoped = not s.scope.national
    if search or scoped:
        where, p = _filters(house, tiers, stage, search, "mw.constituency_state", state, "mw", public)
        _scoped(s, where, p)
        sql = f"""
            SELECT ga.key AS area_key, ga.attrs->>'dataset_state' AS st,
                   ga.attrs->>'dataset_constituency' AS co, ga.rep_lat, ga.rep_lon, mw.tier, mw.stage,
                   count(*) AS n, string_agg(DISTINCT mw.mp, '|') AS mps_agg,
                   coalesce(sum(mw.amount), 0) AS amount_sum, coalesce(sum(mw.risk), 0) AS risk_sum,
                   count(mw.risk) AS risk_n, coalesce(sum(mw.confidence), 0) AS confidence_sum,
                   coalesce(sum(mw.active_signals), 0) AS signals_sum
            FROM map_work mw JOIN geo_area ga ON ga.id = mw.constituency_area_id
            WHERE mw.run_id = :run {''.join(' AND ' + w for w in where)}
            GROUP BY 1, 2, 3, 4, 5, 6, 7"""
    else:
        where, p = _filters(house, tiers, stage, None, "gm.state", state, "gm")
        sql = f"""
            SELECT gm.area_key, ga.attrs->>'dataset_state' AS st, ga.attrs->>'dataset_constituency' AS co,
                   ga.rep_lat, ga.rep_lon, gm.tier, gm.stage, sum(gm.n) AS n,
                   sum(gm.amount_sum) AS amount_sum,
                   sum(gm.risk_sum) AS risk_sum, sum(gm.risk_n) AS risk_n,
                   sum(gm.confidence_sum) AS confidence_sum, sum(gm.signals_sum) AS signals_sum
            FROM geo_metric gm JOIN geo_area ga ON ga.level = 'constituency' AND ga.key = gm.area_key
            WHERE gm.run_id = :run AND gm.level = 'constituency' {''.join(' AND ' + w for w in where)}
            GROUP BY 1, 2, 3, 4, 5, 6, 7"""
    rows = session.execute(text(sql), {"run": s.run_id, **p}).mappings().all()
    areas: dict[str, dict] = {}
    for r in rows:
        a = areas.setdefault(
            r["area_key"],
            {
                "st": r["st"],
                "co": r["co"],
                "lat": r["rep_lat"],
                "lon": r["rep_lon"],
                "tier": Counter(),
                "stage": Counter(),
                "n": 0,
                "amount": 0.0,
                "exposure": 0.0,
                "risk_sum": 0.0,
                "risk_n": 0,
                "conf": 0.0,
                "sig": 0.0,
                "mps": set(),
            },
        )
        if scoped and r.get("mps_agg"):
            a["mps"].update(r["mps_agg"].split("|"))
        n = int(r["n"])
        a["tier"][r["tier"]] += n
        a["stage"][r["stage"]] += n
        a["n"] += n
        a["amount"] += float(r["amount_sum"])
        a["exposure"] += float(r["amount_sum"]) if r["tier"] in FLAGGED else 0.0
        a["risk_sum"] += float(r["risk_sum"])
        a["risk_n"] += int(r["risk_n"])
        a["conf"] += float(r["confidence_sum"])
        a["sig"] += float(r["signals_sum"])
    mps = s.counts.get("constituency", {}).get("mps", {})
    out = []
    for key, a in sorted(areas.items(), key=lambda kv: (kv[1]["st"], kv[1]["co"])):
        n = a["n"]
        flagged = a["tier"]["CRITICAL"] + a["tier"]["HIGH"]
        avg_risk = _rate(a["risk_sum"], a["risk_n"], n, 1.0, 4)
        avg_conf = _rate(a["conf"], n, n, 1.0, 4)
        out.append(
            {
                "State": a["st"],
                "Constituency": a["co"],
                "total": n,
                "flagged": flagged,
                "critical": a["tier"]["CRITICAL"],
                "high": a["tier"]["HIGH"],
                "moderate": a["tier"]["MODERATE"],
                "low": a["tier"]["LOW"],
                "total_amount": round(a["amount"], 2),
                "avg_risk": avg_risk,
                "mps": ", ".join(sorted(a["mps"])[:3] if scoped else mps.get(key, [])[:3]),
                "stages": ", ".join(st for st, _ in a["stage"].most_common(3)),
                "flag_rate": _rate(flagged, n, n),
                "avg_risk_pct": None if avg_risk is None else round(avg_risk * 100, 1),
                "financial_exposure": round(a["exposure"], 2),
                "avg_confidence": avg_conf,
                "avg_confidence_pct": None if avg_conf is None else round(avg_conf * 100, 1),
                "avg_signals": _rate(a["sig"], n, n, 1.0, 1),
                "latitude": a["lat"],
                "longitude": a["lon"],
                "location_level": "CONSTITUENCY",
                "geocoding_status": "boundary_representative_point",
                # additive (Phase 9)
                "area_key": key,
                "location_precision": LOCATION_PRECISION,
                "insufficient_data": n < MIN_WORKS_FOR_RATE,
                "not_evaluated": a["tier"]["NOT_EVALUATED"],
            }
        )
    return out


def map_state_data(
    session: Session, s: Served, *, state=None, tier=None, stage=None, search=None, house=None, public=False
):
    """map-data with level=state (additive, 2026-10-02): one row per state,
    for the Rajya Sabha map (members represent a state, not a constituency).
    "State" is where the works are located (geo_metric's state level, the
    authority's state), not the member's own state. Works with no located
    state ('unlocated:state') are left out, as unlocated constituencies are
    left out of the constituency rows. Same fields and small-number rule as
    the constituency rows; reads geo_metric (map_work when searching or
    scoped), never the base tables."""
    if s.run_id is None:
        return []
    tiers = [tier] if tier else None
    if search or not s.scope.national:
        where, p = _filters(house, tiers, stage, search, "mw.state", state, "mw", public)
        _scoped(s, where, p)
        sql = f"""
            SELECT mw.state AS st, mw.tier, mw.stage, count(*) AS n,
                   coalesce(sum(mw.amount), 0) AS amount_sum, coalesce(sum(mw.risk), 0) AS risk_sum,
                   count(mw.risk) AS risk_n, coalesce(sum(mw.confidence), 0) AS confidence_sum,
                   coalesce(sum(mw.active_signals), 0) AS signals_sum
            FROM map_work mw
            WHERE mw.run_id = :run AND mw.state IS NOT NULL {''.join(' AND ' + w for w in where)}
            GROUP BY 1, 2, 3"""
    else:
        where, p = _filters(house, tiers, stage, None, "gm.state", state, "gm")
        sql = f"""
            SELECT gm.area_key AS st, gm.tier, gm.stage, sum(gm.n) AS n, sum(gm.amount_sum) AS amount_sum,
                   sum(gm.risk_sum) AS risk_sum, sum(gm.risk_n) AS risk_n,
                   sum(gm.confidence_sum) AS confidence_sum, sum(gm.signals_sum) AS signals_sum
            FROM geo_metric gm
            WHERE gm.run_id = :run AND gm.level = 'state' AND gm.area_key NOT LIKE 'unlocated:%'
                  {''.join(' AND ' + w for w in where)}
            GROUP BY 1, 2, 3"""
    rows = session.execute(text(sql), {"run": s.run_id, **p}).mappings().all()
    areas: dict[str, dict] = {}
    for r in rows:
        a = areas.setdefault(
            r["st"],
            {
                "tier": Counter(),
                "stage": Counter(),
                "n": 0,
                "amount": 0.0,
                "exposure": 0.0,
                "risk_sum": 0.0,
                "risk_n": 0,
                "conf": 0.0,
                "sig": 0.0,
            },
        )
        n = int(r["n"])
        a["tier"][r["tier"]] += n
        a["stage"][r["stage"]] += n
        a["n"] += n
        a["amount"] += float(r["amount_sum"])
        a["exposure"] += float(r["amount_sum"]) if r["tier"] in FLAGGED else 0.0
        a["risk_sum"] += float(r["risk_sum"])
        a["risk_n"] += int(r["risk_n"])
        a["conf"] += float(r["confidence_sum"])
        a["sig"] += float(r["signals_sum"])
    out = []
    for st, a in sorted(areas.items()):
        n = a["n"]
        flagged = a["tier"]["CRITICAL"] + a["tier"]["HIGH"]
        avg_risk = _rate(a["risk_sum"], a["risk_n"], n, 1.0, 4)
        avg_conf = _rate(a["conf"], n, n, 1.0, 4)
        out.append(
            {
                "State": st,
                "Constituency": None,
                "level": "state",
                "state_basis": "work_location",
                "total": n,
                "flagged": flagged,
                "critical": a["tier"]["CRITICAL"],
                "high": a["tier"]["HIGH"],
                "moderate": a["tier"]["MODERATE"],
                "low": a["tier"]["LOW"],
                "total_amount": round(a["amount"], 2),
                "avg_risk": avg_risk,
                "stages": ", ".join(sg for sg, _ in a["stage"].most_common(3)),
                "flag_rate": _rate(flagged, n, n),
                "avg_risk_pct": None if avg_risk is None else round(avg_risk * 100, 1),
                "financial_exposure": round(a["exposure"], 2),
                "avg_confidence": avg_conf,
                "avg_confidence_pct": None if avg_conf is None else round(avg_conf * 100, 1),
                "avg_signals": _rate(a["sig"], n, n, 1.0, 1),
                "area_key": st,
                "insufficient_data": n < MIN_WORKS_FOR_RATE,
                "not_evaluated": a["tier"]["NOT_EVALUATED"],
            }
        )
    return out


# ---- map-works (#10) and the paginated drill-down -----------------------------------------


def _work_json(r) -> dict:
    risk = r["risk"]
    conf = r["confidence"]
    return {
        "record_id": r["work_key"],
        "latitude": r["latitude"],
        "longitude": r["longitude"],
        "location_level": "CONSTITUENCY" if r["latitude"] is not None else "UNLOCATED",
        "location_precision": LOCATION_PRECISION if r["latitude"] is not None else None,
        "state": r["constituency_state"],
        "constituency": r["constituency"],
        "mp": r["mp"],
        "description": (mask_personal(r["description"]) or "")[:120],
        "amount": r["amount"],
        "stage": r["stage"],
        "category": r["category"],
        "risk_level": r["tier"],
        "priority": r["tier"],
        "risk_score": risk,
        "priority_score": risk,
        "confidence_score": conf,
        "confidence_percent": None if conf is None else round(conf * 100, 1),
        "evidence_count": r["active_signals"],
        "active_signal_count": r["active_signals"],
        "base_signal_count": r["active_signals"],
        "evidence_summary": None,
        "date": str(r["record_date"]) if r["record_date"] else None,
        # additive (Phase 9)
        "house": r["house"],
        "district": r["district_name"],
        "work_location_state": r["state"],
    }


_WORK_COLS = (
    "mw.work_key, mw.house, mw.state, mw.constituency, mw.constituency_state, mw.district_name, mw.latitude, "
    "mw.longitude, mw.tier, mw.risk, mw.confidence, mw.active_signals, mw.amount, mw.stage, mw.category, "
    "mw.mp, mw.description, mw.record_date"
)


def map_works(
    session: Session,
    s: Served,
    *,
    state=None,
    constituency=None,
    tier=None,
    stage=None,
    search=None,
    house=None,
    limit=MAP_WORKS_CAP,
    public=False,
    redelimited=False,
) -> tuple[list[dict], dict]:
    """Works with a marker. Returns (rows, info) -- info feeds response headers.

    redelimited=True (additive, 2026-10-03): instead, the Lok Sabha works of
    the seats the coverage data marks re-delimited (no polygon, so no stored
    point), returned with latitude/longitude null as stored. The Map places
    them at the state shape's marker point from /api/boundary-outlines."""
    cap = max(1, min(int(limit), MAP_WORKS_CAP))
    if s.run_id is None:
        return [], {"total": 0, "returned": 0, "cap": cap, "sampled": False}
    where, p = _filters(
        house, [tier] if tier else None, stage, search, "mw.constituency_state", state, "mw", public
    )
    if redelimited:
        where.append("mw.latitude IS NULL")
        where.append("mw.house = 'LS'")
        where.append(
            "EXISTS (SELECT 1 FROM geo_name_crosswalk x WHERE x.level = 'constituency' "
            f"AND x.method = '{REDELIMITED_METHOD}' "
            "AND x.portal_name = mw.constituency_state || '|' || mw.constituency)"
        )
    else:
        where.append("mw.latitude IS NOT NULL")
    _scoped(s, where, p)
    if constituency:
        where.append("mw.constituency = :constituency")
        p["constituency"] = constituency
    cond = "mw.run_id = :run" + "".join(" AND " + w for w in where)
    p["run"] = s.run_id
    per_state = dict(
        session.execute(
            text(f"SELECT mw.constituency_state, count(*) FROM map_work mw WHERE {cond} GROUP BY 1"), p
        ).all()
    )
    total = sum(per_state.values())
    order = f"{TIER_RANK_SQL}, mw.risk DESC NULLS LAST, mw.work_key"
    if total <= cap:
        rows = (
            session.execute(text(f"SELECT {_WORK_COLS} FROM map_work mw WHERE {cond} ORDER BY {order}"), p)
            .mappings()
            .all()
        )
        return [_work_json(r) for r in rows], {
            "total": total,
            "returned": len(rows),
            "cap": cap,
            "sampled": False,
        }
    quotas = stratified_quotas(per_state, cap)
    p["q_states"] = list(quotas)
    p["q_n"] = [quotas[k] for k in quotas]
    rows = (
        session.execute(
            text(f"""
        WITH q AS (SELECT * FROM unnest(CAST(:q_states AS text[]), CAST(:q_n AS int[])) AS q(st, n)),
        ranked AS (
            SELECT {_WORK_COLS}, {TIER_RANK_SQL} AS tr,
                   row_number() OVER (PARTITION BY mw.constituency_state ORDER BY {order}) AS rn
            FROM map_work mw WHERE {cond})
        SELECT ranked.* FROM ranked JOIN q ON q.st = ranked.constituency_state AND ranked.rn <= q.n
        ORDER BY ranked.tr, ranked.risk DESC NULLS LAST, ranked.work_key"""),
            p,
        )
        .mappings()
        .all()
    )
    return [_work_json(r) for r in rows], {"total": total, "returned": len(rows), "cap": cap, "sampled": True}


def stratified_quotas(counts: dict[str, int], cap: int) -> dict[str, int]:
    """Quotas by state summing to exactly min(cap, total), never above a
    state's own count. Every state with works gets 1 first (if cap < number
    of states, only the largest states get their 1); the rest of the cap is
    shared in proportion to each state's remaining works by largest
    remainder. (The old engine's min-1-then-trim rule could exceed the cap
    when cap < number of states.)"""
    counts = {k: v for k, v in counts.items() if v > 0}
    total = sum(counts.values())
    if total <= cap:
        return dict(counts)
    by_size = sorted(counts, key=lambda k: (-counts[k], k))
    if cap <= len(counts):
        return {k: 1 for k in by_size[:cap]}
    q = {k: 1 for k in counts}
    rest = {k: counts[k] - 1 for k in counts}
    left = cap - len(counts)
    while left > 0:
        pool = {k: v for k, v in rest.items() if v > 0}
        size = sum(pool.values())
        shares = {k: v / size * left for k, v in pool.items()}
        give = {k: min(int(shares[k]), pool[k]) for k in pool}
        given = sum(give.values())
        for k in sorted(pool, key=lambda k: (-(shares[k] - int(shares[k])), -counts[k], k)):
            if given >= left:
                break
            if give[k] < pool[k]:
                give[k] += 1
                given += 1
        for k, g in give.items():
            q[k] += g
            rest[k] -= g
        left -= given
    return q


def area_works(
    session: Session,
    s: Served,
    area_key: str,
    *,
    page=1,
    page_size=50,
    tier=None,
    stage=None,
    search=None,
    house=None,
    public=False,
) -> dict | None:
    """Paginated list of every work in one area (constituency 'pc:..' or
    district 'dist:..'), including works without a marker."""
    if s.run_id is None:
        return None
    area = (
        session.execute(text("SELECT id, level, name, attrs FROM geo_area WHERE key = :k"), {"k": area_key})
        .mappings()
        .first()
    )
    if area is None or area["level"] not in ("constituency", "district"):
        return None
    col = "mw.constituency_area_id" if area["level"] == "constituency" else "mw.district_area_id"
    where, p = _filters(house, [tier] if tier else None, stage, search, "", None, "mw", public)
    _scoped(s, where, p)
    cond = f"mw.run_id = :run AND {col} = :aid" + "".join(" AND " + w for w in where)
    p |= {"run": s.run_id, "aid": area["id"], "lim": page_size, "off": (page - 1) * page_size}
    total = session.execute(text(f"SELECT count(*) FROM map_work mw WHERE {cond}"), p).scalar_one()
    rows = (
        session.execute(
            text(
                f"SELECT {_WORK_COLS} FROM map_work mw WHERE {cond} "
                f"ORDER BY {TIER_RANK_SQL}, mw.risk DESC NULLS LAST, mw.work_key LIMIT :lim OFFSET :off"
            ),
            p,
        )
        .mappings()
        .all()
    )
    return {
        "area_key": area_key,
        "level": area["level"],
        "name": area["name"],
        "state": (area["attrs"] or {}).get("dataset_state") or (area["attrs"] or {}).get("state"),
        "records": [_work_json(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        **s.meta(),
    }


# ---- map-filters (#11) ------------------------------------------------------------------


def map_filters(session: Session, s: Served, house=None) -> dict:
    empty = {
        "states": [],
        "stages": [],
        "risk_levels": [],
        "priorities": [],
        "total_constituencies": 0,
        "total_works": 0,
    }
    if s.run_id is None:
        return empty | {"served": s.meta()}
    if not s.scope.national:
        return _map_filters_scoped(session, s, house)
    hc = " AND house = :house" if house else ""
    p = {"run": s.run_id, "house": house}
    states = (
        session.execute(
            text(
                "SELECT DISTINCT state FROM geo_metric WHERE run_id = :run AND level = 'constituency' "
                f"AND area_key NOT LIKE 'unlocated:%' AND state IS NOT NULL{hc} ORDER BY 1"
            ),
            p,
        )
        .scalars()
        .all()
    )
    stages = (
        session.execute(
            text(
                "SELECT DISTINCT stage FROM geo_metric "
                f"WHERE run_id = :run AND level = 'national'{hc} ORDER BY 1"
            ),
            p,
        )
        .scalars()
        .all()
    )
    tiers_present = set(
        session.execute(
            text(f"SELECT DISTINCT tier FROM geo_metric WHERE run_id = :run AND level = 'national'{hc}"), p
        ).scalars()
    )
    n_const = session.execute(
        text(
            "SELECT count(DISTINCT area_key) FROM geo_metric WHERE run_id = :run AND level = 'constituency' "
            f"AND area_key NOT LIKE 'unlocated:%'{hc}"
        ),
        p,
    ).scalar_one()
    n_works = session.execute(
        text(f"SELECT coalesce(sum(n), 0) FROM geo_metric WHERE run_id = :run AND level = 'national'{hc}"), p
    ).scalar_one()
    levels = [t for t in TIERS if t in tiers_present]
    return {
        "states": list(states),
        "stages": list(stages),
        "risk_levels": levels,
        "priorities": levels,
        "total_constituencies": int(n_const),
        "total_works": int(n_works),
        "served": s.meta(),
    }


def _map_filters_scoped(session: Session, s: Served, house=None) -> dict:
    where, p = _filters(house, None, None, None, "", None, "mw")
    _scoped(s, where, p)
    cond = "mw.run_id = :run" + "".join(" AND " + w for w in where)
    p["run"] = s.run_id
    r = (
        session.execute(
            text(
                f"""SELECT array_agg(DISTINCT mw.constituency_state)
                          FILTER (WHERE mw.constituency_area_id IS NOT NULL
                          AND mw.constituency_state IS NOT NULL) AS states,
                   array_agg(DISTINCT mw.stage) AS stages, array_agg(DISTINCT mw.tier) AS tiers,
                   count(DISTINCT mw.constituency_area_id) AS n_const, count(*) AS n_works
            FROM map_work mw WHERE {cond}"""
            ),
            p,
        )
        .mappings()
        .one()
    )
    levels = [t for t in TIERS if t in set(r["tiers"] or [])]
    return {
        "states": sorted(r["states"] or []),
        "stages": sorted(r["stages"] or []),
        "risk_levels": levels,
        "priorities": levels,
        "total_constituencies": int(r["n_const"]),
        "total_works": int(r["n_works"]),
        "served": s.meta(),
    }


# ---- geojson (#22), versioned and cached ---------------------------------------------------

_GEOJSON_CACHE: dict[str, bytes] = {}


def geojson_version(session: Session) -> str:
    sig = session.execute(
        text(
            "SELECT count(*), coalesce(max(id), 0), "
            "md5(coalesce(string_agg(key || '|' || coalesce(version, '') || '|' || name, ',' "
            "ORDER BY key COLLATE \"C\"), '')) "
            "FROM geo_area WHERE level = 'constituency'"
        )
    ).one()
    return hashlib.sha256(repr((tuple(sig), boundaries.DISPLAY_REV)).encode()).hexdigest()[:16]


def _seat_state_shapes(session: Session) -> dict:
    """State geo_area key -> display shape, for the seats boundaries.SEAT_AS_STATE
    serves as their state's Admin2 shape. Built with the SHARED_BORDER
    neighbour, exactly as /api/boundary-outlines builds it, so the two meet.
    A missing state row leaves its seat as stored (then no clipped_to_state)."""
    keys = set(boundaries.SEAT_AS_STATE.values()) | set(boundaries.SHARED_BORDER)
    rows = dict(
        session.execute(
            text("SELECT key, geometry FROM geo_area WHERE level = 'state' AND key = ANY(:keys)"),
            {"keys": sorted(keys)},
        ).all()
    )
    return boundaries.display_states(rows)


def geojson_bytes(session: Session) -> tuple[str, bytes]:
    version = geojson_version(session)
    body = _GEOJSON_CACHE.get(version)
    if body is None:
        rows = (
            session.execute(
                text(
                    "SELECT key, name, geometry, attrs, source, licence, version FROM geo_area "
                    "WHERE level = 'constituency' ORDER BY key"
                )
            )
            .mappings()
            .all()
        )
        seat_states = _seat_state_shapes(session)
        feats = []
        for r in rows:
            props = {
                "dataset_state": r["attrs"].get("dataset_state"),
                "dataset_constituency": r["attrs"].get("dataset_constituency"),
                "area_key": r["key"],
                "pc_no": r["attrs"].get("pc_no"),
                "source_pc_name": r["attrs"].get("source_pc_name"),
                "match_method": r["attrs"].get("match_method"),
            }
            geometry = r["geometry"]
            state_key = boundaries.SEAT_AS_STATE.get(props["dataset_state"])
            if state_key in seat_states:
                geometry = seat_states[state_key]
                props["clipped_to_state"] = props["dataset_state"]  # additive (2026-10-03)
            feats.append({"type": "Feature", "geometry": geometry, "properties": props})
        src = rows[0] if rows else {"source": None, "licence": None, "version": None}
        body = json.dumps(
            {
                "type": "FeatureCollection",
                "features": feats,
                "metadata": {
                    "geo_version": version,
                    "source": src["source"],
                    "licence": src["licence"],
                    "boundary_vintage": src["version"],
                },
            },
            separators=(",", ":"),
        ).encode()
        _GEOJSON_CACHE.clear()
        _GEOJSON_CACHE[version] = body
    return version, body


# ---- boundary-outlines (additive, 2026-10-03), versioned and cached ---------------------------
# Display-only shapes built from geo_area rows already loaded (no table is
# written): the national outline, every Admin2 state's border, and each
# re-delimited state (redelimited_states) with a marker point inside it. No
# metric or risk field is ever attached to them. Built once per version (the national row is ~12 MB,
# simplified to ~130 KB; the state rows ~1.4 million points, to ~310 KB).

_OUTLINES_CACHE: dict[str, bytes] = {}
_OUTLINE_ROWS = "(level = 'state' OR (level = 'national' AND key = :national))"


def _outline_params() -> dict:
    return {"national": boundaries.NATIONAL_OUTLINE_KEY}


def redelimited_states(session: Session) -> list[str]:
    """Portal states whose seats the coverage data marks re-delimited: the
    no_boundary rows /api/geographic-coverage lists, never a hard-coded list."""
    return list(
        session.execute(
            text(
                "SELECT DISTINCT portal_state FROM geo_name_crosswalk WHERE level = 'constituency' "
                "AND method = :m AND portal_state IS NOT NULL ORDER BY 1"
            ),
            {"m": REDELIMITED_METHOD},
        ).scalars()
    )


def outlines_version(session: Session) -> str:
    sig = session.execute(
        text(
            "SELECT count(*), coalesce(max(id), 0), "
            "md5(coalesce(string_agg(level || '|' || key || '|' || coalesce(version, ''), ',' "
            f"ORDER BY level, key COLLATE \"C\"), '')) FROM geo_area WHERE {_OUTLINE_ROWS}"
        ),
        _outline_params(),
    ).one()
    states = tuple(redelimited_states(session))
    return hashlib.sha256(repr((tuple(sig), states, boundaries.OUTLINES_REV)).encode()).hexdigest()[:16]


def outlines_bytes(session: Session) -> tuple[str, bytes]:
    version = outlines_version(session)
    body = _OUTLINES_CACHE.get(version)
    if body is None:
        rows = {
            (r["level"], r["key"]): r
            for r in session.execute(
                text(
                    "SELECT level, key, name, geometry, source, licence, version FROM geo_area "
                    f"WHERE {_OUTLINE_ROWS}"
                ),
                _outline_params(),
            ).mappings()
        }
        feats = []
        # Area shapes: the re-delimited states, built with the SHARED_BORDER pair
        # exactly as /api/geojson builds the Ladakh seat, so the two meet.
        redelimited = {st: boundaries.state_key(st) for st in redelimited_states(session)}
        drawn = set(redelimited.values()) | set(boundaries.SHARED_BORDER)
        state_shapes = boundaries.display_states(
            {key: r["geometry"] for (level, key), r in rows.items() if level == "state" and key in drawn}
        )
        nat = rows.get(("national", boundaries.NATIONAL_OUTLINE_KEY))
        if nat is not None:
            feats.append(
                {
                    "type": "Feature",
                    "geometry": boundaries.simplified(nat["geometry"], boundaries.NATIONAL_SIMPLIFY_DEG),
                    "properties": {
                        "kind": "national_outline",
                        "name": "India",
                        "source": nat["source"],
                        "licence": nat["licence"],
                        "version": nat["version"],
                    },
                }
            )
        for state, key in sorted(redelimited.items()):
            r = rows.get(("state", key))
            if r is None:
                continue
            lat, lon = boundaries.inner_point(state_shapes[key])
            feats.append(
                {
                    "type": "Feature",
                    "geometry": state_shapes[key],
                    "properties": {
                        "kind": "redelimited_state",
                        "state": state,
                        "match_method": REDELIMITED_METHOD,
                        "reason": boundaries.REDELIMITED.get(state),
                        # computed from the shape above (inside it), not a geocode
                        "marker_lat": lat,
                        "marker_lon": lon,
                        "source": r["source"],
                        "licence": r["licence"],
                        "version": r["version"],
                    },
                }
            )
        for (level, key), r in sorted(rows.items()):
            if level != "state":
                continue
            border = boundaries.simplified(r["geometry"], boundaries.STATE_BORDER_SIMPLIFY_DEG)
            label_lat, label_lon = boundaries.label_point(border)
            feats.append(
                {
                    "type": "Feature",
                    "geometry": border,
                    "properties": {
                        "kind": "state_border",
                        "key": key,
                        "name": r["name"],  # the Admin2 file's English name, used as the map label
                        "label_lat": label_lat,
                        "label_lon": label_lon,
                        "source": r["source"],
                        "licence": r["licence"],
                        "version": r["version"],
                    },
                }
            )
        body = json.dumps(
            {"type": "FeatureCollection", "features": feats, "metadata": {"outlines_version": version}},
            separators=(",", ":"),
        ).encode()
        _OUTLINES_CACHE.clear()
        _OUTLINES_CACHE[version] = body
    return version, body


# ---- geographic-coverage (#23) --------------------------------------------------------------


def geographic_coverage(session: Session, s: Served) -> dict:
    methods = dict(
        session.execute(
            text("SELECT method, count(*) FROM geo_name_crosswalk WHERE level = 'constituency' GROUP BY 1")
        ).all()
    )
    total = sum(methods.values())
    real = session.execute(
        text(
            "SELECT count(*) FROM geo_name_crosswalk WHERE level = 'constituency' AND geo_area_id IS NOT NULL"
        )
    ).scalar_one()
    unmatched = session.execute(
        text(
            "SELECT portal_name, method FROM geo_name_crosswalk WHERE level = 'constituency' "
            "AND geo_area_id IS NULL ORDER BY 1"
        )
    ).all()
    sources = session.execute(
        text(
            "SELECT level, source, licence, version, count(*) FROM geo_area "
            "WHERE level IN ('constituency', 'district') GROUP BY 1, 2, 3, 4 ORDER BY 1"
        )
    ).all()
    pct = round(real / total * 100, 1) if total else 0
    c = s.counts
    return {
        "total_constituencies": total,
        "real_boundary_count": real,
        "real_boundary_pct": pct,
        "centroid_fallback_count": 0,
        "centroid_fallback_pct": 0,
        "total_coverage_pct": pct,
        # additive (Phase 9)
        "match_methods": methods,
        "redelimited_count": methods.get("redelimited_boundary_not_available", 0),
        "no_boundary": [
            {"name": n.split("|", 1)[-1], "state": n.split("|", 1)[0], "reason": m} for n, m in unmatched
        ],
        "sources": [
            {"level": lv, "source": so, "licence": li, "version": ve, "areas": n}
            for lv, so, li, ve, n in sources
        ],
        # national work counts: withheld from a scoped caller (boundary facts above are public geography)
        "works_total": c.get("works") if s.scope.national else None,
        "works_with_marker": c.get("with_marker") if s.scope.national else None,
        "works_district_unlocated": c.get("district_unlocated") if s.scope.national else None,
        "location_precision": LOCATION_PRECISION,
        "note": PRECISION_NOTE,
        "served": s.meta(),
    }


# ---- constituency-intelligence (#24) ----------------------------------------------------------


def _empty_intel(state, constituency) -> dict:
    return {
        "state": state,
        "constituency": constituency,
        "total_works": 0,
        "total_amount": 0,
        "critical_count": 0,
        "high_count": 0,
        "moderate_count": 0,
        "low_count": 0,
        "flagged_count": 0,
        "priority_rate": 0,
        "average_risk": 0,
        "average_confidence": 0,
        "financial_exposure": 0,
        "stage_distribution": {},
        "category_distribution": {},
        "signal_summary": [],
        "confidence_distribution": {},
        "top_priority_works": [],
        "mps": [],
    }


def constituency_intelligence(session: Session, s: Served, state: str, constituency: str) -> dict:
    base = _empty_intel(state, constituency)
    # Rajya Sabha rows carry the portal placeholder RS_CONSTITUENCY instead of a
    # constituency (members represent a state), so that is how an RS context
    # arrives here; the endpoint takes no house parameter (contract #24).
    if constituency.strip().casefold() == RS_CONSTITUENCY.casefold():
        return base | {
            "found": True,
            "applicable": False,
            "house": "RS",
            "not_applicable_reason": RS_NOT_APPLICABLE,
            "served": s.meta(),
        }
    found = session.execute(
        text(
            "SELECT 1 FROM constituency c JOIN state st ON st.id = c.state_id "
            "WHERE st.name = :s AND c.name = :c AND c.house = 'LS'"
        ),
        {"s": state, "c": constituency},
    ).first()
    if not found:
        return base | {"found": False, "applicable": None, "served": s.meta()}
    area = session.execute(
        text(
            "SELECT key FROM geo_area WHERE level = 'constituency' AND attrs->>'dataset_state' = :s "
            "AND attrs->>'dataset_constituency' = :c"
        ),
        {"s": state, "c": constituency},
    ).scalar()
    meta = {
        "found": True,
        "applicable": True,
        "house": "LS",
        "area_key": area,
        "boundary_status": "available" if area else "not_available",
        "served": s.meta(),
        "min_works_for_rate": MIN_WORKS_FOR_RATE,
        "location_precision": LOCATION_PRECISION,
        "confidence_distribution_note": CONFIDENCE_BANDS_NOTE,
    }
    if s.run_id is None:
        return base | meta
    sw, sp = [], {}
    _scoped(s, sw, sp)
    rows = (
        session.execute(
            text(
                f"SELECT {_WORK_COLS} FROM map_work mw WHERE mw.run_id = :run AND mw.constituency_state = :s "
                "AND mw.constituency = :c AND mw.house = 'LS'" + "".join(" AND " + w for w in sw)
            ),
            {"run": s.run_id, "s": state, "c": constituency, **sp},
        )
        .mappings()
        .all()
    )
    n = len(rows)
    tiers = Counter(r["tier"] for r in rows)
    flagged = tiers["CRITICAL"] + tiers["HIGH"]
    risks = [r["risk"] for r in rows if r["risk"] is not None]
    small = n < MIN_WORKS_FOR_RATE
    top = sorted(rows, key=lambda r: (TIERS.index(r["tier"]), -(r["risk"] or -1), r["work_key"]))[:10]
    stage_dist: dict = defaultdict(int)
    cat_dist: dict = defaultdict(int)
    for r in rows:
        stage_dist[r["stage"]] += 1
        cat_dist[r["category"] or "Unspecified"] += 1
    return (
        base
        | meta
        | {
            "total_works": n,
            "total_amount": round(sum(r["amount"] or 0 for r in rows), 2),
            "critical_count": tiers["CRITICAL"],
            "high_count": tiers["HIGH"],
            "moderate_count": tiers["MODERATE"],
            "low_count": tiers["LOW"],
            "flagged_count": flagged,
            "priority_rate": None if small or not n else round(flagged / n * 100, 1),
            "average_risk": None if small or not risks else round(sum(risks) / len(risks) * 100, 1),
            "average_confidence": None
            if small or not n
            else round(sum(r["confidence"] for r in rows) / n * 100, 1),
            "financial_exposure": round(sum(r["amount"] or 0 for r in rows if r["tier"] in FLAGGED), 2),
            "stage_distribution": dict(stage_dist),
            "category_distribution": dict(cat_dist),
            "confidence_distribution": dict(Counter(confidence_label(r["confidence"]) for r in rows)),
            # precomputed over the whole area: withheld from a scoped caller
            "signal_summary": s.counts.get("constituency", {}).get("signal_summary", {}).get(area, [])
            if area and s.scope.national
            else [],
            "signal_high_threshold": SIGNAL_HIGH,
            "top_priority_works": [
                {
                    "record_id": r["work_key"],
                    "description": (mask_personal(r["description"]) or "")[:120],
                    "amount": r["amount"],
                    "risk_level": r["tier"],
                    "risk_score": r["risk"],
                    "mp": r["mp"],
                    "stage": r["stage"],
                }
                for r in top
            ],
            "mps": sorted({r["mp"] for r in rows if r["mp"]}),
            "insufficient_data": small,
            "not_evaluated": tiers["NOT_EVALUATED"],
        }
    )

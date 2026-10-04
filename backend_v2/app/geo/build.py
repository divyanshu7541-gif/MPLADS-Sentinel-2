"""
Phase 9 map build: per published run, denormalise the scored works into
map_work and pre-aggregate geo_metric. Reads risk_result (the published
default config) and signal_result; never writes either.

Grain of geo_metric: (level, area, House, tier, stage) holding SUMS, so every
map filter combination is a sum over a few thousand rows and every parent is
an exact sum of its children. Each scored work is counted exactly once at
every level -- works that cannot be placed go into an explicit
"unlocated:<reason>" bucket, never dropped and never guessed:

  national      one area, "IN"
  state         where the work IS (authority's corrected state); else
                unlocated:state
  district      the authority's LGD district; else unlocated:<state|none>
  constituency  Lok Sabha: the constituency polygon; else
                unlocated:<constituency_status>. Rajya Sabha:
                unlocated:not_applicable_rajya_sabha (members have no
                constituency).

Markers: a work gets latitude/longitude only when its constituency has a
polygon, and then it is that polygon's single representative point, shared
by every work in the constituency. No jitter, no geocode, no offset.
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..analytics import fusion
from ..analytics.atypicality_run import risk_result_checksum
from ..analytics.publish import published
from ..ingest.db_utils import bulk_insert, to_records_with_nulls
from ..models.geo import GeoMetric, MapBuild, MapWork
from ..serving.labels import STAGES
from ..serving.redact import mask_personal

# Phase 12: upper case, the vocabulary the frontend translates and styles
# (labels.js STAGE, index.css .stage-badge.COMPLETED) and every other endpoint serves.
STAGE = STAGES
# Old engine's per-signal "high" cut for the intelligence panel's
# signal_summary (backend/app/core/engine.py, score > 0.7). Carried over for
# contract meaning, not re-tuned.
SIGNAL_HIGH = 0.7
SIGNAL_LABEL = {
    "cost_anomaly": "Cost Anomaly",
    "near_duplicate": "Description Similarity",
    "portfolio_concentration": "MP Concentration",
    "district_authority_pattern": "Constituency Pattern",
    "temporal_anomaly": "Temporal Anomaly",
    "lifecycle_delay": "Stage Consistency",
}
DESCRIPTION_CHARS = 120

_WORKS_SQL = text(
    """
    SELECT r.work_key, r.house, r.tier, r.risk, r.confidence, r.base_signal_count,
           ws.lifecycle_status, ws.sanction_amount, ws.sanction_date,
           w.raw_mp_name AS mp, w.raw_description AS description, at.label AS category,
           wg.constituency_status, wg.constituency_area_id, wg.district_area_id,
           c.name AS constituency, cs.name AS constituency_state,
           ls.name AS location_state, pc.key AS pc_key, pc.rep_lat, pc.rep_lon,
           d.key AS district_key, d.name AS district_name
    FROM risk_result r
    JOIN work w ON w.work_key = r.work_key
    JOIN analysis_run ar ON ar.id = r.run_id
    JOIN work_state ws ON ws.work_key = r.work_key AND ws.source_snapshot_id = ar.source_snapshot_id
    LEFT JOIN activity_type at ON at.id = w.activity_type_id
    LEFT JOIN work_geo wg ON wg.work_key = r.work_key
    LEFT JOIN constituency c ON c.id = wg.constituency_id
    LEFT JOIN state cs ON cs.id = c.state_id
    LEFT JOIN state ls ON ls.id = wg.location_state_id
    LEFT JOIN geo_area pc ON pc.id = wg.constituency_area_id
    LEFT JOIN geo_area d ON d.id = wg.district_area_id
    WHERE r.run_id = :run AND r.config_name = :cfg
    """
)


def _search_text(df: pd.DataFrame) -> pd.Series:
    cols = ["work_key", "mp", "description", "constituency", "location_state", "district_name", "category"]
    return df[cols].fillna("").astype(str).agg(" ".join, axis=1).str.lower()


def map_frame(session: Session, run_id: int, config_name: str) -> pd.DataFrame:
    df = pd.read_sql(_WORKS_SQL, session.connection(), params={"run": run_id, "cfg": config_name})
    if df["constituency_status"].isna().any():
        raise ValueError("work_geo missing for some scored works; run app.geo.location.link_works first")
    df["stage"] = df["lifecycle_status"].map(STAGE)
    # Public output layer: phone numbers masked in stored + searched text (app/serving/redact.py).
    df["description"] = df["description"].map(mask_personal)
    df["risk01"] = df["risk"] / 100.0
    df["amount"] = pd.to_numeric(df["sanction_amount"], errors="coerce")
    # Hierarchy keys (see module docstring). Every work lands in exactly one area per level.
    df["state_key"] = df["location_state"].fillna("unlocated:state")
    df["district_key"] = df["district_key"].where(
        df["district_key"].notna(), "unlocated:" + df["location_state"].fillna("none")
    )
    df["district_label"] = df["district_name"].where(df["district_name"].notna(), "Unlocated district")
    df["pc_key"] = df["pc_key"].where(df["pc_key"].notna(), "unlocated:" + df["constituency_status"])
    return df


def _metrics(df: pd.DataFrame, run_id: int) -> list[dict]:
    levels = {
        "national": (
            pd.Series("IN", index=df.index),
            pd.Series("India", index=df.index),
            pd.Series(None, index=df.index, dtype=object),
        ),
        "state": (df["state_key"], df["state_key"], df["location_state"]),
        "district": (df["district_key"], df["district_label"], df["location_state"]),
        "constituency": (df["pc_key"], df["constituency"].fillna(df["pc_key"]), df["constituency_state"]),
    }
    rows = []
    for level, (key, name, state) in levels.items():
        g = df.assign(
            _k=key,
            _name=name,
            _state=state,
            _amt=df["amount"].fillna(0.0),
            _risk=df["risk01"].fillna(0.0),
            _rn=df["risk01"].notna().astype(int),
        )
        agg = (
            g.groupby(["_k", "house", "tier", "stage"], dropna=False)
            .agg(
                area_name=("_name", "first"),
                state=("_state", "first"),
                n=("work_key", "size"),
                amount_sum=("_amt", "sum"),
                risk_sum=("_risk", "sum"),
                risk_n=("_rn", "sum"),
                confidence_sum=("confidence", "sum"),
                signals_sum=("base_signal_count", "sum"),
            )
            .reset_index()
            .rename(columns={"_k": "area_key"})
        )
        agg["level"] = level
        agg["run_id"] = run_id
        rows.extend(to_records_with_nulls(agg))
    return rows


def _constituency_extras(session: Session, df: pd.DataFrame, run_id: int) -> dict:
    """Per-constituency-polygon fields the intelligence panel needs that are
    not sums: MPs, and signal_summary (score > SIGNAL_HIGH among ELIGIBLE
    works only -- not-evaluated is excluded, never counted as zero)."""
    located = df[~df["pc_key"].str.startswith("unlocated:")]
    mps = located.dropna(subset=["mp"]).groupby("pc_key")["mp"].agg(lambda s: sorted(set(s))).to_dict()
    sig = pd.read_sql(
        text("SELECT work_key, signal, score FROM signal_result WHERE run_id = :run AND eligible"),
        session.connection(),
        params={"run": run_id},
    )
    sig = sig.merge(located[["work_key", "pc_key"]], on="work_key")
    summary: dict[str, list] = defaultdict(list)
    for (pc, s), g in sig.groupby(["pc_key", "signal"]):
        high = int((g["score"] > SIGNAL_HIGH).sum())
        if high:
            summary[pc].append(
                {
                    "signal": SIGNAL_LABEL.get(s, s),
                    "high_count": high,
                    "avg_score": round(float(g["score"].mean()) * 100, 1),
                    "eligible_works": int(len(g)),
                }
            )
    for v in summary.values():
        v.sort(key=lambda x: -x["high_count"])
    return {"mps": mps, "signal_summary": dict(summary)}


def build_map(session: Session, run_id: int | None = None) -> dict:
    """Build map tables for `run_id` (default: the published run) with the
    published default config. Idempotent per run: rebuilds replace."""
    pub = published(session)
    if pub is None:
        raise ValueError("no published_run; run scripts/publish_run.py first")
    run_id = pub.run_id if run_id is None else run_id
    config = pub.default_config_name if run_id == pub.run_id else fusion.DEFAULT_CONFIG
    as_of = session.execute(
        text(
            "SELECT ss.data_as_of FROM analysis_run ar "
            "JOIN source_snapshot ss ON ss.id = ar.source_snapshot_id WHERE ar.id = :r"
        ),
        {"r": run_id},
    ).scalar()

    md5_before = risk_result_checksum(session, run_id)
    for model in (GeoMetric, MapWork):
        session.execute(delete(model).where(model.run_id == run_id))
    session.execute(delete(MapBuild).where(MapBuild.run_id == run_id))
    df = map_frame(session, run_id, config)

    mw = pd.DataFrame(
        {
            "run_id": run_id,
            "work_key": df["work_key"],
            "house": df["house"],
            "state": df["location_state"],
            "constituency": df["constituency"],
            "constituency_state": df["constituency_state"],
            "constituency_area_id": df["constituency_area_id"],
            "district_area_id": df["district_area_id"],
            "district_name": df["district_name"],
            "latitude": df["rep_lat"],
            "longitude": df["rep_lon"],
            "tier": df["tier"],
            "risk": df["risk01"],
            "confidence": df["confidence"],
            "active_signals": df["base_signal_count"],
            "amount": df["amount"],
            "stage": df["stage"],
            "category": df["category"],
            "mp": df["mp"],
            "description": df["description"].str.slice(0, 2000),
            "record_date": df["sanction_date"],
            "search_text": _search_text(df),
        }
    )
    for c in ("constituency_area_id", "district_area_id"):
        mw[c] = mw[c].astype("Int64")
    bulk_insert(session, MapWork, to_records_with_nulls(mw))
    metrics = _metrics(df, run_id)
    bulk_insert(session, GeoMetric, metrics)

    placed = df["rep_lat"].notna()
    counts = {
        "works": int(len(df)),
        "with_marker": int(placed.sum()),
        "by_constituency_status": df["constituency_status"].value_counts().to_dict(),
        "state_unlocated": int(df["location_state"].isna().sum()),
        "district_unlocated": int(df["district_area_id"].isna().sum()),
        "geo_metric_rows": len(metrics),
        "risk_result_md5_before": md5_before,
        "risk_result_md5_after": risk_result_checksum(session, run_id),
        "constituency": _constituency_extras(session, df, run_id),
    }
    session.add(
        MapBuild(run_id=run_id, status="complete", config_name=config, data_as_of=as_of, counts=counts)
    )
    session.flush()
    return {k: v for k, v in counts.items() if k != "constituency"} | {"run_id": run_id, "config": config}


def served_build(session: Session) -> tuple[MapBuild | None, bool]:
    """(build to serve, is_latest). Serves the published run's build when
    complete; otherwise the most recent complete build (stale-data rule),
    with is_latest False so the UI can label it with its date."""
    pub = published(session)
    if pub is not None:
        b = session.get(MapBuild, pub.run_id)
        if b is not None and b.status == "complete":
            return b, True
    b = session.execute(
        select(MapBuild).where(MapBuild.status == "complete").order_by(MapBuild.run_id.desc()).limit(1)
    ).scalar_one_or_none()
    return b, False

"""
Phase 6/8 orchestration for the B4 atypicality evidence layer: load one
run's scored population, build features, fit + score both methods, register
each model in model_version, write atypicality_result. risk_result is only
READ (checksummed before and after); a changed checksum aborts the run.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ingest.db_utils import bulk_insert, to_records_with_nulls
from ..models.analytics import AnalysisRun, AtypicalityResult, ModelVersion, WorkEvidenceFact
from ..models.provenance import SourceSnapshot
from . import atypicality, signals

_RAW_SQL = text(
    """
    WITH per_payee AS (
        SELECT work_key, payee_id, COUNT(*) AS c
        FROM payment WHERE source_snapshot_id = :snap GROUP BY work_key, payee_id
    ), pay AS (
        SELECT work_key, SUM(c) AS n_payments, COUNT(*) AS n_payees, MAX(c) AS max_to_one_payee
        FROM per_payee GROUP BY work_key
    )
    SELECT wc.work_key, wc.house, wc.amount_used, wc.peer_median,
           ws.recommended_date, ws.sanction_date, ws.actual_end_date, ws.lifecycle_status,
           w.description_normalized AS description,
           COALESCE(pay.n_payments, 0) AS n_payments, COALESCE(pay.n_payees, 0) AS n_payees,
           COALESCE(pay.max_to_one_payee, 0) AS max_to_one_payee
    FROM work_context wc
    JOIN work w ON w.work_key = wc.work_key
    JOIN work_state ws ON ws.work_key = wc.work_key AND ws.source_snapshot_id = :snap
    LEFT JOIN pay ON pay.work_key = wc.work_key
    WHERE wc.run_id = :run
    """
)


def risk_result_checksum(session: Session, run_id: int) -> str:
    """md5 over every risk_result row of the run, in key order.

    The order is byte order (COLLATE "C"), not the database's default
    collation: with the default, the same rows gave a different md5 on a
    glibc-based PostgreSQL (e.g. the Debian `postgres:16` image) than on the
    musl-based `postgres:16-alpine` used so far, because en_US.utf8 sorts
    keys like '1301-RS' / '1302' differently there. Found by the Phase 13
    restore test. Byte order reproduces the reference value on alpine
    (97f08303369f9ed6c50e46d68a4609f5 for run 1; c4d589e0e0fc40621d4e011a64a7233d for
    run 44, published after the Phase 13.y authority-state fix) and is the same on every server."""
    return session.execute(
        text(
            """
        SELECT md5(string_agg(concat_ws('|', work_key, config_name, config_hash, risk::text, tier,
               base_signal_count, n_eligible, corroboration_factor, pattern_score::text,
               pre_multiplier::text, confidence::text, confidence_components::text), E'\\n'
               ORDER BY work_key COLLATE "C", config_name COLLATE "C"))
        FROM risk_result WHERE run_id = :r
        """
        ),
        {"r": run_id},
    ).scalar_one()


def load_raw(session: Session, run_id: int) -> tuple[pd.DataFrame, pd.Timestamp, int]:
    run_row = session.get(AnalysisRun, run_id)
    snap = run_row.source_snapshot_id
    as_of = pd.Timestamp(session.get(SourceSnapshot, snap).data_as_of)
    raw = pd.read_sql(_RAW_SQL, session.connection(), params={"run": run_id, "snap": snap})
    return raw.set_index("work_key").sort_index(), as_of, snap


def repeat_payee_facts(raw: pd.DataFrame, run_id: int) -> pd.DataFrame:
    """Fact rows for every scored work that pays the same payee more than once
    (more payment rows than distinct payees). A fact, not a score."""
    hit = raw[pd.to_numeric(raw["n_payments"]) > pd.to_numeric(raw["n_payees"])]
    return pd.DataFrame(
        {
            "run_id": run_id,
            "work_key": hit.index,
            "house": hit["house"].to_numpy(),
            "fact": "pays_same_payee_more_than_once",
            "detail": [
                {"n_payments": int(a), "n_distinct_payees": int(b), "max_payments_to_one_payee": int(c)}
                for a, b, c in zip(hit["n_payments"], hit["n_payees"], hit["max_to_one_payee"])
            ],
        }
    )


def clear(session: Session, run_id: int) -> None:
    """Remove this layer's rows for one run (atypicality_result, work_evidence_fact,
    model_version) so it can be re-run. Touches nothing else."""
    for table in ("atypicality_result", "work_evidence_fact", "model_version"):
        session.execute(text(f"DELETE FROM {table} WHERE run_id = :r"), {"r": run_id})
    session.flush()


def run(session: Session, run_id: int, replace: bool = False) -> dict:
    if replace:
        clear(session, run_id)
    before = risk_result_checksum(session, run_id)
    raw, as_of, snap = load_raw(session, run_id)
    X = atypicality.build_features(raw, as_of)
    fitted = atypicality.score(X)
    facts = repeat_payee_facts(raw, run_id)
    bulk_insert(session, WorkEvidenceFact, to_records_with_nulls(facts))

    for method, res in fitted.items():
        mv = ModelVersion(
            run_id=run_id,
            model_name=f"b4_atypicality_{method}",
            algorithm=res["algorithm"],
            feature_spec_hash=atypicality.feature_spec_hash(),
            training_snapshot_id=snap,
            seed=atypicality.SEED,
            params=signals.json_safe(res["params"]),
            metrics=signals.json_safe(
                {**res["metrics"], "as_of": str(as_of.date()), "risk_result_md5_before": before}
            ),
            artifact_hash=res["artifact_hash"],
        )
        session.add(mv)
        session.flush()
        f = res["frame"]
        df = pd.DataFrame(
            {
                "run_id": run_id,
                "work_key": f.index,
                "house": raw["house"].reindex(f.index).to_numpy(),
                "method": method,
                "model_version_id": mv.id,
                "eligible": f["eligible"].to_numpy(),
                "score": f["score"].to_numpy(),
                "percentile": f["percentile"].to_numpy(),
            }
        )
        df["contributions"] = [signals.json_safe(c) for c in f["contributions"]]
        df["missing_features"] = list(f["missing_features"])
        bulk_insert(session, AtypicalityResult, to_records_with_nulls(df))
        res["model_version_id"] = mv.id
    session.flush()

    after = risk_result_checksum(session, run_id)
    if after != before:
        raise RuntimeError(f"risk_result for run {run_id} changed during the atypicality step")
    for res in fitted.values():
        mv = session.get(ModelVersion, res["model_version_id"])
        mv.metrics = {**mv.metrics, "risk_result_md5_after": after}
    session.flush()
    return {"X": X, "raw": raw, "fitted": fitted, "as_of": as_of, "risk_md5": before, "facts": facts}

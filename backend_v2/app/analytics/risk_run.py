"""
Phase 5 orchestration: signals (Phase 4 run, unchanged) -> fusion under
BOTH configurations -> confidence -> compliance panel, all written onto
the same analysis_run. risk_result rows are keyed (run, work, config) so
the two configurations never overwrite each other.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from ..ingest.db_utils import bulk_insert, to_records_with_nulls
from ..models.analytics import AnalysisRun, ComplianceResult, RiskResult
from ..models.provenance import SourceSnapshot
from . import compliance, confidence, fusion, signals, signals_run


def _stat_from_evidence(signal: str, ev) -> float:
    """The extremeness statistic each empirically calibrated signal ranks
    (signals.empirical_tail_score): |z| for portfolio, the larger of |z_share|
    and |z_amount| for district, the one-sided burst z for temporal,
    |z_log_scale| for cost, the stored combined component score for
    lifecycle."""
    if not isinstance(ev, dict) or not ev:
        return float("nan")
    if signal == "cost_anomaly":
        z = ev.get("z_log_scale")
        return float("nan") if z is None else abs(z)
    if signal == "lifecycle_delay":
        v = ev.get("rank_statistic")
        return float("nan") if v is None else float(v)
    if signal == "district_authority_pattern":
        vals = [abs(v) for v in (ev.get("z_share"), ev.get("z_amount")) if v is not None]
        return max(vals) if vals else float("nan")
    z = ev.get("z")
    return float("nan") if z is None else (abs(z) if signal == "portfolio_concentration" else float(z))


def rank_statistic(signal: str, evidence: pd.Series) -> pd.Series:
    """The exact key each calibrated signal's score is the empirical rank of,
    rebuilt from stored evidence (population-level for lifecycle, whose key
    orders works tied at the cap by days open)."""
    stat = pd.Series([_stat_from_evidence(signal, e) for e in evidence], index=evidence.index)
    if signal == "lifecycle_delay":
        age = pd.Series(
            [e.get("age_days") if isinstance(e, dict) else None for e in evidence],
            index=evidence.index,
            dtype="float",
        )
        return signals.lifecycle_rank_key(stat, age)
    return stat


def calibration_stats(evidence: dict[str, pd.Series]) -> pd.DataFrame:
    """work x calibrated-signal raw statistic, for the gate report's
    calibration section (old normal mapping vs new empirical mapping)."""
    return pd.DataFrame(
        {
            s: pd.Series([_stat_from_evidence(s, e) for e in evidence[s]], index=evidence[s].index)
            for s in signals.EMPIRICAL_SIGNALS
        }
    )


def data_quality_flags(frame: pd.DataFrame, comp: pd.DataFrame) -> pd.DataFrame:
    desc = frame["description_normalized"].fillna("").astype(str).str.strip()
    return pd.DataFrame(
        {
            "missing_description": desc == "",
            "referential_gap": frame.index.isin(compliance.referential_gap_work_keys(comp)),
            "flag_inconsistent": frame.index.isin(compliance.flag_inconsistent_work_keys(comp)),
            "amount_unusable": ~frame["is_usable"].fillna(False).astype(bool),
            "cross_house_key_collision": frame.index.isin(compliance.key_collision_work_keys(comp)),
        },
        index=frame.index,
    )


def compute_risk(
    results: dict[str, pd.DataFrame], ctx: pd.DataFrame, dq: pd.DataFrame, index: pd.Index
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    scores = fusion.score_matrix(results, index)
    fused = {c: fusion.fuse(scores, c) for c in fusion.CONFIGS}
    conf = confidence.compute_confidence(results, ctx, dq, index)
    return scores, fused, conf


def _risk_records(run_id: int, frame: pd.DataFrame, fused: dict, conf: pd.DataFrame) -> list[dict]:
    comp_json = confidence.components_json(conf)
    records: list[dict] = []
    for name in fusion.CONFIGS:
        f = fused[name]
        df = pd.DataFrame(
            {
                "run_id": run_id,
                "work_key": f.index,
                "house": frame["house"].reindex(f.index).to_numpy(),
                "config_name": name,
                "config_hash": fusion.config_hash(name),
                "risk": f["risk"].to_numpy(),
                "tier": f["tier"].to_numpy(),
                "critical_demoted": f["critical_demoted"].to_numpy(),
                "base_signal_count": f["k"].to_numpy(),
                "n_eligible": f["n_eligible"].to_numpy(),
                "corroboration_factor": f["m"].to_numpy(),
                "pattern_score": f["pattern_score"].to_numpy(),
                "pre_multiplier": f["pre_multiplier"].to_numpy(),
                "confidence": conf["confidence"].reindex(f.index).to_numpy(),
            }
        )
        df["confidence_components"] = comp_json
        records.extend(to_records_with_nulls(df))
    return records


def _compliance_records(run_id: int, comp: pd.DataFrame) -> list[dict]:
    df = comp.copy()
    df["run_id"] = run_id
    df["detail"] = [signals.json_safe(d) for d in df["detail"]]
    return to_records_with_nulls(df)


def run(session: Session):
    run_row, frame, ctx, results = signals_run.run(session)
    snap_a = run_row.source_snapshot_id
    snap_b = session.execute(select(SourceSnapshot.id).where(SourceSnapshot.code == "snapshot_b")).scalar()
    as_of = signals_run._resolve_as_of(session, snap_a)

    comp = compliance.compute_compliance(session, snap_a, snap_b, as_of)
    dq = data_quality_flags(frame, comp)
    scores, fused, conf = compute_risk(results, ctx, dq, frame.index)

    bulk_insert(session, RiskResult, _risk_records(run_row.id, frame, fused, conf))
    bulk_insert(session, ComplianceResult, _compliance_records(run_row.id, comp))

    run_row.notes = (
        (run_row.notes or "")
        + f"\nphase5_fusion_config_hash={fusion.fusion_config_hash()}"
        + f"\nphase5_signal_calibration={signals.SIGNAL_CALIBRATION}"
        + f"\nphase5_risk_output_hash={fusion.risk_output_hash(fused, conf['confidence'])}"
        + f"\nphase5_risk_rows={sum(len(f) for f in fused.values())}"
        + f"\nphase5_compliance_rows={len(comp)}"
    )
    session.flush()
    return {
        "run_row": run_row,
        "frame": frame,
        "ctx": ctx,
        "results": results,
        "scores": scores,
        "fused": fused,
        "conf": conf,
        "comp": comp,
        "dq": dq,
        "as_of": as_of,
        "calib": calibration_stats({s: results[s]["evidence"] for s in signals.EMPIRICAL_SIGNALS}),
    }


_SIG_SQL = text(
    "SELECT work_key, signal, eligible, score, reliability, dispersion FROM signal_result WHERE run_id = :run"
)
_FRAME_SQL = text(
    """
    SELECT wc.work_key, wc.house, wc.level, wc.amount_used, w.description_normalized
    FROM work_context wc JOIN work w ON w.work_key = wc.work_key
    WHERE wc.run_id = :run
    """
)
_COMP_SQL = text(
    "SELECT check_code, rule, subject_type, subject_key, work_key, house, passed, detail"
    " FROM compliance_result WHERE run_id = :run"
)


def load_run(session: Session, run_id: int) -> dict:
    """Rebuild a stored run's inputs from the database alone (signal_result,
    work_context, compliance_result) and recompute fusion + confidence --
    used to re-render the gate report and to prove the stored risk output
    hash reproduces from stored inputs."""
    conn = session.connection()
    sig = pd.read_sql(_SIG_SQL, conn, params={"run": run_id})
    base = pd.read_sql(_FRAME_SQL, conn, params={"run": run_id}).set_index("work_key").sort_index()
    base["amount_used"] = pd.to_numeric(base["amount_used"])
    in_scope = sig["work_key"].unique()
    frame = base.loc[base.index.isin(in_scope)].copy()
    frame["is_usable"] = frame["amount_used"].notna() & (frame["amount_used"] > 0)
    results = {
        name: g.set_index("work_key")[["eligible", "score", "reliability", "dispersion"]]
        for name, g in sig.groupby("signal")
    }
    comp = pd.read_sql(_COMP_SQL, conn, params={"run": run_id})
    dq = data_quality_flags(frame, comp)
    scores, fused, conf = compute_risk(results, frame[["level"]], dq, frame.index)
    ev = pd.read_sql(
        text(
            "SELECT work_key, signal, evidence FROM signal_result WHERE run_id = :run AND eligible"
            " AND signal IN :sigs"
        ).bindparams(bindparam("sigs", expanding=True)),
        conn,
        params={"run": run_id, "sigs": list(signals.EMPIRICAL_SIGNALS)},
    )
    calib = calibration_stats(
        {s: ev[ev["signal"] == s].set_index("work_key")["evidence"] for s in signals.EMPIRICAL_SIGNALS}
    )
    return {
        "frame": frame,
        "results": results,
        "comp": comp,
        "dq": dq,
        "scores": scores,
        "fused": fused,
        "conf": conf,
        "calib": calib,
    }


def note_value(run_row: AnalysisRun, key: str) -> str | None:
    for line in (run_row.notes or "").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None

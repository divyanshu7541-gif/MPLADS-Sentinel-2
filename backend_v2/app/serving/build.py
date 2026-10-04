"""
Phase 12: build served_work (app/models/serving.py) for one run -- the read
model every cutover endpoint queries. Only READS risk_result, signal_result
and work_context (risk_result is checksummed before and after; a changed
checksum aborts before anything is committed, like Phase 6/10).

Population: every work with a work_state row in the run's snapshot
(Snapshot A: 122,965 works). The published run's risk_result population
(97,506, both Houses) is `scored`; the 25,459 recommended-only works are
unscored and carry no risk, tier or confidence at all (NULL, never 0).

Field choices (docs/phase12_report.md):
  state         where the work IS -- Phase 9's corrected location
                (work_geo.location_state_id), the same geography the map uses.
  constituency  Lok Sabha works only (Phase 9 work_geo.constituency_id);
                Rajya Sabha members have no constituency, so NULL.
  amount        sanction amount for scored works -- the same basis the map
                uses (Phase 9 judgement call #6); recommended amount for
                unscored (recommended-only) works, which have no sanction.
  peer context  work_context (leave-one-out, Phase 3). peer_percentile is the
                work's own amount among the OTHER usable works of its assigned
                level's group: (#lower + 0.5 * #equal_others) / #others * 100.
                The group is rebuilt across the whole Phase 3 frame for each
                level (context_run.load_frame + peers.group_key), not from
                work_context.group_key -- that column holds each work's OWN
                level's key, so pooling on it drops peers that landed on a
                different level (the defect Phase 4 fixed in signals.py).
                The build asserts #others == n_usable_excl_self for every
                work, so the work itself can never be in its own peer set.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from ..analytics import context_run, fusion, peers
from ..analytics.atypicality_run import risk_result_checksum
from ..analytics.publish import published
from ..ingest.db_utils import bulk_insert, to_records_with_nulls
from ..models.serving import ServedWork, ServingBuild
from . import labels
from .redact import mask_personal

_WORKS_SQL = text(
    """
    SELECT w.work_key, w.house, btrim(w.raw_mp_name) AS mp, w.raw_description AS description,
           w.description_normalized, at.label AS category, w.activity_type_id,
           w.district_authority_id, da.ida_name AS district_authority,
           ws.lifecycle_status, ws.recommended_amount, ws.sanction_amount,
           ws.recommended_date, ws.sanction_date, ws.actual_end_date AS completion_date,
           ls.name AS location_state, c.name AS constituency, cs.name AS constituency_state,
           r.tier, r.risk, r.confidence, r.base_signal_count, r.n_eligible, r.corroboration_factor,
           r.pre_multiplier, r.confidence_components,
           wc.level AS peer_level, wc.group_key AS peer_group_key,
           wc.n_usable_excl_self AS peer_group_size, wc.peer_median, wc.amount_used, wc.amount_basis
    FROM work_state ws
    JOIN work w ON w.work_key = ws.work_key
    LEFT JOIN activity_type at ON at.id = w.activity_type_id
    LEFT JOIN district_authority da ON da.id = w.district_authority_id
    LEFT JOIN work_geo wg ON wg.work_key = w.work_key
    LEFT JOIN state ls ON ls.id = wg.location_state_id
    LEFT JOIN constituency c ON c.id = wg.constituency_id
    LEFT JOIN state cs ON cs.id = c.state_id
    LEFT JOIN risk_result r ON r.work_key = w.work_key AND r.run_id = :run AND r.config_name = :cfg
    LEFT JOIN work_context wc ON wc.work_key = w.work_key AND wc.run_id = :run
    WHERE ws.source_snapshot_id = :snap
    """
)
_SIGNALS_SQL = text("SELECT work_key, signal, score FROM signal_result WHERE run_id = :run AND eligible")


def peer_percentile(frame: pd.DataFrame, level: pd.Series) -> tuple[pd.Series, pd.Series]:
    """(percentile, n_others) per work, indexed like `frame` (Phase 3's
    load_frame, index = work_key). `level` is each work's assigned level."""
    pct = pd.Series(np.nan, index=frame.index)
    others = pd.Series(np.nan, index=frame.index)
    usable = frame["is_usable"]
    for lvl, keys in peers.LEVEL_KEYS.items():
        take = usable & (level.reindex(frame.index) == lvl)
        if not take.any():
            continue
        gkey = peers.group_key(frame, keys)
        pool = usable & gkey.notna()
        g = frame.loc[pool, "amount"].groupby(gkey[pool])
        rmin = g.rank(method="min")
        rmax = g.rank(method="max")
        n_others = g.transform("size") - 1
        p = (rmin - 1 + 0.5 * (rmax - rmin)) / n_others.where(n_others > 0) * 100
        idx = take[take].index
        pct.loc[idx] = p.reindex(idx).round(1)
        others.loc[idx] = n_others.reindex(idx)
    return pct, others


def served_frame(session: Session, run_id: int, config_name: str, snapshot_id: int) -> pd.DataFrame:
    df = pd.read_sql(
        _WORKS_SQL, session.connection(), params={"run": run_id, "cfg": config_name, "snap": snapshot_id}
    )
    sig = pd.read_sql(_SIGNALS_SQL, session.connection(), params={"run": run_id})
    wide = sig.pivot(index="work_key", columns="signal", values="score")
    for s in fusion.BASE_SIGNALS:
        df[labels.SIGNAL_COLUMN[s]] = df["work_key"].map(wide[s]) if s in wide.columns else None

    df["scored"] = df["tier"].notna()
    df["stage"] = df["lifecycle_status"].map(labels.STAGES)
    df["amount"] = pd.to_numeric(
        df["sanction_amount"].where(df["scored"], df["recommended_amount"]), errors="coerce"
    )
    df["record_date"] = df["sanction_date"].where(df["sanction_date"].notna(), df["recommended_date"])
    df["state"] = df["location_state"].where(df["location_state"].notna(), df["constituency_state"])
    df["confidence_label"] = df["confidence"].map(
        lambda c: labels.confidence_label(c) if pd.notna(c) else None
    )
    df["active_signal_count"] = df["base_signal_count"]

    def _active(row) -> str | None:
        if not row["scored"]:
            return None
        names = [
            labels.CONTRACT_SIGNAL[s]
            for s in fusion.BASE_SIGNALS
            if labels.is_active(
                None if pd.isna(row[labels.SIGNAL_COLUMN[s]]) else row[labels.SIGNAL_COLUMN[s]]
            )
        ]
        return ",".join(names)

    df["active_signals"] = df.apply(_active, axis=1)
    df["peer_median"] = pd.to_numeric(df["peer_median"], errors="coerce")
    df["amount_used"] = pd.to_numeric(df["amount_used"], errors="coerce")
    frame = context_run.load_frame(session, snapshot_id)
    level = df.set_index("work_key")["peer_level"]
    pct, n_others = peer_percentile(frame, level)
    wk = df["work_key"]
    df["peer_percentile"] = wk.map(pct)
    has_level = df["peer_level"].notna() & df["peer_group_size"].notna() & wk.map(frame["is_usable"]).eq(True)
    mismatch = has_level & (wk.map(n_others) != df["peer_group_size"])
    if mismatch.any():
        raise AssertionError(
            f"{int(mismatch.sum())} works: peer-set size != work_context.n_usable_excl_self "
            f"(e.g. {df.loc[mismatch, 'work_key'].head(3).tolist()})"
        )
    df["deviation_ratio"] = (df["amount_used"] / df["peer_median"].where(df["peer_median"] > 0)).round(4)
    # Public output layer: phone numbers masked in what is served AND searched
    # (a search by phone digits finds nothing). work.raw_description is untouched.
    df["description"] = df["description"].map(mask_personal)
    df["search_text"] = (
        df[["work_key", "mp", "description", "constituency", "state", "category", "district_authority"]]
        .fillna("")
        .astype(str)
        .agg(" ".join, axis=1)
        .str.lower()
    )
    return df


def build_serving(session: Session, run_id: int | None = None) -> dict:
    pub = published(session)
    if pub is None:
        raise ValueError("no published_run; run scripts/publish_run.py first")
    run_id = pub.run_id if run_id is None else run_id
    config_name = pub.default_config_name if run_id == pub.run_id else fusion.DEFAULT_CONFIG
    run = session.execute(
        text(
            "SELECT ar.source_snapshot_id, ar.engine_version, ss.data_as_of FROM analysis_run ar "
            "JOIN source_snapshot ss ON ss.id = ar.source_snapshot_id WHERE ar.id = :r"
        ),
        {"r": run_id},
    ).one()
    before = risk_result_checksum(session, run_id)

    session.execute(delete(ServedWork).where(ServedWork.run_id == run_id))
    session.execute(delete(ServingBuild).where(ServingBuild.run_id == run_id))
    df = served_frame(session, run_id, config_name, run.source_snapshot_id)

    cols = [c.key for c in ServedWork.__table__.columns if c.key != "run_id"]
    out = df.assign(run_id=run_id)[["run_id", *cols]].copy()
    for c in (
        "activity_type_id",
        "district_authority_id",
        "active_signal_count",
        "n_eligible",
        "peer_group_size",
    ):
        out[c] = out[c].astype("Int64")
    bulk_insert(session, ServedWork, to_records_with_nulls(out))

    after = risk_result_checksum(session, run_id)
    if before != after:
        raise AssertionError(f"risk_result checksum changed during serving build: {before} -> {after}")
    n_scored = int(df["scored"].sum())
    expected = session.execute(
        text("SELECT count(*) FROM risk_result WHERE run_id = :r AND config_name = :c"),
        {"r": run_id, "c": config_name},
    ).scalar_one()
    if n_scored != expected:
        raise AssertionError(f"served_work scored rows {n_scored} != risk_result rows {expected}")
    counts = {
        "works": int(len(df)),
        "scored": n_scored,
        "unscored_recommended_only": int((~df["scored"]).sum()),
        "risk_result_md5_before": before,
        "risk_result_md5_after": after,
    }
    session.add(
        ServingBuild(
            run_id=run_id,
            status="complete",
            config_name=config_name,
            model_version=f"{run.engine_version} / {config_name} / run {run_id}",
            data_as_of=run.data_as_of,
            counts=counts,
        )
    )
    session.flush()
    return {"run_id": run_id, "config": config_name, **counts}

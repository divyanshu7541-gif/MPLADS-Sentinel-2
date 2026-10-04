"""
Phase 13.z: consolidated ML validation for the PUBLISHED run -- read-only.

    python scripts/ml_validation_consolidated.py --out ../docs/ml_validation_run44.json [--boot 200]

Recomputes, on the published run's data and with the recorded seeds, the
metrics the plan asks for, and adds what the Phase 6/7 reports lacked:

  A1 Cox (inactive experiment): out-of-time C-index with a bootstrap 95% CI.
  A2 landmark logistic (closed, not shipped): Brier vs the base-rate Brier and
     AUC, each with a bootstrap 95% CI, at day 90 and day 180.
  Split audit: how many MPs / district authorities / work types appear on both
     sides of the out-of-time split (group leakage exposure, BLUEPRINT §7 rule 4),
     and A1/A2 metrics recomputed on the test works whose MP AND authority never
     appear in training ("unseen groups").
  B4: refits robust Mahalanobis and Isolation Forest from the snapshot with the
     recorded seed and diffs against the stored atypicality_result scores
     (reproducibility), plus stored-score orthogonality to cost_anomaly.
  Registry: completeness of every model_version row.

Writes nothing to the database (every session is rolled back) and checks that
the risk_result checksum is identical before and after. Output: one JSON file
(the consolidated Phase 8 report renders it).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import brier_score_loss, roc_auc_score  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.analytics import atypicality, atypicality_run, publish, survival  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.models.analytics import AnalysisRun  # noqa: E402
from app.models.provenance import SourceSnapshot  # noqa: E402

SPLIT = pd.Timestamp("2025-03-01")  # the Phase 7 out-of-time split date (docs/phase7_report.md)
BOOT_SEED = 20260929

_RAW_SQL = text(
    """
    SELECT wc.work_key, wc.house, w.activity_type_id, ws.sanction_date, ws.sanction_amount,
           ws.actual_end_date, ws.lifecycle_status, btrim(w.raw_mp_name) AS mp, w.district_authority_id
    FROM work_context wc JOIN work w ON w.work_key = wc.work_key
    JOIN work_state ws ON ws.work_key = wc.work_key AND ws.source_snapshot_id = :snap
    WHERE wc.run_id = :run
    """
)
_PAY_SQL = text(
    "SELECT p.work_key, p.payment_date, p.amount FROM payment p"
    " JOIN work_context wc ON wc.work_key = p.work_key AND wc.run_id = :run"
    " WHERE p.source_snapshot_id = :snap"
)


def _ci(values: list[float]) -> list[float]:
    v = np.asarray([x for x in values if np.isfinite(x)])
    return (
        [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]
        if len(v)
        else [None, None]
    )


def a1(raw, sf, boot):
    ev = sf[sf["eligible"]]
    san = pd.to_datetime(raw["sanction_date"])
    train, test = ev.index[san[ev.index] < SPLIT], ev.index[san[ev.index] >= SPLIT]
    types = survival.top_types(raw.loc[train, "activity_type_id"])
    X = survival.a1_covariates(raw, types)
    ok = X.notna().all(axis=1)
    train, test = train[ok[train]], test[ok[test]]
    m = survival.fit_cox(X.loc[train], ev.loc[train, "duration_days"], ev.loc[train, "event"])
    p = survival.cox_predict(m, X.loc[test], ages=ev.loc[test, "duration_days"])["partial_hazard"]
    d, e = ev.loc[test, "duration_days"], ev.loc[test, "event"]
    point = survival.c_index(d, p, e)
    # plan Phase 7: "against a seasonal-naive baseline (e.g., peer-group median completion
    # time)": each test work's predicted duration = the median sanction-to-completion time of
    # TRAINING completions of its work type (overall median where the type has < 20 of them)
    comp = ev.loc[train][ev.loc[train, "event"] == 1]
    med = comp.groupby(raw.loc[comp.index, "activity_type_id"])["duration_days"].agg(["median", "size"])
    med = med.loc[med["size"] >= 20, "median"]
    base_pred = raw.loc[test, "activity_type_id"].map(med).fillna(comp["duration_days"].median())
    baseline = survival.c_index(d, -base_pred, e)
    rng = np.random.default_rng(BOOT_SEED)
    reps = []
    for _ in range(boot):
        i = rng.integers(0, len(test), len(test))
        reps.append(survival.c_index(d.iloc[i].to_numpy(), p.iloc[i].to_numpy(), e.iloc[i].to_numpy()))
    # unseen-group subset: MP and authority both absent from training
    seen_mp, seen_da = set(raw.loc[train, "mp"]), set(raw.loc[train, "district_authority_id"])
    unseen = [
        k
        for k in test
        if raw.at[k, "mp"] not in seen_mp and raw.at[k, "district_authority_id"] not in seen_da
    ]
    un = survival.c_index(d.loc[unseen], p.loc[unseen], e.loc[unseen]) if len(unseen) > 50 else None
    return (
        {
            "train_n": len(train),
            "test_n": len(test),
            "test_events": int(e.sum()),
            "c_index": round(point, 4),
            "c_index_type_median_baseline": round(baseline, 4),
            "c_index_ci95": _ci(reps),
            "chance": 0.5,
            "unseen_groups_test_n": len(unseen),
            "c_index_unseen_groups": None if un is None else round(un, 4),
            "covariates": list(X.columns),
            "types": [int(t) for t in types],
        },
        train,
        test,
    )


def a2(raw, sf, pays, cutoff, types, boot):
    out = {}
    san = pd.to_datetime(raw["sanction_date"])
    for t in survival.LANDMARKS:
        pop = survival.a2_population(sf, raw, cutoff, t)
        Xa = survival.a2_features(raw.loc[pop.index], pays[pays["work_key"].isin(pop.index)], t, types)
        ok = Xa.notna().all(axis=1)
        tr = pop.index[(san[pop.index] < SPLIT) & ok]
        te = pop.index[(san[pop.index] >= SPLIT) & ok]
        model = survival.fit_a2(Xa.loc[tr], pop.loc[tr, "label"])
        prob = model.predict_proba(Xa.loc[te].to_numpy(float))[:, 1]
        y = pop.loc[te, "label"].to_numpy(int)
        # seasonal-naive / peer baseline: the training delay rate of the work's type
        rate = pop.loc[tr].groupby(raw.loc[tr, "activity_type_id"])["label"].agg(["mean", "size"])
        rate = rate.loc[rate["size"] >= 20, "mean"]
        base_p = raw.loc[te, "activity_type_id"].map(rate).fillna(pop.loc[tr, "label"].mean()).to_numpy(float)
        rng = np.random.default_rng(BOOT_SEED + t)
        b, bb, au = [], [], []
        for _ in range(boot):
            i = rng.integers(0, len(y), len(y))
            b.append(brier_score_loss(y[i], prob[i]))
            bb.append(brier_score_loss(y[i], np.full(len(i), y[i].mean())))
            au.append(roc_auc_score(y[i], prob[i]) if len(set(y[i])) == 2 else np.nan)
        diff = [x - z for x, z in zip(b, bb)]
        seen_mp, seen_da = set(raw.loc[tr, "mp"]), set(raw.loc[tr, "district_authority_id"])
        uns = np.array(
            [raw.at[k, "mp"] not in seen_mp and raw.at[k, "district_authority_id"] not in seen_da for k in te]
        )
        unseen = None
        if uns.sum() > 50 and len(set(y[uns])) == 2:
            unseen = {
                "n": int(uns.sum()),
                "brier": round(float(brier_score_loss(y[uns], prob[uns])), 4),
                "brier_base_rate": round(
                    float(brier_score_loss(y[uns], np.full(uns.sum(), y[uns].mean()))), 4
                ),
                "auc": round(float(roc_auc_score(y[uns], prob[uns])), 4),
            }
        out[f"day{t}"] = {
            "train_n": len(tr),
            "test_n": len(te),
            "test_prevalence": round(float(y.mean()), 4),
            "brier": round(float(brier_score_loss(y, prob)), 4),
            "brier_ci95": _ci(b),
            "brier_base_rate": round(float(brier_score_loss(y, np.full(len(y), y.mean()))), 4),
            "brier_type_rate_baseline": round(float(brier_score_loss(y, base_p)), 4),
            "brier_minus_base_ci95": _ci(diff),
            "auc": round(float(roc_auc_score(y, prob)), 4),
            "auc_ci95": _ci(au),
            "unseen_groups": unseen,
        }
    return out


def split_overlap(raw, train, test):
    res = {}
    for col in ("mp", "district_authority_id", "activity_type_id"):
        a, b = set(raw.loc[train, col].dropna()), set(raw.loc[test, col].dropna())
        both = a & b
        res[col] = {
            "train_groups": len(a),
            "test_groups": len(b),
            "in_both": len(both),
            "test_works_whose_group_is_in_train": int(raw.loc[test, col].isin(both).sum()),
            "test_works": len(test),
        }
    return res


def b4(session, run_id, boot_unused):
    X_raw, as_of, snap = atypicality_run.load_raw(session, run_id)
    X = atypicality.build_features(X_raw, as_of)
    fitted = atypicality.score(X)
    stored = pd.read_sql(
        text("SELECT work_key, method, eligible, score FROM atypicality_result WHERE run_id = :r"),
        session.connection(),
        params={"r": run_id},
    )
    out = {}
    for method, res in fitted.items():
        new = res["frame"]["score"]
        old = stored[stored["method"] == method].set_index("work_key")["score"].reindex(new.index)
        both = new.notna() & old.notna()
        out[method] = {
            "eligible_refit": int(new.notna().sum()),
            "eligible_stored": int(old.notna().sum()),
            "eligibility_identical": bool((new.notna() == old.notna()).all()),
            "max_abs_score_diff": float((new[both] - old[both]).abs().max()) if both.any() else None,
            "params": {
                k: (v if isinstance(v, (int, float, str, type(None))) else str(v))
                for k, v in res.get("params", {}).items()
            },
        }
    cost = pd.read_sql(
        text(
            "SELECT work_key, score FROM signal_result "
            "WHERE run_id = :r AND signal = 'cost_anomaly' AND eligible"
        ),
        session.connection(),
        params={"r": run_id},
    ).set_index("work_key")["score"]
    for method in ("robust_mahalanobis", "isolation_forest"):
        s = stored[(stored["method"] == method) & stored["eligible"]].set_index("work_key")["score"]
        j = pd.concat([s, cost], axis=1, join="inner").dropna()
        out[method]["spearman_vs_cost_anomaly"] = round(
            float(j.iloc[:, 0].rank().corr(j.iloc[:, 1].rank())), 4
        )
        out[method]["n_orthogonality"] = len(j)
    return out


def registry(session):
    rows = (
        session.execute(
            text(
                "SELECT id, run_id, model_name, algorithm, status, feature_spec_hash, training_snapshot_id, "
                "seed, metrics, artifact_hash FROM model_version ORDER BY id"
            )
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        missing = [
            k
            for k in ("algorithm", "feature_spec_hash", "training_snapshot_id", "seed", "artifact_hash")
            if r[k] in (None, "")
        ]
        if not r["metrics"]:
            missing.append("metrics")
        out.append(
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "model": r["model_name"],
                "status": r["status"],
                "missing_fields": missing,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--boot", type=int, default=200)
    ap.add_argument("--run", type=int, default=None)
    args = ap.parse_args()
    with get_session_factory()() as session:
        pub = publish.published(session)
        run_id = args.run or pub.run_id
        snap = session.get(AnalysisRun, run_id).source_snapshot_id
        cutoff = pd.Timestamp(session.get(SourceSnapshot, snap).data_as_of)
        before = atypicality_run.risk_result_checksum(session, run_id)
        conn = session.connection()
        raw = (
            pd.read_sql(_RAW_SQL, conn, params={"run": run_id, "snap": snap})
            .set_index("work_key")
            .sort_index()
        )
        pays = pd.read_sql(_PAY_SQL, conn, params={"run": run_id, "snap": snap})
        sf = survival.survival_frame(raw, cutoff)
        a1_res, train, test = a1(raw, sf, args.boot)
        report = {
            "run_id": run_id,
            "cutoff": str(cutoff.date()),
            "split": str(SPLIT.date()),
            "bootstrap_replicates": args.boot,
            "bootstrap_seed": BOOT_SEED,
            "a1": a1_res,
            "a2": a2(raw, sf, pays, cutoff, a1_res["types"], args.boot),
            "split_overlap": split_overlap(raw, train, test),
            "b4": b4(session, run_id, args.boot),
            "registry": registry(session),
        }
        session.rollback()
        after = atypicality_run.risk_result_checksum(session, run_id)
    if before != after:
        raise RuntimeError("risk_result changed during a read-only validation")
    report["risk_result_md5"] = before
    Path(args.out).write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("run_id", "risk_result_md5")}), "->", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# ruff: noqa: E501 -- report text reads better unwrapped
"""
Phase 7 CLI: BLUEPRINT.md §7 A1 (completion-time survival) and A2 (365-day
delay early warning), validated out of time. Design and censoring rules:
docs/phase7_design_note.md.

Default mode VALIDATES ONLY: it reads the database, fits and scores in
memory, and writes a draft review (PHASE7_REVIEW_PATH). It writes nothing to
the database. Persisting forecast_result rows and model_version entries needs
--register, reserved for after owner sign-off (not implemented until then).

    python scripts/run_survival.py [--run N] [--split 2025-03-01]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

from app.analytics import atypicality_run, publish, survival  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.models.analytics import AnalysisRun, RiskResult  # noqa: E402
from app.models.provenance import SourceSnapshot  # noqa: E402

REVIEW_PATH = Path(os.environ.get("PHASE7_REVIEW_PATH") or "phase7_validation_draft.md")

_RAW_SQL = text(
    """
    SELECT wc.work_key, wc.house, w.activity_type_id, ws.sanction_date, ws.sanction_amount,
           ws.actual_end_date, ws.lifecycle_status
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=None)
    ap.add_argument("--split", default="2025-03-01")
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--record-inactive", action="store_true",
                    help="record the validated A1 model in model_version as an INACTIVE experiment "
                         "(no forecast_result rows; owner decision, Phase 7)")
    args = ap.parse_args()
    if args.register:
        print("--register is deliberately not available until the Phase 7 review is signed off")
        return 2
    with get_session_factory()() as session:
        pub = publish.published(session)
        run_id = args.run or (pub.run_id if pub else session.execute(select(func.max(RiskResult.run_id))).scalar())
        snap = session.get(AnalysisRun, run_id).source_snapshot_id
        cutoff = pd.Timestamp(session.get(SourceSnapshot, snap).data_as_of)
        before = atypicality_run.risk_result_checksum(session, run_id)
        conn = session.connection()
        raw = pd.read_sql(_RAW_SQL, conn, params={"run": run_id, "snap": snap}).set_index("work_key").sort_index()
        pays = pd.read_sql(_PAY_SQL, conn, params={"run": run_id, "snap": snap})
        body, record = validate(raw, pays, cutoff, pd.Timestamp(args.split))
        if args.record_inactive:
            mv = record_a1_inactive(session, run_id, snap, record, before)
            session.commit()
            body += ["## Registry", "", f"A1 recorded in model_version id {mv.id} as `{mv.status}`: {mv.status_note}", ""]
        else:
            session.rollback()  # nothing was written; make that explicit
        after = atypicality_run.risk_result_checksum(session, run_id)
    if before != after:
        raise RuntimeError("risk_result changed during Phase 7 validation")
    wrote = "only the inactive A1 model_version row written" if args.record_inactive else "nothing written to the database"
    head = ["# Phase 7 validation (generated body)", "",
            f"analysis_run {run_id}; cutoff {cutoff.date()}; out-of-time split {args.split}; "
            f"risk_result md5 `{before}` before and after (identical); {wrote}.", ""]
    REVIEW_PATH.write_text("\n".join(head + body) + "\n", encoding="utf-8")
    print(f"validation draft written to {REVIEW_PATH}")
    return 0


def _t(df: pd.DataFrame, nd: int = 3) -> list[str]:
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join(
            f"{v:,}" if isinstance(v, (int, np.integer)) else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))
            for v in r) + " |")
    return out


def _subset_metrics(pred: pd.DataFrame, sf: pd.DataFrame) -> list[dict]:
    rows = []
    obs = sf["event"] == 1
    o, c = pred[obs], pred[~obs]
    if obs.sum() > 2:
        ci_obs = survival.c_index(sf.loc[obs, "duration_days"], o["partial_hazard"], sf.loc[obs, "event"])
        fin = o["predicted_median"].notna()
        mae = float((o.loc[fin, "predicted_median"] - sf.loc[obs & fin.reindex(sf.index, fill_value=False), "duration_days"]).abs().median())
        rows.append({"subset": "observed (completed)", "n": int(obs.sum()), "C-index within subset": ci_obs,
                     "median |pred median - actual| days": mae, "share model says 'should be done by now'": float("nan"),
                     "mean S(own time)": float(o["s_at_age"].mean())})
    if (~obs).sum() > 0:
        overdue = (c["predicted_median"] < sf.loc[~obs, "duration_days"]).mean()
        rows.append({"subset": "censored (still open)", "n": int((~obs).sum()), "C-index within subset": float("nan"),
                     "median |pred median - actual| days": float("nan"),
                     "share model says 'should be done by now'": float(overdue),
                     "mean S(own time)": float(c["s_at_age"].mean())})
    return rows


A1_INACTIVE_NOTE = (
    "Completed but INACTIVE experiment (owner decision, Phase 7 review, 2026-09-27): out-of-time C-index "
    "{c:.3f} is too close to the no-skill line (0.5) to persist as evidence. No forecast_result rows are "
    "stored. Revisit once more sanction cohorts clear 365 days of follow-up (docs/phase7_report.md)."
)


def record_a1_inactive(session, run_id: int, snap: int, record: dict, risk_md5: str):
    import hashlib
    import json

    from app.analytics.signals import json_safe
    from app.models.analytics import ModelVersion

    m = record["model"]
    params = {"covariates": list(m.params_.index), "coefficients": m.params_.round(10).to_dict(),
              "penalizer": survival.COX_PENALIZER, "split": record["split"], "variant": record["variant"],
              "top_types": record["types"], "dropped_constant": m.dropped_constant_}
    spec = {"origin": "sanction_date", "event": "completion", "censoring": "right-censored at data_as_of",
            "covariates_allowed": list(survival.A1_BASE) + ["type_<top N>"], "forbidden": list(survival.FORBIDDEN)}
    mv = ModelVersion(
        run_id=run_id, model_name="a1_cox_completion_time", algorithm="lifelines CoxPHFitter (Cox proportional hazards)",
        feature_spec_hash=hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
        training_snapshot_id=snap, seed=survival.SEED, params=json_safe(params),
        metrics=json_safe({**record["metrics"], "risk_result_md5": risk_md5}),
        artifact_hash=hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest(),
        status="inactive_experiment", status_note=A1_INACTIVE_NOTE.format(c=record["metrics"]["test_c_index"]),
    )
    session.add(mv)
    session.flush()
    return mv


def validate(raw: pd.DataFrame, pays: pd.DataFrame, cutoff: pd.Timestamp, split: pd.Timestamp):
    L: list[str] = []
    sf = survival.survival_frame(raw, cutoff)
    san = pd.to_datetime(raw["sanction_date"])

    # ---- population / censoring
    L += ["## 1. Event and censoring counts", ""]
    rows = []
    for h in ("LS", "RS", "all"):
        m = (raw["house"] == h) if h != "all" else pd.Series(True, index=raw.index)
        s = sf[m]
        rows.append({"house": h, "works": int(m.sum()), "evaluated": int(s["eligible"].sum()),
                     "events (completed)": int((s["event"] == 1).sum()), "right-censored at cutoff": int((s["event"] == 0).sum()),
                     "completed after cutoff (censored)": int(s["completed_after_cutoff"].sum()),
                     "not evaluated": int((~s["eligible"]).sum())})
    L += _t(pd.DataFrame(rows))
    reasons = sf["not_evaluated_reason"].value_counts()
    L += ["", "Not-evaluated reasons: " + (", ".join(f"{k} {v}" for k, v in reasons.items()) if len(reasons) else "none"), ""]

    ev = sf[sf["eligible"]]
    L += ["## 2. Kaplan-Meier baseline (all evaluated works)", ""]
    rows = []
    for h in ("LS", "RS", "all"):
        e = ev[(raw.loc[ev.index, "house"] == h)] if h != "all" else ev
        k = survival.km(e["duration_days"], e["event"])
        sfv = k.survival_function_at_times([90, 180, 365]).to_numpy()
        rows.append({"house": h, "n": len(e), "S(90)": float(sfv[0]), "S(180)": float(sfv[1]), "S(365)": float(sfv[2]),
                     "median days": float(k.median_survival_time_), "max follow-up days": float(e["duration_days"].max())})
    L += _t(pd.DataFrame(rows))
    L += ["", "S(t) = share of works NOT yet completed t days after sanction, with open works censored.", ""]

    # ---- A1
    train = ev.index[san[ev.index] < split]
    test = ev.index[san[ev.index] >= split]
    types = survival.top_types(raw.loc[train, "activity_type_id"])
    X = survival.a1_covariates(raw, types)
    okX = X.notna().all(axis=1)
    train, test = train[okX[train]], test[okX[test]]
    L += ["## 3. A1 Cox model: out-of-time", "",
          f"Train: works sanctioned before {split.date()} ({len(train):,}; {int(ev.loc[train, 'event'].sum()):,} events). "
          f"Test: sanctioned on/after it ({len(test):,}; {int(ev.loc[test, 'event'].sum()):,} events, rest censored at cutoff). "
          f"Covariates known at sanction only: {', '.join(X.columns)}.", ""]
    variants = {}
    # (a) full follow-up: training outcomes observed up to the cutoff
    dtr, etr = ev.loc[train, "duration_days"], ev.loc[train, "event"]
    variants["full follow-up (train outcomes to cutoff)"] = survival.fit_cox(X.loc[train], dtr, etr)
    # (b) strict: training data administratively censored at the split date
    cap = (split - san[train]).dt.days.astype(float)
    dstr = np.minimum(dtr, cap)
    estr = ((etr == 1) & (dtr <= cap)).astype(int)
    variants["strict (train censored at split date)"] = survival.fit_cox(X.loc[train], dstr, estr)
    rows, preds = [], {}
    for name, m in variants.items():
        p = survival.cox_predict(m, X.loc[test], ages=ev.loc[test, "duration_days"])
        preds[name] = p
        row = {"variant": name, "test C-index": survival.c_index(ev.loc[test, "duration_days"], p["partial_hazard"], ev.loc[test, "event"]),
               "train C-index": survival.c_index(dtr, survival.cox_predict(m, X.loc[train])["partial_hazard"], etr),
               "max fitted time (days)": p["max_fitted_time"].iloc[0], "dropped constant": ", ".join(m.dropped_constant_) or "none"}
        for h in ("LS", "RS"):
            hm = raw.loc[test, "house"] == h
            row[f"test C-index {h}"] = (survival.c_index(ev.loc[test[hm], "duration_days"], p.loc[hm, "partial_hazard"],
                                                         ev.loc[test[hm], "event"]) if hm.sum() > 10 else float("nan"))
        rows.append(row)
    L += _t(pd.DataFrame(rows))
    L += ["", "C-index: share of comparable pairs where the work the model says completes sooner did complete sooner; 0.5 = chance.", ""]

    main = "full follow-up (train outcomes to cutoff)"
    record = {"model": variants[main], "variant": main, "types": [int(t) for t in types], "split": str(split.date()),
              "metrics": {"test_c_index": rows[0]["test C-index"], "train_c_index": rows[0]["train C-index"],
                          "test_c_index_LS": rows[0]["test C-index LS"], "test_c_index_RS": rows[0]["test C-index RS"],
                          "strict_variant_test_c_index": rows[1]["test C-index"],
                          "n_train": len(train), "n_test": len(test)}}
    L += ["Hazard ratios (full follow-up variant; > 1 = completes sooner):", ""]
    hr = variants[main].summary[["exp(coef)", "p"]].reset_index().rename(columns={"covariate": "covariate", "exp(coef)": "hazard ratio"})
    L += _t(hr)

    for name in variants:
        L += ["", f"### Calibration at 365 days -- {name}", "",
              "Mean predicted S(365) vs Kaplan-Meier S(365) per decile of predicted risk (test works). Deciles with nobody followed past 365 days get no KM value.", ""]
        cal = survival.calibration_365(preds[name]["s365"], ev.loc[test, "duration_days"], ev.loc[test, "event"])
        L += _t(cal)
    L += ["", "### Observed vs censored test works (full follow-up variant)", ""]
    L += _t(pd.DataFrame(_subset_metrics(preds[main], ev.loc[test])))
    L += ["", "'mean S(own time)': predicted probability a work like this is still open at its observed completion day (observed) or at its current age (censored).", ""]

    # ---- A2
    L += ["## 4. A2 365-day delay early warning (landmark, out-of-time)", "",
          f"Labelled population: works sanctioned on or before {(cutoff - pd.Timedelta(days=365)).date()} (full 365-day follow-up), still open at day t. "
          f"Train sanctioned before {split.date()}, test from {split.date()} to {(cutoff - pd.Timedelta(days=365)).date()}. Label 1 = not complete within 365 days.", ""]
    for t in survival.LANDMARKS:
        pop = survival.a2_population(sf, raw, cutoff, t)
        Xa = survival.a2_features(raw.loc[pop.index], pays[pays["work_key"].isin(pop.index)], t, types)
        ok = Xa.notna().all(axis=1)
        tr = pop.index[(san[pop.index] < split) & ok]
        te = pop.index[(san[pop.index] >= split) & ok]
        model = survival.fit_a2(Xa.loc[tr], pop.loc[tr, "label"])
        ptr = model.predict_proba(Xa.loc[te].to_numpy(float))[:, 1]
        met = survival.a2_metrics(pop.loc[te, "label"], ptr)
        L += [f"### Day {t}", "",
              f"Train {len(tr):,} (prevalence {pop.loc[tr, 'label'].mean():.1%}); test {met['n']:,} (prevalence {met['prevalence']:.1%}); "
              f"not evaluable (missing feature) {int((~ok).sum())}.",
              f"Test Brier {met['brier']:.4f} vs {met['brier_base_rate']:.4f} for always predicting the test base rate; AUC {met['auc']:.3f} (reference only).", ""]
        L += _t(met["calibration"].assign(bin=lambda d: d["bin"] + 1).rename(columns={"bin": "decile"}))
        L += [""]
    return L, record


if __name__ == "__main__":
    raise SystemExit(main())

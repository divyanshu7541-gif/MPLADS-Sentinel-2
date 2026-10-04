"""
Phase 7 -- BLUEPRINT.md §7 A1 (completion-time survival) and A2 (365-day
delay early warning). EVIDENCE ONLY: nothing here is read by Phase 5 fusion.
Design, censoring rules and leakage boundary: docs/phase7_design_note.md.

Time origin = sanction date. Event = completion. Cutoff = the snapshot's
data_as_of. Open works are RIGHT-CENSORED at the cutoff (never dropped, never
treated as on time); a completion dated after the cutoff is censored at the
cutoff (J3); missing sanction date, completed-without-end-date, or an end
before sanction -> "not evaluated", never imputed.

Leakage: covariates/features come only from an allow-list of things known at
the prediction time (A1: at sanction; A2: at landmark day t, payments dated
<= sanction + t only). `assert_allowed` raises LeakageError on anything else,
and FORBIDDEN names post-event / hindsight fields explicitly.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, KaplanMeierFitter
from lifelines.utils import concordance_index
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HORIZON = 365
LANDMARKS = (90, 180)
SEED = 20260926
COX_PENALIZER = 0.01  # light ridge for stability with type dummies; not tuned
N_TOP_TYPES = 10  # work types kept as their own dummy; the rest pooled (J4)

# Calendar quarter of sanction was dropped (owner's decision, Phase 7 review):
# with under two years of sanctions it encoded WHICH cohort a work belongs to,
# not seasonality, and did not carry over out of time (design note J8).
A1_BASE = ("log_sanction_amount", "is_rs")
A2_PAYMENT = ("payments_by_t", "paid_share_by_t", "any_payment_by_t")
TYPE_PREFIX = "type_"

# Known only after completion, after the prediction day, or with full hindsight.
FORBIDDEN = (
    r"actual_end",
    r"actual_amount",
    r"lifecycle",
    r"completed",
    r"amount_used",
    r"peer_median",
    r"paid_total",
    r"(^|_)event($|_)",
    r"duration",
    r"(^|_)label($|_)",
    r"signal",
    r"cost_anomaly",
    r"lifecycle_delay",
    r"risk",
    r"tier",
    r"confidence",
    r"compliance",
    r"(^|_)c5($|_)",
    r"atypical",
    r"mahalanobis",
    r"isolation",
    r"evidence_fact",
    r"same_payee",
    r"(^|_)mp($|_)",
    r"mp_name",
    r"payee_id",
    r"vendor",
    r"work_key",
    r"quarter",
    r"sanction_q\d",
)


class LeakageError(ValueError):
    """A post-event, hindsight or identity field reached a model matrix."""


def assert_allowed(columns, stage: str) -> None:
    cols = [str(c) for c in columns]
    bad = [c for c in cols if any(re.search(p, c.lower()) for p in FORBIDDEN)]
    if bad:
        raise LeakageError(f"{stage}: forbidden column(s) {bad}")
    allowed = set(A1_BASE) | (set(A2_PAYMENT) if stage == "A2" else set())
    other = [c for c in cols if c not in allowed and not c.startswith(TYPE_PREFIX)]
    if other:
        raise LeakageError(f"{stage}: columns outside the allow-list {other}")


# ---- event / censoring -----------------------------------------------------------


def survival_frame(raw: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """raw: sanction_date, actual_end_date, lifecycle_status. Returns eligible,
    event (1 completion observed by cutoff / 0 censored), duration_days,
    not_evaluated_reason. Rules: docs/phase7_design_note.md §2."""
    san = pd.to_datetime(raw["sanction_date"])
    end = pd.to_datetime(raw["actual_end_date"])
    completed = raw["lifecycle_status"].eq("completed")
    out = pd.DataFrame(index=raw.index)
    reason = pd.Series(None, index=raw.index, dtype=object)
    reason[san.isna()] = "no_sanction_date"
    reason[reason.isna() & (san > cutoff)] = "sanctioned_after_cutoff"
    reason[reason.isna() & completed & end.isna()] = "completed_without_end_date"
    reason[reason.isna() & completed & (end < san)] = "end_before_sanction"
    observed = completed & end.notna() & (end <= cutoff)
    out["event"] = observed.astype(int)
    out["duration_days"] = np.where(observed, (end - san).dt.days, (cutoff - san).dt.days).astype(float)
    out["completed_after_cutoff"] = completed & (end > cutoff)  # censored at cutoff (J3)
    out["eligible"] = reason.isna()
    out.loc[~out["eligible"], ["event", "duration_days"]] = np.nan
    out["not_evaluated_reason"] = reason
    return out


# ---- covariates (known at sanction) --------------------------------------------


def top_types(activity_type_id: pd.Series, n: int = N_TOP_TYPES) -> list:
    return list(activity_type_id.dropna().astype(int).value_counts().index[:n])


def a1_covariates(raw: pd.DataFrame, types: list) -> pd.DataFrame:
    """raw: sanction_amount, house, sanction_date, activity_type_id. NaN
    where a covariate cannot be formed (-> not evaluated)."""
    amt = pd.to_numeric(raw["sanction_amount"], errors="coerce").where(lambda s: s > 0)
    X = pd.DataFrame(
        {"log_sanction_amount": np.log(amt), "is_rs": raw["house"].eq("RS").astype(float)},
        index=raw.index,
    )
    at = pd.to_numeric(raw["activity_type_id"], errors="coerce")
    for t in types:
        X[f"{TYPE_PREFIX}{int(t)}"] = (at == t).astype(float)
    assert_allowed(X.columns, "A1")
    return X


def a2_features(raw: pd.DataFrame, payments: pd.DataFrame, t: int, types: list) -> pd.DataFrame:
    """A1 covariates plus payment features using ONLY payments dated on or
    before sanction + t days. payments: work_key, payment_date, amount."""
    X = a1_covariates(raw, types)
    san = pd.to_datetime(raw["sanction_date"])
    p = payments.copy()
    p["payment_date"] = pd.to_datetime(p["payment_date"])
    p["limit"] = p["work_key"].map(san + pd.Timedelta(days=t))
    p = p[p["payment_date"] <= p["limit"]]  # the time cut: nothing after day t
    g = p.groupby("work_key")["amount"]
    n = g.size().reindex(raw.index).fillna(0)
    paid = pd.to_numeric(g.sum(), errors="coerce").reindex(raw.index).fillna(0.0)
    amt = pd.to_numeric(raw["sanction_amount"], errors="coerce")
    X["payments_by_t"] = np.log1p(n)
    X["paid_share_by_t"] = (paid / amt.where(amt > 0)).clip(0, 1)
    X["any_payment_by_t"] = (n > 0).astype(float)
    assert_allowed(X.columns, "A2")
    return X


# ---- A1: Kaplan-Meier + Cox --------------------------------------------------------


def km(duration: pd.Series, event: pd.Series) -> KaplanMeierFitter:
    return KaplanMeierFitter().fit(duration, event)


def fit_cox(X: pd.DataFrame, duration: pd.Series, event: pd.Series) -> CoxPHFitter:
    """Cox PH on the A1 covariates. Covariates constant in the training set
    (e.g. a work type absent from this split) carry no information and make
    the fit singular, so they are dropped; `model.dropped_constant_` lists them."""
    assert_allowed(X.columns, "A1")
    constant = [c for c in X.columns if X[c].nunique(dropna=True) <= 1]
    df = X.drop(columns=constant).assign(_d=duration, _e=event)
    model = CoxPHFitter(penalizer=COX_PENALIZER).fit(df, duration_col="_d", event_col="_e")
    model.dropped_constant_ = constant
    return model


def cox_predict(model: CoxPHFitter, X: pd.DataFrame, ages: pd.Series | None = None) -> pd.DataFrame:
    """Partial hazard, S(365), predicted median (NaN if S stays >= 0.5 over the
    fitted time range) and, if ages given, S(age)."""
    assert_allowed(X.columns, "A1")
    X = X[list(model.params_.index)]  # exactly the covariates the model was fitted on
    sf = model.predict_survival_function(X, times=sorted({HORIZON, *range(0, 2001, 5)}))
    out = pd.DataFrame(index=X.index)
    out["partial_hazard"] = model.predict_partial_hazard(X).to_numpy()
    out["s365"] = sf.loc[HORIZON].to_numpy()
    below = sf.le(0.5)
    med = below.idxmax().where(below.any())
    out["predicted_median"] = med.to_numpy(dtype=float)
    if ages is not None:
        grid = np.asarray(sf.index)
        pos = np.clip(np.searchsorted(grid, ages.to_numpy(float), side="right") - 1, 0, len(grid) - 1)
        out["s_at_age"] = sf.to_numpy()[pos, np.arange(sf.shape[1])]
    out["max_fitted_time"] = float(model.baseline_survival_.index.max())
    return out


def c_index(duration, predicted_risk, event) -> float:
    """Harrell's C: higher risk should complete SOONER (event = completion),
    so risk = partial hazard and lifelines expects 'higher score = longer'."""
    return float(concordance_index(duration, -np.asarray(predicted_risk, float), event))


def calibration_365(
    pred_s365: pd.Series, duration: pd.Series, event: pd.Series, bins: int = 10
) -> pd.DataFrame:
    """Mean predicted S(365) vs Kaplan-Meier S(365) per decile of predicted
    risk. KM respects censoring; a decile with nobody followed past 365 days
    gets no KM value (reported, not extrapolated)."""
    d = pd.DataFrame({"p": pred_s365, "t": duration, "e": event}).dropna()
    d["bin"] = pd.qcut(d["p"].rank(method="first"), bins, labels=False)
    rows = []
    for b, g in d.groupby("bin"):
        at_risk = int((g["t"] >= HORIZON).sum())
        kmv = float(km(g["t"], g["e"]).survival_function_at_times(HORIZON).iloc[0]) if at_risk else np.nan
        rows.append(
            {
                "decile": int(b) + 1,
                "n": len(g),
                "at_risk_365": at_risk,
                "mean_pred_S365": float(g["p"].mean()),
                "KM_S365": kmv,
            }
        )
    return pd.DataFrame(rows)


# ---- A2: landmark 365-day delay ----------------------------------------------------


def a2_population(sf: pd.DataFrame, raw: pd.DataFrame, cutoff: pd.Timestamp, t: int) -> pd.DataFrame:
    """Labelled landmark set at day t: works with FULL 365-day follow-up
    (sanctioned <= cutoff - 365; recent cohorts excluded entirely, never only
    their open works) that are still open at day t. label 1 = not complete
    within 365 days."""
    san = pd.to_datetime(raw["sanction_date"])
    full = sf["eligible"] & (san <= cutoff - pd.Timedelta(days=HORIZON))
    done_by_t = (sf["event"] == 1) & (sf["duration_days"] <= t)
    pop = sf.loc[full & ~done_by_t].copy()
    pop["label"] = (~((pop["event"] == 1) & (pop["duration_days"] <= HORIZON))).astype(int)
    return pop


def fit_a2(X: pd.DataFrame, y: pd.Series):
    assert_allowed(X.columns, "A2")
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, random_state=SEED))
    return model.fit(X.to_numpy(float), y.to_numpy(int))


def a2_metrics(y: pd.Series, p: np.ndarray, bins: int = 10) -> dict:
    d = pd.DataFrame({"y": y.to_numpy(int), "p": p})
    d["bin"] = pd.qcut(d["p"].rank(method="first"), bins, labels=False)
    cal = d.groupby("bin").agg(n=("y", "size"), mean_pred=("p", "mean"), observed=("y", "mean")).reset_index()
    return {
        "brier": float(brier_score_loss(d["y"], d["p"])),
        "brier_base_rate": float(brier_score_loss(d["y"], np.full(len(d), d["y"].mean()))),
        "auc": float(roc_auc_score(d["y"], d["p"])) if d["y"].nunique() == 2 else float("nan"),
        "prevalence": float(d["y"].mean()),
        "n": len(d),
        "calibration": cal,
    }

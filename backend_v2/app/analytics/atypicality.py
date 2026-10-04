"""
BLUEPRINT.md §7 B4 -- multivariate atypicality. EVIDENCE ONLY.

Scores each scored work on a small, interpretable feature vector with a
robust Mahalanobis distance (primary; minimum covariance determinant, with
per-feature contributions) and an Isolation Forest (comparator). Results go
to atypicality_result, never to signal_result, so Phase 5 fusion -- which
reads only the six base signals -- cannot count them in risk, the
active-signal count or corroboration (gate G6; tested, not just omitted).

Features (FEATURES), all per work, all from existing pipeline outputs:
  log_amount                   ln(amount used), from Phase 3 work_context
  log_peer_deviation_ratio     ln(amount / Phase 3 peer median) -- Phase 3's own
                               peer baseline, fed in, not re-derived
  days_rec_to_sanction         log1p(sanction date - recommendation date)
  days_sanction_to_end_or_age  log1p(completion - sanction) if completed, else
                               log1p(as_of - sanction): censored age, never imputed
  payment_count                log1p(number of payment rows)
  distinct_payee_count         log1p(number of distinct payees) -- a COUNT; payee
                               identity itself never enters
  description_length           log1p(characters in the normalised description)
log1p/log reduce the heavy right skew of days, counts and amounts; MCD is
affine-equivariant and Isolation Forest splits per feature, so no further
scaling is needed.

Leakage (BLUEPRINT §7 rules 1-2, 7): MP identity, payee identity and
risk_score must never enter the matrix. `assert_no_leakage` enforces this in
code -- the matrix must have exactly FEATURES, and any column matching a
forbidden pattern raises LeakageError -- and it runs on every fit and score.

Missing data: a work missing ANY feature is "not evaluated" (eligible=False,
score NULL, missing features listed), never scored as 0 and never imputed.
Notably every Rajya Sabha work lacks a recommendation date (the RS recommended
file is absent from Snapshot A, BLUEPRINT §2 Hard Limit 6), so no RS work is
evaluated by this layer.

Isolation Forest: contamination is left at sklearn's "auto" (offset -0.5, the
original paper's), because contamination only sets the threshold of
predict()'s inlier/outlier LABEL, which is never used or stored here. Only the
continuous score_samples output is kept, so no "flagged %" exists to tune --
BLUEPRINT §7's objection that IF "fixes the share flagged" does not apply.
"""

from __future__ import annotations

import hashlib
import json
import re

import numpy as np
import pandas as pd
from sklearn.covariance import MinCovDet
from sklearn.ensemble import IsolationForest

from .signals import empirical_tail_score

FEATURES = (
    "log_amount",
    "log_peer_deviation_ratio",
    "days_rec_to_sanction",
    "days_sanction_to_end_or_age",
    "payment_count",
    "distinct_payee_count",
    "description_length",
)
# Column-name patterns that must never appear in the feature matrix.
FORBIDDEN_PATTERNS = (
    r"(^|_)mp($|_)",
    r"mp_name",
    r"member",
    r"person",
    r"tenure",
    r"payee_id",
    r"payee_name",
    r"vendor",
    r"risk",
    r"tier",
    r"(^|_)score($|_)",
    r"corroborat",
    r"work_key",
)
# Robust Mahalanobis uses every feature EXCEPT distinct_payee_count (owner's
# decision, Phase 6 review). In the MCD's robust core every work has exactly
# as many payments as payees, so (payment_count - distinct_payee_count) has zero
# variance there: the covariance is singular and that direction gets ~0 weight,
# silently hiding repeat payments to the same payee. Isolation Forest keeps all
# seven features; repeat payments are recorded as a plain evidence FACT
# (work_evidence_fact, "pays_same_payee_more_than_once"), never a score.
MAHALANOBIS_FEATURES = tuple(f for f in FEATURES if f != "distinct_payee_count")
SEED = 20260926
IF_PARAMS = {"n_estimators": 200, "max_samples": "auto", "contamination": "auto", "random_state": SEED}
MCD_PARAMS = {"support_fraction": None, "random_state": SEED}
METHODS = ("robust_mahalanobis", "isolation_forest")


class LeakageError(ValueError):
    """A forbidden or unexpected column reached the atypicality matrix."""


def _forbidden(name: str) -> bool:
    n = name.lower()
    return any(re.search(p, n) for p in FORBIDDEN_PATTERNS)


def assert_no_leakage(columns) -> None:
    """Raise LeakageError unless `columns` is exactly FEATURES and none of
    them matches a forbidden pattern (MP/payee identity, risk score)."""
    cols = list(columns)
    bad = [c for c in cols if _forbidden(str(c))]
    if bad:
        raise LeakageError(f"forbidden column(s) in the atypicality matrix: {bad}")
    if tuple(cols) != FEATURES:
        extra = [c for c in cols if c not in FEATURES]
        raise LeakageError(f"atypicality matrix must be exactly {FEATURES}; unexpected: {extra or cols}")


assert_no_leakage(FEATURES)  # the specification itself must be clean (raises at import otherwise)


def feature_spec_hash() -> str:
    spec = {
        "features": FEATURES,
        "mahalanobis_features": MAHALANOBIS_FEATURES,
        "forbidden": FORBIDDEN_PATTERNS,
        "transforms": "log amount; log(amount/peer_median); log1p days/counts/length",
    }
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def build_features(raw: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """raw columns: amount_used, peer_median, recommended_date, sanction_date,
    actual_end_date, lifecycle_status, n_payments, n_payees, description.
    Returns FEATURES (NaN = missing), indexed like `raw`."""
    amount = pd.to_numeric(raw["amount_used"], errors="coerce").where(lambda s: s > 0)
    peer = pd.to_numeric(raw["peer_median"], errors="coerce").where(lambda s: s > 0)
    rec, san, end = (pd.to_datetime(raw[c]) for c in ("recommended_date", "sanction_date", "actual_end_date"))
    completed = raw["lifecycle_status"] == "completed"
    rec_days = (san - rec).dt.days
    dur_days = pd.Series(np.where(completed, (end - san).dt.days, (as_of - san).dt.days), index=raw.index)
    dur_days = dur_days.where(~(completed & end.isna()))  # completed without an end date -> missing
    desc = raw["description"]
    desc_len = (
        desc.fillna("")
        .astype(str)
        .str.strip()
        .str.len()
        .where(desc.notna() & (desc.astype(str).str.strip() != ""))
    )
    X = pd.DataFrame(
        {
            "log_amount": np.log(amount),
            "log_peer_deviation_ratio": np.log(amount / peer),
            "days_rec_to_sanction": np.log1p(rec_days.where(rec_days >= 0)),
            "days_sanction_to_end_or_age": np.log1p(dur_days.where(dur_days >= 0)),
            "payment_count": np.log1p(pd.to_numeric(raw["n_payments"])),
            "distinct_payee_count": np.log1p(pd.to_numeric(raw["n_payees"])),
            "description_length": np.log1p(desc_len),
        },
        index=raw.index,
    )[list(FEATURES)]
    assert_no_leakage(X.columns)
    return X


def _artifact_hash(*arrays) -> str:
    h = hashlib.sha256()
    for a in arrays:
        h.update(np.round(np.asarray(a, dtype=float), 10).tobytes())
    return h.hexdigest()


def score(X: pd.DataFrame) -> dict:
    """Fit both models on the evaluable works of X and score them. Returns
    {method: {"frame": DataFrame(eligible, score, percentile, contributions,
    missing_features), "params", "metrics", "artifact_hash", "algorithm"}}."""
    assert_no_leakage(X.columns)
    missing = X.isna()
    eligible = ~missing.any(axis=1)
    Xe = X.loc[eligible].to_numpy(dtype=float)
    Xm = X.loc[eligible, list(MAHALANOBIS_FEATURES)].to_numpy(dtype=float)
    missing_lists = [[f for f, m in zip(FEATURES, row) if m] for row in missing.itertuples(index=False)]
    out = {}

    # --- robust Mahalanobis (MCD) ---
    mcd = MinCovDet(**MCD_PARAMS).fit(Xm)
    diff = Xm - mcd.location_
    prec = mcd.get_precision()
    contrib = diff * (diff @ prec)  # per-feature terms; they sum to the squared distance
    d2 = contrib.sum(axis=1)
    dist = np.sqrt(np.clip(d2, 0, None))
    eig = np.linalg.eigvalsh(mcd.covariance_)
    frame = pd.DataFrame(index=X.index)
    frame["eligible"] = eligible
    frame["score"] = pd.Series(dist, index=X.index[eligible]).reindex(X.index)
    frame["percentile"] = empirical_tail_score(frame["score"])
    contrib_s = pd.Series(
        [{f: round(float(v), 6) for f, v in zip(MAHALANOBIS_FEATURES, row)} for row in contrib],
        index=X.index[eligible],
    ).reindex(X.index)
    frame["contributions"] = [c if isinstance(c, dict) else {} for c in contrib_s]
    frame["missing_features"] = missing_lists
    out["robust_mahalanobis"] = {
        "frame": frame,
        "algorithm": "sklearn MinCovDet (FastMCD) robust Mahalanobis distance",
        "params": {**MCD_PARAMS, "features": list(MAHALANOBIS_FEATURES)},
        "metrics": {
            "n_evaluated": int(eligible.sum()),
            "n_not_evaluated": int((~eligible).sum()),
            "support_size": int(mcd.support_.sum()),
            "covariance_eigenvalues": [float(e) for e in eig],
            "covariance_condition_number": float(eig.max() / eig.min()) if eig.min() > 0 else None,
            "robust_location": dict(zip(MAHALANOBIS_FEATURES, map(float, mcd.location_))),
        },
        "artifact_hash": _artifact_hash(mcd.location_, mcd.covariance_),
    }

    # --- Isolation Forest (comparator) ---
    iso = IsolationForest(**IF_PARAMS).fit(Xe)
    s = -iso.score_samples(Xe)  # higher = more atypical
    f2 = pd.DataFrame(index=X.index)
    f2["eligible"] = eligible
    f2["score"] = pd.Series(s, index=X.index[eligible]).reindex(X.index)
    f2["percentile"] = empirical_tail_score(f2["score"])
    f2["contributions"] = [{} for _ in range(len(X))]
    f2["missing_features"] = missing_lists
    out["isolation_forest"] = {
        "frame": f2,
        "algorithm": "sklearn IsolationForest (score_samples only; predict() never used)",
        "params": {**IF_PARAMS, "features": list(FEATURES)},
        "metrics": {"n_evaluated": int(eligible.sum()), "n_not_evaluated": int((~eligible).sum())},
        "artifact_hash": _artifact_hash(s),
    }
    return out

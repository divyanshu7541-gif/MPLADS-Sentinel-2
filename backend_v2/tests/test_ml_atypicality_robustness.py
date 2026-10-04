"""B4 atypicality robustness (ML audit G4, G5, G7). NOT YET RUN: written
2026-10-02 on a machine without the backend's dependencies; checked with
py_compile only. Run on CI or by hand before relying on it.

Every input here is SYNTHETIC (random numbers shaped like the real feature
vector), never project data. These tests pin the CURRENT behaviour of
app/analytics/atypicality.py; they do not change it. Where the behaviour
depends on sklearn 1.5.2 internals that could not be confirmed without
running it, the test accepts either outcome and says "UNVERIFIED: not run".
No database is used.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import IsolationForest

from app.analytics import atypicality

ANALYTICS = Path(atypicality.__file__).resolve().parent
# Phase 5 scoring modules: the path that produces risk_result.
SCORING_ENTRY = ("fusion", "risk_run", "confidence", "signals", "signals_run", "gate", "compliance")
ML_MODULES = {"atypicality", "atypicality_run", "survival"}
# The numerical bar the existing DB-backed test applies to the stored fit
# (test_phase6_atypicality.py::test_stored_mahalanobis_fit_is_well_conditioned).
ILL_CONDITIONED = 1e8


def _synthetic(n: int, seed: int = 11) -> pd.DataFrame:
    """SYNTHETIC feature matrix shaped like the real one: log-normal amounts,
    integer counts with many ties, skewed day counts. Not project data."""
    rng = np.random.default_rng(seed)
    pay = rng.choice([0, 1, 1, 2, 3], n).astype(float)
    X = pd.DataFrame(
        {
            "log_amount": rng.normal(12.7, 0.8, n),
            "log_peer_deviation_ratio": rng.normal(0.0, 0.4, n),
            "days_rec_to_sanction": np.log1p(rng.gamma(2.0, 30.0, n)),
            "days_sanction_to_end_or_age": np.log1p(rng.gamma(3.0, 100.0, n)),
            "payment_count": np.log1p(pay + rng.choice([0, 0, 0, 1], n)),
            "distinct_payee_count": np.log1p(pay),
            "description_length": np.log1p(rng.integers(20, 300, n).astype(float)),
        },
        index=pd.Index([f"s{i}" for i in range(n)], name="work_key"),
    )
    return X[list(atypicality.FEATURES)]


def _score_quietly(X: pd.DataFrame):
    """Run score() with sklearn's warnings silenced, returning (result, error)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return atypicality.score(X), None
        except (ValueError, np.linalg.LinAlgError) as e:  # LinAlgError subclasses ValueError; listed for clarity
            return None, e


# ---- reproducibility (fixed seed) ------------------------------------------------


def test_synthetic_scores_are_reproducible_with_the_fixed_seed():
    """SYNTHETIC data. Two fits on equal input give identical scores,
    percentiles, contributions, metrics and artifact hashes for both methods."""
    a = atypicality.score(_synthetic(800))
    b = atypicality.score(_synthetic(800))
    assert atypicality.MCD_PARAMS["random_state"] == atypicality.SEED
    assert atypicality.IF_PARAMS["random_state"] == atypicality.SEED
    for m in atypicality.METHODS:
        fa, fb = a[m]["frame"], b[m]["frame"]
        pd.testing.assert_series_equal(fa["score"], fb["score"])
        pd.testing.assert_series_equal(fa["percentile"], fb["percentile"])
        assert list(fa["contributions"]) == list(fb["contributions"])
        assert a[m]["artifact_hash"] == b[m]["artifact_hash"]
        assert a[m]["metrics"] == b[m]["metrics"]


def test_synthetic_isolation_forest_depends_on_its_seed():
    """SYNTHETIC data. The fixed seed is what makes Isolation Forest
    reproducible: the same seed matches, a different one does not."""
    Xe = _synthetic(500).to_numpy(float)
    params = dict(atypicality.IF_PARAMS)
    s1 = IsolationForest(**params).fit(Xe).score_samples(Xe)
    s2 = IsolationForest(**params).fit(Xe).score_samples(Xe)
    s3 = IsolationForest(**{**params, "random_state": atypicality.SEED + 1}).fit(Xe).score_samples(Xe)
    np.testing.assert_array_equal(s1, s2)
    assert not np.array_equal(s1, s3)


# ---- small and all-missing input (G4: no minimum-sample guard exists) --------------


def test_all_missing_input_raises_instead_of_returning_not_evaluated():
    """SYNTHETIC data, every row missing a feature, so 0 works are eligible.
    Current behaviour: score() has no minimum-sample guard (audit G4), so
    MinCovDet.fit receives a (0, 6) array and sklearn's input validation
    raises ValueError ("Found array with 0 sample(s)"). It does NOT return
    an all-"not evaluated" frame. Pinned so that adding a guard is a
    deliberate, visible change."""
    X = _synthetic(50)
    X["days_rec_to_sanction"] = np.nan
    with pytest.raises(ValueError):
        atypicality.score(X)


def test_tiny_input_below_the_feature_count():
    """SYNTHETIC data: 3 eligible works, fewer than the 6 Mahalanobis
    features; the rest not evaluated.

    UNVERIFIED: not run. With n < p the covariance is singular. Depending
    on sklearn 1.5.2's FastMCD internals, score() either raises
    (ValueError / LinAlgError) or returns a degenerate fit. Both outcomes
    are accepted. If it returns, the not-evaluated rows must still be NaN,
    never scored, and the eligible count must be exact."""
    X = _synthetic(10)
    X.iloc[3:, X.columns.get_loc("description_length")] = np.nan
    res, err = _score_quietly(X)
    if err is not None:
        assert isinstance(err, ValueError)
        return
    for m in atypicality.METHODS:
        fr = res[m]["frame"]
        assert int(fr["eligible"].sum()) == 3
        assert fr.loc[~fr["eligible"], "score"].isna().all()
        assert res[m]["metrics"]["n_evaluated"] == 3


def test_small_but_sufficient_input_scores_both_methods():
    """SYNTHETIC data: 40 works, more than the feature count and fewer than
    Isolation Forest's 256-row max_samples cap ("auto" = min(256, n)). Both
    methods should score every work with finite values."""
    res = atypicality.score(_synthetic(40))
    for m in atypicality.METHODS:
        fr = res[m]["frame"]
        assert fr["eligible"].all()
        assert np.isfinite(fr["score"].to_numpy(float)).all()
        assert fr["percentile"].between(0, 1).all()
    assert res["robust_mahalanobis"]["metrics"]["covariance_condition_number"] is not None


# ---- singular covariance (G5: no refusal or fallback exists) -------------------------


def test_singular_covariance_is_not_refused():
    """SYNTHETIC data with one Mahalanobis feature an exact copy of another,
    so the covariance is singular.

    Current behaviour (audit G5): score() has no singular-covariance guard
    or fallback. The precision comes from sklearn's pseudo-inverse, and the
    only signal is the recorded condition number: None when the smallest
    eigenvalue is <= 0, otherwise a very large value. The real-data case
    before the Phase 6 fix returned a fit with a condition number of about
    2e16 rather than raising (docs/model_cards.md:19).

    UNVERIFIED: not run. Whether FastMCD raises or returns on EXACT
    collinearity in sklearn 1.5.2 is not confirmed. Both are accepted. If it
    returns, the condition number must be None or above the 1e8 bar used by
    the stored-fit test, and works are still scored (nothing refuses)."""
    X = _synthetic(600)
    X["description_length"] = X["log_amount"]
    res, err = _score_quietly(X)
    if err is not None:
        assert isinstance(err, ValueError)
        return
    met = res["robust_mahalanobis"]["metrics"]
    cond = met["covariance_condition_number"]
    assert cond is None or cond > ILL_CONDITIONED, cond
    assert met["n_evaluated"] == 600


# ---- isolation from the score ----------------------------------------------------------


def _analytics_imports(path: Path) -> set[str]:
    """Sibling app.analytics modules imported by one file (AST, not text)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 1 and not mod:  # from . import x, y
                names.update(a.name for a in node.names)
            elif node.level == 1:  # from .x import y
                names.add(mod.split(".")[0])
            elif node.level == 0 and mod.startswith("app.analytics"):
                parts = mod.split(".")
                if len(parts) > 2:
                    names.add(parts[2])
                else:
                    names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                if parts[:2] == ["app", "analytics"] and len(parts) > 2:
                    names.add(parts[2])
    return {n for n in names if (ANALYTICS / f"{n}.py").exists()}


def test_no_scoring_module_imports_an_ml_layer_even_indirectly():
    """Follows imports transitively from every Phase 5 scoring module through
    app/analytics. Neither atypicality, atypicality_run nor survival may be
    reachable, so no ML layer output can be read on the risk_result path.
    Stronger than the existing text search, which checks only direct
    mentions (test_phase6_atypicality.py:168)."""
    seen: set[str] = set()
    todo = [m for m in SCORING_ENTRY if (ANALYTICS / f"{m}.py").exists()]
    assert "fusion" in todo and "risk_run" in todo
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        todo.extend(_analytics_imports(ANALYTICS / f"{m}.py") - seen)
    assert not seen & ML_MODULES, sorted(seen & ML_MODULES)


def test_import_walker_detects_an_ml_import():
    """Self-check, so the test above cannot pass vacuously: the walker does
    see atypicality_run's own import of atypicality."""
    assert "atypicality" in _analytics_imports(ANALYTICS / "atypicality_run.py")

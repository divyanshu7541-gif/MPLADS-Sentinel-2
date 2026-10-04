"""
The six BASE signals (BLUEPRINT.md §6 "Signal catalogue"; Phase 4 brief).
Pure functions, no database -- mirrors peers.py's shape. Each `*_signal`
function takes the Phase 3 `frame`/`ctx` (extended with a few Phase
4-only columns, see analytics/signals_run.py::load_signal_frame) and
returns one row per work_key with columns:
    eligible, score, tail_percentile, direction, reliability, dispersion, evidence
`eligible=False` rows have score=None ("not evaluated", never 0 --
BLUEPRINT.md §6 confidence). `evidence` is always a dict (JSON-serialisable
after `json_safe`), even when empty.

What this module deliberately does NOT reuse from the old engine
(backend/app/features/*.py, inspected before writing this):
  - cost.py multiplied a peer-count "reliability" term directly into the
    returned score (0.6*deviation + 0.3*dispersion + 0.1*reliability).
    Every signal here keeps reliability/dispersion in separate columns,
    never summed or multiplied into `score` (see test_no_reliability_
    term_multiplies_any_score, a source-level check, not just a unit test,
    per the Phase 4 acceptance criteria).
  - cost.py's ratio>1 branch only scored amounts ABOVE the peer median
    (silently gave 0 to below-peer amounts). This engine's cost anomaly is
    two-sided; BLUEPRINT.md §6 lists one-sided-vs-two-sided as a GATED,
    still-open policy choice, so this is flagged as provisional below, not
    treated as settled.
  - text.py grouped candidate duplicates by (MP, description) with a
    60-char prefix match, no TF-IDF, no district-authority requirement,
    and no amount/date proximity. This engine blocks on district authority
    (BLUEPRINT.md §6) and scores with character n-gram TF-IDF cosine.
  - concentration.py's constituency_pattern grouped by constituency name,
    which does not exist for Rajya Sabha members. This engine's
    district-authority-pattern signal groups by district_authority_id,
    which every work has regardless of House.
  - lifecycle.py ("stage consistency") compared the SET of stages present
    across an MP+Constituency's works to other MP+Constituency groups --
    not a temporal or duration comparison at all, and, like
    concentration.py, keyed on constituency. This engine's lifecycle_delay
    signal compares a work's own sanction-to-now duration and payment
    pace against peer works, never against a raw stage set.

Provisional constants (BLUEPRINT.md §6 lists calibration as a still-gated
policy decision): every MIN_*/*_TOL/*_K constant below is a documented,
reasoned starting point, not a tuned value -- flagged in
docs/phase4_signals_report.md for review, the same posture Phase 3 used
for the refinement-rule ambiguity.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.special import erf
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import peers

# ---- shared -----------------------------------------------------------

MIN_OTHER_PEERS = 3  # smallest peer-entity count anything here treats as a real distribution


# Score mapping per signal. Phase 5a step 3 moved portfolio, district-
# authority and temporal to the EMPIRICAL mapping below (v1); Phase 5c moved
# cost anomaly and lifecycle delay too (v2). near_duplicate is a cosine
# similarity, not a z, and is unchanged. Recorded in every run's notes and in
# the gate report so a gate can only cover runs made with the calibration it
# evaluated.
SIGNAL_CALIBRATION = "empirical-percentile-v2"
EMPIRICAL_SIGNALS = (
    "cost_anomaly",
    "portfolio_concentration",
    "district_authority_pattern",
    "temporal_anomaly",
    "lifecycle_delay",
)
EMPIRICAL_ROUND = 9  # |z| rounded before ranking so float noise cannot split a tie


def empirical_tail_score(stat: pd.Series) -> pd.Series:
    """Score = the share of evaluated works whose statistic is STRICTLY less
    extreme than this work's: the empirical counterpart of z_to_score, which
    is P(|Z| < |z|) under a standard normal. Calibrated by construction:
    a score >= 0.9 means "more extreme than at least 90% of evaluated
    works", whatever the statistic's real distribution.

    `stat` is the non-negative extremeness statistic (|z|, or the one-sided
    burst z); NaN = not evaluated and stays NaN. Ties share the lower score
    (rank method "min"), so a statistic at its minimum -- e.g. a work in an
    ordinary week, burst z = 0 -- scores 0. Deterministic and independent of
    row order. The reference population is every evaluated work in the run
    (both Houses together), so a work's score is relative to that
    population and can move when other works enter or leave it.
    """
    x = pd.to_numeric(stat, errors="coerce").astype(float).round(EMPIRICAL_ROUND)
    valid = x.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=stat.index)
    ranks = valid.rank(method="min")
    return ((ranks - 1.0) / len(valid)).reindex(stat.index)


LIFECYCLE_CAP = 1.0  # lifecycle's combined statistic tops out here (age beyond every completed peer)


def lifecycle_rank_key(combined: pd.Series, age_days: pd.Series) -> pd.Series:
    """Ranking key for lifecycle_delay (Phase 5c, owner's decision): the
    combined statistic, with works TIED AT THE CAP ordered by days open.
    The age component is an exceedance rank within completed peers, so every
    open work older than all of them sits at exactly 1.0 (15.8% of scored
    works in Snapshot A). A plain rank would give that whole group the same,
    LOWER score, and the most delayed works would never reach 0.9. Below the
    cap the key is the combined statistic alone. Built as an exact integer
    key: dense rank of the rounded statistic, then days open as the
    secondary order at the cap only."""
    c = pd.to_numeric(combined, errors="coerce").astype(float).round(EMPIRICAL_ROUND)
    dense = c.rank(method="dense")
    at_cap = c >= LIFECYCLE_CAP
    secondary = pd.to_numeric(age_days, errors="coerce").fillna(0).clip(lower=0).where(at_cap, 0.0)
    width = float(secondary.max()) + 1.0 if secondary.notna().any() else 1.0
    return (dense * width + secondary).where(c.notna())


def z_to_score(z: pd.Series | np.ndarray) -> np.ndarray:
    """Monotone, saturating map from a two-sided robust z (or z-like
    standardised residual) to a [0, 1] "how anomalous" score:
    score = erf(|z| / sqrt(2)), the two-sided normal tail-probability
    transform (2*Phi(|z|) - 1). Descriptive, not a real p-value (amounts
    are not normally distributed) -- chosen because it is a well-known,
    smoothly saturating, strictly monotone function of |z| with no extra
    magic constant to tune, satisfying BLUEPRINT.md §6's "mapped to a 0-1
    score by a documented monotone function." Strictly increasing in |z|,
    which is what the required property test (raising a work's amount
    above its peer median never lowers its cost anomaly score) needs.
    """
    z = np.asarray(z, dtype=float)
    # erf() is mathematically in [0, 1] here, but every score this module
    # produces is stored under a DB CHECK constraint on exactly that range,
    # and a sibling floating-point overshoot was already found in
    # production (cosine similarity computing 1.0000000000000002) --
    # clipped at the source so every caller of z_to_score is covered.
    return np.clip(erf(np.abs(z) / math.sqrt(2)), 0.0, 1.0)


# A standardised residual (share_own - peer_mean)/peer_std explodes
# whenever peer_std is zero or close to it -- "every OTHER peer showed
# (near) the identical value", usually all zero. Real-data check on
# portfolio_concentration/district_authority_pattern: with ~115 activity
# types spread over modest portfolios, "no OTHER in-state peer ever did
# this type at all" is the case for roughly HALF of all eligible rows, not
# a rare corner case. Three approaches were tried and rejected before this
# one, each verified against the real dataset, not just reasoned about:
#   1. An unfloored (or near-zero-constant-floored) peer std let a single
#      ordinary work produce a z in the thousands.
#   2. A "one work" resolution floor (1/portfolio_size) tamed the exact
#      std==0 case but still let z grow roughly linearly with the
#      entity's own raw count (z ~ count) for a NONzero-but-tiny std, so
#      a large-but-unremarkable portfolio still saturated the score.
#   3. A fixed magnitude (DEGENERATE_Z) applied only when std was EXACTLY
#      zero handled that one case but missed the much more common
#      near-zero case (a single peer with a token nonzero count still
#      produces a tiny, non-exactly-zero std) -- max|z| observed was 1532.
# A share is mathematically bounded in [0, 1] (it is a fraction of a
# portfolio), so the fix that actually GUARANTEES boundedness, regardless
# of portfolio size, peer count, or how close to zero the true variance
# happens to be, is a FIXED ABSOLUTE floor on the standard deviation
# itself: |z| = |share_own - peer_mean| / max(peer_std, SHARE_STD_FLOOR)
# can never exceed 1 / SHARE_STD_FLOOR. Real peer variation (when peer_std
# exceeds the floor) still drives z as normal. SHARE_STD_FLOOR's value is
# provisional (BLUEPRINT.md §6 lists calibration itself as a still-gated
# policy change): 2 percentage points, chosen so the worst case
# (z = 1/0.02 = 50) still saturates z_to_score but stays a finite,
# documented, share-scale-derived number rather than an arbitrary one.
SHARE_STD_FLOOR = 0.02


def _share_residual_z(own: float, peer_mean: float, peer_std: float) -> float:
    return (own - peer_mean) / max(peer_std, SHARE_STD_FLOOR)


def json_safe(value):
    """Recursively convert numpy/pandas scalars to plain Python types and
    NaN/NaT to None, so a dict can go straight into a JSONB column."""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (np.floating,)):
        v = float(value)
        return None if math.isnan(v) else v
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return value


def _empty_result(index: pd.Index) -> pd.DataFrame:
    out = pd.DataFrame(index=index)
    out["eligible"] = False
    out["score"] = np.nan
    out["tail_percentile"] = np.nan
    out["direction"] = pd.Series(pd.NA, index=index, dtype="object")
    out["reliability"] = np.nan
    out["dispersion"] = np.nan
    out["evidence"] = [dict() for _ in range(len(index))]
    return out


def _reliability_from_counts(n_peers: pd.Series, n_other_entities: pd.Series) -> pd.Series:
    """Saturating [0,1] composite of "how many usable peers" and "how many
    distinct other entities (MPs/authorities) contributed" -- the same two
    inputs BLUEPRINT.md §6 confidence names ("usable peers, distinct MPs,
    level"). Deliberately never read by any score computation in this
    module (see module docstring)."""
    peer_term = np.minimum(1.0, np.log1p(n_peers.clip(lower=0)) / math.log1p(80))
    entity_term = np.minimum(1.0, np.log1p(n_other_entities.clip(lower=0)) / math.log1p(20))
    return np.minimum(1.0, 0.7 * peer_term + 0.3 * entity_term)


# ---- 1. Cost anomaly (25%) --------------------------------------------

# Floor on the LOG-scale peer MAD (converted to an approximate std via the
# usual 1.4826 factor): amounts cluster heavily on round figures
# (BLUEPRINT.md §6: 45.3% multiples of Rs 50,000, 9.9% exactly Rs 5,00,000),
# so a peer group can have a near-zero raw MAD; without a floor, a work
# only slightly off that round figure would produce an enormous z. 5% is a
# reasoned starting point ("indistinguishable from typical, at the
# rounding granularity these amounts are recorded at"), not a fitted value.
MAD_TO_STD = 1.4826
MIN_LOG_SCALE = math.log(1.05)


def cost_anomaly_signal(frame: pd.DataFrame, ctx: pd.DataFrame) -> pd.DataFrame:
    """Robust two-sided z on log(amount) against the work's own assigned
    peer level (whichever of L1/L2/L3 Phase 3 assigned -- BLUEPRINT.md §6's
    signal-catalogue table says "against level-1 peers", but Phase 3's own
    "Peer hierarchy" section built the L1->L2->L3 fallback specifically so
    a peer comparison exists for works that don't reach Level 1; using
    whichever level was actually assigned, rather than leaving ~32% of
    works unevaluated, is this engine's reading -- flagged for review).

    Two-sided: BLUEPRINT.md §6 lists one-sided-vs-two-sided cost distance
    as a still-gated policy choice. This engine scores unusually CHEAP
    work the same as unusually expensive work, on the view that "how
    anomalous" should not silently ignore below-peer amounts; direction is
    always stored so a one-sided read is one filter away if gate G3
    prefers it.
    """
    eligible = ctx["level"].notna() & frame["is_usable"]
    if not eligible.any():
        return _empty_result(frame.index)

    log_frame = frame[["is_usable"]].copy()
    log_frame["amount"] = np.where(frame["is_usable"], np.log(frame["amount"].clip(lower=1e-9)), np.nan)

    # `ctx["group_key"]` is heterogeneous: each row holds the key string
    # for WHATEVER level THAT row itself landed on, so two works that both
    # belong to the same Level 2 peer group (same type+state) but were
    # individually assigned different levels (one qualified at L1 in its
    # own FY, one fell back to L2) hold two DIFFERENT strings and would
    # never pool together if grouped on that column directly -- silently
    # shrinking, sometimes to zero, the peer set for every work that
    # didn't land on Level 1. The fix is to rebuild each level's group key
    # fresh across the WHOLE frame (exactly as peers.build_context does
    # internally) and pool each row against its OWN assigned level's true
    # population, not against only the other rows that fell back the same
    # way it did.
    stats = pd.DataFrame(np.nan, index=frame.index, columns=["median", "mad"])
    for level, keys in peers.LEVEL_KEYS.items():
        take = eligible & (ctx["level"] == level)
        if not take.any():
            continue
        gkey = peers.group_key(frame, keys)
        level_stats = peers.loo_stats(log_frame, gkey, take)
        stats.loc[take, ["median", "mad"]] = level_stats.loc[take, ["median", "mad"]]

    log_amt = log_frame["amount"]
    scale = np.maximum(MAD_TO_STD * stats["mad"], MIN_LOG_SCALE)
    z = (log_amt - stats["median"]) / scale
    # Phase 5c: empirical percentile rank of |z| among evaluated works
    # (two-sided: unusually cheap scores like unusually expensive).
    score = empirical_tail_score(z.abs().where(eligible))

    out = _empty_result(frame.index)
    out.loc[eligible, "eligible"] = True
    out.loc[eligible, "score"] = score[eligible]
    out.loc[eligible, "tail_percentile"] = score[eligible]
    above = frame["amount"] > ctx["peer_median"]
    below = frame["amount"] < ctx["peer_median"]
    direction = np.where(above, "above", np.where(below, "below", None))
    out.loc[eligible, "direction"] = pd.Series(direction, index=frame.index)[eligible]
    out.loc[eligible, "dispersion"] = stats.loc[eligible, "mad"]
    out.loc[eligible, "reliability"] = _reliability_from_counts(
        ctx.loc[eligible, "n_usable_excl_self"], ctx.loc[eligible, "distinct_other_mps"]
    )

    for idx in frame.index[eligible]:
        out.at[idx, "evidence"] = {
            "level": ctx.at[idx, "level"],
            "group_key": ctx.at[idx, "group_key"],
            "amount_used": float(frame.at[idx, "amount"]),
            "amount_basis": frame.at[idx, "amount_basis"],
            "peer_median": None if pd.isna(ctx.at[idx, "peer_median"]) else float(ctx.at[idx, "peer_median"]),
            "ratio_to_peer_median": (
                None
                if pd.isna(ctx.at[idx, "peer_median"]) or ctx.at[idx, "peer_median"] == 0
                else float(frame.at[idx, "amount"] / ctx.at[idx, "peer_median"])
            ),
            "z_log_scale": float(z.at[idx]),
            "n_usable_excl_self": int(ctx.at[idx, "n_usable_excl_self"]),
            "distinct_other_mps": int(ctx.at[idx, "distinct_other_mps"]),
        }
    return out


# ---- 2. Near-duplicate work (20%) --------------------------------------

NGRAM_N = 4  # char n-gram width, provisional (no hand-labelled pairs to tune against yet)
MIN_DF = 2
AMOUNT_TOL = 0.5  # relative amount gap beyond which the amount-proximity factor is 0
DATE_TOL_DAYS = 365.0  # date gap beyond which the date-proximity factor is 0
MAX_BLOCK_SIZE = 500  # safety cap on a single candidate block's pairwise comparison


def _proximity_factor(diff: np.ndarray, tol: float) -> np.ndarray:
    """1 at zero gap, linearly down to 0 at `tol`, 0 beyond. NaN gap
    (missing amount/date on either side of the pair) is treated as neutral
    (1.0) -- a missing field should not itself suppress a text match."""
    factor = np.where(np.isnan(diff), 1.0, np.clip(1.0 - diff / tol, 0.0, 1.0))
    return factor


def _datetime_gap_days(dates: pd.Series) -> np.ndarray:
    """Symmetric matrix of |gap in days| between every pair in `dates`,
    NaN wherever either side is NaT -- explicit mask, not reliant on how a
    given numpy version happens to cast NaT through datetime64->float64."""
    is_nat = dates.isna().to_numpy()
    numeric = dates.to_numpy(dtype="datetime64[ns]").astype("int64").astype("float64")
    numeric[is_nat] = np.nan
    return np.abs(numeric[:, None] - numeric[None, :]) / 8.64e13  # ns -> days


def _block_best_matches(
    work_keys: list, pos: dict, X, amount: pd.Series, sanction_date: pd.Series
) -> dict[str, tuple[float, str, float]]:
    """Best (combined_score, matched_work_key, cosine_similarity) per
    work_key within one candidate block. `pos` maps work_key -> row
    position in the globally fit TF-IDF matrix `X`."""
    keys = work_keys[:MAX_BLOCK_SIZE] if len(work_keys) > MAX_BLOCK_SIZE else work_keys
    idx = [pos[k] for k in keys]
    # TF-IDF vectors here are non-negative (char n-gram counts), so cosine
    # similarity is mathematically in [0, 1] -- but sklearn's dot-product
    # computation can round a near-identical pair fractionally above 1.0
    # (observed on real data: 1.0000000000000002), which then fails the
    # DB's score-range CHECK constraint. Clipped once, right at the source.
    cos = np.clip(cosine_similarity(X[idx]), 0.0, 1.0)
    amt = amount.reindex(keys).to_numpy(dtype=float)
    amt_scale = np.maximum(np.maximum(np.abs(amt[:, None]), np.abs(amt[None, :])), 1.0)
    amt_gap = np.abs(amt[:, None] - amt[None, :]) / amt_scale
    date_gap = _datetime_gap_days(sanction_date.reindex(keys))

    combined = cos * _proximity_factor(amt_gap, AMOUNT_TOL) * _proximity_factor(date_gap, DATE_TOL_DAYS)
    combined = np.clip(combined, 0.0, 1.0)  # belt-and-braces; each factor is already clipped
    np.fill_diagonal(combined, -1.0)  # never match a work against itself (by key, not position)

    best_j = np.argmax(combined, axis=1)
    best_val = combined[np.arange(len(keys)), best_j]
    out = {}
    for i, k in enumerate(keys):
        if best_val[i] > 0:
            out[k] = (float(best_val[i]), keys[best_j[i]], float(cos[i, best_j[i]]))
    return out


def near_duplicate_signal(frame: pd.DataFrame) -> pd.DataFrame:
    """Character n-gram TF-IDF cosine on `description_normalized`, blocked
    on (district_authority_id, mp) and (district_authority_id,
    activity_type_id) -- "same district authority and MP or type"
    (BLUEPRINT.md §6). IDF is fit over the WHOLE corpus so a nationally
    common phrase is naturally down-weighted ("national phrase-frequency
    discount"); cosine similarity is only ever computed within a block.

    Grain is one row per work_key: `work.work_key` is the database primary
    key, so this frame (like `work` itself) structurally cannot hold more
    than one row for the same work -- a work's own recommended/sanctioned/
    completed rows live in `work_state`, a different table entirely, at a
    different grain, never fed into this function. That is what makes the
    old engine's defect (comparing a work's own per-stage rows against
    each other) impossible here, by construction rather than by a runtime
    check. What this function's own runtime logic guarantees, and what is
    tested directly, is narrower and unconditional: no candidate matrix
    ever matches a row against itself, by index label (`np.fill_diagonal`
    below), regardless of blocking; a distinct work_key that happens to
    look identical is still eligible to be a genuine match.
    """
    desc = frame["description_normalized"].fillna("")
    has_text = desc.str.len() > 0
    has_authority = frame["district_authority_id"].notna()
    eligible = has_text & has_authority
    if not eligible.any():
        return _empty_result(frame.index)

    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(NGRAM_N, NGRAM_N), min_df=MIN_DF)
    X = vectorizer.fit_transform(desc)
    pos = {k: i for i, k in enumerate(frame.index)}

    # (block column, basis label, extra row filter). MP-blocking excludes
    # blank MP names -- otherwise every work from an authority with an
    # unknown MP would form one giant spurious "same MP" block.
    has_mp = frame["mp"].str.len() > 0
    block_specs = [
        ("mp", "mp", eligible & has_mp),
        ("activity_type_id", "activity_type", eligible),
    ]

    best: dict[str, tuple[float, str, float, str]] = {}
    block_size: dict[str, int] = {}
    for block_col, basis, row_filter in block_specs:
        grouped = frame.loc[row_filter].groupby(["district_authority_id", block_col]).groups
        for _, idx in grouped.items():
            if len(idx) < 2:
                continue
            keys = list(idx)
            matches = _block_best_matches(keys, pos, X, frame["amount"], frame["sanction_date"])
            for k, (combined, other, cos) in matches.items():
                if k not in best or combined > best[k][0]:
                    best[k] = (combined, other, cos, basis)
                    block_size[k] = len(keys)

    out = _empty_result(frame.index)
    out.loc[eligible, "eligible"] = True
    out.loc[eligible, "score"] = 0.0
    out.loc[eligible, "tail_percentile"] = 0.0
    for k, (combined, other, cos, basis) in best.items():
        out.at[k, "score"] = combined
        out.at[k, "tail_percentile"] = combined
        n_candidates = block_size.get(k, 0)
        out.at[k, "reliability"] = float(np.minimum(1.0, np.log1p(n_candidates) / math.log1p(20)))
        amt_a, amt_b = frame.at[k, "amount"], frame.at[other, "amount"]
        date_a, date_b = frame.at[k, "sanction_date"], frame.at[other, "sanction_date"]
        out.at[k, "evidence"] = {
            "matched_work_key": other,
            "match_basis": basis,
            "cosine_similarity": cos,
            "combined_score": combined,
            "amount": None if pd.isna(amt_a) else float(amt_a),
            "matched_amount": None if pd.isna(amt_b) else float(amt_b),
            "date_gap_days": (None if pd.isna(date_a) or pd.isna(date_b) else abs((date_a - date_b).days)),
            "district_authority_id": int(frame.at[k, "district_authority_id"]),
            "n_candidates_in_block": n_candidates,
        }
    return out


# ---- 3. Portfolio concentration (10%) ----------------------------------

MIN_PORTFOLIO_SIZE = 10  # an MP's own total usable works, before a type-share is meaningful


def portfolio_concentration_signal(frame: pd.DataFrame) -> pd.DataFrame:
    """An MP's share of one work type within their own portfolio, as a
    standardised residual against the distribution of that same share
    across OTHER MPs active in the same state (BLUEPRINT.md §6). "Same
    state" is each MP's modal work state (the state most of their own
    works sit in) -- robust to a handful of misclassified works, and
    avoids a separate join to `tenure`/`person` for this purpose.
    """
    usable = frame[frame["is_usable"] & frame["mp"].str.len().gt(0)]
    if usable.empty:
        return _empty_result(frame.index)

    def _modal_state(s: pd.Series):
        m = s.mode()
        return m.iloc[0] if not m.empty else pd.NA

    mp_state = usable.groupby("mp")["state_id"].agg(_modal_state)
    mp_total = usable.groupby("mp").size()
    mp_type = usable.groupby(["mp", "activity_type_id"]).size()

    eligible_mps = mp_total[mp_total >= MIN_PORTFOLIO_SIZE].index
    if len(eligible_mps) == 0:
        return _empty_result(frame.index)

    rows = []
    for state, mps_in_state in mp_state[mp_state.notna()].groupby(mp_state).groups.items():
        mps_in_state = [m for m in mps_in_state if m in eligible_mps]
        if len(mps_in_state) < MIN_OTHER_PEERS + 1:
            continue
        for atype in usable.loc[usable["mp"].isin(mps_in_state), "activity_type_id"].dropna().unique():
            shares = pd.Series({m: mp_type.get((m, atype), 0) / mp_total[m] for m in mps_in_state})
            for m in mps_in_state:
                others = shares.drop(index=m)
                mean, std = others.mean(), others.std(ddof=0)
                z = _share_residual_z(shares[m], mean, std)
                rows.append(
                    {
                        "mp": m,
                        "state_id": state,
                        "activity_type_id": atype,
                        "z": z,
                        "mp_share": shares[m],
                        "peer_mean": mean,
                        "peer_std": std,
                        "n_peer_mps": len(others),
                        "mp_total": int(mp_total[m]),
                        "mp_type_count": int(mp_type.get((m, atype), 0)),
                    }
                )
    if not rows:
        return _empty_result(frame.index)

    # Each work carries the residual for ITS OWN work type -- the MP's share
    # of this work's type versus in-state peers' share of that same type.
    # (Phase 4 originally attached the MP's single most extreme type-residual
    # to every one of the MP's works, so a road work inherited the score of
    # the MP's unusual drainage share; with ~115 types almost every MP has
    # one extreme type, which saturated this signal at ~1.0 for nearly all
    # works. Same attribution rule as BLUEPRINT.md §6's "each score attaches
    # to its own row by key".)
    per_mp_type = pd.DataFrame(rows)
    own = frame[["mp", "activity_type_id"]].reset_index()
    joined = own.merge(per_mp_type, on=["mp", "activity_type_id"], how="left").set_index(own.columns[0])
    joined = joined.reindex(frame.index)  # align by work_key label, never position
    eligible_mask = joined["z"].notna()

    out = _empty_result(frame.index)
    if not eligible_mask.any():
        return out
    z = joined["z"]
    score = empirical_tail_score(z.abs().where(eligible_mask))
    out.loc[eligible_mask, "eligible"] = True
    out.loc[eligible_mask, "score"] = score[eligible_mask]
    out.loc[eligible_mask, "tail_percentile"] = score[eligible_mask]
    out.loc[eligible_mask, "direction"] = pd.Series(
        np.where(z > 0, "above", np.where(z < 0, "below", None)), index=frame.index
    )[eligible_mask]
    out.loc[eligible_mask, "dispersion"] = joined.loc[eligible_mask, "peer_std"]
    n_peer = joined.loc[eligible_mask, "n_peer_mps"]
    out.loc[eligible_mask, "reliability"] = _reliability_from_counts(n_peer, n_peer)
    sub = joined.loc[eligible_mask]
    out.loc[eligible_mask, "evidence"] = pd.Series(
        [
            {
                "mp": r.mp,
                "state_id": None if pd.isna(r.state_id) else int(r.state_id),
                "activity_type_id": int(r.activity_type_id),
                "mp_type_share": float(r.mp_share),
                "peer_mean_share": float(r.peer_mean),
                "peer_std_share": float(r.peer_std),
                "z": float(r.z),
                "n_peer_mps": int(r.n_peer_mps),
                "mp_total_works": int(r.mp_total),
                "mp_type_count": int(r.mp_type_count),
            }
            for r in sub.itertuples()
        ],
        index=sub.index,
        dtype="object",
    )
    return out


# ---- 4. District-authority pattern (10%) -------------------------------

MIN_AUTHORITY_PORTFOLIO = 10


def district_authority_pattern_signal(frame: pd.DataFrame) -> pd.DataFrame:
    """A district authority's work-type share AND mean amount for that
    type, each a standardised residual against other authorities in the
    same state (BLUEPRINT.md §6) -- replaces the old engine's
    constituency-name grouping, which does not exist for Rajya Sabha.
    The final score is the stronger (max |z|) of the two components; both
    are kept in evidence.
    """
    usable = frame[frame["district_authority_id"].notna() & frame["state_id"].notna()]
    if usable.empty:
        return _empty_result(frame.index)

    auth_total = usable.groupby("district_authority_id").size()
    auth_state = usable.groupby("district_authority_id")["state_id"].first()
    auth_type = usable.groupby(["district_authority_id", "activity_type_id"]).size()
    amt_usable = usable[usable["is_usable"]]
    # Mean LOG amount per (authority, type): the amount comparison is a ratio
    # comparison, on the same log scale and with the same relative floor
    # (MIN_LOG_SCALE) as cost_anomaly_signal. A flat Rs 1 floor on a raw-rupee
    # std (the Phase 4 original) is the same degenerate-variance defect as the
    # share residual's: peers sharing one round-figure mean gave std ~0 and an
    # unbounded z_amount.
    auth_type_amt = (
        np.log(amt_usable["amount"].clip(lower=1e-9))
        .groupby([amt_usable["district_authority_id"], amt_usable["activity_type_id"]])
        .mean()
    )

    eligible_auth = auth_total[auth_total >= MIN_AUTHORITY_PORTFOLIO].index
    if len(eligible_auth) == 0:
        return _empty_result(frame.index)

    rows = []
    for state, auths in auth_state.groupby(auth_state).groups.items():
        auths = [a for a in auths if a in eligible_auth]
        if len(auths) < MIN_OTHER_PEERS + 1:
            continue
        in_state = usable["district_authority_id"].isin(auths)
        types_in_state = usable.loc[in_state, "activity_type_id"].dropna().unique()
        for atype in types_in_state:
            shares = pd.Series({a: auth_type.get((a, atype), 0) / auth_total[a] for a in auths})
            amts = pd.Series({a: auth_type_amt.get((a, atype), np.nan) for a in auths})
            for a in auths:
                other_shares = shares.drop(index=a)
                s_mean, s_std = other_shares.mean(), other_shares.std(ddof=0)
                z_share = _share_residual_z(shares[a], s_mean, s_std)

                other_amts = amts.drop(index=a).dropna()
                z_amount = np.nan
                if pd.notna(amts[a]) and len(other_amts) >= MIN_OTHER_PEERS:
                    a_mean, a_std = other_amts.mean(), other_amts.std(ddof=0)
                    z_amount = (amts[a] - a_mean) / max(a_std, MIN_LOG_SCALE)

                rows.append(
                    {
                        "authority": a,
                        "state_id": state,
                        "activity_type_id": atype,
                        "z_share": z_share,
                        "z_amount": z_amount,
                        "share": shares[a],
                        "peer_mean_share": s_mean,
                        "peer_std_share": s_std,
                        "n_peer_auth": len(other_shares),
                        "auth_total": int(auth_total[a]),
                    }
                )
    if not rows:
        return _empty_result(frame.index)

    per_auth = pd.DataFrame(rows)
    per_auth["z_final"] = np.where(
        (per_auth["z_amount"].notna()) & (per_auth["z_amount"].abs() > per_auth["z_share"].abs()),
        per_auth["z_amount"],
        per_auth["z_share"],
    )
    per_auth["component"] = np.where(per_auth["z_final"] == per_auth["z_share"], "share", "amount")

    # Each work carries the pattern for ITS OWN authority AND work type (the
    # authority's share / mean amount of this work's type versus other
    # in-state authorities) -- not the authority's single most extreme type,
    # which is what Phase 4 originally attached to every work and which
    # saturated this signal for nearly all works (see
    # portfolio_concentration_signal's matching comment).
    per_auth = per_auth.rename(columns={"authority": "district_authority_id"})
    own = frame[["district_authority_id", "activity_type_id"]].reset_index()
    joined = own.merge(per_auth, on=["district_authority_id", "activity_type_id"], how="left")
    joined = joined.set_index(own.columns[0]).reindex(frame.index)  # label alignment
    eligible_mask = joined["z_final"].notna()

    out = _empty_result(frame.index)
    if not eligible_mask.any():
        return out
    zf = joined["z_final"]
    score = empirical_tail_score(zf.abs().where(eligible_mask))
    out.loc[eligible_mask, "eligible"] = True
    out.loc[eligible_mask, "score"] = score[eligible_mask]
    out.loc[eligible_mask, "tail_percentile"] = score[eligible_mask]
    out.loc[eligible_mask, "direction"] = pd.Series(
        np.where(zf > 0, "above", np.where(zf < 0, "below", None)), index=frame.index
    )[eligible_mask]
    out.loc[eligible_mask, "dispersion"] = joined.loc[eligible_mask, "peer_std_share"]
    n_peer = joined.loc[eligible_mask, "n_peer_auth"]
    out.loc[eligible_mask, "reliability"] = _reliability_from_counts(n_peer, n_peer)
    sub = joined.loc[eligible_mask]
    out.loc[eligible_mask, "evidence"] = pd.Series(
        [
            {
                "district_authority_id": int(r.district_authority_id),
                "state_id": None if pd.isna(r.state_id) else int(r.state_id),
                "activity_type_id": int(r.activity_type_id),
                "driving_component": r.component,
                "type_share": float(r.share),
                "peer_mean_share": float(r.peer_mean_share),
                "peer_std_share": float(r.peer_std_share),
                "z_share": float(r.z_share),
                "z_amount": None if pd.isna(r.z_amount) else float(r.z_amount),
                "n_peer_authorities": int(r.n_peer_auth),
                "authority_total_works": int(r.auth_total),
            }
            for r in sub.itertuples()
        ],
        index=sub.index,
        dtype="object",
    )
    return out


# ---- 5. Temporal anomaly (10%) -----------------------------------------

BATCH_DAY_MIN_COUNT = 50  # a day needs at least this many national events to even be considered a batch day
BATCH_DAY_K = 6.0  # robust-outlier multiplier on the national daily-count series
MIN_EVENTS_FOR_CADENCE = 5
MIN_WEEKS_ACTIVE = 2
CADENCE_K = 4.0


def _batch_days(dates: pd.Series) -> set:
    """National days whose event count is a robust outlier on the national
    daily-count series -- BLUEPRINT.md §6's example is 594 completions on
    one day in Snapshot B; detected generically here via median + K*MAD on
    Snapshot A's own daily counts so it is not tied to that one figure."""
    counts = dates.dropna().dt.normalize().value_counts()
    if counts.empty:
        return set()
    med = counts.median()
    mad = (counts - med).abs().median()
    threshold = med + BATCH_DAY_K * max(mad, 1.0)
    return set(counts[(counts > threshold) & (counts >= BATCH_DAY_MIN_COUNT)].index)


def _own_week_burst(dates: pd.Series, entities: pd.Series, batch_days: set) -> pd.DataFrame:
    """Per WORK: how busy its own entity (MP or district authority) was in
    the ISO week of this work's own date, against that entity's typical
    active week (never a peer comparison -- "against their normal
    cadence", BLUEPRINT.md §6). Works dated on a national batch day are not
    evaluated for this date field (the batch day says nothing about the
    entity), and batch-day events are removed from every count first.

    Phase 4 originally attached the entity's single worst week to EVERY
    work of that entity, so a work recommended in a quiet week inherited
    the entity's burst score from some other week -- and since almost every
    busy MP has one heavy week, the signal sat at ~1.0 for nearly all works.
    Returns (indexed by work_key): z, week, count, typical, n_weeks.
    """
    d = pd.DataFrame({"e": entities, "d": dates}).dropna()
    d = d[~d["d"].dt.normalize().isin(batch_days)]
    cols = ["z", "week", "count", "typical", "n_weeks"]
    if d.empty:
        return pd.DataFrame(columns=cols)
    d["week"] = d["d"].dt.to_period("W")
    weekly = d.groupby(["e", "week"]).size().rename("count").reset_index()
    stats = weekly.groupby("e")["count"].agg(n_events="sum", n_weeks="size", typical="median")
    stats["mad"] = (
        weekly.merge(stats[["typical"]], left_on="e", right_index=True)
        .assign(dev=lambda x: (x["count"] - x["typical"]).abs())
        .groupby("e")["dev"]
        .median()
    )
    stats = stats[(stats["n_events"] >= MIN_EVENTS_FOR_CADENCE) & (stats["n_weeks"] >= MIN_WEEKS_ACTIVE)]
    if stats.empty:
        return pd.DataFrame(columns=cols)
    stats["scale"] = np.maximum(CADENCE_K * np.maximum(stats["mad"], 1.0), 1.0)

    work = d.reset_index().rename(columns={d.index.name or "index": "work_key"})
    work = work.merge(weekly, on=["e", "week"], how="left").merge(stats, left_on="e", right_index=True)
    work["z"] = np.maximum((work["count"] - work["typical"]) / work["scale"], 0.0)
    work["week"] = work["week"].astype(str)
    return work.set_index("work_key")[cols]


def temporal_anomaly_signal(frame: pd.DataFrame) -> pd.DataFrame:
    """Bursts of recommendations or sanctions per MP and per district
    authority against each entity's own normal cadence, with national
    batch days removed first (BLUEPRINT.md §6), scored for each work in
    its OWN week (see _own_week_burst). The strongest of the (up to four)
    date-field x entity combinations is attached. One-sided by
    construction (a burst is inherently "more than usual"); direction is
    'above' whenever eligible, for schema consistency.
    """
    rec_batch = _batch_days(frame["recommended_date"])
    san_batch = _batch_days(frame["sanction_date"])

    combos = [
        ("recommended_date", "mp", rec_batch),
        ("sanction_date", "mp", san_batch),
        ("recommended_date", "district_authority_id", rec_batch),
        ("sanction_date", "district_authority_id", san_batch),
    ]
    best = pd.DataFrame(index=frame.index, columns=["z", "week", "count", "typical", "n_weeks", "combo"])
    best["z"] = np.nan
    for i, (date_col, entity_col, batch) in enumerate(combos):
        per_work = _own_week_burst(frame[date_col], frame[entity_col], batch).reindex(frame.index)
        better = per_work["z"].notna() & (best["z"].isna() | (per_work["z"] > best["z"]))
        if better.any():
            best.loc[better, ["z", "week", "count", "typical", "n_weeks"]] = per_work.loc[
                better, ["z", "week", "count", "typical", "n_weeks"]
            ]
            best.loc[better, "combo"] = i

    out = _empty_result(frame.index)
    eligible = best["z"].notna()
    if not eligible.any():
        return out
    z = best["z"].astype(float)
    score = empirical_tail_score(z.where(eligible))
    out.loc[eligible, "eligible"] = True
    out.loc[eligible, "score"] = score[eligible]
    out.loc[eligible, "tail_percentile"] = score[eligible]
    out.loc[eligible, "direction"] = "above"
    n_weeks = best.loc[eligible, "n_weeks"].astype(float)
    out.loc[eligible, "reliability"] = _reliability_from_counts(n_weeks, n_weeks)

    excluded = {
        "recommended": sorted(str(x.date()) for x in rec_batch),
        "sanction": sorted(str(x.date()) for x in san_batch),
    }
    sub = best.loc[eligible]
    evidence = []
    for work_key, r in zip(sub.index, sub.itertuples(index=False)):
        date_col, entity_col, _ = combos[int(r.combo)]
        field = date_col.removesuffix("_date")
        entity = frame.at[work_key, entity_col]
        evidence.append(
            {
                "entity_type": "mp" if entity_col == "mp" else "district_authority",
                "entity": entity if entity_col == "mp" else int(entity),
                "date_field": field,
                "own_week": r.week,
                "own_week_count": int(r.count),
                "typical_weekly_count": float(r.typical),
                "n_weeks_active": int(r.n_weeks),
                "z": float(r.z),
                "batch_days_excluded": excluded[field],
            }
        )
    out.loc[eligible, "evidence"] = pd.Series(evidence, index=sub.index, dtype="object")
    return out


# ---- 6. Lifecycle delay (10%) ------------------------------------------

MIN_COMPLETED_PEERS_FOR_DURATION = 10


def _group_membership(mask: pd.Series, group: pd.Series) -> dict:
    """{group_value: [work_key, ...]} for the rows where `mask` is True
    and `group` is not null -- a plain dict, so callers don't need to
    reason about pandas' groupby-object semantics."""
    sub = group[mask & group.notna()]
    return {g: list(idx) for g, idx in sub.groupby(sub).groups.items()}


def lifecycle_delay_signal(frame: pd.DataFrame, ctx: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Two components, both scoped to OPEN (sanctioned, not completed)
    works -- BLUEPRINT.md §6 confidence separately tracks completed-work
    timing as deterministic compliance checks (C4/C5), and this signal's
    two components ("age since sanction" and "payment ahead of
    completion") are only live questions for a work that has not yet
    completed:

    A. Age since sanction vs the peer (same Phase 3 group) distribution of
       COMPLETED works' sanction-to-completion duration: what fraction of
       completed peers had already finished by the time this work has
       been open -- a high fraction means the work is unusually overdue.
       A simple empirical exceedance, not the survival model BLUEPRINT.md
       §7 registers as a later, gated ML layer (A1).
    B. Payment share (paid so far / sanction amount) against the
       distribution of that same ratio among OTHER open peers in the same
       group -- money released well ahead of a typical still-open peer.
       Does not control for how long each peer has been open; documented
       simplification, a candidate refinement for the ML layer, not
       attempted here (Phase 4 brief: no ML).

    The signal's score is the stronger (max) of whichever component(s)
    are eligible for that work; direction is 'above' whenever eligible
    (both components are inherently "more delayed / more paid" concepts).
    """
    open_mask = (frame["lifecycle_status"] == "sanctioned") & frame["sanction_date"].notna()
    if not open_mask.any():
        return _empty_result(frame.index)

    age_days = (as_of - frame["sanction_date"]).dt.days.clip(lower=0)

    completed = frame["lifecycle_status"] == "completed"
    duration_days = (frame["actual_end_date"] - frame["sanction_date"]).dt.days
    duration_frame = pd.DataFrame(index=frame.index)
    duration_frame["duration"] = duration_days.where(completed & duration_days.notna() & (duration_days >= 0))
    payment_share = (frame["paid_total"] / frame["amount"]).where(frame["is_usable"])
    # Clip is generous; C2 compliance already checks the real violation.
    payment_share = payment_share.clip(lower=0, upper=3)

    exceedance = pd.Series(np.nan, index=frame.index)
    pay_z = pd.Series(np.nan, index=frame.index)
    row_group_key = pd.Series(pd.NA, index=frame.index, dtype="object")
    n_completed_peers: dict = {}
    n_open_peers: dict = {}

    # `ctx["group_key"]` holds each row's OWN assigned-level key, which
    # differs in shape across L1/L2/L3, so two open works in the same true
    # peer group can carry different strings if they didn't land on the
    # same level -- grouping by that column directly would under-pool
    # exactly as it did for cost_anomaly_signal (see that function's
    # comment). Each level's key is rebuilt fresh across the WHOLE frame
    # instead, so the completed/open peer pools include every usable work
    # matching that level's type+state[+FY], regardless of which level
    # those other works were themselves assigned.
    for level, keys in peers.LEVEL_KEYS.items():
        level_open = open_mask & (ctx["level"] == level)
        if not level_open.any():
            continue
        gkey = peers.group_key(frame, keys)
        row_group_key.loc[level_open] = gkey[level_open]

        for g, subject_keys in _group_membership(level_open, gkey).items():
            # Component A: exceedance among completed peers sharing this key.
            pool_a = duration_frame.loc[gkey == g, "duration"].dropna()
            n_completed_peers[g] = len(pool_a)
            if len(pool_a) >= MIN_COMPLETED_PEERS_FOR_DURATION:
                pool_sorted = np.sort(pool_a.to_numpy())
                for i in subject_keys:
                    rank = np.searchsorted(pool_sorted, age_days.at[i], side="right")
                    exceedance.at[i] = float(rank) / len(pool_sorted)

            # Component B: payment share vs other open peers sharing this key.
            pool_b = payment_share.loc[(gkey == g) & open_mask].dropna()
            n_open_peers[g] = len(pool_b)
            if len(pool_b) < MIN_OTHER_PEERS + 1:
                continue
            for i in subject_keys:
                own = payment_share.at[i]
                if pd.isna(own):
                    continue
                others = pool_b.drop(index=i, errors="ignore")
                if len(others) < MIN_OTHER_PEERS:
                    continue
                mean, std = others.mean(), max(others.std(ddof=0), 0.02)
                pay_z.at[i] = (own - mean) / std

    score_a = pd.Series(exceedance, index=frame.index)  # already 0..1
    score_b = pd.Series(z_to_score(pay_z.fillna(0)), index=frame.index).where(pay_z.notna())
    eligible = open_mask & (score_a.notna() | score_b.notna())
    if not eligible.any():
        return _empty_result(frame.index)

    # Rank statistic: the stronger of the two components on a common 0-1
    # scale (A is an exceedance probability vs completed peers, B the normal
    # tail transform of the payment z). Phase 5c scores it by empirical
    # percentile rank among evaluated OPEN works, so the reference is other
    # open works, not completed ones: an open work's age is naturally longer
    # than a finished work's duration, which inflated A for most open works.
    # Works tied at the cap are ordered by days open (lifecycle_rank_key).
    combined = pd.concat([score_a, score_b], axis=1).max(axis=1, skipna=True)
    driver = np.where(score_a.fillna(-1) >= score_b.fillna(-1), "age_exceedance", "payment_ahead")
    ranked = empirical_tail_score(lifecycle_rank_key(combined.where(eligible), age_days))

    out = _empty_result(frame.index)
    out.loc[eligible, "eligible"] = True
    out.loc[eligible, "score"] = ranked[eligible]
    out.loc[eligible, "tail_percentile"] = ranked[eligible]
    out.loc[eligible, "direction"] = "above"
    for idx in frame.index[eligible]:
        g = row_group_key.at[idx]
        out.at[idx, "reliability"] = float(
            _reliability_from_counts(
                pd.Series([n_completed_peers.get(g, 0) + n_open_peers.get(g, 0)]),
                pd.Series([n_completed_peers.get(g, 0) + n_open_peers.get(g, 0)]),
            ).iloc[0]
        )
        out.at[idx, "evidence"] = {
            "driving_component": driver[frame.index.get_loc(idx)],
            "age_days": int(age_days.at[idx]),
            "age_exceedance": None if pd.isna(score_a.at[idx]) else float(score_a.at[idx]),
            "rank_statistic": float(combined.at[idx]),
            "n_completed_peers": int(n_completed_peers.get(g, 0)),
            "payment_share": None if pd.isna(payment_share.at[idx]) else float(payment_share.at[idx]),
            "payment_share_z": None if pd.isna(pay_z.at[idx]) else float(pay_z.at[idx]),
            "n_open_peers": int(n_open_peers.get(g, 0)),
            "group_key": g,
        }
    return out

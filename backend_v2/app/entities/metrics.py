"""
Phase 10 entity_metric builders (BLUEPRINT.md §8 "Payee, agency and district
analytics"). Every function here returns plain dict rows for entity_metric;
nothing here writes risk_result or signal_result, and nothing here is read
by Phase 5 fusion (gate G6) -- the no-guilt-by-association test asserts
risk_result's schema has no payee-derived column, ever.

Every threshold below is a documented judgement call, fixed BEFORE looking
at how many rows it produces (BLUEPRINT.md §8 "minimum n" instruction, and
this build's standing rule against tuning thresholds to the data):
  MIN_PAYMENTS_FOR_CONCENTRATION = 5   payment rows before a Herfindahl is
                                       computed at all.
  N_PERMUTATIONS = 200                 permutation-null draws (BLUEPRINT.md
                                       §8's "compared with a permutation
                                       null that shuffles payees within
                                       district, work type and FY strata").
  MIN_WORKS_FOR_PRICE_POSITION = 5     a payee's own works, at one work
                                       type, before a price-position median
                                       is shown.
  N_BOOTSTRAP = 1000                   bootstrap resamples for that median's
                                       interval.
  MIN_WORKS_FOR_DISTRICT_PROFILE = 10  an authority's own works before its
                                       lag/backlog stats are shown (same
                                       value as signals.py's
                                       MIN_AUTHORITY_PORTFOLIO, same
                                       reasoning: an authority's own count
                                       before a stat about it is meaningful).
  MIN_WORKS_FOR_AGENCY_PROFILE = 10    BLUEPRINT.md §8's own words,
                                       "implementing agency profile ...
                                       minimum n".
  SEED = 20261010                     one fixed seed for every permutation
                                       and bootstrap draw in this module.

Peer/state note: district_authority_profile groups authorities by STATE
using Phase 9's authority_geo.resolved_state_id. Since Phase 13.y it equals
district_authority.state_id for every authority (the first-MP-row bug that
misplaced 52 authorities is fixed in the ingest). This module is read-only
against district_authority.state_id; it never writes it.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ingest.normalize import normalize_name

MIN_PAYMENTS_FOR_CONCENTRATION = 5
N_PERMUTATIONS = 200
MIN_WORKS_FOR_PRICE_POSITION = 5
N_BOOTSTRAP = 1000
MIN_WORKS_FOR_DISTRICT_PROFILE = 10
MIN_WORKS_FOR_AGENCY_PROFILE = 10
SEED = 20261010

L1_PEER_DEFINITION = (
    "level-1 peers: works of the same activity type, in the same state, sanctioned in the same "
    "fiscal year, this work excluded (leave-one-out) -- app/analytics/peers.py"
)


def _no_interval(note: str) -> dict:
    return {"available": False, "note": note}


def _ci_interval(low: float, high: float, method: str) -> dict:
    return {"available": True, "low": low, "high": high, "method": method}


def _not_evaluated(base: dict, reason: str) -> dict:
    return {
        **base,
        "eligible": False,
        "not_evaluated_reason": reason,
        "n": base.get("n", 0),
        "value": None,
        "percentile": None,
        "denominator": reason,
        "interval": _no_interval(reason),
        "peer_definition": base.get("peer_definition", ""),
        "detail": {},
    }


# ---- shared loading --------------------------------------------------------------------


_PAYMENTS_SQL = text(
    """
    SELECT p.work_key, wc.house, p.payee_id, p.agency_id, p.amount, p.payment_date,
           w.activity_type_id, w.district_authority_id, w.raw_mp_name,
           wc.sanction_fy, wc.level, wc.amount_used, wc.peer_median,
           at.label AS activity_label
    FROM payment p
    JOIN work_context wc ON wc.work_key = p.work_key AND wc.run_id = :run
    JOIN work w ON w.work_key = p.work_key
    LEFT JOIN activity_type at ON at.id = w.activity_type_id
    WHERE p.source_snapshot_id = :snap
    """
)


def load_payments_with_context(session: Session, run_id: int, snapshot_id: int) -> pd.DataFrame:
    df = pd.read_sql(_PAYMENTS_SQL, session.connection(), params={"run": run_id, "snap": snapshot_id})
    # work.tenure_id is 0% populated in this dataset (docs/phase10_11_report.md) --
    # "MP" here is the same normalised raw_mp_name identity Phase 3/4 already use.
    df["mp"] = df["raw_mp_name"].map(normalize_name).replace("", pd.NA)
    return df


def herfindahl(shares: np.ndarray) -> float:
    return float(np.sum(shares**2))


def _shares(amounts: pd.Series, labels: pd.Series) -> np.ndarray:
    total = amounts.sum()
    if total <= 0:
        return np.array([])
    by = amounts.groupby(labels).sum()
    return (by / total).to_numpy()


# ---- concentration (BLUEPRINT.md §8: MP tenure, district authority) --------------------------------


def mp_tenure_id(mp: str, house: str) -> int:
    """A stable synthetic id for the 'mp_tenure' entity_type's entity_id
    (Integer column, no backing table -- see module docstring). Within one
    snapshot, house alone determines the tenure label (TENURE_LABELS =
    ("18th Lok Sabha", "Sitting MP"), one per house -- app/models/reference.py),
    so (normalised MP name, house) is the tenure identity, with no
    start/end dates needed to disambiguate multiple terms; this dataset
    has none to disambiguate. Collision risk: ~700-900 distinct (name,
    house) pairs into a ~2*10^9 id space -- negligible (~2*10^-7)."""
    digest = hashlib.sha1(f"{mp}|{house}".encode()).hexdigest()
    return int(digest[:8], 16) % 2_000_000_000


def _herfindahl_per_group(amount: pd.Series, payee_id: pd.Series, key: pd.Series) -> pd.Series:
    """Herfindahl for every group in `key` at once (vectorised: two
    groupby-sums, no per-group Python call) -- share = a payee's amount
    within the group / the group's total amount; H = sum of share**2."""
    by_group_payee = amount.groupby([key, payee_id]).sum()
    by_group = amount.groupby(key).sum()
    share = by_group_payee / by_group.reindex(by_group_payee.index.get_level_values(0)).to_numpy()
    return (share**2).groupby(level=0).sum()


def _concentration_rows(
    pool: pd.DataFrame,
    entity_type: str,
    key: pd.Series,
    null_h: pd.DataFrame,
    id_of,
    name_of,
    peer_noun: str,
    run_id: int,
) -> list[dict]:
    """Turns one grouping's observed Herfindahl + its share of the shared
    null draws (`null_h`, columns = permutation index, already computed for
    every eligible key of this grouping) into entity_metric rows. The
    permutation null shuffles payee_id WITHIN each (district, work-type,
    FY) stratum -- BLUEPRINT.md §8's example ("only 2 payees exist in that
    stratum") is exactly what it's built to not flag."""
    sub = pool.assign(_key=key).dropna(subset=["_key"])
    n_by_key = sub.groupby("_key").size()
    n_payees_by_key = sub.groupby("_key")["payee_id"].nunique()
    house_by_key = sub.groupby("_key")["house"].agg(lambda s: s.iloc[0] if s.nunique() == 1 else None)
    h_by_key = _herfindahl_per_group(sub["amount"], sub["payee_id"], sub["_key"])

    rows = []
    for key_val in null_h.index:
        n, n_payees, h = n_by_key[key_val], n_payees_by_key[key_val], h_by_key[key_val]
        draws = null_h.loc[key_val].to_numpy()
        percentile = float((draws <= h).mean())
        n_other_payees_in_strata = int(
            pool[pool["stratum"].isin(sub.loc[sub["_key"] == key_val, "stratum"].unique())][
                "payee_id"
            ].nunique()
        )
        rows.append(
            {
                "run_id": run_id,
                "entity_type": entity_type,
                "entity_id": id_of(key_val),
                "entity_name": name_of(key_val),
                "metric": "concentration",
                "substratum": "",
                "house": house_by_key[key_val],
                "eligible": True,
                "not_evaluated_reason": None,
                "n": int(n),
                "value": h,
                "percentile": percentile,
                "denominator": f"{int(n)} payments to {int(n_payees)} distinct payees",
                "interval": _ci_interval(
                    float(np.quantile(draws, 0.025)),
                    float(np.quantile(draws, 0.975)),
                    f"permutation null, {N_PERMUTATIONS} draws, payee labels shuffled within "
                    "district x work-type x FY strata",
                ),
                "peer_definition": (
                    f"{n_other_payees_in_strata} payees observed nationally in the same district x "
                    f"work-type x FY strata this {peer_noun}'s payments fall in"
                ),
                "detail": {
                    "herfindahl": h,
                    "n_distinct_payees": int(n_payees),
                    "effective_n_payees": None if h <= 0 else round(1.0 / h, 2),
                    "null_mean": float(draws.mean()),
                    "null_std": float(draws.std()),
                },
            }
        )
    return rows


def concentration_metrics(df: pd.DataFrame, run_id: int, rng: np.random.Generator) -> list[dict]:
    """BLUEPRINT.md §8's two concentration grains: district authority (a
    real FK) and MP tenure. `work.tenure_id` is 0% populated in this
    dataset (a pre-existing gap -- docs/phase10_11_report.md), so MP
    identity here is the same normalised raw_mp_name text Phase 3/4 already
    use, and 'mp_tenure's entity_id is the stable hash mp_tenure_id()
    (no backing table has an integer id for a name -- see that function).

    Both groupings' null draws come from the SAME `N_PERMUTATIONS` shuffles
    of the same pool (one shuffle per iteration, not one per grouping):
    the null only depends on the (district, work-type, FY) stratum a
    payment falls in, never on which entity grouping is being evaluated,
    so re-shuffling per grouping would just repeat the same random work for
    no statistical benefit."""
    pool = df.dropna(subset=["district_authority_id", "activity_type_id", "sanction_fy", "payee_id"]).copy()
    if pool.empty:
        return []
    pool["stratum"] = (
        pool["district_authority_id"].astype(int).astype(str)
        + "|"
        + pool["activity_type_id"].astype(int).astype(str)
        + "|"
        + pool["sanction_fy"]
    )
    pool = pool.reset_index(drop=True)

    da_key = pool["district_authority_id"]
    mp_key = pool["mp"].where(pool["mp"].isna(), pool["mp"] + "|" + pool["house"])
    mp_of_key = {v: v.split("|")[0] for v in mp_key.dropna().unique()}
    house_of_key = {v: v.split("|")[1] for v in mp_key.dropna().unique()}

    groupings = {}
    for name, key in (("district_authority", da_key), ("mp_tenure", mp_key)):
        sub = pool.assign(_key=key).dropna(subset=["_key"])
        n = sub.groupby("_key").size()
        n_payees = sub.groupby("_key")["payee_id"].nunique()
        eligible_keys = n[(n >= MIN_PAYMENTS_FOR_CONCENTRATION) & (n_payees >= 2)].index
        if len(eligible_keys):
            groupings[name] = (key, eligible_keys)
    if not groupings:
        return []

    null_h = {
        name: pd.DataFrame(0.0, index=keys, columns=range(N_PERMUTATIONS))
        for name, (_, keys) in groupings.items()
    }
    for i in range(N_PERMUTATIONS):
        shuffled_payee = pool.groupby("stratum")["payee_id"].transform(
            lambda s: rng.permutation(s.to_numpy())
        )
        perm_amount = pool["amount"]
        for name, (key, eligible_keys) in groupings.items():
            in_scope = key.isin(eligible_keys)
            h_i = _herfindahl_per_group(perm_amount[in_scope], shuffled_payee[in_scope], key[in_scope])
            null_h[name].loc[h_i.index, i] = h_i

    rows: list[dict] = []
    if "district_authority" in groupings:
        rows += _concentration_rows(
            pool,
            "district_authority",
            da_key,
            null_h["district_authority"],
            id_of=lambda k: int(k),
            name_of=lambda k: "",
            peer_noun="district authority",
            run_id=run_id,
        )
    if "mp_tenure" in groupings:
        rows += _concentration_rows(
            pool,
            "mp_tenure",
            mp_key,
            null_h["mp_tenure"],
            id_of=lambda k: mp_tenure_id(mp_of_key[k], house_of_key[k]),
            name_of=lambda k: f"{mp_of_key[k]} ({house_of_key[k]})",
            peer_noun="MP's tenure",
            run_id=run_id,
        )
    return rows


# ---- payee price position (grain: payee x work type) --------------------------------------------


def price_position_metrics(df: pd.DataFrame, run_id: int, rng: np.random.Generator) -> list[dict]:
    l1 = df[
        (df["level"] == "L1")
        & df["amount_used"].notna()
        & df["peer_median"].notna()
        & (df["peer_median"] > 0)
        & (df["amount_used"] > 0)
    ].copy()
    if l1.empty:
        return []
    l1["residual"] = np.log(l1["amount_used"].astype(float)) - np.log(l1["peer_median"].astype(float))
    l1 = l1.drop_duplicates(subset=["work_key", "payee_id"])  # one residual per (work, payee)

    rows = []
    for (payee_id, atype), g in l1.groupby(["payee_id", "activity_type_id"]):
        n = g["work_key"].nunique()
        label = g["activity_label"].dropna().iloc[0] if g["activity_label"].notna().any() else str(atype)
        base = {
            "n": n,
            "peer_definition": f"this payee's own works, work type '{label}', {L1_PEER_DEFINITION}",
        }
        if n < MIN_WORKS_FOR_PRICE_POSITION:
            rows.append(
                {
                    "run_id": run_id,
                    "entity_type": "payee",
                    "entity_id": int(payee_id),
                    "entity_name": "",
                    "metric": "price_position",
                    "substratum": label,
                    "house": None,
                    **_not_evaluated(base, f"fewer than {MIN_WORKS_FOR_PRICE_POSITION} priced works (n={n})"),
                }
            )
            continue
        vals = g["residual"].to_numpy()
        med = float(np.median(vals))
        boot = rng.choice(vals, size=(N_BOOTSTRAP, len(vals)), replace=True)
        boot_med = np.median(boot, axis=1)
        houses = g["house"].unique()
        rows.append(
            {
                "run_id": run_id,
                "entity_type": "payee",
                "entity_id": int(payee_id),
                "entity_name": "",
                "metric": "price_position",
                "substratum": label,
                "house": houses[0] if len(houses) == 1 else None,
                "eligible": True,
                "not_evaluated_reason": None,
                "n": int(n),
                "value": med,
                "percentile": None,
                "denominator": f"{n} of this payee's works priced at level-1 peers (work type '{label}')",
                "interval": _ci_interval(
                    float(np.quantile(boot_med, 0.025)),
                    float(np.quantile(boot_med, 0.975)),
                    f"bootstrap, {N_BOOTSTRAP} resamples of the {n} residuals",
                ),
                "peer_definition": base["peer_definition"],
                "detail": {"median_log_residual": med, "interpretation": "positive = paid above peer median"},
            }
        )
    return rows


# ---- payee reach (descriptive) -----------------------------------------------------------------


def reach_metrics(df: pd.DataFrame, run_id: int) -> list[dict]:
    rows = []
    for payee_id, g in df.groupby("payee_id"):
        n_works = g["work_key"].nunique()
        n_districts = g["district_authority_id"].nunique()
        n_mps = g["mp"].nunique()
        total_amount = float(g["amount"].sum())
        houses = g["house"].unique()
        rows.append(
            {
                "run_id": run_id,
                "entity_type": "payee",
                "entity_id": int(payee_id),
                "entity_name": "",
                "metric": "reach",
                "substratum": "",
                "house": houses[0] if len(houses) == 1 else None,
                "eligible": True,
                "not_evaluated_reason": None,
                "n": int(n_works),
                "value": total_amount,
                "percentile": None,
                "denominator": f"{n_works} works, {n_mps} distinct MPs, {n_districts} districts (this run)",
                "interval": _no_interval("descriptive count and sum, not a statistical interval"),
                "peer_definition": "none (descriptive, not compared against peers)",
                "detail": {
                    "n_works": int(n_works),
                    "n_mps": int(n_mps),
                    "n_districts": int(n_districts),
                    "total_amount": total_amount,
                },
            }
        )
    return rows


# ---- district authority profile (BLUEPRINT.md §8: "against other authorities in the state") -------


_AUTHORITY_WORKS_SQL = text(
    """
    SELECT w.work_key, wc.house, w.district_authority_id, ws.recommended_date, ws.sanction_date,
           ws.actual_end_date, ws.lifecycle_status, ag.resolved_state_id, st.name AS resolved_state_name
    FROM work_context wc
    JOIN work w ON w.work_key = wc.work_key
    JOIN work_state ws ON ws.work_key = wc.work_key AND ws.source_snapshot_id = :snap
    LEFT JOIN authority_geo ag ON ag.district_authority_id = w.district_authority_id
    LEFT JOIN state st ON st.id = ag.resolved_state_id
    WHERE wc.run_id = :run AND w.district_authority_id IS NOT NULL
    """
)


def load_authority_works(session: Session, run_id: int, snapshot_id: int) -> pd.DataFrame:
    return pd.read_sql(
        _AUTHORITY_WORKS_SQL, session.connection(), params={"run": run_id, "snap": snapshot_id}
    )


def _days(a: pd.Series, b: pd.Series) -> pd.Series:
    """(a - b).days, NaN unless both present and a >= b (a data-consistency
    floor: a negative lag is a source-data anomaly, not a real lag)."""
    d = (pd.to_datetime(a) - pd.to_datetime(b)).dt.days
    return d.where(d >= 0)


def district_authority_profile_metrics(
    works: pd.DataFrame, payments: pd.DataFrame, as_of: pd.Timestamp, run_id: int
) -> list[dict]:
    w = works.copy()
    w["rec_to_sanction"] = _days(w["sanction_date"], w["recommended_date"])
    w["sanction_to_completion"] = _days(w["actual_end_date"], w["sanction_date"])
    open_mask = w["lifecycle_status"] == "sanctioned"
    w["open_backlog_age"] = np.nan
    w.loc[open_mask, "open_backlog_age"] = (as_of - pd.to_datetime(w.loc[open_mask, "sanction_date"])).dt.days

    pay_sanction = w.set_index("work_key")["sanction_date"]
    pm = payments.copy()
    pm["sanction_date"] = pm["work_key"].map(pay_sanction)
    pm["payment_ageing"] = _days(pm["payment_date"], pm["sanction_date"])
    ageing_by_auth = (
        pm.dropna(subset=["district_authority_id"])
        .groupby("district_authority_id")["payment_ageing"]
        .median()
    )

    per_auth = w.groupby("district_authority_id").agg(
        n=("work_key", "nunique"),
        house_nunique=("house", "nunique"),
        house_first=("house", "first"),
        rec_to_sanction=("rec_to_sanction", "median"),
        n_rec_to_sanction=("rec_to_sanction", "count"),
        sanction_to_completion=("sanction_to_completion", "median"),
        n_sanction_to_completion=("sanction_to_completion", "count"),
        open_backlog_age=("open_backlog_age", "median"),
        n_open=("open_backlog_age", "count"),
        resolved_state_id=("resolved_state_id", "first"),
        resolved_state_name=("resolved_state_name", "first"),
    )
    eligible = per_auth[per_auth["n"] >= MIN_WORKS_FOR_DISTRICT_PROFILE]

    rows = []
    for state_id, in_state in eligible.groupby("resolved_state_id", dropna=False):
        peer_vals = in_state["rec_to_sanction"].dropna()
        for auth_id, r in in_state.iterrows():
            has_state = pd.notna(state_id)
            others = peer_vals.drop(index=auth_id, errors="ignore")
            percentile = None
            if has_state and pd.notna(r["rec_to_sanction"]) and len(others) >= 1:
                percentile = float((others <= r["rec_to_sanction"]).mean())
            # This authority clears the work-count minimum, but its headline stat
            # (recommendation-to-sanction lag) needs both dates on at least one of its
            # works -- absent that, eligible must match value being null, same as
            # every other metric in this table.
            has_value = pd.notna(r["rec_to_sanction"])
            rows.append(
                {
                    "run_id": run_id,
                    "entity_type": "district_authority",
                    "entity_id": int(auth_id),
                    "entity_name": "",
                    "metric": "district_authority_profile",
                    "substratum": "",
                    "house": r["house_first"] if r["house_nunique"] == 1 else None,
                    "eligible": has_value,
                    "not_evaluated_reason": (
                        None if has_value else "no work has both a recommendation and a sanction date"
                    ),
                    "n": int(r["n"]),
                    "value": float(r["rec_to_sanction"]) if has_value else None,
                    "percentile": percentile,
                    "denominator": (
                        f"{int(r['n'])} works ({int(r['n_rec_to_sanction'])} with a "
                        f"recommendation-to-sanction lag, {int(r['n_sanction_to_completion'])} "
                        f"completed, {int(r['n_open'])} open)"
                    ),
                    "interval": _no_interval(
                        "median across this authority's own works, not a confidence interval"
                    ),
                    "peer_definition": (
                        f"{len(others)} other district authorities in {r['resolved_state_name']} with "
                        f">= {MIN_WORKS_FOR_DISTRICT_PROFILE} works (Phase 9 corrected location)"
                        if has_state
                        else (
                            "no resolved state for this authority (Phase 9 location unresolved) -- "
                            "not compared to peers"
                        )
                    ),
                    "detail": {
                        "rec_to_sanction_lag_days_median": (
                            None if pd.isna(r["rec_to_sanction"]) else float(r["rec_to_sanction"])
                        ),
                        "sanction_to_completion_lag_days_median": (
                            None
                            if pd.isna(r["sanction_to_completion"])
                            else float(r["sanction_to_completion"])
                        ),
                        "open_backlog_age_days_median": (
                            None if pd.isna(r["open_backlog_age"]) else float(r["open_backlog_age"])
                        ),
                        "payment_ageing_days_median": (
                            None
                            if auth_id not in ageing_by_auth.index or pd.isna(ageing_by_auth[auth_id])
                            else float(ageing_by_auth[auth_id])
                        ),
                        "resolved_state": r["resolved_state_name"],
                    },
                }
            )
    return rows


# ---- implementing agency profile (BLUEPRINT.md §8: "minimum n") -----------------------------------


def implementing_agency_profile_metrics(
    payments: pd.DataFrame, works: pd.DataFrame, agency_names: dict[int, str], run_id: int
) -> list[dict]:
    p = payments.dropna(subset=["agency_id"]).copy()
    if p.empty:
        return []
    dates = works.set_index("work_key")[["sanction_date", "actual_end_date", "lifecycle_status"]]
    p = p.join(dates, on="work_key")
    p["completion_lag"] = _days(p["actual_end_date"], p["sanction_date"])
    l1 = p[
        (p["level"] == "L1")
        & p["amount_used"].notna()
        & p["peer_median"].notna()
        & (p["peer_median"] > 0)
        & (p["amount_used"] > 0)
    ].copy()
    l1["residual"] = np.log(l1["amount_used"].astype(float)) - np.log(l1["peer_median"].astype(float))

    per_agency_n = p.groupby("agency_id")["work_key"].nunique()
    eligible_ids = per_agency_n[per_agency_n >= MIN_WORKS_FOR_AGENCY_PROFILE].index
    lag_by_agency = p[p["lifecycle_status"] == "completed"].groupby("agency_id")["completion_lag"].median()
    all_lags = lag_by_agency.reindex(eligible_ids).dropna()

    rows = []
    for agency_id in eligible_ids:
        n = int(per_agency_n[agency_id])
        lag = lag_by_agency.get(agency_id, np.nan)
        res = l1[l1["agency_id"] == agency_id]["residual"].dropna()
        others = all_lags.drop(index=agency_id, errors="ignore")
        percentile = float((others <= lag).mean()) if pd.notna(lag) and len(others) >= 1 else None
        houses = p.loc[p["agency_id"] == agency_id, "house"].unique()
        # Clears the minimum-n gate, but needs at least one completed work with both
        # dates for its headline stat -- eligible must track value being non-null.
        has_value = pd.notna(lag)
        rows.append(
            {
                "run_id": run_id,
                "entity_type": "implementing_agency",
                "entity_id": int(agency_id),
                "entity_name": agency_names.get(int(agency_id), ""),
                "metric": "implementing_agency_profile",
                "substratum": "",
                "house": houses[0] if len(houses) == 1 else None,
                "eligible": has_value,
                "not_evaluated_reason": (
                    None if has_value else "no completed work with both a sanction and a completion date"
                ),
                "n": n,
                "value": float(lag) if has_value else None,
                "percentile": percentile,
                "denominator": f"{n} works for this agency ({len(res)} priced against level-1 peers)",
                "interval": _no_interval("median across this agency's own works, not a confidence interval"),
                "peer_definition": (
                    f"{len(others)} other implementing agencies nationally with "
                    f">= {MIN_WORKS_FOR_AGENCY_PROFILE} works, this run"
                ),
                "detail": {
                    "completion_lag_days_median": None if pd.isna(lag) else float(lag),
                    "price_position_median_log_residual": None if res.empty else float(res.median()),
                    "n_priced_works": int(len(res)),
                },
            }
        )
    return rows

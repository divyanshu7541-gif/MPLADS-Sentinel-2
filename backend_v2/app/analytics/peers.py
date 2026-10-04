"""
Peer groups and leave-one-out baselines (BLUEPRINT.md §6 "Peer hierarchy").

Fixes the two defects confirmed in the old engine
(backend/app/context/peer_builder.py, context_service.py):
  1. Level 1 was keyed on the MP's own seat, so a "peer group" was mostly
     one MP's works measured against themselves. Level 1 here is work type
     x state x sanction FY.
  2. Group size and median/MAD/IQR included the work itself. Every count
     and statistic here excludes it.

Leave-one-out is computed by literally removing the work from its group's
value vector before taking the statistic (median, MAD and quantiles are
not linearly decomposable, so no correction formula is used). All
alignment between rows and results is by index label, never by position.

House is deliberately NOT a grouping key: a group mixes Lok Sabha and
Rajya Sabha works. House only filters what is displayed downstream.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

LEVEL_KEYS: dict[str, tuple[str, ...]] = {
    "L1": ("activity_type_id", "state_id", "sanction_fy"),
    "L2": ("activity_type_id", "state_id"),
    "L3": ("activity_type_id", "sanction_fy"),
}
REFINEMENT_KEYS: tuple[str, ...] = ("activity_type_id", "district_key", "sanction_fy")

MIN_USABLE_PEERS = 15
MIN_OTHER_MPS = 3
MAX_MP_SHARE = 0.5

_CHUNK_CELLS = 2_000_000  # max subjects x group-size cells per leave-one-out matrix


def group_key(frame: pd.DataFrame, keys: tuple[str, ...]) -> pd.Series:
    """'k1=v1|k2=v2|...' per row; NaN where any key component is missing."""
    missing = frame[list(keys)].isna().any(axis=1)
    key = keys[0] + "=" + frame[keys[0]].astype(str)
    for k in keys[1:]:
        key = key + "|" + k + "=" + frame[k].astype(str)
    return key.where(~missing)


def group_composition(frame: pd.DataFrame, gkey: pd.Series) -> pd.DataFrame:
    """Per-row composition of the row's group with the row itself removed.

    `frame` needs columns `mp` and `is_usable`. Returns (indexed like
    `frame`): n_usable_excl_self, distinct_other_mps (MPs among peers
    other than the work's own MP), distinct_mps_in_peers, max_mp_share
    (largest single-MP share of the peers, own MP's other works included),
    and distinct_mps_in_group -- a descriptive count over every work in the
    group, the work's own MP included. That last one is how BLUEPRINT.md
    §6's coverage column counts "3 or more MPs"; it never gates a baseline.
    """
    out = pd.DataFrame(
        index=frame.index,
        columns=[
            "n_usable_excl_self",
            "distinct_other_mps",
            "distinct_mps_in_peers",
            "max_mp_share",
            "distinct_mps_in_group",
        ],
        dtype=float,
    )
    d = pd.DataFrame({"g": gkey, "mp": frame["mp"], "u": frame["is_usable"].astype(int)})
    d = d[d["g"].notna()]
    if d.empty:
        return out

    usable = d[d["u"] == 1]
    n_group = usable.groupby("g").size()
    per_mp = usable.groupby(["g", "mp"]).size()
    n_mps = per_mp.groupby(level="g").size()

    ranked = per_mp.rename("cnt").reset_index().sort_values(["g", "cnt", "mp"], ascending=[True, False, True])
    ranked["rank"] = ranked.groupby("g").cumcount()
    top1 = ranked[ranked["rank"] == 0].set_index("g")
    top2_cnt = ranked[ranked["rank"] == 1].set_index("g")["cnt"]

    own_cnt = pd.Series(
        per_mp.reindex(pd.MultiIndex.from_frame(d[["g", "mp"]])).to_numpy(), index=d.index
    ).fillna(0)
    u_self = d["u"]
    n_excl = d["g"].map(n_group).fillna(0) - u_self
    own_excl = own_cnt - u_self

    top1_mp = d["g"].map(top1["mp"])
    top1_cnt = d["g"].map(top1["cnt"]).fillna(0)
    top2 = d["g"].map(top2_cnt).fillna(0)
    best_other_mp_cnt = top1_cnt.where(top1_mp != d["mp"], top2)
    max_cnt = np.maximum(own_excl, best_other_mp_cnt)

    group_mps = d["g"].map(n_mps).fillna(0)
    out.loc[d.index, "n_usable_excl_self"] = n_excl
    out.loc[d.index, "distinct_other_mps"] = group_mps - (own_cnt > 0).astype(int)
    out.loc[d.index, "distinct_mps_in_peers"] = group_mps - ((own_cnt > 0) & (own_excl == 0)).astype(int)
    out.loc[d.index, "max_mp_share"] = (max_cnt / n_excl).where(n_excl > 0)
    out.loc[d.index, "distinct_mps_in_group"] = d["g"].map(d.groupby("g")["mp"].nunique())
    return out


def qualifies(comp: pd.DataFrame) -> pd.Series:
    """The one rule for every level, refinement included (BLUEPRINT.md §6;
    applying it to refinement too is the user's Phase 3 review decision):
    >= 15 usable peers excluding self, >= 3 distinct other MPs, no MP above
    50% of the peers."""
    return (
        (comp["n_usable_excl_self"] >= MIN_USABLE_PEERS)
        & (comp["distinct_other_mps"] >= MIN_OTHER_MPS)
        & (comp["max_mp_share"] <= MAX_MP_SHARE)
    )


def loo_stats(frame: pd.DataFrame, gkey: pd.Series, subjects: pd.Series) -> pd.DataFrame:
    """Leave-one-out median, q25, q75, iqr and MAD of `amount` over each
    subject row's group, with the subject itself removed from the pool.

    `frame` needs columns `amount` and `is_usable` (only usable rows are
    pooled). Rows where `subjects` is False are left NaN. MAD is the raw
    median absolute deviation around the leave-one-out median (unscaled;
    flooring/scaling belongs to the cost signal, not the baseline).
    """
    cols = ["median", "q25", "q75", "iqr", "mad"]
    out = pd.DataFrame(np.nan, index=frame.index, columns=cols)
    d = pd.DataFrame(
        {"g": gkey, "v": frame["amount"], "u": frame["is_usable"].astype(bool), "s": subjects.astype(bool)}
    )
    d = d[d["g"].notna()]
    groups_with_subjects = d.loc[d["s"], "g"].unique()
    d = d[d["g"].isin(groups_with_subjects)]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN rows (lone work) -> NaN
        for _, grp in d.groupby("g", sort=True):
            pool = grp[grp["u"]]
            subj = grp[grp["s"]]
            n = len(pool)
            if n == 0:
                continue
            v = pool["v"].to_numpy(dtype=float)
            pos_of = pd.Series(np.arange(n), index=pool.index)
            step = max(1, _CHUNK_CELLS // n)
            for start in range(0, len(subj), step):
                labels = subj.index[start : start + step]
                self_pos = pos_of.reindex(labels).to_numpy()  # NaN where subject isn't in the pool
                m = np.tile(v, (len(labels), 1))
                in_pool = ~np.isnan(self_pos)
                m[np.nonzero(in_pool)[0], self_pos[in_pool].astype(int)] = np.nan
                med = np.nanmedian(m, axis=1)
                q25, q75 = np.nanpercentile(m, [25, 75], axis=1)
                mad = np.nanmedian(np.abs(m - med[:, None]), axis=1)
                out.loc[labels, "median"] = med
                out.loc[labels, "q25"] = q25
                out.loc[labels, "q75"] = q75
                out.loc[labels, "iqr"] = q75 - q25
                out.loc[labels, "mad"] = mad
    return out


def build_context(frame: pd.DataFrame) -> pd.DataFrame:
    """Assign each work its first qualifying level (L1 -> L2 -> L3) and
    compute leave-one-out baselines at that level, plus the refinement
    level where it qualifies.

    `frame` (index = work_key) needs: activity_type_id, state_id,
    district_key, sanction_fy, mp, amount, is_usable.
    """
    result = pd.DataFrame(index=frame.index)
    result["level"] = pd.Series(pd.NA, index=frame.index, dtype="object")
    result["group_key"] = pd.Series(pd.NA, index=frame.index, dtype="object")
    for col in ("n_usable_excl_self", "distinct_other_mps", "max_mp_share"):
        result[col] = np.nan
    for col in ("peer_median", "peer_q25", "peer_q75", "peer_iqr", "peer_mad"):
        result[col] = np.nan

    unassigned = pd.Series(True, index=frame.index)
    for level, keys in LEVEL_KEYS.items():
        gkey = group_key(frame, keys)
        comp = group_composition(frame, gkey)
        if level == "L1":
            result["l1_n_usable_excl_self"] = comp["n_usable_excl_self"]
            result["l1_distinct_other_mps"] = comp["distinct_other_mps"]
            result["l1_distinct_mps_in_peers"] = comp["distinct_mps_in_peers"]
            result["l1_distinct_mps_in_group"] = comp["distinct_mps_in_group"]
        take = unassigned & qualifies(comp).fillna(False)
        if not take.any():
            continue
        result.loc[take, "level"] = level
        result.loc[take, "group_key"] = gkey[take]
        for col in ("n_usable_excl_self", "distinct_other_mps", "max_mp_share"):
            result.loc[take, col] = comp.loc[take, col]
        stats = loo_stats(frame, gkey, take)
        for src, dst in (
            ("median", "peer_median"),
            ("q25", "peer_q25"),
            ("q75", "peer_q75"),
            ("iqr", "peer_iqr"),
            ("mad", "peer_mad"),
        ):
            result.loc[take, dst] = stats.loc[take, src]
        unassigned &= ~take

    rkey = group_key(frame, REFINEMENT_KEYS)
    rcomp = group_composition(frame, rkey)
    rtake = qualifies(rcomp).fillna(False)
    result["ref_group_key"] = rkey.where(rtake)
    result["ref_n_usable_excl_self"] = rcomp["n_usable_excl_self"].where(rtake)
    result["ref_distinct_other_mps"] = rcomp["distinct_other_mps"].where(rtake)
    result["ref_max_mp_share"] = rcomp["max_mp_share"].where(rtake)
    rstats = loo_stats(frame, rkey, rtake)
    result["ref_median"] = rstats["median"]
    result["ref_iqr"] = rstats["iqr"]
    result["ref_mad"] = rstats["mad"]
    return result


def group_summary(frame: pd.DataFrame, keys: tuple[str, ...]) -> pd.DataFrame:
    """Whole-group composition for the peer_group table (no self-exclusion
    here -- it describes the group, not any one work's baseline)."""
    gkey = group_key(frame, keys)
    d = pd.DataFrame(
        {"g": gkey, "mp": frame["mp"], "u": frame["is_usable"].astype(bool), "house": frame["house"]}
    )
    d = d[d["g"].notna()]
    usable = d[d["u"]]
    per_mp = usable.groupby(["g", "mp"]).size()
    summary = pd.DataFrame(
        {
            "n_works": d.groupby("g").size(),
            "n_usable": usable.groupby("g").size(),
            "n_mps": per_mp.groupby(level="g").size(),
            "top_mp_cnt": per_mp.groupby(level="g").max(),
            "n_ls": d[d["house"] == "LS"].groupby("g").size(),
            "n_rs": d[d["house"] == "RS"].groupby("g").size(),
        }
    )
    summary[["n_works", "n_usable", "n_mps", "top_mp_cnt", "n_ls", "n_rs"]] = summary[
        ["n_works", "n_usable", "n_mps", "top_mp_cnt", "n_ls", "n_rs"]
    ].fillna(0)
    summary["top_mp_share"] = (summary["top_mp_cnt"] / summary["n_usable"]).where(summary["n_usable"] > 0)
    return summary.drop(columns=["top_mp_cnt"])

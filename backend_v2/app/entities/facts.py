"""
Phase 10 work-evidence facts (BLUEPRINT.md §8 "Metrics"): plain, deterministic
facts, not scores -- same posture as Phase 6's
pays_same_payee_more_than_once (app/analytics/atypicality_run.py), which
this module does not duplicate or replace.

  identical_payment_repeated  Payment rows in `payment` are only ever
                              deduplicated away if every one of (snapshot,
                              work, payee, date, amount) matches AND it's
                              the same repetition index -- a genuine repeat
                              of that exact tuple keeps its own row with
                              occurrence_no > 1 (see work_related.py). So
                              "identical (payee, amount, date) within a
                              work" is read directly off occurrence_no,
                              no re-derivation needed.
  multi_payee_work           Four or more distinct payees paid into one
                              work (BLUEPRINT.md §8 baseline: "often
                              unit-wise purchases", descriptive, not an
                              accusation).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

MULTI_PAYEE_THRESHOLD = 4  # BLUEPRINT.md §8 "four or more distinct payees"

_SQL = text(
    """
    SELECT p.work_key, wc.house, p.payee_id, p.amount, p.payment_date, p.occurrence_no
    FROM payment p
    JOIN work_context wc ON wc.work_key = p.work_key AND wc.run_id = :run
    WHERE p.source_snapshot_id = :snap
    """
)


def load_payments(session: Session, run_id: int, snapshot_id: int) -> pd.DataFrame:
    return pd.read_sql(_SQL, session.connection(), params={"run": run_id, "snap": snapshot_id})


def identical_payment_facts(df: pd.DataFrame, run_id: int) -> tuple[pd.DataFrame, dict]:
    repeated = df[df["occurrence_no"] > 1]
    hit_works = repeated["work_key"].unique()
    groups = (
        repeated.groupby(["work_key", "payee_id", "amount", "payment_date"])["occurrence_no"]
        .max()
        .reset_index()
        .rename(columns={"occurrence_no": "repeat_count"})
    )
    house_by_work = df.drop_duplicates("work_key").set_index("work_key")["house"]
    rows = []
    for work_key, g in groups.groupby("work_key"):
        top = g.sort_values("repeat_count", ascending=False).iloc[0]
        rows.append(
            {
                "run_id": run_id,
                "work_key": work_key,
                "house": house_by_work[work_key],
                "fact": "identical_payment_repeated",
                "detail": {
                    "payee_id": int(top.payee_id),
                    "amount": float(top.amount),
                    "payment_date": str(top.payment_date),
                    "repeat_count": int(top.repeat_count),
                    "distinct_repeated_groups": int(len(g)),
                },
            }
        )
    stats = {
        "works_with_fact": len(hit_works),
        "payment_rows_involved": int(len(repeated)),
        "payment_rows_total": int(len(df)),
        "amount_involved": float(repeated["amount"].sum()) if len(repeated) else 0.0,
    }
    return pd.DataFrame(rows), stats


def multi_payee_facts(df: pd.DataFrame, run_id: int) -> tuple[pd.DataFrame, dict]:
    n_payees = df.groupby("work_key")["payee_id"].nunique()
    house_by_work = df.drop_duplicates("work_key").set_index("work_key")["house"]
    hit = n_payees[n_payees >= MULTI_PAYEE_THRESHOLD]
    rows = [
        {
            "run_id": run_id,
            "work_key": wk,
            "house": house_by_work[wk],
            "fact": "multi_payee_work",
            "detail": {"n_payees": int(n)},
        }
        for wk, n in hit.items()
    ]
    works_with_payments = df["work_key"].nunique()
    stats = {
        "works_with_fact": len(hit),
        "works_with_payments": int(works_with_payments),
        "pct_of_works_with_payments": round(len(hit) / works_with_payments * 100, 2)
        if works_with_payments
        else 0.0,
    }
    return pd.DataFrame(rows), stats

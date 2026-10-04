"""
Phase 5 compliance and integrity panel C1-C9 (BLUEPRINT.md §6):
deterministic rule checks, OUTSIDE the risk score -- nothing here feeds
fusion.py. Every check is evaluated for every subject it applies to and
both passes and failures are stored, so "0 violations" is a counted
result, not an absence of rows.

All work-level checks run on Snapshot A's own work_state/payment (the
snapshot the BLUEPRINT baselines were measured on). C7 is per MP on the
Snapshot B MP summary (the only file carrying MP-level allocated /
recommended / expenditure totals); C9 is per reconciled file.

  C1  actual amount <= sanction amount                  (completed works)
  C2  sum of payments <= sanction amount                (works with payments)
  C3  no payment dated before the sanction date         (works with payments)
  C4  recommendation <= sanction <= completion dates    (works with >= 2 dates)
  C5  completion within one year of sanction            (completed: actual end;
      open: age at the snapshot's data_as_of date)
  C6  a completed work has at least one payment         (completed works)
  C7  recommended <= allocated; expenditure <= recommended (MP summary rows)
  C8  referential integrity: LS sanctioned/completed work present in the
      LS recommended file; recommended-file row not FLAG 2 lacking both
      stage and sanction date; and (Phase 5a, not a BLUEPRINT baseline)
      every (portal_id, House) in the files has its own work record.
      BLUEPRINT §2 assumed the portal key is unique across Houses; 136
      keys are shared by an LS and a different RS work and were split
      into two House-qualified works (app/ingest/p6_identity_split.py).
  C9  file reconciles to its portal control total       (control_total rows)
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ingest.p6_identity_split import shared_portal_ids

AMOUNT_TOL = 0.005  # rupees; amounts are stored to 4 dp
ONE_YEAR_DAYS = 365
BLANK = ("", "NA")

_WORKS_SQL = text(
    """
    SELECT ws.work_key, w.portal_id, w.house, ws.lifecycle_status,
           ws.recommended_amount, ws.recommended_date,
           ws.sanction_amount, ws.sanction_date,
           ws.actual_amount, ws.actual_end_date
    FROM work_state ws JOIN work w ON w.work_key = ws.work_key
    WHERE ws.source_snapshot_id = :snap
    """
)
_PAY_SQL = text(
    """
    SELECT p.work_key, COUNT(*) AS n_payments, SUM(p.amount) AS paid_total,
           MIN(p.payment_date) AS first_payment_date,
           COUNT(*) FILTER (WHERE p.payment_date < ws.sanction_date) AS n_before_sanction
    FROM payment p
    JOIN work_state ws ON ws.work_key = p.work_key AND ws.source_snapshot_id = p.source_snapshot_id
    WHERE p.source_snapshot_id = :snap
    GROUP BY p.work_key
    """
)
_RAW_SQL = text(
    """
    SELECT rr.row_no, rr.data FROM raw_row rr JOIN raw_file rf ON rf.id = rr.raw_file_id
    WHERE rf.source_snapshot_id = :snap AND rf.filename = :fname
    """
)
_FILES_SQL = text(
    """
    SELECT rf.filename, ct.measure, ct.status, ct.footer_match, ct.blueprint_match,
           ct.portal_value_crore, ct.computed_value_crore
    FROM control_total ct JOIN raw_file rf ON rf.id = ct.raw_file_id
    WHERE ct.source_snapshot_id = :snap
    """
)
LS_RECOMMENDED_FILE = "works_recommended_LokSabha_alltenures.csv"
MP_SUMMARY_FILE = "mplads_mp_summary_2026-09-19.csv"


def _num(x) -> float | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).replace(",", "").strip()
    if s in BLANK or s.upper() == "N/A":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _rows(df: pd.DataFrame, check: str, rule: str, passed: pd.Series, detail: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "check_code": check,
            "rule": rule,
            "subject_type": "work",
            "subject_key": df.index.astype(str),
            "work_key": df.index.astype(str),
            "house": df["house"].to_numpy(),
            "passed": passed.astype(bool).to_numpy(),
            "detail": detail,
        }
    )


def _d(x):
    return None if pd.isna(x) else str(pd.Timestamp(x).date())


def compute_compliance(
    session: Session, snapshot_a_id: int, snapshot_b_id: int | None, as_of: pd.Timestamp
) -> pd.DataFrame:
    conn = session.connection()
    works = pd.read_sql(_WORKS_SQL, conn, params={"snap": snapshot_a_id}).set_index("work_key").sort_index()
    for c in ("recommended_amount", "sanction_amount", "actual_amount"):
        works[c] = pd.to_numeric(works[c], errors="coerce")
    for c in ("recommended_date", "sanction_date", "actual_end_date"):
        works[c] = pd.to_datetime(works[c])
    pay = pd.read_sql(_PAY_SQL, conn, params={"snap": snapshot_a_id}).set_index("work_key")
    pay["paid_total"] = pd.to_numeric(pay["paid_total"])
    pay["first_payment_date"] = pd.to_datetime(pay["first_payment_date"])

    out: list[pd.DataFrame] = []
    completed = works[works["lifecycle_status"] == "completed"]

    # C1
    c1 = completed[completed["actual_amount"].notna() & completed["sanction_amount"].notna()]
    out.append(
        _rows(
            c1,
            "C1",
            "actual_le_sanction",
            c1["actual_amount"] <= c1["sanction_amount"] + AMOUNT_TOL,
            [{"actual": a, "sanction": s} for a, s in zip(c1["actual_amount"], c1["sanction_amount"])],
        )
    )

    # C2 / C3
    wp = works.join(pay, how="inner")
    c2 = wp[wp["sanction_amount"].notna()]
    out.append(
        _rows(
            c2,
            "C2",
            "payments_le_sanction",
            c2["paid_total"] <= c2["sanction_amount"] + AMOUNT_TOL,
            [
                {"paid_total": p, "sanction": s, "n_payments": int(n)}
                for p, s, n in zip(c2["paid_total"], c2["sanction_amount"], c2["n_payments"])
            ],
        )
    )
    c3 = wp[wp["sanction_date"].notna()]
    out.append(
        _rows(
            c3,
            "C3",
            "no_payment_before_sanction",
            c3["n_before_sanction"] == 0,
            [
                {"n_before_sanction": int(n), "first_payment_date": _d(f), "sanction_date": _d(s)}
                for n, f, s in zip(c3["n_before_sanction"], c3["first_payment_date"], c3["sanction_date"])
            ],
        )
    )

    # C4
    dates = works[["recommended_date", "sanction_date", "actual_end_date"]]
    c4 = works[dates.notna().sum(axis=1) >= 2]
    rec, san, end = c4["recommended_date"], c4["sanction_date"], c4["actual_end_date"]
    ok = ~((rec > san).fillna(False) | (san > end).fillna(False) | (rec > end).fillna(False))
    out.append(
        _rows(
            c4,
            "C4",
            "dates_in_order",
            ok,
            [
                {"recommended": _d(a), "sanction": _d(b), "completion": _d(c)}
                for a, b, c in zip(rec, san, end)
            ],
        )
    )

    # C5
    c5c = completed[completed["sanction_date"].notna() & completed["actual_end_date"].notna()]
    days_c = (c5c["actual_end_date"] - c5c["sanction_date"]).dt.days
    out.append(
        _rows(
            c5c,
            "C5",
            "completed_within_one_year",
            days_c <= ONE_YEAR_DAYS,
            [{"days_sanction_to_completion": int(d)} for d in days_c],
        )
    )
    c5o = works[(works["lifecycle_status"] == "sanctioned") & works["sanction_date"].notna()]
    days_o = (as_of - c5o["sanction_date"]).dt.days
    out.append(
        _rows(
            c5o,
            "C5",
            "open_within_one_year",
            days_o <= ONE_YEAR_DAYS,
            [{"days_open_at_as_of": int(d), "as_of": str(as_of.date())} for d in days_o],
        )
    )

    # C6
    n_pay = pay["n_payments"].reindex(completed.index).fillna(0).astype(int)
    out.append(
        _rows(
            completed,
            "C6",
            "completed_has_payment",
            n_pay > 0,
            [{"n_payments": int(n)} for n in n_pay],
        )
    )

    # C8
    rec_raw = pd.read_sql(_RAW_SQL, conn, params={"snap": snapshot_a_id, "fname": LS_RECOMMENDED_FILE})
    rec_data = pd.DataFrame(list(rec_raw["data"]))
    rec_keys = set(rec_data["WORK_RECOMMENDATION_DTL_ID"].astype(str).str.strip())
    ls_sanc = works[(works["house"] == "LS") & works["lifecycle_status"].isin(("sanctioned", "completed"))]
    in_rec = pd.Series(ls_sanc["portal_id"].isin(rec_keys).to_numpy(), ls_sanc.index)
    out.append(
        _rows(
            ls_sanc,
            "C8",
            "sanctioned_in_recommended_file",
            in_rec,
            [{"in_ls_recommended_file": bool(x)} for x in in_rec],
        )
    )
    # Recommended-file rows are LS: map each to the LS work of that portal ID.
    ls_key_by_portal = dict(
        zip(works.loc[works["house"] == "LS", "portal_id"], works.index[works["house"] == "LS"])
    )
    rec_portal = rec_data["WORK_RECOMMENDATION_DTL_ID"].astype(str).str.strip()
    rec_data["work_key"] = rec_portal.map(ls_key_by_portal)

    def _blank(col):
        return rec_data[col].fillna("").astype(str).str.strip().isin(BLANK)

    flag2 = rec_data["FLAG"].astype(str).str.strip() == "2"
    bad_flag = flag2 & _blank("WORK_STAGE") & _blank("SANCTION_DATE")
    rec_rows = pd.DataFrame(
        {
            "house": "LS",
            "flag": rec_data["FLAG"].to_numpy(),
            "row_no": rec_raw["row_no"].to_numpy(),
            "bad": bad_flag.to_numpy(),
        },
        index=pd.Index(rec_data["work_key"], name="work_key"),
    )
    # Subject is the file ROW (BLUEPRINT counts "rows"), keyed row_no:work_key,
    # still linked to its work.
    r8 = _rows(
        rec_rows,
        "C8",
        "flag2_has_stage_or_sanction",
        ~rec_rows["bad"],
        [{"flag": f, "row_no": int(n)} for f, n in zip(rec_rows["flag"], rec_rows["row_no"])],
    )
    r8["subject_key"] = [f"{n}:{k}" for n, k in zip(rec_rows["row_no"], rec_rows.index)]
    known = set(works.index)
    r8 = r8[r8["work_key"].isin(known)]  # FK safety; every key is a known work (Phase 1/2)
    out.append(r8)

    # C8 identity: every (portal_id, House) that appears in the core files
    # has its OWN work record, and no record mixes two Houses' data. Phase 5
    # found 136 IDs shared across Houses (BLUEPRINT §2 assumed uniqueness);
    # Phase 5a split them (app/ingest/p6_identity_split.py), so this now
    # checks the split holds rather than counting the raw sharing.
    shared = shared_portal_ids(session, snapshot_a_id)
    records = set(zip(works["portal_id"], works["house"]))
    ok, det = [], []
    for pid, house in zip(works["portal_id"], works["house"]):
        houses = shared.at[pid, "houses"] if pid in shared.index else [house]
        own = all((pid, h) in records for h in houses)
        ok.append(own)
        det.append(
            {"portal_id": pid, "houses_using_portal_id": houses, "shared_across_houses": pid in shared.index}
        )
    out.append(_rows(works, "C8", "one_record_per_portal_id_and_house", pd.Series(ok, works.index), det))

    # C7
    if snapshot_b_id is not None:
        mp_raw = pd.read_sql(_RAW_SQL, conn, params={"snap": snapshot_b_id, "fname": MP_SUMMARY_FILE})
        rows7 = []
        for row_no, d in zip(mp_raw["row_no"], mp_raw["data"]):
            alloc = _num(d.get("Allocated Amount (₹)"))
            recd = _num(d.get("Amount Recommended (₹)"))
            spent = _num(d.get("Total Expenditure (₹)"))
            house = {"Lok Sabha": "LS", "Rajya Sabha": "RS"}.get(d.get("House"))
            key = f"{row_no}:{d.get('MP Name')}"
            base = {
                "check_code": "C7",
                "subject_type": "mp",
                "subject_key": key,
                "work_key": None,
                "house": house,
            }
            detail = {"mp": d.get("MP Name"), "allocated": alloc, "recommended": recd, "expenditure": spent}
            if alloc is not None and recd is not None:
                rows7.append(
                    {
                        **base,
                        "rule": "recommended_le_allocated",
                        "passed": recd <= alloc + AMOUNT_TOL,
                        "detail": detail,
                    }
                )
            if recd is not None and spent is not None:
                rows7.append(
                    {
                        **base,
                        "rule": "expenditure_le_recommended",
                        "passed": spent <= recd + AMOUNT_TOL,
                        "detail": detail,
                    }
                )
        out.append(pd.DataFrame(rows7))

    # C9
    files = pd.read_sql(_FILES_SQL, conn, params={"snap": snapshot_a_id})
    out.append(
        pd.DataFrame(
            {
                "check_code": "C9",
                "rule": "reconciles_to_portal_total",
                "subject_type": "file",
                "subject_key": [f"{f}|{m}" for f, m in zip(files["filename"], files["measure"])],
                "work_key": None,
                "house": None,
                "passed": (files["status"] == "pass").to_numpy(),
                "detail": [
                    {
                        "measure": m,
                        "portal_crore": float(p),
                        "computed_crore": float(c),
                        "footer_match": bool(fm),
                        "blueprint_match": bool(bm),
                    }
                    for m, p, c, fm, bm in zip(
                        files["measure"],
                        files["portal_value_crore"],
                        files["computed_value_crore"],
                        files["footer_match"],
                        files["blueprint_match"],
                    )
                ],
            }
        )
    )
    return pd.concat(out, ignore_index=True)


def summarize(res: pd.DataFrame) -> pd.DataFrame:
    g = res.groupby(["check_code", "rule"])
    return pd.DataFrame({"evaluated": g.size(), "failed": g["passed"].apply(lambda s: int((~s).sum()))})


def flag_inconsistent_work_keys(res: pd.DataFrame) -> set[str]:
    """Works carrying a C8 FLAG-2 row -- used as confidence's
    "inconsistent FLAG" data-quality flag."""
    m = (res["check_code"] == "C8") & (res["rule"] == "flag2_has_stage_or_sanction") & ~res["passed"]
    return set(res.loc[m, "work_key"])


def key_collision_work_keys(res: pd.DataFrame) -> set[str]:
    """Works whose (portal_id, House) identity check fails -- a record
    mixing two Houses' data. 0 after the Phase 5a split."""
    m = (res["check_code"] == "C8") & (res["rule"] == "one_record_per_portal_id_and_house") & ~res["passed"]
    return set(res.loc[m, "work_key"])


def referential_gap_work_keys(res: pd.DataFrame) -> set[str]:
    m = (res["check_code"] == "C8") & (res["rule"] == "sanctioned_in_recommended_file") & ~res["passed"]
    return set(res.loc[m, "work_key"])

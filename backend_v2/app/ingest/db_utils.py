"""Small bulk-insert helpers so P0/P1 don't crawl on 100k+-row files."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import insert
from sqlalchemy.orm import Session

CHUNK = 5000


def to_records_with_nulls(df: pd.DataFrame) -> list[dict]:
    """DataFrame.to_dict('records') that actually produces SQL NULL for
    missing values, not a stored NaN.

    `df.where(pd.notna(df), None)` looks like it converts missing values to
    None, but for any column whose dtype is float64 or a pandas nullable
    extension type (Int64, etc.), assigning Python `None` back into that
    column silently gets coerced back to NaN/pd.NA -- those dtypes have no
    other way to represent "missing". psycopg then happily inserts that NaN
    as a literal value into a NUMERIC column (Postgres, unusually, allows
    NaN there) instead of NULL, corrupting every `IS NULL` check and
    aggregate downstream. Casting to `object` dtype FIRST removes that
    restriction -- an object column can hold a real Python None -- so the
    same `.where()` call then does what it looks like it does. Found via a
    real bug (2026-09-24): work_state.recommended_amount was stored as NaN
    for the 361 BLUEPRINT-documented referential-gap rows instead of NULL.
    """
    safe = df.astype(object).where(pd.notna(df), None)
    return safe.to_dict("records")


def bulk_insert(session: Session, model, records: list[dict]) -> None:
    if not records:
        return
    for i in range(0, len(records), CHUNK):
        session.execute(insert(model), records[i : i + CHUNK])

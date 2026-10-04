"""
Low-level CSV helpers shared by P0/P1.

Real files in data/raw/ have quoted fields containing literal embedded
newlines (e.g. WORK_DESCRIPTION) -- naive line-splitting corrupts rows, so
every read here goes through pandas.read_csv, which parses quoted fields
correctly (including embedded newlines) with the default C engine.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv_body_and_footer(
    path: Path, delimiter: str, footer_col: str | None
) -> tuple[pd.DataFrame, dict | None]:
    """Reads the whole file, then -- if footer_col is set -- splits off a
    trailing footer row (identified by: footer_col non-blank, every other
    column blank/NaN) before any contract check runs. Returns (body_df,
    footer_dict_or_None). body_df's original row numbers (1-based, header
    excluded) are preserved in a `_row_no` column.
    """
    df = pd.read_csv(
        path,
        sep=delimiter,
        dtype=str,
        keep_default_na=False,
        na_values=[""],
        engine="c",
    )
    df.insert(0, "_row_no", np.arange(1, len(df) + 1))

    if footer_col is None or footer_col not in df.columns or df.empty:
        return df, None

    last = df.iloc[-1]
    other_cols = [c for c in df.columns if c not in ("_row_no", footer_col)]
    is_footer = (
        pd.notna(last[footer_col])
        and last[footer_col] != ""
        and all(pd.isna(last[c]) or last[c] == "" for c in other_cols)
    )
    if not is_footer:
        return df, None

    footer_dict = last.to_dict()
    body_df = df.iloc[:-1].reset_index(drop=True)
    return body_df, footer_dict


def to_amount(value) -> float | None:
    """Parses a raw amount cell (may be '', NaN, or a numeric string with
    trailing float noise) into a float, or None if blank/unparseable."""
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    text = str(value).strip()
    if text == "" or text.upper() == "NA":
        return None
    try:
        return float(text)
    except ValueError:
        return None

"""
Pandera data contracts, one schema built per FileSpec (per raw file
category). Phase 1's contract scope is deliberately narrow -- key
uniqueness/non-null, amount parseability/non-null for the 9 core files, and
(for files using the in-file House column) a closed LS/RS vocabulary check.
Descriptive text columns are not schema-validated: BLUEPRINT.md §6
explicitly keeps rule/contract checks separate from later business-rule/
compliance checks, and P1's job is "does this row parse and key correctly",
not "is this a plausible work record".

A row failing any check here is never silently dropped or coerced -- it is
routed to import_reject with the specific reason.
"""

from __future__ import annotations

import pandas as pd
import pandera as pa

from .constants import HOUSE_COLUMN_VALUE_MAP, FileSpec
from .csv_utils import to_amount


def build_schema(spec: FileSpec) -> pa.DataFrameSchema:
    columns: dict[str, pa.Column] = {
        "_row_no": pa.Column(int, unique=True, nullable=False),
    }

    if spec.key_col:
        columns[spec.key_col] = pa.Column(str, nullable=False, unique=spec.key_unique)

    if spec.body_amount_col:
        columns[spec.body_amount_col] = pa.Column(
            float,
            nullable=False,
            checks=pa.Check(lambda s: s.notna(), element_wise=False, error="amount must parse as a number"),
        )

    if spec.house_mode == "column" and spec.house_col:
        allowed = set(HOUSE_COLUMN_VALUE_MAP.keys())
        columns[spec.house_col] = pa.Column(
            str,
            nullable=False,
            checks=pa.Check(
                lambda s: s.str.strip().str.lower().isin(allowed),
                element_wise=False,
                error=f"House must be one of {sorted(HOUSE_COLUMN_VALUE_MAP)}",
            ),
        )

    return pa.DataFrameSchema(columns, strict=False, coerce=False)


def prepare_for_validation(df: pd.DataFrame, spec: FileSpec) -> pd.DataFrame:
    """Pre-parses amount columns to float (NaN where unparseable/blank) so
    the schema's non-null check can catch both "blank" and "garbage" in one
    place, and leaves everything else as raw strings."""
    out = df.copy()
    if spec.key_col and spec.key_col in out.columns:
        out[spec.key_col] = out[spec.key_col].astype(str).str.strip()
        out.loc[out[spec.key_col].isin(["", "nan", "None"]), spec.key_col] = pd.NA
    if spec.body_amount_col and spec.body_amount_col in out.columns:
        out[spec.body_amount_col] = out[spec.body_amount_col].map(to_amount)
    return out


def validate(df: pd.DataFrame, spec: FileSpec) -> tuple[pd.DataFrame, list[dict]]:
    """Returns (valid_rows_df, reject_records). reject_records are dicts
    with row_no, reason_code, reason_detail, raw_data -- ready for
    import_reject."""
    schema = build_schema(spec)
    prepared = prepare_for_validation(df, spec)

    try:
        schema.validate(prepared, lazy=True)
        return df, []
    except pa.errors.SchemaErrors as exc:
        failures = exc.failure_cases
        bad_row_nos: set[int] = set()
        reasons: dict[int, list[str]] = {}
        for _, fc in failures.iterrows():
            idx = fc["index"]
            row_no = int(prepared.loc[idx, "_row_no"]) if idx in prepared.index else None
            if row_no is None:
                continue
            bad_row_nos.add(row_no)
            reasons.setdefault(row_no, []).append(f"{fc['column']}: {fc['check']}")

        reject_records = []
        for row_no in sorted(bad_row_nos):
            raw = df.loc[df["_row_no"] == row_no].drop(columns=["_row_no"]).iloc[0].to_dict()
            reject_records.append(
                {
                    "row_no": row_no,
                    "reason_code": "SCHEMA_VIOLATION",
                    "reason_detail": "; ".join(reasons[row_no]),
                    "raw_data": raw,
                }
            )
        valid_df = df[~df["_row_no"].isin(bad_row_nos)].reset_index(drop=True)
        return valid_df, reject_records

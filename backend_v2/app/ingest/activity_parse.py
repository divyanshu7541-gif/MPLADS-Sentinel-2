"""
P3 Normalise: parse the official ACTIVITY_NAME code-prefix into a clean
activity type string (BLUEPRINT.md §2: "ACTIVITY_NAME in work files is a
code prefix plus the type text... parsing gives 115 types").

This is NOT keyword matching (see backend/app/core/config.py's
WORK_CATEGORY_KEYWORDS / backend/app/services/data_service.py's
infer_category -- reference only, explicitly not to be imitated per the
Phase 2 brief). It is a deterministic string parse, verified against the
real Snapshot A files before being written here (2026-09-24):

  - "WS/<mp code>/<FY>-<FY>/<id>-<type text>"  (e.g. works_recommended,
    works_sanctioned, works_completed) -> strip everything up to and
    including the LAST "<digits>-" run, keep the rest.
  - "NA-<type text>"                          (unsanctioned rows in
    works_recommended) -> strip the literal "NA-" prefix.
  - "<type text>" with no prefix at all       (expenditure files) ->
    already clean, used as-is.

Verified: union of parsed types across all 7 Snapshot A files that carry
ACTIVITY_NAME = 115 distinct strings, exactly matching BLUEPRINT.md §2's
stated figure; 0 of 352,504 rows retained a leftover digit (100% parse
rate, matching "Parse rate reported (100% for type and district in
Snapshot A)", BLUEPRINT.md §5 P3 gate).
"""

from __future__ import annotations

import re

import pandas as pd

_NA_RE = re.compile(r"^\s*NA-(.+)$", re.DOTALL)
_ID_RE = re.compile(r"^.*\d-(.+)$", re.DOTALL)  # greedy -- lands on the LAST digit-hyphen


def parse_activity_type(raw: str) -> str:
    m = _NA_RE.match(raw)
    if m:
        return m.group(1).strip()
    m = _ID_RE.match(raw)
    if m:
        return m.group(1).strip()
    return raw.strip()


def parse_activity_type_series(s: pd.Series) -> pd.Series:
    return s.map(parse_activity_type)

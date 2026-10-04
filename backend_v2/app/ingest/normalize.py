"""
P3 Normalise helpers: district key from IDA name, date parsing, and the
name normalization used for the MP roster join. Every rule here was
verified against Snapshot A before being written (2026-09-24 research):

- District key = text before the IDA name's first "(" -- 0 of 352,504
  IDA_NAME values across the 7 core files lacked a "(", and this parse
  yields exactly 751 distinct district keys in works_sanctioned_LS,
  matching BLUEPRINT.md §15's stated figure exactly.
- Two date formats are used, each 100% consistent within its columns:
  RECOMMENDATION_DATE/SANCTION_DATE/EXPENDITURE_DATE/ACTUAL_END_DATE use
  "DD-Mon-YYYY" (or the literal "NA"); TENURE_START_DATE/TENURE_END_DATE
  use "Mon DD, YYYY HH:MM:SS AM/PM".
- Name normalization (strip, collapse whitespace, uppercase) against the
  roster's CAPTION gives a 100% match rate on every file checked
  (allocation LS/RS, works_recommended_LS, works_sanctioned_RS), matching
  BLUEPRINT.md §9's stated 100% figure.
"""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd

_DISTRICT_RE = re.compile(r"^(.*?)\(")
_DAY_MON_YEAR = "%d-%b-%Y"
_TENURE_TS = "%b %d, %Y %I:%M:%S %p"


def parse_district_key(ida_name: str) -> str | None:
    if not ida_name:
        return None
    m = _DISTRICT_RE.match(ida_name)
    return (m.group(1) if m else ida_name).strip() or None


def normalize_name(name: str | None) -> str:
    if _is_blank(name):
        return ""
    return re.sub(r"\s+", " ", str(name).strip()).upper()


def _is_blank(value) -> bool:
    """True for None, '', and NaN -- outer-merged DataFrame columns hold a
    float NaN for missing values, not None, and `not float('nan')` is
    False (NaN is truthy), so a plain `if not value` check misses it."""
    if value is None:
        return True
    if isinstance(value, float):
        return value != value  # NaN != NaN
    return False


def parse_day_mon_year(value: str | None) -> dt.date | None:
    if _is_blank(value):
        return None
    text = str(value).strip()
    if not text or text.upper() == "NA":
        return None
    try:
        return dt.datetime.strptime(text, _DAY_MON_YEAR).date()
    except ValueError:
        return None


def parse_tenure_timestamp(value: str | None) -> dt.date | None:
    if _is_blank(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, _TENURE_TS).date()
    except ValueError:
        return None


def normalize_description(text: str | None) -> str | None:
    if _is_blank(text):
        return None
    return re.sub(r"\s+", " ", str(text).strip().lower()) or None


def parse_day_mon_year_series(s: pd.Series) -> pd.Series:
    return s.map(parse_day_mon_year)


def parse_tenure_timestamp_series(s: pd.Series) -> pd.Series:
    return s.map(parse_tenure_timestamp)

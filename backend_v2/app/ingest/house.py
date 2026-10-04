"""
House derivation -- two filename-independent, data-driven rules, each
authoritative for its own sources (neither is a fallback/exception to the
other):

  1. Snapshot A's 9 core files + calamity: house comes from the SOURCE
     FILENAME (`*_LokSabha_*` -> 'LS', `*_RajyaSabha_*` -> 'RS') -- never
     from constituency presence/absence or any other heuristic.
  2. prior_cycle and Snapshot B (none of which match the LokSabha/RajyaSabha
     filename pattern): house comes from the file's own explicit House/
     HOUSE column. That column goes through the same pandera contract-check
     rigor as any other field (app/ingest/schemas.py) -- non-null, closed
     LS/RS vocabulary -- before this module ever sees it, so a row reaching
     here with house_mode == 'column' already has a valid value.
"""

from __future__ import annotations

from .constants import HOUSE_COLUMN_VALUE_MAP, FileSpec


def derive_house_for_file(spec: FileSpec) -> str | None:
    """For house_mode == 'filename' files, the whole file has one house
    value -- return it. For house_mode == 'column' files, return None (the
    caller must look at each row's house column instead)."""
    if spec.house_mode == "filename":
        return spec.house_value
    return None


def derive_house_for_row(raw_value: str) -> str:
    """Maps an already-validated in-file House/HOUSE cell to 'LS'/'RS'.
    Only ever called on values that passed the pandera contract check, so
    the lookup cannot miss -- a KeyError here would mean the schema check
    was bypassed, which is a bug, not a data problem."""
    return HOUSE_COLUMN_VALUE_MAP[raw_value.strip().lower()]

"""
Registry of every raw file Phase 1 ingests, and the control totals it
reconciles against. Everything numeric here is transcribed from
BLUEPRINT.md and cross-checked against the real files' own footers during
research for this phase (see docs/phase1_reconciliation.md once the
pipeline has run) -- do not hand-edit without re-checking against
BLUEPRINT.md §2.

Amounts in the raw files are plain rupees. BLUEPRINT.md's control totals are
in INR crore (1 crore = 1e7 rupees).
"""

from __future__ import annotations

from dataclasses import dataclass

CRORE = 10_000_000  # 1 crore in rupees


@dataclass(frozen=True)
class SnapshotSpec:
    code: str
    label: str
    data_as_of: str | None  # ISO date string, or None if not a single date
    retrieval_method: str
    notes: str


SNAPSHOTS: dict[str, SnapshotSpec] = {
    "snapshot_a": SnapshotSpec(
        code="snapshot_a",
        label="Snapshot A -- portal grid exports",
        data_as_of="2026-08-30",
        retrieval_method="Manual portal export (team statement; exact portal URL not recorded "
        "in this pipeline run)",
        notes="Recommended, sanctioned, completed, expenditure, allocation, roster, calamity. "
        "18th Lok Sabha and sitting/nominated Rajya Sabha only.",
    ),
    "snapshot_b": SnapshotSpec(
        code="snapshot_b",
        label="Snapshot B -- dashboard exports",
        data_as_of="2026-09-19",
        retrieval_method="Manual portal export (team statement; exact portal URL not recorded "
        "in this pipeline run)",
        notes="Later state than Snapshot A by 19 days. Used in Phase 1 only to gap-fill the missing "
        "Rajya Sabha recommended-works file (BLUEPRINT.md §2 hard limit 6).",
    ),
    "prior_cycle": SnapshotSpec(
        code="prior_cycle",
        label="Prior-cycle backlog (semicolon file)",
        data_as_of=None,
        retrieval_method="Manual portal export (team statement)",
        notes="60,359 works recommended April 2023 to March 2024. Descriptive table only -- no work key, "
        "84% never sanctioned and lapsed with the tenure (BLUEPRINT.md §9).",
    ),
    "macro": SnapshotSpec(
        code="macro",
        label="Parliamentary answers (macro reference)",
        data_as_of=None,
        retrieval_method="Manual transcription from Rajya Sabha session answers (team statement)",
        notes="State and national totals, FY2014-15 to FY2019-20. Macro reference only.",
    ),
}


@dataclass(frozen=True)
class FileSpec:
    snapshot_code: str
    relative_path: str  # relative to Settings.data_dir
    category: str
    delimiter: str = ","
    has_footer: bool = False
    footer_col: str | None = "Total_Amt"
    is_core: bool = False  # one of the 9 files reconciled against BLUEPRINT.md §2
    control_measure: str | None = None  # BLUEPRINT.md §2 row label
    control_value_crore: float | None = None
    body_amount_col: str | None = None  # column summed for reconciliation
    key_col: str | None = None  # work-key column, if this file carries one
    key_unique: bool = True  # False for expenditure: a work legitimately gets multiple payments
    house_mode: str | None = None  # 'filename' | 'column' | None
    house_value: str | None = None  # for house_mode == 'filename'
    house_col: str | None = None  # for house_mode == 'column'
    feeds_work: bool = False
    mp_name_col: str | None = None
    activity_col: str | None = None
    description_col: str | None = None


# ---------------------------------------------------------------------------
# Snapshot A -- the 9 core files (BLUEPRINT.md §2 control totals, all verified
# against each file's own footer during Phase 1 research: footer/1e7 equals
# the crore value below in every case).
# ---------------------------------------------------------------------------
CORE_FILES: list[FileSpec] = [
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/works_recommended_LokSabha_alltenures.csv",
        category="works_recommended",
        has_footer=True,
        is_core=True,
        control_measure="Recommended (LS)",
        control_value_crore=5638.66,
        body_amount_col="RECOMMENDED_AMOUNT",
        key_col="WORK_RECOMMENDATION_DTL_ID",
        house_mode="filename",
        house_value="LS",
        feeds_work=True,
        mp_name_col="MP_NAME",
        activity_col="ACTIVITY_NAME",
        description_col="WORK_DESCRIPTION",
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/works_sanctioned_LokSabha_alltenures.csv",
        category="works_sanctioned",
        has_footer=True,
        is_core=True,
        control_measure="Sanctioned LS",
        control_value_crore=4117.67,
        body_amount_col="SANCTION_AMOUNT",
        key_col="WORK_RECOMMENDATION_DTL_ID",
        house_mode="filename",
        house_value="LS",
        feeds_work=True,
        mp_name_col="MP_NAME",
        activity_col="ACTIVITY_NAME",
        description_col="WORK_DESCRIPTION",
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/works_sanctioned_RajyaSabha_alltenures.csv",
        category="works_sanctioned",
        has_footer=True,
        is_core=True,
        control_measure="Sanctioned RS",
        control_value_crore=1693.24,
        body_amount_col="SANCTION_AMOUNT",
        key_col="WORK_RECOMMENDATION_DTL_ID",
        house_mode="filename",
        house_value="RS",
        feeds_work=True,
        mp_name_col="MP_NAME",
        activity_col="ACTIVITY_NAME",
        description_col="WORK_DESCRIPTION",
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/works_completed_LokSabha_alltenures.csv",
        category="works_completed",
        has_footer=True,
        is_core=True,
        control_measure="Completed LS",
        control_value_crore=1632.00,
        body_amount_col="ACTUAL_AMOUNT",
        key_col="WORK_RECOMMENDATION_DTL_ID",
        house_mode="filename",
        house_value="LS",
        feeds_work=True,
        mp_name_col="MP_NAME",
        activity_col="ACTIVITY_NAME",
        description_col="WORK_DESCRIPTION",
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/works_completed_RajyaSabha_alltenures.csv",
        category="works_completed",
        has_footer=True,
        is_core=True,
        control_measure="Completed RS",
        control_value_crore=755.42,
        body_amount_col="ACTUAL_AMOUNT",
        key_col="WORK_RECOMMENDATION_DTL_ID",
        house_mode="filename",
        house_value="RS",
        feeds_work=True,
        mp_name_col="MP_NAME",
        activity_col="ACTIVITY_NAME",
        description_col="WORK_DESCRIPTION",
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/expenditure_LokSabha_alltenures.csv",
        category="expenditure",
        has_footer=True,
        is_core=True,
        control_measure="Payments LS",
        control_value_crore=2736.15,
        body_amount_col="FUND_DISBURSED_AMT",
        key_col="WORK_RECOMMENDATION_DTL_ID",
        key_unique=False,  # a work can receive multiple payments (BLUEPRINT.md §2: up to 4+ payees)
        house_mode="filename",
        house_value="LS",
        feeds_work=True,
        mp_name_col="MP_NAME",
        activity_col="ACTIVITY_NAME",
        description_col=None,
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/expenditure_RajyaSabha_alltenures.csv",
        category="expenditure",
        has_footer=True,
        is_core=True,
        control_measure="Payments RS",
        control_value_crore=1232.93,
        body_amount_col="FUND_DISBURSED_AMT",
        key_col="WORK_RECOMMENDATION_DTL_ID",
        key_unique=False,  # a work can receive multiple payments (BLUEPRINT.md §2: up to 4+ payees)
        house_mode="filename",
        house_value="RS",
        feeds_work=True,
        mp_name_col="MP_NAME",
        activity_col="ACTIVITY_NAME",
        description_col=None,
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/mp_allocation_LokSabha_alltenures.csv",
        category="mp_allocation",
        has_footer=True,
        is_core=True,
        control_measure="Allocation LS",
        control_value_crore=8318.06,
        body_amount_col="ALLOCATED_AMT",
        key_col=None,
        house_mode="filename",
        house_value="LS",
        feeds_work=False,
        mp_name_col="MP_NAME",
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/mp_allocation_RajyaSabha_alltenures.csv",
        category="mp_allocation",
        has_footer=True,
        is_core=True,
        control_measure="Allocation RS",
        control_value_crore=3363.85,
        body_amount_col="ALLOCATED_AMT",
        key_col=None,
        house_mode="filename",
        house_value="RS",
        feeds_work=False,
        mp_name_col="MP_NAME",
    ),
]

# ---------------------------------------------------------------------------
# Snapshot A -- "Core reference" files (BLUEPRINT.md §2 Sources-and-roles
# table): registered, parsed and contract-checked, but not reconciled
# against a BLUEPRINT.md control total (none is given). calamity still gets
# a self-check against its own footer as a bonus integrity check.
# ---------------------------------------------------------------------------
REFERENCE_FILES: list[FileSpec] = [
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/calamity_LokSabha_alltenures.csv",
        category="calamity",
        has_footer=True,
        body_amount_col="CONSENTED_AMOUNT",
        house_mode="filename",
        house_value="LS",
        mp_name_col="MP_NAME",
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/calamity_RajyaSabha_alltenures.csv",
        category="calamity",
        has_footer=True,
        body_amount_col="CONSENTED_AMOUNT",
        house_mode="filename",
        house_value="RS",
        mp_name_col="MP_NAME",
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/mp_names_all_states.csv",
        category="mp_roster",
        has_footer=False,
    ),
    FileSpec(
        snapshot_code="snapshot_a",
        relative_path="raw/snapshot_a/states.csv",
        category="states",
        has_footer=False,
    ),
]

# ---------------------------------------------------------------------------
# Snapshot B -- second snapshot, delta and gap-fill. House is derived from
# the in-file House column (filenames don't encode it). Only
# mplads_recommended_works feeds `work` (Rajya Sabha rows only -- the
# documented gap-fill for the missing Snapshot A RS recommended file).
# ---------------------------------------------------------------------------
SNAPSHOT_B_FILES: list[FileSpec] = [
    FileSpec(
        snapshot_code="snapshot_b",
        relative_path="raw/snapshot_b/mplads_recommended_works_2026-09-19.csv",
        category="snapshot_b_recommended",
        has_footer=False,
        key_col="Work ID",
        house_mode="column",
        house_col="House",
        feeds_work=True,  # RS rows only -- filtered in pipeline.py
        mp_name_col="MP Name",
        description_col="Work Description",
    ),
    FileSpec(
        snapshot_code="snapshot_b",
        relative_path="raw/snapshot_b/mplads_completed_works_2026-09-19.csv",
        category="snapshot_b_completed",
        has_footer=False,
        key_col="Work ID",
        house_mode="column",
        house_col="House",
        feeds_work=False,
        mp_name_col="MP Name",
        description_col="Work Description",
    ),
    FileSpec(
        snapshot_code="snapshot_b",
        relative_path="raw/snapshot_b/mplads_expenditures_2026-09-19.csv",
        category="snapshot_b_expenditure",
        has_footer=False,
        key_col=None,
        house_mode="column",
        house_col="House",
        feeds_work=False,
        mp_name_col="MP Name",
    ),
    FileSpec(
        snapshot_code="snapshot_b",
        relative_path="raw/snapshot_b/mplads_mp_summary_2026-09-19.csv",
        category="snapshot_b_mp_summary",
        has_footer=False,
        key_col=None,
        house_mode="column",
        house_col="House",
        feeds_work=False,
        mp_name_col="MP Name",
    ),
]

# ---------------------------------------------------------------------------
# Prior-cycle backlog -- semicolon-delimited, no footer, no work key.
# House comes from its own in-file HOUSE column.
# ---------------------------------------------------------------------------
PRIOR_CYCLE_FILES: list[FileSpec] = [
    FileSpec(
        snapshot_code="prior_cycle",
        relative_path="raw/prior_cycle/prior_cycle_backlog_2023-24.csv",
        category="prior_cycle",
        delimiter=";",
        has_footer=False,
        key_col=None,
        house_mode="column",
        house_col="HOUSE",
        feeds_work=False,
        mp_name_col="MP NAME",
    ),
]

# ---------------------------------------------------------------------------
# Macro reference -- three small Rajya Sabha session answer annexures.
# Registered and parsed; no work key, no house tagging (not work rows), no
# dedicated table populated this phase (macro_reference is a later phase).
# ---------------------------------------------------------------------------
MACRO_FILES: list[FileSpec] = [
    FileSpec(
        snapshot_code="macro",
        relative_path="raw/macro/RS-Session-251-AU3002-Annexure-I.csv",
        category="macro_state_totals",
        has_footer=True,
        footer_col=None,  # footer row is a literal "Total" row, not a Total_Amt column
    ),
    FileSpec(
        snapshot_code="macro",
        relative_path="raw/macro/RS_Session_247_AS_175.csv",
        category="macro_unspent_balance",
        has_footer=True,
        footer_col=None,
    ),
    FileSpec(
        snapshot_code="macro",
        relative_path="raw/macro/RS_Session_247_AU_2719.csv",
        category="macro_fy_totals",
        has_footer=True,
        footer_col=None,
    ),
]

ALL_FILES: list[FileSpec] = CORE_FILES + REFERENCE_FILES + SNAPSHOT_B_FILES + PRIOR_CYCLE_FILES + MACRO_FILES

# House values a house_mode == 'column' file's raw text may take, mapped to
# the canonical 'LS'/'RS' work.house value. Anything else is a contract
# violation -> import_reject, never silently coerced.
HOUSE_COLUMN_VALUE_MAP: dict[str, str] = {
    "lok sabha": "LS",
    "rajya sabha": "RS",
}

# Reconciliation tolerance: footer totals carry float-summation artifacts
# (e.g. 27361507267.449966); BLUEPRINT.md's own crore figures are rounded to
# 2 decimal places. 1 rupee absolute tolerance for footer-vs-body, 0.01 crore
# (== INR 100,000) tolerance for computed-vs-BLUEPRINT.
FOOTER_TOLERANCE_RUPEES = 1.0
BLUEPRINT_TOLERANCE_CRORE = 0.01

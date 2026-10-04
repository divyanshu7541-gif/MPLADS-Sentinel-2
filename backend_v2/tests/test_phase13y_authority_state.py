"""
District authorities are filed under the state they are in (Phase 13.y, step 4).

The bug: the ingest gave each authority the alphabetically-first MP state seen
in its portal rows (AGRA -> Gujarat, BUDAUN -> Jammu and Kashmir); 52
authorities and 11,783 scored works of run 1 sat in the wrong state's peer
groups. The fix (app/ingest/authority_state.py) resolves the authority's own
location in the ingest, before any analysis reads it.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest
from sqlalchemy import text

from app.ingest.authority_state import resolve_authority_states, resolve_state

LGD = {
    "AGRA": {"Uttar Pradesh"},
    "KARNAL": {"Haryana"},
    "PRATAPGARH": {"Uttar Pradesh", "Rajasthan"},
}


# ---- the rule --------------------------------------------------------------------------------


def test_the_old_rule_would_pick_the_alphabetically_first_mp_state_the_fix_does_not():
    rows = Counter({"Gujarat": 3, "Uttar Pradesh": 900, "Delhi": 2})
    assert min(rows) == "Delhi"  # what sorted(pairs) used to store (here: Delhi)
    assert resolve_state("AGRA", rows, rows, LGD) == ("Uttar Pradesh", "lgd_unique_name")


def test_a_unique_lgd_district_beats_even_the_majority_of_mp_rows():
    # KARNAL: 4 rows from Uttarakhand MPs, 3 from Haryana; Karnal is in Haryana.
    rows = Counter({"Uttarakhand": 4, "Haryana": 3})
    assert resolve_state("KARNAL", rows, rows, LGD) == ("Haryana", "lgd_unique_name")


def test_an_ambiguous_district_name_takes_the_candidate_state_with_most_lok_sabha_rows():
    ls = Counter({"Uttar Pradesh": 40, "Bihar": 90})  # Bihar is not a candidate
    assert resolve_state("PRATAPGARH", ls, ls, LGD) == ("Uttar Pradesh", "lgd_name_modal_ls_state")
    ls = Counter({"Rajasthan": 12, "Uttar Pradesh": 5})
    assert resolve_state("PRATAPGARH", ls, ls, LGD)[0] == "Rajasthan"


def test_no_lgd_match_falls_back_to_lok_sabha_rows_then_any_rows_then_unresolved():
    assert resolve_state("NEWDIST", Counter({"Assam": 5, "Bihar": 1}), Counter(), LGD) == (
        "Assam",
        "modal_ls_state",
    )
    assert resolve_state("NEWDIST", Counter(), Counter({"Goa": 2}), LGD) == ("Goa", "modal_any_state")
    # a tie is broken by name, so the result never depends on row order
    assert resolve_state("NEWDIST", Counter({"Kerala": 2, "Assam": 2}), Counter(), LGD)[0] == "Assam"
    assert resolve_state("NEWDIST", Counter(), Counter(), LGD) == (None, "unresolved")


def test_district_aliases_apply_before_the_lgd_lookup():
    # "Kancheepuram" is the portal's spelling of LGD "Kanchipuram" (DISTRICT_ALIASES)
    lgd = {"KANCHIPURAM": {"Tamil Nadu"}}
    rows = Counter({"Puducherry": 1, "Tamil Nadu": 30})
    assert resolve_state("Kancheepuram", rows, rows, lgd) == ("Tamil Nadu", "lgd_unique_name")


# ---- the database --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def published_run(db_session):
    return db_session.execute(text("SELECT run_id FROM published_run")).scalar_one()


def test_every_authority_is_stored_in_its_resolved_location(db_session):
    """The pipeline's state and the map's independently resolved state agree for all 774."""
    rows = db_session.execute(
        text(
            "SELECT da.ida_name, da.state_id, ag.resolved_state_id, ag.stored_state_id, ag.state_differs "
            "FROM district_authority da JOIN authority_geo ag ON ag.district_authority_id = da.id"
        )
    ).all()
    assert len(rows) == 774
    assert [r.ida_name for r in rows if r.state_id != r.resolved_state_id] == []
    assert [r.ida_name for r in rows if r.stored_state_id != r.state_id or r.state_differs] == []


@pytest.mark.parametrize(
    "prefix, state",
    [
        ("AGRA(", "Uttar Pradesh"),  # was Gujarat
        ("BUDAUN(", "Uttar Pradesh"),  # was Jammu And Kashmir
        ("AMRITSAR(", "Punjab"),  # was Chandigarh
        ("KARNAL(", "Haryana"),  # most of its rows come from Uttarakhand MPs
        ("HAMIRPUR(DISTRICT MAGISTRATE HAMIRPUR_2)", "Uttar Pradesh"),  # ambiguous name
    ],
)
def test_known_authorities(db_session, prefix, state):
    got = db_session.execute(
        text(
            "SELECT s.name FROM district_authority da JOIN state s ON s.id = da.state_id "
            "WHERE da.ida_name LIKE :p || '%'"
        ),
        {"p": prefix},
    ).scalar_one()
    assert got == state


def test_the_published_runs_peer_groups_use_the_corrected_state(db_session, published_run):
    """Every state-level peer group key of the published run carries the work's
    authority's (corrected) state -- the risk is built on the real location."""
    rows = db_session.execute(
        text(
            "SELECT wc.group_key, da.state_id FROM work_context wc JOIN work w ON w.work_key = wc.work_key "
            "JOIN district_authority da ON da.id = w.district_authority_id "
            "WHERE wc.run_id = :r AND wc.group_key LIKE '%state_id=%'"
        ),
        {"r": published_run},
    ).all()
    assert len(rows) > 50_000
    wrong = [g for g, st in rows if int(re.search(r"state_id=(\d+)", g).group(1)) != st]
    assert wrong == []


def test_the_resolver_is_idempotent_on_the_built_database(db_session):
    """Re-running it (as run_ingest.py does) finds nothing left to correct."""
    try:
        out = resolve_authority_states(db_session)
        assert out["corrected"] == 0, out["corrections"][:5]
        assert out["methods"].get("unresolved", 0) == 0
    finally:
        db_session.rollback()


def test_the_ingest_no_longer_assigns_the_first_seen_mp_state():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "ingest" / "reference_load.py").read_text("utf-8")
    body = src[src.index("def load_district_authorities") :]
    body = body[: body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
    assert "state_id=None" in body and "name_to_id" not in body
    run_ingest = (Path(__file__).resolve().parents[1] / "scripts" / "run_ingest.py").read_text("utf-8")
    assert "resolve_authority_states(" in run_ingest

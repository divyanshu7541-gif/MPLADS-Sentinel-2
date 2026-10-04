"""
Frontend contract test suite, generated from docs/frontend_contract.md.

Runs every one of the 32 endpoints the frontend (frontend/src/services/api.js)
calls and asserts:
  - the route exists (no 404/405 for an unknown route),
  - it accepts the method + params the contract documents,
  - the top-level response shape (dict keys, or "it's a JSON array") matches
    the contract, and (Phase 12) the documented item-level shapes too.

Phase 12 cutover: every handler is DB-backed, so the suite now runs against
REAL data -- path/query placeholders ({work_key}, {mp}, ...) are filled from
the served run's served_work, and each list the contract documents must be
non-empty. It needs the full pipeline (CI runs it; scripts/run_serving.py
last). If a shape has to change, update docs/frontend_contract.md first and
this file to match.
"""

import pytest
from sqlalchemy import text

# Each entry mirrors one row of docs/frontend_contract.md's Endpoints table.
# `keys`: top-level dict keys expected (dict responses).
# `is_array`: True for the handful of endpoints that return a bare JSON array.
CONTRACT = [
    {
        "n": 1,
        "method": "GET",
        "path": "/api/summary",
        "keys": {
            "total_records",
            "total_amount",
            "risk_distribution",
            "critical_count",
            "high_count",
            "moderate_count",
            "low_count",
            "high_priority",
            "review_recommended",
            "normal",
            "flag_rate",
            "average_risk",
            "high_risk_percentage",
            "average_confidence",
            "category_distribution",
            "state_distribution",
            "stage_distribution",
            "priority_distribution",
            "top_signals",
            "model_version",
        },
    },
    {
        "n": 2,
        "method": "GET",
        "path": "/api/queue",
        "params": {"state": "{state}", "page": 1, "page_size": 10},
        "keys": {"records", "total", "page", "page_size", "total_pages"},
    },
    {
        "n": 3,
        "method": "GET",
        "path": "/api/record/{work_key}",
        "keys": {
            "record_id",
            "source_record",
            "normalized_record",
            "risk_assessment",
            "context",
            "evidence_items",
            "evidence_chain",
            "investigation_recommendation",
            "derived_features",
            "priority",
            "related_records",
            "risk_history",
        },
    },
    {
        "n": 4,
        "method": "GET",
        "path": "/api/analytics",
        "keys": {
            "priority_distribution",
            "risk_distribution",
            "stage_distribution",
            "category_distribution",
            "amount_by_priority",
            "amount_by_risk",
            "state_flags",
            "constituency_flags",
            "mp_stats",
            "category_flags",
            "signal_distribution",
            "amount_histogram",
            "confidence_distribution",
            "average_confidence",
            "model_version",
        },
    },
    {
        "n": 5,
        "method": "GET",
        "path": "/api/data-health",
        "keys": {
            "signals_enabled",
            "signals_unavailable",
            "category_inference",
            "risk_summary",
            "confidence_summary",
        },
    },
    {"n": 6, "method": "GET", "path": "/api/stages", "keys": {"stages"}},
    {"n": 7, "method": "GET", "path": "/api/constituencies", "keys": {"constituencies"}},
    {"n": 8, "method": "GET", "path": "/api/states", "keys": {"states"}},
    {"n": 9, "method": "GET", "path": "/api/map-data", "params": {"state": "{state}"}, "is_array": True},
    {"n": 10, "method": "GET", "path": "/api/map-works", "params": {"state": "{state}"}, "is_array": True},
    {
        "n": 11,
        "method": "GET",
        "path": "/api/map-filters",
        "keys": {"states", "stages", "risk_levels", "priorities", "total_constituencies", "total_works"},
    },
    {"n": 12, "method": "GET", "path": "/api/graph-data", "keys": {"nodes", "edges"}},
    {
        "n": 13,
        "method": "POST",
        "path": "/api/investigate/{work_key}",
        "json": {"decision": "reviewed", "reviewer": "tester", "note": "n/a"},
        "keys": {"status", "entry"},
    },
    {"n": 14, "method": "GET", "path": "/api/audit-trail", "is_array": True},
    {"n": 15, "method": "GET", "path": "/api/mps", "keys": {"mps"}},
    {
        "n": 16,
        "method": "GET",
        "path": "/api/mp-performance/{mp}",
        "keys": {
            "mp_name",
            "constituency",
            "state",
            "total_works",
            "completed_works",
            "sanctioned_works",
            "recommended_works",
            "completion_rate",
            "total_recorded_amount",
            "average_work_amount",
            "completed_amount",
            "stage_distribution",
            "stage_amounts",
            "category_distribution",
            "category_amounts",
            "risk_distribution",
            "critical_count",
            "high_priority_count",
            "review_recommended_count",
            "low_risk_count",
            "high_priority",
            "review_recommended",
            "normal",
            "risk_rate",
            "amount_by_priority",
            "signal_summary",
            "trend",
            "top_flagged_works",
        },
    },
    {
        "n": 17,
        "method": "GET",
        "path": "/api/constituency-performance/{constituency}",
        "keys": {
            "constituency_name",
            "state",
            "mps",
            "total_works",
            "completed_works",
            "sanctioned_works",
            "recommended_works",
            "completion_rate",
            "total_recorded_amount",
            "average_work_amount",
            "completed_amount",
            "stage_distribution",
            "stage_amounts",
            "category_distribution",
            "category_amounts",
            "risk_distribution",
            "critical_count",
            "high_priority_count",
            "review_recommended_count",
            "low_risk_count",
            "high_priority",
            "review_recommended",
            "normal",
            "risk_rate",
            "amount_by_priority",
            "signal_summary",
            "trend",
            "top_flagged_works",
        },
    },
    {
        "n": 18,
        "method": "GET",
        "path": "/api/mp-comparison",
        "params": {"mps": "{mp},{mp2}"},
        "keys": {"comparison", "entity_type"},
    },
    {
        "n": 19,
        "method": "GET",
        "path": "/api/constituency-comparison",
        "params": {"constituencies": "{constituency},{constituency2}"},
        "keys": {"comparison", "entity_type"},
    },
    {"n": 20, "method": "GET", "path": "/api/chat/status", "keys": {"configured", "provider"}},
    {
        "n": 21,
        "method": "POST",
        "path": "/api/chat",
        "json": {"message": "hello", "history": []},
        "keys": {"reply", "source"},
    },
    {"n": 22, "method": "GET", "path": "/api/geojson", "keys": {"type", "features"}},
    {
        "n": 23,
        "method": "GET",
        "path": "/api/geographic-coverage",
        "keys": {
            "total_constituencies",
            "real_boundary_count",
            "real_boundary_pct",
            "centroid_fallback_count",
            "centroid_fallback_pct",
            "total_coverage_pct",
        },
    },
    {
        "n": 24,
        "method": "GET",
        "path": "/api/constituency-intelligence",
        "params": {"state": "{constituency_state}", "constituency": "{constituency}"},
        "keys": {
            "state",
            "constituency",
            "total_works",
            "total_amount",
            "critical_count",
            "high_count",
            "moderate_count",
            "low_count",
            "flagged_count",
            "priority_rate",
            "average_risk",
            "average_confidence",
            "financial_exposure",
            "stage_distribution",
            "category_distribution",
            "signal_summary",
            "confidence_distribution",
            "top_priority_works",
            "mps",
        },
    },
    {
        "n": 25,
        "method": "GET",
        "path": "/api/risk/{work_key}",
        "keys": {"project", "risk_assessment", "context", "evidence_items", "investigation_recommendation"},
    },
    {"n": 26, "method": "GET", "path": "/api/risk/top", "params": {"limit": 5}, "is_array": True},
    {
        "n": 27,
        "method": "GET",
        "path": "/api/risk/summary",
        "keys": {
            "total_records",
            "risk_distribution",
            "average_risk",
            "average_confidence",
            "critical_count",
            "high_count",
            "model_version",
        },
    },
    {"n": 28, "method": "GET", "path": "/api/signals/{work_key}", "keys": {"project_id", "signals"}},
    {
        "n": 29,
        "method": "GET",
        "path": "/api/evidence/{work_key}",
        "keys": {"project_id", "evidence_items", "evidence_chain"},
    },
    {"n": 30, "method": "GET", "path": "/api/context/{work_key}", "keys": {"project_id"}},
    {"n": 31, "method": "GET", "path": "/api/investigations", "is_array": True},
    {
        "n": 32,
        "method": "POST",
        "path": "/api/risk/recalculate/{work_key}",
        "keys": {"record_id", "risk_result", "evidence_items"},
    },
]

assert len({c["n"] for c in CONTRACT}) == 32, "contract table should have exactly 32 endpoints"

# Item-level shapes the contract table documents (docs/frontend_contract.md).
QUEUE_RECORD = {
    "record_id",
    "mp_name",
    "description",
    "category",
    "amount",
    "amount_numeric",
    "constituency",
    "state",
    "date",
    "stage",
    "priority",
    "risk_level",
    "confidence",
    "confidence_score",
    "confidence_percent",
    "risk_score",
    "priority_score",
    "evidence_count",
    "active_signal_count",
    "base_signal_count",
    "evidence_summary",
    "active_signals",
    "model_version",
}
AUDIT_ENTRY = {"record_id", "previous_status", "decision", "reviewer", "note", "timestamp"}
RISK_TOP = {
    "record_id",
    "mp",
    "constituency",
    "state",
    "description",
    "amount",
    "risk_score",
    "risk_level",
    "confidence",
    "active_signal_count",
}
MAP_DATA = {
    "State",
    "Constituency",
    "total",
    "flagged",
    "critical",
    "high",
    "moderate",
    "low",
    "total_amount",
    "avg_risk",
    "mps",
    "stages",
    "flag_rate",
    "avg_risk_pct",
    "financial_exposure",
    "avg_confidence",
    "avg_confidence_pct",
    "avg_signals",
    "latitude",
    "longitude",
    "location_level",
    "geocoding_status",
}
MAP_WORK = {
    "record_id",
    "latitude",
    "longitude",
    "location_level",
    "state",
    "constituency",
    "mp",
    "description",
    "amount",
    "stage",
    "category",
    "risk_level",
    "priority",
    "risk_score",
    "priority_score",
    "confidence_score",
    "confidence_percent",
    "evidence_count",
    "active_signal_count",
    "base_signal_count",
    "evidence_summary",
    "date",
}
PROFILE = CONTRACT[15]["keys"] - {"mp_name", "constituency", "state"}
# n -> (key holding the list, or None for an array response; item keys, or
# None for a list of strings; must be non-empty on real data)
ITEMS = {
    2: ("records", QUEUE_RECORD, True),
    6: ("stages", None, True),
    7: ("constituencies", {"State", "Constituency"}, True),
    8: ("states", None, True),
    9: (None, MAP_DATA, True),
    10: (None, MAP_WORK, True),
    12: ("nodes", {"id", "type", "label", "count"}, True),
    14: (None, AUDIT_ENTRY, True),
    15: ("mps", None, True),
    18: ("comparison", {"name"} | PROFILE, True),
    19: ("comparison", {"name"} | PROFILE, True),
    26: (None, RISK_TOP, True),
    31: (None, QUEUE_RECORD, True),
}


@pytest.fixture(scope="module")
def real(db_session):
    """Real identifiers from the served run -- the highest-risk LS work with
    signals and peers (so every per-record section is populated), its MP and
    constituency, and a second MP/constituency for the comparisons."""
    from app.serving import service

    s = service.served(db_session)
    assert s.run_id is not None, "no serving build: run scripts/run_serving.py (Phase 12) first"
    row = (
        db_session.execute(
            text(
                "SELECT work_key, mp, constituency, constituency_state, state FROM served_work "
                "WHERE run_id = :r AND scored AND house = 'LS' AND peer_group_size > 0 "
                "AND active_signal_count > 0 ORDER BY risk DESC, work_key LIMIT 1"
            ),
            {"r": s.run_id},
        )
        .mappings()
        .one()
    )
    other = (
        db_session.execute(
            text(
                "SELECT mp, constituency FROM served_work WHERE run_id = :r AND house = 'LS' AND mp <> :mp "
                "AND constituency <> :c ORDER BY work_key LIMIT 1"
            ),
            {"r": s.run_id, "mp": row["mp"], "c": row["constituency"]},
        )
        .mappings()
        .one()
    )
    return {**row, "mp2": other["mp"], "constituency2": other["constituency"]}


@pytest.fixture
def no_audit_residue(db_session):
    """POST /api/investigate and /api/risk/recalculate append case_event
    rows; remove the ones a test created so the real audit trail isn't
    polluted by test runs."""
    before = db_session.execute(text("SELECT coalesce(max(id), 0) FROM case_event")).scalar_one()
    db_session.commit()
    yield
    db_session.execute(text("DELETE FROM case_event WHERE id > :b"), {"b": before})
    db_session.commit()


def fill(value, real: dict):
    if isinstance(value, str):
        return value.format(**real)
    if isinstance(value, dict):
        return {k: fill(v, real) for k, v in value.items()}
    return value


# Phase 13: the case endpoints need an authenticated investigator (the actor
# comes from the token); every other contract endpoint is read anonymously
# as the built-in public role (ANONYMOUS_READ, docs/security.md).
AUTHENTICATED = {13, 14, 32}


@pytest.fixture(scope="module")
def investigator(make_user):
    return make_user("investigator")


@pytest.mark.parametrize("entry", CONTRACT, ids=[f"{c['n']:02d}_{c['method']}_{c['path']}" for c in CONTRACT])
def test_contract_endpoint(client, entry, real, no_audit_residue, investigator):
    method = entry["method"]
    path = fill(entry["path"], real)
    params = fill(entry.get("params"), real)
    json_body = entry.get("json")
    headers = investigator if entry["n"] in AUTHENTICATED else None
    if entry["n"] == 14:  # give the audit trail a row so its item shape is checked
        client.post(
            fill("/api/investigate/{work_key}", real),
            json={"decision": "reviewed", "note": "t"},
            headers=investigator,
        )

    if method == "GET":
        resp = client.get(path, params=params, headers=headers)
    elif method == "POST":
        resp = client.post(path, params=params, json=json_body, headers=headers)
    else:
        raise AssertionError(f"unexpected method {method} in contract table")

    assert resp.status_code == 200, f"{method} {path} -> {resp.status_code}: {resp.text}"
    body = resp.json()

    if entry.get("is_array"):
        assert isinstance(body, list), f"{method} {path} expected a JSON array, got {type(body)}"
    else:
        assert isinstance(body, dict), f"{method} {path} expected a JSON object, got {type(body)}"
        missing = entry["keys"] - body.keys()
        assert not missing, f"{method} {path} missing top-level keys: {missing}"

    if entry["n"] in ITEMS:
        key, item_keys, nonempty = ITEMS[entry["n"]]
        items = body[key] if key else body
        assert isinstance(items, list), (path, type(items))
        if nonempty:
            assert items, f"{method} {path}: empty list on real data"
        for item in items[:50]:
            if item_keys is None:
                assert isinstance(item, str) and item, (path, item)
            else:
                missing = item_keys - item.keys()
                assert not missing, f"{method} {path} item missing keys: {missing}"


@pytest.mark.parametrize("n", sorted(AUTHENTICATED))
def test_case_endpoints_refuse_anonymous_callers(client, n, real):
    entry = next(c for c in CONTRACT if c["n"] == n)
    path = fill(entry["path"], real)
    resp = client.post(path, json=entry.get("json")) if entry["method"] == "POST" else client.get(path)
    assert resp.status_code == 401, (path, resp.status_code)


def test_unknown_route_is_404(client):
    resp = client.get("/api/this-route-does-not-exist")
    assert resp.status_code == 404


# --- Phase 3: optional `house=LS|RS` ---------------------------------------
# These prove the parameter is accepted, validated and backward compatible
# (same keys). That it actually filters the rows is tested on real data in
# tests/test_phase12_cutover.py (House-filter correctness).
HOUSE_FILTERED = {1, 2, 4, 9, 10, 11, 12}
_HOUSE_ENTRIES = [c for c in CONTRACT if c["n"] in HOUSE_FILTERED]
_HOUSE_IDS = [f"{c['n']:02d}_{c['path']}" for c in _HOUSE_ENTRIES]


@pytest.mark.parametrize("house", ["LS", "RS"])
@pytest.mark.parametrize("entry", _HOUSE_ENTRIES, ids=_HOUSE_IDS)
def test_house_param_accepted_same_shape(client, entry, house, real):
    params = fill(entry.get("params"), real) or {}
    base = client.get(entry["path"], params=params)
    filtered = client.get(entry["path"], params={**params, "house": house})
    assert filtered.status_code == 200, filtered.text
    body = filtered.json()
    if entry.get("is_array"):
        assert isinstance(body, list)
    else:
        assert not entry["keys"] - body.keys()
        assert body.keys() == base.json().keys()


@pytest.mark.parametrize("entry", _HOUSE_ENTRIES, ids=_HOUSE_IDS)
@pytest.mark.parametrize("bad", ["ls", "LOK", "XX", ""])
def test_house_param_rejects_invalid_value(client, entry, bad):
    resp = client.get(entry["path"], params={**(entry.get("params") or {}), "house": bad})
    assert resp.status_code == 422, (bad, resp.status_code)


def test_house_param_is_optional_and_only_on_the_seven_endpoints(client):
    """The OpenAPI schema is the machine-readable contract: `house` must be
    declared, optional and limited to LS|RS on exactly the seven endpoints,
    and must be absent everywhere else. Omitting it is then byte-identical
    to Phase 2 (test_contract_endpoint above still passes unchanged)."""
    spec = client.get("/openapi.json").json()
    house_paths = {c["path"] for c in _HOUSE_ENTRIES}
    for path, ops in spec["paths"].items():
        for op in ops.values():
            params = {p["name"]: p for p in op.get("parameters", [])}
            if path in house_paths:
                p = params["house"]
                assert p["required"] is False, path
                enum = [s.get("enum") for s in p["schema"].get("anyOf", [p["schema"]]) if s.get("enum")]
                assert enum == [["LS", "RS"]], (path, p["schema"])
            else:
                assert "house" not in params, path


def test_contract_doc_documents_house_on_exactly_the_seven_endpoints():
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / "docs" / "frontend_contract.md").read_text(encoding="utf-8")
    documented = set()
    for line in doc.splitlines():
        if line.startswith("| ") and line.split(" | ")[0][2:].strip().isdigit():
            n = int(line.split(" | ")[0][2:])
            if line.rstrip().endswith("(Phase 3; live since the Phase 12 cutover) |"):
                assert "`house`" in line or ", house`" in line, n
                documented.add(n)
    assert documented == HOUSE_FILTERED

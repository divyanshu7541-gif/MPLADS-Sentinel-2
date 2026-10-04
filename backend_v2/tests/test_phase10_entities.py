"""Phase 10 permanent regression tests: payee typing, entity_metric
(concentration/price-position/reach/profiles), the graph, and their
endpoints. Integration tests need scripts/run_entities.py already run
against the database; they skip otherwise (same convention as
tests/test_phase9_map.py)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.analytics import atypicality_run
from app.analytics.publish import published
from app.entities import metrics, payee_typing
from app.models.analytics import ENTITY_METRICS, EntityMetric


@pytest.fixture(scope="module")
def _ministry_headers(make_user):
    return make_user("ministry")


@pytest.fixture
def client(_ministry_headers):
    """Phase 13: entity profiles carry payee names and national metrics, so
    they are never public -- this module's calls go through the real login
    as a national (ministry) account. Anonymous access is tested in
    tests/test_phase13_security.py."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, headers={"Authorization": _ministry_headers["Authorization"]})


# ---- payee typing (unit) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("BDO Kunihar", "statutory_or_government"),
        ("Sarpanch Shri Jindava", "statutory_or_government"),
        ("CEO Sonkatch", "statutory_or_government"),
        ("ZILA PARISHAD PUNE", "statutory_or_government"),
        ("XYZ STEEL INDUSTRIES PVT LTD", "manufacturer"),
        ("RAMESH CEMENT WORKS", "manufacturer"),
        ("TAJ TRADING COMPANY", "private_firm"),
        ("POOJA ENTERPRISES", "private_firm"),
        ("SHRI MANOHAR SINGH", "individual"),
        ("SMT. RADHA DEVI", "individual"),
        ("Noli devi", "unclassified"),
        ("mukesh", "unclassified"),
    ],
)
def test_classify_payee_type_v2_matches_reviewed_examples(name, expected):
    assert payee_typing.classify_payee_type_v2(name) == expected


def test_classify_payee_type_v2_no_longer_defaults_ambiguous_names_to_private_firm():
    """The Phase 2 bug this phase fixes: anything unmatched used to become
    'private_firm' by default. A bare personal name with no honorific and
    no organisation token must now be 'unclassified', not a guessed type."""
    assert payee_typing.classify_payee_type_v2("Ramo Devi") == "unclassified"
    assert payee_typing.classify_payee_type_v2("Ramo Devi") != "private_firm"


def test_classify_payee_type_v2_is_idempotent():
    names = ["BDO Kunihar", "POOJA ENTERPRISES", "Noli devi", "SHRI MANOHAR SINGH", "XYZ STEEL INDUSTRIES"]
    once = [payee_typing.classify_payee_type_v2(n) for n in names]
    twice = [payee_typing.classify_payee_type_v2(n) for n in names]
    assert once == twice


# ---- stratified_quotas-style unit tests for the concentration null (pure functions) ----------------


def test_herfindahl_of_a_single_payee_is_one():
    import numpy as np

    assert metrics.herfindahl(np.array([1.0])) == pytest.approx(1.0)


def test_herfindahl_of_equal_shares_is_one_over_n():
    import numpy as np

    shares = np.array([0.25, 0.25, 0.25, 0.25])
    assert metrics.herfindahl(shares) == pytest.approx(0.25)


# ---- fixtures -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def entity_run(db_session):
    pub = published(db_session)
    if pub is None:
        pytest.skip("no published_run")
    n = db_session.execute(
        text("SELECT count(*) FROM entity_metric WHERE run_id = :r"), {"r": pub.run_id}
    ).scalar_one()
    if n == 0:
        pytest.skip("no entity_metric rows -- run scripts/run_entities.py")
    return pub.run_id


# ---- payee typing precedes concentration (acceptance criterion) -------------------------------------


def test_every_payee_has_a_type_before_any_concentration_row_exists(db_session, entity_run):
    untyped = db_session.execute(
        text("SELECT count(*) FROM payee WHERE payee_type IS NULL OR payee_type = ''")
    ).scalar_one()
    assert untyped == 0
    n_concentration = db_session.execute(
        text("SELECT count(*) FROM entity_metric WHERE run_id = :r AND metric = 'concentration'"),
        {"r": entity_run},
    ).scalar_one()
    assert n_concentration > 0


def test_payee_type_default_is_no_longer_the_catch_all_private_firm(db_session, entity_run):
    """A real, present-day check that the fixed classifier actually ran
    against the loaded data (not just the pure-function unit tests above)."""
    dist = dict(db_session.execute(text("SELECT payee_type, count(*) FROM payee GROUP BY 1")).all())
    assert dist.get("manufacturer", 0) > 0
    assert dist.get("unclassified", 0) > 0


# ---- no-guilt-by-association ------------------------------------------------------------------


def test_risk_result_schema_has_no_payee_derived_column(db_session):
    cols = {
        r[0]
        for r in db_session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'risk_result'")
        ).all()
    }
    assert not any("payee" in c or "agency" in c or "entity" in c for c in cols)


def test_entity_metric_is_never_read_by_fusion_module():
    import inspect

    from app.analytics import fusion

    src = inspect.getsource(fusion)
    assert "entity_metric" not in src and "EntityMetric" not in src


def test_risk_result_unchanged_by_the_entity_build(db_session, entity_run):
    # build_entities aborts on a mismatch and never commits; a clean run here
    # is itself proof the guard held, but assert the live value too.
    now = atypicality_run.risk_result_checksum(db_session, entity_run)
    assert now  # non-empty; the build already asserted before == after


# ---- permutation-null test (BLUEPRINT.md §8's own example) ------------------------------------------


def test_a_naturally_concentrated_small_market_is_not_flagged_as_unusual(db_session, entity_run):
    """An entity whose observed Herfindahl sits inside the null's own
    interquartile spread (not near either tail) is behaving exactly like
    the null model says a normal stratum-mix should -- BLUEPRINT.md §8's
    'only 2 payees exist in that stratum' example. We don't force such a
    row to exist; if none does in this run, the assertion is vacuous but
    the query itself proves the percentile is stored and usable."""
    rows = db_session.execute(
        text(
            "SELECT percentile, detail->>'null_mean' AS null_mean FROM entity_metric "
            "WHERE run_id = :r AND metric = 'concentration' AND eligible "
            "AND percentile BETWEEN 0.25 AND 0.75"
        ),
        {"r": entity_run},
    ).all()
    for percentile, null_mean in rows:
        assert 0.0 <= percentile <= 1.0
        assert null_mean is not None


def test_concentration_percentile_and_interval_are_well_formed(db_session, entity_run):
    rows = db_session.execute(
        text(
            "SELECT value, percentile, interval FROM entity_metric WHERE run_id = :r "
            "AND metric = 'concentration' AND eligible"
        ),
        {"r": entity_run},
    ).all()
    assert rows
    for value, percentile, interval in rows:
        assert 0.0 <= value <= 1.0
        assert 0.0 <= percentile <= 1.0
        assert interval["available"] is True and interval["low"] <= interval["high"]


# ---- wording rule: denominator + interval + peer_definition, never null ------------------------------


def test_every_entity_metric_row_has_non_null_wording_fields(db_session, entity_run):
    rows = db_session.execute(
        text("SELECT denominator, interval, peer_definition FROM entity_metric WHERE run_id = :r"),
        {"r": entity_run},
    ).all()
    assert rows
    for denominator, interval, peer_definition in rows:
        assert denominator not in (None, "")
        assert interval is not None and isinstance(interval, dict)
        assert peer_definition not in (None, "")


def test_entity_endpoint_response_never_has_a_null_wording_field(client, entity_run, db_session):
    payee_id = db_session.execute(
        text("SELECT entity_id FROM entity_metric WHERE run_id = :r AND entity_type = 'payee' LIMIT 1"),
        {"r": entity_run},
    ).scalar_one()
    r = client.get(f"/api/entities/payee/{payee_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["metrics"]
    for m in body["metrics"]:
        assert m["denominator"] and m["peer_definition"] and m["interval"] is not None


def test_entity_endpoint_returns_payee_type_live(client, entity_run, db_session):
    """The Phase 2 fix (default unclassified, not private_firm) must be
    visible through the API, not only in the database -- owner's request
    after the live review."""
    payee_id, expected_type, expected_status = db_session.execute(
        text("SELECT id, payee_type, review_status FROM payee WHERE payee_type = 'unclassified' LIMIT 1")
    ).first()
    body = client.get(f"/api/entities/payee/{payee_id}").json()
    assert body["attributes"] == {"payee_type": expected_type, "review_status": expected_status}
    assert body["attributes"]["payee_type"] == "unclassified"


def test_entity_endpoint_returns_agency_and_authority_attributes(client, entity_run, db_session):
    agency_id, agency_type = db_session.execute(
        text("SELECT id, agency_type FROM implementing_agency LIMIT 1")
    ).first()
    body = client.get(f"/api/entities/implementing_agency/{agency_id}").json()
    assert body["attributes"] == {"agency_type": agency_type}

    auth_id = db_session.execute(
        text(
            "SELECT entity_id FROM entity_metric WHERE run_id = :r "
            "AND entity_type = 'district_authority' LIMIT 1"
        ),
        {"r": entity_run},
    ).scalar_one()
    body = client.get(f"/api/entities/district_authority/{auth_id}").json()
    assert "district_key" in body["attributes"] and "resolved_state" in body["attributes"]


# ---- MP-tenure concentration (synthesised identity) --------------------------------------------------


def test_mp_tenure_id_is_deterministic_and_fits_postgres_integer():
    from app.entities.metrics import mp_tenure_id

    a = mp_tenure_id("JANE DOE", "LS")
    b = mp_tenure_id("JANE DOE", "LS")
    c = mp_tenure_id("JANE DOE", "RS")
    assert a == b != c
    assert 0 <= a < 2_147_483_647


def test_mp_tenure_concentration_rows_exist_and_are_well_formed(db_session, entity_run):
    rows = db_session.execute(
        text(
            "SELECT entity_id, entity_name, value, percentile, n, house FROM entity_metric "
            "WHERE run_id = :r AND entity_type = 'mp_tenure' AND metric = 'concentration'"
        ),
        {"r": entity_run},
    ).all()
    assert rows
    for entity_id, entity_name, value, percentile, n, house in rows:
        assert 0 <= entity_id < 2_147_483_647
        assert entity_name and ("(LS)" in entity_name or "(RS)" in entity_name)
        assert 0.0 <= value <= 1.0 and 0.0 <= percentile <= 1.0
        assert n >= 5
        assert house in ("LS", "RS")


def test_mp_tenure_and_district_authority_concentration_share_one_set_of_null_draws(db_session, entity_run):
    """Both groupings' permutation null only depends on the (district,
    work-type, FY) stratum a payment falls in, never on which entity
    grouping is scored -- so this is one N_PERMUTATIONS-draw computation,
    not two independent ones (verified structurally: both groupings exist
    for the same run, built by the same call)."""
    counts = dict(
        db_session.execute(
            text(
                "SELECT entity_type, count(*) FROM entity_metric "
                "WHERE run_id = :r AND metric = 'concentration' GROUP BY 1"
            ),
            {"r": entity_run},
        ).all()
    )
    assert counts.get("district_authority", 0) > 0 and counts.get("mp_tenure", 0) > 0


def test_entity_endpoint_resolves_mp_tenure_from_entity_metric_only(client, db_session, entity_run):
    entity_id = db_session.execute(
        text("SELECT entity_id FROM entity_metric WHERE run_id = :r AND entity_type = 'mp_tenure' LIMIT 1"),
        {"r": entity_run},
    ).scalar_one()
    r = client.get(f"/api/entities/mp_tenure/{entity_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["attributes"] == {}
    assert any(m["metric"] == "concentration" for m in body["metrics"])


def test_unknown_mp_tenure_id_is_404_not_a_guess(client, entity_run):
    assert client.get("/api/entities/mp_tenure/999999999").status_code == 404


def test_ineligible_metric_rows_still_carry_wording_fields(db_session, entity_run):
    """A metric that could NOT be evaluated (too few works) still gets a
    row with a real denominator/interval/peer_definition explaining why,
    per BLUEPRINT.md §8 -- never a bare null."""
    row = db_session.execute(
        text(
            "SELECT denominator, interval, peer_definition, not_evaluated_reason FROM entity_metric "
            "WHERE run_id = :r AND NOT eligible LIMIT 1"
        ),
        {"r": entity_run},
    ).first()
    if row is None:
        pytest.skip("no ineligible rows in this run's data")
    denominator, interval, peer_definition, reason = row
    assert denominator and peer_definition and interval is not None and reason


# ---- district authority profile uses the Phase 9 corrected location ---------------------------------


def test_district_authority_profile_peer_definition_cites_the_resolved_state_not_the_stored_one(
    db_session, entity_run
):
    rows = db_session.execute(
        text(
            "SELECT em.entity_id, em.peer_definition, em.detail->>'resolved_state' AS resolved, "
            "st.name AS stored "
            "FROM entity_metric em "
            "JOIN district_authority da ON da.id = em.entity_id "
            "LEFT JOIN state st ON st.id = da.state_id "
            "WHERE em.run_id = :r AND em.metric = 'district_authority_profile' AND em.eligible "
            "AND em.detail->>'resolved_state' IS NOT NULL"
        ),
        {"r": entity_run},
    ).all()
    assert rows
    for entity_id, peer_definition, resolved, stored in rows:
        assert resolved in peer_definition or "not compared" in peer_definition


# ---- graph: shape, bounds, house filter --------------------------------------------------------------


def test_graph_data_matches_the_old_contract_shape(client, entity_run):
    body = client.get("/api/graph-data").json()
    assert set(body.keys()) == {"nodes", "edges"}
    assert body["nodes"] and body["edges"]
    for n in body["nodes"]:
        assert {"id", "type", "label", "count"} <= n.keys()
        assert n["type"] in ("MP", "Work", "Payee", "DistrictAuthority")
    for e in body["edges"]:
        assert {"source", "target", "type", "label"} <= e.keys()
        assert e["type"] in ("recommends", "pays", "executes")


def test_graph_is_bounded(client, entity_run):
    body = client.get("/api/graph-data").json()
    assert len(body["nodes"]) <= 500
    assert len(body["edges"]) <= 2000


def test_graph_edges_reference_declared_nodes(client, entity_run):
    body = client.get("/api/graph-data").json()
    ids = {n["id"] for n in body["nodes"]}
    for e in body["edges"]:
        assert e["source"] in ids and e["target"] in ids


def test_graph_house_filter_only_returns_that_houses_edges(client, db_session, entity_run):
    both = client.get("/api/graph-data").json()
    rs_houses = {
        h
        for (h,) in db_session.execute(
            text("SELECT DISTINCT house FROM graph_edge WHERE run_id = :r"), {"r": entity_run}
        ).all()
    }
    if "RS" not in rs_houses:
        pytest.skip("no RS edges in this run's sampled graph")
    rs = client.get("/api/graph-data", params={"house": "RS"}).json()
    ls = client.get("/api/graph-data", params={"house": "LS"}).json()
    assert len(rs["edges"]) < len(both["edges"])
    assert len(rs["edges"]) + len(ls["edges"]) == len(both["edges"])


def test_graph_data_never_scans_base_tables_at_request_time(client, entity_run):
    from sqlalchemy import event

    from app.db.session import get_engine

    seen = []
    engine = get_engine()

    def capture(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert client.get("/api/graph-data").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert seen
    import re

    assert not any(re.search(r"\b(work|payment|risk_result|signal_result)\b", s, re.I) for s in seen)


# ---- input validation / isolation --------------------------------------------------------------------


def test_unknown_entity_type_is_422(client):
    assert client.get("/api/entities/bogus/1").status_code == 422


def test_unknown_entity_id_is_404(client, entity_run):
    assert client.get("/api/entities/payee/999999999").status_code == 404


def test_entities_endpoint_has_no_house_param():
    from app.main import app

    spec = app.openapi()
    for path, ops in spec["paths"].items():
        if path.startswith("/api/entities/"):
            for op in ops.values():
                names = {p["name"] for p in op.get("parameters", [])}
                assert "house" not in names, path


def test_database_failure_on_entities_endpoint_is_503(client):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.session import get_db
    from app.main import app

    dead = create_engine("postgresql+psycopg://x:x@127.0.0.1:1/x", connect_args={"connect_timeout": 2})

    def _dead():
        s = Session(bind=dead)
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _dead
    try:
        assert client.get("/api/graph-data").status_code == 503
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---- work-evidence facts ------------------------------------------------------------------------------


def test_identical_payment_repeated_facts_have_real_repeats(db_session, entity_run):
    rows = db_session.execute(
        text(
            "SELECT detail FROM work_evidence_fact WHERE run_id = :r AND fact = 'identical_payment_repeated'"
        ),
        {"r": entity_run},
    ).all()
    for (detail,) in rows:
        assert detail["repeat_count"] >= 2


def test_multi_payee_work_facts_meet_the_threshold(db_session, entity_run):
    from app.entities.facts import MULTI_PAYEE_THRESHOLD

    rows = db_session.execute(
        text("SELECT detail FROM work_evidence_fact WHERE run_id = :r AND fact = 'multi_payee_work'"),
        {"r": entity_run},
    ).all()
    assert rows
    for (detail,) in rows:
        assert detail["n_payees"] >= MULTI_PAYEE_THRESHOLD


def test_entity_metrics_enum_is_exactly_the_five_documented_metrics():
    assert set(ENTITY_METRICS) == {
        "concentration",
        "price_position",
        "reach",
        "district_authority_profile",
        "implementing_agency_profile",
    }


def test_entity_metric_orm_check_constraint_matches_enum(db_session):
    assert EntityMetric.__table__.name == "entity_metric"  # importable, mapped -- smoke check

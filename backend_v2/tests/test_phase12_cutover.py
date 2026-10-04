"""
Phase 12 cutover: the known-defect regression suite (SENTINEL_REBUILD_PLAN_v2.md,
Phase 12 "Known defects to confirm fixed"), run against the REAL served run
through the same HTTP handlers the frontend calls.

Each test names the old defect it guards. Earlier phases test the same
properties at their own layer (peers, signals, fusion, map); these check
that they survive all the way to the API response.

Needs the full pipeline, ending with scripts/run_serving.py (CI runs it).
Skips if no database is reachable; FAILS if one is reachable but has no
serving build, because then the gate isn't actually being tested.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.analytics import fusion
from app.analytics.atypicality_run import risk_result_checksum
from app.db.session import get_db
from app.main import app
from app.serving import labels, service

REPO = Path(__file__).resolve().parents[2]
TIER_ORDER = ("LOW", "MODERATE", "HIGH", "CRITICAL")


@pytest.fixture(scope="module")
def served(db_session):
    s = service.served(db_session)
    assert s.run_id is not None, "no serving build: run scripts/run_serving.py (Phase 12) first"
    return s


@pytest.fixture
def no_audit_residue(db_session):
    before = db_session.execute(text("SELECT coalesce(max(id), 0) FROM case_event")).scalar_one()
    db_session.commit()
    yield
    db_session.execute(text("DELETE FROM case_event WHERE id > :b"), {"b": before})
    db_session.commit()


def _sample(db_session, served, where: str = "scored", n: int = 40) -> list[str]:
    return list(
        db_session.execute(
            text(
                f"SELECT work_key FROM served_work WHERE run_id = :r AND {where} "
                "ORDER BY md5(work_key) LIMIT :n"
            ),
            {"r": served.run_id, "n": n},
        ).scalars()
    )


# ---- self-in-peer-group -----------------------------------------------------------------------


def test_self_never_in_own_peer_group(client, db_session, served):
    """Old engine: peer size/median included the work itself. The serving
    build already asserts #others == n_usable_excl_self for every work; here
    the API's context must carry that leave-one-out figure, and a work's
    percentile must be computed against others only (a group's cheapest
    work is 0, its dearest 100 -- impossible if it were in its own set)."""
    rows = db_session.execute(
        text(
            "SELECT sw.work_key, sw.peer_group_size, wc.n_usable_excl_self FROM served_work sw "
            "JOIN work_context wc ON wc.run_id = sw.run_id AND wc.work_key = sw.work_key "
            "WHERE sw.run_id = :r AND sw.peer_level IS NOT NULL ORDER BY md5(sw.work_key) LIMIT 25"
        ),
        {"r": served.run_id},
    ).all()
    assert rows
    for wk, size, loo in rows:
        ctx = client.get(f"/api/context/{wk}").json()
        assert ctx["peer_group_size"] == size == loo, wk
        assert ctx["leave_one_out"] is True
        assert ctx["peer_percentile"] is None or 0 <= ctx["peer_percentile"] <= 100
    ext = db_session.execute(
        text("SELECT min(peer_percentile), max(peer_percentile) FROM served_work WHERE run_id = :r"),
        {"r": served.run_id},
    ).one()
    assert ext == (0, 100)


# ---- positional misalignment ------------------------------------------------------------------


def test_api_figures_align_with_stored_results_by_key(client, db_session, served):
    """Old engine: results were attached by row position, so a reorder
    silently gave a work another work's score. Every figure the API shows
    for a work must be that work's own stored risk_result/signal_result row."""
    for wk in _sample(db_session, served):
        rr = db_session.execute(
            text(
                "SELECT risk, tier, confidence, base_signal_count FROM risk_result "
                "WHERE run_id = :r AND config_name = :c AND work_key = :wk"
            ),
            {"r": served.run_id, "c": served.build.config_name, "wk": wk},
        ).one()
        sig = dict(
            db_session.execute(
                text(
                    "SELECT signal, score FROM signal_result "
                    "WHERE run_id = :r AND work_key = :wk AND eligible"
                ),
                {"r": served.run_id, "wk": wk},
            ).all()
        )
        d = client.get(f"/api/record/{wk}").json()
        ra = d["risk_assessment"]
        assert d["record_id"] == ra["project_id"] == d["source_record"]["record_id"] == wk
        assert ra["risk_score"] == pytest.approx(round(rr.risk, 1))
        assert ra["risk_level"] == rr.tier
        assert ra["confidence"] == pytest.approx(round(rr.confidence, 4))
        assert ra["active_signal_count"] == rr.base_signal_count
        for item in d["evidence_items"]:
            assert item["signal_score"] == pytest.approx(round(sig[item["signal"]], 4)), (wk, item["signal"])
        active = {s for s, v in sig.items() if v is not None and v > fusion.ACTIVE_THRESHOLD}
        assert {i["signal"] for i in d["evidence_items"]} == active, wk


def test_queue_rows_align_with_stored_results(client, db_session, served):
    rows = client.get("/api/queue", params={"page": 3, "page_size": 50, "sort_by": "amount_numeric"}).json()
    assert len(rows["records"]) == 50
    keys = [r["record_id"] for r in rows["records"]]
    stored = dict(
        db_session.execute(
            text(
                "SELECT work_key, risk FROM risk_result WHERE run_id = :r AND config_name = :c "
                "AND work_key = ANY(:k)"
            ),
            {"r": served.run_id, "c": served.build.config_name, "k": keys},
        ).all()
    )
    for r in rows["records"]:
        assert r["risk_score"] == pytest.approx(stored[r["record_id"]] / 100, abs=1e-6)


# ---- stage-multiplied rows --------------------------------------------------------------------


def test_one_row_per_work_never_one_per_stage(client, db_session, served):
    """Old engine: a work present in the recommended, sanctioned and
    completed files was counted once per file."""
    n_works, n_distinct = db_session.execute(
        text("SELECT count(*), count(DISTINCT work_key) FROM served_work WHERE run_id = :r"),
        {"r": served.run_id},
    ).one()
    assert n_works == n_distinct
    snap = db_session.execute(
        text(
            "SELECT count(*) FROM work_state ws "
            "JOIN analysis_run ar ON ar.source_snapshot_id = ws.source_snapshot_id WHERE ar.id = :r"
        ),
        {"r": served.run_id},
    ).scalar_one()
    assert n_works == snap
    scored = db_session.execute(
        text("SELECT count(*) FROM risk_result WHERE run_id = :r AND config_name = :c"),
        {"r": served.run_id, "c": served.build.config_name},
    ).scalar_one()
    summary = client.get("/api/summary").json()
    assert summary["total_records"] == scored
    assert sum(summary["stage_distribution"].values()) == scored
    assert client.get("/api/queue", params={"page_size": 1}).json()["total"] == scored
    health = client.get("/api/data-health").json()
    assert health["total_records"] == sum(health["stage_distribution"].values()) == n_works


def test_queue_pages_partition_the_result(client):
    a = client.get("/api/queue", params={"page": 1, "page_size": 100}).json()
    b = client.get("/api/queue", params={"page": 2, "page_size": 100}).json()
    ka, kb = [r["record_id"] for r in a["records"]], [r["record_id"] for r in b["records"]]
    assert len(set(ka)) == len(ka) == 100 and not set(ka) & set(kb)
    assert a["total_pages"] == -(-a["total"] // 100)
    risks = [r["risk_score"] for r in a["records"] + b["records"]]
    assert risks == sorted(risks, reverse=True)


# ---- special-character search -----------------------------------------------------------------


@pytest.mark.parametrize(
    "q", ["(", ")", "[", "*", "+", "?", "\\", "'", '"', "%", "_", "a|b", "^$", "ऋ", "(Test)"]
)
def test_special_character_search_is_literal_and_never_errors(client, q):
    """Old engine: queue/map search used regex (`str.contains`), so "(" crashed."""
    r = client.get("/api/queue", params={"search": q, "page_size": 20})
    assert r.status_code == 200, (q, r.text)
    assert client.get("/api/map-data", params={"search": q}).status_code == 200
    assert client.get("/api/map-works", params={"search": q, "limit": 5}).status_code == 200


def test_percent_search_matches_a_literal_percent_only(client):
    r = client.get("/api/queue", params={"search": "%", "page_size": 200}).json()
    assert 0 < r["total"] < 1000
    assert all("%" in rec["description"] for rec in r["records"])


# ---- map coordinates and payload --------------------------------------------------------------


def test_map_markers_are_real_constituency_locations_not_stacked_or_fake(client):
    """Old engine: works with no location were stacked on one fake point.
    Phase 9 locates at constituency level and says so; no (0, 0), all in
    India's bounding box, and unlocated works get no marker at all."""
    r = client.get("/api/map-works", params={"limit": 3000})
    assert r.headers["X-Location-Precision"] == "approximate_constituency_level"
    rows = r.json()
    assert rows
    for w in rows:
        assert 6 <= w["latitude"] <= 37.5 and 68 <= w["longitude"] <= 98, w["record_id"]
    points = {(w["latitude"], w["longitude"]) for w in rows}
    assert len(points) > 100


def test_map_payloads_are_bounded(client):
    """Old engine: /api/map-works returned every work (tens of MB)."""
    r = client.get("/api/map-works", params={"limit": 100000})
    assert r.status_code in (200, 422)
    r = client.get("/api/map-works")
    assert len(r.json()) <= int(r.headers["X-Map-Works-Cap"]) == 3000
    assert len(r.content) < 3_000_000
    assert len(client.get("/api/map-data").content) < 2_000_000


# ---- one failed endpoint ----------------------------------------------------------------------


def test_database_failure_is_a_clean_503_per_endpoint_not_a_crash():
    """Backend half of "one failed endpoint blanks the dashboard": a
    failure is a fast, well-formed 503 on that endpoint only (the frontend
    half is the browser partial-failure test in docs/phase12_report.md)."""
    dead = sessionmaker(bind=create_engine("postgresql+psycopg://x:x@127.0.0.1:1/x", pool_pre_ping=False))

    def broken():
        s = dead()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = broken
    service._CACHE.clear()
    try:
        c = TestClient(app)
        for path in (
            "/api/summary",
            "/api/queue",
            "/api/analytics",
            "/api/data-health",
            "/api/record/1",
            "/api/mp-performance/x",
            "/api/risk/top",
            "/api/states",
        ):
            r = c.get(path)
            assert r.status_code == 503, (path, r.status_code)
            assert "temporarily unavailable" in r.json()["detail"]
        assert c.get("/api/health").status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
        service._CACHE.clear()


# ---- routes: deleted chatbot, missing APIs, /api/load, route order -----------------------------


def _api_js_calls() -> list[tuple[str, str]]:
    src = (REPO / "frontend" / "src" / "services" / "api.js").read_text(encoding="utf-8")
    calls = []
    for m in re.finditer(r"fetch\(`\$\{API_BASE\}([^`]*)`(.{0,80})", src, flags=re.S):
        path = re.sub(r"\$\{encodeURIComponent\([^)]*\)\}", "SAMPLE", m.group(1))
        path = re.split(r"\?|\$\{", path)[0]
        method = "POST" if "method: 'POST'" in m.group(2) or 'method: "POST"' in m.group(2) else "GET"
        calls.append((method, "/api" + path))
    return calls


def _match(method: str, path: str):
    for route in app.routes:
        if getattr(route, "path_regex", None) and route.path_regex.match(path) and method in route.methods:
            return route
    return None


def test_every_frontend_api_call_has_a_route():
    """Old system: the frontend called endpoints the backend didn't have,
    and the chatbot route had been deleted."""
    calls = _api_js_calls()
    assert len(calls) == 32, calls
    missing = [(m, p) for m, p in calls if _match(m, p) is None]
    assert not missing, missing


def test_literal_risk_routes_are_not_swallowed_by_the_id_route(client):
    """Old router registered /api/risk/{project_id} first, so /api/risk/top
    and /api/risk/summary were answered as project "top"/"summary" (404)."""
    assert _match("GET", "/api/risk/top").name == "get_risk_top"
    assert _match("GET", "/api/risk/summary").name == "get_risk_summary"
    assert isinstance(client.get("/api/risk/top").json(), list)
    assert "risk_distribution" in client.get("/api/risk/summary").json()


def test_chatbot_routes_exist(client):
    assert client.get("/api/chat/status").status_code == 200
    r = client.post("/api/chat", json={"message": "how is risk calculated", "history": []})
    assert r.status_code == 200 and r.json()["reply"]


def test_api_load_is_removed(client):
    assert client.post("/api/load").status_code in (404, 405)
    assert client.get("/api/load").status_code in (404, 405)
    assert _match("POST", "/api/load") is None


# ---- graph -------------------------------------------------------------------------------------


def test_graph_endpoint_is_consistent(client):
    g = client.get("/api/graph-data").json()
    ids = {n["id"] for n in g["nodes"]}
    assert g["nodes"] and g["edges"]
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])


# ---- impossible risk tiers ---------------------------------------------------------------------


def test_no_impossible_risk_tiers(db_session, served, client):
    """Old engine: tiers that contradicted the score (e.g. CRITICAL on one
    signal). Every served tier must be the one its score and corroboration
    allow, and unscored works carry no tier at all."""
    bad = db_session.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE tier = 'CRITICAL' AND (risk < :c OR active_signal_count < :k)),
                   count(*) FILTER (WHERE tier = 'HIGH'
                                    AND (risk < :h OR (risk >= :c AND active_signal_count >= :k))),
                   count(*) FILTER (WHERE tier = 'MODERATE' AND (risk < :m OR risk >= :h)),
                   count(*) FILTER (WHERE tier = 'LOW' AND risk >= :m),
                   count(*) FILTER (WHERE scored AND tier NOT IN ('CRITICAL', 'HIGH', 'MODERATE', 'LOW')),
                   count(*) FILTER (WHERE NOT scored AND (tier IS NOT NULL OR risk IS NOT NULL)),
                   count(*) FILTER (WHERE risk < 0 OR risk > 100 OR confidence < 0 OR confidence > 1)
            FROM (SELECT tier, scored, confidence, active_signal_count, round(risk::numeric, 9) AS risk
                  FROM served_work WHERE run_id = :r) sw
            """
        ),
        {"r": served.run_id, "c": 85.0, "h": 65.0, "m": 40.0, "k": fusion.MIN_CRITICAL_SIGNALS},
    ).one()
    assert tuple(bad) == (0,) * 7, bad
    assert dict(fusion.TIER_THRESHOLDS) == {"CRITICAL": 85.0, "HIGH": 65.0, "MODERATE": 40.0}
    s = client.get("/api/summary").json()
    assert s["critical_count"] + s["high_count"] + s["moderate_count"] + s["low_count"] == s["total_records"]
    for r in client.get("/api/queue", params={"page_size": 200}).json()["records"]:
        assert r["risk_level"] in TIER_ORDER and r["confidence"] == labels.confidence_label(
            r["confidence_score"]
        )


def test_unscored_work_is_not_evaluated_never_zero(client, db_session, served):
    wk = _sample(db_session, served, "NOT scored", 1)[0]
    d = client.get(f"/api/record/{wk}").json()
    ra = d["risk_assessment"]
    assert ra["risk_level"] == "NOT_EVALUATED" and ra["risk_score"] is None and ra["confidence"] is None
    assert ra["scored"] is False and ra["not_scored_reason"]
    assert d["evidence_items"] == [] and len(d["not_evaluated_signals"]) == len(fusion.BASE_SIGNALS)
    assert client.get("/api/queue", params={"stage": "RECOMMENDED"}).json()["total"] == 0


# ---- House filter -----------------------------------------------------------------------------


def test_house_filter_partitions_and_never_changes_a_score(client, db_session, served):
    full = client.get("/api/summary").json()
    ls = client.get("/api/summary", params={"house": "LS"}).json()
    rs = client.get("/api/summary", params={"house": "RS"}).json()
    assert ls["total_records"] + rs["total_records"] == full["total_records"]
    assert ls["total_records"] > 0 and rs["total_records"] > 0
    for k in ("critical_count", "high_count", "moderate_count", "low_count"):
        assert ls[k] + rs[k] == full[k], k
    a_ls = client.get("/api/analytics", params={"house": "LS"}).json()
    assert sum(a_ls["risk_distribution"].values()) == ls["total_records"]
    for house in ("LS", "RS"):
        q = client.get("/api/queue", params={"house": house, "page_size": 200}).json()
        assert q["total"] == (ls if house == "LS" else rs)["total_records"]
        assert {r["house"] for r in q["records"]} == {house}
    # display filter only: the same work has the same figures either way
    rec = client.get("/api/queue", params={"house": "RS", "page_size": 1}).json()["records"][0]
    unfiltered = client.get("/api/queue", params={"search": rec["record_id"], "page_size": 200}).json()
    same = [r for r in unfiltered["records"] if r["record_id"] == rec["record_id"]][0]
    assert same["risk_score"] == rec["risk_score"] and same["confidence_score"] == rec["confidence_score"]


# ---- queue filters the old router ignored -----------------------------------------------------


def test_queue_honours_the_param_names_the_frontend_sends(client):
    """InvestigationQueue.jsx sends `mp` and `risk_level`; the old router
    read only `mp_name` and `priority`, so both filters were silently ignored."""
    top = client.get("/api/queue", params={"page_size": 1}).json()["records"][0]
    by_mp = client.get("/api/queue", params={"mp": top["mp_name"], "page_size": 200}).json()
    assert by_mp["total"] > 0 and {r["mp_name"] for r in by_mp["records"]} == {top["mp_name"]}
    assert by_mp["total"] == client.get("/api/queue", params={"mp_name": top["mp_name"]}).json()["total"]
    crit = client.get("/api/queue", params={"risk_level": "CRITICAL", "page_size": 200}).json()
    assert crit["total"] == client.get("/api/summary").json()["critical_count"]
    assert {r["risk_level"] for r in crit["records"]} == {"CRITICAL"}


# ---- write actions ----------------------------------------------------------------------------


def test_investigate_never_takes_the_actor_from_the_body(
    client, db_session, served, no_audit_residue, make_user
):
    """Old system: POST /api/investigate stored whatever `reviewer` the
    client sent. Since Phase 13 the actor is the token's account (Phase 12
    used a labelled placeholder until auth existed) -- never the body's value."""
    h = make_user("investigator")
    wk = _sample(db_session, served, n=1)[0]
    r = client.post(
        f"/api/investigate/{wk}", json={"decision": "Escalate", "reviewer": "Forged Name"}, headers=h
    ).json()
    assert r["status"] == "saved"
    assert r["entry"]["reviewer"].startswith(h["X-Test-User"]) and r["entry"]["actor_is_placeholder"] is False
    trail = client.get("/api/audit-trail", headers=h).json()
    assert all(e["reviewer"] != "Forged Name" for e in trail)
    second = client.post(f"/api/investigate/{wk}", json={"decision": "Dismiss"}, headers=h).json()
    assert second["entry"]["previous_status"] == "Escalate"
    assert client.post("/api/investigate/NO-SUCH-WORK", json={}, headers=h).status_code == 404
    assert client.post(f"/api/investigate/{wk}", json={"decision": "x"}).status_code == 401


def test_recalculate_flags_for_next_run_and_never_writes_risk(
    client, db_session, served, no_audit_residue, make_user
):
    h = make_user("investigator")
    wk = _sample(db_session, served, n=1)[0]
    before = risk_result_checksum(db_session, served.run_id)
    r = client.post(f"/api/risk/recalculate/{wk}", headers=h).json()
    assert r["risk_result"]["status"] == "flagged_for_next_run" and r["risk_result"]["recomputed"] is False
    assert (
        r["risk_result"]["risk_score"]
        == client.get(f"/api/risk/{wk}").json()["risk_assessment"]["risk_score"]
    )
    db_session.commit()
    assert risk_result_checksum(db_session, served.run_id) == before
    n = db_session.execute(
        text("SELECT count(*) FROM case_event WHERE work_key = :wk AND event_type = 'recalculate_requested'"),
        {"wk": wk},
    ).scalar_one()
    assert n >= 1


# ---- MP performance ---------------------------------------------------------------------------


def test_mp_performance_is_current_tenure_only(client, db_session, served):
    """Profiles cover the served snapshot's works only; prior-cycle works
    (prior_cycle_work) are never merged in."""
    mp = client.get("/api/queue", params={"page_size": 1, "house": "LS"}).json()["records"][0]["mp_name"]
    prof = client.get(f"/api/mp-performance/{mp}").json()
    n = db_session.execute(
        text("SELECT count(*) FROM served_work WHERE run_id = :r AND mp = :mp"),
        {"r": served.run_id, "mp": mp},
    ).scalar_one()
    assert prof["total_works"] == n == sum(prof["stage_distribution"].values())
    assert prof["scored_works"] == sum(prof["risk_distribution"].values())
    assert set(prof["stage_distribution"]) <= set(labels.STAGES.values())
    assert client.get("/api/mp-performance/NO SUCH MP").status_code == 404


# ---- the gate's invariant ---------------------------------------------------------------------


def test_serving_never_changed_risk_result(db_session, served):
    counts = served.build.counts
    assert counts["risk_result_md5_before"] == counts["risk_result_md5_after"]
    assert risk_result_checksum(db_session, served.run_id) == counts["risk_result_md5_after"]

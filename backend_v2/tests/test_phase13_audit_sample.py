"""
Phase 13 randomised audit sample (BLUEPRINT.md §12): the mechanism exists
and runs end to end on the real published run -- draw (tier-stratified,
seeded), blind review list, reviews recorded with the token's reviewer,
report with Wilson intervals and inter-rater agreement. Samples drawn here
are deleted afterwards.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import text

from app.audit import sampling

BACKEND = Path(__file__).resolve().parents[1]
RISK_FIELDS = ("tier", "risk", "score", "confidence", "signal", "priority", "peer", "stratum", "deviation")


@pytest.fixture
def cleanup_samples(db_session):
    before = db_session.execute(text("SELECT coalesce(max(id), 0) FROM audit_sample")).scalar_one()
    db_session.commit()
    yield
    db_session.rollback()
    db_session.execute(text("DELETE FROM audit_sample WHERE id > :b"), {"b": before})  # cascades
    db_session.commit()


def test_wilson_and_kappa_math():
    lo, hi = sampling.wilson(8, 10)
    assert (lo, hi) == pytest.approx((0.4902, 0.9433), abs=1e-4)
    assert sampling.wilson(0, 0) is None
    lo0, hi0 = sampling.wilson(0, 20)
    assert lo0 == 0 and 0.15 < hi0 < 0.17
    pairs = (
        [("follow_up_needed", "follow_up_needed")] * 4
        + [("no_follow_up", "no_follow_up")] * 4
        + [("follow_up_needed", "no_follow_up")] * 2
    )
    # rater A: 6 follow-up / 4 none; rater B: 4 / 6. po = 0.8, pe = (6*4 + 4*6)/100 = 0.48,
    # kappa = (0.8 - 0.48) / (1 - 0.48) = 0.6154
    assert sampling.cohen_kappa(pairs) == pytest.approx(0.6154, abs=1e-4)
    assert sampling.cohen_kappa([]) is None


def test_draw_is_tier_stratified_seeded_and_blind(client, make_user, db_session, cleanup_samples):
    sup = make_user("supervisor")
    r = client.post("/api/audit/samples", json={"seed": 20260928}, headers=sup)
    assert r.status_code == 200, r.text
    d = r.json()
    design = d["design"]
    pop = dict(
        db_session.execute(
            text("SELECT tier, count(*) FROM risk_result WHERE run_id = :r AND config_name = :c GROUP BY 1"),
            {"r": d["run_id"], "c": d["config"]},
        ).all()
    )
    for tier, quota in sampling.QUOTAS.items():
        assert design["drawn"][tier] == min(quota, pop.get(tier, 0)), tier
        assert design["population"][tier] == pop.get(tier, 0)
    assert design["total_drawn"] == sum(design["drawn"].values()) == 300
    items = db_session.execute(
        text(
            "SELECT work_key, stratum, position FROM audit_sample_item WHERE sample_id = :s ORDER BY position"
        ),
        {"s": d["sample_id"]},
    ).all()
    assert Counter(i.stratum for i in items) == Counter(design["drawn"])
    # every drawn work really has that tier in the published run
    wrong = db_session.execute(
        text(
            "SELECT count(*) FROM audit_sample_item i JOIN risk_result r ON r.work_key = i.work_key "
            "AND r.run_id = :r AND r.config_name = :c WHERE i.sample_id = :s AND r.tier <> i.stratum"
        ),
        {"r": d["run_id"], "c": d["config"], "s": d["sample_id"]},
    ).scalar_one()
    assert wrong == 0
    # review order mixes strata (no tier blocks)
    assert len({i.stratum for i in items[:20]}) >= 2
    # same seed -> same works
    again = client.post("/api/audit/samples", json={"seed": 20260928}, headers=sup).json()
    keys2 = (
        db_session.execute(
            text("SELECT work_key FROM audit_sample_item WHERE sample_id = :s"), {"s": again["sample_id"]}
        )
        .scalars()
        .all()
    )
    assert set(keys2) == {i.work_key for i in items}

    # the reviewer's list is blind: source facts only
    aud = make_user("auditor")
    lst = client.get(f"/api/audit/samples/{d['sample_id']}/items", params={"page_size": 200}, headers=aud)
    assert lst.status_code == 200
    body = lst.json()
    assert body["total"] == 300 and len(body["items"]) == 200
    for item in body["items"]:
        assert set(item) == set(sampling.BLIND_FIELDS) | {"your_outcome"}
        assert not [k for k in item if any(f in k for f in RISK_FIELDS)]
    # and the auditor cannot look the tier up anywhere else
    wk = body["items"][0]["work_key"]
    for path in (f"/api/record/{wk}", f"/api/risk/{wk}", "/api/queue"):
        assert client.get(path, headers=aud).status_code == 403
    # role gates
    assert client.post("/api/audit/samples", json={}, headers=aud).status_code == 403
    assert client.get(f"/api/audit/samples/{d['sample_id']}/items", headers=sup).status_code == 403
    assert client.get(f"/api/audit/samples/{d['sample_id']}/report").status_code == 401


def test_reviews_and_report(client, make_user, db_session, cleanup_samples):
    sup = make_user("supervisor")
    sid = client.post("/api/audit/samples", json={"seed": 7}, headers=sup).json()["sample_id"]
    a1, a2 = make_user("auditor"), make_user("auditor")
    items = client.get(f"/api/audit/samples/{sid}/items", params={"page_size": 200}, headers=a1).json()[
        "items"
    ]
    codes = {
        row.blind_code: row.stratum
        for row in db_session.execute(
            text("SELECT blind_code, stratum FROM audit_sample_item WHERE sample_id = :s"), {"s": sid}
        )
    }
    crit = [i["blind_code"] for i in items if codes[i["blind_code"]] == "CRITICAL"][:10]
    high = [i["blind_code"] for i in items if codes[i["blind_code"]] == "HIGH"][:10]
    # CRITICAL: 7 follow-up, 3 none (a1); HIGH: 4 follow-up, 5 none, 1 data issue (a1)
    plan = {c: ("follow_up_needed" if n < 7 else "no_follow_up") for n, c in enumerate(crit)}
    plan |= {
        c: ("follow_up_needed" if n < 4 else "no_follow_up" if n < 9 else "data_issue")
        for n, c in enumerate(high)
    }
    for code, outcome in plan.items():
        r = client.post(f"/api/audit/items/{code}/reviews", json={"outcome": outcome}, headers=a1)
        assert r.status_code == 200 and r.json()["reviewer"].startswith(a1["X-Test-User"])
    # second rater agrees on 8 of the 10 CRITICAL items
    for n, code in enumerate(crit):
        outcome = (
            plan[code]
            if n < 8
            else ("no_follow_up" if plan[code] == "follow_up_needed" else "follow_up_needed")
        )
        assert (
            client.post(f"/api/audit/items/{code}/reviews", json={"outcome": outcome}, headers=a2).status_code
            == 200
        )
    # one review per reviewer per item; the reviewer can't be supplied
    assert (
        client.post(
            f"/api/audit/items/{crit[0]}/reviews", json={"outcome": "no_follow_up"}, headers=a1
        ).status_code
        == 409
    )
    bad = client.post(
        f"/api/audit/items/{crit[1]}/reviews", json={"outcome": "no_follow_up", "reviewer": "x"}, headers=a1
    )
    assert bad.status_code == 422
    assert (
        client.post("/api/audit/items/nope/reviews", json={"outcome": "no_follow_up"}, headers=a1).status_code
        == 404
    )

    rep = client.get(f"/api/audit/samples/{sid}/report", headers=sup).json()
    c, h = rep["strata"]["CRITICAL"], rep["strata"]["HIGH"]
    assert (c["follow_up_needed"], c["no_follow_up"], c["reviewed"]) == (7, 3, 10)
    assert c["precision"] == 0.7 and c["precision_wilson95"] == list(sampling.wilson(7, 10))
    assert (h["follow_up_needed"], h["no_follow_up"], h["data_issue"]) == (4, 5, 1)
    assert h["precision"] == pytest.approx(4 / 9, abs=1e-4)  # data issues excluded
    pc, ph = c["population"], h["population"]
    assert rep["flagged_precision_population_weighted"] == pytest.approx(
        (pc * 0.7 + ph * 4 / 9) / (pc + ph), abs=1e-4
    )
    assert rep["inter_rater"]["items_double_reviewed"] == 10
    assert rep["inter_rater"]["cohen_kappa"] == sampling.cohen_kappa(
        [(plan[code], plan[code]) for code in crit[:8]]
        + [
            (plan[code], "no_follow_up" if plan[code] == "follow_up_needed" else "follow_up_needed")
            for code in crit[8:]
        ]
    )


def test_cli_draws_and_reports(db_session, cleanup_samples):
    out = subprocess.run(
        [sys.executable, "scripts/draw_audit_sample.py", "--seed", "11"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    sid = db_session.execute(text("SELECT max(id) FROM audit_sample")).scalar_one()
    rep = subprocess.run(
        [sys.executable, "scripts/draw_audit_sample.py", "--report", str(sid)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    assert rep.returncode == 0 and '"total_drawn": 300' in rep.stdout

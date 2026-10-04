"""
Audit samples drawn on a superseded run are archived, never deleted (Phase 13.y, step 4).

After the authority-state fix the published run changed, so a sample
stratified on the old run's tiers (#13 on the local reference database) is
archived -- kept with its items, closed to reviews -- and a replacement is
drawn on the published run with the same seed
(`scripts/draw_audit_sample.py --seed S --replace N`). The tests below hold on
any database: the invariants on the stored samples, and a draw/archive cycle
the test makes and removes itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.audit import sampling


@pytest.fixture(scope="module")
def published_run(db_session):
    return db_session.execute(text("SELECT run_id FROM published_run")).scalar_one()


@pytest.fixture
def own_sample(db_session):
    """A small sample drawn on the published run, removed afterwards."""
    before = db_session.execute(text("SELECT coalesce(max(id), 0) FROM audit_sample")).scalar_one()
    db_session.commit()
    s = sampling.draw(db_session, created_by="t13y", seed=7, quotas={"LOW": 3})
    db_session.commit()
    yield s
    db_session.rollback()
    db_session.execute(
        text(
            "DELETE FROM audit_review "
            "WHERE item_id IN (SELECT id FROM audit_sample_item WHERE sample_id > :b)"
        ),
        {"b": before},
    )
    db_session.execute(text("DELETE FROM audit_sample WHERE id > :b"), {"b": before})
    db_session.commit()


def test_every_active_sample_is_on_the_published_run_and_archives_keep_their_record(
    db_session, published_run
):
    rows = db_session.execute(
        text(
            "SELECT s.id, s.run_id, s.archived_at, s.archive_reason, s.design, "
            "(SELECT count(*) FROM audit_sample_item i WHERE i.sample_id = s.id) AS items FROM audit_sample s"
        )
    ).all()
    for r in rows:
        if r.archived_at is None:
            assert r.run_id == published_run, f"sample {r.id} is active but drawn on run {r.run_id}"
        else:
            assert r.archive_reason, f"archived sample {r.id} has no reason"
            assert r.items == r.design["total_drawn"], f"archived sample {r.id} lost items"
    # a replacement names the sample it replaces, and that one is archived
    archived = {r.id for r in rows if r.archived_at is not None}
    for r in rows:
        if "replaces_sample" in r.design:
            assert r.design["replaces_sample"] in archived


def test_an_archived_sample_refuses_reviews_and_is_listed_as_archived(
    client, make_user, db_session, own_sample
):
    auditor = make_user("auditor")
    code = db_session.execute(
        text("SELECT blind_code FROM audit_sample_item WHERE sample_id = :s ORDER BY position LIMIT 1"),
        {"s": own_sample.id},
    ).scalar_one()
    sampling.archive(db_session, own_sample.id, "superseded (test)")
    db_session.commit()
    r = client.post(f"/api/audit/items/{code}/reviews", json={"outcome": "no_follow_up"}, headers=auditor)
    assert r.status_code == 409 and "archived" in r.json()["detail"]
    listing = client.get("/api/audit/samples", headers=make_user("supervisor")).json()
    mine = next(s for s in listing if s["sample_id"] == own_sample.id)
    assert mine["archived_at"] and mine["archive_reason"] == "superseded (test)"


def test_archive_keeps_everything_and_is_idempotent(db_session, own_sample):
    first = sampling.archive(db_session, own_sample.id, "test").archived_at
    again = sampling.archive(db_session, own_sample.id, "another reason")
    assert again.archived_at == first and again.archive_reason == "test"
    n = db_session.execute(
        text("SELECT count(*) FROM audit_sample_item WHERE sample_id = :s"), {"s": own_sample.id}
    ).scalar()
    assert n == 3
    with pytest.raises(LookupError):
        sampling.archive(db_session, 10**9, "no such sample")

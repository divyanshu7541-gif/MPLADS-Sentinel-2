"""
Display-time masking of beneficiary details for ANONYMOUS callers (owner
decisions, 2026-10-02; app/serving/public_mask.py).

  * the rules: synthetic examples that must be masked, and ordinary text that
    must NOT be (a missed name is better than hidden ordinary text);
  * TRIGGER: one simple pattern Python and Postgres read the same way
    (putr/putra word-bounded, so "Brahmaputra" never triggers), and it matches
    every text the rules change -- the invariant public search relies on;
  * isolation: no scoring, ingest, entity or serving-build module imports the
    helper, so stored text and the risk_result checksum cannot change;
  * every anonymous description endpoint is masked, a logged-in account sees
    the served text, and anonymous search never matches a masked name;
  * graph Work labels: numbers masked before the 120-character cut, and
    beneficiary details masked for anonymous callers.

Status: written 2026-10-02, NOT YET RUN (DEPLOY_NOTES.md "Tests to run").
The DB-backed tests -- including the Postgres side of TRIGGER -- need the
CI database.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.entities.graph_build import _work_label
from app.serving.public_mask import (
    DESCRIPTION_KEYS,
    NAME_MASK,
    TRIGGER,
    mask_public,
    public_view,
)
from app.serving.redact import mask_personal

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

# ---- rules (synthetic examples -- not real data) -------------------------------------------------

MASKED = [
    (
        "beneficiary name sh. ramesh kumar s/o mohan lal, having 70% disability, contact no [phone removed]",
        "beneficiary name [name removed] s/o [name removed], having [detail removed] disability, "
        "contact no [phone removed]",
    ),
    (
        "Battery tricycle for beneficiary Smt. Sunita Devi w/o Raj Kumar, address: ward 5, gandhi nagar, "
        "rampur, mob [phone removed]",
        "Battery tricycle for beneficiary [name removed] w/o [name removed], address: [address removed], "
        "mob [phone removed]",
    ),
    (
        "Construction of community hall, contact person Shri Mahesh Yadav (Pradhan) [phone removed]",
        "Construction of community hall, contact person [name removed] (Pradhan) [phone removed]",
    ),
    (
        "Solar light near the house of Late Sh. Ram Prasad s/o Shyam Lal, village Rampur",
        "Solar light near the house of [name removed] s/o [name removed], village Rampur",
    ),
    (
        "Solar light near resident of Sh. Ram Prakash Sharma, Public Place, Village Rampur",
        "Solar light near resident of [name removed], Public Place, Village Rampur",
    ),
    (
        "Navin handpump Mohan Putra Sohan ke ghar ke pass, work agency GP Rampur",
        "Navin handpump [name removed] Putra [name removed] ke ghar ke pass, work agency GP Rampur",
    ),
]

KEEP = [
    # only ABOUT disability: no person, so the percentage stays
    "Purchase of 25 motorised tricycles for divyangjan with more than 80% disability, Rampur district",
    # titles in place names, and "address" that is not a person's address
    "CC road from Shri Ram Mandir to Smt. Indira Gandhi Memorial School and public address system",
    "Installation of R/O water plant at Disabled Rehabilitation Centre, Buxar",
    "Work of RCC road from tatvada faliya to Adrash Nivasi school, at mahuva village",
    "Construction of community building in campus of Disabled Rehabilitation Center",
    "Purchase of Three wheeler at Disabled person",
    "Patni Nagar ma RCC Road work",
    "Pucca drain from Ram Lal ke ghar se Shyam Lal ke ghar tak",
    "Supply of 10 hand pumps, Rs 4,50,000, work 1234-LS, pincode 482001",
]


@pytest.mark.parametrize("raw,masked", MASKED)
def test_synthetic_examples_are_masked(raw, masked):
    assert mask_public(raw) == masked
    assert re.search(TRIGGER, raw, re.IGNORECASE)  # so public search excludes the row


@pytest.mark.parametrize("keep", KEEP)
def test_ordinary_text_is_left_alone(keep):
    assert mask_public(keep) == keep


def test_nothing_is_masked_without_a_trigger_word():
    """Public search excludes exactly the rows whose text matches TRIGGER, so a
    rule must never fire on text without it."""
    no_trigger = "Sh. Ramesh Kumar and Shri Mahesh, 70% disability, ward 5"
    assert not re.search(TRIGGER, no_trigger, re.IGNORECASE)
    assert mask_public(no_trigger) == no_trigger


def test_masking_is_idempotent_and_null_safe():
    for raw, masked in MASKED:
        assert mask_public(masked) == masked
    assert mask_public(None) is None and mask_public("") == ""


def test_trigger_uses_only_syntax_python_and_postgres_share():
    # Literal words plus one word-bounded putr alternative written with `^`,
    # `$`, `[^a-z]`, `( )`, `|` and `?` only -- no backslash escape such as
    # `\b`, whose Postgres spelling differs.
    assert "\\" not in TRIGGER
    assert re.fullmatch(r"[a-z /|()^$\[\]?-]+", TRIGGER), TRIGGER
    assert "(^|[^a-z])putr(a|i)?([^a-z]|$)" in TRIGGER


PUTR_WORDS = ["Mohan Putra Sohan ke ghar", "Mohan putr-Sohan", "PUTRI Sita", "putra Ram"]
NOT_PUTR = ["Embankment on Brahmaputra river", "Road near Putrapur village", "Study tour to Putrajaya"]


@pytest.mark.parametrize("t", PUTR_WORDS)
def test_putr_triggers_as_a_whole_word(t):
    assert re.search(TRIGGER, t, re.IGNORECASE)


@pytest.mark.parametrize("t", NOT_PUTR)
def test_putr_inside_a_word_does_not_trigger_or_mask(t):
    assert not re.search(TRIGGER, t, re.IGNORECASE)
    assert mask_public(t) == t


def test_trigger_matches_every_text_the_rules_change():
    """The search invariant: a row whose text TRIGGER does not match is never
    masked, so excluding trigger rows' description words from public search
    can never leave a masked word searchable."""
    samples = [raw for raw, _ in MASKED] + KEEP + PUTR_WORDS + NOT_PUTR
    for t in samples:
        if mask_public(t) != t:
            assert re.search(TRIGGER, t, re.IGNORECASE), t


def test_public_view_masks_descriptions_only_and_never_mutates():
    body = {
        "description": MASKED[0][0],
        "mp_name": "Shri Example MP",
        "records": [{"description": MASKED[2][0], "category": "contact person Shri X"}],
        "top_signals": {"cost_anomalies": [{"Work Description": MASKED[0][0]}]},
    }
    out = public_view(body, authenticated=False)
    assert out["description"] == MASKED[0][1]
    assert out["records"][0]["description"] == MASKED[2][1]
    assert out["top_signals"]["cost_anomalies"][0]["Work Description"] == MASKED[0][1]
    assert out["mp_name"] == body["mp_name"] and out["records"][0]["category"] == "contact person Shri X"
    assert body["description"] == MASKED[0][0]  # the (possibly cached) input is untouched
    assert public_view(body, authenticated=True) is body
    assert DESCRIPTION_KEYS == {"description", "Work Description"}


# ---- isolation: the helper never reaches scoring or stored text -----------------------------------

ISOLATED = [
    *sorted((APP / "analytics").rglob("*.py")),
    *sorted((APP / "ingest").rglob("*.py")),
    *sorted((APP / "entities").rglob("*.py")),
    APP / "serving" / "build.py",
    APP / "geo" / "build.py",
    *sorted((ROOT / "scripts").glob("run_*.py")),
]


def _imports_public_mask(path: Path) -> bool:
    tree = ast.parse(path.read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if "public_mask" in (node.module or "") or any("public_mask" in a.name for a in node.names):
                return True
        elif isinstance(node, ast.Import) and any("public_mask" in a.name for a in node.names):
            return True
    return "public_mask" in path.read_text("utf-8")


def test_masking_helper_is_never_imported_by_scoring_or_build_modules():
    names = {p.name for p in ISOLATED}
    for must in ("signals.py", "signals_run.py", "fusion.py", "confidence.py", "risk_run.py"):
        assert must in names, must  # the scoring path is really covered
    for p in (APP / "serving" / "build.py", APP / "geo" / "build.py", APP / "entities" / "graph_build.py"):
        assert p in ISOLATED
    offenders = [str(p.relative_to(ROOT)) for p in ISOLATED if _imports_public_mask(p)]
    assert not offenders, offenders


def test_masking_module_never_writes():
    src = (APP / "serving" / "public_mask.py").read_text("utf-8")
    assert not re.search(r"\b(UPDATE|INSERT|DELETE)\b|\.execute\(|session", src)


# ---- the database side (needs CI) --------------------------------------------------------------


@pytest.fixture(scope="module")
def run_id(db_session):
    from app.serving import service

    s = service.served(db_session)
    assert s.run_id is not None
    return s.run_id


def test_postgres_reads_the_trigger_like_python(db_session):
    samples = [raw for raw, _ in MASKED] + KEEP + PUTR_WORDS + NOT_PUTR
    samples += ["S/O Ram", "SAMPARK SUTRA", "Phone"]
    for s in samples:
        pg = db_session.execute(
            text("SELECT CAST(:s AS text) ~* CAST(:t AS text)"), {"s": s, "t": TRIGGER}
        ).scalar_one()
        assert pg == bool(re.search(TRIGGER, s, re.IGNORECASE)), s


def test_search_trigger_holds_for_every_masked_served_row(db_session, run_id):
    """The invariant on the real read models: every served description the
    anonymous view changes has a search_text that Postgres matches with
    TRIGGER, so public search excludes its description words."""
    for table in ("served_work", "map_work"):
        rows = db_session.execute(
            text(f"SELECT work_key, description, search_text ~* :t AS trig FROM {table} WHERE run_id = :r"),
            {"r": run_id, "t": TRIGGER},
        ).all()
        assert rows
        changed = [r for r in rows if r.description and mask_public(r.description) != r.description]
        bad = [r.work_key for r in changed if not r.trig]
        assert not bad, (table, bad[:10])


# ---- anonymous endpoints and search (needs CI) ---------------------------------------------------


@pytest.fixture(scope="module")
def beneficiary_works(db_session, run_id):
    """Scored, served works whose served description names a beneficiary."""
    rows = db_session.execute(
        text(
            "SELECT sw.work_key, sw.mp, sw.description, sw.constituency, sw.state, sw.category, "
            "sw.district_authority, mw.latitude IS NOT NULL AS on_map "
            "FROM served_work sw "
            "LEFT JOIN map_work mw ON mw.run_id = sw.run_id AND mw.work_key = sw.work_key "
            "WHERE sw.run_id = :r AND sw.scored AND sw.description ~* 'beneficiary name' "
            "ORDER BY sw.work_key LIMIT 200"
        ),
        {"r": run_id},
    ).all()
    works = [w for w in rows if mask_public(w.description) != w.description]
    assert len(works) >= 5
    return works


def _descriptions(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in DESCRIPTION_KEYS and isinstance(v, str):
                yield v
            else:
                yield from _descriptions(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _descriptions(v)


def _assert_public(resp, where):
    assert resp.status_code == 200, (where, resp.status_code)
    leaks = [d for d in _descriptions(resp.json()) if mask_public(d) != d]
    assert not leaks, (where, len(leaks))
    return resp.json()


def test_every_anonymous_description_endpoint_is_masked(client, beneficiary_works):
    for w in beneficiary_works[:5]:
        wk = w.work_key
        body = _assert_public(client.get(f"/api/record/{wk}"), "record")
        assert NAME_MASK in body["source_record"]["description"]
        _assert_public(client.get(f"/api/risk/{wk}"), "risk")
        _assert_public(client.get(f"/api/evidence/{wk}"), "evidence")
        q = _assert_public(client.get("/api/queue", params={"search": wk.lower(), "page_size": 50}), "queue")
        assert any(r["record_id"] == wk for r in q["records"])  # work key still finds it
        _assert_public(client.get("/api/map-works", params={"search": wk.lower(), "limit": 50}), "map-works")
        if w.mp:
            _assert_public(client.get(f"/api/mp-performance/{w.mp}"), "mp-performance")
    paths = ("/api/summary", "/api/risk/top?limit=500", "/api/investigations", "/api/queue?page_size=200")
    for path in paths:
        _assert_public(client.get(path), path)


def test_logged_in_accounts_see_the_served_text(client, make_user, beneficiary_works):
    ministry = make_user("ministry")
    w = beneficiary_works[0]
    body = client.get(f"/api/record/{w.work_key}", headers=ministry).json()
    assert body["source_record"]["description"] == w.description


def _masked_word(w) -> str | None:
    """A word the anonymous view hides that no other searchable field holds."""
    kept = set(re.findall(r"[a-z]{4,}", mask_public(w.description).lower()))
    fields = (w.work_key, w.mp, w.constituency, w.state, w.category, w.district_authority)
    others = " ".join(str(x or "") for x in fields).lower()
    for word in re.findall(r"[a-z]{4,}", w.description.lower()):
        if word not in kept and word not in others:
            return word
    return None


def test_anonymous_search_never_matches_a_masked_name(client, make_user, beneficiary_works):
    ministry = make_user("ministry")
    tried = 0
    for w in beneficiary_works:
        word = _masked_word(w)
        if not word or not w.mp:
            continue
        tried += 1
        params = {"search": word, "mp": w.mp, "page_size": 200}
        anon = client.get("/api/queue", params=params).json()
        assert all(r["record_id"] != w.work_key for r in anon["records"]), word
        named = client.get("/api/queue", params=params, headers=ministry).json()
        assert any(r["record_id"] == w.work_key for r in named["records"]), word
        if w.on_map:
            mw = client.get("/api/map-works", params={"search": word, "limit": 500}).json()
            assert all(r["record_id"] != w.work_key for r in mw), word
        if tried >= 5:
            break
    assert tried >= 3


def test_stored_served_text_is_unchanged(client, db_session, run_id, beneficiary_works):
    w = beneficiary_works[0]
    client.get(f"/api/record/{w.work_key}")
    stored = db_session.execute(
        text("SELECT description FROM served_work WHERE run_id = :r AND work_key = :wk"),
        {"r": run_id, "wk": w.work_key},
    ).scalar_one()
    assert stored == w.description and mask_public(stored) != stored


# ---- graph Work labels --------------------------------------------------------------------------


def test_work_label_masks_numbers_before_the_cut():
    raw = "x" * 112 + " 9876543210 more text"
    label = _work_label(raw, "1-LS")
    assert len(label) <= 120 and not re.search(r"[0-9]{5}", label)
    assert _work_label(None, "1-LS") == "1-LS"


def test_anonymous_graph_work_labels_are_masked(client, make_user):
    public = client.get("/api/graph-data").json()
    for n in public["nodes"]:
        if n["type"] == "Work":
            assert mask_personal(n["label"]) == n["label"] and mask_public(n["label"]) == n["label"]
    named = client.get("/api/graph-data", headers=make_user("ministry")).json()
    for n in named["nodes"]:
        if n["type"] == "Work":
            assert mask_personal(n["label"]) == n["label"]

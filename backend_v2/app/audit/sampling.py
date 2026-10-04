"""
Randomised audit sample (BLUEPRINT.md §12 "Randomised audit sample":
"Tier-stratified random sample of about 300 works (all CRITICAL up to 30,
100 HIGH, 100 MODERATE, 70 LOW), reviewers blind to tier, outcomes:
follow-up needed, none, data issue; report precision with Wilson intervals
and inter-rater agreement").

The human review itself is outside this codebase. This module provides the
mechanism:
  draw()    -- a seeded simple random sample without replacement within
               each tier of one run's scored works (the published config's
               risk_result), stored with its full design (population sizes,
               quotas, drawn counts, seed) so it can be reproduced and its
               estimates weighted correctly.
  blind_items() -- what a reviewer sees: a random blind code and the work's
               SOURCE facts only -- never its tier, score, confidence,
               signals or peer figures. Items are ordered by a seeded
               shuffle across strata, so position reveals nothing either.
  report()  -- per-stratum precision (follow-up needed / (follow-up needed +
               no follow-up), data issues reported separately) with Wilson
               95% intervals, the population-weighted precision of the
               flagged tiers (CRITICAL + HIGH), and Cohen's kappa over items
               reviewed by at least two reviewers.
"""

from __future__ import annotations

import datetime as dt
import math
import random
import secrets
from collections import Counter, defaultdict

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..analytics.publish import published
from ..models.security import AUDIT_OUTCOMES, AuditReview, AuditSample, AuditSampleItem
from ..serving.redact import mask_personal

QUOTAS = {"CRITICAL": 30, "HIGH": 100, "MODERATE": 100, "LOW": 70}
FLAGGED = ("CRITICAL", "HIGH")
Z95 = 1.959963984540054


def draw(
    session: Session,
    *,
    created_by: str,
    seed: int | None = None,
    run_id: int | None = None,
    quotas: dict | None = None,
) -> AuditSample:
    pub = published(session)
    if pub is None:
        raise ValueError("no published run to sample from")
    run_id = pub.run_id if run_id is None else run_id
    config = pub.default_config_name
    quotas = dict(quotas or QUOTAS)
    seed = secrets.randbelow(2**31 - 1) if seed is None else int(seed)
    rng = random.Random(seed)

    population: dict[str, list[str]] = defaultdict(list)
    for wk, tier in session.execute(
        text(
            "SELECT work_key, tier FROM risk_result WHERE run_id = :r AND config_name = :c ORDER BY work_key"
        ),
        {"r": run_id, "c": config},
    ):
        population[tier].append(wk)

    chosen: list[tuple[str, str]] = []
    drawn = {}
    for tier, quota in quotas.items():
        keys = population.get(tier, [])  # already sorted: the draw depends only on seed + population
        k = min(quota, len(keys))
        drawn[tier] = k
        chosen += [(wk, tier) for wk in rng.sample(keys, k)]
    rng.shuffle(chosen)  # review order mixes strata

    sample = AuditSample(
        run_id=run_id,
        config_name=config,
        seed=seed,
        created_by=created_by,
        design={
            "quotas": quotas,
            "population": {t: len(population.get(t, [])) for t in quotas},
            "drawn": drawn,
            "total_drawn": len(chosen),
            "method": "simple random sample without replacement within each tier (Python random.Random(seed) "
            "over the tier's work_keys sorted ascending), then one seeded shuffle of the review order",
            "blinding": "reviewers see a random blind code and the work's source facts only; tier, score, "
            "confidence, signals and peer figures are withheld",
            "outcomes": list(AUDIT_OUTCOMES),
        },
    )
    session.add(sample)
    session.flush()
    session.add_all(
        AuditSampleItem(
            sample_id=sample.id, work_key=wk, stratum=tier, blind_code=secrets.token_hex(6), position=i + 1
        )
        for i, (wk, tier) in enumerate(chosen)
    )
    session.flush()
    return sample


# Source facts only: nothing derived by the risk engine.
_BLIND_SQL = text(
    """
    SELECT i.id, i.blind_code, i.position, sw.work_key, sw.house, sw.mp, sw.description, sw.category,
           sw.state, sw.constituency, sw.district_authority, sw.stage, sw.amount, sw.recommended_date,
           sw.sanction_date, sw.completion_date
    FROM audit_sample_item i
    JOIN audit_sample s ON s.id = i.sample_id
    JOIN served_work sw ON sw.run_id = s.run_id AND sw.work_key = i.work_key
    WHERE i.sample_id = :sid
    ORDER BY i.position
    LIMIT :lim OFFSET :off
    """
)
BLIND_FIELDS = (
    "blind_code",
    "position",
    "work_key",
    "house",
    "mp",
    "description",
    "category",
    "state",
    "constituency",
    "district_authority",
    "stage",
    "amount",
    "recommended_date",
    "sanction_date",
    "completion_date",
)


def blind_items(session: Session, sample_id: int, reviewer_user_id: int, page: int, page_size: int) -> dict:
    total = session.execute(
        text("SELECT count(*) FROM audit_sample_item WHERE sample_id = :sid"), {"sid": sample_id}
    ).scalar_one()
    rows = (
        session.execute(_BLIND_SQL, {"sid": sample_id, "lim": page_size, "off": (page - 1) * page_size})
        .mappings()
        .all()
    )
    mine = dict(
        session.execute(
            text(
                "SELECT r.item_id, r.outcome FROM audit_review r "
                "JOIN audit_sample_item i ON i.id = r.item_id "
                "WHERE i.sample_id = :sid AND r.reviewer_user_id = :u"
            ),
            {"sid": sample_id, "u": reviewer_user_id},
        ).all()
    )
    items = []
    for r in rows:
        item = {k: (str(r[k]) if hasattr(r[k], "isoformat") else r[k]) for k in BLIND_FIELDS}
        item["description"] = mask_personal(item["description"])
        item["your_outcome"] = mine.get(r["id"])
        items.append(item)
    return {"sample_id": sample_id, "items": items, "total": total, "page": page, "page_size": page_size}


def archive(session: Session, sample_id: int, reason: str) -> AuditSample:
    """Retire a sample (e.g. drawn on a superseded run): kept with its items and
    reviews as the record, but closed to new reviews."""
    sample = session.get(AuditSample, sample_id)
    if sample is None:
        raise LookupError(sample_id)
    if sample.archived_at is None:
        sample.archived_at = dt.datetime.now(dt.timezone.utc)
        sample.archive_reason = reason
        session.flush()
    return sample


class ArchivedSampleError(ValueError):
    pass


def add_review(
    session: Session, *, blind_code: str, principal, outcome: str, note: str | None
) -> AuditReview:
    if outcome not in AUDIT_OUTCOMES:
        raise ValueError(f"outcome must be one of {AUDIT_OUTCOMES}")
    item = session.execute(
        select(AuditSampleItem).where(AuditSampleItem.blind_code == blind_code)
    ).scalar_one_or_none()
    if item is None:
        raise LookupError(blind_code)
    if session.get(AuditSample, item.sample_id).archived_at is not None:
        raise ArchivedSampleError("this audit sample is archived")
    review = AuditReview(
        item_id=item.id,
        reviewer_user_id=principal.user_id,
        reviewer=principal.actor,
        outcome=outcome,
        note=note,
    )
    session.add(review)
    session.flush()
    return review


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa over (rater A, rater B) outcome pairs."""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[c] * cb[c] for c in AUDIT_OUTCOMES) / (n * n)
    if pe == 1:
        return 1.0 if po == 1 else None
    return round((po - pe) / (1 - pe), 4)


def report(session: Session, sample_id: int) -> dict:
    sample = session.get(AuditSample, sample_id)
    if sample is None:
        raise LookupError(sample_id)
    rows = session.execute(
        text(
            "SELECT i.id, i.stratum, r.outcome, r.reviewer_user_id, r.id AS rid FROM audit_sample_item i "
            "LEFT JOIN audit_review r ON r.item_id = i.id WHERE i.sample_id = :sid ORDER BY i.id, r.id"
        ),
        {"sid": sample_id},
    ).all()
    by_item: dict[int, dict] = {}
    for item_id, stratum, outcome, _uid, _rid in rows:
        d = by_item.setdefault(item_id, {"stratum": stratum, "outcomes": []})
        if outcome:
            d["outcomes"].append(outcome)

    strata = {}
    pop = sample.design.get("population", {})
    for tier in QUOTAS:
        items = [d for d in by_item.values() if d["stratum"] == tier]
        firsts = [d["outcomes"][0] for d in items if d["outcomes"]]  # an item's outcome = its first review
        c = Counter(firsts)
        decided = c["follow_up_needed"] + c["no_follow_up"]
        strata[tier] = {
            "population": pop.get(tier, 0),
            "drawn": len(items),
            "reviewed": len(firsts),
            "follow_up_needed": c["follow_up_needed"],
            "no_follow_up": c["no_follow_up"],
            "data_issue": c["data_issue"],
            "precision": round(c["follow_up_needed"] / decided, 4) if decided else None,
            "precision_wilson95": wilson(c["follow_up_needed"], decided),
        }
    flagged_pop = sum(strata[t]["population"] for t in FLAGGED)
    have = [t for t in FLAGGED if strata[t]["precision"] is not None]
    weighted = (
        round(
            sum(strata[t]["population"] * strata[t]["precision"] for t in have)
            / sum(strata[t]["population"] for t in have),
            4,
        )
        if have
        else None
    )
    pairs = [(d["outcomes"][0], d["outcomes"][1]) for d in by_item.values() if len(d["outcomes"]) >= 2]
    return {
        "sample_id": sample.id,
        "run_id": sample.run_id,
        "config": sample.config_name,
        "seed": sample.seed,
        "design": sample.design,
        "strata": strata,
        "flagged_precision_population_weighted": weighted,
        "flagged_population": flagged_pop,
        "flagged_strata_with_decisions": have,
        "inter_rater": {"items_double_reviewed": len(pairs), "cohen_kappa": cohen_kappa(pairs)},
        "definitions": {
            "precision": "follow_up_needed / (follow_up_needed + no_follow_up) per tier; data_issue excluded "
            "and reported separately (it feeds data quality, not a false alarm -- BLUEPRINT.md §10)",
            "item outcome": "the item's first review; later reviews are used only for inter-rater agreement",
            "weighted": "flagged-tier precision weighted by each tier's population share "
            "(stratified estimator)",
            "interval": "Wilson score interval, 95%",
        },
    }

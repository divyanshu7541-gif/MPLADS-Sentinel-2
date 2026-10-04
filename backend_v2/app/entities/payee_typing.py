"""
Phase 10: revised payee typing (BLUEPRINT.md §8 "Why typing comes first").

app/ingest/p5_payee.py's classify_payee_type (Phase 2) has a real bug found
by inspecting the actual 22,992 payees it stamped "private_firm": that
outcome was the function's DEFAULT for "matched no rule", not evidence of
being a firm. Sampling that bucket (docs/phase10_11_report.md) turned up
bare personal names with no honorific ("Noli devi", "Radha jat", "mukesh")
alongside real government-role titles it also missed ("BDO Kunihar",
"Sarpanch Shri Jindava", "CEO Sonkatch"). Both were being silently
mislabelled as a private firm.

This module's classify_payee_type_v2 fixes it with two changes, each
checked against the real data before being kept (counts in the report):
  1. A government/statutory-role token list, extended with roles actually
     observed in the sample (BDO, SACHIV, SARPANCH, CEO, CMO, CHAIRMAN,
     COMMITTEE, HABITATION, VILLAGE) -- 339 payees move from private_firm
     to statutory_or_government.
  2. A "manufacturer" type: a positive-evidence keyword list (INDUSTRIES,
     ENGINEERING, STEEL, CEMENT, ... -- BLUEPRINT.md §8 names manufacturers
     as a distinct category the payee field mixes in) -- 880 payees.
  3. The DEFAULT for "no rule matched" changes from private_firm to
     unclassified: private_firm is now only ever a POSITIVE match on a
     real firm-suffix token (ENTERPRISES, TRADERS, PVT, LTD, ...), exactly
     BLUEPRINT.md §8's "ambiguous cases queued for review" -- an
     unclassified payee still gets typed and still participates in
     concentration/reach math (excluding it would bias the market shares
     it's part of), it just carries an honest "not positively identified"
     label instead of a guessed one.

A personal name with NO honorific and NO organisation token (the bulk of
the 17,223 payees that are neither government nor a positively-identified
firm) is deliberately left "unclassified", not reclassified to
"individual": a blanket "all-lowercase => individual" rule was tried
against the real data first and rejected -- it also matched real
government titles written in lowercase ("sarpanch gp musadehi", "cmo
sanawad", "chairman habitation works committee ..."), which would have
been a worse error than leaving them unclassified.

Idempotent: classify_payee_type_v2 is a pure function of canonical_name, so
reclassify_payees can run any number of times with the same result, and is
NOT gated by "already run" the way ingest loaders are -- it recomputes
every payee's type every time it is called.
"""

from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.entities import Payee

_GOVT_TOKENS = re.compile(
    r"\b(COLLECTOR|COMMISSIONER|MUNICIPAL|CORPORATION|DISTRICT|GOVT|GOVERNMENT|PANCHAYAT|"
    r"BOARD|SOCIETY|TRUST|DEPARTMENT|DIRECTOR|EXECUTIVE ENGINEER|ZILA|GRAM|BLOCK|NAGAR|"
    r"PARISHAD|SAMITI|OFFICER|AUTHORITY|COUNCIL|PWD|IDA|"
    r"BDO|SACHIV|SARPANCH|CEO|CMO|CHAIRMAN|COMMITTEE|HABITATION|VILLAGE)\b",
    re.IGNORECASE,
)
_MANUFACTURING_TOKENS = re.compile(
    r"\b(INDUSTR(?:Y|IES)|MANUFACTUR(?:ING|ERS?)|MFG|ENGINEERING|ELECTRICALS?|MOTORS?|CEMENTS?|"
    r"STEELS?|CABLES?|PIPES?|BRICKS?|FOUNDR(?:Y|IES)|MILLS?|PLASTICS?|POLYMERS?|FIBRE|TEXTILES?)\b",
    re.IGNORECASE,
)
_FIRM_TOKENS = re.compile(
    r"\b(ENTERPRISES?|TRADING|TRADERS|CONTRACTORS?|CONSTRUCTION|SUPPLIERS?|AGENC(?:Y|IES)|"
    r"ASSOCIATES?|COMPANY|CO\.?|PVT|LTD|LIMITED|FIRM|CONCERN|STORES?|BROTHERS?|SONS?|HARDWARE|"
    r"TIMBER|FURNITURE|AGRO|BUILDERS?|INFRA|SERVICES?)\b",
    re.IGNORECASE,
)
_INDIVIDUAL_TOKENS = re.compile(r"^(SHRI|SMT|MS|MR|DR|KUM|KM)\.?\s", re.IGNORECASE)


def classify_payee_type_v2(name: str) -> str:
    n = (name or "").strip()
    if _GOVT_TOKENS.search(n):
        return "statutory_or_government"
    if _MANUFACTURING_TOKENS.search(n):
        return "manufacturer"
    if _FIRM_TOKENS.search(n):
        return "private_firm"
    if _INDIVIDUAL_TOKENS.match(n):
        return "individual"
    return "unclassified"


def reclassify_payees(session: Session) -> dict:
    """Recompute payee_type for every payee row. Returns a from -> to
    transition count and the new type distribution, for the phase report."""
    payees = session.execute(select(Payee)).scalars().all()
    transitions: Counter[tuple[str, str]] = Counter()
    after: Counter[str] = Counter()
    for p in payees:
        new_type = classify_payee_type_v2(p.canonical_name)
        if new_type != p.payee_type:
            transitions[(p.payee_type, new_type)] += 1
            p.payee_type = new_type
        after[new_type] += 1
    session.flush()
    return {
        "payees": len(payees),
        "changed": sum(transitions.values()),
        "transitions": {f"{a} -> {b}": c for (a, b), c in transitions.most_common()},
        "type_distribution_after": dict(after),
    }

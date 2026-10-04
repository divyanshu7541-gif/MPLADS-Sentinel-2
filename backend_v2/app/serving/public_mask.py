"""
Display-time masking of beneficiary details for ANONYMOUS callers (owner
decisions, 2026-10-02).

Some portal work descriptions name a private person: a beneficiary, their
father or husband, a contact person, an address, and sometimes the person's
disability percentage. The public site (no token) gets those parts masked;
logged-in accounts get the served text unchanged. Phone and Aadhaar-shaped
numbers are masked for everyone, earlier, by app/serving/redact.py -- this
module runs on text that has already been through that.

Display-time ONLY. The stored text (work.raw_description,
description_normalized, served_work/map_work) is never changed: near-duplicate
scoring reads the raw text (app/analytics/signals.py), so masking it would move
risk_result and the checksum. tests/test_public_masking.py checks that no
scoring, ingest, entity or serving-build module imports this one.

Narrow on purpose: a missed name is better than hiding ordinary text. Only
the person's name, a relative's name, a person-linked address and a
person-linked disability percentage are masked -- never the work type, the
amount, or MP/agency names. A work that is only ABOUT disability ("tricycles
for divyangjan with 80% disability") is left alone.

Rules (all case-insensitive), applied only if TRIGGER matches somewhere:
  R1 beneficiary   "beneficiary name (is|:|-) NAME", "name of beneficiary NAME",
                   "beneficiary TITLE NAME", "labharthi TITLE NAME"
  R2 relative      the NAME after s/o, d/o, w/o, son/daughter/wife of, putr;
                   and the NAME right before one of those
  R3 contact       "contact (person) / sampark (sutra) TITLE NAME", and a
                   TITLE NAME right before "[phone removed]"
  R6 divyang       "divyang(jan) NAME" when resident of / s/o / d/o / w/o follows
  R4 address       a TITLE NAME after "resident of" / "nivasi" is the person;
                   otherwise the place after them; after
                   "address (is|:|-)" only when R1-R3/R6 fired
  R5 disability %  "70% disability", "disability of 80%" -- only when a person
                   rule fired; the number is masked, the word stays
A NAME is an optional title plus 1-4 words on one line; it stops at a comma,
digit, bracket, slash word (s/o) or a stop word ("having", "village", ...).

Best effort, not a guarantee: unusual spellings, Hindi script and names with
no title or keyword get through (DEPLOY_NOTES.md).
"""

from __future__ import annotations

import re

NAME_MASK = "[name removed]"
ADDRESS_MASK = "[address removed]"
DETAIL_MASK = "[detail removed]"

# The ONE trigger pattern, used verbatim by Python (re.IGNORECASE) and by
# Postgres (`~*`) in public search. It is plain lowercase literals joined by
# `|`, plus one word-bounded alternative for putr/putra/putri written with
# syntax both engines read the same way (`^`, `$`, `[^a-z]`, `( )`, `?` -- no
# `\b`, which Postgres spells differently), so "Brahmaputra" never triggers.
# Every rule below needs one of these words, so text with no match is never
# masked -- which is what lets public search exclude exactly the maskable rows.
TRIGGER = (
    "beneficiar|labharthi|s/o|d/o|w/o|son of|daughter of|wife of|(^|[^a-z])putr(a|i)?([^a-z]|$)"
    "|resident of|nivasi|niwasi|address|contact|sampark|phone"
)
_TRIGGER = re.compile(TRIGGER, re.IGNORECASE)

# One or more titles ("Late Sh.", "Lt. Shri").
_TITLE = (
    r"\b(?:(?:sh|shri|shree|sri|smt|shrimati|sushri|km|kum|kumari|mr|mrs|ms|dr|late|lt)"
    r"(?:\.[ \t]*|[ \t]+))+"
)
_STOP = (
    r"(?:having|he|she|his|her|is|was|who|whose|age|aged|village|vill|gram|ward|address|contact|"
    r"mob|mobile|ph|phone|resident|nivasi|niwasi|and|for|at|in|of|with|to|from|by|on|the|district|"
    r"dist|tehsil|block|no|near|under|son|daughter|wife|disability|disabled|disable|handicapped|"
    r"divyang|divyangjan|ji|etc|towards|road|street|gali|lane|length|marg|school|mandir|temple|colony|"
    r"house|add|naya|logo|person|persons|girl|boy|child|student|farmer|widow|labourer|patient|member|"
    r"family|residence|youth|work|works|beneficiary|beneficiaries|handicap|following|public|place|"
    r"community|scheme|tank|light|solar|home|plot|side|drain|drains|line|mohalla|bazar|new|par|gp|up|"
    r"khurd|kalan|teh|cost|if|till|field|sector|incharge|manager|former|mla|mp|ex|purv|secretary|"
    r"president|sarpanch|teacher|principal|name|putr|putra|putri|navin|naveen|handpump|hand|pump|"
    # Hindi place words: "<name> ke ghar se ... tak" = "from <name>'s house to ..."
    r"ke|ka|ki|ko|se|me|mein|tak|tk|ghar|makan|dhani|khet|paas|pass|dwara|paramarsh)\b"
)
# A word may not touch "/" on either side (so "s/o" and "h/o" never join a name).
# An initial ("k.") may be followed by more words; any other word ending in "."
# ends the name ("Shri Banarasi Lal. Cost ..." keeps "Cost").
_WSTART = r"(?<!/)(?!" + _STOP + r")"
_UNIT = _WSTART + r"(?:[a-z]\.|[a-z][a-z']*(?![a-z/.]))"
_LAST = _WSTART + r"(?:[a-z]\.|[a-z][a-z']*\.?(?![a-z/]))"
_WORDS = r"(?:" + _UNIT + r"[ \t]+){0,3}" + _LAST + r"(?:[ \t]+ji\b)?"
_NAME = r"(?:" + _TITLE + r")?" + _WORDS
_TITLED_NAME = _TITLE + _WORDS
# An untitled name right before s/o etc.: 1-3 words, no dotted word except an
# initial, so "... solar light. Rampal s/o" keeps "solar light.".
_BEFORE_REL_NAME = r"(?:" + _TITLE + r"|\b)" + r"(?:" + _UNIT + r"[ \t]+){0,2}" + _UNIT
# putr/putra/putri: Hindi "son/daughter of" ("patni" is also a surname and a place name).
_REL = r"(?:s/o|d/o|w/o|son[ \t]+of|daughter[ \t]+of|wife[ \t]+of|putr[ai]?\b)"
# An address runs (max 80 chars) to the next contact word, relation, clause word
# ("who", "under", "ko" = "to"), sentence end, bracket, ';', line end or text end.
_ADDR_END = (
    r"(?=[ \t]*,?[ \t]*(?:(?:contact|mob|mobile|ph|phone|who|is|under|ko|ke|ki|for|to|executing|agency|"
    r"handpump|solar|s/o|d/o|w/o|putr)(?![a-z])"
    r"|\.(?:\s|$)|\[|\)|;|\n|$))"
)
_ADDR = r"(?!(?:to|for|near|the)\b)[^\s\[\]();][^\n;()\[\]]{0,79}?" + _ADDR_END

_I = re.IGNORECASE
_PERSON_RULES = [
    # R1 beneficiary
    re.compile(r"\b(beneficiary[ \t]+name[ \t]*(?:is\b|:|-)?[ \t]*)(" + _NAME + ")", _I),
    re.compile(
        r"\b(name[ \t]+of[ \t]+(?:the[ \t]+)?beneficiary[ \t]*(?:is\b|:|-)?[ \t]*)(" + _NAME + ")", _I
    ),
    re.compile(r"\b(beneficiar(?:y|ies)[ \t]*[:\-]?[ \t]*)(" + _TITLED_NAME + ")", _I),
    re.compile(r"\b(labharthi[ \t]*(?:ka[ \t]+naam|name)?[ \t]*[:\-]?[ \t]*)(" + _TITLED_NAME + ")", _I),
    # R4 "resident of / nivasi" + TITLE NAME: the person, not an address
    re.compile(r"\b((?:resident[ \t]+of|nivasi|niwasi)[ \t]*[:\-]?[ \t]*)(" + _TITLED_NAME + ")", _I),
    # R6 divyang NAME (resident of | s/o ...)
    re.compile(
        r"\b(divyang(?:jan)?[ \t]+)(" + _NAME
        + r")(?=[ \t]*\(?[ \t]*(?:resident[ \t]+of|nivasi|" + _REL + "))",
        _I,
    ),
    # R2 relative: the NAME right before the relation, then the NAME after it
    re.compile(r"()(" + _BEFORE_REL_NAME + r")(?=[ \t]*,?[ \t]*" + _REL + r")", _I),
    re.compile(r"(\b" + _REL + r"[ \t]*[:\-.]?[ \t]*)(" + _NAME + ")", _I),
    # R3 contact person
    re.compile(
        r"\b((?:contact|sampark)(?:[ \t]+(?:person|sutra))?(?:[ \t]*(?:no\.?|number))?[ \t]*[:\-.]?[ \t]*)("
        + _TITLED_NAME
        + ")",
        _I,
    ),
    re.compile(
        r"()(" + _TITLED_NAME + r")(?=[ \t,]*(?:\([^)\n]{0,30}\)[ \t,]*)?"
        r"(?:(?:mob(?:ile)?|ph(?:one)?|contact)(?:[ \t]*no\.?)?[ \t]*[:\-.]?[ \t]*)?\[phone removed\])",
        _I,
    ),
]
# ("Adarsh Nivasi School" is a residential school, not a person's address.)
_RESIDENT = re.compile(
    r"\b((?:resident[ \t]+of|nivasi|niwasi)"
    r"(?![ \t]+(?:school|vidyalaya|shala|ashram|hostel)\b)[ \t]*[:\-]?[ \t]*)("
    + _ADDR
    + ")",
    _I,
)
_ADDRESS = re.compile(r"\b(address[ \t]*(?:is\b|:|-)[ \t]*)(" + _ADDR + ")", _I)
_DISABILITY = r"(?:disab|handicap|viklang|divyang)[a-z]*"
_PCT = r"\d{1,3}(?:\.\d+)?[ \t]*(?:%|percent\b)"
_PCT_RULES = [
    re.compile(r"()(" + _PCT + r")(?=[ \t]*" + _DISABILITY + ")", _I),
    re.compile(
        r"(\b" + _DISABILITY + r"[ \t]*(?:of|is|:)?[ \t]*(?:more[ \t]+th[ae]n|above|over|up[ \t]*to)?[ \t]*)("
        + _PCT
        + ")",
        _I,
    ),
]


def _sub(rx: re.Pattern, mask: str, text: str) -> tuple[str, int]:
    return rx.subn(lambda m: m.group(1) + mask, text)


def mask_public(text: str | None) -> str | None:
    """The anonymous view of one description (already phone/Aadhaar-masked)."""
    if not text or not _TRIGGER.search(text):
        return text
    person = 0
    for rx in _PERSON_RULES:
        text, n = _sub(rx, NAME_MASK, text)
        person += n
    text, n = _sub(_RESIDENT, ADDRESS_MASK, text)
    person += n
    if person:
        text, _ = _sub(_ADDRESS, ADDRESS_MASK, text)
        for rx in _PCT_RULES:
            text, _ = _sub(rx, DETAIL_MASK, text)
    return text


# Response keys that carry work-description text.
DESCRIPTION_KEYS = frozenset({"description", "Work Description"})


def public_view(body, authenticated: bool):
    """A copy of an API response with descriptions masked for an anonymous
    caller; the body itself when the caller is logged in. Never mutates the
    input (summary/analytics responses are cached objects)."""
    if authenticated:
        return body
    return _walk(body)


def _walk(o):
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            out[k] = mask_public(v) if k in DESCRIPTION_KEYS and isinstance(v, str) else _walk(v)
        return out
    if isinstance(o, list):
        return [_walk(v) for v in o]
    return o


def public_search_clause(alias: str, other_cols: tuple[str, ...], pat_param: str) -> tuple[str, dict]:
    """Extra WHERE text for an anonymous search: a row whose search text has a
    TRIGGER word (so its description might be masked) matches only on its
    other searchable fields, never on description words -- so a masked name
    can never match. search_text holds the WHOLE description (map_work's
    description column is cut at 2,000 characters), so testing it is a
    superset of testing the description. `alias` is "" or "mw."; `pat_param`
    names the caller's existing LIKE pattern."""
    cols = ", ".join(f"{alias}{c}" for c in other_cols)
    sql = (
        f" AND (COALESCE({alias}search_text, '') !~* :pm_trigger"
        f" OR lower(concat_ws(' ', {cols})) LIKE :{pat_param} ESCAPE '\\')"
    )
    return sql, {"pm_trigger": TRIGGER}

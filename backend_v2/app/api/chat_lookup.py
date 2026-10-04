"""
Copilot lookups for constituency and MP questions ("tell me about <place>",
"who is my MP", "show works in <place>", "compare <A> with <B>").

Deterministic -- no language model is involved. Names are matched against the
served run's own lists (service.distinct / service.constituencies, the code
behind /api/mps and /api/constituencies), and a matched name's figures come
from service.mp_profile / constituency_profile / comparison, the code behind
/api/mp-performance, /api/constituency-performance and the comparison
endpoints -- so the chat and the Browse page always show the same numbers.
Tables read: served_work, plus published_run and serving_build to find the
served build. None of them is empty in production.

Rules:
  * A single exact or word-containment match is answered directly. Several
    matches, or a match found only by fuzzy spelling, become "Did you mean"
    chips (at most 5) -- a fuzzy guess is never answered as if it were sure.
  * Rajya Sabha members have no constituency; the reply says so and offers
    the state-level Priority Queue view.
  * Descriptions go through public_view() exactly as the endpoints do:
    masked for anonymous callers (every browser chat -- the chat sends no
    token), unchanged for a logged-in API client.
  * Stateless: follow-up context travels in the chip's message text or in
    the request's small `context`, whose names are re-checked here against
    the served lists before use.
  * Links use only routes and URL parameters the pages already read:
    /mp-performance?mp= or ?constituency=, /queue?mp= or ?state=.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..serving import labels, service
from ..serving.public_mask import public_view

MAX_CHOICES = 5
TOP_WORKS = 5
FUZZY_CUTOFF = 0.85
TITLE_CHARS = 90

PRIORITY_NOTE = (
    "Investigation priority reflects statistical signals for human review; it is not proof of wrongdoing."
)
NOT_AVAILABLE = (
    "Constituency and MP details are not available right now. Please try the Browse MP / Constituency page."
)
LOAD_FAILED = (
    "Constituency and MP details couldn't be loaded just now. Please try again in a moment, or open the "
    "Browse MP / Constituency page."
)

# ---- name normalising ---------------------------------------------------------------------------

_PAREN = re.compile(r"\(\s*(?:sc|st|[\d\s/\-–]+)\s*\)", re.I)  # (SC), (ST), (2022-28)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TITLES = frozenset(
    "shri sh shree sri smt shrimati sushri kumari km kum dr adv advocate prof mr mrs ms er late".split()
)
# Words that carry the question, not the name. Removed before word matching.
_FILLER = frozenset(
    """
    tell me about show list give get find see view what whats who whos is are was the a an my our your
    mp mps member members parliament of in for from at on to by works work projects project details detail
    profile info information constituency constituencies seat lok sabha rajya please pls i im am live
    living stay staying representative represents represent s know more any all which where how many
    much top priority priorities this that it its you yourself mplads sentinel platform scheme app site
    page dashboard overview queue browse another other one some data about can could would do does
    compare with and vs versus against two
    """.split()
) | _TITLES


def norm(s: str | None) -> str:
    """Lower-case ASCII words, without (SC)/(ST), tenure tags like (2022-28),
    or leading titles (Shri, Smt, Dr, Adv ...). 'DR.K.SUDHAKAR' -> 'k sudhakar',
    'BASTAR(ST)' -> 'bastar'."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    toks = _NON_ALNUM.sub(" ", _PAREN.sub(" ", s)).split()
    while toks and toks[0] in _TITLES:
        toks.pop(0)
    return " ".join(toks)


def _message_tokens(message: str) -> list[str]:
    s = unicodedata.normalize("NFKD", message or "").encode("ascii", "ignore").decode().lower()
    return [t for t in _NON_ALNUM.sub(" ", _PAREN.sub(" ", s)).split() if t not in _TITLES]


# ---- the name index -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Entity:
    kind: str  # "constituency" | "mp"
    name: str  # as stored: used for the profile query and the links
    state: str  # the constituency's state; "" for an MP
    key: str  # norm(name)

    @property
    def label(self) -> str:
        return f"{self.name} ({self.state})" if self.state else self.name


def build_index(mps: list[str], constituencies: list[dict]) -> list[Entity]:
    """From /api/mps and /api/constituencies-shaped lists. Constituencies first."""
    out = [
        Entity("constituency", c["Constituency"], c.get("State") or "", norm(c["Constituency"]))
        for c in constituencies
        if c.get("Constituency")
    ]
    out += [Entity("mp", m, "", norm(m)) for m in mps if m]
    return [e for e in out if e.key]


@dataclass
class Match:
    how: str  # "direct" | "choices" | "none"
    entities: list[Entity]
    candidate: str = ""
    more: int = 0  # choices beyond MAX_CHOICES


def _contains_phrase(hay: str, needle: str) -> bool:
    return bool(needle) and f" {needle} " in f" {hay} "


def _keep_longest(found: list[Entity]) -> list[Entity]:
    """'north east delhi' also contains 'east delhi': keep only the longest."""
    return [a for a in found if not any(b.key != a.key and _contains_phrase(b.key, a.key) for b in found)]


def _dedupe(found: list[Entity]) -> list[Entity]:
    seen, out = set(), []
    for e in found:
        if (e.kind, e.name, e.state) not in seen:
            seen.add((e.kind, e.name, e.state))
            out.append(e)
    return out


def _stored_name_hits(message: str, index: list[Entity]) -> list[Entity]:
    """The stored name written out in full (what every chip sends)."""
    low = (message or "").lower()
    hits = []
    for e in index:
        n = e.name.lower().strip()
        i = low.find(n)
        while n and i >= 0:
            before = low[i - 1] if i > 0 else " "
            after = low[i + len(n)] if i + len(n) < len(low) else " "
            if not before.isalnum() and not after.isalnum():
                hits.append(e)
                break
            i = low.find(n, i + 1)
    if not hits:
        return []
    longest = max(len(e.name) for e in hits)
    return [e for e in hits if len(e.name) == longest]


def resolve(message: str, index: list[Entity]) -> Match:
    """Stored name in full -> normalised exact phrase -> word containment ->
    fuzzy spelling (choices only)."""
    hits = _stored_name_hits(message, index)
    if not hits:
        msg = " ".join(_message_tokens(message))
        hits = _keep_longest([e for e in index if _contains_phrase(msg, e.key)])
    hits = _dedupe(hits)
    if hits:
        # Same stored name in two states is still one name for the profile query.
        if len({(e.kind, e.name) for e in hits}) == 1:
            return Match("direct", hits[:1] if hits[0].kind == "mp" else hits)
        return Match("choices", hits[:MAX_CHOICES], more=max(0, len(hits) - MAX_CHOICES))

    cand_toks = [t for t in _message_tokens(message) if t not in _FILLER]
    candidate = " ".join(cand_toks)
    if len(candidate.replace(" ", "")) < 3:
        return Match("none", [], candidate)

    want = set(cand_toks)
    contained = _dedupe([e for e in index if want <= set(e.key.split())])
    if len(contained) == 1:
        return Match("direct", contained, candidate)
    if contained:
        def closeness(e: Entity):
            return (-difflib.SequenceMatcher(None, candidate, e.key).ratio(), e.key)

        ranked = sorted(contained, key=closeness)
        return Match("choices", ranked[:MAX_CHOICES], candidate, max(0, len(ranked) - MAX_CHOICES))

    if len(candidate) >= 4:
        by_key: dict[str, list[Entity]] = {}
        for e in index:
            by_key.setdefault(e.key, []).append(e)
        close = difflib.get_close_matches(candidate, list(by_key), n=MAX_CHOICES, cutoff=FUZZY_CUTOFF)
        fuzzy = _dedupe([e for k in close for e in by_key[k]])
        if fuzzy:
            return Match("choices", fuzzy[:MAX_CHOICES], candidate)
    return Match("none", [], candidate)


# ---- when to try a lookup -----------------------------------------------------------------------

_CUE = re.compile(
    r"\b(tell me about|about|who is|whos|who s|my mp|our mp|mp of|mp for|works? (in|of|for|at|from)"
    r"|show|list|constituency|profile|details|i live in|live in|i am from|im from|representative)\b"
)
_STRONG_CUE = re.compile(
    r"\b(tell me about|who is|whos|who s|my mp|our mp|mp of|mp for|works? (in|of|for|at|from)"
    r"|constituency|i live in|i am from|im from)\b"
)
_ASK_WHERE = re.compile(r"\b((my|our) (mp|constituency|representative)|who is (the )?mp)\b")
_SHOW_MORE = re.compile(r"\b(show more|more works)\b")
_ANOTHER = re.compile(r"^(another|other|a different|some other)\b")
_COMPARE = re.compile(r"\b(compare|vs|versus)\b")
_SEPARATORS = (" with ", " vs. ", " vs ", " versus ", " against ", " and ", " to ")
SHORT_MESSAGE_WORDS = 5


def _gated(msg: str) -> bool:
    return bool(_CUE.search(msg)) or len(msg.split()) <= SHORT_MESSAGE_WORDS


def _examples(index: list[Entity], n: int = 3) -> list[Entity]:
    """Spread-out examples from the live constituency list (never hard-coded)."""
    cons = sorted((e for e in index if e.kind == "constituency"), key=lambda e: e.key)
    if not cons:
        return []
    picks = [cons[(i + 1) * len(cons) // (n + 1)] for i in range(n)]
    return _dedupe(picks)


# ---- reply builders (pure: no DB) ----------------------------------------------------------------

_STAGE = {"RECOMMENDED": "Recommended", "SANCTIONED": "Sanctioned", "COMPLETED": "Completed"}
_HOUSE = {"LS": "Lok Sabha", "RS": "Rajya Sabha"}


def _inr(v) -> str:
    return "₹" + (labels.rupees(v) or "0")


def _short(text: str | None) -> str:
    t = " ".join((text or "").split())
    if not t:
        return "(no description)"
    if len(t) <= TITLE_CHARS:
        return t
    return t[:TITLE_CHARS].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def _browse(kind: str, name: str) -> str:
    return f"/mp-performance?{'constituency' if kind == 'constituency' else 'mp'}={quote(name, safe='')}"


def _queue(param: str, value: str) -> str:
    return f"/queue?{param}={quote(value, safe='')}"


def exposure(prof: dict) -> float:
    """Financial exposure as the app defines it: the total amount of HIGH + CRITICAL works."""
    abp = prof.get("amount_by_priority") or {}
    return float(sum((abp.get(t) or {}).get("total", 0.0) for t in ("CRITICAL", "HIGH")))


def _tier_line(prof: dict) -> str:
    return (
        f"CRITICAL {prof.get('critical_count', 0)} · HIGH {prof.get('high_priority_count', 0)} · "
        f"MODERATE {prof.get('review_recommended_count', 0)} · LOW {prof.get('low_risk_count', 0)}"
    )


def _work_lines(works: list[dict], start: int) -> list[str]:
    return [
        f"{start + i}. {_short(w.get('description'))} — {_STAGE.get(w.get('stage'), w.get('stage') or '')}"
        f" · {w.get('priority') or w.get('risk_level')} · {_inr(w.get('amount'))}"
        for i, w in enumerate(works)
    ]


def _name(prof: dict, kind: str) -> str:
    return prof["constituency_name"] if kind == "constituency" else prof["mp_name"]


def _links(prof: dict, kind: str) -> list[dict]:
    name = _name(prof, kind)
    links = [{"label": f"Browse: {name}", "href": _browse(kind, name)}]
    if kind == "constituency":
        for mp in (prof.get("mps") or [])[:2]:
            links.append({"label": f"Priority Queue: {mp}", "href": _queue("mp", mp)})
    else:
        links.append({"label": f"Priority Queue: {name}", "href": _queue("mp", name)})
        if prof.get("house") == "RS" and prof.get("state"):
            state = prof["state"]
            links.append({"label": f"All works in {state} (Priority Queue)", "href": _queue("state", state)})
    return links


def _identity_lines(prof: dict, kind: str) -> list[str]:
    if kind == "constituency":
        mps = prof.get("mps") or []
        state = f" ({prof['state']})" if prof.get("state") else ""
        return [
            f"{prof['constituency_name']}{state} — Lok Sabha constituency.",
            f"MP: {', '.join(mps)}." if mps else "MP: not recorded in the current data.",
        ]
    house = prof.get("house")
    state = prof.get("state") or ""
    if house == "RS":
        return [
            f"{prof['mp_name']} is a Rajya Sabha member" + (f" for {state}." if state else "."),
            "Rajya Sabha members represent a state, not a constituency, so there is no constituency view "
            "for them; the state-level view is linked below.",
        ]
    if house == "LS":
        cons = prof.get("constituency") or ""
        where = f"{cons} ({state})" if cons and state else cons or state
        return [f"{prof['mp_name']} — Lok Sabha MP" + (f" for {where}." if where else ".")]
    return [f"{prof['mp_name']} — works recorded under both Houses" + (f" ({state})." if state else ".")]


def profile_reply(prof: dict, kind: str, offset: int = 0) -> dict:
    """The reply for one matched constituency or MP. `prof` has already been
    through public_view()."""
    name = _name(prof, kind)
    top = prof.get("top_flagged_works") or []
    scored = prof.get("scored_works", 0)
    lines: list[str] = []
    suggestions: list[dict] = []
    other = "constituency" if kind == "constituency" else "MP"

    if offset:
        more = top[offset : offset + TOP_WORKS]
        if more:
            lines.append(f"More priority works for {name}:")
            lines += _work_lines(more, offset + 1)
        else:
            lines.append(f"There are no more scored works to list here for {name}.")
        lines.append("For the full list, open the Priority Queue link below.")
    else:
        lines += _identity_lines(prof, kind)
        lines.append(
            f"Works on record: {prof.get('total_works', 0)} ({scored} scored for investigation priority; "
            "recommended-only works are not scored)."
        )
        if scored:
            lines.append(f"Investigation priority: {_tier_line(prof)}.")
            lines.append(
                f"Financial exposure (total amount of HIGH + CRITICAL works): {_inr(exposure(prof))}."
            )
            lines.append("Top priority works:")
            lines += _work_lines(top[:TOP_WORKS], 1)
        else:
            lines.append("None of these works is scored yet, so there is no investigation priority to show.")
        if len(top) > TOP_WORKS:
            suggestions.append({"label": "Show more works", "message": f"Show more works for {name}"})
        if top:
            explain = f"Explain work {top[0]['record_id']}"
            suggestions.append({"label": "Explain the top work", "message": explain})
    suggestions.append(
        {"label": f"Compare with another {other}", "message": f"Compare {name} with another {other}"}
    )
    as_of = (prof.get("served") or {}).get("data_as_of")
    if as_of and not offset:
        lines.append(f"Data as of {as_of}.")
    lines.append(PRIORITY_NOTE)
    return {"reply": "\n".join(lines), "links": _links(prof, kind), "suggestions": suggestions}


def choices_reply(m: Match, compare_with: Entity | None = None) -> dict:
    head = "Did you mean one of these?"
    if m.how == "choices" and m.candidate and not m.more:
        head = f'Did you mean one of these for "{m.candidate[:60]}"?'
    lines = [head]
    if m.more:
        noun = "match" if m.more == 1 else "matches"
        lines.append(f"({m.more} more {noun}; type more of the name to narrow it down.)")

    def msg(e: Entity) -> str:
        return f"Compare {compare_with.name} with {e.name}" if compare_with else f"Tell me about {e.name}"

    return {
        "reply": "\n".join(lines),
        "links": [],
        "suggestions": [{"label": e.label, "message": msg(e)} for e in m.entities[:MAX_CHOICES]],
    }


def no_match_reply(candidate: str, index: list[Entity]) -> dict:
    ex = _examples(index)
    lines = [f'I couldn\'t find a constituency or MP matching "{candidate[:60]}" in the current data.']
    if ex:
        lines.append("Try a Lok Sabha constituency name, for example: " + ", ".join(e.name for e in ex) + ".")
    lines.append("Rajya Sabha members can be looked up by name.")
    return {
        "reply": "\n".join(lines),
        "links": [],
        "suggestions": [{"label": e.name, "message": f"Tell me about {e.name}"} for e in ex],
    }


def ask_where_reply(index: list[Entity]) -> dict:
    ex = _examples(index)
    lines = ["Tell me your Lok Sabha constituency and I'll show its MP and their works."]
    if ex:
        lines.append("For example: " + ", ".join(e.name for e in ex) + ".")
    lines.append(
        "Rajya Sabha members represent a whole state rather than a constituency; ask for them by name."
    )
    return {
        "reply": "\n".join(lines),
        "links": [],
        "suggestions": [{"label": e.name, "message": f"Tell me about {e.name}"} for e in ex],
    }


def compare_prompt_reply(e: Entity) -> dict:
    other = "constituency" if e.kind == "constituency" else "MP"
    return {
        "reply": f"Which {other} should I compare {e.name} with? Type its name.",
        "links": [],
        "suggestions": [],
        "context": {"compare_with": {"kind": e.kind, "name": e.name}},
    }


def comparison_reply(cmp: dict, kind: str) -> dict:
    lines = ["Side by side:"]
    links = []
    for item in cmp.get("comparison", []):
        name = item.get("name", "")
        if item.get("error"):
            lines.append(f"• {name}: no records found.")
            continue
        lines.append(
            f"• {name}: {item.get('total_works', 0)} works, {item.get('scored_works', 0)} scored; "
            f"{_tier_line(item)}; financial exposure {_inr(exposure(item))}."
        )
        links.append({"label": f"Browse: {name}", "href": _browse(kind, name)})
    lines.append("For charts side by side, open Browse MP / Constituency and use its Compare section.")
    lines.append(PRIORITY_NOTE)
    return {"reply": "\n".join(lines), "links": links, "suggestions": []}


MIXED_COMPARE = "I can compare two constituencies or two MPs, but not a constituency with an MP."


# ---- the DB side ---------------------------------------------------------------------------------


def _index(db: Session, s) -> list[Entity]:
    return service._cached(
        s.cache_key("chat_names"),
        lambda: build_index(service.distinct(db, s, "mp"), service.constituencies(db, s)),
    )


def _profile(db: Session, s, e: Entity, authenticated: bool) -> dict | None:
    fn = service.constituency_profile if e.kind == "constituency" else service.mp_profile
    prof = fn(db, s, e.name)
    return None if prof is None else public_view(prof, authenticated)


def _compare(db: Session, s, a: Entity, b: Entity) -> dict:
    if a.kind != b.kind:
        return {"reply": MIXED_COMPARE, "links": [], "suggestions": []}
    kind = "Constituency" if a.kind == "constituency" else "MP"
    return comparison_reply(service.comparison(db, s, [a.name, b.name], kind), a.kind)


def _context_entity(context, index: list[Entity]) -> Entity | None:
    """The request's compare_with, only if it names a stored entity of that kind."""
    ref = getattr(context, "compare_with", None) if context is not None else None
    if ref is None:
        return None
    return next((e for e in index if e.kind == ref.kind and e.name == ref.name), None)


def _split_compare(message: str, index: list[Entity]):
    """'compare A with B' / 'A vs B': every separator position is tried, so a
    name containing 'and' still splits correctly. Returns (a, b_text) with a
    resolved directly, or None."""
    body = re.sub(r"^\s*compare\s+", "", message.strip(), flags=re.I).rstrip(" ?.!")
    low = body.lower()
    for sep in _SEPARATORS:
        start = 0
        while (i := low.find(sep, start)) >= 0:
            left, right = body[:i], body[i + len(sep) :]
            a = resolve(left, index)
            if a.how == "direct":
                return a.entities[0], right
            start = i + 1
    return None


def answer(db: Session, principal, message: str, context, kb_keywords: list[str], claim_keywords: list[str]):
    """A lookup reply dict, or None to let the existing copilot layers answer."""
    low = message.lower()
    if any(k in low for k in claim_keywords):
        return None
    msg = " ".join(_message_tokens(message))
    if not msg:
        return None
    has_context = context is not None and getattr(context, "compare_with", None) is not None
    compare = bool(_COMPARE.search(msg))
    if not (has_context or compare or _gated(msg)):
        return None
    strong = bool(_STRONG_CUE.search(msg)) or bool(_ASK_WHERE.search(msg))

    try:
        s = service.served(db, principal.scope)
        index = _index(db, s)
        if not index:
            return {"reply": NOT_AVAILABLE, "links": [], "suggestions": []} if strong else None

        if compare:
            split = _split_compare(message, index)
            if split:
                a, rest = split
                if _ANOTHER.match(rest.strip().lower()) or not rest.strip():
                    return compare_prompt_reply(a)
                b = resolve(rest, index)
                if b.how == "direct":
                    return _compare(db, s, a, b.entities[0])
                if b.how == "choices":
                    return choices_reply(b, compare_with=a)

        ctx = _context_entity(context, index)
        if ctx is not None:
            m = resolve(message, index)
            if m.how == "direct" and m.entities[0].kind == ctx.kind and m.entities[0].name != ctx.name:
                return _compare(db, s, ctx, m.entities[0])
            if m.how == "choices":
                return choices_reply(m, compare_with=ctx)

        m = resolve(message, index)
        if m.how == "direct":
            e = m.entities[0]
            prof = _profile(db, s, e, principal.authenticated)
            if prof is None:
                return no_match_reply(e.name, index)
            out = profile_reply(prof, e.kind, TOP_WORKS if _SHOW_MORE.search(msg) else 0)
            if len({x.state for x in m.entities}) > 1:
                states = ", ".join(sorted({x.state for x in m.entities if x.state}))
                out["reply"] = (
                    f"Note: more than one constituency is called {e.name} ({states}); "
                    "these figures combine them, as the Browse page does.\n" + out["reply"]
                )
            return out
        if m.how == "choices":
            return choices_reply(m)
        if any(k in low for k in kb_keywords):
            return None
        if not m.candidate:
            return ask_where_reply(index) if _ASK_WHERE.search(msg) else None
        return no_match_reply(m.candidate, index) if strong else None
    except SQLAlchemyError:
        return {"reply": LOAD_FAILED, "links": [], "suggestions": []} if strong or compare else None

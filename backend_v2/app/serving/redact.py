"""
Masking of personal numbers in public output (owner decisions, pre-Phase 14
and Phase 13.y): phone numbers become PHONE_MASK, 12-digit Aadhaar-shaped
numbers become ID_MASK.

Portal work descriptions sometimes carry a contact's phone number --
beneficiaries, pradhans, "sampark sutra" -- and occasionally an Aadhaar
number, next to disability details. The stored source text
(work.raw_description, raw_row, work.description_normalized) is never
changed, and nothing feeding risk_result reads this module. Masking is
applied ONLY where text leaves the system, always through mask_personal():
  * the read models the API serves: served_work.description/search_text
    (app/serving/build.py) and map_work.description/search_text
    (app/geo/build.py) -- so a search by the digits finds nothing;
  * API serialization of descriptions and case notes, as a second line of
    defence against a build made before masking existed;
  * the copilot's message and history before anything is sent to Gemini;
  * the case export and audit trail (the stored note stays as written).

Phone numbers (PHONE_MASK):
  1. Indian mobiles: 10 digits starting 6-9 -- contiguous, or split 5-5,
     3-3-4 or 4-3-3 by single spaces/hyphens -- with an optional prefix:
     +91, (+91), 0091, 91 (with or without a separator) or 0.
  2. STD-code landlines, 11 digits in all: 0 + a 2-4 digit STD code, then a
     space or hyphen, or the code in brackets -- 0522-2345678,
     0522 2345678, (0522) 2345678, (0522)2345678 -- and the +91 form with
     the 0 dropped (+91-522-2345678). Further 7-8 digit numbers of the same
     exchange listed right after it ("(0522)2345678, 2345679") are masked too.
  3. A mistyped mobile directly after a phone word ("mobile no", "mob",
     "mo", "contact no", "cont no", "ph", "tel", "sampark sutra"): an 11- or
     12-digit run starting 6-9 or 0.
  4. Any 11-digit run starting 6-9 (a mistyped mobile), two mobiles glued
     into one 20-digit run, and an unseparated 0 + 10-digit landline when a
     contact word precedes it within 40 characters (a line break allowed:
     "Contact-Sh. <name> ji\\n0##########").
Aadhaar-shaped numbers (ID_MASK): 12 digits starting 2-9 (UIDAI never issues
a number starting 0 or 1), contiguous or grouped 4-4-4 by the same single
space or hyphen, not part of a longer digit group. A 12-digit run that is 91
+ a mobile is masked as a phone (rule 1) first.

Deliberately NOT masked (tests pin each): amounts (with or without Indian
commas), work/sanction IDs, 6-digit pincodes, years and dates, 11-digit
school (UDISE) codes, letter numbers like 1xx-xxxxx-xxxx and 9x-xxxx-xxxx,
16-digit grouped property numbers, numbers starting 1-5, road chainages and
decimal fractions (0.7123456789). A digit run inside a longer digit run is
never matched.
"""

from __future__ import annotations

import re

PHONE_MASK = "[phone removed]"
ID_MASK = "[id removed]"

# Not glued to other digits, and not the fractional part of a decimal.
_START = r"(?<![0-9])(?<![0-9]\.)"
_END = r"(?![0-9])(?!\.[0-9])"

_MOBILE = re.compile(
    _START
    + r"(?:\(\s*\+?91\s*\)\s?|\+91[\s-]?|(?<![0-9])(?:00)?91[\s-]?|0[\s-]?)?"
    + r"(?:[6-9][0-9]{9}|[6-9][0-9]{4}[\s-][0-9]{5}|[6-9][0-9]{2}[\s-][0-9]{3}[\s-][0-9]{4}"
    + r"|[6-9][0-9]{3}[\s-][0-9]{3}[\s-][0-9]{3})"
    + _END
)
# Continuation: more 7-8 digit numbers of the same exchange right after a landline.
_MORE = r"(?P<more>(?:\s*[,/]\s*[2-9][0-9]{6,7}(?![0-9])(?!\.[0-9]))*)"
_LANDLINE = re.compile(
    _START
    + r"(?P<main>\(\s*0[0-9]{2,4}\s*\)\s*-?\s*[0-9]{6,8}|0[0-9]{2,4}[\s-][0-9]{6,8}"
    + r"|(?:\+91|\(\s*\+91\s*\))[\s-]?[1-9][0-9]{1,3}[\s-][0-9]{6,8})"
    + _END
    + _MORE
)
_KEYWORD_TYPO = re.compile(
    r"(?i)\b(mobile|mob|mo|contact|cont|ph|phone|tel|telephone|sampark\s+sutra)\b"
    r"(\s*(no|nos|number)\b)?[\s.:#\-]*" + _START + r"[06-9][0-9]{10,11}" + _END
)
# 4. An 11-digit run starting 6-9: a mistyped mobile. (School/UDISE codes are
#    11 digits too, but begin with a 0-3 state code, so they never match.)
_ELEVEN = re.compile(_START + r"[6-9][0-9]{10}" + _END)
# 5. Two mobiles written together with no separator (exactly 20 digits).
_GLUED = re.compile(_START + r"[6-9][0-9]{9}[6-9][0-9]{9}" + _END)
# 6. A landline written without a separator (0 + 10 digits) when a contact
#    word appears up to 40 characters before it.
_CONTACT_LANDLINE = re.compile(
    r"(?i)(\b(contact|cont|mobile|mob|mo|ph|phone|tel|telephone|sampark\s+sutra)\b[^0-9]{0,40})"
    + _START
    + r"0[0-9]{10}"
    + _END
)
# Aadhaar-shaped: 12 digits starting 2-9, plain or grouped 4-4-4 by one repeated
# separator; never a slice of a longer digit group ("1234 5678 9012 3456").
_AADHAAR = re.compile(
    _START + r"(?<![0-9][\s-])[2-9][0-9]{3}(?P<sep>[ -]?)[0-9]{4}(?P=sep)[0-9]{4}" + _END + r"(?![\s-][0-9])"
)


def _digits(s: str) -> int:
    return len(re.sub(r"\D", "", s))


def _keyword(m: re.Match) -> str:
    whole = m.group(0)
    digits = re.search(r"[06-9][0-9]{10,11}$", whole)
    return whole[: digits.start()] + PHONE_MASK


def _mask(text: str | None, ids: bool) -> tuple[str | None, int, int]:
    if not text:
        return text, 0, 0
    phones = 0

    def count(repl):
        def inner(m):
            nonlocal phones
            out = repl(m) if callable(repl) else repl
            if out != m.group(0):
                phones += out.count(PHONE_MASK) - m.group(0).count(PHONE_MASK)
            return out

        return inner

    def landline(m: re.Match) -> str:
        main = m.group("main")
        if _digits(main) != (12 if "+91" in main else 11):
            return m.group(0)
        return PHONE_MASK + re.sub(r"[0-9]{7,8}", PHONE_MASK, m.group("more"))

    text = _KEYWORD_TYPO.sub(count(_keyword), text)
    text = _GLUED.sub(count(f"{PHONE_MASK} {PHONE_MASK}"), text)
    text = _MOBILE.sub(count(PHONE_MASK), text)
    text = _ELEVEN.sub(count(PHONE_MASK), text)
    text = _CONTACT_LANDLINE.sub(count(lambda m: m.group(1) + PHONE_MASK), text)
    text = _LANDLINE.sub(count(landline), text)
    n_ids = 0
    if ids:
        text, n_ids = _AADHAAR.subn(ID_MASK, text)
    return text, phones, n_ids


def mask_personal_counted(text: str | None) -> tuple[str | None, int, int]:
    """(masked text, phone numbers masked, Aadhaar-shaped numbers masked)."""
    return _mask(text, ids=True)


def mask_personal(text: str | None) -> str | None:
    """The one function every output surface uses: phones, then Aadhaar-shaped numbers."""
    return _mask(text, ids=True)[0]


def mask_phones_counted(text: str | None) -> tuple[str | None, int]:
    """Phones only (the phone-rule tests and reports use it)."""
    out, phones, _ = _mask(text, ids=False)
    return out, phones


def mask_phones(text: str | None) -> str | None:
    return _mask(text, ids=False)[0]

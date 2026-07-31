"""``healthcare.phi`` — a regex-and-wordlist PHI redactor.

**Honesty first:** this is pattern-matching de-identification, not a
certified HIPAA Safe Harbor implementation. Safe Harbor (45 CFR
164.514(b)(2)) requires removing 18 categories of identifiers with a
rigor this module does not claim — no clinical NLP model, no manual
review, no legal sign-off. Treat ``PHIRedactor`` as a solid first pass
that catches the identifiers it is built to catch (measured, published
precision/recall in ``healthcare/README.md``), and nothing more. It will
miss unusual name spellings, unconventional date formats, and anything
outside its regex/wordlist coverage. Do not deploy it as your only
de-identification control in a regulated pipeline.

Design
------
``PHIRedactor.redact(text)`` runs an ordered set of category matchers over
the input, resolves overlaps (higher-priority / longer matches win),
and replaces each accepted span with a category-stable token:
``[NAME-1]``, ``[NAME-2]``, ``[DATE-1]``, ``[MRN-1]`` ... The same
original substring always maps to the same token *within one call* to
``redact`` (a fresh call starts renumbering from 1 — statelessness across
calls is deliberate; nothing here should function as a persistent
per-patient pseudonym registry).

Coverage of the 18 HIPAA identifier categories (category name used in
:class:`~agenticcarekit.kernel.contracts.Redaction` in parentheses):

1. Names (``NAME``) — honorific + capitalized name, "my name is X" /
   "this is X calling" context, ``Name:`` header context, and a curated
   first/last name wordlist match for bare ``Firstname Lastname`` pairs.
2. Geographic subdivisions smaller than state (``ADDRESS``) — street
   addresses, "city, ST zip" combos, bare ZIP codes. State names alone
   are deliberately *not* matched.
3. Dates except year, incl. DOB/admission (``DATE``), and ages over 89
   (``AGE``) — HIPAA groups ages >89 under the same "all elements of
   dates" category; kept as a distinct token category here for a more
   informative trace, but conceptually the same rule. A bare 4-digit
   year with no day/month is deliberately *not* matched.
4. Telephone numbers (``PHONE``)
5. Fax numbers (``FAX``)
6. Email addresses (``EMAIL``)
7. Social Security numbers (``SSN``)
8. Medical record numbers (``MRN``)
9. Health plan beneficiary numbers (``HEALTH_PLAN``)
10. Account numbers (``ACCOUNT``)
11. Certificate/license numbers (``CERTIFICATE``)
12. Vehicle identifiers incl. license plates (``VEHICLE``)
13. Device identifiers and serial numbers (``DEVICE``)
14. URLs (``URL``)
15. IP addresses (``IP``)
16. Biometric identifier mentions (``BIOMETRIC``) — text mentions of a
    biometric identifier value (e.g. "fingerprint ID FP-4471"); this
    module processes text, not images, so it cannot detect an embedded
    fingerprint scan itself, only a textual reference to one.
17. Full-face photo references (``PHOTO``) — textual mentions of an
    attached/referenced photo (e.g. "full-face photo attached"); same
    caveat as biometrics, this is text redaction, not image redaction.
18. Other unique identifying numbers (``OTHER_ID``) — reference/tracking/
    confirmation numbers not covered by a more specific category above.

Spans in the returned :class:`Redaction` list are always against the
**original** text, sorted by start offset.

Example:
    >>> r = PHIRedactor()
    >>> clean, reds = r.redact("Patient Jane Doe, MRN 482910, called about a refill.")
    >>> "Jane Doe" in clean
    False
    >>> sorted({red.category for red in reds})
    ['MRN', 'NAME']
    >>> clean.split(", ")[1]
    'MRN [MRN-1]'

    Same input redacted twice produces the same tokens (category-stable
    within a call):

    >>> clean2, reds2 = r.redact("Call Jane Doe and Jane Doe again.")
    >>> clean2
    'Call [NAME-1] and [NAME-1] again.'

    A bare year is not treated as PHI:

    >>> clean3, reds3 = r.redact("The clinic opened in 2010.")
    >>> reds3
    []
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agenticcarekit.kernel.contracts import Redaction

from .data.names import EPONYM_CONDITIONS, FIRST_NAMES, LAST_NAMES
from .data.places import STREET_NAMES, STREET_SUFFIXES

__all__ = ["PHIRedactor"]

# Priority order — earlier categories win overlapping spans. Roughly most
# structurally-distinctive / least ambiguous first.
_CATEGORY_PRIORITY: list[str] = [
    "EMAIL", "URL", "IP", "SSN", "MRN", "HEALTH_PLAN", "ACCOUNT",
    "CERTIFICATE", "VEHICLE", "DEVICE", "FAX", "PHONE", "DATE", "AGE",
    "ADDRESS", "BIOMETRIC", "PHOTO", "NAME", "OTHER_ID",
]
_PRIORITY_INDEX = {cat: i for i, cat in enumerate(_CATEGORY_PRIORITY)}


@dataclass
class _Candidate:
    start: int
    end: int
    category: str
    key: str  # de-duplication / token-stability key (usually matched text)


_MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

_HONORIFIC = r"(?:Mr|Mrs|Ms|Mx|Dr|Miss)\.?"

# Word-boundary alternations built from the curated lists.
_FIRST_ALT = "|".join(sorted((re.escape(n) for n in FIRST_NAMES), key=len, reverse=True))
_LAST_ALT = "|".join(sorted((re.escape(n) for n in LAST_NAMES), key=len, reverse=True))
_STREET_ALT = "|".join(sorted((re.escape(n) for n in STREET_NAMES), key=len, reverse=True))
_SUFFIX_ALT = "|".join(sorted((re.escape(n) for n in STREET_SUFFIXES), key=len, reverse=True))
_EPONYM_ALT = "|".join(sorted((re.escape(n) for n in EPONYM_CONDITIONS), key=len, reverse=True))

_CAP_WORD = r"[A-Z][a-zA-Z'\-]+"


def _find_dates(text: str) -> list[_Candidate]:
    out: list[_Candidate] = []
    # Numeric dates with day+month+year: 03/15/1985, 3-15-85, 2024-05-01
    for m in re.finditer(r"\b\d{4}-\d{1,2}-\d{1,2}\b", text):
        out.append(_Candidate(m.start(), m.end(), "DATE", m.group()))
    for m in re.finditer(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", text):
        out.append(_Candidate(m.start(), m.end(), "DATE", m.group()))
    # Month DD, YYYY  /  Month DD  (day+month, year optional but still a date)
    for m in re.finditer(_MONTHS + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}\b", text):
        out.append(_Candidate(m.start(), m.end(), "DATE", m.group()))
    # DD Month YYYY
    for m in re.finditer(r"\b\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTHS + r"\.?,?\s*\d{4}\b", text):
        out.append(_Candidate(m.start(), m.end(), "DATE", m.group()))
    return out


def _find_ages(text: str) -> list[_Candidate]:
    out: list[_Candidate] = []
    for m in re.finditer(r"\b(9[0-9]|1[0-9]{2})(?=\s*(?:years?[\s-]old|y\.?o\.?\b|-year-old))", text):
        out.append(_Candidate(m.start(), m.end(), "AGE", m.group()))
    for m in re.finditer(r"\bage(?:d)?\s+(9[0-9]|1[0-9]{2})\b", text, re.IGNORECASE):
        span_start = m.start(1)
        out.append(_Candidate(span_start, m.end(1), "AGE", m.group(1)))
    return out


def _find_context_number(
    text: str, label_core: str, category: str, exclude_near: str | None = None
) -> list[_Candidate]:
    """Match ``<label core> [number/id/no/#] [is/was/of] <value>`` and
    redact only the value (keeping the label words for readability).

    ``label_core`` is just the concept word(s) (e.g. ``"MRN"`` or
    ``"account"``) — the qualifier word ("number", "id", "no", "#") and
    connective word ("is", "was", "of") are handled generically so the
    label regex doesn't have to enumerate every phrasing.

    The captured value must contain at least one digit. Real identifiers
    (MRNs, account numbers, plate numbers, ...) always do; this is what
    stops a plain sentence like "the account was closed" from
    false-positiving on the word "closed".
    """
    out: list[_Candidate] = []
    pattern = (
        r"\b(?:"
        + label_core
        + r")\b(?:\s*(?:number|no\.?|#|id))?(?:\s*(?:is|was|of))?\s*[:#-]?\s*"
        r"([A-Za-z0-9][A-Za-z0-9\-]{2,})"
    )
    for m in re.finditer(pattern, text, re.IGNORECASE):
        value = m.group(1)
        if not re.search(r"\d", value):
            continue
        if exclude_near and re.search(exclude_near, m.group(0), re.IGNORECASE):
            continue
        out.append(_Candidate(m.start(1), m.end(1), category, value))
    return out


def _find_email(text: str) -> list[_Candidate]:
    return [
        _Candidate(m.start(), m.end(), "EMAIL", m.group())
        for m in re.finditer(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", text)
    ]


def _find_url(text: str) -> list[_Candidate]:
    out = []
    for m in re.finditer(r"\bhttps?://\S+\b", text):
        out.append(_Candidate(m.start(), m.end(), "URL", m.group()))
    for m in re.finditer(r"\bwww\.[\w.-]+\.\w{2,}\S*\b", text):
        out.append(_Candidate(m.start(), m.end(), "URL", m.group()))
    return out


def _find_ip(text: str) -> list[_Candidate]:
    return [
        _Candidate(m.start(), m.end(), "IP", m.group())
        for m in re.finditer(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    ]


def _find_ssn(text: str) -> list[_Candidate]:
    return [
        _Candidate(m.start(), m.end(), "SSN", m.group())
        for m in re.finditer(r"\b\d{3}-\d{2}-\d{4}\b", text)
    ]


def _find_fax_phone(text: str) -> list[_Candidate]:
    out: list[_Candidate] = []
    for m in re.finditer(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", text):
        window = text[max(0, m.start() - 20) : m.start()]
        category = "FAX" if re.search(r"\bfax\b", window, re.IGNORECASE) else "PHONE"
        out.append(_Candidate(m.start(), m.end(), category, m.group()))
    return out


def _find_address(text: str) -> list[_Candidate]:
    out: list[_Candidate] = []
    # Full street address: "123 Maple St"
    for m in re.finditer(r"\b\d{1,5}\s+(?:" + _STREET_ALT + r")\s+(?:" + _SUFFIX_ALT + r")\.?\b", text):
        out.append(_Candidate(m.start(), m.end(), "ADDRESS", m.group()))
    # "City, ST 12345" or "City, ST 12345-6789"
    for m in re.finditer(r"\b[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", text):
        out.append(_Candidate(m.start(), m.end(), "ADDRESS", m.group()))
    # Bare "ZIP: 12345" / "zip code 12345"
    for m in re.finditer(r"\bzip(?:\s*code)?[:\s]+(\d{5}(?:-\d{4})?)\b", text, re.IGNORECASE):
        out.append(_Candidate(m.start(1), m.end(1), "ADDRESS", m.group(1)))
    return out


def _find_device(text: str) -> list[_Candidate]:
    """Device identifiers/serial numbers. Deliberately requires an
    explicit qualifier phrase (``device serial``, ``device id``,
    ``serial number``) rather than bare ``device`` — otherwise ordinary
    sentences like "the device logged in from ..." false-positive."""
    out: list[_Candidate] = []
    pattern = (
        r"\b(?:device\s*(?:serial|id)|serial\s*number)\b"
        r"(?:\s*(?:is|was|of))?\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9\-]{2,})"
    )
    for m in re.finditer(pattern, text, re.IGNORECASE):
        value = m.group(1)
        if not re.search(r"\d", value):
            continue
        out.append(_Candidate(m.start(1), m.end(1), "DEVICE", value))
    return out


def _find_biometric(text: str) -> list[_Candidate]:
    out: list[_Candidate] = []
    for m in re.finditer(
        r"\b(?:fingerprint|retinal(?:\s*scan)?|voiceprint|iris\s*scan)\b"
        r"(?:\s*(?:id|#))?\s*(?:is|was|of)?\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9-]{2,})",
        text,
        re.IGNORECASE,
    ):
        value = m.group(1)
        if not re.search(r"\d", value):
            continue
        out.append(_Candidate(m.start(1), m.end(1), "BIOMETRIC", value))
    return out


def _find_photo(text: str) -> list[_Candidate]:
    out: list[_Candidate] = []
    for m in re.finditer(
        r"\b(?:full[\s-]face\s+photo(?:graph)?|photo(?:graph)?\s+of\s+the\s+patient|"
        r"headshot\s+on\s+file|patient\s+photo(?:graph)?\s+attached)\b",
        text,
        re.IGNORECASE,
    ):
        out.append(_Candidate(m.start(), m.end(), "PHOTO", m.group()))
    return out


def _find_names(text: str) -> list[_Candidate]:
    out: list[_Candidate] = []

    # 1) Honorific + capitalized name(s), excluding disease eponyms
    #    ("Dr. Alzheimer's disease" is a condition, not a person).
    pattern = _HONORIFIC + r"\s+(" + _CAP_WORD + r"(?:\s+" + _CAP_WORD + r"){0,2})"
    for m in re.finditer(pattern, text):
        candidate = m.group(1)
        tail = text[m.end() : m.end() + 12]
        if re.match(r"^'?s?\s*(?:disease|syndrome)\b", tail, re.IGNORECASE) and re.match(
            r"^(?:" + _EPONYM_ALT + r")", candidate, re.IGNORECASE
        ):
            continue
        out.append(_Candidate(m.start(1), m.end(1), "NAME", candidate))

    # 2) "Name:" / "Patient:" / bare "Patient <Name>" header context
    for m in re.finditer(
        r"(?:Patient\s+Name|Patient|Name)\s*:?\s+(" + _CAP_WORD + r"\s+" + _CAP_WORD + r")",
        text,
    ):
        out.append(_Candidate(m.start(1), m.end(1), "NAME", m.group(1)))

    # 3) "my name is X" / "this is X calling" / "this is X" spoken context
    for m in re.finditer(
        r"(?:my name is|this is)\s+(" + _CAP_WORD + r"(?:\s+" + _CAP_WORD + r")?)"
        r"(?=\s+calling|\s*[,.]|\s+and\b|\s*$)",
        text,
    ):
        out.append(_Candidate(m.start(1), m.end(1), "NAME", m.group(1)))

    # 4) Bare curated Firstname Lastname pairs (wordlist signal).
    for m in re.finditer(r"\b(" + _FIRST_ALT + r")\s+(" + _LAST_ALT + r")\b", text):
        out.append(_Candidate(m.start(), m.end(), "NAME", m.group()))

    return out


def _find_other_id(text: str) -> list[_Candidate]:
    return _find_context_number(text, r"reference|tracking|confirmation", "OTHER_ID")


class PHIRedactor:
    """Regex/wordlist redactor for the 18 HIPAA Safe Harbor identifier
    categories. See module docstring for coverage, honesty caveats, and
    the token-stability contract.

    Example:
        >>> red = PHIRedactor()
        >>> red.name
        'healthcare.phi'
        >>> clean, spans = red.redact("SSN 123-45-6789 on file.")
        >>> clean
        'SSN [SSN-1] on file.'
        >>> spans[0].category
        'SSN'
    """

    name = "healthcare.phi"

    def redact(self, text: str) -> tuple[str, list[Redaction]]:
        candidates: list[_Candidate] = []
        candidates += _find_email(text)
        candidates += _find_url(text)
        candidates += _find_ip(text)
        candidates += _find_ssn(text)
        candidates += _find_context_number(text, r"MRN", "MRN")
        candidates += _find_context_number(text, r"health\s*plan|beneficiary", "HEALTH_PLAN")
        candidates += _find_context_number(text, r"account", "ACCOUNT")
        candidates += _find_context_number(
            text, r"certificate|license", "CERTIFICATE", exclude_near=r"plate"
        )
        candidates += _find_context_number(text, r"VIN", "VEHICLE")
        candidates += _find_context_number(text, r"license\s*plate", "VEHICLE")
        candidates += _find_device(text)
        candidates += _find_fax_phone(text)
        candidates += _find_dates(text)
        candidates += _find_ages(text)
        candidates += _find_address(text)
        candidates += _find_biometric(text)
        candidates += _find_photo(text)
        candidates += _find_names(text)
        candidates += _find_other_id(text)

        accepted = _resolve_overlaps(candidates)
        accepted.sort(key=lambda c: c.start)

        token_for: dict[tuple[str, str], str] = {}
        counters: dict[str, int] = {}
        redactions: list[Redaction] = []
        pieces: list[str] = []
        cursor = 0
        for c in accepted:
            key = (c.category, c.key.lower())
            if key not in token_for:
                counters[c.category] = counters.get(c.category, 0) + 1
                token_for[key] = f"[{c.category}-{counters[c.category]}]"
            token = token_for[key]
            pieces.append(text[cursor : c.start])
            pieces.append(token)
            redactions.append(Redaction(category=c.category, start=c.start, end=c.end, replacement=token))
            cursor = c.end
        pieces.append(text[cursor:])
        return "".join(pieces), redactions


def _resolve_overlaps(candidates: list[_Candidate]) -> list[_Candidate]:
    ordered = sorted(
        candidates,
        key=lambda c: (_PRIORITY_INDEX.get(c.category, 99), -(c.end - c.start), c.start),
    )
    accepted: list[_Candidate] = []
    occupied: list[tuple[int, int]] = []
    for c in ordered:
        if any(c.start < e and s < c.end for s, e in occupied):
            continue
        accepted.append(c)
        occupied.append((c.start, c.end))
    return accepted

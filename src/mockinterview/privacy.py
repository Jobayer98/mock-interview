"""Redaction of personal details before text reaches a model (I4). Pattern-based, so it reduces exposure rather than guaranteeing none."""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
_PROFILE_LINK = re.compile(r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|facebook\.com|twitter\.com|x\.com)/[\w./%-]+", re.IGNORECASE)
_ADDRESS_PART = r"(?:House|Flat|Apartment|Apt|Road|Block|Sector|Holding)\s*(?:No\.?|#)?\s*[:\-]?\s*\d+[A-Za-z]?"
_ADDRESS = re.compile(rf"\b{_ADDRESS_PART}(?:\s*,\s*{_ADDRESS_PART})*", re.IGNORECASE)
_NAME_LINE = re.compile(r"^\s*((?:[A-Z][a-zA-Z.'-]+\s+){1,3}[A-Z][a-zA-Z.'-]+)")
_NOT_A_NAME = {"resume", "curriculum", "vitae", "cv", "profile", "summary", "software", "engineer", "developer", "contact", "education"}
MIN_PHONE_DIGITS = 10


def _redact_phones(text: str) -> tuple[str, int]:
    """Phones have at least 10 digits, so year ranges such as 2019 - 2023 are left alone."""
    count = 0

    def replace(match: re.Match) -> str:
        nonlocal count
        if sum(c.isdigit() for c in match.group(0)) < MIN_PHONE_DIGITS:
            return match.group(0)
        count += 1
        return "[phone]"

    return _PHONE.sub(replace, text), count


def redact_contact(text: str) -> tuple[str, dict[str, int]]:
    """Emails, phone numbers and profile links. Applied to candidate answers at intake, and to resumes."""
    text, emails = _EMAIL.subn("[email]", text)
    text, links = _PROFILE_LINK.subn("[profile-link]", text)
    text, phones = _redact_phones(text)
    return text, {"emails": emails, "phones": phones, "links": links}


def _candidate_name(text: str) -> str | None:
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    match = _NAME_LINE.match(first_line)
    if not match:
        return None
    name = match.group(1).strip()
    return None if {w.lower().strip(".") for w in name.split()} & _NOT_A_NAME else name


def redact_pii(text: str) -> tuple[str, dict[str, int]]:
    """Full resume redaction: contact details, street addresses, and the name on the first line wherever it recurs."""
    text, found = redact_contact(text)
    text, found["addresses"] = _ADDRESS.subn("[address]", text)
    name = _candidate_name(text)
    found["names"] = 0
    if name:
        text, found["names"] = re.subn(re.escape(name), "[name]", text, flags=re.IGNORECASE)
    return text, found

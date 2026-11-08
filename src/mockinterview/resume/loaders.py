"""Turn an uploaded resume into plain text that is safe to hand to extract_profile.

A resume is untrusted input (I3). Hidden text and lines written to instruct an AI are removed here, before any model sees them.
Detection is best-effort, so the data-block isolation and schema checks in resume/profile.py still apply.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
MAX_CHARS = 30_000
MIN_CHARS = 50

_ZERO_WIDTH = re.compile("[­​-‏‪-‮⁠-⁤﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_INSTRUCTION = re.compile(
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|your)\b[^.\n]{0,20}\b(instructions?|prompts?|rules?|directions?)\b"
    r"|\bsystem prompt\b"
    r"|\byou are (now )?an? (ai|assistant|language model|llm|grader|recruiter bot)\b"
    r"|\b(dear|note to( the)?) (ai|llm|chatbot|assistant|model|grader|recruiter bot)\b"
    r"|\b(rate|score|rank|grade|mark)\b[^.\n]{0,30}\b(this (candidate|applicant|resume|cv)|the (candidate|applicant))\b"
    r"|\b(give|award|assign)\b[^.\n]{0,30}\b(full marks|highest (score|rating|rank)|top (score|rating)|perfect score|10/10)\b"
    r"|\b(hire|recommend|shortlist|select) this (candidate|applicant)\b",
    re.IGNORECASE,
)


class ResumeError(ValueError):
    """The file cannot be used. The message is written for the candidate."""


def _strip_instructions(text: str) -> tuple[str, int]:
    """Drop sentences addressed to an AI. Works per sentence so that one bad phrase does not cost a whole run-on line."""
    removed = 0
    kept_lines = []
    for line in text.split("\n"):
        sentences = _SENTENCE_END.split(line)
        kept = [s for s in sentences if not _INSTRUCTION.search(s)]
        removed += len(sentences) - len(kept)
        if kept or not line.strip():
            kept_lines.append(" ".join(kept))
    return "\n".join(kept_lines), removed


def _read_plain_text(path: Path) -> tuple[str, list[str]]:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace"), []
    try:
        return raw.decode("utf-8-sig"), []
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), []


_READERS: dict[str, Callable[[Path], tuple[str, list[str]]]] = {".txt": _read_plain_text}


def _finish(text: str, warnings: list[str]) -> tuple[str, list[str]]:
    text = _CONTROL.sub("", _ZERO_WIDTH.sub("", text.replace("\r\n", "\n").replace("\r", "\n")))
    if len(text) > MAX_CHARS:
        raise ResumeError(f"This resume is too long. Please keep it under about {MAX_CHARS // 1000},000 characters.")
    text, removed = _strip_instructions(text)
    if removed:
        plural = "passage" if removed == 1 else "passages"
        warnings.append(f"Removed {removed} {plural} that looked like instructions to an AI.")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in text.split("\n"))).strip()
    if len(text) < MIN_CHARS:
        raise ResumeError("This file has almost no readable text. If it is a scan or an image, please upload a file with selectable text.")
    for warning in warnings:  # counts only: resume content must not reach the logs
        logger.warning("resume: %s", warning)
    return text, warnings


def load_resume(path: str | Path) -> tuple[str, list[str]]:
    """Return (text, warnings). Raises ResumeError, with a message for the candidate, if the file cannot be used."""
    path = Path(path)
    if not path.is_file():
        raise ResumeError(f"Could not find the file {path.name}.")
    if path.stat().st_size > MAX_BYTES:
        raise ResumeError(f"This file is too large. Please upload a resume under {MAX_BYTES // (1024 * 1024)} MB.")
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        raise ResumeError("Please upload a PDF, a Word file (.docx) or a plain text file (.txt).")
    text, warnings = reader(path)
    return _finish(text, warnings)

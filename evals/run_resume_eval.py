"""Measure resume extraction against the gold answers in evals/resumes/gold.yaml.

The gold is written by Claude and marked draft, so results on it are not proof (I10).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mockinterview.privacy import redact_pii  # noqa: E402
from mockinterview.resume.loaders import ResumeError, load_resume  # noqa: E402
from mockinterview.resume.profile import grounded  # noqa: E402

RESUME_DIR = ROOT / "evals" / "resumes"
GOLD_FILE = RESUME_DIR / "gold.yaml"
MIN_RESUMES = 10
REQUIRED_CASES = {"fresher_no_experience", "career_switcher", "buzzword_stuffed", "messy_formatting", "injection", "very_long", "non_tech_background"}
SENIORITY_LEVELS = {"intern", "junior", "mid", "senior"}
LABEL_STATUSES = {"draft", "reviewed"}


def load_gold(path: Path = GOLD_FILE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def aliases(skill: str | list[str]) -> list[str]:
    """A gold skill is one name, or a list of interchangeable names."""
    return list(skill) if isinstance(skill, list) else [skill]


def extraction_text(path: Path) -> str:
    """What the model is given in production: the loaded resume, after redaction."""
    return redact_pii(load_resume(path)[0])[0]


def validate_gold(gold: dict, folder: Path = RESUME_DIR) -> list[str]:
    """Every gold answer must be something the resume actually says, or the score would measure a typo."""
    errors: list[str] = []
    if gold.get("label_status") not in LABEL_STATUSES:
        errors.append(f"bad label_status {gold.get('label_status')!r}")
    entries = gold.get("resumes", [])
    if len(entries) < MIN_RESUMES:
        errors.append(f"need at least {MIN_RESUMES} resumes, got {len(entries)}")
    missing = REQUIRED_CASES - {entry["case"] for entry in entries}
    if missing:
        errors.append(f"missing cases: {sorted(missing)}")
    for entry in entries:
        name = entry["file"]
        try:
            text = extraction_text(folder / name)
        except ResumeError as error:
            errors.append(f"{name}: cannot be loaded: {error}")
            continue
        if not set(entry["seniority"]) <= SENIORITY_LEVELS:
            errors.append(f"{name}: bad seniority {entry['seniority']}")
        for skill in entry["skills"]:
            if not any(grounded(alias, text) for alias in aliases(skill)):
                errors.append(f"{name}: skill not in resume: {skill}")
        for bullet in [*entry["bullets"], *entry.get("optional_bullets", [])]:
            if not grounded(bullet, text):
                errors.append(f"{name}: bullet not in resume: {bullet}")
    return errors

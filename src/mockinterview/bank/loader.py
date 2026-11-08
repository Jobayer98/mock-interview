"""Load and validate the question bank and the labeled eval dataset."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS_FILE = ROOT / "data" / "questions" / "junior_swe.yaml"
DATASET_FILE = ROOT / "evals" / "dataset.yaml"

SCORES = (1, 2, 3, 4)
QUESTION_TYPES = {"technical", "behavioral"}
LABEL_STATUSES = {"draft", "reviewed"}
QUESTION_KEYS = {"id", "type", "topic", "question", "expected_signals", "red_flags", "rubric", "follow_up_hints"}


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def role_label(bank: dict) -> str:
    """Display name for prompts and the opening line, e.g. 'junior software engineer'."""
    return f"{bank['level']} {bank['role'].replace('_', ' ')}"


def load_questions(path: Path = QUESTIONS_FILE) -> list[dict]:
    """Each question carries its bank's role, rubric version and topic keywords, so the engine holds no role-specific content."""
    bank = _load(path)
    keywords = bank.get("topic_keywords", {})
    for q in bank["questions"]:
        q["role_label"] = role_label(bank)
        q["rubric_version"] = bank["rubric_version"]
        q["topic_keywords"] = keywords.get(q["topic"], [])
    return bank["questions"]


def load_dataset(path: Path = DATASET_FILE) -> list[dict]:
    return _load(path)["answers"]


def validate_questions(questions: list[dict]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for q in questions:
        qid = q.get("id", "<missing id>")
        if qid in seen:
            errors.append(f"{qid}: duplicate id")
        seen.add(qid)
        missing = QUESTION_KEYS - q.keys()
        if missing:
            errors.append(f"{qid}: missing keys {sorted(missing)}")
            continue
        if q["type"] not in QUESTION_TYPES:
            errors.append(f"{qid}: bad type {q['type']!r}")
        if sorted(q["rubric"]) != list(SCORES):
            errors.append(f"{qid}: rubric must define exactly levels {list(SCORES)}")
        for key in ("expected_signals", "red_flags", "follow_up_hints"):
            if not q[key]:
                errors.append(f"{qid}: {key} is empty")
    return errors


def validate_dataset(answers: list[dict], questions: list[dict]) -> list[str]:
    errors: list[str] = []
    qids = {q["id"] for q in questions}
    seen: set[str] = set()
    for a in answers:
        aid = a.get("id", "<missing id>")
        if aid in seen:
            errors.append(f"{aid}: duplicate id")
        seen.add(aid)
        if a.get("question_id") not in qids:
            errors.append(f"{aid}: unknown question_id {a.get('question_id')!r}")
        if a.get("score") not in SCORES:
            errors.append(f"{aid}: score must be one of {list(SCORES)}")
        if a.get("label_status") not in LABEL_STATUSES:
            errors.append(f"{aid}: bad label_status {a.get('label_status')!r}")
        if not str(a.get("answer", "")).strip():
            errors.append(f"{aid}: empty answer")
        if not str(a.get("reason", "")).strip():
            errors.append(f"{aid}: empty reason")
    covered = {a.get("question_id") for a in answers}
    for qid in sorted(qids - covered):
        errors.append(f"{qid}: no eval answers cover this question")
    return errors


def summarize(answers: list[dict]) -> str:
    by_score = Counter(a["score"] for a in answers)
    by_status = Counter(a["label_status"] for a in answers)
    dist = ", ".join(f"{s}:{by_score.get(s, 0)}" for s in SCORES)
    status = ", ".join(f"{k}:{v}" for k, v in sorted(by_status.items()))
    return f"{len(answers)} answers | score distribution {dist} | label status {status}"

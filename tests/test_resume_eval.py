"""Golden resumes and the extraction eval (P0-16). Model calls are faked here (I13); real runs live in evals/."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

import run_resume_eval as ev  # noqa: E402

from mockinterview.resume.loaders import load_resume  # noqa: E402

GOLD = ev.load_gold()
ENTRIES = {entry["file"]: entry for entry in GOLD["resumes"]}


# --- the golden set itself ---

def test_gold_is_consistent_with_the_resumes():
    assert ev.validate_gold(GOLD) == []


def test_gold_is_marked_draft_because_claude_wrote_it():
    assert GOLD["label_status"] == "draft"


def test_there_are_enough_resumes_covering_the_required_cases():
    assert len(GOLD["resumes"]) >= 10 and ev.REQUIRED_CASES <= {entry["case"] for entry in GOLD["resumes"]}


def test_every_level_of_seniority_is_represented():
    assert ev.SENIORITY_LEVELS <= {level for entry in GOLD["resumes"] for level in entry["seniority"]}


def test_every_resume_is_labelled_as_fictional_and_uses_reserved_contact_details():
    for name in ENTRIES:
        text = (ev.RESUME_DIR / name).read_text(encoding="utf-8")
        assert "synthetic sample resume - fictional person" in text.splitlines()[0]
        assert "@example.com" in text


def test_the_long_resume_is_long_but_within_the_loader_limit():
    assert 8_000 < len(load_resume(ev.RESUME_DIR / "very_long.txt")[0]) < 30_000


def test_the_loader_catches_the_direct_injection():
    text, warnings = load_resume(ev.RESUME_DIR / "injection_direct.txt")
    assert "Ignore all previous instructions" not in text and warnings


@pytest.mark.parametrize("name", ["injection_subtle.txt", "injection_in_bullet.txt"])
def test_the_subtle_injections_reach_the_model_so_its_defences_are_really_tested(name):
    text, warnings = load_resume(ev.RESUME_DIR / name)
    planted = ENTRIES[name]["injection"]["planted_text"][0]
    assert planted in text and warnings == []


def test_validation_catches_a_bullet_the_resume_does_not_contain():
    broken = {**GOLD, "resumes": [{**GOLD["resumes"][0], "bullets": ["Scaled the API to ten million users"]}]}
    assert any("bullet not in resume" in error for error in ev.validate_gold(broken))


def test_validation_catches_a_skill_the_resume_does_not_contain():
    broken = {**GOLD, "resumes": [{**GOLD["resumes"][0], "skills": ["Haskell"]}]}
    assert any("skill not in resume" in error for error in ev.validate_gold(broken))


def test_validation_requires_the_named_cases():
    assert any("missing cases" in error for error in ev.validate_gold({**GOLD, "resumes": GOLD["resumes"][:1]}))

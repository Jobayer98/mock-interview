"""Golden resumes and the extraction eval (P0-16). Model calls are faked here (I13); real runs live in evals/."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

import run_resume_eval as ev  # noqa: E402

from mockinterview.llm.base import LLMResult  # noqa: E402
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


# --- scoring ---

def profile(skills=(), bullets=(), seniority="junior", dropped=(), injection=False):
    return {"skills": [{"name": s, "evidence": ""} for s in skills], "experience": [{"bullets": [{"text": b, "claim_type": "other"} for b in bullets]}],
            "projects": [], "seniority": {"level": seniority, "reasons": []}, "dropped_ungrounded_bullets": list(dropped), "injection_attempt": injection}


SIMPLE = {"file": "x.txt", "case": "c", "seniority": ["junior"], "skills": ["Python", ["Microsoft Excel", "Excel"], "Node.js"], "optional_skills": ["REST"],
          "bullets": ["Built a thing", "Fixed a bug"], "optional_bullets": ["Volunteered"]}


def test_skill_spelling_differences_are_ignored():
    scores = ev.score_skills(SIMPLE, profile(["python", "EXCEL", "NodeJS"]))
    assert (scores["tp"], scores["fp"], scores["fn"]) == (3, 0, 0)


def test_interchangeable_names_count_once():
    scores = ev.score_skills(SIMPLE, profile(["Excel", "Microsoft Excel", "Python", "Node.js"]))
    assert (scores["tp"], scores["fp"], scores["fn"]) == (3, 0, 0)


def test_an_optional_skill_is_neither_credit_nor_penalty():
    assert ev.score_skills(SIMPLE, profile(["Python", "REST"])) == {"tp": 1, "fp": 0, "fn": 2, "missed": ["msexcel".replace("ms", "microsoft"), "nodejs"], "extra": []}


def test_an_unexpected_skill_is_a_false_positive():
    assert ev.score_skills(SIMPLE, profile(["Python", "Cobol"]))["extra"] == ["cobol"]


def test_bullets_match_despite_case_markers_and_a_final_full_stop():
    scores = ev.score_bullets(SIMPLE, profile(bullets=["- built  a thing.", "Fixed a bug"]))
    assert (scores["tp"], scores["fp"], scores["fn"]) == (2, 0, 0)


def test_an_optional_bullet_is_not_penalized_but_an_extra_one_is():
    scores = ev.score_bullets(SIMPLE, profile(bullets=["Built a thing", "Volunteered", "Gave a talk"]))
    assert (scores["tp"], scores["fp"], scores["fn"]) == (1, 1, 1)


def test_f1_arithmetic_and_empty_cases():
    assert ev.f1_scores(8, 2, 2) == pytest.approx({"precision": 0.8, "recall": 0.8, "f1": 0.8, "tp": 8, "fp": 2, "fn": 2})
    assert ev.f1_scores(0, 0, 0)["f1"] == 0.0


PLANTED = {**SIMPLE, "injection": {"planted_skills": ["Kubernetes"], "planted_text": ["Led a team of 200"], "banned_seniority": ["senior"]}}


def test_a_clean_profile_has_not_obeyed_the_injection():
    assert ev.obeyed_injection(PLANTED, profile(["Python"])) == []


@pytest.mark.parametrize("kwargs,expected", [
    ({"skills": ["kubernetes"]}, "skill: Kubernetes"),
    ({"bullets": ["Led a team of 200 engineers"]}, "bullet text: Led a team of 200"),
    ({"seniority": "senior"}, "seniority: senior"),
])
def test_each_way_of_obeying_an_injection_is_detected(kwargs, expected):
    assert ev.obeyed_injection(PLANTED, profile(**kwargs)) == [expected]


# --- running an entry against a fake model ---

class Fake:
    """Echoes a scripted extraction and keeps the prompts."""

    def __init__(self, **fields):
        base = {"seniority": {"level": "junior", "reasons": []}, "skills": [], "experience": [], "projects": [], "education": [], "injection_attempt": False}
        self.body = {**base, **fields}
        self.prompts = []

    def complete(self, *, system, prompt, model=None):
        self.prompts.append(prompt)
        return LLMResult(text=json.dumps(self.body), latency_s=0.0, cost_usd=0.01, model="fake")


def experience(*texts):
    return [{"company": "", "title": "", "dates": "", "bullets": [{"text": t, "claim_type": "other"} for t in texts]}]


def entry(name):
    return ENTRIES[name]


def test_a_perfect_extraction_scores_perfectly():
    gold = entry("injection_direct.txt")
    llm = Fake(skills=[{"name": s, "evidence": ""} for s in gold["skills"]], experience=experience(*gold["bullets"]))
    result = ev.evaluate_entry(llm, gold)
    assert (result["skills"]["fp"], result["skills"]["fn"], result["bullets"]["fp"], result["bullets"]["fn"]) == (0, 0, 0, 0)


def test_an_invented_bullet_is_dropped_and_counted_but_never_output():
    gold = entry("intern_student.txt")
    llm = Fake(skills=[{"name": "Kotlin", "evidence": ""}], experience=experience(gold["bullets"][0], "Scaled the app to ten million users"))
    result = ev.evaluate_entry(llm, gold)
    assert result["invented_and_dropped"] == 1 and result["fabricated_in_output"] == []


def test_injection_resumes_also_run_with_the_loader_bypassed():
    gold = entry("injection_direct.txt")
    llm = Fake(skills=[{"name": "Python", "evidence": ""}], injection_attempt=True)
    result = ev.evaluate_entry(llm, gold)
    assert len(llm.prompts) == 2 and "Ignore all previous instructions" not in llm.prompts[0] and "Ignore all previous instructions" in llm.prompts[1]
    assert result["injection"]["loader_removed_something"] and result["injection"]["raw_model_flagged"] and result["injection"]["raw_obeyed"] == []


def test_obeying_with_the_loader_bypassed_is_reported():
    llm = Fake(skills=[{"name": "Kubernetes", "evidence": ""}, {"name": "Python", "evidence": ""}])
    assert ev.evaluate_entry(llm, entry("injection_direct.txt"))["injection"]["raw_obeyed"] == ["skill: Kubernetes"]


def test_ordinary_resumes_run_once():
    llm = Fake(skills=[{"name": "Python", "evidence": ""}])
    ev.evaluate_entry(llm, entry("senior_engineer.txt"))
    assert len(llm.prompts) == 1


def test_a_failed_extraction_is_reported_not_raised():
    result = ev.evaluate_entry(Fake(), entry("senior_engineer.txt"))
    assert "no grounded content" in result["error"]


def run_main(monkeypatch, tmp_path, llm, *extra):
    monkeypatch.setattr(ev, "ClaudeCliLLM", lambda model=None: llm)
    out = tmp_path / "report.json"
    assert ev.main(["--out", str(out), "--workers", "1", *extra]) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_report_is_saved_and_says_the_gold_is_draft(monkeypatch, tmp_path, capsys):
    llm = Fake(skills=[{"name": "sample", "evidence": ""}], injection_attempt=True)  # "sample" is the one word every fixture contains
    report = run_main(monkeypatch, tmp_path, llm)
    assert report["gold_status"] == "draft gold (NOT human verified)" and report["resumes"] == len(ENTRIES)
    assert report["errors"] == [] and report["injection"]["resumes"] == 3 and report["injection"]["raw_path_flagged"] == 3
    assert report["ops"]["calls"] == len(ENTRIES) + 3
    output = capsys.readouterr().out
    assert "NOT human verified" in output and "skills: F1" in output and "target 0.95" in output


def test_limit_runs_only_the_first_resumes(monkeypatch, tmp_path):
    assert run_main(monkeypatch, tmp_path, Fake(skills=[{"name": "Python", "evidence": ""}]), "--limit", "2")["resumes"] == 2


def test_invalid_gold_stops_before_any_model_call(monkeypatch, capsys):
    monkeypatch.setattr(ev, "load_gold", lambda: {**GOLD, "resumes": GOLD["resumes"][:1]})
    monkeypatch.setattr(ev, "ClaudeCliLLM", lambda model=None: pytest.fail("model was created"))
    assert ev.main([]) == 2 and "gold is invalid" in capsys.readouterr().err

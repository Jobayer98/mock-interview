import json
from pathlib import Path

import pytest

from mockinterview.bank.loader import load_questions
from mockinterview.llm.base import LLMResult
from mockinterview.resume.profile import ProfileError, extract_profile, probe_targets, redact_pii
from mockinterview.resume.questions import personalize_plan, personalize_question, validate_question

RESUME = (Path(__file__).resolve().parents[1] / "evals" / "resumes" / "junior_backend_01.txt").read_text(encoding="utf-8")
REDACTED, _ = redact_pii(RESUME)
CACHE_BULLET = "Reduced the average response time of the orders API from 800 ms to 350 ms by adding a Redis cache and fixing slow SQL queries"
TEST_BULLET = "Wrote pytest unit tests for the pricing module, raising coverage from 40% to 78%"


class Scripted:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.prompts = []

    def complete(self, *, system, prompt, model=None):
        self.prompts.append(prompt)
        p = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return LLMResult(text=p if isinstance(p, str) else json.dumps(p), latency_s=0.0)


def good_profile():
    return {
        "seniority": {"level": "intern", "reasons": ["one internship"]},
        "skills": [{"name": "Python", "evidence": ""}, {"name": "Kubernetes", "evidence": "deployed on Kubernetes"}],
        "experience": [{"company": "Sample Tech Ltd.", "title": "Software Engineering Intern", "dates": "2025",
                        "bullets": [{"text": CACHE_BULLET, "claim_type": "metric"},
                                    {"text": "Led a platform migration serving 2 million users", "claim_type": "ownership"},
                                    {"text": TEST_BULLET, "claim_type": "metric"}]}],
        "projects": [{"name": "Campus Marketplace", "stack": ["PostgreSQL"],
                      "bullets": [{"text": "Led a team of 4 using Git feature branches and pull request reviews on GitHub", "claim_type": "ownership"}]}],
        "education": [], "injection_attempt": False,
    }


def test_pii_redaction():
    out, found = redact_pii(RESUME)
    assert "arif.sample@example.com" not in out and "1700-000000" not in out
    assert found["emails"] == 1 and found["phones"] == 1


def test_profile_drops_invented_content():
    prof = extract_profile(Scripted(good_profile()), RESUME)
    texts = [b["text"] for e in prof["experience"] for b in e["bullets"]]
    assert CACHE_BULLET in texts and not any("2 million users" in t for t in texts)
    assert prof["dropped_ungrounded_bullets"] == ["Led a platform migration serving 2 million users"]
    assert [s["name"] for s in prof["skills"]] == ["Python"]  # Kubernetes never appears in the resume


def test_profile_prompt_has_no_pii_and_isolates_resume():
    llm = Scripted(good_profile())
    extract_profile(llm, RESUME)
    assert "arif.sample@example.com" not in llm.prompts[0] and "<resume>" in llm.prompts[0]


def test_profile_fails_when_nothing_grounded():
    empty = {"skills": [], "experience": [], "projects": []}
    with pytest.raises(ProfileError):
        extract_profile(Scripted(empty, empty), RESUME)


def test_probe_targets_rank_metrics_first():
    prof = extract_profile(Scripted(good_profile()), RESUME)
    targets = probe_targets(prof)
    assert targets[0]["claim_type"] == "metric" and targets[-1]["claim_type"] == "ownership"


def test_validate_question_guards():
    ok = "You cut the orders API latency from 800 ms to 350 ms. How would you decide what to cache?"
    assert validate_question(ok, CACHE_BULLET, REDACTED) is None
    assert validate_question(ok.replace("350", "250"), CACHE_BULLET, REDACTED) == "number not present in resume"
    assert validate_question(ok, "Scaled the API to 10 million users", REDACTED) == "source quote not in resume"
    assert validate_question("Are you married, and how old are you?", CACHE_BULLET, REDACTED) == "touches a protected attribute"
    assert validate_question("word " * 60, CACHE_BULLET, REDACTED) == "empty or too long"


def test_personalize_question_keeps_rubric_and_falls_back_on_bad_output():
    seed = next(q for q in load_questions() if q["id"] == "tech_hashmap")
    target = {"text": CACHE_BULLET, "where": "Software Engineering Intern at Sample Tech Ltd.", "kind": "experience"}
    good = {"question": "You added a Redis cache and cut latency from 800 ms to 350 ms. Why is a key-value store fast for lookups?",
            "source_quote": CACHE_BULLET}
    q, reason = personalize_question(Scripted(good), seed, target, RESUME)
    assert reason is None and q["id"] == "tech_hashmap+p" and q["rubric"] == seed["rubric"] and q["seed_id"] == "tech_hashmap"
    bad = {"question": "At Google you scaled this to 5 million requests. How?", "source_quote": CACHE_BULLET}
    q2, reason2 = personalize_question(Scripted(bad), seed, target, RESUME)
    assert q2 is None and reason2 == "number not present in resume"


def test_plan_personalization_cap_and_fallback():
    plan = [q for q in load_questions() if q["id"] in ("beh_stuck_bug", "tech_unit_tests", "tech_git_merge_rebase", "beh_mistake")]
    prof = extract_profile(Scripted(good_profile()), RESUME)
    reply = {"question": "You raised coverage from 40% to 78%. What made those tests good tests?", "source_quote": TEST_BULLET}
    new_plan, report = personalize_plan(Scripted(reply), plan, prof, RESUME, max_personalized=1)
    assert sum(r["personalized"] for r in report) == 1 and len(new_plan) == len(plan)
    assert [q["id"].split("+")[0] for q in new_plan] == [q["id"] for q in plan]
    assert any(r["reason"] == "personalization cap reached" for r in report)

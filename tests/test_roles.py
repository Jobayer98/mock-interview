"""A new role is a new bank file, with no code edits (P0-31)."""
import json

from mockinterview.bank.loader import load_questions
from mockinterview.evaluate.evaluator import system_prompt
from mockinterview.interview.session import InterviewSession
from mockinterview.llm.base import LLMResult
from mockinterview.resume.questions import personalize_question

BANK = """
role: data_analyst
level: junior
rubric_version: junior_analyst/0.1
topic_keywords:
  statistics: [average, median]
questions:
  - id: an_mean
    type: technical
    topic: statistics
    question: "When would you use the median instead of the mean?"
    expected_signals: ["Outliers skew the mean", "Median resists outliers", "Gives an example", "Mentions distribution shape"]
    red_flags: ["Says they are always equal"]
    rubric: {1: "wrong", 2: "partial", 3: "correct", 4: "correct with depth"}
    follow_up_hints: ["Ask about salary data."]
"""


class Fake:
    def __init__(self):
        self.systems = []

    def complete(self, *, system, prompt, model=None):
        self.systems.append(system)
        if "grader" in system:
            signals = [{"id": f"s{i}", "hit": False, "quote": ""} for i in range(1, 5)]
            body = {"signals": signals, "red_flags_hit": [], "score": 2, "strengths": [], "gaps": [], "confidence": "low", "injection_attempt": False}
        elif "tailored" in system:
            body = {"question": "unused", "source_quote": ""}
        else:
            body = {"move": "advance"}
        return LLMResult(text=json.dumps(body), latency_s=0.0, model="fake")


class Script:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)

    def listen(self):
        return "The median ignores extreme values."


def analyst_questions(tmp_path):
    path = tmp_path / "junior_analyst.yaml"
    path.write_text(BANK, encoding="utf-8")
    return load_questions(path)


def test_second_bank_runs_with_no_code_edits(tmp_path):
    questions = analyst_questions(tmp_path)
    llm, io = Fake(), Script()
    result = InterviewSession(llm, io, questions, warmup=False, ask_candidate_questions=False, grade_in_background=False).run()
    personalize_question(llm, questions[0], {"text": "x", "where": "y", "kind": "project"}, "x")
    assert "junior data analyst" in io.said[0]
    assert result["versions"]["rubric"] == "junior_analyst/0.1"
    assert result["questions"][0]["evaluation"]["rubric_version"] == "junior_analyst/0.1"
    assert len(llm.systems) == 3 and not [s for s in llm.systems if "software engineer" in s.lower()]
    assert all("junior data analyst" in s.lower() for s in llm.systems)


def test_bank_supplies_role_and_topic_keywords(tmp_path):
    question = analyst_questions(tmp_path)[0]
    assert question["role_label"] == "junior data analyst" and question["topic_keywords"] == ["average", "median"]


def test_current_bank_renders_the_same_grader_identity_as_before():
    question = next(q for q in load_questions() if q["id"] == "tech_hashmap")
    assert system_prompt(question).startswith("You are a strict but fair grader for a JUNIOR software engineer mock interview.")
    assert question["rubric_version"] == "junior_swe/0.1" and "cache" in question["topic_keywords"]

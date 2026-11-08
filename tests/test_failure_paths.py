"""Failure paths and the session clock (P0-29)."""
import json

from mockinterview.bank.loader import load_questions
from mockinterview.interview import voice
from mockinterview.interview.policy import FOLLOWUP_CUTOFF_FRACTION, followups_allowed, time_is_up
from mockinterview.interview.session import InterviewSession
from mockinterview.llm.base import LLMError, LLMResult

QUESTIONS = load_questions()
HASH = next(q for q in QUESTIONS if q["id"] == "tech_hashmap")
TESTS = next(q for q in QUESTIONS if q["id"] == "tech_unit_tests")


def grade_body():
    signals = [{"id": f"s{i}", "hit": i <= 2, "quote": "answer words" if i <= 2 else ""} for i in range(1, 5)]
    return {"signals": signals, "red_flags_hit": [], "score": 3, "strengths": [], "gaps": [], "confidence": "medium", "injection_attempt": False}


class Fake:
    def __init__(self, move="advance", utterance="Why does that matter here?"):
        self.move, self.utterance = move, utterance
        self.turns = 0
        self.grades = 0

    def complete(self, *, system, prompt, model=None):
        if "grader" in system:
            self.grades += 1
            return LLMResult(text=json.dumps(grade_body()), latency_s=0.0, model="fake")
        self.turns += 1
        return LLMResult(text=json.dumps({"move": self.move, "target": "s2", "utterance": self.utterance}), latency_s=0.0, model="fake")


class Script:
    def __init__(self, replies, on_listen=None):
        self.replies = list(replies)
        self.said = []
        self.on_listen = on_listen

    def say(self, text):
        self.said.append(text)

    def listen(self):
        if self.on_listen:
            self.on_listen()
        return self.replies.pop(0) if self.replies else None


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def run(llm, io, questions, **kwargs):
    return InterviewSession(llm, io, questions, warmup=False, ask_candidate_questions=False, grade_in_background=False, phrasing_seed=1, **kwargs).run()


def test_disconnect_after_an_answer_still_grades_it():
    llm = Fake(move="probe")
    result = run(llm, Script(["my answer words here"]), [HASH])
    rec = result["questions"][0]
    assert result["ended_reason"] == "disconnect" and result["ended_early"]
    assert rec["evaluation"]["score"] == 3 and llm.grades == 1 and rec["skipped"] is False


def test_disconnect_before_any_answer_grades_nothing():
    llm = Fake()
    result = run(llm, Script([]), [HASH])
    assert llm.grades == 0 and result["questions"][0]["evaluation"] is None


def test_turn_backend_can_be_separate_and_is_still_metered():
    grader, turns = Fake(), Fake()
    result = run(grader, Script(["my answer words here"]), [HASH], turn_llm=turns)
    assert grader.turns == 0 and turns.turns == 1 and grader.grades == 1
    assert [c["purpose"] for c in result["llm_calls"]] == ["turn", "grade"]


def test_grading_failure_is_reported_without_ending_the_interview():
    class BrokenGrader(Fake):
        def complete(self, *, system, prompt, model=None):
            if "grader" in system:
                raise LLMError("grader unavailable")
            return super().complete(system=system, prompt=prompt, model=model)

    result = run(BrokenGrader(), Script(["first answer words", "second answer words"]), [HASH, TESTS])
    assert len(result["questions"]) == 2 and all("failed" in q["error"] for q in result["questions"])
    assert not result["ended_early"]


def test_clock_rules():
    assert followups_allowed(0, None) and followups_allowed(100, 1000)
    assert followups_allowed(1000 * FOLLOWUP_CUTOFF_FRACTION - 1, 1000) and not followups_allowed(1000 * FOLLOWUP_CUTOFF_FRACTION, 1000)
    assert not time_is_up(5, None) and not time_is_up(999, 1000) and time_is_up(1000, 1000)


def test_no_follow_ups_near_the_end_of_the_session():
    clock = Clock()

    def near_the_end():
        clock.now = 900

    llm = Fake(move="probe")
    result = run(llm, Script(["my answer words here", "unused"], on_listen=near_the_end), [HASH], time_budget_s=1000, clock=clock)
    assert llm.turns == 0 and result["questions"][0]["followups"] == [] and llm.grades == 1


def test_interview_wraps_up_when_time_runs_out():
    clock = Clock()

    def advance_time():
        clock.now += 600

    llm = Fake()
    io = Script(["first answer words", "second answer words"], on_listen=advance_time)
    result = run(llm, io, [HASH, TESTS], time_budget_s=500, clock=clock)
    assert len(result["questions"]) == 1 and result["ended_reason"] == "time" and not result["ended_early"]
    assert voice.OUT_OF_TIME in io.said and io.said[-1] == voice.OUTRO_NO_QUESTIONS


def test_no_budget_means_no_limit():
    clock = Clock()
    clock.now = 10_000_000
    llm = Fake(move="probe")
    result = run(llm, Script(["one answer words", "two answer words", "three answer words"]), [HASH], clock=clock)
    assert len(result["questions"][0]["followups"]) == 2

"""The step-based engine: actions, serializable state, resuming (P0-13)."""
import json
import threading

import pytest

from mockinterview.bank.loader import load_questions
from mockinterview.evaluate.evaluator import build_prompt
from mockinterview.interview import voice
from mockinterview.interview.engine import SAY_KINDS, EngineStateError, Finished, InterviewEngine, Say, SessionState, Wait
from mockinterview.llm.base import LLMResult, MeteredLLM

QUESTIONS = load_questions()
HASH = next(q for q in QUESTIONS if q["id"] == "tech_hashmap")
TESTS = next(q for q in QUESTIONS if q["id"] == "tech_unit_tests")
BEHAVIORAL = next(q for q in QUESTIONS if q["id"] == "beh_stuck_bug")
FIXED = {"phrasing_seed": 11, "grade_in_background": False}


class Fake:
    """Replies depend only on the prompt, so a resumed interview with a fresh client gets the same answers."""

    def __init__(self):
        self.turns = 0
        self.grades = 0

    def complete(self, *, system, prompt, model=None):
        if "grader" in system:
            self.grades += 1
            signals = [{"id": f"s{i}", "hit": False, "quote": ""} for i in range(1, 5)]
            body = {"signals": signals, "red_flags_hit": [], "score": 2, "strengths": ["ok"], "gaps": [], "confidence": "medium", "injection_attempt": False}
            return LLMResult(text=json.dumps(body), latency_s=0.0, cost_usd=0.01, model="fake")
        self.turns += 1
        first_turn = "may still ask on this question: 2" in prompt
        body = {"move": "probe", "target": "s2", "utterance": "Why does that matter here?"} if first_turn else {"move": "advance"}
        return LLMResult(text=json.dumps(body), latency_s=0.0, cost_usd=0.001, model="fake")


def said(actions):
    return [a for a in actions if isinstance(a, Say)]


REPLIES = [
    "I studied computer science and did an internship.",
    "Can you repeat the question?",
    "A dictionary finds things by key.",
    "Because the lookup is quick.",
    "just tell me the answer",
    "I don't know",
    "There was a bug in my code and I logged everything.",
    "It turned out to be a race in the connection.",
    "no thanks",
]


def stable(session):
    """The result without wall-clock values."""
    data = json.loads(json.dumps(session))
    for key in ("started_at", "duration_s", "feedback_wait_s"):
        data.pop(key)
    for event in data["events"]:
        event.pop("t")
    for call in data["llm_calls"]:
        call.pop("latency_s")
    return data


def run_straight(questions, replies, **kwargs):
    engine = InterviewEngine.new(Fake(), questions, **FIXED, **kwargs)
    actions = list(engine.start())
    for reply in replies:
        if isinstance(actions[-1], Finished):
            break
        actions += list(engine.step(reply))
    return actions


def run_resuming_every_turn(questions, replies, **kwargs):
    engine = InterviewEngine.new(Fake(), questions, **FIXED, **kwargs)
    actions = list(engine.start())
    for reply in replies:
        if isinstance(actions[-1], Finished):
            break
        stored = json.dumps(engine.snapshot())
        engine = InterviewEngine.restore(Fake(), json.loads(stored), grade_in_background=False)
        actions += list(engine.step(reply))
    return actions


def test_start_introduces_then_waits_for_the_warmup():
    actions = list(InterviewEngine.new(Fake(), [HASH], **FIXED).start())
    assert [a.kind for a in said(actions)] == ["intro", "warmup"] and isinstance(actions[-1], Wait)


def test_start_without_warmup_goes_to_the_first_question():
    actions = list(InterviewEngine.new(Fake(), [HASH], warmup=False, **FIXED).start())
    assert [a.kind for a in said(actions)] == ["intro", "question"] and isinstance(actions[-1], Wait)
    assert actions[1].question_id == HASH["id"] and HASH["question"] in actions[1].text


def test_acknowledgement_is_yielded_before_any_model_call():
    llm = Fake()
    engine = InterviewEngine.new(llm, [HASH], warmup=False, **FIXED)
    list(engine.start())
    steps = engine.step("A dictionary finds things by key.")
    first = next(steps)
    assert first.kind == "ack" and first.text in voice.ACKNOWLEDGEMENTS and llm.turns == 0
    list(steps)
    assert llm.turns == 1


def test_each_step_ends_in_wait_or_finished_and_kinds_are_known():
    engine = InterviewEngine.new(Fake(), [HASH, BEHAVIORAL], **FIXED)
    batches = [list(engine.start())]
    for reply in REPLIES:
        batches.append(list(engine.step(reply)))
        if isinstance(batches[-1][-1], Finished):
            break
    assert isinstance(batches[-1][-1], Finished)
    assert all(isinstance(batch[-1], Wait) for batch in batches[:-1])
    kinds = {a.kind for batch in batches for a in said(batch)}
    assert kinds <= SAY_KINDS and {"intro", "warmup", "ack", "question", "follow_up", "clarify", "meta_guard", "closing", "outro"} <= kinds


def test_state_survives_json_at_every_turn_and_finishes_the_same():
    questions = [HASH, BEHAVIORAL]
    straight, resumed = run_straight(questions, REPLIES), run_resuming_every_turn(questions, REPLIES)
    assert isinstance(straight[-1], Finished) and isinstance(resumed[-1], Finished)
    assert [a for a in straight if isinstance(a, Say)] == [a for a in resumed if isinstance(a, Say)]
    assert stable(straight[-1].session) == stable(resumed[-1].session)
    assert resumed[-1].session["questions"][0]["evaluation"]["score"] == 2


def test_resuming_mid_question_keeps_counters_and_the_active_follow_up():
    engine = InterviewEngine.new(Fake(), [HASH], warmup=False, **FIXED)
    list(engine.start())
    list(engine.step("A dictionary finds things by key."))
    restored = InterviewEngine.restore(Fake(), json.loads(json.dumps(engine.snapshot())), grade_in_background=False)
    assert restored.state.phase == "question" and restored.state.active_question == "Why does that matter here?"
    assert [a.text for a in said(restored.step("Can you repeat that?"))] == ["Sure. Why does that matter here?"]


def test_a_grade_still_running_is_redone_after_resuming():
    release = threading.Event()

    class Stuck(Fake):
        def complete(self, *, system, prompt, model=None):
            if "grader" in system:
                release.wait(timeout=5)
            return super().complete(system=system, prompt=prompt, model=model)

    engine = InterviewEngine.new(Stuck(), [HASH, TESTS, BEHAVIORAL], warmup=False, ask_candidate_questions=False, phrasing_seed=11)
    list(engine.start())
    list(engine.step("skip"))
    list(engine.step("A test checks one behavior."))
    list(engine.step("Tests should be fast."))
    data = json.loads(json.dumps(engine.snapshot()))
    assert data["pending_grades"] == [1] and data["records"][1]["evaluation"] is None
    fresh = Fake()
    restored = InterviewEngine.restore(fresh, data, grade_in_background=False)
    assert restored.settle_grades() >= 0 and fresh.grades == 1
    assert restored.state.pending_grades == [] and restored.state.records[1]["evaluation"]["score"] == 2
    release.set()


def test_finished_background_grades_are_folded_into_the_snapshot():
    engine = InterviewEngine.new(Fake(), [HASH, TESTS], warmup=False, ask_candidate_questions=False, phrasing_seed=11)
    list(engine.start())
    list(engine.step("A dictionary finds things by key."))
    list(engine.step("Because the lookup is quick."))
    engine.settle_grades()
    data = engine.snapshot()
    assert data["pending_grades"] == [] and data["records"][0]["evaluation"]["score"] == 2


def test_rubric_levels_are_integers_again_after_json():
    data = json.loads(json.dumps(SessionState(questions=[HASH]).to_dict()))
    assert list(data["questions"][0]["rubric"]) == ["1", "2", "3", "4"]
    restored = SessionState.from_dict(data)
    assert list(restored.questions[0]["rubric"]) == [1, 2, 3, 4]
    assert build_prompt(restored.questions[0], "An answer.") == build_prompt(HASH, "An answer.")


def test_from_dict_does_not_share_memory_with_its_input():
    data = SessionState(questions=[HASH]).to_dict()
    restored = SessionState.from_dict(data)
    restored.records.append({"changed": True})
    assert data["records"] == []


def test_unknown_state_version_is_rejected():
    data = SessionState(questions=[HASH]).to_dict()
    data["schema_version"] = 999
    with pytest.raises(ValueError):
        SessionState.from_dict(data)


def test_calls_out_of_order_raise_immediately():
    engine = InterviewEngine.new(Fake(), [HASH], warmup=False, ask_candidate_questions=False, **FIXED)
    with pytest.raises(EngineStateError):
        engine.step("too early")
    list(engine.start())
    with pytest.raises(EngineStateError):
        engine.start()
    list(engine.step("skip"))
    assert engine.state.phase == "finished"
    with pytest.raises(EngineStateError):
        engine.step("too late")


def test_session_clock_carries_across_a_resume():
    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

    clock = Clock()
    engine = InterviewEngine.new(Fake(), [HASH, TESTS], warmup=False, ask_candidate_questions=False, time_budget_s=100, clock=clock, **FIXED)
    list(engine.start())
    data = json.loads(json.dumps(engine.snapshot()))
    clock.now = 1200.0
    restored = InterviewEngine.restore(Fake(), data, clock=clock, grade_in_background=False)
    actions = list(restored.step("skip"))
    assert voice.OUT_OF_TIME in [a.text for a in said(actions)] and isinstance(actions[-1], Finished)
    assert actions[-1].session["ended_reason"] == "time"


def test_existing_metered_log_is_kept_so_resume_calls_are_counted():
    llm = MeteredLLM(Fake())
    llm.for_purpose("resume").complete(system="x", prompt="y")
    engine = InterviewEngine.new(llm, [HASH], warmup=False, ask_candidate_questions=False, **FIXED)
    list(engine.start())
    session = list(engine.step("A dictionary finds things by key."))[-1]
    assert isinstance(session, Wait)
    finished = list(engine.step("skip"))[-1].session
    assert [c["purpose"] for c in finished["llm_calls"]] == ["resume", "turn", "grade"]


def test_contact_details_are_removed_before_state_or_model():
    engine = InterviewEngine.new(Fake(), [HASH], warmup=False, **FIXED)
    list(engine.start())
    list(engine.step("Reach me at a.b@example.com about dictionaries."))
    assert "a.b@example.com" not in json.dumps(engine.snapshot())

"""Interviewer memory and format enforcement (P0-25)."""
import json

from mockinterview.bank.loader import load_questions
from mockinterview.interview.interviewer import GENERIC_FALLBACK, decide_turn
from mockinterview.interview.session import InterviewSession
from mockinterview.llm.base import LLMResult

QUESTIONS = load_questions()
HASH = next(q for q in QUESTIONS if q["id"] == "tech_hashmap")
TESTS = next(q for q in QUESTIONS if q["id"] == "tech_unit_tests")
HASH_ANSWER = "A dictionary finds things quickly by key."
GOOD_PROBE = {"move": "probe", "target": "s2", "utterance": "What makes something usable as a key?"}


class TurnSequence:
    """Fake turn model that returns a scripted decision per call and keeps the prompts."""

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.prompts = []

    def complete(self, *, system, prompt, model=None):
        self.prompts.append(prompt)
        decision = self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]
        return LLMResult(text=json.dumps(decision), latency_s=0.0, model="fake")


class Grader(TurnSequence):
    """Answers grader prompts with a valid evaluation, turn prompts with the scripted decisions."""

    def complete(self, *, system, prompt, model=None):
        if "grader" not in system:
            return super().complete(system=system, prompt=prompt, model=model)
        self.grader_prompts = [*getattr(self, "grader_prompts", []), prompt]
        signals = [{"id": f"s{i}", "hit": False, "quote": ""} for i in range(1, 5)]
        body = {"signals": signals, "red_flags_hit": [], "score": 2, "strengths": [], "gaps": [], "confidence": "medium", "injection_attempt": False}
        return LLMResult(text=json.dumps(body), latency_s=0.0, model="fake")


class Script:
    def __init__(self, replies):
        self.replies = list(replies)

    def say(self, text):
        pass

    def listen(self):
        return self.replies.pop(0) if self.replies else None


def run(llm, questions, replies, **kwargs):
    kwargs.setdefault("warmup", False)
    return InterviewSession(llm, Script(replies), questions, ask_candidate_questions=False, grade_in_background=False, phrasing_seed=1, **kwargs).run()


def test_double_barreled_follow_up_is_retried_with_the_reason():
    bad = {"move": "probe", "target": "s2", "utterance": "What makes a key valid, and how would you pick one?"}
    llm = TurnSequence(bad, GOOD_PROBE)
    decision = decide_turn(llm, HASH, [HASH_ANSWER], [], followups_left=2)
    assert decision.utterance == GOOD_PROBE["utterance"] and not decision.fallback
    assert len(llm.prompts) == 2 and "asks more than one question" in llm.prompts[1]


def test_two_question_marks_are_rejected():
    bad = {"move": "probe", "target": "s2", "utterance": "What is a key? Why does it matter?"}
    assert decide_turn(TurnSequence(bad), HASH, [HASH_ANSWER], [], followups_left=2).rejected == "asks more than one question"


def test_over_long_follow_up_falls_back_after_one_retry():
    long = {"move": "probe", "target": "s2", "utterance": "Could you " + "really " * 35 + "explain that?"}
    llm = TurnSequence(long)
    decision = decide_turn(llm, HASH, [HASH_ANSWER], [], followups_left=2)
    assert decision.fallback and decision.rejected == "over 30 words" and len(llm.prompts) == 2 and decision.utterance == GENERIC_FALLBACK


def test_second_fallback_differs_from_the_first():
    long = {"move": "probe", "target": "s2", "utterance": "word " * 40}
    decision = decide_turn(TurnSequence(long), HASH, [HASH_ANSWER, "More words here."], ["Earlier question?"], followups_left=1)
    assert decision.fallback and decision.utterance != GENERIC_FALLBACK


def test_statement_plus_one_question_is_allowed():
    text = {"move": "probe", "target": "s2", "utterance": "You mentioned finding things by key. What makes something usable as a key?"}
    assert decide_turn(TurnSequence(text), HASH, [HASH_ANSWER], [], followups_left=2).fallback is False


def test_earlier_quotes_resume_claim_and_openers_reach_the_prompt():
    llm = TurnSequence({"move": "advance"})
    decide_turn(llm, HASH, [HASH_ANSWER], [], followups_left=2, earlier=["I built a Flask API"], resume_claim="Added a Redis cache", avoid_openers=["You mentioned"])
    prompt = llm.prompts[0]
    assert "I built a Flask API" in prompt and "Added a Redis cache" in prompt and '"You mentioned"' in prompt and "<candidate_context>" in prompt


def test_no_context_block_when_there_is_no_context():
    llm = TurnSequence({"move": "advance"})
    decide_turn(llm, HASH, [HASH_ANSWER], [], followups_left=2)
    assert "<candidate_context>" not in llm.prompts[0]


def test_prior_follow_ups_are_in_the_prompt_so_they_are_not_repeated():
    llm = TurnSequence({"move": "advance"})
    decide_turn(llm, HASH, [HASH_ANSWER, "It uses hashing."], ["What makes it fast?"], followups_left=1)
    assert "What makes it fast?" in llm.prompts[0]


def test_only_verified_phrases_are_remembered():
    real = {"move": "advance", "remember": "finds things quickly by key"}
    assert decide_turn(TurnSequence(real), HASH, [HASH_ANSWER], [], followups_left=2).remember == real["remember"]
    invented = {"move": "advance", "remember": "I built a Kafka pipeline"}
    assert decide_turn(TurnSequence(invented), HASH, [HASH_ANSWER], [], followups_left=2).remember is None


def test_memory_carries_to_later_questions_but_not_within_one():
    llm = Grader({"move": "advance", "remember": "unique remembered phrase"})
    run(llm, [HASH, TESTS], ["This has a unique remembered phrase inside.", "second answer words here"])
    assert "Earlier they said" not in llm.prompts[0]
    assert 'Earlier they said: "unique remembered phrase"' in llm.prompts[1]


def test_warmup_excerpt_reaches_later_turns_but_never_the_grader():
    llm = Grader({"move": "advance"})
    run(llm, [HASH], ["I worked on a payments dashboard.", "A dictionary gives O(1) lookup on average."], warmup=True)
    assert "payments dashboard" in llm.prompts[0]
    assert not [p for p in llm.grader_prompts if "payments dashboard" in p]


def test_recent_openers_are_tracked_for_variety():
    probe = {"move": "probe", "target": "s2", "utterance": "You mentioned that earlier. Why does it matter?"}
    llm = Grader(probe)
    run(llm, [HASH], ["first answer words", "second answer words", "third answer words"])
    assert '"You mentioned"' not in llm.prompts[0] and '"You mentioned"' in llm.prompts[1]


def test_resume_claim_reaches_the_turn_prompt_for_a_tailored_question():
    tailored = {**HASH, "personalized_from": {"quote": "Added a Redis cache to the orders API", "where": "Intern", "kind": "experience"}}
    llm = Grader({"move": "advance"})
    run(llm, [tailored], ["A dictionary gives O(1) lookup on average."])
    assert "Added a Redis cache to the orders API" in llm.prompts[0]

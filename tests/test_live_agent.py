"""Live voice (P1b-01). Everything here uses fakes (I13); a real run needs LiveKit credentials."""
import asyncio
import json
import threading

from mockinterview.bank.loader import load_questions
from mockinterview.interview import voice
from mockinterview.interview.engine import InterviewEngine
from mockinterview.live.bridge import LiveInterview, TurnTimer
from mockinterview.llm.base import LLMResult

QUESTIONS = load_questions()
HASH = next(q for q in QUESTIONS if q["id"] == "tech_hashmap")
TESTS = next(q for q in QUESTIONS if q["id"] == "tech_unit_tests")


class Fake:
    """Replies depend on the prompt. `gate`, when given, holds each turn call until the test releases it."""

    def __init__(self, gate=None, probe=True):
        self.gate, self.probe = gate, probe
        self.turns = self.grades = 0

    def complete(self, *, system, prompt, model=None):
        if "grader" in system:
            self.grades += 1
            signals = [{"id": f"s{i}", "hit": False, "quote": ""} for i in range(1, 5)]
            body = {"signals": signals, "red_flags_hit": [], "score": 2, "strengths": [], "gaps": [], "confidence": "medium", "injection_attempt": False}
            return LLMResult(text=json.dumps(body), latency_s=0.0, model="fake")
        if self.gate is not None:
            assert self.gate.wait(timeout=5)
        self.turns += 1
        first_turn = self.probe and "may still ask on this question: 2" in prompt
        body = {"move": "probe", "target": "s2", "utterance": "Why does that matter here?"} if first_turn else {"move": "advance"}
        return LLMResult(text=json.dumps(body), latency_s=0.0, model="fake")


def interview(llm, questions=(HASH,), warmup=False):
    engine = InterviewEngine.new(llm, list(questions), warmup=warmup, ask_candidate_questions=False, phrasing_seed=3, grade_in_background=False)
    return LiveInterview(engine), engine


async def collect(stream):
    return [line async for line in stream]


def test_opening_needs_no_model_call():
    llm = Fake()
    live, _ = interview(llm, warmup=True)
    assert [line.kind for line in live.opening()] == ["intro", "warmup"] and llm.turns == 0 and llm.grades == 0


def test_a_turn_yields_acknowledgement_then_follow_up():
    async def run():
        live, _ = interview(Fake())
        live.opening()
        return await collect(live.reply("A dictionary finds things by key."))

    lines = asyncio.run(run())
    assert [line.kind for line in lines] == ["ack", "follow_up"] and lines[0].text in voice.ACKNOWLEDGEMENTS


def test_the_acknowledgement_arrives_while_the_model_is_still_thinking():
    async def run():
        gate, llm = threading.Event(), None
        llm = Fake(gate)
        live, _ = interview(llm)
        live.opening()
        stream = live.reply("A dictionary finds things by key.")
        first = await asyncio.wait_for(anext(stream), timeout=2)
        turns_so_far = llm.turns
        gate.set()
        rest = await collect(stream)
        return first, turns_so_far, rest

    first, turns_so_far, rest = asyncio.run(run())
    assert first.kind == "ack" and turns_so_far == 0 and [line.kind for line in rest] == ["follow_up"]


def test_words_spoken_while_the_interviewer_thinks_finish_the_answer():
    async def run():
        gate = threading.Event()
        live, engine = interview(Fake(gate))
        live.opening()
        stream = live.reply("First answer about keys.")
        await anext(stream)
        await stream.aclose()  # the candidate interrupted, so the voice framework dropped this turn
        second = asyncio.create_task(collect(live.reply("And it uses hashing.")))
        await asyncio.sleep(0.05)
        waited = not second.done() and engine.state.records[0]["answers"] == ["First answer about keys."]
        gate.set()
        return waited, await second, engine.state.records[0]["answers"], engine.state.records[0]["followups"]

    waited, spoken, answers, followups = asyncio.run(run())
    assert waited and answers == ["First answer about keys. And it uses hashing."] and followups == ["Why does that matter here?"]
    assert [line.text for line in spoken] == ["Why does that matter here?"]  # the follow-up was cut off, so it is said again


def test_a_reply_after_the_interviewer_finished_thinking_is_a_new_answer():
    async def run():
        live, engine = interview(Fake())
        live.opening()
        await collect(live.reply("First answer about keys."))
        await collect(live.reply("Second answer about hashing."))
        return engine.state.records[0]["answers"]

    assert asyncio.run(run()) == ["First answer about keys.", "Second answer about hashing."]


def test_a_stop_request_while_the_interviewer_thinks_is_still_honoured():
    async def run():
        gate = threading.Event()
        live, _ = interview(Fake(gate))
        live.opening()
        stream = live.reply("First answer about keys.")
        await anext(stream)
        await stream.aclose()
        second = asyncio.create_task(collect(live.reply("I want to stop the interview now.")))
        await asyncio.sleep(0.05)
        gate.set()
        return await second, live.result

    spoken, result = asyncio.run(run())
    assert spoken[-1].text == voice.STOPPING and result["ended_reason"] == "stop"


def test_a_crisis_while_the_interviewer_thinks_is_never_treated_as_more_answer():
    async def run():
        gate = threading.Event()
        live, _ = interview(Fake(gate))
        live.opening()
        stream = live.reply("First answer about keys.")
        await anext(stream)
        await stream.aclose()
        second = asyncio.create_task(collect(live.reply("I want to kill myself")))
        await asyncio.sleep(0.05)
        gate.set()
        return await second, live.result

    spoken, result = asyncio.run(run())
    assert spoken[-1].text == voice.CRISIS and "kill myself" not in json.dumps(result)


def test_a_resent_turn_only_counts_its_new_words():
    async def run():
        live, engine = interview(Fake())
        live.opening()
        await collect(live.reply("A dictionary finds things."))
        await collect(live.reply("A dictionary finds things. It uses hashing."))
        return engine.state.records[0]["answers"]

    assert asyncio.run(run()) == ["A dictionary finds things.", "It uses hashing."]


def test_a_blank_transcript_is_ignored():
    async def run():
        llm = Fake()
        live, _ = interview(llm)
        live.opening()
        return await collect(live.reply("   ")), llm.turns

    assert asyncio.run(run()) == ([], 0)


def test_the_result_is_kept_when_the_interview_finishes():
    async def run():
        live, _ = interview(Fake(probe=False))
        live.opening()
        lines = await collect(live.reply("A dictionary finds things by key."))
        return lines, live.result, await collect(live.reply("Anything else?"))

    lines, result, after = asyncio.run(run())
    assert lines[-1].kind == "outro" and result["questions"][0]["evaluation"]["score"] == 2 and after == []


def test_leaving_midway_grades_what_was_answered():
    async def run():
        llm = Fake()
        live, _ = interview(llm)
        live.opening()
        await collect(live.reply("A dictionary finds things by key."))
        return await live.close(), llm.grades

    result, grades = asyncio.run(run())
    assert result["ended_reason"] == "disconnect" and grades == 1 and result["questions"][0]["evaluation"]["score"] == 2


def test_closing_twice_is_safe():
    async def run():
        llm = Fake()
        live, _ = interview(llm)
        live.opening()
        await collect(live.reply("A dictionary finds things by key."))
        first = await live.close()
        return first is await live.close(), llm.grades

    assert asyncio.run(run()) == (True, 1)


def test_closing_before_the_interview_began_does_nothing():
    live, _ = interview(Fake())
    assert asyncio.run(live.close()) is None


def test_a_stop_request_ends_the_interview():
    async def run():
        live, _ = interview(Fake(), questions=(HASH, TESTS))
        live.opening()
        return await collect(live.reply("I want to stop the interview now.")), live.result

    lines, result = asyncio.run(run())
    assert lines[-1].text == voice.STOPPING and result["ended_reason"] == "stop"


def test_turn_timer_measures_speech_end_to_first_audio():
    now = [10.0]
    timer = TurnTimer(clock=lambda: now[0])
    timer.speech_ended()
    now[0] = 11.5
    timer.audio_started()
    now[0] = 12.0
    timer.audio_started()  # the same turn's later audio is not another measurement
    timer.speech_ended()
    now[0] = 12.5
    timer.audio_started()
    assert timer.summary() == {"turns": 2, "median_s": 1.0, "max_s": 1.5, "all_s": [1.5, 0.5]}


def test_turn_timer_with_nothing_measured():
    assert TurnTimer().summary() == {"turns": 0, "median_s": None, "max_s": None, "all_s": []}

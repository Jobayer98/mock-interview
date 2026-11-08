"""The LiveKit side of live voice (P1b-01), run through LiveKit's real pipeline in text mode, with fakes for every model."""
import asyncio
import json

import pytest

pytest.importorskip("livekit.agents")

from livekit.agents import AgentSession, llm  # noqa: E402

from mockinterview.bank.loader import load_questions  # noqa: E402
from mockinterview.interview import voice  # noqa: E402
from mockinterview.interview.engine import InterviewEngine  # noqa: E402
from mockinterview.live import agent as live_agent  # noqa: E402
from mockinterview.live.agent import InterviewAgent, NoBuiltInLLM, latest_user_text, save_session, session_options, watch_turns  # noqa: E402
from mockinterview.live.bridge import LiveInterview, TurnTimer  # noqa: E402
from mockinterview.llm.base import LLMResult  # noqa: E402

HASH = next(q for q in load_questions() if q["id"] == "tech_hashmap")


class Fake:
    def __init__(self, probe=True):
        self.probe = probe
        self.prompts = []

    def complete(self, *, system, prompt, model=None):
        self.prompts.append(prompt)
        if "grader" in system:
            signals = [{"id": f"s{i}", "hit": False, "quote": ""} for i in range(1, 5)]
            body = {"signals": signals, "red_flags_hit": [], "score": 2, "strengths": [], "gaps": [], "confidence": "medium", "injection_attempt": False}
        elif self.probe and "may still ask on this question: 2" in prompt:
            body = {"move": "probe", "target": "s2", "utterance": "Why does that matter here?"}
        else:
            body = {"move": "advance"}
        return LLMResult(text=json.dumps(body), latency_s=0.0, model="fake")


def live_interview(llm_fake, warmup=False):
    return LiveInterview(InterviewEngine.new(llm_fake, [HASH], warmup=warmup, ask_candidate_questions=False, phrasing_seed=3, grade_in_background=False))


class SpokenAgent(InterviewAgent):
    """Text mode has no speech output, so the opening is recorded instead of spoken."""

    def __init__(self, live):
        super().__init__(live)
        self.opening_lines = []

    async def on_enter(self):
        self.opening_lines = [line.text for line in self._live.opening()]


async def spoken_reply(agent, text, llm_object=None):
    async with AgentSession(llm=llm_object or NoBuiltInLLM(), turn_handling=session_options()) as session:
        await session.start(agent)
        result = await session.run(user_input=text)
        return result, session


def assistant_text(result):
    return " ".join(event.item.text_content for event in result.events if event.type == "message")


def test_the_pipeline_speaks_the_engines_words_not_a_built_in_llm():
    async def run():
        agent = SpokenAgent(live_interview(Fake()))
        result, _ = await spoken_reply(agent, "A dictionary finds things by key.")
        return agent, assistant_text(result)

    agent, text = asyncio.run(run())
    assert "Why does that matter here?" in text and any(ack in text for ack in voice.ACKNOWLEDGEMENTS)
    assert "AI interviewer" in agent.opening_lines[0]


def test_the_candidates_words_reach_the_engine():
    fake = Fake()

    async def run():
        live = live_interview(fake)
        await spoken_reply(SpokenAgent(live), "A dictionary finds things by key.")
        return live

    live = asyncio.run(run())
    assert live._engine.state.records[0]["answers"] == ["A dictionary finds things by key."]
    assert any("A dictionary finds things by key." in prompt for prompt in fake.prompts)


def test_the_built_in_llm_placeholder_refuses_to_run():
    with pytest.raises(RuntimeError, match="replaces the built-in LLM"):
        NoBuiltInLLM().chat(chat_ctx=llm.ChatContext.empty())


def test_speculative_generation_is_off():
    assert session_options()["preemptive_generation"] == {"enabled": False}


def test_a_thinking_pause_does_not_end_the_turn():
    options = session_options()
    assert options["turn_detection"] == "vad" and options["endpointing"]["min_delay"] >= 2.0 and options["endpointing"]["max_delay"] > options["endpointing"]["min_delay"]


@pytest.mark.parametrize("setting,expected", [("normal", "normal"), ("0.9", 0.9)])
def test_the_voice_speed_can_be_a_word_or_a_number(monkeypatch, setting, expected):
    captured = {}
    monkeypatch.setattr(live_agent, "TTS_SPEED", setting)
    monkeypatch.setattr(live_agent.inference, "TTS", lambda model, language, extra_kwargs: captured.update(model=model, language=language, **extra_kwargs))
    live_agent.interviewer_voice()
    assert captured == {"model": live_agent.TTS_MODEL, "language": "en", "speed": expected}


def test_the_default_voice_speed_is_a_gentle_slowdown():
    assert 0.8 <= float(live_agent.TTS_SPEED) < 1.0


def test_the_latest_candidate_message_is_found():
    context = llm.ChatContext.empty()
    context.add_message(role="assistant", content="First question?")
    context.add_message(role="user", content="An early answer.")
    context.add_message(role="assistant", content="Follow-up?")
    context.add_message(role="user", content="The latest answer.")
    assert latest_user_text(context) == "The latest answer."
    assert latest_user_text(llm.ChatContext.empty()) == ""


class Event:
    def __init__(self, old_state, new_state):
        self.old_state, self.new_state = old_state, new_state


class FakeSession:
    def __init__(self):
        self.handlers = {}
        self.said = []
        self.stopped = False

    def on(self, name, handler):
        self.handlers[name] = handler

    def say(self, text):
        self.said.append(text)

    def shutdown(self, drain=True):
        self.stopped = drain


def test_turn_timing_is_taken_from_session_events():
    now = [0.0]
    timer, session = TurnTimer(clock=lambda: now[0]), FakeSession()
    watch_turns(session, timer)
    session.handlers["user_state_changed"](Event("listening", "speaking"))
    now[0] = 5.0
    session.handlers["user_state_changed"](Event("speaking", "listening"))
    now[0] = 5.4
    session.handlers["agent_state_changed"](Event("thinking", "speaking"))
    assert timer.turns == [0.4]


def test_opening_lines_are_spoken_on_entry(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(InterviewAgent, "session", session)
    asyncio.run(InterviewAgent(live_interview(Fake(), warmup=True)).on_enter())
    assert len(session.said) == 2 and session.said[1] == voice.WARMUP


def test_the_session_ends_after_the_last_line(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(InterviewAgent, "session", session)

    async def run():
        live = live_interview(Fake(probe=False))
        live.opening()
        context = llm.ChatContext.empty()
        context.add_message(role="user", content="A dictionary finds things by key.")
        return [chunk async for chunk in InterviewAgent(live).llm_node(context, [], None)]

    chunks = asyncio.run(run())
    assert any(isinstance(chunk, str) and voice.OUTRO_NO_QUESTIONS in chunk for chunk in chunks) and session.stopped


def test_the_acknowledgement_is_flushed_to_speech_early(monkeypatch):
    monkeypatch.setattr(InterviewAgent, "session", FakeSession())

    async def run():
        live = live_interview(Fake())
        live.opening()
        context = llm.ChatContext.empty()
        context.add_message(role="user", content="A dictionary finds things by key.")
        return [chunk async for chunk in InterviewAgent(live).llm_node(context, [], None)]

    chunks = asyncio.run(run())
    assert isinstance(chunks[0], str) and type(chunks[1]).__name__ == "FlushSentinel" and "Why does that matter" in chunks[2]


def test_a_finished_session_is_saved_with_scores_and_timing(tmp_path):
    async def run():
        live = live_interview(Fake(probe=False))
        live.opening()
        async for _ in live.reply("A dictionary finds things by key."):
            pass
        return live.result

    timer = TurnTimer()
    timer.turns = [1.0]
    path = save_session(asyncio.run(run()), timer, tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["scorecard"]["overall"] == 2 and saved["turn_timing"]["turns"] == 1


def test_build_engine_uses_a_short_timeout_for_turns(monkeypatch):
    created = []

    class Cli:
        def __init__(self, model=None, timeout_s=120.0):
            created.append((model, timeout_s))

    monkeypatch.setattr(live_agent, "ClaudeCliLLM", Cli)
    monkeypatch.delenv("MOCKINTERVIEW_RESUME", raising=False)
    engine = live_agent.build_engine()
    assert (live_agent.TURN_MODEL, live_agent.TURN_TIMEOUT_S) in created and engine.state.time_budget_s == live_agent.SESSION_BUDGET_S
    assert len(engine.state.questions) == 5

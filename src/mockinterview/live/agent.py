"""LiveKit voice agent: a spoken interview driven by the step engine.

    python -m mockinterview.live.agent dev

This is the developer smoke test (P1b-01). It uses `claude -p`, so real candidates must never reach it (I9), and at about 6 s
per turn it proves the wiring, not how natural the interview feels. Needs LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobProcess, cli, llm
from livekit.agents.types import FlushSentinel
from livekit.agents.voice.turn import TurnHandlingOptions
from livekit.plugins import silero

from mockinterview.bank.loader import ROOT, load_questions
from mockinterview.interview.engine import InterviewEngine
from mockinterview.interview.session import plan_questions
from mockinterview.live.bridge import LiveInterview, TurnTimer
from mockinterview.llm.base import MeteredLLM
from mockinterview.llm.claude_cli import ClaudeCliLLM
from mockinterview.report.scorecard import build_scorecard
from mockinterview.resume.loaders import load_resume
from mockinterview.resume.profile import extract_profile
from mockinterview.resume.questions import personalize_plan

logger = logging.getLogger(__name__)

GRADE_MODEL = "claude-sonnet-5-5"
TURN_MODEL = "claude-haiku-5-5"
TURN_TIMEOUT_S = 30.0
SESSION_BUDGET_S = 30 * 60
STT_MODEL = os.environ.get("MOCKINTERVIEW_STT", "deepgram/nova-3:en")  # English only (I1)
TTS_MODEL = os.environ.get("MOCKINTERVIEW_TTS", "cartesia/sonic-3")
SESSIONS_DIR = ROOT / "data" / "sessions"


class NoBuiltInLLM(llm.LLM):
    """LiveKit will not run a voice pipeline without an LLM object, but InterviewAgent.llm_node replaces it entirely."""

    def chat(self, **kwargs):
        raise RuntimeError("InterviewAgent.llm_node replaces the built-in LLM")


def latest_user_text(chat_ctx: llm.ChatContext) -> str:
    for item in reversed(chat_ctx.items):
        if item.type == "message" and item.role == "user":
            return item.text_content or ""
    return ""


class InterviewAgent(Agent):
    """Every word comes from the engine, so the policies and guards tested in text apply unchanged to voice."""

    def __init__(self, live: LiveInterview):
        super().__init__(instructions="The interview engine decides every turn.")
        self._live = live

    async def on_enter(self) -> None:
        for line in self._live.opening():
            self.session.say(line.text)

    async def llm_node(self, chat_ctx, tools, model_settings):
        async for line in self._live.reply(latest_user_text(chat_ctx)):
            yield f"{line.text} "
            if line.kind == "ack":
                yield FlushSentinel()  # send the acknowledgement to speech now, while the model is still thinking
        if self._live.result is not None:
            self.session.shutdown(drain=True)


def session_options() -> TurnHandlingOptions:
    """Speculative generation must stay off: each engine step changes the interview, so a discarded guess would count as an answer."""
    return TurnHandlingOptions(preemptive_generation={"enabled": False})


def build_engine() -> InterviewEngine:
    """Blocking, because tailoring questions to a resume calls the model. Set MOCKINTERVIEW_RESUME to use one."""
    grader = MeteredLLM(ClaudeCliLLM(model=GRADE_MODEL))
    questions = plan_questions(load_questions())
    resume = os.environ.get("MOCKINTERVIEW_RESUME")
    if resume:
        text, _ = load_resume(resume)
        resume_llm = grader.for_purpose("resume")
        profile = extract_profile(resume_llm, text, model=GRADE_MODEL)
        questions, _ = personalize_plan(resume_llm, questions, profile, text, model=GRADE_MODEL)
    return InterviewEngine.new(grader, questions, model=GRADE_MODEL, turn_model=TURN_MODEL, time_budget_s=SESSION_BUDGET_S,
                               turn_llm=ClaudeCliLLM(model=TURN_MODEL, timeout_s=TURN_TIMEOUT_S))


def watch_turns(session, timer: TurnTimer) -> None:
    def user_changed(event) -> None:
        if event.old_state == "speaking" and event.new_state == "listening":
            timer.speech_ended()

    def agent_changed(event) -> None:
        if event.new_state == "speaking":
            timer.audio_started()

    session.on("user_state_changed", user_changed)
    session.on("agent_state_changed", agent_changed)


def save_session(result: dict, timer: TurnTimer, folder: Path = SESSIONS_DIR) -> Path:
    """Sessions can hold a real person's answers, so this folder is git-ignored."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"live_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps({"session": result, "scorecard": build_scorecard(result), "turn_timing": timer.summary()}, indent=2), encoding="utf-8")
    return path


server = AgentServer()


def prewarm(process: JobProcess) -> None:
    process.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    logger.warning("This agent uses claude -p and is for developer testing only. Real candidates need the API backend.")
    live, timer = LiveInterview(await asyncio.to_thread(build_engine)), TurnTimer()
    session = AgentSession(stt=STT_MODEL, tts=TTS_MODEL, llm=NoBuiltInLLM(), vad=ctx.proc.userdata["vad"], turn_handling=session_options())
    watch_turns(session, timer)

    async def finish() -> None:
        result = await live.close()
        if result is not None:
            logger.info("saved %s; turn timing %s", save_session(result, timer), timer.summary())

    ctx.add_shutdown_callback(finish)
    await session.start(agent=InterviewAgent(live), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)

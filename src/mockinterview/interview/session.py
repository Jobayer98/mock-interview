"""Plan the questions, and run an interview over blocking input and output (the CLI and tests). The engine holds the logic."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from mockinterview.llm.base import LLM

from .engine import Finished, InterviewEngine, Say
from .policy import MAX_FOLLOWUPS


class IO(Protocol):
    def say(self, text: str) -> None: ...
    def listen(self) -> str | None: ...


def plan_questions(questions: list[dict], *, seed: int | None = None, n_behavioral: int = 2, n_technical: int = 3) -> list[dict]:
    """Open with one behavioral question that is easy to start on, run the technical block, close with behavioral."""
    rng = random.Random(seed)
    beh = [q for q in questions if q["type"] == "behavioral"]
    tech = [q for q in questions if q["type"] == "technical"]
    rng.shuffle(beh)
    rng.shuffle(tech)
    openers = [q for q in beh if not q.get("avoid_as_opener")] or beh
    opening = openers[:1] if n_behavioral else []
    closing = [q for q in beh if q not in opening][: max(n_behavioral - 1, 0)]
    return opening + tech[:n_technical] + closing


def drive(engine: InterviewEngine, io: IO) -> dict:
    """Pump the engine over blocking input and output until the interview finishes."""
    actions = engine.start()
    while True:
        for action in actions:
            if isinstance(action, Say):
                io.say(action.text)
            elif isinstance(action, Finished):
                return action.session
        actions = engine.step(io.listen())


@dataclass
class InterviewSession:
    llm: LLM
    io: IO
    questions: list[dict]
    model: str | None = None
    turn_model: str | None = None
    max_followups: int = MAX_FOLLOWUPS
    warmup: bool = True
    ask_candidate_questions: bool = True
    grade_in_background: bool = True
    phrasing_seed: int | None = None
    turn_llm: LLM | None = None  # optional separate backend, e.g. with a short timeout for the conversation path
    time_budget_s: float | None = None
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        self._engine = InterviewEngine.new(
            self.llm, self.questions, model=self.model, turn_model=self.turn_model, max_followups=self.max_followups, warmup=self.warmup,
            ask_candidate_questions=self.ask_candidate_questions, time_budget_s=self.time_budget_s, phrasing_seed=self.phrasing_seed,
            turn_llm=self.turn_llm, clock=self.clock, grade_in_background=self.grade_in_background)

    def run(self) -> dict:
        return drive(self._engine, self.io)
